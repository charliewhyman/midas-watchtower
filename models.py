"""Data models for AI Safety Monitor"""
from datetime import datetime
from typing import Dict, Any, List, Optional
from pydantic import BaseModel
import yaml


class URLConfig(BaseModel):
    """Configuration for a monitored URL"""
    url: str
    type: str
    priority: str = "medium"

class SchedulingConfig(BaseModel):
    """Scheduling configuration"""
    polling_interval: int = 300  # 5 minutes - how often to check for due URLs


class AppConfig(BaseModel):
    """Main application configuration"""
    central_check_interval: int = 3600
    monitored_urls: List[URLConfig]
    scheduling: SchedulingConfig

    @classmethod
    def load_from_yaml(cls, file_path: str) -> 'AppConfig':
        """Load configuration from YAML file"""
        with open(file_path, 'r') as f:
            data = yaml.safe_load(f)
        return cls(**data)


class UrlMetadata(BaseModel):
    """Metadata for a URL"""
    url: str
    timestamp: datetime
    status_code: Optional[int]
    headers: Dict[str, str] = {}
    final_url: str
    error: Optional[str] = None
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class ChangeDetails(BaseModel):
    """Details of a detected change"""
    change_type: str  # 'content_change', 'metadata_change', 'status_change', etc.
    source: str  # 'changedetection', 'direct_metadata', etc.
    details: Dict[str, Any] = {}
    severity: str = 'medium'  # low, medium, high, critical


class DetectedChange(BaseModel):
    """A detected change event"""
    url: str
    changes: List[ChangeDetails]
    metadata: Optional[UrlMetadata] = None
    timestamp: datetime
    change_source: str
    priority: str = 'medium'
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class MonitoringCycleStats(BaseModel):
    """Statistics for a monitoring cycle"""
    cycle_id: str
    start_time: datetime
    end_time: Optional[datetime] = None
    urls_checked: int = 0
    changes_detected: int = 0
    errors: int = 0
    sheets_logged: int = 0
    sheets_failed: int = 0
    first_run: bool = False
    duration_seconds: Optional[float] = None
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class UrlSchedule(BaseModel):
    """Scheduling information for a URL"""
    url: str
    type: str
    priority: str
    last_checked: Optional[datetime] = None
    next_check: Optional[datetime] = None
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }