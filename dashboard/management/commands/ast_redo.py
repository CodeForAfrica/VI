import re
from django.core.management.base import BaseCommand
from django.db import connection
from your_app.models import StrategicArticle # Replace with your actual app name
from your_app.services import MLInferenceService 

class Command(BaseCommand):
    help = 'Fast batch update for strategic intent'

    def handle(self, *args, **options):
        service = MLInferenceService()
        
        # 1. Get only the data that hasn't been processed yet
        articles = StrategicArticle.objects.filter(strategic_intent__isnull=True)
        total = articles.count()
        self.stdout.write(self.style.SUCCESS(f"🚀 Starting fast redo for {total} articles..."))

        results_to_update = []
        batch_size = 100

        for i, article in enumerate(articles):
            # THE HEARTBEAT: Prevents "Connection already closed" errors
            connection.close_if_unusable_or_obsolete()

            try:
                # 2. Run Inference (LLM + Model)
                intent, conf, notes = service.perform_strategic_intent_inference(article.text)
                
                # 3. Apply the MAPPING (Fix Trade Dominance -> Economic here)
                intent_mapping = {
                    "trade dominance": "Economic",
                    "cultural hegemony": "Sovereignty",
                    "reputation damage": "Sovereignty",
                    "political influence": "Sovereignty"
                }
                final_intent = intent_mapping.get(intent.lower().strip(), intent)

                # 4. Update object in memory
                article.strategic_intent = final_intent
                article.strategic_intent_conf = conf
                results_to_update.append(article)

                # 5. BULK UPDATE: Saves 100 articles in ONE database hit
                if len(results_to_update) >= batch_size:
                    StrategicArticle.objects.bulk_update(
                        results_to_update, 
                        ['strategic_intent', 'strategic_intent_conf']
                    )
                    self.stdout.write(f"✅ Processed {i+1}/{total} articles...")
                    results_to_update = []

            except Exception as e:
                self.stdout.write(self.style.ERROR(f"❌ Failed on ID {article.id}: {e}"))
                continue

        # Final save for any remaining records in the last batch
        if results_to_update:
            StrategicArticle.objects.bulk_update(results_to_update, ['strategic_intent', 'strategic_intent_conf'])

        self.stdout.write(self.style.SUCCESS("🎉 All records processed successfully!"))
