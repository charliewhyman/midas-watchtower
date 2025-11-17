"""Change detection functionality"""
from datetime import datetime
from typing import Dict, Any, List, Optional
import json
from pathlib import Path

from models import UrlMetadata, ChangeDetails, DetectedChange
import logging

logger = logging.getLogger(__name__)


class ChangeDetector:
    """Detects changes between URL metadata snapshots"""
    
    def __init__(self, history_file: Path):
        self.history_file = history_file
        self.history: Dict[str, Any] = self._load_history()
    
    def _load_history(self) -> Dict[str, Any]:
        """Load URL history from file"""
        try:
            if self.history_file.exists():
                with open(self.history_file, 'r') as f:
                    return json.load(f)
            return {}
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Failed to load history file, starting fresh: {e}")
            return {}
    
    def save_history(self) -> None:
        """Save URL history to file"""
        try:
            self.history_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.history_file, 'w') as f:
                json.dump(self.history, f, indent=2, default=str)
        except IOError as e:
            logger.error(f"Failed to save history file: {e}")
    
    def detect_metadata_changes(self, url: str, current_meta: UrlMetadata) -> List[ChangeDetails]:
        """Detect metadata changes between current and previous state"""
        changes = []
        
        if url not in self.history:
            # First time seeing this URL
            self.history[url] = {
                'metadata': current_meta.dict(),
                'first_seen': datetime.now().isoformat(),
                'last_checked': datetime.now().isoformat()
            }
            return changes
        
        previous_data = self.history[url]
        previous_meta = previous_data.get('metadata', {})
        
        # Check status code changes
        if (previous_meta.get('status_code') != current_meta.status_code and
            previous_meta.get('status_code') is not None):
            
            changes.append(ChangeDetails(
                change_type='status_change',
                source='direct_metadata',
                details={
                    'old_status': previous_meta.get('status_code'),
                    'new_status': current_meta.status_code
                },
                severity='high' if current_meta.status_code >= 400 else 'medium'
            ))
        
        # Check content-type changes
        old_content_type = previous_meta.get('headers', {}).get('content-type', '')
        new_content_type = current_meta.headers.get('content-type', '')
        
        if old_content_type and old_content_type != new_content_type:
            changes.append(ChangeDetails(
                change_type='content_type_change',
                source='direct_metadata',
                details={
                    'old_type': old_content_type,
                    'new_type': new_content_type
                },
                severity='medium'
            ))
        
        # Check redirect changes
        old_final_url = previous_meta.get('final_url', '')
        new_final_url = current_meta.final_url
        
        if old_final_url and old_final_url != new_final_url:
            changes.append(ChangeDetails(
                change_type='redirect_change',
                source='direct_metadata',
                details={
                    'old_url': old_final_url,
                    'new_url': new_final_url
                },
                severity='medium'
            ))
        
        # Update history with current metadata
        self.history[url].update({
            'metadata': current_meta.dict(),
            'last_checked': datetime.now().isoformat()
        })
        
        return changes
    
    def is_first_run(self) -> bool:
        """Check if this appears to be the first run"""
        if not self.history_file.exists():
            return True
        
        try:
            with open(self.history_file, 'r') as f:
                content = f.read().strip()
                return not content or content in ('{}', 'null')
        except Exception:
            return True