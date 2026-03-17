### For import_initial_data.py
import os
import csv
import tempfile
import boto3
import botocore
from django.core.management.base import BaseCommand
from django.conf import settings
from dashboard.models import MediaNarrative, VulnerabilityIndex

class Command(BaseCommand):
    help = "Import initial data from CSV files (supports S3 URIs and anchor-size)."

    def add_arguments(self, parser):
        parser.add_argument(
            '--articles-csv',
            help='Local path to articles CSV (if not using S3)'
        )
        parser.add_argument(
            '--articles-s3-key',
            help='S3 key (path) of articles CSV (uses bucket from settings)'
        )
        parser.add_argument(
            '--anchor-size',
            type=int,
            help='Number of first articles to mark as anchors (e.g., 15000)'
        )
        parser.add_argument(
            '--anchor-ids-csv',
            help='Local path to anchor IDs CSV (one ID per line) – overrides --anchor-size'
        )
        parser.add_argument(
            '--anchor-ids-s3-key',
            help='S3 key of anchor IDs file – overrides --anchor-size'
        )
        parser.add_argument(
            '--risk-table-csv',
            default=os.path.join(os.getcwd(), 'final_risk_by_actor_intent_country.csv'),
            help='Path to final_risk_by_actor_intent_country.csv (default: ./final_risk_by_actor_intent_country.csv)'
        )
        parser.add_argument(
            '--risk-table-s3-key',
            help='S3 key of risk table CSV (if provided, overrides local path)'
        )

    def get_s3_client(self):
        """Initialize S3 client using Django settings (same as MLInferenceService)."""
        aws_key = getattr(settings, 'AWS_ACCESS_KEY_ID', None) or os.environ.get('AWS_ACCESS_KEY_ID')
        aws_secret = getattr(settings, 'AWS_SECRET_ACCESS_KEY', None) or os.environ.get('AWS_SECRET_ACCESS_KEY')
        aws_bucket = getattr(settings, 'S3_MODELS_BUCKET', None) or os.environ.get('S3_MODELS_BUCKET')
        aws_region = getattr(settings, 'AWS_S3_REGION_NAME', None) or os.environ.get('AWS_S3_REGION_NAME', 'eu-west-1')

        if not all([aws_key, aws_secret, aws_bucket]):
            self.stderr.write(self.style.ERROR("AWS credentials not configured."))
            return None, None

        client = boto3.client(
            's3',
            aws_access_key_id=aws_key,
            aws_secret_access_key=aws_secret,
            region_name=aws_region,
            config=botocore.config.Config(
                retries={'max_attempts': 10, 'mode': 'adaptive'},
                connect_timeout=60,
                read_timeout=300
            )
        )
        return client, aws_bucket

    def download_from_s3(self, s3_key, description):
        """Download file from S3 to a temporary file and return its local path."""
        client, bucket = self.get_s3_client()
        if not client:
            raise Exception("S3 client not available.")

        self.stdout.write(f"Downloading {description} from s3://{bucket}/{s3_key}...")
        fd, local_path = tempfile.mkstemp(suffix='.csv')
        os.close(fd)
        try:
            client.download_file(bucket, s3_key, local_path)
            self.stdout.write(self.style.SUCCESS(f"Downloaded to {local_path}"))
            return local_path
        except Exception as e:
            os.unlink(local_path)
            raise Exception(f"Failed to download {s3_key}: {e}")

    def handle(self, *args, **options):
        # ----- Articles CSV -----
        articles_path = None
        if options['articles_s3_key']:
            articles_path = self.download_from_s3(options['articles_s3_key'], 'articles CSV')
        elif options['articles_csv']:  
            articles_path = options['articles_csv']
        else:
            self.stderr.write(self.style.ERROR("Either --articles-csv or --articles-s3-key must be provided."))
            return

        # ----- Risk table CSV -----
        risk_path = None
        if options['risk_table_s3_key']:
            risk_path = self.download_from_s3(options['risk_table_s3_key'], 'risk table CSV')
        else:
            risk_path = options['risk_table_csv']   # default local path

        # ----- Anchor handling -----
        anchor_ids = set()
        use_anchor_size = False
        if options['anchor_ids_s3_key']:
            # Download anchor IDs file from S3
            anchor_path = self.download_from_s3(options['anchor_ids_s3_key'], 'anchor IDs CSV')
            use_anchor_size = False
        elif options['anchor_ids_csv']:
            anchor_path = options['anchor_ids_csv']
            use_anchor_size = False
        elif options['anchor_size']:
            use_anchor_size = True
            anchor_size = options['anchor_size']
        else:
            use_anchor_size = False
            self.stdout.write("No anchor specification; all articles will have is_anchor=False.")

        # ----- Import articles -----
        self.stdout.write("Importing articles...")
        with open(articles_path, encoding='utf-8') as f:
            reader = csv.DictReader(f)
            # If using anchor-size, we need to split the rows
            if use_anchor_size:
                all_rows = list(reader)
                anchor_rows = all_rows[:anchor_size]
                non_anchor_rows = all_rows[anchor_size:]
                self.stdout.write(f"First {anchor_size} rows will be anchors.")
            else:
                # No anchor-size, just one list
                all_rows = list(reader)
                anchor_rows = []
                non_anchor_rows = all_rows

        # Helper to create article objects from rows, with given is_anchor value
        # Helper to create article objects without the missing 'is_anchor' column
        def create_articles_from_rows(rows):
            objs = []
            for row in rows:
                objs.append(MediaNarrative(
                    article_text=row['article_text'],
                    posting_time=row.get('posting_time') or None,
                    media_outlet=row.get('media_outlet', ''),
                    inferred_actor=row.get('inferred_actor', ''),
                    strategic_intent=row.get('strategic_intent') or None,
                    sector=row.get('sector', ''),
                    tone=row.get('tone', ''),
                    target_country=row.get('target_country', ''),
                    url=row.get('URL', ''),
                    confidence=float(row.get('confidence', 0)) if row.get('confidence') else 0.0,
                    lang_detect=row.get('lang_detect', ''),
                    use_afrolm=row.get('use_afrolm', '').lower() in ('true', '1', 'yes'),
                    # is_anchor=is_anchor_val  
                ))
            return objs

        if anchor_rows:
            anchor_objs = create_articles_from_rows(anchor_rows) 
            MediaNarrative.objects.bulk_create(anchor_objs, batch_size=1000)
            self.stdout.write(f"Inserted {len(anchor_objs)} anchor articles.")

        if non_anchor_rows:
            non_anchor_objs = create_articles_from_rows(non_anchor_rows) 
            # UNCOMMENTED THIS so data actually saves:
            MediaNarrative.objects.bulk_create(non_anchor_objs, batch_size=1000)
            self.stdout.write(f"Inserted {len(non_anchor_objs)} non-anchor articles.")

        total_imported = len(anchor_rows) + len(non_anchor_rows)
        self.stdout.write(self.style.SUCCESS(f"Imported {total_imported} articles total."))

        # 2. Skip anchor marking entirely to avoid the RDS column error
        self.stdout.write("Skipping anchor marking (column 'is_anchor' missing in DB).")
        
        # ----- Import VulnerabilityIndex -----
        self.stdout.write(f"Importing vulnerability index table from {risk_path}...")
        with open(risk_path, encoding='utf-8') as f:
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

        # ----- Clean up temporary files -----
        if options['articles_s3_key'] and articles_path and os.path.exists(articles_path):
            os.unlink(articles_path)
        if 'anchor_path' in locals() and anchor_path and os.path.exists(anchor_path):
            os.unlink(anchor_path)
        if options['risk_table_s3_key'] and risk_path and os.path.exists(risk_path):
            os.unlink(risk_path)
