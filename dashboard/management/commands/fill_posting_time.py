from django.core.management.base import BaseCommand
from dashboard.models import MediaNarrative
from dateutil.parser import parse
import re

class Command(BaseCommand):
    help = 'Fill posting_time from article_text or url'

    def handle(self, *args, **options):
        updated = 0
        for article in MediaNarrative.objects.filter(posting_time__isnull=True):
            date_str = None

            # Try to find date in article_text (first 300 chars)
            text_snippet = article.article_text[:300]

            # Common patterns
            date_patterns = [
                r'\b(\d{1,2}[/-]\d{1,2}[/-]\d{4})\b',  # 15/04/2025 or 4/15/2025
                r'\b(\w+ \d{1,2}, \d{4})\b',            # April 15, 2025
                r'\b(\d{1,2} \w+ \d{4})\b',             # 15 April 2025
            ]

            for pattern in date_patterns:
                match = re.search(pattern, text_snippet)
                if match:
                    date_str = match.group(1)
                    break

            if date_str:
                try:
                    parsed_date = parse(date_str, dayfirst=True)  # Handles DD/MM and MM/DD
                    article.posting_time = parsed_date
                    article.save()
                    updated += 1
                    if updated % 1000 == 0:
                        self.stdout.write(f"Updated {updated} articles...")
                except:
                    continue

        self.stdout.write(self.style.SUCCESS(f'Successfully updated {updated} articles with posting_time!'))