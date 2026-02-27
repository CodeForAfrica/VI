import logging
import trafilatura
from django.core.management.base import BaseCommand
from dashboard.models import MediaNarrative
from dashboard.services.ml_inference_service import get_ml_service

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Runs the end-to-end ML pipeline: Extraction -> Validation -> Target/Actor Extraction -> ML Inference -> Scoring -> Storage'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Test without saving')
        parser.add_argument('--limit', type=int, default=50, help='Number of articles to process')
        parser.add_argument('--skip-extraction', action='store_true', help='Skip text extraction')
        parser.add_argument('--skip-ml', action='store_true', help='Skip ML inference')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        limit = options['limit']
        skip_extraction = options['skip_extraction']
        skip_ml = options['skip_ml']
        ml_service = get_ml_service()
        
        self.stdout.write(self.style.SUCCESS(f"--- Starting Full Pipeline (Dry Run: {dry_run}) ---"))
        self.stdout.write(f"Limit: {limit} articles")
        
        # Step 1: Get articles that need processing
        # Articles that need any processing
        articles = MediaNarrative.objects.filter(
            article_text__isnull=True
        )[:limit]
        
        total_count = articles.count()
        self.stdout.write(f"Found {total_count} articles to process")
        
        if total_count == 0:
            self.stdout.write(self.style.WARNING("No articles found needing processing"))
            return
        
        processed = 0
        skipped = 0
        errors = 0
        
        for article in articles:
            try:
                self.stdout.write(f"\n📄 Processing: {article.id} - {article.url[:50]}...")
                
                # Step 2: Extract full article text from URL
                if not skip_extraction:
                    downloaded = trafilatura.fetch_url(article.url)
                    text = trafilatura.extract(downloaded) if downloaded else None
                    
                    if not text or len(text.strip()) < 50:
                        self.stdout.write(self.style.ERROR(f"⚠️ Skipping {article.id}: Extraction failed or empty"))
                        skipped += 1
                        continue
                    
                    self.stdout.write(f"✅ Extracted {len(text)} characters")
                else:
                    text = article.article_text
                
                # Step 3: Extract target country and foreign actor using NER
                entities = ml_service.extract_entities_from_content(text)
                target_country = entities['countries'][0] if entities['countries'] else article.target_country or 'Unknown'
                inferred_actor = entities['organizations'][0] if entities['organizations'] else article.inferred_actor or 'Unknown'
                
                self.stdout.write(f"🎯 Target: {target_country} | Actor: {inferred_actor}")
                
                # Step 4: ML Inference (if not skipped)
                if not skip_ml:
                    results = ml_service.perform_inference(text)
                    
                    strategic_intent = results['strategic_intent']
                    tone = results['tone']
                    confidence = results['confidence']
                    lang_detect = results['lang_detect']
                    
                    self.stdout.write(f"🧠 Intent: {strategic_intent} | Tone: {tone} | Conf: {confidence:.2f}")
                else:
                    strategic_intent = article.strategic_intent or 'Unknown'
                    tone = article.tone or 'neutral'
                    confidence = 0.0
                    lang_detect = article.lang_detect or 'en'
                
                # Step 5: Calculate Vulnerability Index
                if not skip_ml:
                    vulnerability_index = ml_service.calculate_vulnerability_index(
                        strategic_intent, 
                        tone, 
                        target_country, 
                        inferred_actor, 
                        confidence
                    )
                    self.stdout.write(f"📊 Vulnerability Index: {vulnerability_index}")
                else:
                    vulnerability_index = article.vulnerability_index or 0.0
                
                # Step 6: Save to database (or dry run)
                if dry_run:
                    self.stdout.write(self.style.WARNING(f"[DRY RUN] Would save:"))
                    self.stdout.write(f"  - article_text: {len(text)} chars")
                    self.stdout.write(f"  - strategic_intent: {strategic_intent}")
                    self.stdout.write(f"  - tone: {tone}")
                    self.stdout.write(f"  - vulnerability_index: {vulnerability_index}")
                else:
                    article.article_text = text
                    article.strategic_intent = strategic_intent
                    article.tone = tone
                    article.confidence = confidence
                    article.lang_detect = lang_detect
                    article.vulnerability_index = vulnerability_index
                    article.ml_processed_at = django.utils.timezone.now()
                    article.save()
                    
                    self.stdout.write(self.style.SUCCESS(f"✅ Saved: ID {article.id}"))
                
                processed += 1
                
            except Exception as e:
                logger.error(f"❌ Error processing {article.id}: {e}")
                self.stdout.write(self.style.ERROR(f"❌ Error: {str(e)[:50]}"))
                errors += 1
        
        # Cleanup
        ml_service.cleanup()
        
        # Summary
        self.stdout.write(self.style.SUCCESS(f"\n--- Pipeline Complete ---"))
        self.stdout.write(f"Processed: {processed}")
        self.stdout.write(f"Skipped: {skipped}")
        self.stdout.write(f"Errors: {errors}")
