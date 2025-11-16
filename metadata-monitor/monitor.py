import sys
import requests
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
import gspread

def setup_logging():
    """Comprehensive logging setup"""
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    
    # Clear any existing handlers
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    
    # Formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s'
    )
    
    # File handler
    file_handler = logging.FileHandler('logs/monitor.log')
    file_handler.setFormatter(formatter)
    
    # Stream handler (console)
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    
    return logger

# Initialize logging
logger = setup_logging()

app = FastAPI(title="AI Safety Metadata Monitor")

class GoogleSheetsReporter:
    def __init__(self, credentials_file="google-sheets-credentials.json", use_env=False):
        self.credentials_file = credentials_file
        self.use_env = use_env
        self.client = None
        self.setup_client()
    
    def setup_client(self):
        """Setup Google Sheets client from file or environment"""
        try:
            scopes = [
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive"
            ]
            
            if self.use_env or os.getenv('GOOGLE_SHEETS_USE_ENV'):
                logger.info("Attempting to initialize Google Sheets client from environment variables")
                creds = self._get_credentials_from_env()
            else:
                logger.info(f"Attempting to initialize Google Sheets client with file: {self.credentials_file}")
                if not os.path.exists(self.credentials_file):
                    logger.error(f"Google Sheets credentials file not found: {self.credentials_file}")
                    return
                creds = Credentials.from_service_account_file(self.credentials_file, scopes=scopes)
            
            if creds:
                self.client = gspread.authorize(creds)
                logger.info("Google Sheets client authorized successfully")
                self.test_connection()
            else:
                logger.error("Failed to create credentials from any source")
                
        except Exception as e:
            logger.error(f"Unexpected error setting up Google Sheets: {e}")
            self.client = None
    
    def _get_credentials_from_env(self):
        """Create credentials from environment variables"""
        try:
            from google.oauth2.service_account import Credentials
            import json
            
            # Check if all required environment variables are set
            required_vars = [
                'GOOGLE_SHEETS_TYPE',
                'GOOGLE_SHEETS_PROJECT_ID', 
                'GOOGLE_SHEETS_PRIVATE_KEY_ID',
                'GOOGLE_SHEETS_PRIVATE_KEY',
                'GOOGLE_SHEETS_CLIENT_EMAIL',
                'GOOGLE_SHEETS_CLIENT_ID',
                'GOOGLE_SHEETS_AUTH_URI',
                'GOOGLE_SHEETS_TOKEN_URI',
                'GOOGLE_SHEETS_AUTH_PROVIDER_X509_CERT_URL',
                'GOOGLE_SHEETS_CLIENT_X509_CERT_URL'
            ]
            
            missing_vars = [var for var in required_vars if not os.getenv(var)]
            if missing_vars:
                logger.error(f"Missing required environment variables: {missing_vars}")
                return None
            
            # Build service account info from environment
            service_account_info = {
                "type": os.getenv('GOOGLE_SHEETS_TYPE'),
                "project_id": os.getenv('GOOGLE_SHEETS_PROJECT_ID'),
                "private_key_id": os.getenv('GOOGLE_SHEETS_PRIVATE_KEY_ID'),
                "private_key": os.getenv('GOOGLE_SHEETS_PRIVATE_KEY').replace('\\n', '\n'),
                "client_email": os.getenv('GOOGLE_SHEETS_CLIENT_EMAIL'),
                "client_id": os.getenv('GOOGLE_SHEETS_CLIENT_ID'),
                "auth_uri": os.getenv('GOOGLE_SHEETS_AUTH_URI'),
                "token_uri": os.getenv('GOOGLE_SHEETS_TOKEN_URI'),
                "auth_provider_x509_cert_url": os.getenv('GOOGLE_SHEETS_AUTH_PROVIDER_X509_CERT_URL'),
                "client_x509_cert_url": os.getenv('GOOGLE_SHEETS_CLIENT_X509_CERT_URL')
            }
            
            scopes = [
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive"
            ]
            
            creds = Credentials.from_service_account_info(service_account_info, scopes=scopes)
            logger.info("Successfully created credentials from environment variables")
            return creds
            
        except Exception as e:
            logger.error(f"Error creating credentials from environment: {e}")
            return None
            
    def test_connection(self):
        """Test Google Sheets connection"""
        try:
            # Try to list spreadsheets to verify connection
            spreadsheets = self.client.list_spreadsheet_files()
            logger.info(f"Google Sheets connection test successful. Found {len(spreadsheets)} spreadsheets.")
            return True
        except Exception as e:
            logger.error(f"Google Sheets connection test failed: {e}")
            self.client = None
            return False
    
    def ensure_spreadsheet_exists(self, spreadsheet_name="AI Safety Changes Monitor"):
        """Create or get existing spreadsheet with detailed logging"""
        if not self.client:
            logger.error("Google Sheets client not available for ensure_spreadsheet_exists")
            return None
            
        try:
            logger.info(f"Looking for spreadsheet: {spreadsheet_name}")
            spreadsheet = self.client.open(spreadsheet_name)
            logger.info(f"Using existing spreadsheet: {spreadsheet_name}")
            return spreadsheet
        except gspread.SpreadsheetNotFound:
            logger.info(f"Spreadsheet not found, creating new one: {spreadsheet_name}")
            try:
                spreadsheet = self.client.create(spreadsheet_name)
                logger.info(f"Created new spreadsheet: {spreadsheet_name}")
                return spreadsheet
            except Exception as create_error:
                logger.error(f"Failed to create spreadsheet: {create_error}")
                return None
        except Exception as e:
            logger.error(f"Error accessing spreadsheet: {e}")
            return None
    
    def setup_sheets_structure(self, spreadsheet):
        """Setup the sheets with proper structure"""
        try:
            worksheet = spreadsheet.worksheet("Changes_Log")
            logger.info("Changes_Log sheet already exists")
            return worksheet
        except gspread.WorksheetNotFound:
            logger.info("Changes_Log sheet not found, creating new one")
            try:
                worksheet = spreadsheet.add_worksheet(
                    title="Changes_Log", 
                    rows=1000, 
                    cols=11
                )
                # Add headers
                headers = [
                    "Timestamp", "URL", "Change Type", "Change Details", 
                    "Status Code", "Content Type", "Final URL", "Source",
                    "Priority", "Resolved", "Notes"
                ]
                worksheet.append_row(headers)
                logger.info("Created Changes_Log sheet with headers")
                return worksheet
            except Exception as e:
                logger.error(f"Failed to create Changes_Log sheet: {e}")
                return None
        except Exception as e:
            logger.error(f"Error setting up sheet structure: {e}")
            return None
    
    def log_change_to_sheets(self, change_data):
        """Log a change to Google Sheets"""
        if not self.client:
            logger.error("Google Sheets client not available")
            return False
        
        try:
            logger.info(f"Attempting to log change to Google Sheets: {change_data.get('url', 'Unknown URL')}")
            
            spreadsheet = self.ensure_spreadsheet_exists()
            if not spreadsheet:
                logger.error("Failed to get or create spreadsheet")
                return False
            
            worksheet = self.setup_sheets_structure(spreadsheet)
            if not worksheet:
                logger.error("Failed to get or create worksheet")
                return False
            
            # Prepare change row
            change_row = self.prepare_change_row(change_data)
            
            # Append to Changes_Log sheet
            worksheet.append_row(change_row)
            
            logger.info(f"Successfully logged change to Google Sheets: {change_data['url']}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to log change to Google Sheets: {e}")
            logger.error(f"Change data that failed: {change_data}")
            return False
    
    def prepare_change_row(self, change_data):
        """Prepare a row for the Changes_Log sheet"""
        try:
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
                        elif meta_type == 'redirect':
                            change_details.append(f"Redirect: {meta_details.get('old')}→{meta_details.get('new')}")
                        elif meta_type == 'new_url':
                            change_details.append("New URL detected")
            
            # Get status code and content type from metadata
            status_code = metadata.get('status_code', '')
            content_type = metadata.get('headers', {}).get('content-type', '')
            final_url = metadata.get('final_url', change_data.get('url', ''))
            
            return [
                change_data.get('timestamp', datetime.now().isoformat()),
                change_data.get('url', ''),
                ', '.join(change_types) if change_types else 'no_change',
                '; '.join(change_details) if change_details else 'No changes detected',
                status_code,
                content_type,
                final_url,
                change_data.get('change_source', ''),
                'medium',  # Default priority
                'FALSE',   # Not resolved
                ''         # Notes
            ]
        
        except Exception as e:
            logger.error(f"Error preparing change row: {e}")
            return ['ERROR'] * 11
    
class GitHubActionsReporter:
    def __init__(self):
        self.reports_dir = Path("data/reports")
        self.reports_dir.mkdir(exist_ok=True, parents=True)
        
    def generate_json_report(self, changes_detected, cycle_stats):
        """Generate JSON report for GitHub Actions artifacts"""
        try:
            report_data = {
                'report_id': f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                'report_date': datetime.now().isoformat(),
                'changes_detected': changes_detected,
                'cycle_stats': cycle_stats,
                'summary': {
                    'total_changes': len(changes_detected),
                    'first_run': cycle_stats.get('first_run', False),
                    'sheets_enabled': False,
                    'github_actions': os.getenv('GITHUB_ACTIONS') == 'true'
                },
                'environment': {
                    'github_actions': os.getenv('GITHUB_ACTIONS') == 'true',
                    'run_id': os.getenv('GITHUB_RUN_ID'),
                    'run_attempt': os.getenv('GITHUB_RUN_ATTEMPT')
                }
            }
            
            # Save JSON report
            report_path = self.reports_dir / f"{report_data['report_id']}.json"
            with open(report_path, 'w') as f:
                json.dump(report_data, f, indent=2, default=str)
            
            logger.info(f"JSON report generated: {report_path}")
            return report_path
            
        except Exception as e:
            logger.error(f"Error generating JSON report: {e}")
            return None
            
class AISafetyMonitor:
    def __init__(self, config_path="config.yaml"):
        # Initialize cycle_stats FIRST before any other methods
        self.cycle_stats = {
            'start_time': None,
            'end_time': None,
            'urls_checked': 0,
            'errors': 0,
            'sheets_logged': 0,
            'sheets_failed': 0,
            'first_run': False
        }
        
        self.load_config(config_path)
        self.setup_data_directory()
        self.setup_session()
        self.setup_changedetection_watches()
        self.setup_scheduled_checks()
        
        # Use environment variables if available, otherwise fall back to file
        use_env = os.getenv('GOOGLE_SHEETS_USE_ENV') == 'true'
        self.sheets_reporter = GoogleSheetsReporter(use_env=use_env)
        self.gh_reporter = GitHubActionsReporter()
        
        logger.info("AI Safety Monitor initialized successfully")
    
    def load_config(self, config_path):
        """Load configuration from YAML file"""
        try:
            with open(config_path, 'r') as f:
                self.config = yaml.safe_load(f)
            logger.info(f"Configuration loaded from {config_path}")
        except Exception as e:
            logger.error(f"Failed to load config from {config_path}: {e}")
            # Default configuration
            self.config = {
                'monitored_urls': [],
                'scheduling': {'polling_interval': 300}
            }
            logger.info("Using default configuration")
    
    def is_first_run(self):
        """Check if this appears to be the first run"""
        if not self.history_file.exists():
            return True
        
        try:
            with open(self.history_file, 'r') as f:
                content = f.read().strip()
                return content == '' or content == '{}' or content == 'null'
        except:
            return True
    
    def setup_data_directory(self):
        """Ensure data directory exists with proper structure"""
        try:
            Path("data").mkdir(exist_ok=True)
            Path("logs").mkdir(exist_ok=True)
            self.history_file = Path("data/url_history.json")
            
            # Check if this is the first run
            if self.is_first_run():
                with open(self.history_file, 'w') as f:
                    json.dump({}, f)
                logger.info("First run detected - created new url_history.json")
                self.cycle_stats['first_run'] = True
            else:
                # Check if history file is empty or invalid
                try:
                    with open(self.history_file, 'r') as f:
                        history_data = json.load(f)
                    if not history_data:
                        logger.info("Empty history file detected - treating as first run")
                        self.cycle_stats['first_run'] = True
                    else:
                        logger.info(f"Loaded existing history with {len(history_data)} URLs")
                except json.JSONDecodeError:
                    logger.warning("Invalid JSON in history file - resetting")
                    with open(self.history_file, 'w') as f:
                        json.dump({}, f)
                    self.cycle_stats['first_run'] = True
                    
        except Exception as e:
            logger.error(f"Error setting up data directory: {e}")
            # Create emergency history file
            with open(self.history_file, 'w') as f:
                json.dump({}, f)
            self.cycle_stats['first_run'] = True
    
    def setup_session(self):
        """Setup requests session"""
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
        })
    
    def get_changedetection_headers(self):
        """Return headers for changedetection.io API"""
        api_key = os.getenv("CHANGEDETECTION_API_KEY")
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["x-api-key"] = api_key
        return headers
    
    def wait_for_changedetection(self, timeout=60):
        """Wait for changedetection.io to be ready"""
        base_url = os.getenv("CHANGEDETECTION_URL", "http://changedetection:5000")
        headers = self.get_changedetection_headers()
        
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                response = requests.get(f"{base_url}/api/v1/watch", headers=headers, timeout=5)
                if response.status_code == 200:
                    logger.info("changedetection.io is ready!")
                    return True
            except Exception as e:
                logger.debug(f"Waiting for changedetection.io... ({e})")
                time.sleep(2)
        
        logger.error(f"changedetection.io not ready after {timeout} seconds")
        return False

    def setup_changedetection_watches(self):
        """Ensure all URLs are monitored by changedetection.io with config intervals"""
        logger.info("Setting up changedetection.io watches with config intervals...")
        
        # Wait for changedetection.io to be ready
        if not self.wait_for_changedetection():
            logger.error("Skipping changedetection.io setup - service not available")
            return
        
        base_url = os.getenv("CHANGEDETECTION_URL", "http://changedetection:5000")
        headers = self.get_changedetection_headers()

        # Get existing watches
        try:
            response = requests.get(f"{base_url}/api/v1/watch", headers=headers, timeout=10)
            if response.status_code == 200:
                watches = response.json()
                # Handle both response formats
                if isinstance(watches, list) and watches and isinstance(watches[0], str):
                    # Convert UUID list to watch objects
                    existing_watches = {}
                    for uuid in watches:
                        try:
                            watch_response = requests.get(f"{base_url}/api/v1/watch/{uuid}", headers=headers, timeout=10)
                            if watch_response.status_code == 200:
                                watch_data = watch_response.json()
                                if isinstance(watch_data, dict) and watch_data.get('url'):
                                    existing_watches[watch_data['url']] = watch_data
                        except Exception as e:
                            logger.warning(f"Failed to fetch watch details for {uuid}: {e}")
                else:
                    existing_watches = {watch['url']: watch for watch in watches if isinstance(watch, dict) and watch.get('tag') == 'ai-safety'}
            else:
                logger.error(f"Failed to fetch existing watches: {response.status_code}")
                existing_watches = {}
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
                            logger.info(f"Updated interval for {url}: {check_interval}s")
                        else:
                            logger.warning(f"Failed to update {url}: {response.status_code}, response: {response.text}")
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
                        logger.info(f"Added to changedetection.io: {url} (interval: {check_interval}s)")
                    else:
                        logger.warning(f"Failed to add {url}: {response.status_code}")
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
            # Try HEAD first (lighter)
            response = self.session.head(url, timeout=10, allow_redirects=True)
            
            # Some sites block HEAD requests; fallback to GET
            if response.status_code >= 400 or not response.headers:
                response = self.session.get(url, timeout=10, allow_redirects=True, stream=True)
                # Only read headers, not body
                response.close()

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
       
        changes = {}
        
        # Status code changes
        old_status = old_meta.get('status_code')
        new_status = new_meta.get('status_code')
        if old_status != new_status:
            changes['status'] = {
                'old': old_status,
                'new': new_status
            }
            logger.info(f"Status code changed: {old_status} -> {new_status}")
        
        # Content-type changes
        old_type = old_meta.get('headers', {}).get('content-type', '')
        new_type = new_meta.get('headers', {}).get('content-type', '')
        if old_type != new_type:
            changes['content_type'] = {
                'old': old_type,
                'new': new_type
            }
            logger.info(f"Content-Type changed: {old_type} -> {new_type}")
        
        # Redirect changes
        old_final = old_meta.get('final_url', '')
        new_final = new_meta.get('final_url', '')
        if old_final != new_final:
            changes['redirect'] = {
                'old': old_final,
                'new': new_final
            }
            logger.info(f"Final URL changed: {old_final} -> {new_final}")
        
        return changes

    def check_changedetection_content_changes(self):
        """Check changedetection.io for content changes"""
        logger.info("Checking changedetection.io for content changes...")
        
        # Wait for service to be ready
        if not self.wait_for_changedetection(timeout=30):
            logger.error("Skipping content change check - changedetection.io not available")
            return []
        
        base_url = os.getenv("CHANGEDETECTION_URL", "http://changedetection:5000")
        headers = self.get_changedetection_headers()
        changes_detected = []
        
        try:
            # Load last check times
            with open(self.history_file, 'r') as f:
                history = json.load(f)
            
            response = requests.get(f"{base_url}/api/v1/watch", headers=headers, timeout=10)
            if response.status_code == 200:
                watches = response.json()
                # Handle UUID list format
                if isinstance(watches, list) and watches and isinstance(watches[0], str):
                    logger.info(f"Received list of {len(watches)} UUIDs, fetching watch details...")
                    watch_objects = []
                    for uuid in watches:
                        try:
                            watch_response = requests.get(f"{base_url}/api/v1/watch/{uuid}", headers=headers, timeout=10)
                            if watch_response.status_code == 200:
                                watch_detail = watch_response.json()
                                if isinstance(watch_detail, dict):
                                    watch_objects.append(watch_detail)
                        except Exception as e:
                            logger.warning(f"Failed to fetch watch {uuid}: {e}")
                    watches = watch_objects
                elif not isinstance(watches, list):
                    logger.error(f"Unexpected response format: {watches}")
                    watches = []
            else:
                logger.error(f"Failed to fetch watches: {response.status_code}")
                watches = []
            
            for watch in watches:
                if not isinstance(watch, dict):
                    continue
                    
                if watch.get('tag') == 'ai-safety':
                    url = watch.get('url')
                    if not url:
                        continue

                    last_changed = watch.get('last_changed')
                    last_checked_str = history.get(url, {}).get('last_content_check')

                    # Determine if we should register a content change
                    if not last_checked_str:
                        # First time checking this URL
                        changes_detected.append({
                            'url': url,
                            'changes': {'content_change': {'source': 'changedetection.io', 'status': 'new_watch'}},
                            'timestamp': datetime.now().isoformat(),
                            'change_source': 'changedetection_content'
                        })
                        if url not in history:
                            history[url] = {}
                        history[url]['last_content_check'] = datetime.now().isoformat()
                    elif last_changed:
                        try:
                            last_changed_dt = datetime.fromisoformat(last_changed.replace('Z', '+00:00'))
                            if last_changed_dt > datetime.fromisoformat(last_checked_str):
                                changes_detected.append({
                                    'url': url,
                                    'changes': {'content_change': {'source': 'changedetection.io'}},
                                    'timestamp': datetime.now().isoformat(),
                                    'change_source': 'changedetection_content'
                                })
                                history[url]['last_content_check'] = datetime.now().isoformat()
                        except Exception as e:
                            logger.error(f"Error parsing dates for {url}: {e}")
                    
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
        
        # Track if we need to save history
        history_updated = False
    
        for due_url in due_urls:
            url = due_url['url']
            
            current_meta = self.get_url_metadata(url)
            previous_meta = history.get(url, {}).get('metadata')
            
            # Only detect changes if we have previous metadata
            if previous_meta is not None:
            
                changes = self.detect_metadata_changes(previous_meta, current_meta)
                if changes:
                    changes_detected.append({
                        'url': url,
                        'changes': {'metadata_change': changes},
                        'metadata': current_meta,
                        'timestamp': datetime.now().isoformat(),
                        'change_source': 'direct_metadata'
                    })
                    logger.info(f"Metadata changes detected for {url}: {changes}")
                    
                else:
                    # This is the first time we're checking this URL
                    logger.info(f"First metadata check for {url} - storing baseline")
            
            # Always update the history with current metadata
        if url not in history:
            history[url] = {}
            history[url]['metadata'] = current_meta
            history[url]['last_metadata_check'] = datetime.now().isoformat()
            history_updated = True 
            
            # Update schedule
            self.update_url_schedule(url)
            
            # Small delay between requests
            time.sleep(1)
        
         # Save history only if it was updated
        if history_updated:
            try:
                with open(self.history_file, 'w') as f:
                    json.dump(history, f, indent=2)
                logger.info("History file updated successfully")
            except Exception as e:
                logger.error(f"Failed to save history file: {e}")
        
        return changes_detected

    def run_monitoring_cycle(self):
        """Run one complete monitoring cycle"""
        logger.info("=" * 50)
        logger.info("Starting monitoring cycle...")
        logger.info("=" * 50)
        
        all_changes = []
        
        self.cycle_stats['start_time'] = datetime.now()
        self.cycle_stats['sheets_logged'] = 0
        self.cycle_stats['sheets_failed'] = 0
        
        try:
            # Check for content changes via changedetection.io
            logger.info("Step 1: Checking changedetection.io for content changes...")
            content_changes = self.check_changedetection_content_changes()
            all_changes.extend(content_changes)
            logger.info(f"Found {len(content_changes)} content changes")
            
            # Check for metadata changes on due URLs
            logger.info("Step 2: Checking metadata changes...")
            metadata_changes = self.check_metadata_changes()
            all_changes.extend(metadata_changes)
            logger.info(f"Found {len(metadata_changes)} metadata changes")
            
            # Log changes to Google Sheets
            logger.info("Step 3: Logging changes to Google Sheets...")
            for change in all_changes:
                success = self.sheets_reporter.log_change_to_sheets(change)
                if success:
                    self.cycle_stats['sheets_logged'] += 1
                else:
                    self.cycle_stats['sheets_failed'] += 1
            
            # 4. Generate JSON report for GitHub Actions
            logger.info("Step 4: Generating JSON report...")
            json_report_path = self.gh_reporter.generate_json_report(all_changes, self.cycle_stats)
            
            # Update cycle stats
            self.cycle_stats['end_time'] = datetime.now()
            duration = (self.cycle_stats['end_time'] - self.cycle_stats['start_time']).total_seconds()
            self.cycle_stats['duration_seconds'] = duration
            
            logger.info("=" * 50)
            logger.info("Monitoring cycle completed!")
            logger.info(f"Total changes detected: {len(all_changes)}")
            logger.info(f"Sheets logged: {self.cycle_stats['sheets_logged']}")
            logger.info(f"Sheets failed: {self.cycle_stats['sheets_failed']}")
            logger.info(f"Duration: {duration:.2f} seconds")
            logger.info("=" * 50)
            
        except Exception as e:
            logger.error(f"Monitoring cycle failed with error: {e}")
            self.cycle_stats['errors'] += 1
        
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

# FastAPI endpoints
@app.get("/")
async def root():
    return {"status": "running", "service": "AI Safety Metadata Monitor"}

@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}

_monitor_instance = None

def get_monitor():
    global _monitor_instance
    if _monitor_instance is None:
        _monitor_instance = AISafetyMonitor()
    return _monitor_instance

@app.get("/check-now")
async def manual_check():
    monitor = get_monitor()
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
    
# Debugging endpoints
@app.get("/debug/files")
async def debug_files():
    """Debug endpoint to check file structure"""
    import os
    files = {}
    for root, dirs, filenames in os.walk('.'):
        for filename in filenames:
            if 'google' in filename.lower() or 'cred' in filename.lower():
                rel_path = os.path.join(root, filename)
                files[rel_path] = os.path.exists(rel_path)
    return files

@app.get("/debug/sheets")
async def debug_sheets():
    """Debug endpoint for Google Sheets status"""
    monitor = AISafetyMonitor()
    sheets_status = {
        "client_available": monitor.sheets_reporter.client is not None,
        "credentials_file": monitor.sheets_reporter.credentials_file,
        "credentials_file_exists": os.path.exists(monitor.sheets_reporter.credentials_file),
        "current_directory": os.getcwd(),
        "directory_contents": os.listdir('.')
    }
    return sheets_status

@app.get("/debug/logs")
async def debug_logs():
    """Get recent logs"""
    try:
        with open('logs/monitor.log', 'r') as f:
            logs = f.readlines()[-100:]
        return {"logs": logs}
    except Exception as e:
        return {"error": str(e)}

def main():
    """Main entry point for the application"""    
    try:
        # One-shot mode for GitHub Actions
        if os.getenv('GITHUB_ACTIONS') == 'true':
            logger.info("GitHub Actions environment detected - running one-shot mode")
            print("Running one-shot monitoring cycle...")
            
            monitor = AISafetyMonitor()
            changes = monitor.run_monitoring_cycle()
            
            # Detailed reporting for GitHub Actions
            print(f"\n=== MONITORING CYCLE COMPLETED ===")
            print(f"Changes detected: {len(changes)}")
            print(f"Sheets successfully logged: {monitor.cycle_stats['sheets_logged']}")
            print(f"Sheets failed: {monitor.cycle_stats['sheets_failed']}")
            print(f"Errors: {monitor.cycle_stats['errors']}")
            
            if changes:
                print(f"\n=== CHANGES DETECTED ===")
                for change in changes:
                    print(f"- {change['url']}: {list(change.get('changes', {}).keys())}")
            
            # Exit with appropriate code
            if monitor.cycle_stats['errors'] > 0:
                logger.warning("Monitoring completed with errors")
                exit(1)
            else:
                logger.info("Monitoring completed successfully")
                exit(0)
        
        # Continuous monitoring mode
        logger.info("Starting AI Safety Metadata Monitor in continuous mode...")
        monitor = AISafetyMonitor()
        
        # Start FastAPI server
        import threading
        def start_api():
            logger.info("Starting FastAPI server on port 8000...")
            uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
        
        api_thread = threading.Thread(target=start_api, daemon=True)
        api_thread.start()
        
        # Start scheduled monitoring
        logger.info("Starting scheduled monitoring...")
        monitor.run_scheduled_monitoring()
        
    except Exception as e:
        logger.error(f"Application failed to start: {e}")
        if os.getenv('GITHUB_ACTIONS') == 'true':
            exit(1)
        else:
            raise

if __name__ == "__main__":
    main()