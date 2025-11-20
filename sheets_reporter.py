"""Google Sheets reporting functionality"""
import os
from typing import Optional
from google.oauth2.service_account import Credentials
import gspread

from config import AppConfig
from models import DetectedChange
import logging

logger = logging.getLogger(__name__)


class GoogleSheetsReporter:
    """Handles reporting to Google Sheets"""
    
    def __init__(self, config: AppConfig):
        self.config = config
        self.client = None
        self.setup_client()
    
    def setup_client(self) -> None:
        """Setup Google Sheets client"""
        try:
            scopes = [
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive"
            ]
            
            # Use the config to determine credential source
            cred_source = self.config.settings.get_google_sheets_credential_source()
            
            if cred_source == "github_actions":
                creds = self._get_credentials_from_github_actions()
            elif cred_source == "environment":
                creds = self._get_credentials_from_env()
            else:  # file
                creds = self._get_credentials_from_file()
            
            if creds:
                self.client = gspread.authorize(creds)
                logger.info("Google Sheets client authorized successfully")
                self.test_connection()
            else:
                logger.error("Failed to create Google Sheets credentials")
                self.client = None
                
        except Exception as e:
            logger.error(f"Unexpected error setting up Google Sheets: {e}")
            self.client = None
    
    def _is_github_actions(self) -> bool:
        """Check if running in GitHub Actions environment"""
        return os.getenv('GITHUB_ACTIONS') == 'true'
    
    def _get_credentials_from_github_actions(self) -> Optional[Credentials]:
        """Create credentials from GitHub Actions secrets"""
        try:
            logger.info("Setting up Google Sheets credentials from GitHub Actions secrets")
            
            # Required environment variables from GitHub Secrets
            required_secrets = {
                'GOOGLE_SHEETS_TYPE': 'type',
                'GOOGLE_SHEETS_PROJECT_ID': 'project_id',
                'GOOGLE_SHEETS_PRIVATE_KEY_ID': 'private_key_id',
                'GOOGLE_SHEETS_PRIVATE_KEY': 'private_key',
                'GOOGLE_SHEETS_CLIENT_EMAIL': 'client_email',
                'GOOGLE_SHEETS_CLIENT_ID': 'client_id',
            }
            
            # Check for missing secrets
            missing_secrets = []
            service_account_info = {}
            
            for env_var, key in required_secrets.items():
                value = os.getenv(env_var)
                if not value:
                    missing_secrets.append(env_var)
                else:
                    service_account_info[key] = value
            
            if missing_secrets:
                logger.error(f"Missing required GitHub Actions secrets: {missing_secrets}")
                return None
            
            # Handle private key formatting (replace escaped newlines)
            if 'private_key' in service_account_info:
                service_account_info['private_key'] = service_account_info['private_key'].replace('\\n', '\n')
            
            # Add optional fields with defaults if not provided
            optional_fields = {
                'auth_uri': 'https://accounts.google.com/o/oauth2/auth',
                'token_uri': 'https://oauth2.googleapis.com/token',
                'auth_provider_x509_cert_url': 'https://www.googleapis.com/oauth2/v1/certs',
                'client_x509_cert_url': f"https://www.googleapis.com/robot/v1/metadata/x509/{service_account_info['client_email'].replace('@', '%40')}"
            }
            
            for key, default_value in optional_fields.items():
                env_var = f"GOOGLE_SHEETS_{key.upper()}"
                value = os.getenv(env_var, default_value)
                service_account_info[key] = value
            
            scopes = [
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive"
            ]
            
            creds = Credentials.from_service_account_info(service_account_info, scopes=scopes)
            logger.info("Successfully created credentials from GitHub Actions secrets")
            return creds
            
        except Exception as e:
            logger.error(f"Error creating credentials from GitHub Actions: {e}")
            return None
    
    def _get_credentials_from_env(self) -> Optional[Credentials]:
        """Create credentials from environment variables"""
        try:
            required_vars = [
                'GOOGLE_SHEETS_TYPE',
                'GOOGLE_SHEETS_PROJECT_ID', 
                'GOOGLE_SHEETS_PRIVATE_KEY_ID',
                'GOOGLE_SHEETS_PRIVATE_KEY',
                'GOOGLE_SHEETS_CLIENT_EMAIL',
                'GOOGLE_SHEETS_CLIENT_ID',
            ]
            
            missing_vars = [var for var in required_vars if not getattr(self.config.settings, var.lower(), None)]
            if missing_vars:
                logger.error(f"Missing required environment variables: {missing_vars}")
                return None
            
            service_account_info = {
                "type": self.config.settings.google_sheets_type,
                "project_id": self.config.settings.google_sheets_project_id,
                "private_key_id": self.config.settings.google_sheets_private_key_id,
                "private_key": self.config.settings.google_sheets_private_key.replace('\\n', '\n'),
                "client_email": self.config.settings.google_sheets_client_email,
                "client_id": self.config.settings.google_sheets_client_id,
                "auth_uri": self.config.settings.google_sheets_auth_uri,
                "token_uri": self.config.settings.google_sheets_token_uri,
                "auth_provider_x509_cert_url": self.config.settings.google_sheets_auth_provider_x509_cert_url,
                "client_x509_cert_url": self.config.settings.google_sheets_client_x509_cert_url,
            }
            
            scopes = [
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive"
            ]
            
            return Credentials.from_service_account_info(service_account_info, scopes=scopes)
            
        except Exception as e:
            logger.error(f"Error creating credentials from environment: {e}")
            return None
    
    def _get_credentials_from_file(self) -> Optional[Credentials]:
        """Create credentials from service account file"""
        try:
            credentials_file = self.config.settings.google_sheets_credentials_file
            if not os.path.exists(credentials_file):
                logger.error(f"Google Sheets credentials file not found: {credentials_file}")
                return None
            
            scopes = [
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive"
            ]
            
            return Credentials.from_service_account_file(credentials_file, scopes=scopes)
            
        except Exception as e:
            logger.error(f"Error creating credentials from file: {e}")
            return None
    
    def test_connection(self) -> bool:
        """Test Google Sheets connection"""
        if not self.client:
            return False
        
        try:
            spreadsheets = self.client.list_spreadsheet_files()
            logger.info(f"Google Sheets connection test successful. Found {len(spreadsheets)} spreadsheets.")
            return True
        except Exception as e:
            logger.error(f"Google Sheets connection test failed: {e}")
            self.client = None
            return False
    
    def ensure_spreadsheet_exists(self, spreadsheet_name: str = "AI Safety Changes Monitor") -> Optional[gspread.Spreadsheet]:
        """Create or get existing spreadsheet"""
        if not self.client:
            logger.error("Google Sheets client not available")
            return None
            
        try:
            spreadsheet = self.client.open(spreadsheet_name)
            logger.info(f"Using existing spreadsheet: {spreadsheet_name}")
            return spreadsheet
        except gspread.SpreadsheetNotFound:
            logger.info(f"Spreadsheet not found, creating new one: {spreadsheet_name}")
            try:
                spreadsheet = self.client.create(spreadsheet_name)
                logger.info(f"Created new spreadsheet: {spreadsheet_name}")
                return spreadsheet
            except Exception as e:
                logger.error(f"Failed to create spreadsheet: {e}")
                return None
        except Exception as e:
            logger.error(f"Error accessing spreadsheet: {e}")
            return None
    
    def setup_sheets_structure(self, spreadsheet: gspread.Spreadsheet) -> Optional[gspread.Worksheet]:
        """Setup the sheets with proper structure"""
        try:
            worksheet = spreadsheet.worksheet("Changes_Log")
            # Ensure headers exist
            if worksheet.row_count == 0 or worksheet.row_values(1) == []:
                headers = [
                    "Timestamp", "URL", "Change Type", "Change Details", 
                    "Status Code", "Content Type", "Final URL", "Source",
                    "Priority", "Resolved", "Notes"
                ]
                worksheet.append_row(headers)
            return worksheet
        except gspread.WorksheetNotFound:
            worksheet = spreadsheet.add_worksheet(title="Changes_Log", rows=1000, cols=11)
            headers = [
                "Timestamp", "URL", "Change Type", "Change Details", 
                "Status Code", "Content Type", "Final URL", "Source",
                "Priority", "Resolved", "Notes"
            ]
            worksheet.append_row(headers)
            return worksheet
    
    def log_change(self, change: DetectedChange) -> bool:
        """Log a change to Google Sheets, ensuring headers exist first"""
        if not self.client:
            logger.error("Google Sheets client not available")
            return False
        
        try:
            spreadsheet = self.ensure_spreadsheet_exists()
            if not spreadsheet:
                logger.error("Failed to get or create spreadsheet")
                return False
            
            worksheet = self.setup_sheets_structure(spreadsheet)
            if not worksheet:
                logger.error("Failed to get or create worksheet")
                return False
            
            change_row = self._prepare_change_row(change)
            worksheet.append_row(change_row)
            
            logger.info(f"Successfully logged change: {change.url}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to log change to Google Sheets: {e}")
            return False
    
    def _prepare_change_row(self, change: DetectedChange) -> list:
        """Prepare a row for the Changes_Log sheet"""
        try:
            # Extract change types and details
            change_types = []
            change_details = []
            
            for change_detail in change.changes:
                change_types.append(change_detail.change_type)
                
                if change_detail.change_type == 'content_change':
                    change_details.append("Content modified")
                elif change_detail.change_type == 'status_change':
                    details = change_detail.details
                    change_details.append(f"Status: {details.get('old_status')}→{details.get('new_status')}")
                elif change_detail.change_type == 'content_type_change':
                    details = change_detail.details
                    change_details.append(f"Content-Type: {details.get('old_type')}→{details.get('new_type')}")
                elif change_detail.change_type == 'redirect_change':
                    details = change_detail.details
                    change_details.append(f"Redirect: {details.get('old_url')}→{details.get('new_url')}")
            
            # Get metadata information
            metadata = change.metadata.dict() if change.metadata else {}
            status_code = metadata.get('status_code', '')
            content_type = metadata.get('headers', {}).get('content-type', '')
            final_url = metadata.get('final_url', change.url)
            
            return [
                change.timestamp.isoformat(),
                change.url,
                ', '.join(change_types),
                '; '.join(change_details) if change_details else 'No changes detected',
                status_code,
                content_type,
                final_url,
                change.change_source,
                change.priority,
                'FALSE',  # Not resolved
                ''        # Notes
            ]
        
        except Exception as e:
            logger.error(f"Error preparing change row: {e}")
            return ['ERROR'] * 11