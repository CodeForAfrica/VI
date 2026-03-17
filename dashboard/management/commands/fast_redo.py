from django.core.management.base import BaseCommand
from django.db import transaction, connection
from dashboard.models import MediaNarrative
# Use your existing service helper
from dashboard.services.ml_inference_service import get_ml_service
# Use the mapping logic we finalized
from dashboard.management.commands.update_vulnerability_indexes import map_raw_intent_to_contextual

class Command(BaseCommand):
    help = 'Ultra-fast local inference using the Ensemble ML Service'

    def handle(self, *args, **options):
        # 1. Initialize the Service (This handles the ensemble loading for you)
        self.stdout.write("📦 Initializing Ensemble ML Service...")
        ml_service = get_ml_service() 
        
        # 2. Get Pending Articles
        articles_query = MediaNarrative.objects.filter(
            strategic_intent__isnull=True,
            article_text__isnull=False
        ).exclude(article_text='')

        total = articles_query.count()
        self.stdout.write(self.style.SUCCESS(f"🧐 Found {total} articles to process."))

        results_to_update = []
        batch_size = 50 

        # 3. Processing Loop
        for i, article in enumerate(articles_query):
            try:
                # The service handles the 5 models, the label_encoder, and the device (MPS/CPU)
                inference_result = ml_service.perform_inference(article.article_text)
                
                # Get the raw intent from the ensemble/LLM
                raw_intent = inference_result.get('strategic_intent', 'SocialFragility')
                
                # Use our finalized Contextual Mapping logic
                final_intent = map_raw_intent_to_contextual(raw_intent) or "SocialFragility"

                # Update the article object
                article.strategic_intent = final_intent
                article.tone = inference_result.get('tone', 'Factual')
                article.confidence = inference_result.get('confidence', 0.5)
                article.prediction_source = inference_result.get('source', 'ensemble')

                results_to_update.append(article)

                # 4. Batch Save
                if len(results_to_update) >= batch_size:
                    with transaction.atomic():
                        MediaNarrative.objects.bulk_update(
                            results_to_update, 
                            ['strategic_intent', 'tone', 'confidence', 'prediction_source']
                        )
                    self.stdout.write(f"📈 Progress: {i+1}/{total} saved...")
                    results_to_update = []
                    connection.close_if_unusable_or_obsolete()

            except Exception as e:
                self.stderr.write(f"❌ ID {article.id} failed: {e}")

        # Final Save
        if results_to_update:
            MediaNarrative.objects.bulk_update(
                results_to_update, 
                ['strategic_intent', 'tone', 'confidence', 'prediction_source']
            )
        
        self.stdout.write(self.style.SUCCESS("🎉 Finished! 13k articles processed."))
