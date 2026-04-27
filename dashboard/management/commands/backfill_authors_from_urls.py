#!/usr/bin/env python
"""
Backfill author names by scraping existing article URLs.
Processes articles where author='Unknown' or author IS NULL.
"""

import os
import sys
import django
import time
import logging
from datetime import datetime

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from dashboard.models import MediaNarrative
from django.db import transaction
from bs4 import BeautifulSoup
import requests
import json
import re

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('backfill_authors.log'),
        logging.StreamHandler(sys.stdout)
    ]
)

def extract_author_from_url(url, timeout=15):
    """Extract author from article HTML (same function as above)"""
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
        logging.debug(f"Failed to extract from {url}: {e}")
        return None


def backfill_authors(batch_size=100, delay=2.0, max_articles=None):
    """
    Backfill author field by scraping article URLs.
    
    Args:
        batch_size: Process this many articles before committing
        delay: Seconds to wait between requests (be nice to servers)
        max_articles: Limit total articles to process (None = all)
    """
    
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
    logging.info(f"🔍 Found {total} articles needing author extraction")
    
    if total == 0:
        logging.info("✅ All articles already have authors!")
        return
    
    # Stats
    updated = 0
    failed = 0
    skipped = 0
    start_time = time.time()
    
    # Process in batches
    batch = []
    for i, article in enumerate(articles, 1):
        try:
            # Extract author
            author_name = extract_author_from_url(article.url, timeout=15)
            
            if author_name:
                article.author = author_name
                batch.append(article)
                updated += 1
                status = f"✅ '{author_name}'"
            else:
                skipped += 1
                status = "⚠️  Not found"
            
            # Commit batch
            if len(batch) >= batch_size:
                with transaction.atomic():
                    MediaNarrative.objects.bulk_update(batch, ['author'])
                batch = []
                
                # Progress report
                elapsed = time.time() - start_time
                rate = i / elapsed if elapsed > 0 else 0
                eta = (total - i) / rate if rate > 0 else 0
                logging.info(f"📊 Progress: {i}/{total} ({i/total*100:.1f}%) | "
                           f"Updated: {updated} | Skipped: {skipped} | "
                           f"Rate: {rate:.2f}/sec | ETA: {eta/60:.1f}min")
            
            # Rate limiting
            time.sleep(delay)
            
        except Exception as e:
            failed += 1
            logging.error(f"❌ Error processing {article.url}: {e}")
            continue
    
    # Final batch commit
    if batch:
        with transaction.atomic():
            MediaNarrative.objects.bulk_update(batch, ['author'])
    
    # Final stats
    elapsed = time.time() - start_time
    logging.info(f"\n{'='*60}")
    logging.info(f"🎉 Backfill Complete!")
    logging.info(f"{'='*60}")
    logging.info(f"📝 Total processed: {total}")
    logging.info(f"✅ Authors found: {updated} ({updated/total*100:.1f}%)")
    logging.info(f"⚠️  Not found: {skipped} ({skipped/total*100:.1f}%)")
    logging.info(f"❌ Errors: {failed}")
    logging.info(f"⏱️  Duration: {elapsed/60:.1f} minutes")
    logging.info(f"📈 Rate: {total/elapsed:.2f} articles/sec")
    logging.info(f"{'='*60}")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Backfill author names from article URLs')
    parser.add_argument('--batch', type=int, default=100, help='Batch size for DB commits')
    parser.add_argument('--delay', type=float, default=2.0, help='Delay between requests (seconds)')
    parser.add_argument('--limit', type=int, default=None, help='Limit total articles to process')
    parser.add_argument('--test', action='store_true', help='Test mode: process only 10 articles')
    
    args = parser.parse_args()
    
    if args.test:
        logging.info("🧪 TEST MODE: Processing only 10 articles")
        args.limit = 10
        args.delay = 1.0
    
    backfill_authors(batch_size=args.batch, delay=args.delay, max_articles=args.limit)
