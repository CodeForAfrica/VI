import csv
from django.core.management.base import BaseCommand
from dashboard.models import MediaNarrative, VulnerabilityIndex

class Command(BaseCommand):
    help = "Import initial data from CSV files."

    def add_arguments(self, parser):
        parser.add_argument('--articles-csv', required=True, help='Path to articles CSV (with human labels)')
        parser.add_argument('--risk-table-csv', required=True, help='Path to final_risk_by_actor_intent_country.csv')
        parser.add_argument('--anchor-ids-csv', help='Optional: CSV with list of anchor article IDs (one per line, or with header "id")')

    def handle(self, *args, **options):
        # 1. Import articles
        self.stdout.write("Importing articles...")
        with open(options['articles_csv'], encoding='utf-8') as f:
            reader = csv.DictReader(f)
            articles = []
            for row in reader:
                # Handle empty strings for nullable fields
                articles.append(MediaNarrative(
                    article_text=row['article_text'],
                    posting_time=row.get('posting_time') or None,
                    media_outlet=row.get('media_outlet', ''),
                    inferred_actor=row.get('inferred_actor', ''),
                    strategic_intent=row.get('strategic_intent') or None,   # human label (may be empty)
                    sector=row.get('sector', ''),
                    tone=row.get('tone', ''),
                    target_country=row.get('target_country', ''),
                    URL=row.get('URL', ''),
                    confidence=float(row.get('confidence', 0)),
                    lang_detect=row.get('lang_detect', ''),
                    use_aflom=row.get('use_aflom', '').lower() in ('true', '1', 'yes'),
                ))
            # Bulk insert (adjust batch size if needed)
            MediaNarrative.objects.bulk_create(articles, batch_size=1000)
        self.stdout.write(self.style.SUCCESS(f"Imported {len(articles)} articles."))

        # 2. Mark anchor articles if anchor-ids-csv provided
        if options['anchor_ids_csv']:
            self.stdout.write("Marking anchor articles...")
            anchor_ids = set()
            with open(options['anchor_ids_csv']) as f:
                # Assume first column contains IDs, possibly with header 'id'
                reader = csv.reader(f)
                header = next(reader, None)
                if header and header[0].lower() == 'id':
                    pass  # header read, now rows are data
                else:
                    # No header, rewind
                    f.seek(0)
                    reader = csv.reader(f)
                for row in reader:
                    if row:
                        anchor_ids.add(int(row[0]))
            updated = MediaNarrative.objects.filter(id__in=anchor_ids).update(is_anchor=True)
            self.stdout.write(self.style.SUCCESS(f"Marked {updated} articles as anchors."))
        else:
            self.stdout.write("No anchor IDs file provided; you can set is_anchor manually later.")

        # 3. Import VulnerabilityIndex
        self.stdout.write("Importing vulnerability index table...")
        with open(options['risk_table_csv'], encoding='utf-8') as f:
            reader = csv.DictReader(f)
            objs = []
            for row in reader:
                objs.append(VulnerabilityIndex(
                    actor=row['actor'],
                    country=row['country'],
                    intent=row['intent'],
                    final_risk=float(row['FinalRisk'])
                ))
            VulnerabilityIndex.objects.bulk_create(objs, batch_size=1000)
        self.stdout.write(self.style.SUCCESS(f"Imported {len(objs)} vulnerability indexes."))
