"""URL scheduling functionality"""
import schedule
import time
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, TYPE_CHECKING
from collections import deque

from config import AppConfig
from models import UrlSchedule
import logging

if TYPE_CHECKING:
    from monitoring_service import MonitoringService

logger = logging.getLogger(__name__)


class UrlScheduler:
    """Manages URL checking schedules"""
    
    def __init__(self, config: AppConfig):        
        self.config = config
        self.schedules: Dict[str, UrlSchedule] = {}
        self._initialize_schedules()
    
    def _initialize_schedules(self) -> None:
        """Initialize schedules from configuration"""
        for url_config in self.config.url_configs:
            self.schedules[url_config.url] = UrlSchedule(
                url=url_config.url,
                check_interval=url_config.check_interval,
                type=url_config.type,
                priority=url_config.priority,
                next_check=datetime.now()
            )
    
    def get_due_urls(self) -> List[Dict[str, Any]]:
        """Get URLs that are due for checking"""
        due_urls = []
        current_time = datetime.now()
        
        for url, schedule in self.schedules.items():
            if schedule.next_check is None or current_time >= schedule.next_check:
                due_urls.append({
                    'url': url,
                    'config': schedule
                })
        
        return due_urls
    
    def update_schedule(self, url: str) -> None:
        """Update schedule after URL check"""
        if url in self.schedules:
            schedule = self.schedules[url]
            schedule.last_checked = datetime.now()
            schedule.next_check = datetime.now() + timedelta(seconds=schedule.check_interval)
    
    def get_status(self) -> Dict[str, Any]:
        """Get current scheduler status"""
        due_urls = self.get_due_urls()
        return {
            'total_urls': len(self.schedules),
            'due_urls': len(due_urls),
            'next_check_in': self._get_next_check_seconds(),
            'schedules': {url: schedule.dict() for url, schedule in self.schedules.items()}
        }
    
    def _get_next_check_seconds(self) -> Optional[float]:
        """Get seconds until next scheduled check"""
        next_check = None
        
        for schedule in self.schedules.values():
            if schedule.next_check:
                if next_check is None or schedule.next_check < next_check:
                    next_check = schedule.next_check
        
        if next_check:
            return (next_check - datetime.now()).total_seconds()
        
        return None


class MonitoringScheduler:
    """Main monitoring scheduler"""
    
    def __init__(self, monitoring_service: 'MonitoringService', polling_interval: int = 300):
        self.monitoring_service = monitoring_service
        self.polling_interval = polling_interval
        self.running = False
    
    def start(self) -> None:
        """Start the scheduled monitoring"""
        self.running = True
        
        # Schedule monitoring cycle
        polling_minutes = self.polling_interval // 60
        schedule.every(polling_minutes).minutes.do(self._run_monitoring_cycle)
        
        logger.info(f"Started scheduled monitoring (checks every {self.polling_interval}s for due URLs)")
        
        try:
            while self.running:
                schedule.run_pending()
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Monitoring scheduler stopped by user")
        except Exception as e:
            logger.error(f"Monitoring scheduler failed: {e}")
        finally:
            self.running = False
    
    def stop(self) -> None:
        """Stop the scheduled monitoring"""
        self.running = False
    
    def _run_monitoring_cycle(self) -> None:
        """Run a monitoring cycle"""
        try:
            self.monitoring_service.run_cycle()
        except Exception as e:
            logger.error(f"Monitoring cycle failed: {e}")