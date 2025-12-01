"""Data models for AI Safety Monitor"""
from datetime import datetime
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field
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


class HtmlMetadata(BaseModel):
    """HTML content metadata"""
    url: str
    title: Optional[str] = None
    meta_description: Optional[str] = None
    canonical_url: Optional[str] = None
    og_metadata: Dict[str, str] = Field(default_factory=dict)
    twitter_metadata: Dict[str, str] = Field(default_factory=dict)
    other_metadata: Dict[str, str] = Field(default_factory=dict)
    structured_data: Dict[str, Any] = Field(default_factory=dict)
    important_links: Dict[str, List[Dict[str, str]]] = Field(default_factory=dict)
    content_analysis: Dict[str, Any] = Field(default_factory=dict)
    language: Optional[str] = None
    charset: Optional[str] = None
    has_forms: bool = False
    has_comments: bool = False
    error: Optional[str] = None


class UrlMetadata(BaseModel):
    """Enhanced URL metadata with HTML content"""
    url: str
    timestamp: datetime
    status_code: Optional[int] = None
    headers: Dict[str, str] = Field(default_factory=dict)
    final_url: Optional[str] = None
    html_metadata: Optional[HtmlMetadata] = None
    content_length: int = 0
    response_time: float = 0.0
    error: Optional[str] = None
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class ChangeDetails(BaseModel):
    """Details of a detected change"""
    change_type: str  # 'content_change', 'metadata_change', 'status_change', 'policy_change', etc.
    source: str  # 'direct_metadata', 'html_metadata', 'policy_analysis', etc.
    details: Dict[str, Any] = Field(default_factory=dict)
    severity: str = 'medium'  # low, medium, high, critical
    policy_alert: bool = False  # Whether this is a policy-relevant change


class DetectedChange(BaseModel):
    """A detected change event"""
    url: str
    changes: List[ChangeDetails]
    metadata: Optional[UrlMetadata] = None
    timestamp: datetime
    change_source: str  # 'direct_metadata', 'content', 'policy_metadata', 'html_analysis'
    priority: str = 'medium'
    policy_alerts: Optional[List[Dict[str, Any]]] = Field(default_factory=list)
    
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
    html_parsing_errors: int = 0
    policy_alerts_detected: int = 0
    
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


class PolicyAlert(BaseModel):
    """Policy-specific alert for detected changes"""
    alert_type: str  # 'STEALTH_VERSION_CHANGE', 'KEY_SECTION_CONTENT_CHANGE', etc.
    severity: str  # 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL'
    message: str
    details: Dict[str, Any] = Field(default_factory=dict)
    url: str
    timestamp: datetime
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class ContentAnalysis(BaseModel):
    """Detailed content analysis results"""
    word_count: int = 0
    heading_structure: Dict[str, int] = Field(default_factory=dict)
    image_count: int = 0
    paragraph_count: int = 0
    list_count: int = 0
    has_main_content: bool = False
    text_preview: Optional[str] = None
    policy_keyword_counts: Dict[str, int] = Field(default_factory=dict)
    version_indicators: List[str] = Field(default_factory=list)
    date_indicators: List[str] = Field(default_factory=list)
    has_legal_language: bool = False


# Configuration models for HTML parsing settings
class HtmlParsingConfig(BaseModel):
    """HTML parsing configuration"""
    enabled: bool = True
    parse_titles: bool = True
    parse_meta: bool = True
    parse_opengraph: bool = True
    parse_structured_data: bool = True
    content_analysis: bool = True
    policy_analysis: bool = True


class SettingsConfig(BaseModel):
    """Application settings configuration"""
    max_retries: int = 3
    request_timeout: int = 30
    history_file: str = "data/change_history.json"
    html_parsing: HtmlParsingConfig = Field(default_factory=HtmlParsingConfig)


class EnhancedAppConfig(BaseModel):
    """Enhanced application configuration with HTML parsing settings"""
    central_check_interval: int = 3600
    monitored_urls: List[URLConfig]
    scheduling: SchedulingConfig
    settings: SettingsConfig = Field(default_factory=SettingsConfig)

    @classmethod
    def load_from_yaml(cls, file_path: str) -> 'EnhancedAppConfig':
        """Load configuration from YAML file"""
        with open(file_path, 'r') as f:
            data = yaml.safe_load(f)
        
        # Handle both old and new config formats
        if 'settings' not in data:
            data['settings'] = {
                'max_retries': 3,
                'request_timeout': 30,
                'history_file': "data/change_history.json",
                'html_parsing': {
                    'enabled': True,
                    'parse_titles': True,
                    'parse_meta': True,
                    'parse_opengraph': True,
                    'parse_structured_data': True,
                    'content_analysis': True,
                    'policy_analysis': True
                }
            }
        
        return cls(**data)