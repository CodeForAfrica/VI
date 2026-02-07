from django.core.management.base import BaseCommand
from dashboard.services.mediacloud_ingestion_service import MediaCloudIngestionService
import logging

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Ingest articles from MediaCloud API with ML inference and vulnerability indexing'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--query',
            type=str,
            help='MediaCloud search query',
            default='ethiopia OR nigeria OR kenya'  # Customize based on your monitoring
        )
        parser.add_argument(
            '--limit',
            type=int,
            help='Number of articles to fetch',
            default=100
        )
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Enable verbose logging'
        )
    
    def handle(self, *args, **options):
        # Set up logging based on verbose flag
        if options['verbose']:
            logging.basicConfig(level=logging.DEBUG)
        else:
            logging.basicConfig(level=logging.INFO)
        
        service = MediaCloudIngestionService()
        
        query_params = {
            'q': options['query'],
            'limit': options['limit'],
            'format': 'json'
        }
        
        self.stdout.write(
            self.style.SUCCESS(f'Starting MediaCloud ingestion with query: {options["query"]}')
        )
        self.stdout.write(
            self.style.NOTICE(f'Fetching {options["limit"]} articles...')
        )
        
        try:
            results = service.ingest_batch(query_params)
            
            self.stdout.write(
                self.style.SUCCESS(
                    f'\nIngestion completed successfully!\n'
                    f'Processed: {results["processed"]} articles\n'
                    f'Saved: {results["saved"]} articles\n'
                    f'Success Rate: {results["saved"]/results["processed"]*100:.1f}%'
                )
            )
            
            # Print summary statistics
            if results["saved"] > 0:
                from dashboard.models import MediaNarrative
                latest_articles = MediaNarrative.objects.order_by('-posting_time')[:5]
                
                self.stdout.write(
                    self.style.HTTP_INFO('\nLatest processed articles:')
                )
                for article in latest_articles:
                    self.stdout.write(
                        f'  • {article.target_country}: {article.strategic_intent} ({article.tone}) - '
                        f'VI: {article.vulnerability_index:.3f}'
                    )
            
        except KeyboardInterrupt:
            self.stdout.write(
                self.style.WARNING('\nOperation cancelled by user.')
            )
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'\nIngestion failed: {str(e)}')
            )
            import traceback
            if options['verbose']:
                self.stdout.write(
                    self.style.ERROR(f'Detailed error:\n{traceback.format_exc()}')
                )
