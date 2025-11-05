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
        
        # Create embeds for Discord
        embeds = []
        
        for change in changes:
            embed = {
                "title": "🔍 AI Safety Change Detected",
                "url": change['url'],
                "color": 0xff6b6b,  # Red color
                "fields": [],
                "timestamp": change['timestamp'],
                "footer": {
                    "text": "AI Safety Monitor"
                }
            }
            
            # Add change details
            for change_type, details in change['changes'].items():
                if change_type == 'status':
                    embed["fields"].append({
                        "name": "Status Code Change",
                        "value": f"`{details['old']}` → `{details['new']}`",
                        "inline": True
                    })
                elif change_type == 'content':
                    embed["fields"].append({
                        "name": "Content Changed",
                        "value": "Content hash updated",
                        "inline": True
                    })
                elif change_type in ['last-modified', 'etag']:
                    old_val = details['old'] or 'None'
                    new_val = details['new'] or 'None'
                    embed["fields"].append({
                        "name": f"Header: {change_type}",
                        "value": f"`{old_val}` → `{new_val}`",
                        "inline": True
                    })
                elif change_type == 'new_url':
                    embed["fields"].append({
                        "name": "New URL Discovered",
                        "value": "URL is now being monitored",
                        "inline": True
                    })
            
            # Add final URL if different
            if change['metadata'].get('final_url') != change['url']:
                embed["fields"].append({
                    "name": "Redirected to",
                    "value": change['metadata']['final_url']
                })
            
            embeds.append(embed)
        
        # Send to Discord (split into multiple messages if too many embeds)
        for i in range(0, len(embeds), 10):  # Discord limit: 10 embeds per message
            chunk = embeds[i:i+10]
            
            payload = {
                "username": self.username,
                "embeds": chunk
            }
            
            if self.avatar_url:
                payload["avatar_url"] = self.avatar_url
            
            try:
                response = requests.post(
                    self.webhook_url,
                    json=payload,
                    headers={'Content-Type': 'application/json'},
                    timeout=10
                )
                response.raise_for_status()
                logger.info(f"Discord notification sent successfully for {len(chunk)} changes")
            except Exception as e:
                logger.error(f"Failed to send Discord notification: {e}")

    def send_test_message(self):
        """Send a test message to verify webhook setup"""
        if not self.webhook_url:
            logger.warning("No Discord webhook URL configured")
            return False
            
        payload = {
            "username": self.username,
            "embeds": [{
                "title": "✅ Test Notification",
                "description": "AI Safety Monitor is successfully connected to Discord!",
                "color": 0x4caf50,  # Green
                "timestamp": datetime.now().isoformat(),
                "footer": {
                    "text": "You will receive alerts here when changes are detected"
                }
            }]
        }
        
        if self.avatar_url:
            payload["avatar_url"] = self.avatar_url
        
        try:
            response = requests.post(self.webhook_url, json=payload, timeout=10)
            response.raise_for_status()
            logger.info("Discord test message sent successfully")
            return True
        except Exception as e:
            logger.error(f"Discord test message failed: {e}")
            return False

class AISafetyMonitor:
    def __init__(self, config_path="config.yaml"):
        self.load_config(config_path)
        self.setup_data_directory()
        self.setup_session()
        self.setup_notifier()
        self.setup_changedetection_watches()
        logger.info("AISafetyMonitor initialized")
    
    def load_config(self, config_path):
        """Load configuration from YAML file with environment variable overrides"""
        try:
            with open(config_path, 'r') as f:
                self.config = yaml.safe_load(f)
            logger.info("Configuration loaded successfully")
        except Exception as e:
            logger.error(f"Error loading config: {e}")
            # Default config
            self.config = {
                'monitored_urls': [
                    'https://openai.com/policies/usage-policies',
                    'https://openai.com/safety'
                ],
                'check_interval': 3600
            }
        
        # Override with environment variables
        discord_webhook = os.getenv('DISCORD_WEBHOOK_URL')
        if discord_webhook:
            if 'notifications' not in self.config:
                self.config['notifications'] = {}
            if 'discord' not in self.config['notifications']:
                self.config['notifications']['discord'] = {}
            self.config['notifications']['discord']['webhook_url'] = discord_webhook
    
    def setup_data_directory(self):
        """Ensure data directory exists"""
        Path("data").mkdir(exist_ok=True)
        Path("logs").mkdir(exist_ok=True)
        self.history_file = Path("data/url_history.json")
        
        if not self.history_file.exists():
            with open(self.history_file, 'w') as f:
                json.dump({}, f)
    
    def setup_session(self):
        """Setup requests session with proper headers"""
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        })
    
    def setup_notifier(self):
        """Setup Discord notifier if configured"""
        self.notifier = None
        discord_config = self.config.get('notifications', {}).get('discord', {})
        
        if discord_config.get('webhook_url'):
            self.notifier = DiscordNotifier(
                webhook_url=discord_config['webhook_url'],
                username=discord_config.get('username', 'AI Safety Monitor'),
                avatar_url=discord_config.get('avatar_url')
            )
            logger.info("Discord notifier initialized")
            
            # Send test message on startup
            if self.notifier.send_test_message():
                logger.info("Discord test message sent successfully")
            else:
                logger.warning("Discord test message failed")
        else:
            logger.info("Discord notifications not configured")
    
    def get_changedetection_headers(self):
        """Return headers including API key if available"""
        api_key = os.getenv("CHANGEDETECTION_API_KEY")
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["x-api-key"] = api_key
        return headers

    def get_url_metadata(self, url):
        """Get comprehensive metadata for a URL"""
        try:
            logger.info(f"Checking URL: {url}")
            
            response = self.session.head(url, timeout=10, allow_redirects=True)
            
            metadata = {
                'url': url,
                'timestamp': datetime.now().isoformat(),
                'status_code': response.status_code,
                'headers': dict(response.headers),
                'final_url': response.url,
            }
            
            if response.status_code == 200:
                content_response = self.session.get(url, timeout=30)
                metadata.update({
                    'content_hash': hashlib.sha256(content_response.content).hexdigest(),
                    'content_length': len(content_response.content),
                    'content_type': content_response.headers.get('content-type', '')
                })
            
            return metadata
            
        except Exception as e:
            logger.error(f"Error checking {url}: {e}")
            return {
                'url': url,
                'timestamp': datetime.now().isoformat(),
                'error': str(e),
                'status_code': None
            }
    
    def discover_new_urls(self):
        """Discover new URLs using patterns"""
        discovered = []
        
        for pattern in self.config.get('discovery_patterns', []):
            base_url = pattern['base']
            models = pattern.get('models', [])
            days_to_check = pattern.get('days_to_check', 30)
            
            for model in models:
                for days_ago in range(0, days_to_check):
                    date_str = (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d")
                    test_url = f"{base_url}/{date_str}-{model}-model-card.pdf"
                    
                    # Check if URL exists
                    metadata = self.get_url_metadata(test_url)
                    if metadata.get('status_code') == 200:
                        logger.info(f"Discovered: {test_url}")
                        discovered.append(test_url)
        
        return discovered
    
    def detect_changes(self, old_meta, new_meta):
        """Detect meaningful changes between metadata snapshots"""
        if not old_meta:
            return {'type': 'new_url'}
        
        changes = {}
        
        # Status code changes
        if old_meta.get('status_code') != new_meta.get('status_code'):
            changes['status'] = {
                'old': old_meta.get('status_code'),
                'new': new_meta.get('status_code')
            }
        
        # Content changes
        if old_meta.get('content_hash') != new_meta.get('content_hash'):
            changes['content'] = {
                'old_hash': old_meta.get('content_hash'),
                'new_hash': new_meta.get('content_hash')
            }
        
        # Header changes
        important_headers = ['last-modified', 'etag', 'content-length']
        for header in important_headers:
            old_val = old_meta.get('headers', {}).get(header)
            new_val = new_meta.get('headers', {}).get(header)
            if old_val != new_val:
                changes[header] = {'old': old_val, 'new': new_val}
        
        return changes
    
    def add_url_to_changedetection(self, url):
        """Add a single URL to changedetection.io"""
        try:
            base_url = os.getenv("CHANGEDETECTION_URL", "http://changedetection:5000")
            response = requests.post(
                f"{base_url}/api/v1/watch",
                json={
                    "url": url,
                    "tag": "ai-safety-discovery",
                    "title": f"Discovered - {url}"
                },
                timeout=10
            )
            if response.status_code in [200, 201]:
                logger.info(f"✓ Added discovered URL to changedetection.io: {url}")
            else:
                logger.warning(f"✗ Failed to add discovered URL {url}: {response.status_code}")
        except Exception as e:
            logger.error(f"Error adding discovered URL {url} to changedetection.io: {e}")
            
    def check_all_urls(self):
        """Check all monitored URLs for changes"""
        logger.info(f"Starting URL check at {datetime.now()}")
        
        try:
            with open(self.history_file, 'r') as f:
                history = json.load(f)
        except Exception as e:
            logger.error(f"Error loading history: {e}")
            history = {}
        
        changes_detected = []
        
        # Check configured URLs
        for url_config in self.config.get('monitored_urls', []):
            url = url_config['url'] if isinstance(url_config, dict) else url_config
            current_meta = self.get_url_metadata(url)
            previous_meta = history.get(url)
            
            changes = self.detect_changes(previous_meta, current_meta)
            if changes:
                changes_detected.append({
                    'url': url,
                    'changes': changes,
                    'metadata': current_meta,
                    'timestamp': datetime.now().isoformat()
                })
                logger.info(f"Change detected for {url}: {changes}")
            
            history[url] = current_meta
        
        # Run discovery
        discovered_urls = self.discover_new_urls()
        for new_url in discovered_urls:
            if new_url not in history:
                current_meta = self.get_url_metadata(new_url)
                changes_detected.append({
                    'url': new_url,
                    'changes': {'type': 'new_url'},
                    'metadata': current_meta,
                    'timestamp': datetime.now().isoformat()
                })
                logger.info(f"New URL discovered: {new_url}")
                history[new_url] = current_meta
                
                # add to changedetection.io
                self.add_url_to_changedetection(new_url)
        
        # Save updated history
        with open(self.history_file, 'w') as f:
            json.dump(history, f, indent=2)
        
        if changes_detected:
            self.notify_changes(changes_detected)
        
        logger.info(f"Check completed. Changes detected: {len(changes_detected)}")
        return changes_detected
    
    def notify_changes(self, changes):
        """Notify about detected changes via Discord"""
        if self.notifier and changes:
            self.notifier.send_alert(changes)
        
        # Also log changes
        for change in changes:
            logger.info(f"CHANGE DETECTED: {change['url']} - {change['changes']}")
    
    def run_scheduled_checks(self):
        """Run checks on a schedule"""
        interval = self.config.get('check_interval', 3600)
        schedule.every(interval).seconds.do(self.check_all_urls)
        
        logger.info(f"Scheduled checks every {interval} seconds")
        
        while True:
            schedule.run_pending()
            time.sleep(1)
            
    def setup_changedetection_watches(self):
        """Sync all monitored URLs from YAML to changedetection.io"""
        logger.info("Syncing monitored URLs to changedetection.io...")

        base_url = os.getenv("CHANGEDETECTION_URL", "http://changedetection:5000")
        headers = self.get_changedetection_headers()

        # Get existing watches
        try:
            response = requests.get(f"{base_url}/api/v1/watch", headers=headers, timeout=10)
            existing_urls = [watch['url'] for watch in response.json()]
            logger.info(f"Existing watches: {existing_urls}")
        except Exception as e:
            logger.error(f"Failed to fetch existing watches: {e}")
            existing_urls = []

        # Loop through YAML URLs
        for url_config in self.config.get('monitored_urls', []):
            url = url_config['url'] if isinstance(url_config, dict) else url_config
            payload = {
                "url": url,
                "tag": "ai-safety",
                "title": f"AI Safety - {url}"
            }

            # Only add if not already present
            if url not in existing_urls:
                try:
                    add_response = requests.post(f"{base_url}/api/v1/watch", json=payload, headers=headers, timeout=10)
                    if add_response.status_code in [200, 201]:
                        logger.info(f"✓ Added URL: {url}")
                    else:
                        logger.warning(f"✗ Failed to add URL {url}: {add_response.status_code}")
                except Exception as e:
                    logger.error(f"Error adding URL {url}: {e}")
            else:
                logger.info(f"URL already exists in changedetection.io: {url}")

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
    changes = monitor.check_all_urls()
    return {"changes_detected": len(changes), "changes": changes}

@app.post("/test-discord")
async def test_discord():
    """Test Discord webhook configuration"""
    monitor = AISafetyMonitor()
    if hasattr(monitor, 'notifier') and monitor.notifier:
        success = monitor.notifier.send_test_message()
        return {"status": "success" if success else "failed"}
    return {"status": "discord_not_configured"}

@app.get("/status")
async def status():
    """Get current monitoring status"""
    try:
        with open('data/url_history.json', 'r') as f:
            history = json.load(f)
        return {
            "monitored_urls": len(history),
            "last_check": datetime.now().isoformat()
        }
    except:
        return {"monitored_urls": 0, "last_check": None}

def run_once(self):
    """Run one monitoring cycle and exit (for GitHub Actions)"""
    logger.info("Running single monitoring cycle...")
    changes = self.check_all_urls()
    
    if changes:
        logger.info(f"Detected {len(changes)} changes")
        for change in changes:
            logger.info(f"Change: {change['url']} - {change['changes']}")
    else:
        logger.info("No changes detected")
    
    return changes

if __name__ == "__main__":
    import os
    
    # Check if running in GitHub Actions or one-shot mode
    if os.getenv('GITHUB_ACTIONS') == 'true' or os.getenv('CHECK_INTERVAL') == '1':
        print(" Running in one-shot mode")
        monitor = AISafetyMonitor()
        
        # Use run_once if it exists, otherwise use check_all_urls
        if hasattr(monitor, 'run_once'):
            changes = monitor.run_once()
        else:
            changes = monitor.check_all_urls()
            
        print(f"Scan complete. Changes detected: {len(changes)}")
        
        # Exit with code 0 (success) regardless of changes
        # GitHub Actions will show the results in the summary
        exit(0)
        
    else:
        # Original continuous monitoring (for local/Docker)
        print("Running in continuous monitoring mode")
        monitor = AISafetyMonitor()
        
        # Start FastAPI server in background
        import threading
        def start_api():
            uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
        
        api_thread = threading.Thread(target=start_api, daemon=True)
        api_thread.start()
        
        # Start scheduled checks
        logger.info("Starting AI Safety Metadata Monitor...")
        monitor.run_scheduled_checks()