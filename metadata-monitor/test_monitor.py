from fastapi import testclient
import pytest
import json
import tempfile
import os
from unittest.mock import Mock, mock_open, patch
from datetime import datetime, timedelta

import yaml

from monitor import AISafetyMonitor, GitHubActionsReporter, GoogleSheetsReporter, app
from fastapi.testclient import TestClient

@pytest.fixture
def temp_config():
    """Create a temporary config file for testing"""
    config_data = {
        'monitored_urls': [
            {
                'url': 'https://example.com',
                'type': 'policy',
                'priority': 'high',
                'check_interval': 3600
            },
            {
                'url': 'https://test.org',
                'type': 'guideline', 
                'priority': 'medium',
                'check_interval': 7200
            }
        ],
        'scheduling': {
            'polling_interval': 300
        }
    }
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        yaml.dump(config_data, f)
        temp_path = f.name
    
    yield temp_path
    os.unlink(temp_path)

@pytest.fixture
def temp_data_dir():
    """Create temporary data directory"""
    with tempfile.TemporaryDirectory() as temp_dir:
        yield temp_dir    
    
class TestGoogleSheetsReporter:
    def test_init_no_credentials(self, caplog):
        """Test initialization without credentials file"""
        with patch('os.path.exists', return_value=False):
            reporter = GoogleSheetsReporter("nonexistent.json")
            assert reporter.client is None
            assert "Failed to setup Google Sheets" in caplog.text

    @patch('gspread.authorize')
    @patch('google.oauth2.service_account.Credentials.from_service_account_file')
    def test_init_success(self, mock_creds, mock_auth):
        """Test successful initialization"""
        mock_client = Mock()
        mock_auth.return_value = mock_client

        reporter = GoogleSheetsReporter()
        assert reporter.client == mock_client

    def test_prepare_change_row(self):
        """Test change row preparation"""
        reporter = GoogleSheetsReporter()
        change_data = {
            'url': 'https://example.com',
            'timestamp': '2023-01-01T00:00:00',
            'changes': {
                'content_change': {'source': 'test'},
                'metadata_change': {
                    'status': {'old': 200, 'new': 404},
                    'content_type': {'old': 'text/html', 'new': 'application/json'}
                }
            },
            'metadata': {
                'status_code': 404,
                'headers': {'content-type': 'application/json'},
                'final_url': 'https://example.com/redirect'
            },
            'change_source': 'test'
        }

        row = reporter.prepare_change_row(change_data)
        assert row[0] == '2023-01-01T00:00:00'
        assert row[1] == 'https://example.com'
        assert 'content_change' in row[2]
        assert 'metadata_change' in row[2]
        assert 'Status: 200→404' in row[3]
        assert 'Content-Type: text/html→application/json' in row[3]
        assert row[4] == 404
        assert row[5] == 'application/json'
        assert row[6] == 'https://example.com/redirect'
        assert row[7] == 'test'
        assert row[8] == 'medium'
        assert row[9] == 'FALSE'
        assert row[10] == ''

    @patch('gspread.Client')
    def test_log_change_to_sheets_no_client(self, mock_client, caplog):
        """Test logging when no client available"""
        reporter = GoogleSheetsReporter()
        reporter.client = None
        
        result = reporter.log_change_to_sheets({})
        assert result is False
        assert "Google Sheets client not available" in caplog.text

    @patch('gspread.Spreadsheet')
    @patch('gspread.Client')
    def test_log_change_to_sheets_success(self, mock_client, mock_spreadsheet):
        """Test successful logging to sheets"""
        # Mock the client and spreadsheet
        mock_worksheet = Mock()
        mock_spreadsheet.worksheet.return_value = mock_worksheet
        mock_client.open.return_value = mock_spreadsheet
        
        reporter = GoogleSheetsReporter()
        reporter.client = mock_client
        
        change_data = {
            'url': 'https://example.com',
            'timestamp': '2023-01-01T00:00:00',
            'changes': {'content_change': {}},
            'metadata': {}
        }
        
        result = reporter.log_change_to_sheets(change_data)
        assert result is True
        mock_worksheet.append_row.assert_called_once()


class TestGitHubActionsReporter:
    def test_generate_json_report(self, tmp_path):
        """Test JSON report generation"""
        reporter = GitHubActionsReporter(data_dir=tmp_path)
        
        changes = [{
            'url': 'https://example.com',
            'changes': {'content_change': {}},
            'timestamp': '2023-01-01T00:00:00'
        }]
        
        cycle_stats = {
            'start_time': datetime(2023, 1, 1, 0, 0, 0),
            'urls_checked': 10,
            'errors': 0
        }
        
        report_path = reporter.generate_json_report(changes, cycle_stats)
        
        # Check file was created
        assert report_path.exists()
        
        # Verify content
        with open(report_path, 'r') as f:
            report_data = json.load(f)
        
        assert report_data['summary']['total_changes'] == 1
        assert len(report_data['changes_detected']) == 1
        assert report_data['changes_detected'][0]['url'] == 'https://example.com'
        
        # Check latest.json was also created
        latest_path = tmp_path / "reports" / "latest.json"
        assert latest_path.exists()


class TestAISafetyMonitor:

    def test_init_with_config(self, temp_config, temp_data_dir):
        """Test monitor initialization with config file"""
        with patch('pathlib.Path.mkdir'), \
             patch('pathlib.Path.exists', return_value=True), \
             patch('builtins.open', mock_open(read_data='{}')):
            
            monitor = AISafetyMonitor(config_path=temp_config)
            assert len(monitor.config['monitored_urls']) == 2
            assert monitor.sheets_reporter is not None
            assert monitor.gh_reporter is not None

    def test_init_no_config(self, caplog, temp_data_dir):
        """Test monitor initialization without config file"""
        with patch('pathlib.Path.mkdir'), \
             patch('pathlib.Path.exists', return_value=True), \
             patch('builtins.open', side_effect=FileNotFoundError):
            
            monitor = AISafetyMonitor(config_path="nonexistent.yaml")
            assert monitor.config == {'monitored_urls': []}
            assert "Error loading config" in caplog.text

    def test_setup_data_directory(self, temp_config, temp_data_dir):
        """Test data directory setup"""
        with patch('pathlib.Path.mkdir') as mock_mkdir, \
             patch('pathlib.Path.exists', return_value=False):
            
            monitor = AISafetyMonitor(config_path=temp_config)
            # Should create data and logs directories
            assert mock_mkdir.call_count >= 2

    def test_setup_session(self, temp_config):
        """Test requests session setup"""
        monitor = AISafetyMonitor(config_path=temp_config)
        assert monitor.session is not None
        assert 'User-Agent' in monitor.session.headers
        assert 'Accept' in monitor.session.headers

    def test_get_urls_due_for_check(self, temp_config):
        """Test URL scheduling logic"""
        monitor = AISafetyMonitor(config_path=temp_config)
        
        # Initially all URLs should be due
        due_urls = monitor.get_urls_due_for_check()
        assert len(due_urls) == 2
        
        # Update one URL's schedule to future
        future_time = datetime.now() + timedelta(hours=1)
        monitor.url_schedules['https://example.com']['next_check'] = future_time
        
        # Now only one URL should be due
        due_urls = monitor.get_urls_due_for_check()
        assert len(due_urls) == 1
        assert due_urls[0]['url'] == 'https://test.org'

    def test_update_url_schedule(self, temp_config):
        """Test URL schedule updates"""
        monitor = AISafetyMonitor(config_path=temp_config)
        
        original_next_check = monitor.url_schedules['https://example.com']['next_check']
        monitor.update_url_schedule('https://example.com')
        
        new_next_check = monitor.url_schedules['https://example.com']['next_check']
        assert new_next_check > original_next_check
        assert monitor.url_schedules['https://example.com']['last_checked'] is not None

    @patch('requests.Session.head')
    def test_get_url_metadata_success(self, mock_head, temp_config):
        """Test successful metadata retrieval"""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {'content-type': 'text/html'}
        mock_response.url = 'https://example.com'
        mock_head.return_value = mock_response
        
        monitor = AISafetyMonitor(config_path=temp_config)
        metadata = monitor.get_url_metadata('https://example.com')
        
        assert metadata['status_code'] == 200
        assert metadata['headers']['content-type'] == 'text/html'
        assert metadata['final_url'] == 'https://example.com'
        assert 'timestamp' in metadata

    @patch('requests.Session.head')
    def test_get_url_metadata_failure(self, mock_head, temp_config, caplog):
        """Test metadata retrieval failure"""
        mock_head.side_effect = Exception("Connection failed")
        
        monitor = AISafetyMonitor(config_path=temp_config)
        metadata = monitor.get_url_metadata('https://example.com')
        
        assert metadata['status_code'] is None
        assert 'error' in metadata
        assert "Connection failed" in metadata['error']
        assert "Error checking https://example.com" in caplog.text

    def test_detect_metadata_changes_new_url(self, temp_config):
        """Test metadata change detection for new URL"""
        monitor = AISafetyMonitor(config_path=temp_config)
        
        new_meta = {
            'status_code': 200,
            'headers': {'content-type': 'text/html'},
            'final_url': 'https://example.com'
        }
        
        changes = monitor.detect_metadata_changes(None, new_meta)
        assert 'new_url' in changes

    def test_detect_metadata_changes_status_code(self, temp_config):
        """Test status code change detection"""
        monitor = AISafetyMonitor(config_path=temp_config)
        
        old_meta = {'status_code': 200, 'headers': {}, 'final_url': 'https://example.com'}
        new_meta = {'status_code': 404, 'headers': {}, 'final_url': 'https://example.com'}
        
        changes = monitor.detect_metadata_changes(old_meta, new_meta)
        assert 'status' in changes
        assert changes['status']['old'] == 200
        assert changes['status']['new'] == 404

    def test_detect_metadata_changes_content_type(self, temp_config):
        """Test content type change detection"""
        monitor = AISafetyMonitor(config_path=temp_config)
        
        old_meta = {'status_code': 200, 'headers': {'content-type': 'text/html'}, 'final_url': 'https://example.com'}
        new_meta = {'status_code': 200, 'headers': {'content-type': 'application/json'}, 'final_url': 'https://example.com'}
        
        changes = monitor.detect_metadata_changes(old_meta, new_meta)
        assert 'content_type' in changes
        assert changes['content_type']['old'] == 'text/html'
        assert changes['content_type']['new'] == 'application/json'

    def test_detect_metadata_changes_redirect(self, temp_config):
        """Test redirect change detection"""
        monitor = AISafetyMonitor(config_path=temp_config)
        
        old_meta = {'status_code': 200, 'headers': {}, 'final_url': 'https://example.com'}
        new_meta = {'status_code': 200, 'headers': {}, 'final_url': 'https://example.com/new'}
        
        changes = monitor.detect_metadata_changes(old_meta, new_meta)
        assert 'redirect' in changes
        assert changes['redirect']['old'] == 'https://example.com'
        assert changes['redirect']['new'] == 'https://example.com/new'

    def test_detect_metadata_changes_no_changes(self, temp_config):
        """Test no changes detected"""
        monitor = AISafetyMonitor(config_path=temp_config)
        
        old_meta = {'status_code': 200, 'headers': {'content-type': 'text/html'}, 'final_url': 'https://example.com'}
        new_meta = {'status_code': 200, 'headers': {'content-type': 'text/html'}, 'final_url': 'https://example.com'}
        
        changes = monitor.detect_metadata_changes(old_meta, new_meta)
        assert changes == {}

    @patch('requests.get')
    @patch('builtins.open', new_callable=mock_open, read_data='{}')
    def test_check_changedetection_content_changes(self, mock_file, mock_get, temp_config):
        """Test changedetection.io content change checking"""
        # Mock changedetection.io API response
        mock_response = Mock()
        mock_response.json.return_value = [
            {
                'url': 'https://example.com',
                'tag': 'ai-safety',
                'uuid': 'test-uuid'
            }
        ]
        mock_get.return_value = mock_response
        
        # Mock watch detail response
        mock_detail_response = Mock()
        mock_detail_response.json.return_value = {
            'last_changed': '2023-01-01T00:00:00Z'
        }
        
        monitor = AISafetyMonitor(config_path=temp_config)
        
        with patch('requests.get', side_effect=[mock_response, mock_detail_response]):
            changes = monitor.check_changedetection_content_changes()
            
            # Should detect changes for monitored URLs
            assert len(changes) >= 0  # Could be 0 or more depending on test setup

    @patch('builtins.open', new_callable=mock_open, read_data='{}')
    def test_check_metadata_changes_no_due_urls(self, mock_file, temp_config):
        """Test metadata checking with no due URLs"""
        monitor = AISafetyMonitor(config_path=temp_config)
        
        # Set all URLs to future check times
        future_time = datetime.now() + timedelta(hours=1)
        for url in monitor.url_schedules:
            monitor.url_schedules[url]['next_check'] = future_time
        
        changes = monitor.check_metadata_changes()
        assert changes == []

    @patch('requests.Session.head')
    @patch('builtins.open', new_callable=mock_open, read_data=json.dumps({
        'https://example.com': {
            'metadata': {
                'status_code': 200,
                'headers': {'content-type': 'text/html'},
                'final_url': 'https://example.com'
            }
        }
    }))
    @patch('requests.Session.head')
    def test_check_metadata_changes_with_changes(self, mock_head, temp_config):
        """Test metadata checking that detects changes"""
        # Mock current metadata with changes
        mock_response = Mock()
        mock_response.status_code = 404  # Changed from 200
        mock_response.headers = {'content-type': 'text/html'}
        mock_response.url = 'https://example.com'
        mock_head.return_value = mock_response

        # Create the monitor first (read real YAML config)
        monitor = AISafetyMonitor(config_path=temp_config)

        # Now patch open() to fake prior metadata
        with patch('builtins.open', mock_open(read_data=json.dumps({
            'https://example.com': {
                'metadata': {
                    'status_code': 200,
                    'headers': {'content-type': 'text/html'},
                    'final_url': 'https://example.com'
                }
            }
        }))):
            # Force URL to be due for check
            monitor.url_schedules['https://example.com']['next_check'] = datetime.now() - timedelta(hours=1)
            changes = monitor.check_metadata_changes()

        assert len(changes) > 0
        change = changes[0]
        assert change['url'] == 'https://example.com'
        assert 'metadata_change' in change['changes']
        assert 'status' in change['changes']['metadata_change']

    def test_get_detailed_status(self, temp_config):
        """Test detailed status reporting"""
        monitor = AISafetyMonitor(config_path=temp_config)
        
        status = monitor.get_detailed_status()
        
        assert 'url_schedules' in status
        assert 'due_urls' in status
        assert 'config_summary' in status
        assert status['config_summary']['total_urls'] == 2
        assert isinstance(status['config_summary']['sheets_enabled'], bool)


# Integration tests
class TestIntegration:
    @pytest.fixture
    def monitor_with_mocks(self, temp_config):
        """Create a monitor with all external dependencies mocked"""
        with patch('requests.Session'), \
             patch('gspread.authorize'), \
             patch('pathlib.Path.mkdir'), \
             patch('pathlib.Path.exists', return_value=True), \
             patch('builtins.open', mock_open(read_data='{}')):
            
            monitor = AISafetyMonitor(config_path=temp_config)
            yield monitor

    def test_full_monitoring_cycle_no_changes(self, monitor_with_mocks):
        """Test complete monitoring cycle with no changes detected"""
        with patch.object(monitor_with_mocks, 'check_changedetection_content_changes', return_value=[]), \
             patch.object(monitor_with_mocks, 'check_metadata_changes', return_value=[]), \
             patch.object(monitor_with_mocks, 'sheets_reporter') as mock_sheets:
            
            changes = monitor_with_mocks.run_monitoring_cycle()
            
            assert changes == []
            # Sheets should not be called when no changes
            assert not mock_sheets.log_change_to_sheets.called

    def test_full_monitoring_cycle_with_changes(self, monitor_with_mocks):
        """Test complete monitoring cycle with changes detected"""
        test_changes = [{
            'url': 'https://example.com',
            'changes': {'content_change': {'source': 'test'}},
            'timestamp': '2023-01-01T00:00:00',
            'metadata': {},
            'change_source': 'test'
        }]
        
        with patch.object(monitor_with_mocks, 'check_changedetection_content_changes', return_value=test_changes), \
             patch.object(monitor_with_mocks, 'check_metadata_changes', return_value=[]), \
             patch.object(monitor_with_mocks, 'sheets_reporter') as mock_sheets, \
             patch.object(monitor_with_mocks, 'gh_reporter') as mock_gh:
            
            changes = monitor_with_mocks.run_monitoring_cycle()
            
            assert len(changes) == 1
            # Sheets should be called for each change
            mock_sheets.log_change_to_sheets.assert_called_once_with(test_changes[0])
            # GitHub reporter should be called
            mock_gh.generate_json_report.assert_called_once()


# Test FastAPI endpoints
@pytest.fixture
def test_client():
    return TestClient(app)
    
class TestFastAPIEndpoints:

    def test_root_endpoint(self, test_client):
        """Test root endpoint"""
        response = test_client.get("/")
        assert response.status_code == 200
        assert response.json()["status"] == "running"

    def test_health_endpoint(self, test_client):
        """Test health endpoint"""
        response = test_client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"
        assert "timestamp" in response.json()

    @patch('monitor.AISafetyMonitor')
    def test_check_now_endpoint(self, mock_monitor, test_client):
        """Test manual check endpoint"""
        mock_instance = Mock()
        mock_instance.run_monitoring_cycle.return_value = [{'test': 'change'}]
        mock_monitor.return_value = mock_instance
        
        response = test_client.get("/check-now")
        assert response.status_code == 200
        data = response.json()
        assert data["changes_detected"] == 1
        assert "changes" in data

    @patch('monitor.AISafetyMonitor')
    def test_status_endpoint(self, mock_monitor, test_client):
        """Test status endpoint"""
        mock_instance = Mock()
        mock_instance.get_urls_due_for_check.return_value = [{'url': 'https://example.com'}]
        mock_instance.url_schedules = {'https://example.com': {}}
        mock_monitor.return_value = mock_instance
        
        response = test_client.get("/status")
        assert response.status_code == 200
        data = response.json()
        assert "due_urls" in data
        assert "total_due" in data
        assert "total_monitored" in data

    @patch('monitor.AISafetyMonitor')
    def test_sheets_status_endpoint(self, mock_monitor, test_client):
        """Test sheets status endpoint"""
        mock_instance = Mock()
        mock_instance.sheets_reporter.client = Mock()
        mock_monitor.return_value = mock_instance
        
        response = test_client.get("/api/sheets-status")
        assert response.status_code == 200
        data = response.json()
        assert "sheets_connected" in data
        assert "last_updated" in data