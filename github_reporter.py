"""GitHub Actions reporting functionality"""
import json
import os
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any

from models import DetectedChange, MonitoringCycleStats
import logging

logger = logging.getLogger(__name__)


class GitHubReporter:
    """Handles reporting for GitHub Actions environment"""
    
    def __init__(self, reports_dir: str = "data/reports"):
        self.reports_dir = Path(reports_dir)
        self.reports_dir.mkdir(exist_ok=True, parents=True)
    
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
                    'sheets_enabled': False,  # This would be set by the main service
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
            with open(report_path, 'w') as f:
                json.dump(report_data, f, indent=2, default=str)
            
            logger.info(f"JSON report generated: {report_path}")
            return report_path
            
        except Exception as e:
            logger.error(f"Error generating JSON report: {e}")
            raise
    
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