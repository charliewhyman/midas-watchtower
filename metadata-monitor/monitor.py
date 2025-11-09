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

class DiscordNotifier:
    def __init__(self, webhook_url, username="AI Safety Monitor", avatar_url=None):
        self.webhook_url = webhook_url
        self.username = username
        self.avatar_url = avatar_url
    
    def send_alert(self, changes):
        """Send formatted alert to Discord"""
        if not changes or not self.webhook_url:
            return
        
        embeds = []
        for change in changes:
            embed = {
                "title": "🔍 AI Safety Change Detected",
                "url": change['url'],
                "color": 0xff6b6b,
                "fields": [],
                "timestamp": change['timestamp'],
                "footer": {"text": "AI Safety Monitor"}
            }
            
            for change_type, details in change['changes'].items():
                if change_type == 'content_change':
                    embed["fields"].append({
                        "name": "Content Change",
                        "value": f"Detected via changedetection.io",
                        "inline": True
                    })
                elif change_type == 'metadata_change':
                    embed["fields"].append({
                        "name": "Metadata Change",
                        "value": f"{details.get('type', 'Unknown')}",
                        "inline": True
                    })
                elif change_type == 'status':
                    embed["fields"].append({
                        "name": "Status Change",
                        "value": f"`{details['old']}` → `{details['new']}`",
                        "inline": True
                    })
            
            embeds.append(embed)
        
        for i in range(0, len(embeds), 10):
            chunk = embeds[i:i+10]
            payload = {
                "username": self.username,
                "embeds": chunk
            }
            
            if self.avatar_url:
                payload["avatar_url"] = self.avatar_url
            
            try:
                response = requests.post(self.webhook_url, json=payload, timeout=10)
                response.raise_for_status()
                logger.info(f"Discord notification sent for {len(chunk)} changes")
            except Exception as e:
                logger.error(f"Failed to send Discord notification: {e}")

class AISafetyMonitor:
    def __init__(self, config_path="config.yaml"):
        self.load_config(config_path)
        self.setup_data_directory()
        self.setup_session()
        self.setup_notifier()
        self.setup_changedetection_watches()
        self.setup_scheduled_checks()
        self.report_generator = ReportGenerator()
        self.cycle_stats = {
            'start_time': None,
            'end_time': None,
            'urls_checked': 0,
            'errors': 0
        }
        
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
    
    def setup_notifier(self):
        """Setup Discord notifier if configured"""
        self.notifier = None
        discord_config = self.config.get('notifications', {}).get('discord', {})
        
        if discord_config.get('webhook_url'):
            self.notifier = DiscordNotifier(
                webhook_url=discord_config['webhook_url'],
                username=discord_config.get('username', 'AI Safety Monitor')
            )
            logger.info("Discord notifier initialized")
    
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
        
        # Notify if changes detected
        if all_changes and self.notifier:
            self.notifier.send_alert(all_changes)
            
        # Generate reports
        report = self.report_generator.generate_monitoring_report(
            monitoring_results=all_changes,
            changes_detected=all_changes,
            cycle_stats=self.cycle_stats
        )
        
        logger.info(f"Monitoring cycle completed. Changes: {len(all_changes)}")
        return report

    def run_scheduled_monitoring(self):
        """Run scheduled monitoring"""
        # Run monitoring every 5 minutes to check for due URLs
        schedule.every(5).minutes.do(self.run_monitoring_cycle)
        
        logger.info("Started scheduled monitoring (runs every 5 minutes)")
        
        while True:
            schedule.run_pending()
            time.sleep(1)
            
    def get_detailed_status(self):
        """Get detailed status for API/reporting"""
        # Compile current state for reporting
        return {
            'url_schedules': self.url_schedules,
            'due_urls': self.get_urls_due_for_check(),
            'config_summary': self._get_config_summary()
        }

class ReportGenerator:
    def __init__(self, data_dir="data"):
        self.data_dir = Path(data_dir)
        self.reports_dir = self.data_dir / "reports"
        self.reports_dir.mkdir(exist_ok=True)
    
    def generate_monitoring_report(self, monitoring_results, changes_detected, cycle_stats):
        """Generate comprehensive reports in multiple formats"""
        report_data = self._compile_report_data(monitoring_results, changes_detected, cycle_stats)
        
        # Generate different report formats
        self._generate_json_report(report_data)
        self._generate_markdown_summary(report_data)
        self._generate_github_summary(report_data)
        
        return report_data

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

@app.post("/test-discord")
async def test_discord():
    """Test Discord webhook"""
    monitor = AISafetyMonitor()
    if monitor.notifier:
        test_change = [{
            'url': 'https://example.com',
            'changes': {'test': {'type': 'test_change'}},
            'timestamp': datetime.now().isoformat(),
            'change_source': 'test'
        }]
        monitor.notifier.send_alert(test_change)
        return {"status": "test_sent"}
    return {"status": "discord_not_configured"}

if __name__ == "__main__":
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