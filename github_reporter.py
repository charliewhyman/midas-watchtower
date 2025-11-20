"""GitHub Actions reporting functionality"""
import json
import os
from datetime import datetime
from pathlib import Path
from typing import List

from models import DetectedChange, MonitoringCycleStats
import logging

logger = logging.getLogger(__name__)


class GitHubReporter:
    """Handles reporting for GitHub Actions environment"""
    
    def __init__(self, reports_dir: str = "data/reports"):
        self.reports_dir = Path(reports_dir)
        self._ensure_directory_writable()
    
    def _ensure_directory_writable(self):
        """Ensure reports directory exists and is writable"""
        try:
            self.reports_dir.mkdir(exist_ok=True, parents=True)
            # Try to make it writable
            try:
                self.reports_dir.chmod(0o777)
            except:
                pass  # Ignore permission errors on chmod
        except Exception as e:
            logger.warning(f"Could not create reports directory {self.reports_dir}: {e}")
            # Fallback to current directory
            self.reports_dir = Path(".")
            logger.info(f"Using fallback directory: {self.reports_dir.absolute()}")
    
    def is_github_actions(self) -> bool:
        """Check if running in GitHub Actions environment"""
        return os.getenv('GITHUB_ACTIONS') == 'true'
    
    def generate_json_report(self, changes: List[DetectedChange], stats: MonitoringCycleStats) -> Path:
        """Generate JSON report for GitHub Actions artifacts"""
        try:
            report_data = {
                'report_id': stats.cycle_id,
                'report_date': datetime.now().isoformat(),
                'changes_detected': [change.dict() for change in changes],
                'cycle_stats': stats.dict(),
                'summary': {
                    'total_changes': len(changes),
                    'first_run': stats.first_run,
                    'sheets_enabled': False,
                    'github_actions': self.is_github_actions()
                },
                'environment': {
                    'github_actions': self.is_github_actions(),
                    'run_id': os.getenv('GITHUB_RUN_ID'),
                    'run_attempt': os.getenv('GITHUB_RUN_ATTEMPT'),
                    'sha': os.getenv('GITHUB_SHA'),
                    'ref': os.getenv('GITHUB_REF')
                }
            }
            
            report_path = self.reports_dir / f"{stats.cycle_id}.json"
            
            # Ensure we can write to the file
            try:
                with open(report_path, 'w') as f:
                    json.dump(report_data, f, indent=2, default=str)
                logger.info(f"JSON report generated: {report_path}")
            except PermissionError:
                # Fallback to current directory
                fallback_path = Path(f"{stats.cycle_id}.json")
                with open(fallback_path, 'w') as f:
                    json.dump(report_data, f, indent=2, default=str)
                logger.info(f"JSON report generated in fallback location: {fallback_path}")
                return fallback_path
            
            return report_path
            
        except Exception as e:
            logger.error(f"Error generating JSON report: {e}")
            # Don't raise, just log and continue
            return Path("report_failed.json")
    
    def print_github_summary(self, changes: List[DetectedChange], stats: MonitoringCycleStats) -> None:
        """Print summary for GitHub Actions workflow"""
        if not self.is_github_actions():
            return
        
        duration = stats.duration_seconds if stats.duration_seconds is not None else 0.0

        print(f"\n=== AI SAFETY MONITORING SUMMARY ===")
        print(f"Cycle ID: {stats.cycle_id}")
        print(f"Duration: {duration:.2f}s")
        print(f"URLs checked: {stats.urls_checked}")
        print(f"Changes detected: {stats.changes_detected}")
        print(f"Sheets logged: {stats.sheets_logged}")
        print(f"Errors: {stats.errors}")
        
        if changes:
            print(f"\n=== CHANGES DETECTED ===")
            for change in changes:
                change_types = [cd.change_type for cd in change.changes]
                print(f"📄 {change.url}")
                print(f"   Types: {', '.join(change_types)}")
                print(f"   Source: {change.change_source}")
                print(f"   Time: {change.timestamp}")
                print()
        
        if stats.errors > 0:
            print("❌ Monitoring completed with errors")
        else:
            print("✅ Monitoring completed successfully")