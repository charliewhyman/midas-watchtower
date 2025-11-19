"""Main monitoring service orchestrator"""
import time
from datetime import datetime
from typing import Dict, List, Optional
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
        
        # Initialize components
        self.http_monitor = HttpMonitor(self.config)
        self.change_detector = ChangeDetector(Path(self.config.settings.history_file))
        self.changedetection_service = ChangedetectionService(self.config)
        self.sheets_reporter = GoogleSheetsReporter(self.config)
        self.gh_reporter = GitHubReporter()
        self.scheduler = UrlScheduler(self.config)
        
        # Initialize changedetection.io watches with robust retry logic
        self._setup_changedetection_with_retry()
        
        logger.info("Monitoring service initialized successfully")
    
    def _wait_for_changedetection(self, timeout: int = 120) -> bool:
        """Wait for changedetection.io to be ready"""
        logger.info(f"Waiting for changedetection.io to be ready (timeout: {timeout}s)...")
        
        start_time = time.time()
        last_log_time = start_time
        
        while time.time() - start_time < timeout:
            try:
                # Try to connect to changedetection
                response = requests.get("http://changedetection:5000", timeout=5)
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
        if not self._wait_for_changedetection(timeout=120):
            logger.error("Changedetection.io never became ready, skipping watch setup")
            return
        
        # Now try to setup watches with retries
        for attempt in range(max_retries):
            try:
                logger.info(f"Attempt {attempt + 1}/{max_retries}: Setting up changedetection.io watches...")
                self.changedetection_service.setup_watches(self.change_detector)
                logger.info("✅ Changedetection.io watches setup completed successfully")
                return
                
            except Exception as e:
                logger.warning(f"Attempt {attempt + 1} failed: {e}")
                if attempt < max_retries - 1:
                    logger.info(f"Retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                else:
                    logger.error("❌ All attempts to setup changedetection.io watches failed")
                    logger.info("You can setup watches manually via http://localhost:5000")
    
    def run_cycle(self) -> MonitoringCycleStats:
        """Run one complete monitoring cycle"""
        cycle_id = f"cycle_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        stats = MonitoringCycleStats(
            cycle_id=cycle_id,
            start_time=datetime.now(),
            first_run=self.change_detector.is_first_run()
        )
        
        logger.info("=" * 50)
        logger.info(f"Starting monitoring cycle {cycle_id}...")
        logger.info("=" * 50)
        
        all_changes: List[DetectedChange] = []
        
        try:
            # Step 1: Check for content changes via changedetection.io
            logger.info("Step 1: Checking changedetection.io for content changes...")
            content_changes = self.changedetection_service.check_content_changes(self.change_detector)
            all_changes.extend(content_changes)
            
            # Step 2: Check for metadata changes on due URLs
            logger.info("Step 2: Checking metadata changes...")
            metadata_changes = self._check_metadata_changes()
            all_changes.extend(metadata_changes)
            
            # Step 3: Log changes to Google Sheets
            logger.info("Step 3: Logging changes to Google Sheets...")
            sheets_results = self._log_changes_to_sheets(all_changes)
            stats.sheets_logged = sheets_results['successful']
            stats.sheets_failed = sheets_results['failed']
            
            # Step 4: Generate reports
            logger.info("Step 4: Generating reports...")
            self._generate_reports(all_changes, stats)
            
            # Update final statistics
            stats.end_time = datetime.now()
            stats.duration_seconds = (stats.end_time - stats.start_time).total_seconds()
            stats.urls_checked = len(self.scheduler.get_due_urls())
            stats.changes_detected = len(all_changes)
            stats.errors = sheets_results['failed']  # Could be expanded to track other errors
            
            # Print summary
            self._log_cycle_summary(stats, all_changes)
            
            return stats
            
        except Exception as e:
            logger.error(f"Monitoring cycle failed with error: {e}")
            stats.errors += 1
            stats.end_time = datetime.now()
            return stats
    
    def _check_metadata_changes(self) -> List[DetectedChange]:
        """Check for metadata changes on due URLs"""
        due_urls = self.scheduler.get_due_urls()
        changes_detected = []
        
        if not due_urls:
            return changes_detected
        
        logger.info(f"Checking metadata for {len(due_urls)} due URLs")
        
        for due_url in due_urls:
            url = due_url['url']
            
            try:
                # Get current metadata
                current_meta = self.http_monitor.get_url_metadata(url)
                
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
                
                # Update schedule
                self.scheduler.update_schedule(url)
                
                # Small delay between requests to be respectful
                time.sleep(0.5)
                
            except Exception as e:
                logger.error(f"Error checking metadata for {url}: {e}")
                # Continue with other URLs even if one fails
        
        # Save history after processing all URLs
        self.change_detector.save_history()
        
        return changes_detected
    
    def _log_changes_to_sheets(self, changes: List[DetectedChange]) -> Dict[str, int]:
        """Log changes to Google Sheets and return results"""
        results = {'successful': 0, 'failed': 0}
        
        for change in changes:
            try:
                success = self.sheets_reporter.log_change(change)
                if success:
                    results['successful'] += 1
                else:
                    results['failed'] += 1
            except Exception as e:
                logger.error(f"Failed to log change for {change.url} to sheets: {e}")
                results['failed'] += 1
        
        return results
    
    def _generate_reports(self, changes: List[DetectedChange], stats: MonitoringCycleStats) -> None:
        """Generate all reports"""
        try:
            # JSON report for GitHub Actions
            json_report_path = self.gh_reporter.generate_json_report(changes, stats)
            logger.info(f"Generated JSON report: {json_report_path}")
            
            # GitHub Actions summary
            self.gh_reporter.print_github_summary(changes, stats)
            
        except Exception as e:
            logger.error(f"Error generating reports: {e}")
    
    def _log_cycle_summary(self, stats: MonitoringCycleStats, changes: List[DetectedChange]) -> None:
        """Log cycle summary"""
        duration = stats.duration_seconds if stats.duration_seconds is not None else 0.0
        
        logger.info("=" * 50)
        logger.info("MONITORING CYCLE COMPLETED!")
        logger.info(f"Cycle ID: {stats.cycle_id}")
        logger.info(f"Duration: {duration:.2f} seconds")
        logger.info(f"URLs checked: {stats.urls_checked}")
        logger.info(f"Changes detected: {stats.changes_detected}")
        logger.info(f"Sheets logged: {stats.sheets_logged}")
        logger.info(f"Sheets failed: {stats.sheets_failed}")
        logger.info(f"Errors: {stats.errors}")
        logger.info("=" * 50)
        
        if changes:
            logger.info("CHANGES DETECTED:")
            for change in changes:
                change_types = [cd.change_type for cd in change.changes]
                logger.info(f"  - {change.url}: {change_types}")
    
    def get_status(self) -> Dict[str, any]:
        """Get current service status"""
        return {
            'scheduler': self.scheduler.get_status(),
            'sheets_connected': self.sheets_reporter.client is not None,
            'changedetection_available': self.changedetection_service.wait_for_service(timeout=5),
            'first_run': self.change_detector.is_first_run(),
            'total_monitored_urls': len(self.config.url_configs)
        }