"""Change detection functionality with HTML metadata and policy monitoring"""
from datetime import datetime
from typing import Dict, Any, List, Optional
import json
from pathlib import Path
import re
from urllib.parse import urlparse, urlunparse

from models import UrlMetadata, HtmlMetadata, ChangeDetails, PolicyAlert
import logging

logger = logging.getLogger(__name__)


class ChangeDetector:
    """Detects changes between URL metadata snapshots with HTML and policy analysis"""
    
    def __init__(self, history_file: Path, settings: Optional[object] = None):
        """Initialize ChangeDetector.

        Args:
            history_file: Path to history JSON file.
            settings: Optional settings object (e.g. `MonitorSettings`) providing thresholds.
        """
        self.history_file = history_file
        self.history: Dict[str, Any] = self._load_history()

        # Load thresholds from settings if provided, otherwise use sensible defaults
        self.content_size_threshold = getattr(settings, 'content_size_threshold', 1000)
        self.word_count_threshold = getattr(settings, 'word_count_threshold', 50)
        self.word_count_major_threshold = getattr(settings, 'word_count_major_threshold', 100)
        self.policy_keyword_count_threshold = getattr(settings, 'policy_keyword_count_threshold', 2)
    
    def _load_history(self) -> Dict[str, Any]:
        """Load URL history from file"""
        try:
            if self.history_file.exists():
                with open(self.history_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            return {'metadata_history': {}, 'policy_alerts': []}
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Failed to load history file, starting fresh: {e}")
            return {'metadata_history': {}, 'policy_alerts': []}
    
    def save_history(self) -> None:
        """Save URL history to file"""
        try:
            self.history_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.history_file, 'w', encoding='utf-8') as f:
                json.dump(self.history, f, indent=2, ensure_ascii=False, default=str)
        except IOError as e:
            logger.error(f"Failed to save history file: {e}")
    
    def detect_metadata_changes(self, url: str, current_meta: UrlMetadata) -> List[ChangeDetails]:
        """Detect metadata changes between current and previous state including HTML"""
        changes = []
        
        # Get previous metadata
        previous_meta = self._get_previous_metadata(url)
        
        if not previous_meta:
            # First time seeing this URL
            self._save_current_metadata(url, current_meta)
            changes.append(ChangeDetails(
                change_type='first_detection',
                source='direct_metadata',
                details={'message': 'URL detected for the first time'},
                severity='low'
            ))
            return changes
        
        # Check HTTP-level changes
        changes.extend(self._detect_http_changes(url, current_meta, previous_meta))
        
        # Check HTML metadata changes if available
        if current_meta.html_metadata and previous_meta.get('html_metadata'):
            html_changes = self._detect_html_metadata_changes(
                url, current_meta.html_metadata, previous_meta['html_metadata']
            )
            changes.extend(html_changes)
        
        # Check policy-specific changes
        if current_meta.html_metadata and previous_meta.get('html_metadata'):
            policy_changes = self._detect_policy_changes(
                url, current_meta.html_metadata, previous_meta['html_metadata']
            )
            changes.extend(policy_changes)
        
        # Save current state
        self._save_current_metadata(url, current_meta)
        
        return changes
    
    def _get_previous_metadata(self, url: str) -> Optional[Dict]:
        """Get previous metadata for a URL"""
        history = self.history.get('metadata_history', {})

        # Direct match first
        if url in history:
            return history[url]

        # Try normalized key lookup
        norm_url = self._normalize_url(url)
        if norm_url in history:
            return history[norm_url]

        # Try common variants (http/https) and without/with www
        variants = self._generate_url_variants(url)
        for v in variants:
            if v in history:
                return history[v]

        # Fallback: try to match against stored final_url or canonical_url fields
        for entry in history.values():
            final = entry.get('final_url')
            canonical = None
            if entry.get('html_metadata'):
                canonical = entry['html_metadata'].get('canonical_url')

            try:
                if final and (final == url or self._normalize_url(final) == norm_url):
                    return entry
                if canonical and (canonical == url or self._normalize_url(canonical) == norm_url):
                    return entry
            except Exception:
                continue

        return None
    
    def _save_current_metadata(self, url: str, metadata: UrlMetadata):
        """Save current metadata to history"""
        if 'metadata_history' not in self.history:
            self.history['metadata_history'] = {}
        
        # Convert to serializable format
        serializable_meta = {
            'url': metadata.url,
            'timestamp': metadata.timestamp.isoformat(),
            'status_code': metadata.status_code,
            # Normalize headers to lowercase keys for consistent comparisons
            'headers': {k.lower(): v for k, v in (metadata.headers or {}).items()},
            'final_url': metadata.final_url,
            'content_length': metadata.content_length,
            'response_time': metadata.response_time,
            'error': metadata.error,
        }
        
        # Add HTML metadata if available
        if metadata.html_metadata:
            serializable_meta['html_metadata'] = {
                'title': metadata.html_metadata.title,
                'meta_description': metadata.html_metadata.meta_description,
                'canonical_url': metadata.html_metadata.canonical_url,
                'og_metadata': metadata.html_metadata.og_metadata,
                'twitter_metadata': metadata.html_metadata.twitter_metadata,
                'other_metadata': metadata.html_metadata.other_metadata,
                'structured_data': metadata.html_metadata.structured_data,
                'important_links': metadata.html_metadata.important_links,
                'content_analysis': metadata.html_metadata.content_analysis,
                'language': metadata.html_metadata.language,
                'charset': metadata.html_metadata.charset,
                'has_forms': metadata.html_metadata.has_forms,
                'has_comments': metadata.html_metadata.has_comments,
                'error': metadata.html_metadata.error,
            }
        
        # Use a normalized key to avoid duplicates due to minor URL form differences
        key_source = metadata.final_url or metadata.url
        try:
            key = self._normalize_url(key_source)
        except Exception:
            key = url

        self.history['metadata_history'][key] = serializable_meta

    def _normalize_url(self, url: str) -> str:
        """Normalize URLs for consistent history keys.

        Normalization rules:
        - Lowercase scheme and netloc
        - Remove default ports (80, 443)
        - Strip trailing slash
        - Remove fragments
        """
        if not url:
            return url

        parsed = urlparse(url)
        scheme = parsed.scheme.lower() if parsed.scheme else 'http'
        netloc = parsed.netloc.lower()

        # Remove default ports
        if ':' in netloc:
            host, port = netloc.rsplit(':', 1)
            if (scheme == 'http' and port == '80') or (scheme == 'https' and port == '443'):
                netloc = host

        path = parsed.path or ''
        # Strip trailing slash for normalization (but keep root '/')
        if path != '/' and path.endswith('/'):
            path = path[:-1]

        normalized = urlunparse((scheme, netloc, path, '', '', ''))
        return normalized

    def _generate_url_variants(self, url: str) -> List[str]:
        """Generate common URL variants to try when looking up history."""
        variants = set()
        try:
            parsed = urlparse(url)
            scheme = parsed.scheme or ''
            netloc = parsed.netloc or parsed.path  # fallback if scheme missing
            path = parsed.path if parsed.scheme else ''

            # Base normalized
            base = self._normalize_url(url)
            variants.add(base)

            # Add http/https versions
            if scheme != 'http':
                variants.add(self._normalize_url(urlunparse(('http', netloc, path, '', '', ''))))
            if scheme != 'https':
                variants.add(self._normalize_url(urlunparse(('https', netloc, path, '', '', ''))))

            # Toggle www
            if netloc.startswith('www.'):
                variants.add(self._normalize_url(urlunparse((scheme or 'http', netloc[4:], path, '', '', ''))))
            else:
                variants.add(self._normalize_url(urlunparse((scheme or 'http', f'www.{netloc}', path, '', '', ''))))

        except Exception:
            pass

        return list(variants)
    
    def _detect_http_changes(self, url: str, current: UrlMetadata, previous: Dict) -> List[ChangeDetails]:
        """Detect HTTP-level changes"""
        changes = []
        
        # Status code changes
        if current.status_code != previous.get('status_code'):
            changes.append(ChangeDetails(
                change_type='status_change',
                source='http_metadata',
                details={
                    'old_status': previous.get('status_code'),
                    'new_status': current.status_code
                },
                severity='high' if current.status_code and current.status_code >= 400 else 'medium'
            ))
        
        # Final URL changes (redirects)
        if current.final_url != previous.get('final_url'):
            changes.append(ChangeDetails(
                change_type='redirect_change',
                source='http_metadata',
                details={
                    'old_url': previous.get('final_url'),
                    'new_url': current.final_url
                },
                severity='medium'
            ))
        
        # Content length changes
        current_length = current.content_length or 0
        previous_length = previous.get('content_length', 0)
        if abs(current_length - previous_length) > self.content_size_threshold:  # Significant size change
            changes.append(ChangeDetails(
                change_type='content_size_change',
                source='http_metadata',
                details={
                    'old_size': previous_length,
                    'new_size': current_length,
                    'change_percent': abs(current_length - previous_length) / max(previous_length, 1) * 100
                },
                severity='medium'
            ))
        
        # Header changes
        header_changes = self._detect_header_changes(current.headers, previous.get('headers', {}))
        changes.extend(header_changes)
        
        return changes
    
    def _detect_header_changes(self, current_headers: Dict, previous_headers: Dict) -> List[ChangeDetails]:
        """Detect significant header changes"""
        changes = []
        important_headers = ['last-modified', 'etag', 'content-type', 'content-length', 'cache-control']
        # Normalize header dicts to lowercase keys for reliable lookup
        current_norm = {k.lower(): v for k, v in (current_headers or {}).items()}
        previous_norm = {k.lower(): v for k, v in (previous_headers or {}).items()}

        for header in important_headers:
            header_lower = header.lower()
            current_val = current_norm.get(header_lower)
            previous_val = previous_norm.get(header_lower)

            if current_val != previous_val:
                changes.append(ChangeDetails(
                    change_type='header_change',
                    source='http_metadata',
                    details={
                        'header': header,
                        'old_value': previous_val,
                        'new_value': current_val
                    },
                    severity='low' if header == 'last-modified' else 'medium'
                ))
        
        return changes
    
    def _detect_html_metadata_changes(self, url: str, current: HtmlMetadata, previous: Dict) -> List[ChangeDetails]:
        """Detect HTML metadata changes"""
        changes = []
        
        # Title changes
        if current.title != previous.get('title'):
            changes.append(ChangeDetails(
                change_type='title_change',
                source='html_metadata',
                details={
                    'old_title': previous.get('title'),
                    'new_title': current.title
                },
                severity='high'
            ))
        
        # Meta description changes
        if current.meta_description != previous.get('meta_description'):
            changes.append(ChangeDetails(
                change_type='meta_description_change',
                source='html_metadata',
                details={
                    'old_description': previous.get('meta_description'),
                    'new_description': current.meta_description
                },
                severity='medium'
            ))
        
        # Canonical URL changes
        if current.canonical_url != previous.get('canonical_url'):
            changes.append(ChangeDetails(
                change_type='canonical_url_change',
                source='html_metadata',
                details={
                    'old_canonical': previous.get('canonical_url'),
                    'new_canonical': current.canonical_url
                },
                severity='medium'
            ))
        
        # OpenGraph changes
        og_changes = self._detect_og_changes(current.og_metadata, previous.get('og_metadata', {}))
        changes.extend(og_changes)
        
        # Content analysis changes
        content_changes = self._detect_content_changes(
            current.content_analysis, 
            previous.get('content_analysis', {})
        )
        changes.extend(content_changes)
        
        return changes
    
    def _detect_og_changes(self, current_og: Dict, previous_og: Dict) -> List[ChangeDetails]:
        """Detect OpenGraph metadata changes"""
        changes = []
        important_og_fields = ['title', 'description', 'image', 'url']
        
        for field in important_og_fields:
            if current_og.get(field) != previous_og.get(field):
                changes.append(ChangeDetails(
                    change_type='opengraph_change',
                    source='html_metadata',
                    details={
                        'field': field,
                        'old_value': previous_og.get(field),
                        'new_value': current_og.get(field)
                    },
                    severity='medium'
                ))
        
        return changes
    
    def _detect_content_changes(self, current_content: Dict, previous_content: Dict) -> List[ChangeDetails]:
        """Detect content analysis changes"""
        changes = []
        
        # Word count changes
        current_words = current_content.get('word_count', 0)
        previous_words = previous_content.get('word_count', 0)
        if abs(current_words - previous_words) > self.word_count_threshold:  # Significant content change
            changes.append(ChangeDetails(
                change_type='word_count_change',
                source='content_analysis',
                details={
                    'old_count': previous_words,
                    'new_count': current_words,
                    'change_percent': abs(current_words - previous_words) / max(previous_words, 1) * 100
                },
                severity='medium' if abs(current_words - previous_words) > self.word_count_major_threshold else 'low'
            ))
        
        # Heading structure changes
        current_headings = current_content.get('heading_structure', {})
        previous_headings = previous_content.get('heading_structure', {})
        if current_headings != previous_headings:
            changes.append(ChangeDetails(
                change_type='heading_structure_change',
                source='content_analysis',
                details={
                    'old_structure': previous_headings,
                    'new_structure': current_headings
                },
                severity='low'
            ))
        
        return changes
    
    def _detect_policy_changes(self, url: str, current: HtmlMetadata, previous: Dict) -> List[ChangeDetails]:
        """Detect policy-relevant changes"""
        changes = []
        
        # Version information changes
        current_version = current.other_metadata.get('version')
        previous_version = previous.get('other_metadata', {}).get('version')
        if current_version != previous_version:
            changes.append(ChangeDetails(
                change_type='version_change',
                source='policy_analysis',
                details={
                    'old_version': previous_version,
                    'new_version': current_version
                },
                severity='high',
                policy_alert=True
            ))
        
        # Significant content changes in policy-related sections
        content_changes = self._detect_policy_content_changes(
            current.content_analysis,
            previous.get('content_analysis', {})
        )
        changes.extend(content_changes)
        
        # Keyword presence changes
        keyword_changes = self._detect_keyword_changes(
            current.content_analysis,
            previous.get('content_analysis', {})
        )
        changes.extend(keyword_changes)
        
        return changes
    
    def _detect_policy_content_changes(self, current_content: Dict, previous_content: Dict) -> List[ChangeDetails]:
        """Detect policy-specific content changes"""
        changes = []
        
        # Check for significant changes in policy keyword counts
        policy_keywords = ['privacy', 'terms', 'liability', 'termination', 'rights', 'governance']
        
        for keyword in policy_keywords:
            current_count = current_content.get(f'{keyword}_keyword_count', 0)
            previous_count = previous_content.get(f'{keyword}_keyword_count', 0)
            
            if abs(current_count - previous_count) > self.policy_keyword_count_threshold:  # Significant keyword count change
                changes.append(ChangeDetails(
                    change_type='policy_keyword_change',
                    source='policy_analysis',
                    details={
                        'keyword': keyword,
                        'old_count': previous_count,
                        'new_count': current_count
                    },
                    severity='medium',
                    policy_alert=True
                ))
        
        # Check for version indicator changes
        current_versions = current_content.get('version_indicators', [])
        previous_versions = previous_content.get('version_indicators', [])
        if set(current_versions) != set(previous_versions):
            changes.append(ChangeDetails(
                change_type='version_indicator_change',
                source='policy_analysis',
                details={
                    'old_versions': previous_versions,
                    'new_versions': current_versions
                },
                severity='high',
                policy_alert=True
            ))
        
        return changes
    
    def _detect_keyword_changes(self, current_content: Dict, previous_content: Dict) -> List[ChangeDetails]:
        """Detect keyword presence/absence changes"""
        changes = []
        
        # Check if legal language presence changed
        current_legal = current_content.get('has_legal_language', False)
        previous_legal = previous_content.get('has_legal_language', False)
        
        if current_legal != previous_legal:
            changes.append(ChangeDetails(
                change_type='legal_language_change',
                source='policy_analysis',
                details={
                    'old_state': previous_legal,
                    'new_state': current_legal
                },
                severity='medium',
                policy_alert=True
            ))
        
        return changes
    
    def detect_stealth_updates(self, current_meta: UrlMetadata, previous_meta: Dict) -> List[PolicyAlert]:
        """Detect potential stealth policy updates"""
        alerts = []
        
        if not current_meta.html_metadata or not previous_meta.get('html_metadata'):
            return alerts
        
        current_html = current_meta.html_metadata
        previous_html = previous_meta['html_metadata']
        
        # 1. Content changed but no version update
        current_words = current_html.content_analysis.get('word_count', 0)
        previous_words = previous_html.get('content_analysis', {}).get('word_count', 0)
        
        if abs(current_words - previous_words) > 100:  # Significant content change
            current_versions = current_html.content_analysis.get('version_indicators', [])
            previous_versions = previous_html.get('content_analysis', {}).get('version_indicators', [])
            
            if set(current_versions) == set(previous_versions):
                alerts.append(PolicyAlert(
                    alert_type='STEALTH_CONTENT_CHANGE',
                    severity='HIGH',
                    message='Significant content changes detected without version update',
                    details={
                        'word_count_change': current_words - previous_words,
                        'current_versions': current_versions,
                        'previous_versions': previous_versions
                    },
                    url=current_meta.url,
                    timestamp=datetime.now()
                ))
        
        # 2. Last-modified header changed but minor content changes
        current_headers = {k.lower(): v for k, v in (current_meta.headers or {}).items()}
        previous_headers = {k.lower(): v for k, v in (previous_meta.get('headers', {}) or {}).items()}

        current_last_modified = current_headers.get('last-modified')
        previous_last_modified = previous_headers.get('last-modified')

        if (current_last_modified != previous_last_modified and
            abs(current_words - previous_words) < 50):  # Minor content change
            
            alerts.append(PolicyAlert(
                alert_type='STEALTH_LAST_MODIFIED_UPDATE',
                severity='MEDIUM',
                message='Last-Modified header changed with minimal content changes',
                details={
                    'last_modified_change': f'{previous_last_modified} -> {current_last_modified}',
                    'word_count_change': current_words - previous_words
                },
                url=current_meta.url,
                timestamp=datetime.now()
            ))
        
        return alerts
    
    def is_first_run(self) -> bool:
        """Check if this appears to be the first run"""
        if not self.history_file.exists():
            return True
        
        try:
            with open(self.history_file, 'r', encoding='utf-8') as f:
                content = f.read().strip()
                return (not content or content in ('{}', 'null', '{"metadata_history": {}}'))
        except Exception:
            return True
    
    def get_url_history(self, url: str) -> Optional[Dict]:
        """Get complete history for a URL"""
        return self.history.get('metadata_history', {}).get(url)
    
    def get_all_tracked_urls(self) -> List[str]:
        """Get list of all tracked URLs"""
        return list(self.history.get('metadata_history', {}).keys())