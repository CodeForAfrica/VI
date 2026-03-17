import logging
import re
from django.core.management.base import BaseCommand
from django.db import connection, transaction
from dashboard.models import MediaNarrative
from dashboard.services.ml_inference_service import get_ml_service

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'High-speed batch update for strategic intent using bulk_update'

    def handle(self, *args, **options):
        # 1. Initialize the service correctly using your project's helper
        self.stdout.write("Initializing ML Inference Service...")
        ml_service = get_ml_service()
        
        # 2. Get articles that need processing
        # We only look for articles missing strategic_intent to avoid re-doing work
        articles_query = MediaNarrative.objects.filter(
            strategic_intent__isnull=True,
            article_text__isnull=False
        ).exclude(article_text='')
        
        total_to_process = articles_query.count()
        self.stdout.write(self.style.SUCCESS(f"🚀 Found {total_to_process} articles to process."))

        if total_to_process == 0:
            return

        # 3. Processing Settings
        batch_size = 50  # Increased for speed
        results_to_update = []
        
        # Intent Mapping for cleaning labels
        intent_mapping = {
            "trade dominance": "Economic",
            "cultural hegemony": "Sovereignty",
            "reputation damage": "Sovereignty",
            "political influence": "Sovereignty",
            "economic dependency": "Economic"
        }

        # 4. The Loop
        # We fetch IDs only to iterate safely without memory issues
        article_ids = list(articles_query.values_list('id', flat=True))
        
        for i, article_id in enumerate(article_ids):
            # Heartbeat to prevent "Connection Closed"
            connection.close_if_unusable_or_obsolete()
            
            try:
                article = MediaNarrative.objects.get(id=article_id)
                
                # Perform Inference
                # (Matches your logic: Try batch-style if possible, or fall back to LLM)
                result_dict = ml_service.perform_inference(article.article_text)
                
                raw_intent = result_dict.get('strategic_intent', 'Neutral')
                conf = result_dict.get('strategic_intent_conf', 0.0)
                
                # Apply Mapping
                normalized_intent = raw_intent.lower().strip()
                final_intent = intent_mapping.get(normalized_intent, raw_intent)

                # Update memory object
                article.strategic_intent = final_intent
                article.confidence = conf
                article.prediction_source = result_dict.get('source', 'llm')
                
                # Get Tone and Risk Score
                article.tone = ml_service._get_tone(article.article_text)
                article.vulnerability_index = ml_service.calculate_vulnerability_index(
                    final_intent, article.tone, article.target_country, 
                    article.inferred_actor, conf
                )

                results_to_update.append(article)

                # 5. Bulk Update every 50 records
                if len(results_to_update) >= batch_size:
                    with transaction.atomic():
                        MediaNarrative.objects.bulk_update(
                            results_to_update, 
                            ['strategic_intent', 'confidence', 'prediction_source', 'tone', 'vulnerability_index']
                        )
                    self.stdout.write(f"✅ Batch Saved: {i+1}/{total_to_process} processed...")
                    results_to_update = []

            except Exception as e:
                self.stderr.write(self.style.ERROR(f"❌ Error on ID {article_id}: {e}"))
                continue

        # Final cleanup for remaining records
        if results_to_update:
            MediaNarrative.objects.bulk_update(results_to_update, ['strategic_intent', 'confidence', 'prediction_source', 'tone', 'vulnerability_index'])

        self.stdout.write(self.style.SUCCESS(f"🎉 Redo Complete! Processed {total_to_process} articles."))
        ml_service.cleanup()
