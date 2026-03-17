import logging
from django.core.management import call_command
from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Run the full pipeline: import data, update strategic intent, update vulnerability indexes.'

    def add_arguments(self, parser):
        # Arguments for import_initial_data
        parser.add_argument('--articles-s3-key', help='S3 key for articles CSV')
        parser.add_argument('--articles-csv', help='Local path to articles CSV')
        parser.add_argument('--anchor-size', type=int, help='Number of first articles to mark as anchors')
        parser.add_argument('--risk-table-s3-key', help='S3 key for risk table CSV')
        parser.add_argument(
            '--risk-table-csv',
            default='./final_risk_by_actor_intent_country.csv',
            help='Local path to risk table CSV (default: ./final_risk_by_actor_intent_country.csv)'
        )
        parser.add_argument('--anchor-ids-s3-key', help='S3 key for anchor IDs file (overrides anchor-size)')
        parser.add_argument('--anchor-ids-csv', help='Local path to anchor IDs file (overrides anchor-size)')

        # Arguments for update_strategic_intent
        parser.add_argument('--llm-only', action='store_true', help='Only run LLM (skip model)')
        parser.add_argument('--model-only', action='store_true', help='Only run model (skip LLM)')
        parser.add_argument(
            '--batch-size',
            type=int,
            default=500,
            help='Batch size for database updates (used in update_strategic_intent)'
        )

        # Control flags
        parser.add_argument(
            '--skip-import',
            action='store_true',
            help='Skip the import step (if data already loaded)'
        )

    def handle(self, *args, **options):
        self.stdout.write("=== Starting Full Pipeline ===")

        # Step 1: Import data (unless skipped)
        if not options['skip_import']:
            self.stdout.write("\n--- Step 1: Importing initial data ---")
            import_args = []

            # Pass through relevant arguments
            if options['articles_s3_key']:
                import_args.extend(['--articles-s3-key', options['articles_s3_key']])
            if options['articles_csv']:
                import_args.extend(['--articles-csv', options['articles_csv']])
            if options['anchor_size']:
                import_args.extend(['--anchor-size', str(options['anchor_size'])])
            if options['risk_table_s3_key']:
                import_args.extend(['--risk-table-s3-key', options['risk_table_s3_key']])
            if options['risk_table_csv']:
                import_args.extend(['--risk-table-csv', options['risk_table_csv']])
            if options['anchor_ids_s3_key']:
                import_args.extend(['--anchor-ids-s3-key', options['anchor_ids_s3_key']])
            if options['anchor_ids_csv']:
                import_args.extend(['--anchor-ids-csv', options['anchor_ids_csv']])

            if not import_args:
                self.stderr.write(
                    self.style.ERROR("No import arguments provided. Use --skip-import if data already loaded.")
                )
                return

            try:
                call_command('import_initial_data', *import_args)
                self.stdout.write(self.style.SUCCESS("Import completed successfully."))
            except Exception as e:
                self.stderr.write(self.style.ERROR(f"Import failed: {e}"))
                return
        else:
            self.stdout.write("Skipping import step as requested.")

        # Step 2: Update strategic intent
        self.stdout.write("\n--- Step 2: Updating strategic intent for new articles ---")
        intent_args = []
        if options['llm_only']:
            intent_args.append('--llm-only')
        if options['model_only']:
            intent_args.append('--model-only')
        if options['batch_size']:
            intent_args.extend(['--batch-size', str(options['batch_size'])])

        try:
            call_command('update_strategic_intent', *intent_args)
            self.stdout.write(self.style.SUCCESS("Strategic intent update completed."))
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"Strategic intent update failed: {e}"))
            return

        # Step 3: Update vulnerability indexes
        self.stdout.write("\n--- Step 3: Recalculating vulnerability indexes ---")
        try:
            call_command('update_vulnerability_indexes')
            self.stdout.write(self.style.SUCCESS("Vulnerability indexes updated."))
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"Vulnerability index update failed: {e}"))
            return

        self.stdout.write(self.style.SUCCESS("\n=== Full pipeline completed successfully ==="))

