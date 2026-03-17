import time
from django.core.management.base import BaseCommand
from django.db import transaction, connection
from dashboard.models import MediaNarrative
# Use your existing service helper
from dashboard.services.ml_inference_service import get_ml_service
# Use the mapping logic we finalized
from dashboard.management.commands.update_vulnerability_indexes import map_raw_intent_to_contextual

class Command(BaseCommand):
    help = 'Ultra-fast local inference using the Ensemble ML Service (MPS Optimized)'

    def handle(self, *args, **options):
        # 1. Initialize the Service
        # Note: Speed boost depends on the .to(self.device) changes in your MLInferenceService
        self.stdout.write("📦 Initializing Ensemble ML Service...")
        start_time = time.time()
        ml_service = get_ml_service() 
        
        # 2. Get Pending Articles (Wipe intent in shell first to redo all)
        articles_query = MediaNarrative.objects.filter(
            strategic_intent__isnull=True,
            article_text__isnull=False
        ).exclude(article_text='')

        total = articles_query.count()
        self.stdout.write(self.style.SUCCESS(f"🧐 Found {total} articles to process."))

        if total == 0:
            self.stdout.write(self.style.WARNING("⚠️ No pending articles. If redoing all, wipe the intent field first."))
            return

        results_to_update = []
        batch_size = 50 

        # 3. Processing Loop
        for i, article in enumerate(articles_query):
            try:
                # The service handles the 5 models, the label_encoder, and the device (MPS/CPU)
                inference_result = ml_service.perform_inference(article.article_text)

                # TRACKING: Print ID every 10 articles
                if i % 10 == 0:
                    elapsed = time.time() - start_time
                    avg_speed = (i + 1) / elapsed
                    remaining = (total - (i + 1)) / avg_speed if i > 0 else 0
                    
                    self.stdout.write(
                        f"🔍 Processing ID: {article.id} | Progress: {i+1}/{total} "
                        f"({(i+1)/total*100:.1f}%) | Est. Remaining: {remaining/60:.1f} mins"
                    )
                    
                # Get the raw intent from the ensemble/LLM
                raw_intent = inference_result.get('strategic_intent', 'Unknown')
                
                # Use our finalized Contextual Mapping logic
                final_intent = map_raw_intent_to_contextual(raw_intent) or "Other"

                # Update the article object in memory
                article.strategic_intent = final_intent
                article.tone = inference_result.get('tone', 'Factual')
                article.confidence = inference_result.get('confidence', 0.5)
                # Mark source so you know it was processed via the local MPS run
                article.prediction_source = inference_result.get('source', 'ensemble_mps_local')

                results_to_update.append(article)

                # 4. Batch Save to RDS
                if len(results_to_update) >= batch_size:
                    with transaction.atomic():
                        MediaNarrative.objects.bulk_update(
                            results_to_update, 
                            ['strategic_intent', 'tone', 'confidence', 'prediction_source']
                        )
                    self.stdout.write(self.style.SUCCESS(f"📈 Batch Saved: {i+1} articles synced to RDS."))
                    results_to_update = []
                    # Keep RDS connection healthy across long processing runs
                    connection.close_if_unusable_or_obsolete()

            except Exception as e:
                self.stderr.write(self.style.ERROR(f"❌ ID {article.id} failed: {e}"))

        # Final Save for remaining records
        if results_to_update:
            with transaction.atomic():
                MediaNarrative.objects.bulk_update(
                    results_to_update, 
                    ['strategic_intent', 'tone', 'confidence', 'prediction_source']
                )
        
        end_time = time.time()
        total_duration = (end_time - start_time) / 60
        self.stdout.write(self.style.SUCCESS(f"🎉 Finished! {total} articles processed in {total_duration:.2f} minutes."))
