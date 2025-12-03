"""Main monitoring service orchestrator"""
import time
import os
import json
from datetime import datetime
from typing import Dict, List, Any
from pathlib import Path


from config import AppConfig
from http_monitor import HttpMonitor
from change_detector import ChangeDetector
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
        
        # Log configuration summary
        config_summary = self.config.get_config_summary()
        logger.info(f"📊 Configuration Summary:")
        logger.info(f"   • Central check interval: {config_summary['central_check_interval']}s")
        logger.info(f"   • Polling interval: {config_summary['polling_interval']}s")
        logger.info(f"   • Total URLs: {config_summary['total_urls']}")
        logger.info(f"   • Priority distribution: {config_summary['priority_distribution']}")
        logger.info(f"   • Type distribution: {config_summary['type_distribution']}")
        
        # Detect first run status
        self.first_run = self._detect_first_run()
        logger.info(f"First run detected: {self.first_run}")
        
        # Initialize components with first_run context
        self.http_monitor = HttpMonitor(self.config)
        # Pass settings through so ChangeDetector can use configurable thresholds
        self.change_detector = ChangeDetector(Path(self.config.settings.history_file), settings=self.config.settings)
        self.sheets_reporter = GoogleSheetsReporter(self.config)
        self.gh_reporter = GitHubReporter()
        self.url_scheduler = UrlScheduler(self.config)  # Updated to use central interval
        
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
        
        # Check for previous monitoring reports
        reports_path = Path("data/reports")
        if reports_path.exists():
            report_files = list(reports_path.glob("cycle_*.json"))
            if report_files:
                logger.info(f"Found {len(report_files)} previous reports")
                return False
        
        # Check change detector history (support both legacy 'history' and current 'metadata_history')
        history_path = Path(self.config.settings.history_file)
        if history_path.exists():
            try:
                with open(history_path, 'r', encoding='utf-8') as f:
                    history_data = json.load(f)
                    # Ensure the loaded JSON is a dict before accessing keys.
                    if not isinstance(history_data, dict):
                        logger.debug("History file JSON is not an object/dict; ignoring for first-run detection")
                    else:
                        # Support legacy format that used 'history' key as well as the current 'metadata_history'
                        has_legacy_history = bool(history_data.get('history'))
                        has_metadata_history = bool(history_data.get('metadata_history'))
                        if has_legacy_history or has_metadata_history:
                            logger.info("Found existing change history")
                            return False
            except (json.JSONDecodeError, ValueError, TypeError, OSError) as e:
                logger.debug(f"Could not parse history file: {e}")
        
        # No existing data found - this is the first run
        logger.info("No existing monitoring data found - first run")
        return True
    
    def run_cycle(self) -> MonitoringCycleStats:
        """Run one complete monitoring cycle"""
        cycle_id = f"cycle_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        stats = MonitoringCycleStats(
            cycle_id=cycle_id,
            start_time=datetime.now(),
            first_run=self.first_run
        )
        # Ensure numeric defaults for stats counters to avoid TypeErrors
        stats.errors = int(stats.errors or 0)
        
        logger.info("=" * 60)
        logger.info(f"Starting monitoring cycle {cycle_id}")
        logger.info(f"Run type: {'FIRST RUN 🆕' if self.first_run else 'CONTINUING RUN 🔄'}")
        central_interval = getattr(self.config, 'central_check_interval', None)
        # If not present directly on config, try nested settings or scheduling
        if central_interval is None:
            central_interval = getattr(getattr(self.config, 'settings', object()), 'central_check_interval', None)
        if central_interval is None:
            central_interval = getattr(getattr(self.config, 'scheduling', object()), 'central_check_interval', None)
        logger.info(f"Central check interval: {central_interval}s")
        logger.info("=" * 60)
        
        all_changes: List[DetectedChange] = []
        urls_checked = 0
        sheets_results = {'successful': 0, 'failed': 0}
        
        try:
            # Step 1: Check for metadata changes on due URLs
            logger.info("Step 1: Checking metadata changes...")
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
            
        except (RuntimeError, OSError, ValueError) as e:
            logger.error(f"Monitoring cycle failed with error: {e}")
            logger.exception("Full traceback:")
            stats.errors += 1
            stats.end_time = datetime.now()
            # Still try to generate error report
            try:
                self._generate_reports(all_changes, stats)
            except (OSError, RuntimeError) as report_error:
                logger.error(f"Failed to generate error report: {report_error}")
            return stats
    
    def _check_metadata_changes(self) -> tuple[List[DetectedChange], int]:
        """Check for metadata changes on due URLs using central interval
        
        Returns:
            Tuple of (changes_detected, urls_checked_count)
        """
        raw_due = self.url_scheduler.get_due_urls()  # Updated to use central interval
        try:
            due_urls = list(raw_due) if raw_due is not None else []
        except TypeError:
            logger.warning("url_scheduler.get_due_urls() returned a non-iterable; treating as empty list")
            due_urls = []

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
                
                # Update schedule using central interval
                self.url_scheduler.mark_url_as_checked(url, success=True)
                
                # Small delay between requests to be respectful
                time.sleep(0.5)
                
            except (requests.RequestException, RuntimeError, ValueError, TypeError, OSError) as e:
                logger.error(f"Error checking metadata for {url}: {e}")
                # Mark as checked but schedule retry sooner
                self.url_scheduler.mark_url_as_checked(url, success=False)
        
        # Save history after processing all URLs
        try:
            self.change_detector.save_history()
        except (OSError, IOError) as e:
            logger.error(f"Error saving change history: {e}")
        
        return changes_detected, urls_checked
    
    def _log_changes_to_sheets(self, changes: List[DetectedChange]) -> Dict[str, int]:
        """Log changes to Google Sheets and return results"""
        results = {'successful': 0, 'failed': 0}
        try:
            successful, failed = self.sheets_reporter.log_changes(changes)
            results['successful'] = successful
            results['failed'] = failed
            return results
        except (RuntimeError, OSError, ValueError) as e:
            logger.error(f"Batch logging to Sheets failed: {e}")
            # Fallback: count all as failed
            results['failed'] = len(changes)
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
            
        except (OSError, RuntimeError) as e:
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
        logger.info(f"Central Check Interval: {self.config.central_check_interval}s")
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
    
    def get_status(self) -> Dict[str, Any]:
        """Get current service status"""
        scheduler_status = self.url_scheduler.get_status()
        
        return {
            'first_run': self.first_run,
            'scheduler': scheduler_status,
            'sheets_connected': getattr(self.sheets_reporter, "client", None) is not None,
            'container_connectivity': False,
            'total_monitored_urls': len(getattr(self.config, 'url_configs', getattr(self.config, 'monitored_urls', []))),
            'central_check_interval': getattr(self.config, 'central_check_interval', None),
            'polling_interval': getattr(getattr(self.config, 'scheduling', object()), 'polling_interval', None),
            'data_directories': {
                'datastore': Path("data/datastore").exists(),
                'reports': Path("data/reports").exists(),
                'logs': Path("logs").exists()
            }
        }