# dashboard/management/commands/backfill_authors.py
import time
import logging
from django.core.management.base import BaseCommand
from django.db import transaction
from bs4 import BeautifulSoup
import requests
import json
import re
from dashboard.models import MediaNarrative

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Backfill author names by scraping existing article URLs'

    def add_arguments(self, parser):
        parser.add_argument('--batch', type=int, default=100, help='Batch size for DB commits')
        parser.add_argument('--delay', type=float, default=2.0, help='Delay between requests (seconds)')
        parser.add_argument('--limit', type=int, default=None, help='Limit total articles to process')
        parser.add_argument('--test', action='store_true', help='Test mode: process only 10 articles')

    def extract_author_from_url(self, url, timeout=15):
        """Extract author from article HTML."""
        try:
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
            response = requests.get(url, headers=headers, timeout=timeout)
            if response.status_code != 200:
                return None
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Meta tags
            for meta in soup.find_all('meta'):
                if meta.get('name') == 'author' and meta.get('content'):
                    name = meta['content'].strip()
                    if name and name.lower() not in ['unknown', 'none', 'n/a', 'staff', 'editor', 'by', 'agency', 'reuters', 'afp']:
                        return name
                if meta.get('property') == 'article:author' and meta.get('content'):
                    name = meta['content'].strip()
                    if name and name.lower() not in ['unknown', 'none', 'n/a', 'staff', 'editor', 'by', 'agency', 'reuters', 'afp']:
                        return name
            
            # JSON-LD
            for script in soup.find_all('script', type='application/ld+json'):
                try:
                    data = json.loads(script.string)
                    if isinstance(data, list):
                        data = next((item for item in data if isinstance(item, dict)), {})
                    if isinstance(data, dict) and data.get('author'):
                        author = data['author']
                        if isinstance(author, dict) and author.get('name'):
                            name = author['name'].strip()
                            if name and name.lower() not in ['unknown', 'none', 'n/a', 'staff', 'editor', 'by', 'agency']:
                                return name
                        elif isinstance(author, list) and author and isinstance(author[0], dict) and author[0].get('name'):
                            name = author[0]['name'].strip()
                            if name and name.lower() not in ['unknown', 'none', 'n/a', 'staff', 'editor', 'by', 'agency']:
                                return name
                except:
                    continue
            
            # CSS selectors
            for selector in ['.byline', '.author-name', '[rel="author"]']:
                elem = soup.select_one(selector)
                if elem and elem.text.strip():
                    name = re.sub(r'^(By|by)\s+', '', elem.text.strip(), flags=re.IGNORECASE)
                    if name and len(name) > 2 and name.lower() not in ['unknown', 'none', 'n/a', 'staff', 'editor']:
                        return name
            
            # "By [Name]" pattern
            text = soup.get_text()[:500]
            match = re.search(r'(?:By|by)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z\.]+)+)', text)
            if match:
                name = match.group(1).strip()
                if name and len(name) > 2:
                    return name
            
            return None
            
        except Exception as e:
            logger.debug(f"Failed to extract from {url}: {e}")
            return None

    def handle(self, *args, **options):
        batch_size = options['batch']
        delay = options['delay']
        max_articles = options['limit']
        test_mode = options['test']
        
        if test_mode:
            self.stdout.write(self.style.WARNING("🧪 TEST MODE: Processing only 10 articles"))
            max_articles = 10
            delay = 1.0

        # Find articles needing author extraction
        articles = MediaNarrative.objects.filter(
            author__in=['Unknown', '', None]
        ).exclude(
            url__isnull=True
        ).exclude(
            url=''
        ).order_by('-posting_time')
        
        if max_articles:
            articles = articles[:max_articles]
        
        total = articles.count()
        self.stdout.write(f"🔍 Found {total} articles needing author extraction")
        
        if total == 0:
            self.stdout.write(self.style.SUCCESS("✅ All articles already have authors!"))
            return
        
        updated = 0
        failed = 0
        skipped = 0
        start_time = time.time()
        
        batch = []
        for i, article in enumerate(articles, 1):
            try:
                author_name = self.extract_author_from_url(article.url, timeout=15)
                
                if author_name:
                    article.author = author_name
                    batch.append(article)
                    updated += 1
                else:
                    skipped += 1
                
                if len(batch) >= batch_size:
                    with transaction.atomic():
                        MediaNarrative.objects.bulk_update(batch, ['author'])
                    batch = []
                    
                    elapsed = time.time() - start_time
                    rate = i / elapsed if elapsed > 0 else 0
                    eta = (total - i) / rate if rate > 0 else 0
                    self.stdout.write(
                        f"📊 Progress: {i}/{total} ({i/total*100:.1f}%) | "
                        f"Updated: {updated} | Skipped: {skipped} | "
                        f"Rate: {rate:.2f}/sec | ETA: {eta/60:.1f}min"
                    )
                
                time.sleep(delay)
                
            except Exception as e:
                failed += 1
                self.stderr.write(f"❌ Error processing {article.url}: {e}")
                continue
        
        if batch:
            with transaction.atomic():
                MediaNarrative.objects.bulk_update(batch, ['author'])
        
        elapsed = time.time() - start_time
        self.stdout.write(self.style.SUCCESS(f"\n🎉 Backfill Complete!"))
        self.stdout.write(f"📝 Total processed: {total}")
        self.stdout.write(f"✅ Authors found: {updated} ({updated/total*100:.1f}%)")
        self.stdout.write(f"⚠️  Not found: {skipped} ({skipped/total*100:.1f}%)")
        self.stdout.write(f"❌ Errors: {failed}")
        self.stdout.write(f"⏱️  Duration: {elapsed/60:.1f} minutes")
