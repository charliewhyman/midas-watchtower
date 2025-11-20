"""HTTP monitoring functionality"""
import time
from datetime import datetime
from typing import Optional
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import AppConfig
from models import UrlMetadata
import logging

logger = logging.getLogger(__name__)


class HttpMonitor:
    """Handles HTTP requests and metadata extraction"""
    
    def __init__(self, config: AppConfig):
        self.config = config
        self.session = self._create_session()
    
    def _create_session(self) -> requests.Session:
        """Create configured requests session with retry strategy"""
        session = requests.Session()
        
        # Configure retry strategy
        retry_strategy = Retry(
            total=self.config.settings.max_retries,
            backoff_factor=0.5,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        
        # Set default headers
        session.headers.update({
            'User-Agent': 'AI-Safety-Monitor/1.0 (+https://github.com/org/ai-safety-monitor)',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
        })
        
        # Set default timeout
        session.request = lambda method, url, **kwargs: requests.Session.request(
            self.session, method, url, timeout=self.config.settings.request_timeout, **kwargs
        )
        
        return session
    
    def get_url_metadata(self, url: str) -> UrlMetadata:
        """
        Get comprehensive metadata for a URL.
        Uses HEAD requests first, falls back to GET if necessary.
        """
        start_time = time.time()
        
        try:
            # Try HEAD request first (more efficient)
            response = self._try_head_request(url)
            
            # If HEAD fails or is not allowed, try GET
            if response is None or response.status_code == 405:
                response = self.session.get(url, allow_redirects=True, stream=True)
                response.close()  # Don't download content, just headers
            
            metadata = UrlMetadata(
                url=url,
                timestamp=datetime.now(),
                status_code=response.status_code,
                headers=dict(response.headers),
                final_url=str(response.url),
            )
            
            duration = time.time() - start_time
            logger.debug(f"Metadata collected for {url} in {duration:.2f}s")
            
            return metadata
            
        except requests.RequestException as e:
            logger.warning(f"Request failed for {url}: {e}")
            return UrlMetadata(
                url=url,
                timestamp=datetime.now(),
                error=str(e),
                final_url=url
            )
        except Exception as e:
            logger.error(f"Unexpected error checking {url}: {e}")
            return UrlMetadata(
                url=url,
                timestamp=datetime.now(),
                error=f"Unexpected error: {e}",
                final_url=url
            )
    
    def _try_head_request(self, url: str) -> Optional[requests.Response]:
        """Attempt HEAD request, return None if not allowed"""
        try:
            response = self.session.head(url, allow_redirects=True)
            return response
        except requests.RequestException as e:
            logger.debug(f"HEAD request failed for {url}, will try GET: {e}")
            return None