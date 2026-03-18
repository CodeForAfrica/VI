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
        import pandas as pd # Ensure pandas is available
        self.stdout.write("📦 Initializing Ensemble ML Service...")
        start_time = time.time()
        ml_service = get_ml_service() 
        
        articles_query = MediaNarrative.objects.filter(
            strategic_intent__isnull=True,
            article_text__isnull=False
        ).exclude(article_text='')

        total = articles_query.count()
        self.stdout.write(self.style.SUCCESS(f"🧐 Found {total} articles to process."))

        # Storage for the final run
        results_to_update = []
        backup_data = []
        backup_file = "inference_backup_15k.csv"

        # 3. Processing Loop
        for i, article in enumerate(articles_query):
            try:
                inference_result = ml_service.perform_inference(article.article_text)

                if i % 10 == 0:
                    elapsed = time.time() - start_time
                    avg_speed = (i + 1) / elapsed
                    remaining = (total - (i + 1)) / avg_speed if i > 0 else 0
                    self.stdout.write(f"🔍 ID: {article.id} | Progress: {i+1}/{total} | Est. Remaining: {remaining/60:.1f} mins")
                
                raw_intent = inference_result.get('strategic_intent', 'Unknown')
                final_intent = map_raw_intent_to_contextual(raw_intent) or "Other"

                # Update memory object
                article.strategic_intent = final_intent
                article.tone = inference_result.get('tone', 'Factual')
                article.confidence = inference_result.get('confidence', 0.5)
                article.prediction_source = inference_result.get('source', 'ensemble_mps_local')
                article.is_anchor = True # Setting your 15k anchor baseline

                results_to_update.append(article)
                
                # Update Backup List
                backup_data.append({
                    'id': article.id,
                    'intent': final_intent,
                    'tone': article.tone,
                    'conf': article.confidence
                })

                # LOCAL BACKUP ONLY (Every 500) - No RDS call here
                if (i + 1) % 500 == 0:
                    pd.DataFrame(backup_data).to_csv(backup_file, index=False)
                    self.stdout.write(self.style.WARNING(f"💾 Safety Backup saved to {backup_file}"))

            except Exception as e:
                self.stderr.write(self.style.ERROR(f"❌ ID {article.id} failed: {e}"))

        # 4. FINAL MASSIVE SAVE (Outside the loop)
        if results_to_update:
            self.stdout.write(self.style.SUCCESS(f"🏁 Inference complete. Syncing {len(results_to_update)} articles to RDS..."))
            
            # Final CSV save
            pd.DataFrame(backup_data).to_csv(backup_file, index=False)

            # Chunked save to avoid RDS timeouts
            chunk_size = 1000
            for j in range(0, len(results_to_update), chunk_size):
                chunk = results_to_update[j : j + chunk_size]
                with transaction.atomic():
                    MediaNarrative.objects.bulk_update(
                        chunk, 
                        ['strategic_intent', 'tone', 'confidence', 'prediction_source', 'is_anchor']
                    )
                self.stdout.write(f"✅ Synced chunk {j//chunk_size + 1}")
        
        end_time = time.time()
        self.stdout.write(self.style.SUCCESS(f"🎉 Done in {(end_time - start_time) / 60:.2f} mins."))
