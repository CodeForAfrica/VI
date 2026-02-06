import requests
from bs4 import BeautifulSoup
from django.core.management.base import BaseCommand
from dashboard.models import MediaNarrative

class Command(BaseCommand):
    help = 'Extract authors from article URLs and update the author field (skipping "Unknown")'

    def handle(self, *args, **options):
        updated = 0
        skipped = 0

        articles = MediaNarrative.objects.all().order_by('-posting_time')  # or .filter(author="Unknown") to target specific

        for article in articles:
            if article.author != "Unknown":
                skipped += 1
                continue

            author = self.extract_author(article.url)
            if author and author.lower() != "unknown":
                article.author = author
                article.save()
                updated += 1
                self.stdout.write(self.style.SUCCESS(f"Updated {article.url} with author: {author}"))
            else:
                skipped += 1

        self.stdout.write(self.style.SUCCESS(f'Completed! Updated {updated} articles. Skipped {skipped}.'))

    def extract_author(self, url):
        if not url:
            return "Unknown"

        try:
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')

            # Common author locations
            author_selectors = [
                'meta[name="author"]',
                'meta[property="article:author"]',
                'span.byline-author',
                'div.author-name',
                'a[rel="author"]',
                'p.byline',
                'span.author',
                'div.post-meta .author',
                'div.article-author',
            ]

            for selector in author_selectors:
                elem = soup.select_one(selector)
                if elem:
                    author_text = elem.get('content') or elem.get_text()
                    if author_text and 'unknown' not in author_text.lower():
                        return author_text.strip()

            return "Unknown"
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error extracting from {url}: {str(e)}"))
            return "Unknown"