from django.db.models import (
    Model, TextField, DateTimeField, CharField, URLField,
    FloatField, BooleanField, IntegerField, ForeignKey, SET_NULL
)

from django.utils.safestring import mark_safe
import requests
from bs4 import BeautifulSoup
#from django.db import Model
from django.db import models 
from django.db.models import SET_NULL

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
    #name = CharField(max_length=255, unique=True)
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
    lang_detect = CharField(max_length=10, blank=True, null=True)
    use_afrolm = BooleanField(default=False)
    llm_strat = CharField(max_length=255, blank=True, null=True)
    llm_strat_conf = FloatField(blank=True, null=True)
    llm_strat_notes = TextField(blank=True, null=True)
    pseudo_kept = BooleanField(default=False)
    pseudo_weight = FloatField(default=0.0)
    llm_strat_id = IntegerField(blank=True, null=True)
    strategic_intent_id = IntegerField(blank=True, null=True)
    author = CharField(max_length=255, blank=True, null=True, default="Unknown")  # Add back if removed

    journalist_fk = ForeignKey('Journalist', on_delete=SET_NULL, null=True, blank=True, related_name='articles')
    media_outlet_fk = ForeignKey('MediaOutlet', on_delete=SET_NULL, null=True, blank=True, related_name='articles')

    class Meta:
        ordering = ['-posting_time']
        verbose_name = "Media Narrative"
        verbose_name_plural = "Media Narratives"

    def __str__(self):
        outlet = self.media_outlet_fk.name if self.media_outlet_fk else self.media_outlet or "Unknown"
        date = self.posting_time.strftime("%Y-%m-%d") if self.posting_time else "No date"
        return f"{outlet} - {date}"

    # NEW: Fetch real article image from URL
    def get_article_image(self):
        """
        Returns the main article image URL (from og:image or fallback)
        Returns None if no image found or error
        """
        if not self.url:
            return None

        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }
            response = requests.get(self.url, headers=headers, timeout=8)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, 'html.parser')

            # Priority 1: Open Graph image (most reliable)
            og_image = soup.find("meta", property="og:image")
            if og_image and og_image.get("content"):
                img_url = og_image["content"]
                if img_url.startswith('//'):
                    img_url = 'https:' + img_url
                elif img_url.startswith('/'):
                    img_url = 'https://' + response.url.split('/')[2] + img_url
                return img_url

            # Priority 2: Twitter card image
            twitter_image = soup.find("meta", name="twitter:image")
            if twitter_image and twitter_image.get("content"):
                return twitter_image["content"]

            # Priority 3: First reasonable <img> tag
            for img in soup.find_all("img", src=True):
                src = img["src"]
                if src and not any(bad in src.lower() for bad in ['logo', 'avatar', 'icon', 'spacer']):
                    if src.startswith('//'):
                        src = 'https:' + src
                    return src

            return None
        except Exception:
            return None

    # For Django Admin preview
    def article_image_tag(self):
        img_url = self.get_article_image()
        if img_url:
            return mark_safe(f'<img src="{img_url}" style="max-height: 150px; border-radius: 4px;">')
        return "(No image found)"

    article_image_tag.short_description = "Image Preview"
    article_image_tag.allow_tags = True