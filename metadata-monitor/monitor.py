import requests
import hashlib
import json
import uvicorn
import yaml
import time
import schedule
from datetime import datetime, timedelta
from pathlib import Path
import logging
import os
from fastapi import FastAPI
from google.oauth2.service_account import Credentials
import pandas as pd
import gspread

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/monitor.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

app = FastAPI(title="AI Safety Metadata Monitor")

class GoogleSheetsReporter:
    def __init__(self, credentials_file="google-sheets-credentials.json"):
        self.credentials_file = credentials_file
        self.setup_client()
    
    def setup_client(self):
        """Setup Google Sheets client"""
        try:
            scopes = [
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive"
            ]
            
            creds = Credentials.from_service_account_file(
                self.credentials_file, scopes=scopes
            )
            self.client = gspread.authorize(creds)
            logger.info("Google Sheets client initialized")
        except Exception as e:
            logger.error(f"Failed to setup Google Sheets: {e}")
            self.client = None
    
    def ensure_spreadsheet_exists(self, spreadsheet_name="AI Safety Changes Monitor"):
        """Create or get existing spreadsheet"""
        if not self.client:
            return None
            
        try:
            spreadsheet = self.client.open(spreadsheet_name)
            logger.info(f"Using existing spreadsheet: {spreadsheet_name}")
        except gspread.SpreadsheetNotFound:
            spreadsheet = self.client.create(spreadsheet_name)
            logger.info(f"Created new spreadsheet: {spreadsheet_name}")
        
        return spreadsheet
    
    def setup_sheets_structure(self, spreadsheet):
        """Setup the sheets with proper structure"""
        try:
            worksheet = spreadsheet.worksheet("Changes_Log")
            logger.info("Changes_Log sheet already exists")
        except gspread.WorksheetNotFound:
            worksheet = spreadsheet.add_worksheet(
                title="Changes_Log", 
                rows=1000, 
                cols=11
            )
            worksheet.append_row([
                "Timestamp", "URL", "Change Type", "Change Details", 
                "Status Code", "Content Type", "Final URL", "Source",
                "Priority", "Resolved", "Notes"
            ])
            logger.info("Created Changes_Log sheet")
    
    def log_change_to_sheets(self, change_data):
        """Log a change to Google Sheets"""
        if not self.client:
            logger.error("Google Sheets client not available")
            return False
        
        try:
            spreadsheet = self.ensure_spreadsheet_exists()
            if not spreadsheet:
                return False
            
            self.setup_sheets_structure(spreadsheet)
            
            # Prepare change row
            change_row = self.prepare_change_row(change_data)
            
            # Append to Changes_Log sheet
            changes_sheet = spreadsheet.worksheet("Changes_Log")
            changes_sheet.append_row(change_row)
            
            logger.info(f"Logged change to Google Sheets: {change_data['url']}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to log change to Google Sheets: {e}")
            return False
    
    def prepare_change_row(self, change_data):
        """Prepare a row for the Changes_Log sheet"""
        changes = change_data.get('changes', {})
        metadata = change_data.get('metadata', {})
        
        # Extract change types and details
        change_types = []
        change_details = []
        
        for change_type, details in changes.items():
            change_types.append(change_type)
            if change_type == 'content_change':
                change_details.append("Content modified")
            elif change_type == 'metadata_change':
                for meta_type, meta_details in details.items():
                    if meta_type == 'status':
                        change_details.append(f"Status: {meta_details.get('old')}→{meta_details.get('new')}")
                    elif meta_type == 'content_type':
                        change_details.append(f"Content-Type: {meta_details.get('old')}→{meta_details.get('new')}")
        
        return [
            change_data.get('timestamp', datetime.now().isoformat()),
            change_data.get('url', ''),
            ', '.join(change_types),
            '; '.join(change_details),
            metadata.get('status_code', ''),
            metadata.get('headers', {}).get('content-type', ''),
            metadata.get('final_url', ''),
            change_data.get('change_source', ''),
            'medium',  # Default priority
            'FALSE',   # Not resolved
            ''         # Notes
        ]

class GitHubActionsReporter:
    """Minimal reporter for GitHub Actions artifacts"""
    def __init__(self, data_dir="data"):
        self.data_dir = Path(data_dir)
        self.reports_dir = self.data_dir / "reports"
        self.reports_dir.mkdir(exist_ok=True)
    
    def generate_json_report(self, changes_detected, cycle_stats):
        """Generate JSON report for GitHub Actions artifacts"""
        report_data = {
            'report_id': f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            'report_date': datetime.now().isoformat(),
            'changes_detected': changes_detected,
            'cycle_stats': cycle_stats,
            'summary': {
                'total_changes': len(changes_detected),
            }
        }
        
        # Save JSON report for GitHub Actions artifacts
        report_path = self.reports_dir / f"{report_data['report_id']}.json"
        with open(report_path, 'w') as f:
            json.dump(report_data, f, indent=2, default=str)
        
        # Also create a latest.json for easy access
        latest_path = self.reports_dir / "latest.json"
        with open(latest_path, 'w') as f:
            json.dump(report_data, f, indent=2, default=str)
        
        return report_path

class AISafetyMonitor:
    def __init__(self, config_path="config.yaml"):
        self.load_config(config_path)
        self.setup_data_directory()
        self.setup_session()
        self.setup_changedetection_watches()
        self.setup_scheduled_checks()
        self.cycle_stats = {
            'start_time': None,
            'end_time': None,
            'urls_checked': 0,
            'errors': 0
        }
        self.sheets_reporter = GoogleSheetsReporter()
        # ADDED: Minimal GitHub Actions reporter
        self.gh_reporter = GitHubActionsReporter()
        
        logger.info("AI Safety Monitor initialized")
    
    def load_config(self, config_path):
        """Load configuration from YAML file"""
        try:
            with open(config_path, 'r') as f:
                self.config = yaml.safe_load(f)
            logger.info("Configuration loaded successfully")
        except Exception as e:
            logger.error(f"Error loading config: {e}")
            self.config = {'monitored_urls': []}
    
    def setup_data_directory(self):
        """Ensure data directory exists"""
        Path("data").mkdir(exist_ok=True)
        Path("logs").mkdir(exist_ok=True)
        self.history_file = Path("data/url_history.json")
        
        if not self.history_file.exists():
            with open(self.history_file, 'w') as f:
                json.dump({}, f)
    
    def setup_session(self):
        """Setup requests session"""
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        })
    
    def get_changedetection_headers(self):
        """Return headers for changedetection.io API"""
        api_key = os.getenv("CHANGEDETECTION_API_KEY")
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["x-api-key"] = api_key
        return headers

    def setup_changedetection_watches(self):
        """Ensure all URLs are monitored by changedetection.io with config intervals"""
        logger.info("Setting up changedetection.io watches with config intervals...")
        
        base_url = os.getenv("CHANGEDETECTION_URL", "http://changedetection:5000")
        headers = self.get_changedetection_headers()

        # Get existing watches
        try:
            response = requests.get(f"{base_url}/api/v1/watch", headers=headers, timeout=10)
            existing_watches = {watch['url']: watch for watch in response.json() if watch.get('tag') == 'ai-safety'}
        except Exception as e:
            logger.error(f"Failed to fetch existing watches: {e}")
            existing_watches = {}

        # Add or update URLs
        for url_config in self.config.get('monitored_urls', []):
            url = url_config['url']
            check_interval = url_config.get('check_interval', 3600)
            
            if url in existing_watches:
                # Update existing watch if interval changed
                existing_watch = existing_watches[url]
                if existing_watch.get('check_interval') != check_interval:
                    try:
                        update_payload = {
                            "check_interval": check_interval
                        }
                        response = requests.patch(
                            f"{base_url}/api/v1/watch/{existing_watch['uuid']}",
                            json=update_payload,
                            headers=headers,
                            timeout=10
                        )
                        if response.status_code == 200:
                            logger.info(f"✓ Updated interval for {url}: {check_interval}s")
                        else:
                            logger.warning(f"✗ Failed to add {url}: {response.status_code}, response: {response.text}")
                    except Exception as e:
                        logger.error(f"Error updating {url}: {e}")
                else:
                    logger.info(f"✓ Already configured in changedetection.io: {url}")
            else:
                # Add new watch
                payload = {
                    "url": url,
                    "tag": "ai-safety",
                    "title": f"AI Safety - {url}",
                    "check_interval": check_interval
                }
                
                try:
                    response = requests.post(f"{base_url}/api/v1/watch", json=payload, headers=headers, timeout=10)
                    if response.status_code in [200, 201]:
                        logger.info(f"✓ Added to changedetection.io: {url} (interval: {check_interval}s)")
                    else:
                        logger.warning(f"✗ Failed to add {url}: {response.status_code}")
                except Exception as e:
                    logger.error(f"Error adding {url}: {e}")

    def setup_scheduled_checks(self):
        """Setup scheduled checks based on config intervals"""
        self.url_schedules = {}
        
        for url_config in self.config.get('monitored_urls', []):
            url = url_config['url']
            check_interval = url_config.get('check_interval', 3600)
            
            self.url_schedules[url] = {
                'check_interval': check_interval,
                'type': url_config.get('type', 'policy'),
                'priority': url_config.get('priority', 'medium'),
                'last_checked': None,
                'next_check': datetime.now()
            }
    
    def get_urls_due_for_check(self):
        """Get URLs that are due for metadata checking"""
        due_urls = []
        current_time = datetime.now()
        
        for url, schedule_info in self.url_schedules.items():
            next_check = schedule_info.get('next_check')
            
            if not next_check or current_time >= next_check:
                due_urls.append({
                    'url': url,
                    'config': schedule_info
                })
        
        return due_urls

    def update_url_schedule(self, url):
        """Update next check time for a URL"""
        if url in self.url_schedules:
            check_interval = self.url_schedules[url]['check_interval']
            self.url_schedules[url].update({
                'last_checked': datetime.now(),
                'next_check': datetime.now() + timedelta(seconds=check_interval)
            })

    def get_url_metadata(self, url):
        """Get basic URL metadata"""
        try:
            response = self.session.head(url, timeout=10, allow_redirects=True)
            
            metadata = {
                'url': url,
                'timestamp': datetime.now().isoformat(),
                'status_code': response.status_code,
                'headers': dict(response.headers),
                'final_url': response.url,
            }
            
            return metadata
            
        except Exception as e:
            logger.error(f"Error checking {url}: {e}")
            return {
                'url': url,
                'timestamp': datetime.now().isoformat(),
                'error': str(e),
                'status_code': None
            }

    def detect_metadata_changes(self, old_meta, new_meta):
        """Detect metadata changes"""
        if not old_meta:
            return {'new_url': {'type': 'new_url'}}
        
        changes = {}
        
        # Status code changes
        if old_meta.get('status_code') != new_meta.get('status_code'):
            changes['status'] = {
                'old': old_meta.get('status_code'),
                'new': new_meta.get('status_code')
            }
        
        # Content-type changes
        old_type = old_meta.get('headers', {}).get('content-type')
        new_type = new_meta.get('headers', {}).get('content-type')
        if old_type != new_type:
            changes['content_type'] = {
                'old': old_type,
                'new': new_type
            }
        
        # Redirect changes
        if old_meta.get('final_url') != new_meta.get('final_url'):
            changes['redirect'] = {
                'old': old_meta.get('final_url'),
                'new': new_meta.get('final_url')
            }
        
        return changes

    def check_changedetection_content_changes(self):
        """Check changedetection.io for content changes"""
        logger.info("Checking changedetection.io for content changes...")
        
        base_url = os.getenv("CHANGEDETECTION_URL", "http://changedetection:5000")
        headers = self.get_changedetection_headers()
        changes_detected = []
        
        try:
            # Load last check times
            with open(self.history_file, 'r') as f:
                history = json.load(f)
            
            response = requests.get(f"{base_url}/api/v1/watch", headers=headers, timeout=10)
            watches = response.json()
            
            for watch in watches:
                if watch.get('tag') == 'ai-safety':
                    url = watch['url']

                    # Get watch details
                    watch_detail = requests.get(
                        f"{base_url}/api/v1/watch/{watch['uuid']}", 
                        headers=headers, 
                        timeout=10
                    ).json()

                    last_changed = watch_detail.get('last_changed')
                    last_checked_str = history.get(url, {}).get('last_content_check')

                    # Determine if we should register a content change
                    if not last_checked_str:
                        # First time checking this URL; treat as new
                        changes_detected.append({
                            'url': url,
                            'changes': {'content_change': {'source': 'changedetection.io', 'status': 'new_watch'}},
                            'timestamp': datetime.now().isoformat(),
                            'change_source': 'changedetection_content'
                        })
                        # Initialize last_content_check in history
                        if url not in history:
                            history[url] = {}
                        history[url]['last_content_check'] = datetime.now().isoformat()
                    elif last_changed:
                        # If last_changed exists, only mark as change if it's newer than last check
                        last_changed_dt = datetime.fromisoformat(last_changed.replace('Z', '+00:00'))
                        if last_changed_dt > datetime.fromisoformat(last_checked_str):
                            changes_detected.append({
                                'url': url,
                                'changes': {'content_change': {'source': 'changedetection.io'}},
                                'timestamp': datetime.now().isoformat(),
                                'change_source': 'changedetection_content'
                            })
                            history[url]['last_content_check'] = datetime.now().isoformat()
                    
                    # Save updated history
                    with open(self.history_file, 'w') as f:
                        json.dump(history, f, indent=2)
                
        except Exception as e:
            logger.error(f"Error checking changedetection.io: {e}")
        
        return changes_detected

    def check_metadata_changes(self):
        """Check for metadata changes on due URLs"""
        due_urls = self.get_urls_due_for_check()
        changes_detected = []
        
        if not due_urls:
            return changes_detected
        
        logger.info(f"Checking metadata for {len(due_urls)} due URLs")
        
        try:
            with open(self.history_file, 'r') as f:
                history = json.load(f)
        except Exception as e:
            logger.error(f"Error loading history: {e}")
            history = {}
        
        for due_url in due_urls:
            url = due_url['url']
            
            current_meta = self.get_url_metadata(url)
            previous_meta = history.get(url, {}).get('metadata')
            
            changes = self.detect_metadata_changes(previous_meta, current_meta)
            if changes:
                changes_detected.append({
                    'url': url,
                    'changes': {'metadata_change': changes},
                    'metadata': current_meta,
                    'timestamp': datetime.now().isoformat(),
                    'change_source': 'direct_metadata'
                })
            
            # Update history
            if url not in history:
                history[url] = {}
            history[url]['metadata'] = current_meta
            history[url]['last_metadata_check'] = datetime.now().isoformat()
            
            # Update schedule
            self.update_url_schedule(url)
            
            # Small delay between requests
            time.sleep(1)
        
        # Save history
        with open(self.history_file, 'w') as f:
            json.dump(history, f, indent=2)
        
        return changes_detected

    def run_monitoring_cycle(self):
        """Run one complete monitoring cycle"""
        logger.info("Starting monitoring cycle...")
        
        all_changes = []
        
        self.cycle_stats['start_time'] = datetime.now()
        
        # 1. Check for content changes via changedetection.io
        content_changes = self.check_changedetection_content_changes()
        all_changes.extend(content_changes)
        
        # 2. Check for metadata changes on due URLs
        metadata_changes = self.check_metadata_changes()
        all_changes.extend(metadata_changes)
        
        # Log changes to Google Sheets
        for change in all_changes:
            self.sheets_reporter.log_change_to_sheets(change)
        
        # Generate JSON report for GitHub Actions artifacts
        json_report_path = self.gh_reporter.generate_json_report(all_changes, self.cycle_stats)
        logger.info(f"JSON report saved: {json_report_path}")
        
        logger.info(f"Monitoring cycle completed. Changes: {len(all_changes)}")
        return all_changes

    def run_scheduled_monitoring(self):
        """Run scheduled monitoring with configurable polling interval"""
        scheduling_config = self.config.get('scheduling', {})
        polling_interval = scheduling_config.get('polling_interval', 300)  # Default 5 minutes
        
        polling_minutes = polling_interval // 60
        
        schedule.every(polling_minutes).minutes.do(self.run_monitoring_cycle)
        
        logger.info(f"Started scheduled monitoring (checks every {polling_interval}s for due URLs)")
        
        while True:
            schedule.run_pending()
            time.sleep(1)
            
    def get_detailed_status(self):
        """Get detailed status for API/reporting"""
        return {
            'url_schedules': self.url_schedules,
            'due_urls': self.get_urls_due_for_check(),
            'config_summary': {
                'total_urls': len(self.config.get('monitored_urls', [])),
                'sheets_enabled': self.sheets_reporter.client is not None
            }
        }

# FastAPI endpoints (remain the same)
@app.get("/")
async def root():
    return {"status": "running", "service": "AI Safety Metadata Monitor"}

@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}

@app.get("/check-now")
async def manual_check():
    monitor = AISafetyMonitor()
    changes = monitor.run_monitoring_cycle()
    return {"changes_detected": len(changes), "changes": changes}

@app.get("/status")
async def status():
    """Get current monitoring status"""
    monitor = AISafetyMonitor()
    due_urls = monitor.get_urls_due_for_check()
    return {
        "due_urls": [u['url'] for u in due_urls],
        "total_due": len(due_urls),
        "total_monitored": len(monitor.url_schedules)
    }

@app.get("/api/sheets-status")
async def sheets_status():
    """Check Google Sheets integration status"""
    monitor = AISafetyMonitor()
    return {
        "sheets_connected": monitor.sheets_reporter.client is not None,
        "last_updated": datetime.now().isoformat()
    }

def main():
    """Main entry point for the application"""
    import os
    
    # One-shot mode for GitHub Actions
    if os.getenv('GITHUB_ACTIONS') == 'true':
        print("Running one-shot monitoring cycle...")
        monitor = AISafetyMonitor()
        changes = monitor.run_monitoring_cycle()
        print(f"Completed. Changes detected: {len(changes)}")
        exit(0)
    
    # Continuous monitoring mode
    print("Starting AI Safety Metadata Monitor...")
    monitor = AISafetyMonitor()
    
    # Start FastAPI server
    import threading
    def start_api():
        uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
    
    api_thread = threading.Thread(target=start_api, daemon=True)
    api_thread.start()
    
    # Start scheduled monitoring
    monitor.run_scheduled_monitoring()

if __name__ == "__main__":
    main()