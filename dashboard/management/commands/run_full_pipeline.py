import logging
import trafilatura
from django.core.management.base import BaseCommand
from dashboard.models import MediaNarrative
from dashboard.services.ml_inference_service import get_ml_service

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Runs the end-to-end ML pipeline: Extraction -> Inference -> Scoring'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Test without saving')
        parser.add_argument('--limit', type=int, default=5, help='Number of articles to process')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        limit = options['limit']
        ml_service = get_ml_service()
        
        self.stdout.write(f"--- Starting Pipeline (Dry Run: {dry_run}) ---")
        
        articles = MediaNarrative.objects.filter(article_text__isnull=True)[:limit]  

        for article in articles:
            try:
                # 1. EXTRACTION (proven trafilatura logic)
                downloaded = trafilatura.fetch_url(article.url)
                text = trafilatura.extract(downloaded) if downloaded else None
                
                if not text or len(text.strip()) < 50:
                    self.stdout.write(self.style.ERROR(f"Skipping {article.title}: Extraction failed or empty."))
                    continue
                
                # 2. ML INFERENCE 
                results = ml_service.perform_inference(text)
                
                # 3. SCORING
                score = ml_service.calculate_vulnerability_index(
                    results['strategic_intent'], 
                    results['tone'], 
                    article.target_country, 
                    article.inferred_actor, 
                    results['confidence']
                )

                # 4. OUTPUT/SAVE
                msg = f"Title: {article.title[:30]}... | Intent: {results['strategic_intent']} | Score: {score}"
                
                if dry_run:
                    self.stdout.write(self.style.WARNING(f"[DRY RUN] {msg}"))
                else:
                    article.article_text = text
                    article.strategic_intent = results['strategic_intent']
                    article.tone = results['tone']
                    article.vulnerability_index = score
                    article.save()
                    
            except Exception as e:
                logger.error(f"Failed to process {article.title}: {e}")
                self.stdout.write(self.style.ERROR(f"Error on {article.title}: {e}"))
        
        ml_service.cleanup()
        self.stdout.write("--- Pipeline Finished ---")
