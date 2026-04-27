# dashboard/models.py
from django.db.models import (
    Model, TextField, DateTimeField, CharField, URLField,
    FloatField, BooleanField, IntegerField, ForeignKey, SET_NULL
)
from django.utils.safestring import mark_safe
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import re
from django.db import models 


class Journalist(Model):
    name = CharField(max_length=255, unique=True)
    city = CharField(max_length=100, blank=True, null=True)
    country_based = CharField(max_length=100, blank=True, null=True)
    nationality = CharField(max_length=100, blank=True, null=True)
    linkedin_profile = URLField(blank=True, null=True)
    facebook_profile = URLField(blank=True, null=True)
    x_profile = URLField(blank=True, null=True)
    instagram_profile = URLField(blank=True, null=True)
    

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['name']

class MediaOutlet(Model):
    name = models.CharField(max_length=255, unique=True)
    parent_organisation = CharField(max_length=255, blank=True, null=True)
    website = URLField(blank=True, null=True)
    country = CharField(max_length=100, blank=True, null=True)
    city = CharField(max_length=100, blank=True, null=True)
    content_sharing_agreements = TextField(blank=True, null=True)
    linkedin_profile = URLField(blank=True, null=True)
    facebook_profile = URLField(blank=True, null=True)
    x_profile = URLField(blank=True, null=True)
    instagram_profile = URLField(blank=True, null=True)
    

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['name']

class VulnerabilityIndex(models.Model):
    actor = models.CharField(max_length=50)
    country = models.CharField(max_length=50)
    intent = models.CharField(max_length=50)
    final_risk = models.FloatField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('actor', 'country', 'intent')
        ordering = ['actor', 'country', 'intent']
    
        
class MediaNarrative(Model):
    article_text = TextField()
    posting_time = DateTimeField(null=True, blank=True)
    media_outlet = CharField(max_length=255, blank=True, null=True)
    inferred_actor = CharField(max_length=255, blank=True, null=True)
    strategic_intent = CharField(max_length=255, blank=True, null=True)
    sector = CharField(max_length=255, blank=True, null=True)
    tone = CharField(max_length=255, blank=True, null=True)
    target_country = CharField(max_length=255, blank=True, null=True)
    url = URLField(max_length=500, blank=True, null=True)
    confidence = FloatField(null=True, blank=True)
    prediction_source = models.CharField(
        max_length=50, blank=True, null=True,
        help_text="Source of the strategic intent prediction: model, llm, ensemble_matched, model_only, llm_only, error"
    )
    lang_detect = CharField(max_length=10, blank=True, null=True)
    use_afrolm = BooleanField(default=False)
    llm_strat = CharField(max_length=255, blank=True, null=True)
    llm_strat_conf = FloatField(blank=True, null=True)
    llm_strat_notes = TextField(blank=True, null=True)
    pseudo_kept = BooleanField(default=False)
    pseudo_weight = FloatField(default=0.0)
    llm_strat_id = IntegerField(blank=True, null=True)
    strategic_intent_id = IntegerField(blank=True, null=True)
    author = CharField(max_length=255, blank=True, null=True, default="Unknown")
    
    journalist_fk = ForeignKey('Journalist', on_delete=SET_NULL, null=True, blank=True, related_name='articles')
    media_outlet_fk = ForeignKey('MediaOutlet', on_delete=SET_NULL, null=True, blank=True, related_name='articles')
    ml_processed_at = models.DateTimeField(null=True, blank=True)
    is_anchor = models.BooleanField(default=False)
    true_label = CharField(max_length=255, blank=True, null=True,
    help_text="Human-verified ground truth strategic intent for anchor articles")

    class Meta:
        ordering = ['-posting_time']
        verbose_name = "Media Narrative"
        verbose_name_plural = "Media Narratives"

    def __str__(self):
        outlet = self.media_outlet_fk.name if self.media_outlet_fk else self.media_outlet or "Unknown"
        date = self.posting_time.strftime("%Y-%m-%d") if self.posting_time else "No date"
        return f"{outlet} - {date}"
        
    #for media tab
    @property
    def narrative_summary(self):
        """
        Returns a clean summary. Priority: 
        1. LLM Notes 
        2. LLM Strategy 
        3. Strategic Intent 
        4. Fallback to truncated text
        """
        if self.llm_strat_notes:
            return self.llm_strat_notes
        if self.llm_strat:
            return self.llm_strat
        if self.strategic_intent:
            return self.strategic_intent
        return self.article_text[:100] + "..." if self.article_text else "No summary available"

    # IMPROVED: Fetch real article image from URL with media outlet logo fallback
    def get_article_image(self):
        """
        Returns the main article image URL with multiple fallback strategies
        Falls back to media outlet logo if no article image found
        Returns None if no image found or error
        """
        if not self.url:
            return None

        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
            }

            # Try to get the page with timeout
            response = requests.get(self.url, headers=headers, timeout=10, verify=False)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, 'html.parser')

            # Strategy 1: Open Graph image (highest priority)
            og_image = soup.find("meta", property="og:image") or soup.find("meta", attrs={"property": "og:image"})
            if og_image and og_image.get("content"):
                img_url = og_image["content"].strip()
                if img_url:
                    img_url = self._normalize_url(img_url, response.url)
                    if self._is_valid_image_url(img_url):
                        return img_url

            # Strategy 2: Twitter card image
            twitter_image = soup.find("meta", attrs={"name": "twitter:image"}) or soup.find("meta", attrs={"property": "twitter:image"})
            if twitter_image and twitter_image.get("content"):
                img_url = twitter_image["content"].strip()
                if img_url:
                    img_url = self._normalize_url(img_url, response.url)
                    if self._is_valid_image_url(img_url):
                        return img_url

            # Strategy 3: Article header/main image (higher priority)
            # Look for images in article tags, figure tags, or main content areas
            article_selectors = ['article', 'main', '[role="main"]', '.article-body', '.post-content', '.entry-content', '#content', '.content']
            for selector in article_selectors:
                article_elem = soup.select_one(selector)
                if article_elem:
                    # Look for images within the article content
                    img_tags = article_elem.find_all('img', src=True)
                    for img in img_tags:
                        img_url = img.get('src', '').strip()
                        if img_url and self._is_meaningful_image(img_url):
                            img_url = self._normalize_url(img_url, response.url)
                            if self._is_valid_image_url(img_url):
                                return img_url

            # Strategy 4: Large images on the page (not in nav/ads)
            all_images = soup.find_all('img', src=True)
            for img in all_images:
                img_url = img.get('src', '').strip()
                parent_classes = ' '.join(img.find_parents()[-1].get('class', []) if img.find_parents() else [])
                
                # Skip navigation, sidebar, ad images
                if any(skip_class in parent_classes.lower() for skip_class in ['nav', 'sidebar', 'ad', 'banner', 'logo', 'menu']):
                    continue
                
                if img_url and self._is_meaningful_image(img_url):
                    img_url = self._normalize_url(img_url, response.url)
                    if self._is_valid_image_url(img_url):
                        return img_url

            # Strategy 5: Image from JSON-LD schema (structured data)
            json_scripts = soup.find_all('script', type='application/ld+json')
            for script in json_scripts:
                try:
                    import json
                    data = json.loads(script.string)
                    if isinstance(data, list):
                        for item in data:
                            if isinstance(item, dict) and 'image' in item:
                                img_url = item['image']
                                if isinstance(img_url, dict) and 'url' in img_url:
                                    img_url = img_url['url']
                                if img_url:
                                    img_url = self._normalize_url(img_url, response.url)
                                    if self._is_valid_image_url(img_url):
                                        return img_url
                    elif isinstance(data, dict) and 'image' in data:
                        img_url = data['image']
                        if isinstance(img_url, dict) and 'url' in img_url:
                            img_url = img_url['url']
                        if img_url:
                            img_url = self._normalize_url(img_url, response.url)
                            if self._is_valid_image_url(img_url):
                                return img_url
                except:
                    continue

            # FALLBACK: Try to get media outlet logo from MediaOutlet
            if self.media_outlet_fk and self.media_outlet_fk.website:
                logo_url = self._get_media_outlet_logo()
                if logo_url:
                    return logo_url

            return None
        except Exception as e:
            # Log the error for debugging but don't crash
            print(f"Error getting article image from {self.url}: {e}")
            # Even if extraction fails, try media outlet logo as last resort
            if self.media_outlet_fk and self.media_outlet_fk.website:
                try:
                    return self._get_media_outlet_logo()
                except:
                    pass
            return None

    def _get_media_outlet_logo(self):
        """Try to get logo from media outlet website"""
        if not self.media_outlet_fk or not self.media_outlet_fk.website:
            return None

        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            }

            response = requests.get(self.media_outlet_fk.website, headers=headers, timeout=8, verify=False)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, 'html.parser')

            # Try various common logo selectors
            logo_selectors = [
                'link[rel="apple-touch-icon"]',
                'link[rel="icon"]',
                'link[rel="shortcut icon"]',
                'img[src*="logo"]',
                'img[alt*="logo" i]',
                'img[title*="logo" i]',
                '.logo img',
                '#logo img',
                '[class*="logo"] img',
                '[id*="logo"] img',
                'header img',
                '.site-logo img'
            ]

            for selector in logo_selectors:
                logo_img = soup.select_one(selector)
                if logo_img:
                    logo_src = logo_img.get('src') or logo_img.get('href')
                    if logo_src:
                        logo_url = self._normalize_url(logo_src, response.url)
                        if self._is_valid_image_url(logo_url):
                            return logo_url

            # If no logo found, try favicon
            favicon_link = soup.find("link", rel="icon") or soup.find("link", rel="shortcut icon")
            if favicon_link and favicon_link.get("href"):
                favicon_url = self._normalize_url(favicon_link["href"], response.url)
                return favicon_url

        except Exception as e:
            print(f"Error getting media outlet logo: {e}")
        
        return None

    def _normalize_url(self, url, base_url):
        """Convert relative URLs to absolute URLs"""
        if not url:
            return None
            
        if url.startswith('//'):
            return 'https:' + url
        elif url.startswith('/'):
            base_parts = urlparse(base_url)
            return f"{base_parts.scheme}://{base_parts.netloc}{url}"
        elif not url.startswith(('http://', 'https://')):
            return urljoin(base_url, url)
        return url

    def _is_valid_image_url(self, url):
        """Check if URL is a valid image URL"""
        if not url:
            return False
        
        # Check if it's an actual image URL
        image_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.svg', '.ico']
        parsed_url = urlparse(url.lower())
        
        # Check extension or if it contains image-related parts
        has_extension = any(parsed_url.path.endswith(ext) for ext in image_extensions)
        has_image_indicator = any(indicator in parsed_url.path.lower() for indicator in ['/image', '/img', '/photo', '/pic'])
        
        return has_extension or has_image_indicator

    def _is_meaningful_image(self, url):
        """Check if image URL is meaningful (not a spacer, logo, etc.)"""
        if not url:
            return False
            
        url_lower = url.lower()
        
        # Skip common non-meaningful images
        skip_patterns = [
            'logo', 'avatar', 'icon', 'spacer', 'blank', 'transparent', 'pixel', 'tracking',
            'button', 'ad', 'banner', 'social', 'share', 'menu', 'nav', 'cookie', 'consent'
        ]
        
        return not any(pattern in url_lower for pattern in skip_patterns)

    # For Django Admin preview
    def article_image_tag(self):
        img_url = self.get_article_image()
        if img_url:
            return mark_safe(f'<img src="{img_url}" style="max-height: 150px; width: auto; border-radius: 4px; object-fit: cover;" alt="Article/Media Logo">')
        return "(No image found)"

    article_image_tag.short_description = "Image Preview"
    article_image_tag.allow_tags = True
