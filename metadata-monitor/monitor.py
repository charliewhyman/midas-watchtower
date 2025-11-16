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

# FastAPI endpoints
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