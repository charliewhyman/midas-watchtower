"""Configuration management for AI Safety Monitor"""
import os
from pathlib import Path
from typing import Dict, Any, List, Optional
from pydantic_settings import BaseSettings
import yaml


class MonitorSettings(BaseSettings):
    """Application settings with validation"""
    
    # Google Sheets Configuration
    google_sheets_credentials_file: str = "google-sheets-credentials.json"
    
    # Google Sheets Environment Variables (for service account)
    google_sheets_type: Optional[str] = None
    google_sheets_project_id: Optional[str] = None
    google_sheets_private_key_id: Optional[str] = None
    google_sheets_private_key: Optional[str] = None
    google_sheets_client_email: Optional[str] = None
    google_sheets_client_id: Optional[str] = None
    google_sheets_auth_uri: str = "https://accounts.google.com/o/oauth2/auth"
    google_sheets_token_uri: str = "https://oauth2.googleapis.com/token"
    google_sheets_auth_provider_x509_cert_url: str = "https://www.googleapis.com/oauth2/v1/certs"
    google_sheets_client_x509_cert_url: Optional[str] = None
    
    # Application Settings
    polling_interval: int = 300  # 5 minutes
    request_timeout: int = 10
    max_retries: int = 3
    log_level: str = "INFO"
    # Thresholds for change detection (tunable per deployment)
    content_size_threshold: int = 1000  # bytes change considered significant
    word_count_threshold: int = 50  # words change considered significant
    word_count_major_threshold: int = 100  # larger change threshold
    policy_keyword_count_threshold: int = 2  # keyword count delta considered significant
    
    # File Paths
    data_dir: str = "data"
    logs_dir: str = "logs"
    history_file: str = "data/metadata_history.json"
    config_file: str = "config.yaml"
    
    @property
    def is_github_actions(self) -> bool:
        """Check if running in GitHub Actions environment"""
        return os.getenv('GITHUB_ACTIONS') == 'true'
    
    @property
    def should_use_github_actions_creds(self) -> bool:
        """Determine if we should use GitHub Actions credentials"""
        if not self.is_github_actions:
            return False
        
        # Check if required GitHub Actions secrets are available
        required_secrets = [
            'GOOGLE_SHEETS_TYPE',
            'GOOGLE_SHEETS_PROJECT_ID',
            'GOOGLE_SHEETS_PRIVATE_KEY_ID', 
            'GOOGLE_SHEETS_PRIVATE_KEY',
            'GOOGLE_SHEETS_CLIENT_EMAIL',
            'GOOGLE_SHEETS_CLIENT_ID',
        ]
        
        return all(os.getenv(secret) for secret in required_secrets)
    
    def should_use_env_creds(self) -> bool:
        """Detect if service-account fields are present in environment/config for env-based creds."""
        required_fields = [
            'google_sheets_type',
            'google_sheets_project_id',
            'google_sheets_private_key_id',
            'google_sheets_private_key',
            'google_sheets_client_email',
            'google_sheets_client_id',
        ]
        return all(bool(getattr(self, f, None)) for f in required_fields)

    def get_google_sheets_credential_source(self) -> str:
        """Determine which credential source to use: 'github_actions', 'environment', or 'file'."""
        if self.should_use_github_actions_creds:
            return "github_actions"
        if self.should_use_env_creds():
            return "environment"
        return "file"

    class Config:
        env_file = ".env"
        case_sensitive = False


class UrlConfig(BaseSettings):
    """Configuration for a single monitored URL"""
    url: str
    type: str = "policy"  # policy, research, guideline, etc.
    priority: str = "medium"  # low, medium, high, critical
    # Removed individual check_interval


class SchedulingConfig(BaseSettings):
    """Scheduling configuration"""
    polling_interval: int = 300  # 5 minutes - how often to check for due URLs


class AppConfig:
    """Main application configuration manager"""
    
    def __init__(self, config_path: str = "config.yaml"):
        self.settings = MonitorSettings()
        self.config_path = Path(config_path)
        self.central_check_interval: int = 3600  # Default: 1 hour
        self.url_configs: List[UrlConfig] = []
        self.scheduling: SchedulingConfig = SchedulingConfig()
        self.load_config()
    
    def load_config(self) -> None:
        """Load configuration from YAML file"""
        try:
            if self.config_path.exists():
                with open(self.config_path, 'r') as f:
                    config_data = yaml.safe_load(f) or {}
                self._parse_config(config_data)
            else:
                self._create_default_config()
        except (OSError, yaml.YAMLError, ValueError) as e:
            raise ConfigurationError(f"Failed to load config from {self.config_path}: {e}")
    
    def _parse_config(self, config_data: Dict[str, Any]) -> None:
        """Parse configuration data"""
        # Parse central check interval
        self.central_check_interval = config_data.get('central_check_interval', 3600)
        
        # Parse URL configurations
        for url_config in config_data.get('monitored_urls', []):
            # Remove check_interval from URL config if present (for backward compatibility)
            url_config_data = url_config.copy()
            url_config_data.pop('check_interval', None)  # Remove individual intervals
            self.url_configs.append(UrlConfig(**url_config_data))
        
        # Parse scheduling configuration
        if 'scheduling' in config_data:
            scheduling_data = config_data['scheduling']
            self.scheduling = SchedulingConfig(**scheduling_data)
            
            # Override settings from config file
            self.settings.polling_interval = scheduling_data.get('polling_interval', 300)
    
    def _create_default_config(self) -> None:
        """Create default configuration file"""
        default_config = {
            'central_check_interval': 3600,
            'monitored_urls': [],
            'scheduling': {
                'polling_interval': 300
            }
        }
        
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.config_path, 'w') as f:
            yaml.dump(default_config, f, default_flow_style=False)
        
        self._parse_config(default_config)
    
    def validate_urls(self) -> List[str]:
        """Validate all URL configurations"""
        errors = []
        seen_urls = set()
        
        for url_config in self.url_configs:
            if url_config.url in seen_urls:
                errors.append(f"Duplicate URL: {url_config.url}")
            seen_urls.add(url_config.url)
            
            # Validate URL format (basic check)
            if not url_config.url.startswith(('http://', 'https://')):
                errors.append(f"Invalid URL format: {url_config.url}")
            
            # Validate priority
            if url_config.priority not in ['low', 'medium', 'high', 'critical']:
                errors.append(f"Invalid priority for {url_config.url}: {url_config.priority}")
        
        # Validate central check interval
        if self.central_check_interval < 300:  # Minimum 5 minutes
            errors.append(f"Central check interval too short: {self.central_check_interval}s (minimum: 300s)")
        
        if self.central_check_interval > 86400:  # Maximum 1 day
            errors.append(f"Central check interval too long: {self.central_check_interval}s (maximum: 86400s)")
        
        return errors
    
    def get_config_summary(self) -> Dict[str, Any]:
        """Get configuration summary for logging and status"""
        priority_counts = {}
        type_counts = {}
        
        for url_config in self.url_configs:
            priority_counts[url_config.priority] = priority_counts.get(url_config.priority, 0) + 1
            type_counts[url_config.type] = type_counts.get(url_config.type, 0) + 1
        
        return {
            'total_urls': len(self.url_configs),
            'central_check_interval': self.central_check_interval,
            'polling_interval': self.scheduling.polling_interval,
            'priority_distribution': priority_counts,
            'type_distribution': type_counts,
            'sheets_credential_source': self.settings.get_google_sheets_credential_source(),
            'sheets_configured': (
                self.settings.get_google_sheets_credential_source() in ('github_actions', 'environment')
                or Path(self.settings.google_sheets_credentials_file).exists()
            )
        }


class ConfigurationError(Exception):
    """Configuration-related errors"""
    pass