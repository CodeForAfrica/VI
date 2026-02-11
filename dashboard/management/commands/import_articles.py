import csv
from datetime import datetime
from django.core.management.base import BaseCommand
from dashboard.models import MediaNarrative

class Command(BaseCommand):
    help = 'Safely import media narratives from a CSV file into the database'

    def add_arguments(self, parser):
        parser.add_argument('csv_file', type=str, help='Path to the CSV file (e.g., merged_dataset.csv)')

    def handle(self, *args, **options):
        csv_file = options['csv_file']
        imported = 0
        skipped = 0

        def safe_float(value, default=None):
            value = value.strip()
            try:
                return float(value) if value else default
            except (ValueError, TypeError):
                return default

        def safe_int(value, default=None):
            value = value.strip()
            try:
                return int(value) if value else default
            except (ValueError, TypeError):
                return default

        def safe_bool(value):
            value = value.strip().lower()
            return value == 'true' if value else False

        with open(csv_file, 'r', encoding='utf-8') as file:
            reader = csv.DictReader(file)
            for row_num, row in enumerate(reader, start=2):
                try:
                    # FIX: Your CSV uses MM/DD/YYYY HH:MM format
                    posting_time_str = row.get('posting_time', '').strip()
                    if not posting_time_str:
                        raise ValueError("posting_time is empty")

                    # Try multiple common formats
                    posting_time = None
                    for fmt in ('%m/%d/%Y %H:%M', '%Y-%m-%d %H:%M:%S', '%d/%m/%Y %H:%M'):
                        try:
                            posting_time = datetime.strptime(posting_time_str, fmt)
                            break
                        except ValueError:
                            continue

                    if not posting_time:
                        raise ValueError(f"Could not parse date: {posting_time_str}")

                    MediaNarrative.objects.create(
                        article_text=row['article_text'],
                        posting_time=posting_time,  # Now correctly parsed
                        media_outlet=row.get('media_outlet', '').strip() or 'Unknown',
                        inferred_actor=row.get('inferred_actor', '').strip() or 'Unknown',
                        strategic_intent=row.get('strategic_intent', '').strip() or 'Unknown',
                        sector=row.get('sector', '').strip() or 'Unknown',
                        tone=row.get('tone', '').strip() or 'Unknown',
                        target_country=row.get('target_country', '').strip() or 'Unknown',
                        url=row.get('URL', '').strip() or '',
                        confidence=safe_float(row.get('confidence', ''), 4.0),
                        lang_detect=row.get('lang_detect', '').strip() or 'en',
                        use_afrolm=safe_bool(row.get('use_afrolm', '')),
                        llm_strat=row.get('llm_strat', '').strip() or None,
                        llm_strat_conf=safe_float(row.get('llm_strat_conf', '')),
                        llm_strat_notes=row.get('llm_strat_notes', '').strip() or None,
                        pseudo_kept=safe_bool(row.get('pseudo_kept', '')),
                        pseudo_weight=safe_float(row.get('pseudo_weight', ''), 0.0),
                        llm_strat_id=safe_int(row.get('llm_strat_id', ''), -1),
                        strategic_intent_id=safe_int(row.get('strategic_intent_id', ''), 0),
                    )
                    imported += 1

                except Exception as e:
                    self.stdout.write(
                        self.style.ERROR(f"Row {row_num} skipped: {e}")
                    )
                    skipped += 1
                    continue

        self.stdout.write(
            self.style.SUCCESS(
                f"Import finished: {imported} articles successfully imported, {skipped} rows skipped."
            )
        )