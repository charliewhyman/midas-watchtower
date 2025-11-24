"""URL scheduling functionality"""
import schedule
import time
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, TYPE_CHECKING

from config import AppConfig
from models import UrlSchedule
import logging

if TYPE_CHECKING:
    from monitoring_service import MonitoringService

logger = logging.getLogger(__name__)


class UrlScheduler:
    """Manages URL checking schedules using central interval"""
    
    def __init__(self, config: AppConfig):        
        self.config = config
        self.central_check_interval = config.central_check_interval
        self.schedules: Dict[str, UrlSchedule] = {}
        self._initialize_schedules()
        logger.info(f"🔧 URL Scheduler initialized with central interval: {self.central_check_interval}s")
    
    def _initialize_schedules(self) -> None:
        """Initialize schedules from configuration using central interval"""
        for url_config in self.config.url_configs:
            self.schedules[url_config.url] = UrlSchedule(
                url=url_config.url,
                type=url_config.type,
                priority=url_config.priority,
                next_check=datetime.now()  # All URLs start as due for immediate check
            )
    
    def get_due_urls(self) -> List[Dict[str, Any]]:
        """Get URLs that are due for checking using central interval"""
        due_urls = []
        current_time = datetime.now()
        
        for url, schedule in self.schedules.items():
            if schedule.next_check is None or current_time >= schedule.next_check:
                due_urls.append({
                    'url': url,
                    'config': schedule
                })
        
        logger.debug(f"Found {len(due_urls)} URLs due for checking")
        return due_urls
    
    def update_schedule(self, url: str) -> None:
        """Update schedule after URL check using central interval"""
        if url in self.schedules:
            schedule = self.schedules[url]
            schedule.last_checked = datetime.now()
            schedule.next_check = datetime.now() + timedelta(seconds=self.central_check_interval)
            logger.debug(f"Updated schedule for {url}: next check at {schedule.next_check}")
    
    def mark_url_as_checked(self, url: str, success: bool = True) -> None:
        """Mark URL as checked and schedule next check"""
        if url in self.schedules:
            self.schedules[url].last_checked = datetime.now()
            if success:
                self.schedules[url].next_check = datetime.now() + timedelta(seconds=self.central_check_interval)
            else:
                # On failure, retry sooner (half the interval)
                self.schedules[url].next_check = datetime.now() + timedelta(seconds=self.central_check_interval // 2)
    
    def get_status(self) -> Dict[str, Any]:
        """Get current scheduler status"""
        due_urls = self.get_due_urls()
        next_check_seconds = self._get_next_check_seconds()
        
        # Count URLs by priority
        priority_counts = {}
        for schedule in self.schedules.values():
            priority_counts[schedule.priority] = priority_counts.get(schedule.priority, 0) + 1
        
        return {
            'total_urls': len(self.schedules),
            'due_urls': len(due_urls),
            'next_check_in': next_check_seconds,
            'central_check_interval': self.central_check_interval,
            'priority_distribution': priority_counts,
            'polling_interval': self.config.scheduling.polling_interval
        }
    
    def _get_next_check_seconds(self) -> Optional[float]:
        """Get seconds until next scheduled check"""
        next_check = None
        
        for schedule in self.schedules.values():
            if schedule.next_check:
                if next_check is None or schedule.next_check < next_check:
                    next_check = schedule.next_check
        
        if next_check:
            seconds_until = (next_check - datetime.now()).total_seconds()
            return max(0, seconds_until)  # Don't return negative values
        
        return None
    
    def get_upcoming_checks(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get the next URLs to be checked"""
        upcoming = []
        for url, schedule in self.schedules.items():
            if schedule.next_check:
                upcoming.append({
                    'url': url,
                    'next_check': schedule.next_check,
                    'priority': schedule.priority,
                    'seconds_until': (schedule.next_check - datetime.now()).total_seconds()
                })
        
        # Sort by next check time
        upcoming.sort(key=lambda x: x['next_check'])
        return upcoming[:limit]
    
    def reset_schedule(self, url: str) -> None:
        """Reset schedule for a URL (make it due immediately)"""
        if url in self.schedules:
            self.schedules[url].next_check = datetime.now()
            logger.info(f"Reset schedule for {url} - will be checked immediately")


class MonitoringScheduler:
    """Main monitoring scheduler using polling interval"""
    
    def __init__(self, monitoring_service: 'MonitoringService', polling_interval: int = 300):
        self.monitoring_service = monitoring_service
        self.polling_interval = polling_interval
        self.running = False
        logger.info(f"🔧 Monitoring Scheduler initialized with polling interval: {self.polling_interval}s")
    
    def start(self) -> None:
        """Start the scheduled monitoring"""
        self.running = True
        
        # Schedule monitoring cycle based on polling interval
        polling_minutes = max(1, self.polling_interval // 60)  # At least 1 minute
        schedule.every(polling_minutes).minutes.do(self._run_monitoring_cycle)
        
        logger.info(f"🚀 Started scheduled monitoring (polling every {self.polling_interval}s for due URLs)")
        logger.info(f"📊 Central check interval: {self.monitoring_service.config.central_check_interval}s")
        logger.info(f"🔍 Polling interval: {self.polling_interval}s")
        
        # Log initial status
        status = self.monitoring_service.url_scheduler.get_status()
        logger.info(f"📈 Initial status: {status['total_urls']} URLs, {status['due_urls']} due")
        
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
            logger.info("Monitoring scheduler stopped")
    
    def stop(self) -> None:
        """Stop the scheduled monitoring"""
        self.running = False
        logger.info("Stopping monitoring scheduler...")
    
    def _run_monitoring_cycle(self) -> None:
        """Run a monitoring cycle"""
        try:
            cycle_start = datetime.now()
            logger.info(f"🔄 Starting scheduled monitoring cycle at {cycle_start}")
            
            # Get scheduler status before cycle
            pre_status = self.monitoring_service.url_scheduler.get_status()
            logger.info(f"📊 Pre-cycle: {pre_status['due_urls']}/{pre_status['total_urls']} URLs due")
            
            # Run the monitoring cycle
            stats = self.monitoring_service.run_cycle()
            
            # Log cycle results
            logger.info(f"✅ Monitoring cycle completed: "
                       f"{stats.changes_detected} changes, "
                       f"{stats.errors} errors, "
                       f"duration: {stats.duration_seconds:.1f}s")
            
        except Exception as e:
            logger.error(f"❌ Monitoring cycle failed: {e}")
    
    def get_status(self) -> Dict[str, Any]:
        """Get scheduler status"""
        url_scheduler_status = self.monitoring_service.url_scheduler.get_status()
        
        return {
            'running': self.running,
            'polling_interval': self.polling_interval,
            'central_check_interval': self.monitoring_service.config.central_check_interval,
            **url_scheduler_status
        }