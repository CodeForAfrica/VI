import ast
from django.core.management.base import BaseCommand
from dashboard.models import MediaNarrative

class Command(BaseCommand):
    help = 'Cleans Tone field by removing probability numbers from saved tuple strings'

    def handle(self, *args, **options):
        # We only target records that look like string-tuples: ('Label', 0.123)
        articles = MediaNarrative.objects.filter(tone__startswith='(')
        total = articles.count()
        
        self.stdout.write(f"🔍 Found {total} records with numeric tone data. Starting cleanup...")

        updated_articles = []
        for article in articles:
            try:
                # ast.literal_eval safely converts the string "('Tone', 0.9)" back to a tuple
                tone_data = ast.literal_eval(article.tone)
                
                if isinstance(tone_data, (tuple, list)):
                    article.tone = str(tone_data[0])
                    updated_articles.append(article)
                
                # Bulk update in chunks of 500 for safety and speed
                if len(updated_articles) >= 500:
                    MediaNarrative.objects.bulk_update(updated_articles, ['tone'])
                    self.stdout.write(f"✅ Cleaned {len(updated_articles)} records...")
                    updated_articles = []

            except (ValueError, SyntaxError):
                # If it's not a tuple string, we skip it
                continue

        # Final save for the last chunk
        if updated_articles:
            MediaNarrative.objects.bulk_update(updated_articles, ['tone'])

        self.stdout.write(self.style.SUCCESS(f"🎉 Cleanup complete. {total} records are now clean."))
