"""Main monitoring service orchestrator"""
import time
import os
import json
from datetime import datetime
from typing import Dict, List
from pathlib import Path

import requests

from config import AppConfig
from http_monitor import HttpMonitor
from change_detector import ChangeDetector
from changedetection_service import ChangedetectionService
from sheets_reporter import GoogleSheetsReporter
from github_reporter import GitHubReporter
from scheduler import UrlScheduler
from models import DetectedChange, MonitoringCycleStats
import logging

logger = logging.getLogger(__name__)


class MonitoringService:
    def __init__(self, config_path: str = "config.yaml"):
        # Load configuration first
        self.config = AppConfig(config_path)
        
        # Detect first run status
        self.first_run = self._detect_first_run()
        logger.info(f"First run detected: {self.first_run}")
        
        # Initialize components with first_run context
        self.http_monitor = HttpMonitor(self.config)
        self.change_detector = ChangeDetector(Path(self.config.settings.history_file))
        self.changedetection_service = ChangedetectionService(self.config)
        self.sheets_reporter = GoogleSheetsReporter(self.config)
        self.gh_reporter = GitHubReporter()
        self.scheduler = UrlScheduler(self.config)
        
        # Initialize changedetection.io watches with robust retry logic
        self._setup_changedetection_with_retry()
        
        logger.info("Monitoring service initialized successfully")
    
    def _detect_first_run(self) -> bool:
        """
        Detect if this is the first run by checking multiple sources.
        Priority:
        1. MONITOR_FIRST_RUN environment variable (from run_monitor.py)
        2. FIRST_RUN environment variable (from GitHub Actions)
        3. Existing datastore with watches
        4. Previous monitoring reports
        5. Change detector history
        """
        # Check MONITOR_FIRST_RUN environment variable first
        monitor_first_run = os.getenv('MONITOR_FIRST_RUN', '').lower()
        if monitor_first_run in ['false', '0', 'no']:
            logger.info("MONITOR_FIRST_RUN environment variable set to false")
            return False
        elif monitor_first_run in ['true', '1', 'yes']:
            logger.info("MONITOR_FIRST_RUN environment variable set to true")
            return True
        
        # Check FIRST_RUN environment variable (GitHub Actions)
        first_run_env = os.getenv('FIRST_RUN', '').lower()
        if first_run_env in ['false', '0', 'no']:
            logger.info("FIRST_RUN environment variable set to false")
            return False
        elif first_run_env in ['true', '1', 'yes']:
            logger.info("FIRST_RUN environment variable set to true")
            return True
        
        # Check for existing changedetection.io datastore
        datastore_path = Path("data/datastore")
        if datastore_path.exists():
            datastore_files = list(datastore_path.glob("*.json"))
            if datastore_files:
                logger.info(f"Found {len(datastore_files)} datastore files")
                
                # Try to check if datastore has watches configured
                for datastore_file in datastore_files:
                    try:
                        with open(datastore_file, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                            if 'watches' in data and data['watches']:
                                watch_count = len(data['watches'])
                                logger.info(f"Datastore contains {watch_count} watches")
                                return False
                    except (json.JSONDecodeError, KeyError, Exception) as e:
                        logger.debug(f"Could not parse {datastore_file}: {e}")
                        continue
        
        # Check for previous monitoring reports
        reports_path = Path("data/reports")
        if reports_path.exists():
            report_files = list(reports_path.glob("cycle_*.json"))
            if report_files:
                logger.info(f"Found {len(report_files)} previous reports")
                return False
        
        # Check change detector history
        history_path = Path(self.config.settings.history_file)
        if history_path.exists():
            try:
                with open(history_path, 'r', encoding='utf-8') as f:
                    history_data = json.load(f)
                    if history_data and 'history' in history_data and history_data['history']:
                        logger.info("Found existing change history")
                        return False
            except (json.JSONDecodeError, Exception) as e:
                logger.debug(f"Could not parse history file: {e}")
        
        # No existing data found - this is the first run
        logger.info("No existing monitoring data found - first run")
        return True
    
    def _wait_for_changedetection(self, timeout: int = 60) -> bool:
        """Wait for changedetection.io to be ready with extended timeout"""
        logger.info(f"Waiting for changedetection.io to be ready (timeout: {timeout}s)...")
        
        start_time = time.time()
        last_log_time = start_time
        
        while time.time() - start_time < timeout:
            try:
                # Try to connect to changedetection
                response = requests.get("http://changedetection:5000/api/v1/systeminfo", timeout=5)
                if response.status_code == 200:
                    logger.info("✅ changedetection.io is ready!")
                    return True
            except requests.exceptions.ConnectionError:
                # Normal during startup - log every 15 seconds to avoid spam
                current_time = time.time()
                if current_time - last_log_time > 15:
                    elapsed = int(current_time - start_time)
                    logger.info(f"Waiting for changedetection.io... ({elapsed}s/{timeout}s)")
                    last_log_time = current_time
            except Exception as e:
                logger.debug(f"Changedetection.io not ready yet: {e}")
            
            time.sleep(5)
        
        logger.error(f"❌ changedetection.io not ready after {timeout} seconds")
        return False
    
    def _setup_changedetection_with_retry(self, max_retries: int = 5, retry_delay: int = 10):
        """Setup changedetection.io watches with robust retry logic"""
        # First, wait for changedetection to be ready
        if not self._wait_for_changedetection(timeout=60):
            logger.error("Changedetection.io never became ready, skipping watch setup")
            return
        
        # Only setup watches on first run or if no watches exist
        if self.first_run:
            logger.info("First run - setting up initial changedetection.io watches")
            self._setup_initial_watches_with_retry(max_retries, retry_delay)
        else:
            logger.info("Continuing run - checking existing changedetection.io watches")
            self._verify_existing_watches()
    
    def _setup_initial_watches_with_retry(self, max_retries: int, retry_delay: int):
        """Setup initial watches with retry logic for first run"""
        for attempt in range(max_retries):
            try:
                logger.info(f"Attempt {attempt + 1}/{max_retries}: Setting up initial changedetection.io watches...")
                self.changedetection_service.setup_watches(self.change_detector)
                logger.info("✅ Initial changedetection.io watches setup completed successfully")
                return
                
            except Exception as e:
                logger.warning(f"Attempt {attempt + 1} failed: {e}")
                if attempt < max_retries - 1:
                    logger.info(f"Retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                else:
                    logger.error("❌ All attempts to setup initial changedetection.io watches failed")
                    logger.info("You can setup watches manually via http://localhost:5000")
    
    def _verify_existing_watches(self):
        """Verify existing watches are working on subsequent runs"""
        try:
            # Check if we can access the watches API
            watches = self.changedetection_service.get_existing_watches()
            if watches:
                logger.info(f"✅ Found {len(watches)} existing watches in changedetection.io")
            else:
                logger.warning("No existing watches found in changedetection.io")
        except Exception as e:
            logger.warning(f"Could not verify existing watches: {e}")
    
    def run_cycle(self) -> MonitoringCycleStats:
        """Run one complete monitoring cycle"""
        cycle_id = f"cycle_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        stats = MonitoringCycleStats(
            cycle_id=cycle_id,
            start_time=datetime.now(),
            first_run=self.first_run
        )
        
        logger.info("=" * 60)
        logger.info(f"Starting monitoring cycle {cycle_id}")
        logger.info(f"Run type: {'FIRST RUN 🆕' if self.first_run else 'CONTINUING RUN 🔄'}")
        logger.info("=" * 60)
        
        all_changes: List[DetectedChange] = []
        urls_checked = 0
        sheets_results = {'successful': 0, 'failed': 0}
        
        try:
            # Step 1: Check for content changes via changedetection.io
            logger.info("Step 1: Checking changedetection.io for content changes...")
            content_changes = self.changedetection_service.check_content_changes(self.change_detector)
            all_changes.extend(content_changes)
            logger.info(f"Content changes detected: {len(content_changes)}")
            
            # Step 2: Check for metadata changes on due URLs
            logger.info("Step 2: Checking metadata changes...")
            metadata_changes, checked_count = self._check_metadata_changes()
            all_changes.extend(metadata_changes)
            urls_checked = checked_count
            logger.info(f"Metadata changes detected: {len(metadata_changes)}")
            logger.info(f"URLs checked for metadata: {checked_count}")
            
            # Step 3: Log changes to Google Sheets if there are changes
            if all_changes:
                logger.info("Step 3: Logging changes to Google Sheets...")
                sheets_results = self._log_changes_to_sheets(all_changes)
                stats.sheets_logged = sheets_results['successful']
                stats.sheets_failed = sheets_results['failed']
                logger.info(f"Sheets logged: {stats.sheets_logged}, failed: {stats.sheets_failed}")
            else:
                logger.info("Step 3: No changes detected, skipping sheets logging")
            
            # Step 4: Generate reports
            logger.info("Step 4: Generating reports...")
            self._generate_reports(all_changes, stats)
            
            # Update final statistics
            stats.end_time = datetime.now()
            stats.duration_seconds = (stats.end_time - stats.start_time).total_seconds()
            stats.urls_checked = urls_checked
            stats.changes_detected = len(all_changes)
            stats.errors = sheets_results['failed']
                
            # Print summary
            self._log_cycle_summary(stats, all_changes)
            
            return stats
            
        except Exception as e:
            logger.error(f"Monitoring cycle failed with error: {e}")
            logger.exception("Full traceback:")
            stats.errors += 1
            stats.end_time = datetime.now()
            # Still try to generate error report
            try:
                self._generate_reports(all_changes, stats)
            except Exception as report_error:
                logger.error(f"Failed to generate error report: {report_error}")
            return stats
    
    def _check_metadata_changes(self) -> tuple[List[DetectedChange], int]:
        """Check for metadata changes on due URLs
        
        Returns:
            Tuple of (changes_detected, urls_checked_count)
        """
        due_urls = self.scheduler.get_due_urls()
        changes_detected = []
        urls_checked = 0
        
        if not due_urls:
            logger.info("No URLs due for metadata checking at this time")
            return changes_detected, urls_checked
        
        logger.info(f"Checking metadata for {len(due_urls)} due URLs")
        
        for due_url in due_urls:
            url = due_url['url']
            
            try:
                # Get current metadata
                current_meta = self.http_monitor.get_url_metadata(url)
                urls_checked += 1  # Count each URL we successfully check
                
                # Detect changes
                metadata_changes = self.change_detector.detect_metadata_changes(url, current_meta)
                
                if metadata_changes:
                    change = DetectedChange(
                        url=url,
                        changes=metadata_changes,
                        metadata=current_meta,
                        timestamp=datetime.now(),
                        change_source='direct_metadata',
                        priority=due_url['config'].priority
                    )
                    changes_detected.append(change)
                    logger.info(f"Metadata changes detected for {url}: {len(metadata_changes)} changes")
                else:
                    logger.debug(f"No metadata changes detected for {url}")
                
                # Update schedule
                self.scheduler.update_schedule(url)
                
                # Small delay between requests to be respectful
                time.sleep(0.5)
                
            except Exception as e:
                logger.error(f"Error checking metadata for {url}: {e}")
                # Continue with other URLs even if one fails
        
        # Save history after processing all URLs
        try:
            self.change_detector.save_history()
        except Exception as e:
            logger.error(f"Error saving change history: {e}")
        
        return changes_detected, urls_checked
    
    def _log_changes_to_sheets(self, changes: List[DetectedChange]) -> Dict[str, int]:
        """Log changes to Google Sheets and return results"""
        results = {'successful': 0, 'failed': 0}
        
        for change in changes:
            try:
                success = self.sheets_reporter.log_change(change)
                if success:
                    results['successful'] += 1
                    logger.debug(f"Successfully logged change for {change.url} to sheets")
                else:
                    results['failed'] += 1
                    logger.warning(f"Failed to log change for {change.url} to sheets")
            except Exception as e:
                logger.error(f"Error logging change for {change.url} to sheets: {e}")
                results['failed'] += 1
        
        return results
    
    def _generate_reports(self, changes: List[DetectedChange], stats: MonitoringCycleStats) -> None:
        """Generate all reports"""
        try:
            # Ensure report directory exists
            report_dir = Path("data/reports")
            report_dir.mkdir(parents=True, exist_ok=True)
            
            # JSON report for GitHub Actions
            json_report_path = self.gh_reporter.generate_json_report(changes, stats)
            logger.info(f"Generated JSON report: {json_report_path}")
            
            # GitHub Actions summary
            self.gh_reporter.print_github_summary(changes, stats)
            
        except Exception as e:
            logger.error(f"Error generating reports: {e}")
            # Don't raise the exception - reports are secondary to monitoring
    
    def _log_cycle_summary(self, stats: MonitoringCycleStats, changes: List[DetectedChange]) -> None:
        """Log cycle summary"""
        duration = stats.duration_seconds if stats.duration_seconds is not None else 0.0
        
        logger.info("=" * 60)
        if self.first_run:
            logger.info("🎉 FIRST RUN COMPLETED SUCCESSFULLY!")
        else:
            logger.info("✅ MONITORING CYCLE COMPLETED!")
        logger.info("=" * 60)
        logger.info(f"Cycle ID: {stats.cycle_id}")
        logger.info(f"Run Type: {'First Run 🆕' if self.first_run else 'Continuing Run 🔄'}")
        logger.info(f"Duration: {duration:.2f} seconds")
        logger.info(f"URLs checked: {stats.urls_checked}")
        logger.info(f"Changes detected: {stats.changes_detected}")
        logger.info(f"Sheets logged: {stats.sheets_logged}")
        logger.info(f"Sheets failed: {stats.sheets_failed}")
        logger.info(f"Errors: {stats.errors}")
        logger.info("=" * 60)
        
        if changes:
            logger.info("📈 CHANGES DETECTED:")
            for change in changes:
                change_types = [cd.change_type for cd in change.changes]
                logger.info(f"  - {change.url}: {change_types}")
        else:
            logger.info("📊 No changes detected in this cycle")
    
    def get_status(self) -> Dict[str, any]:
        """Get current service status"""
        return {
            'first_run': self.first_run,
            'scheduler': self.scheduler.get_status(),
            'sheets_connected': self.sheets_reporter.client is not None,
            'changedetection_available': self.changedetection_service.wait_for_service(timeout=5),
            'total_monitored_urls': len(self.config.url_configs),
            'data_directories': {
                'datastore': Path("data/datastore").exists(),
                'reports': Path("data/reports").exists(),
                'logs': Path("logs").exists()
            }
        }