"""Integration with changedetection.io service"""
import time
from datetime import datetime
from typing import List, Dict, Any, Optional
import requests

from change_detector import ChangeDetector
from config import AppConfig
from models import ChangeDetails, DetectedChange
import logging

logger = logging.getLogger(__name__)


class ChangedetectionService:
    """Client for changedetection.io API"""
    
    def __init__(self, config: AppConfig):
        self.config = config
        self.base_url = config.settings.changedetection_url.rstrip('/')
        logger.info(f"🔧 Changedetection service initialized with URL: {self.base_url}")
        self.headers = self._get_headers()
    
    def _get_headers(self) -> Dict[str, str]:
        """Get API headers with authentication"""
        headers = {"Content-Type": "application/json"}
        api_key = self.config.settings.changedetection_api_key
        if api_key and api_key.strip():  # Only use if actually set
            headers["x-api-key"] = api_key
    
    def wait_for_service(self, timeout: int = 10, check_api: bool = True) -> bool:
        """Wait for changedetection.io service to be ready
        
        Args:
            timeout: Maximum time to wait in seconds
            check_api: If True, check API endpoint. If False, just check if service is up.
        """
        logger.info(f"⏳ Waiting for changedetection.io to be ready (timeout: {timeout}s)...")
        
        start_time = time.time()
        last_log_time = start_time
        
        # Determine which endpoint to check
        check_url = f"{self.base_url}/api/v1/watch" if check_api else self.base_url
        headers = self.headers if check_api else {}
        
        while time.time() - start_time < timeout:
            try:
                response = requests.get(check_url, headers=headers, timeout=5)
                # Accept 200 or 401 (unauthorized) as "service is up"
                if response.status_code in [200, 401]:
                    logger.info(f"✅ changedetection.io is ready! (status: {response.status_code})")
                    return True
            except requests.exceptions.ConnectionError:
                # Normal during startup - log every 20 seconds
                current_time = time.time()
                if current_time - last_log_time > 20:
                    elapsed = int(current_time - start_time)
                    logger.info(f"⏳ Waiting for changedetection.io... ({elapsed}s/{timeout}s)")
                    last_log_time = current_time
            except Exception as e:
                logger.debug(f"Changedetection.io not ready yet: {e}")
            
            time.sleep(5)
        
        logger.error(f"❌ changedetection.io not ready after {timeout} seconds")
        return False
    
    def setup_watches(self, change_detector: 'ChangeDetector') -> None:
        """Ensure all URLs are monitored by changedetection.io"""
        logger.info("🔧 Setting up changedetection.io watches...")
        
        try:
            existing_watches = self._get_existing_watches()
            self._sync_watches(existing_watches, change_detector)
        except Exception as e:
            logger.error(f"Failed to setup changedetection.io watches: {e}")
    
    def _get_existing_watches(self) -> Dict[str, Dict[str, Any]]:
        """Get existing watches from changedetection.io"""
        try:
            # FIX: Use self.base_url properly
            response = requests.get(f"{self.base_url}/api/v1/watch", headers=self.headers, timeout=10)
            
            if response.status_code != 200:
                logger.error(f"Failed to fetch watches: {response.status_code}")
                return {}
            
            watches_data = response.json()
            return self._parse_watches_response(watches_data)
            
        except Exception as e:
            logger.error(f"Error fetching existing watches: {e}")
            return {}
    
    def _parse_watches_response(self, watches_data) -> Dict[str, Dict[str, Any]]:
        """Parse watches response in different formats"""
        existing_watches = {}
        
        if isinstance(watches_data, list):
            if watches_data and isinstance(watches_data[0], str):
                # UUID list format - fetch details for each
                for uuid in watches_data:
                    watch_detail = self._get_watch_detail(uuid)
                    if watch_detail and isinstance(watch_detail, dict) and watch_detail.get('url'):
                        existing_watches[watch_detail['url']] = watch_detail
            else:
                # List of watch objects
                for watch in watches_data:
                    if isinstance(watch, dict) and watch.get('url'):
                        existing_watches[watch['url']] = watch
        elif isinstance(watches_data, dict):
            # Single watch object
            if watches_data.get('url'):
                existing_watches[watches_data['url']] = watches_data
        
        logger.info(f"Found {len(existing_watches)} existing watches")
        return existing_watches
    
    def _get_watch_detail(self, uuid: str) -> Optional[Dict[str, Any]]:
        """Get detailed watch information by UUID"""
        try:
            # FIX: Use self.base_url properly
            response = requests.get(f"{self.base_url}/api/v1/watch/{uuid}", headers=self.headers, timeout=10)
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            logger.warning(f"Failed to fetch watch {uuid}: {e}")
        return None
    
    def _sync_watches(self, existing_watches: Dict[str, Dict[str, Any]], change_detector: 'ChangeDetector') -> None:
        """Sync URL configurations with changedetection.io"""
        for url_config in self.config.url_configs:
            url = url_config.url
            check_interval = url_config.check_interval
            
            if url in existing_watches:
                self._update_existing_watch(existing_watches[url], check_interval)
            else:
                self._create_new_watch(url, check_interval, change_detector)
    
    def _update_existing_watch(self, watch: Dict[str, Any], check_interval: int) -> None:
        """Update existing watch if interval changed"""
        if watch.get('check_interval') == check_interval:
            logger.debug(f"Watch already configured: {watch['url']}")
            return
        
        try:
            update_payload = {"check_interval": check_interval}
            # FIX: Use self.base_url properly
            response = requests.patch(
                f"{self.base_url}/api/v1/watch/{watch['uuid']}",
                json=update_payload,
                headers=self.headers,
                timeout=10
            )
            
            if response.status_code == 200:
                logger.info(f"Updated interval for {watch['url']}: {check_interval}s")
            else:
                logger.warning(f"Failed to update {watch['url']}: {response.status_code}")
        except Exception as e:
            logger.error(f"Error updating watch {watch['url']}: {e}")
    
    def _create_new_watch(self, url: str, check_interval: int, change_detector: 'ChangeDetector') -> None:
        """Create new watch in changedetection.io"""
        payload = {
            "url": url,
            "tag": "ai-safety",
            "title": f"AI Safety - {url}",
            "check_interval": check_interval
        }
        
        try:
            # FIX: Use self.base_url properly
            response = requests.post(
                f"{self.base_url}/api/v1/watch",
                json=payload,
                headers=self.headers,
                timeout=10
            )
            
            if response.status_code in [200, 201]:
                logger.info(f"✅ Added to changedetection.io: {url} (interval: {check_interval}s)")
                # Initialize history for new watch
                if url not in change_detector.history:
                    change_detector.history[url] = {
                        'last_content_check': datetime.now().isoformat()
                    }
            else:
                logger.warning(f"Failed to add {url}: {response.status_code} - {response.text}")
        except Exception as e:
            logger.error(f"Error adding watch {url}: {e}")
    
    def check_content_changes(self, change_detector: 'ChangeDetector') -> List[DetectedChange]:
        """Check for content changes via changedetection.io"""
        logger.info("Checking changedetection.io for content changes...")
        
        if not self.wait_for_service(timeout=30):
            logger.error("changedetection.io not available for content check")
            return []
        
        changes_detected = []
        
        try:
            existing_watches = self._get_existing_watches()
            
            for watch in existing_watches.values():
                if watch.get('tag') != 'ai-safety':
                    continue
                
                url = watch.get('url')
                if not url:
                    continue
                
                last_changed = watch.get('last_changed')
                last_checked = change_detector.history.get(url, {}).get('last_content_check')
                
                change = self._detect_content_change(url, last_changed, last_checked)
                if change:
                    changes_detected.append(change)
                    change_detector.history.setdefault(url, {})['last_content_check'] = datetime.now().isoformat()
            
            # Save updated history
            change_detector.save_history()
            
        except Exception as e:
            logger.error(f"Error checking changedetection.io for content changes: {e}")
        
        logger.info(f"Found {len(changes_detected)} content changes")
        return changes_detected
    
    def _detect_content_change(self, url: str, last_changed: Optional[str], last_checked: Optional[str]) -> Optional[DetectedChange]:
        """Detect if a content change has occurred"""
        if not last_checked:
            # First time checking this URL
            return DetectedChange(
                url=url,
                changes=[ChangeDetails(
                    change_type='content_change',
                    source='changedetection',
                    details={'status': 'new_watch'}
                )],
                timestamp=datetime.now(),
                change_source='changedetection_content'
            )
        
        if last_changed:
            try:
                last_changed_dt = datetime.fromisoformat(last_changed.replace('Z', '+00:00'))
                last_checked_dt = datetime.fromisoformat(last_checked)
                
                if last_changed_dt > last_checked_dt:
                    return DetectedChange(
                        url=url,
                        changes=[ChangeDetails(
                            change_type='content_change',
                            source='changedetection',
                            details={'last_changed': last_changed}
                        )],
                        timestamp=datetime.now(),
                        change_source='changedetection_content'
                    )
            except Exception as e:
                logger.error(f"Error parsing dates for {url}: {e}")
        
        return None