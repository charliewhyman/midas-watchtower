"""HTTP monitoring functionality with HTML metadata parsing"""
import time
import json
import re
from datetime import datetime
from typing import Optional, Dict, Any, List
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup

from config import AppConfig
from models import UrlMetadata, HtmlMetadata
import logging

logger = logging.getLogger(__name__)


class HttpMonitor:
    """Handles HTTP requests and metadata extraction with HTML parsing"""
    
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
            'Accept-Encoding': 'gzip, deflate',
        })
        
        return session
    
    def get_url_metadata(self, url: str) -> UrlMetadata:
        """
        Get comprehensive metadata for a URL including HTML content.
        Uses HEAD requests first for basic checks, falls back to GET for HTML parsing.
        """
        start_time = time.monotonic()
        
        try:
            # First, try HEAD request for basic HTTP metadata
            head_response = self._try_head_request(url)
            basic_metadata = self._extract_basic_metadata(url, head_response)
            
            # Always do GET request for HTML content parsing
            logger.debug(f"Fetching HTML content for {url}")
            html_response = self.session.get(
                url, 
                allow_redirects=True, 
                timeout=self.config.settings.request_timeout
            )
            
            # Parse HTML metadata
            html_metadata = self._parse_html_metadata(url, html_response)
            
            # Combine basic and HTML metadata
            metadata = UrlMetadata(
                url=url,
                timestamp=datetime.now(),
                status_code=html_response.status_code,
                headers=dict(html_response.headers),
                final_url=str(html_response.url),
                html_metadata=html_metadata,
                content_length=len(html_response.content) if html_response.content else 0,
                response_time=time.monotonic() - start_time
            )
            
            duration = time.monotonic() - start_time
            logger.debug(f"Full metadata collected for {url} in {duration:.2f}s")
            
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
    
    def _extract_basic_metadata(self, url: str, response: Optional[requests.Response]) -> Dict[str, Any]:
        """Extract basic HTTP metadata from HEAD response"""
        if response is None:
            return {}
        
        # Normalize header keys to lowercase for consistent comparisons
        headers = {k.lower(): v for k, v in dict(response.headers).items()}

        return {
            'status_code': response.status_code,
            'headers': headers,
            'final_url': str(response.url),
        }
    
    def _parse_html_metadata(self, url: str, response: requests.Response) -> HtmlMetadata:
        """Parse HTML content and extract comprehensive metadata"""
        if response.status_code != 200:
            return HtmlMetadata(
                url=url,
                error=f"HTTP {response.status_code} - Cannot parse HTML"
            )
        
        # Check if content is HTML
        content_type = response.headers.get('content-type', '').lower()
        if 'text/html' not in content_type:
            return HtmlMetadata(
                url=url,
                error=f"Non-HTML content type: {content_type}"
            )
        
        try:
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Extract basic HTML metadata
            title = self._extract_title(soup)
            meta_description = self._extract_meta_description(soup)
            canonical_url = self._extract_canonical_url(soup)
            
            # Extract OpenGraph metadata
            og_metadata = self._extract_opengraph_metadata(soup)
            
            # Extract Twitter Card metadata
            twitter_metadata = self._extract_twitter_metadata(soup)
            
            # Extract other important meta tags
            other_metadata = self._extract_other_metadata(soup)
            
            # Extract structured data (JSON-LD, Microdata)
            structured_data = self._extract_structured_data(soup)
            
            # Extract important links
            links = self._extract_important_links(soup, str(response.url))
            
            # Content analysis
            content_analysis = self._analyze_content(soup)
            
            # Policy-specific content analysis
            policy_content = self._analyze_policy_content(soup)
            content_analysis.update(policy_content)
            
            return HtmlMetadata(
                url=url,
                title=title,
                meta_description=meta_description,
                canonical_url=canonical_url,
                og_metadata=og_metadata,
                twitter_metadata=twitter_metadata,
                other_metadata=other_metadata,
                structured_data=structured_data,
                important_links=links,
                content_analysis=content_analysis,
                language=self._detect_language(soup),
                charset=self._detect_charset(soup, response),
                has_forms=bool(soup.find('form')),
                has_comments=self._has_comments(soup),
            )
            
        except Exception as e:
            logger.error(f"Error parsing HTML for {url}: {e}")
            return HtmlMetadata(
                url=url,
                error=f"HTML parsing error: {e}"
            )
    
    def _extract_title(self, soup: BeautifulSoup) -> Optional[str]:
        """Extract page title"""
        title_tag = soup.find('title')
        return title_tag.get_text().strip() if title_tag else None
    
    def _extract_meta_description(self, soup: BeautifulSoup) -> Optional[str]:
        """Extract meta description"""
        meta_desc = soup.find('meta', attrs={'name': 'description'})
        return meta_desc.get('content', '').strip() if meta_desc else None
    
    def _extract_canonical_url(self, soup: BeautifulSoup) -> Optional[str]:
        """Extract canonical URL"""
        canonical = soup.find('link', attrs={'rel': 'canonical'})
        return canonical.get('href') if canonical else None
    
    def _extract_opengraph_metadata(self, soup: BeautifulSoup) -> Dict[str, str]:
        """Extract OpenGraph metadata"""
        og_metadata = {}
        og_tags = soup.find_all('meta', attrs={'property': re.compile(r'^og:', re.I)})
        
        for tag in og_tags:
            property_name = tag.get('property', '').lower()
            content = tag.get('content', '')
            if property_name and content:
                # Remove 'og:' prefix and use as key
                key = property_name.replace('og:', '')
                og_metadata[key] = content
        
        return og_metadata
    
    def _extract_twitter_metadata(self, soup: BeautifulSoup) -> Dict[str, str]:
        """Extract Twitter Card metadata"""
        twitter_metadata = {}
        twitter_tags = soup.find_all('meta', attrs={'name': re.compile(r'^twitter:', re.I)})
        
        for tag in twitter_tags:
            name = tag.get('name', '').lower()
            content = tag.get('content', '')
            if name and content:
                # Remove 'twitter:' prefix and use as key
                key = name.replace('twitter:', '')
                twitter_metadata[key] = content
        
        return twitter_metadata
    
    def _extract_other_metadata(self, soup: BeautifulSoup) -> Dict[str, str]:
        """Extract other important meta tags"""
        other_meta = {}
        
        # Common meta tags to extract
        meta_fields = [
            'keywords', 'author', 'viewport', 'robots', 'generator',
            'theme-color', 'msapplication-TileColor', 'application-name'
        ]
        
        for field in meta_fields:
            meta_tag = soup.find('meta', attrs={'name': field})
            if meta_tag and meta_tag.get('content'):
                other_meta[field] = meta_tag.get('content')
        
        # Also check for http-equiv meta tags
        http_equiv_tags = soup.find_all('meta', attrs={'http-equiv': True})
        for tag in http_equiv_tags:
            equiv = tag.get('http-equiv', '').lower()
            content = tag.get('content', '')
            if equiv and content:
                other_meta[f"http_equiv_{equiv}"] = content
        
        return other_meta
    
    def _extract_structured_data(self, soup: BeautifulSoup) -> Dict[str, Any]:
        """Extract structured data (JSON-LD, Microdata)"""
        structured_data = {
            'json_ld': [],
            'microdata': {}
        }
        
        # Extract JSON-LD data
        json_ld_scripts = soup.find_all('script', type='application/ld+json')
        for script in json_ld_scripts:
            try:
                if script.string:
                    data = json.loads(script.string)
                    structured_data['json_ld'].append(data)
            except (json.JSONDecodeError, Exception) as e:
                logger.debug(f"Failed to parse JSON-LD data: {e}")
        
        # Basic microdata extraction
        microdata_items = soup.find_all(attrs={'itemtype': True})
        if microdata_items:
            structured_data['microdata']['item_count'] = len(microdata_items)
            # Extract first few item types as sample
            item_types = list(set(item.get('itemtype') for item in microdata_items[:5] if item.get('itemtype')))
            structured_data['microdata']['sample_types'] = item_types
        
        return structured_data
    
    def _extract_important_links(self, soup: BeautifulSoup, base_url: str) -> Dict[str, list]:
        """Extract important links from the page"""
        links = {
            'internal': [],
            'external': [],
            'social': []
        }
        
        all_links = soup.find_all('a', href=True)
        
        # Extract domain from base_url for internal link detection
        try:
            from urllib.parse import urlparse
            base_domain = urlparse(base_url).netloc
        except Exception:
            base_domain = None
        
        social_domains = ['facebook.com', 'twitter.com', 'linkedin.com', 'instagram.com', 'youtube.com']
        
        for link in all_links:
            href = link.get('href', '')
            text = link.get_text(strip=True)
            
            if not href or href.startswith(('javascript:', 'mailto:', 'tel:')):
                continue
                
            link_info = {
                'url': href,
                'text': text[:100] if text else '',  # Limit text length
                'title': link.get('title', '')[:100]
            }
            
            # Categorize links
            if href.startswith('/') or (base_domain and base_domain in href):
                links['internal'].append(link_info)
            elif any(domain in href for domain in social_domains):
                links['social'].append(link_info)
            else:
                links['external'].append(link_info)
        
        return links
    
    def _analyze_content(self, soup: BeautifulSoup) -> Dict[str, Any]:
        """Basic content analysis"""
        # Remove script and style elements
        for element in soup(['script', 'style', 'nav', 'footer', 'header']):
            element.decompose()
        
        text_content = soup.get_text()
        # Clean up whitespace
        text_content = ' '.join(text_content.split())
        words = text_content.split()
        
        return {
            'word_count': len(words),
            'text_preview': text_content[:500] + '...' if len(text_content) > 500 else text_content,
            'heading_structure': self._analyze_headings(soup),
            'image_count': len(soup.find_all('img')),
            'has_main_content': bool(soup.find('main') or soup.find('article') or soup.find(class_=re.compile(r'content|main', re.I))),
            'paragraph_count': len(soup.find_all('p')),
            'list_count': len(soup.find_all(['ul', 'ol'])),
        }
    
    def _analyze_policy_content(self, soup: BeautifulSoup) -> Dict[str, Any]:
        """Analyze content for policy-specific indicators"""
        text_content = soup.get_text().lower()
        
        policy_keywords = {
            'privacy': ['privacy', 'data protection', 'personal data', 'gdpr', 'ccpa'],
            'terms': ['terms', 'conditions', 'agreement', 'contract'],
            'liability': ['liability', 'warranty', 'guarantee', 'responsible', 'damages'],
            'termination': ['terminate', 'suspend', 'close account', 'cancel', 'breach'],
            'rights': ['rights', 'permission', 'license', 'intellectual property', 'copyright'],
            'governance': ['governance', 'compliance', 'regulation', 'policy', 'guidelines']
        }
        
        keyword_counts = {}
        for category, keywords in policy_keywords.items():
            count = 0
            for keyword in keywords:
                count += text_content.count(keyword)
            keyword_counts[f"{category}_keyword_count"] = count
        
        # Look for version indicators
        version_indicators = self._find_version_indicators(soup)
        
        # Look for date indicators
        date_indicators = self._find_date_indicators(soup)
        
        return {
            **keyword_counts,
            'version_indicators': version_indicators,
            'date_indicators': date_indicators,
            'has_legal_language': any(count > 0 for count in keyword_counts.values()),
        }
    
    def _find_version_indicators(self, soup: BeautifulSoup) -> List[str]:
        """Find version numbers and indicators in the content"""
        version_patterns = [
            r'version\s*:?\s*([\d\.]+)',
            r'v\.?\s*(\d+\.\d+)',
            r'revision\s*:?\s*([\d\.]+)',
            r'ver\.?\s*(\d+)',
        ]
        
        text_content = soup.get_text()
        versions = []
        
        for pattern in version_patterns:
            matches = re.findall(pattern, text_content, re.IGNORECASE)
            versions.extend(matches)
        
        return versions
    
    def _find_date_indicators(self, soup: BeautifulSoup) -> List[str]:
        """Find date information in the content"""
        date_patterns = [
            r'last\s+(?:updated|modified|revised)\s*:?\s*([^<\.]+)',
            r'updated\s+on\s*:?\s*([^<\.]+)',
            r'effective\s+as\s+of\s*:?\s*([^<\.]+)',
            r'revision\s+date\s*:?\s*([^<\.]+)',
            r'date\s*:?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
        ]
        
        text_content = soup.get_text()
        dates = []
        
        for pattern in date_patterns:
            matches = re.findall(pattern, text_content, re.IGNORECASE)
            dates.extend(matches)
        
        return dates
    
    def _analyze_headings(self, soup: BeautifulSoup) -> Dict[str, int]:
        """Analyze heading structure"""
        headings = {}
        for level in range(1, 7):
            h_tags = soup.find_all(f'h{level}')
            headings[f'h{level}'] = len(h_tags)
        return headings
    
    def _detect_language(self, soup: BeautifulSoup) -> Optional[str]:
        """Detect page language"""
        html_tag = soup.find('html')
        return html_tag.get('lang') if html_tag and html_tag.get('lang') else None
    
    def _detect_charset(self, soup: BeautifulSoup, response: requests.Response) -> Optional[str]:
        """Detect character encoding"""
        # From meta tag
        meta_charset = soup.find('meta', attrs={'charset': True})
        if meta_charset:
            return meta_charset.get('charset')
        
        # From content-type meta tag
        meta_content_type = soup.find('meta', attrs={'http-equiv': re.compile('content-type', re.I)})
        if meta_content_type and meta_content_type.get('content'):
            content_type = meta_content_type.get('content')
            if 'charset=' in content_type.lower():
                return content_type.split('charset=')[1].split(';')[0].strip()
        
        # From response headers
        content_type_header = response.headers.get('content-type', '')
        if 'charset=' in content_type_header.lower():
            return content_type_header.split('charset=')[1].split(';')[0].strip()
        
        return None
    
    def _has_comments(self, soup: BeautifulSoup) -> bool:
        """Check if the page has HTML comments"""
        comments = soup.find_all(string=lambda text: isinstance(text, str) and '<!--' in text and '-->' in text)
        return len(comments) > 0
    
    def _try_head_request(self, url: str) -> Optional[requests.Response]:
        """Attempt HEAD request, return None if not allowed"""
        try:
            response = self.session.head(
                url, 
                allow_redirects=True, 
                timeout=self.config.settings.request_timeout
            )
            return response
        except requests.RequestException as e:
            logger.debug(f"HEAD request failed for {url}, will try GET: {e}")
            return None
    
    def close(self):
        """Close the HTTP session cleanly."""
        try:
            if self.session:
                self.session.close()
                logger.info("HTTP session closed")
        except Exception as e:
            logger.exception(f"Error closing HTTP session: {e}")