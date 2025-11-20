"""Configuration management for AI Safety Monitor"""
from pathlib import Path
from typing import Dict, Any, List, Optional
from pydantic_settings import BaseSettings
import yaml


class MonitorSettings(BaseSettings):
    """Application settings with validation"""
    
    # Google Sheets Configuration
    google_sheets_use_env: bool = False
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
    
    # Changedetection.io Configuration
    changedetection_url: str = "http://changedetection:5000"
    changedetection_api_key: Optional[str] = None
    
    # Application Settings
    polling_interval: int = 300  # 5 minutes
    request_timeout: int = 10
    max_retries: int = 3
    log_level: str = "INFO"
    
    # File Paths
    data_dir: str = "data"
    logs_dir: str = "logs"
    history_file: str = "data/url_history.json"
    config_file: str = "config.yaml"
    
    class Config:
        env_file = ".env"
        case_sensitive = False


class UrlConfig(BaseSettings):
    """Configuration for a single monitored URL"""
    url: str
    check_interval: int = 3600  # 1 hour
    type: str = "policy"  # policy, research, guideline, etc.
    priority: str = "medium"  # low, medium, high, critical


class AppConfig:
    """Main application configuration manager"""
    
    def __init__(self, config_path: str = "config.yaml"):
        self.settings = MonitorSettings()
        self.config_path = Path(config_path)
        self.url_configs: List[UrlConfig] = []
        self.load_config()
    
    def load_config(self) -> None:
        """Load configuration from YAML file"""
        try:
            if self.config_path.exists():
                with open(self.config_path, 'r') as f:
                    config_data = yaml.safe_load(f)
                self._parse_config(config_data)
            else:
                self._create_default_config()
        except Exception as e:
            raise ConfigurationError(f"Failed to load config from {self.config_path}: {e}")
    
    def _parse_config(self, config_data: Dict[str, Any]) -> None:
        """Parse configuration data"""
        # Parse URL configurations
        for url_config in config_data.get('monitored_urls', []):
            self.url_configs.append(UrlConfig(**url_config))
        
        # Override settings from config file
        if 'scheduling' in config_data:
            self.settings.polling_interval = config_data['scheduling'].get('polling_interval', 300)
    
    def _create_default_config(self) -> None:
        """Create default configuration file"""
        default_config = {
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
            
            if url_config.check_interval < 60:
                errors.append(f"Check interval too short for {url_config.url}: {url_config.check_interval}")
        
        return errors


class ConfigurationError(Exception):
    """Configuration-related errors"""
    pass