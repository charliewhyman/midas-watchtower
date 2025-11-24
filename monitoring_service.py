"""Main monitoring service orchestrator"""
import time
import os
import json
import socket
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
    
    def _check_container_connectivity(self) -> bool:
        """Check basic container connectivity before attempting API calls"""
        logger.info("🔍 Checking container connectivity...")
        
        # Test DNS resolution
        try:
            ip = socket.gethostbyname('changedetection')
            logger.info(f"📡 DNS resolution successful: changedetection -> {ip}")
        except socket.gaierror as e:
            logger.error(f"🔍 DNS resolution failed: {e}")
            return False
        
        # Test basic TCP connectivity
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(10)
            result = sock.connect_ex(('changedetection', 5000))
            sock.close()
            
            if result == 0:
                logger.info("🔌 TCP port 5000 is accessible")
                return True
            else:
                logger.error(f"🔌 TCP port 5000 not accessible (error code: {result})")
                return False
        except Exception as e:
            logger.error(f"🔌 TCP connectivity test failed: {e}")
            return False
    
    def _wait_for_changedetection(self, timeout: int = 120) -> bool:
        """Wait for changedetection.io to be fully ready with comprehensive diagnostics"""
        logger.info(f"Waiting for changedetection.io to be ready (timeout: {timeout}s)...")
        
        start_time = time.time()
        attempt = 0
        
        while time.time() - start_time < timeout:
            attempt += 1
            elapsed = int(time.time() - start_time)
            
            try:
                # Test basic connectivity first
                response = requests.get(
                    "http://changedetection:5000/api/v1/systeminfo",
                    headers={'x-api-key': self.changedetection_service.api_key},
                    timeout=10
                )
                
                if response.status_code == 200:
                    # Verify we get meaningful data back
                    data = response.json()
                    if 'version' in data:
                        logger.info(f"changedetection.io ready after {attempt} attempts, version: {data.get('version')}")
                        return True
                    else:
                        logger.warning(f"⚠️ Service responding but invalid response: {data}")
                else:
                    logger.warning(f"⚠️ Service returned HTTP {response.status_code} (attempt {attempt})")
                    
            except requests.exceptions.ConnectionError as e:
                logger.warning(f"🔌 Connection error (attempt {attempt}, {elapsed}s): {e}")
            except requests.exceptions.Timeout as e:
                logger.warning(f"⏰ Request timeout (attempt {attempt}, {elapsed}s): {e}")
            except requests.exceptions.RequestException as e:
                logger.warning(f"🌐 Request exception (attempt {attempt}, {elapsed}s): {e}")
            except Exception as e:
                logger.warning(f"Unexpected error (attempt {attempt}, {elapsed}s): {e}")
            
            # Progressive backoff
            sleep_time = min(2 ** min(attempt, 5), 10)  # Exponential backoff, max 10s
            if time.time() + sleep_time - start_time > timeout:
                break  # Don't sleep if it would exceed timeout
            time.sleep(sleep_time)
        
        logger.error(f"changedetection.io not ready after {timeout} seconds and {attempt} attempts")
        return False
    
    def _sync_watches_with_config(self, max_retries: int, retry_delay: int):
        """Ensure changedetection watches match config.yaml (create missing ones)"""
        for attempt in range(max_retries):
            try:
                logger.info(f"Attempt {attempt + 1}/{max_retries}: Syncing changedetection.io watches with config.yaml...")
                
                # Get existing watches from changedetection.io
                existing_watches = self.changedetection_service.get_existing_watches()
                
                if existing_watches is None:
                    logger.error("Failed to retrieve existing watches (auth error)")
                    raise Exception("Authentication failed")
                
                existing_urls = {watch.get('url') for watch in existing_watches.values() if watch.get('url')}
                
                logger.info(f"📊 Found {len(existing_urls)} existing watches in changedetection.io")
                if existing_urls:
                    logger.info(f"📋 Existing watches:")
                    for url in sorted(existing_urls):
                        logger.info(f"   - {url}")
                
                # Get URLs from config.yaml
                config_urls = {url_config.url for url_config in self.config.url_configs}
                logger.info(f"📊 Found {len(config_urls)} URLs in config.yaml")
                if config_urls:
                    logger.info(f"📋 Config URLs:")
                    for url in sorted(config_urls):
                        logger.info(f"   - {url}")
                
                # Find missing URLs that need to be added
                missing_urls = config_urls - existing_urls
                
                if missing_urls:
                    logger.info(f"📝 Found {len(missing_urls)} URLs from config.yaml not in changedetection.io")
                    logger.info(f"🔧 Creating missing watches:")
                    for url in sorted(missing_urls):
                        logger.info(f"   - {url}")
                    
                    # Create watches for missing URLs using the public method
                    created_count = 0
                    failed_count = 0
                    
                    for url_config in self.config.url_configs:
                        if url_config.url in missing_urls:
                            logger.info(f"➕ Creating watch for: {url_config.url}")
                            success = self.changedetection_service.create_watch(url_config, self.change_detector)
                            
                            if success:
                                created_count += 1
                                logger.info(f"✅ Successfully created watch for {url_config.url}")
                            else:
                                failed_count += 1
                                logger.error(f"❌ Failed to create watch for {url_config.url}")
                    
                    logger.info(f"✅ Watch creation summary: {created_count} succeeded, {failed_count} failed")
                    
                    if failed_count > 0 and created_count == 0:
                        raise Exception(f"Failed to create any watches ({failed_count} failures)")
                        
                else:
                    logger.info(f"✅ All {len(config_urls)} config URLs already exist in changedetection.io")
                    logger.info(f"🎯 No new watches needed - system is in sync!")
                
                return
                
            except Exception as e:
                logger.warning(f"❌ Attempt {attempt + 1} failed: {e}")
                logger.exception("Full traceback:")
                if attempt < max_retries - 1:
                    logger.info(f"⏳ Retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                else:
                    logger.error("💥 All attempts to sync changedetection.io watches failed")
                    logger.error("💡 Check the logs above for specific errors")
                
    def _setup_changedetection_with_retry(self, max_retries: int = 3, retry_delay: int = 15):
        """Setup changedetection.io watches with robust retry logic"""
        logger.info("Setting up changedetection.io integration...")
        
        # First, check basic container connectivity
        if not self._check_container_connectivity():
            logger.error("Basic container connectivity failed - cannot setup changedetection.io")
            return
        
        # Wait for changedetection to be fully ready
        if not self._wait_for_changedetection(timeout=120):
            logger.error("Changedetection.io never became ready, skipping watch setup")
            return
        
        # Always sync watches from config, not just on first run
        logger.info("🔄 Syncing changedetection.io watches from config.yaml")
        self._sync_watches_with_config(max_retries, retry_delay)
    
    def _setup_initial_watches_with_retry(self, max_retries: int, retry_delay: int):
        """Setup initial watches with retry logic for first run"""
        for attempt in range(max_retries):
            try:
                logger.info(f"🔄 Attempt {attempt + 1}/{max_retries}: Setting up initial changedetection.io watches...")
                self.changedetection_service.setup_watches(self.change_detector)
                logger.info("Initial changedetection.io watches setup completed successfully")
                return
                
            except Exception as e:
                logger.warning(f"Attempt {attempt + 1} failed: {e}")
                if attempt < max_retries - 1:
                    logger.info(f"Retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                    
                    # Restart changedetection service between attempts if possible
                    try:
                        self._restart_changedetection_service()
                    except Exception as restart_error:
                        logger.warning(f"Could not restart changedetection service: {restart_error}")
                else:
                    logger.error("💥 All attempts to setup initial changedetection.io watches failed")
                    logger.info("💡 You can setup watches manually via http://localhost:5000")
    
    def _restart_changedetection_service(self):
        """Attempt to restart changedetection service"""
        logger.info("🔄 Attempting to restart changedetection service...")
        try:
            # This would use docker compose in production, but for now just log
            logger.info("📝 In production, this would restart the changedetection container")
            # Example: subprocess.run(["docker", "compose", "restart", "changedetection"])
            time.sleep(10)  # Wait for restart
        except Exception as e:
            logger.warning(f"Could not restart changedetection service: {e}")
    
    def _verify_existing_watches(self):
        """Verify existing watches are working on subsequent runs"""
        try:
            # Check if we can access the watches API
            watches = self.changedetection_service.get_existing_watches()
            if watches:
                logger.info(f"Found {len(watches)} existing watches in changedetection.io")
                
                # Test that at least one watch is accessible
                if watches:
                    first_watch_uuid = list(watches.keys())[0]
                    try:
                        watch_data = self.changedetection_service.get_watch(first_watch_uuid)
                        if watch_data:
                            logger.info(f"Successfully accessed watch {first_watch_uuid}")
                        else:
                            logger.warning(f"⚠️ Could not access watch data for {first_watch_uuid}")
                    except Exception as e:
                        logger.warning(f"⚠️ Error accessing watch {first_watch_uuid}: {e}")
            else:
                logger.warning("⚠️ No existing watches found in changedetection.io")
        except Exception as e:
            logger.warning(f"⚠️ Could not verify existing watches: {e}")
    
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
            logger.info("MONITORING CYCLE COMPLETED!")
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
            'changedetection_available': self._wait_for_changedetection(timeout=5),
            'container_connectivity': self._check_container_connectivity(),
            'total_monitored_urls': len(self.config.url_configs),
            'data_directories': {
                'datastore': Path("data/datastore").exists(),
                'reports': Path("data/reports").exists(),
                'logs': Path("logs").exists()
            }
        }