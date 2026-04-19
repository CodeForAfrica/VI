import time
from django.core.management.base import BaseCommand
from django.db import transaction, connection
from dashboard.models import MediaNarrative
from dashboard.services.ml_inference_service import get_ml_service
from dashboard.management.commands.update_vulnerability_indexes import map_raw_intent_to_contextual

class Command(BaseCommand):
    help = 'Ultra-fast local inference using the Ensemble ML Service (MPS Optimized) - PPI Compliant'

    def handle(self, *args, **options):
        import pandas as pd
        self.stdout.write("📦 Initializing Ensemble ML Service...")
        start_time = time.time()
        ml_service = get_ml_service() 
        
        articles_query = MediaNarrative.objects.filter(
            strategic_intent__isnull=True,
            article_text__isnull=False
        ).exclude(article_text='')

        total = articles_query.count()
        self.stdout.write(self.style.SUCCESS(f"🧐 Found {total} articles to process."))

        # PPI CONFIG - Set exact 15k anchor cutoff
        ANCHOR_CUTOFF_ID = 45119  

        results_to_update = []
        backup_data = []
        backup_file = "inference_backup_ppi.csv"

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

                # 🚫 FILTER: Skip invalid actor/target
                actor = article.inferred_actor
                target = article.target_country
                if not actor or actor.lower() in ['unknown', 'none', 'null', '']: continue
                if not target or target.lower() in ['unknown', 'none', 'null', '']: continue

                # ✅ PPI-Compliant Updates
                article.strategic_intent = final_intent
                article.tone = inference_result.get('tone', 'Factual')
                article.confidence = inference_result.get('confidence', 0.5)
                article.prediction_source = inference_result.get('source', 'ensemble_mps_local')
                
                # 🎓 PPI ANCHOR LOGIC (15k only)
                if article.id <= ANCHOR_CUTOFF_ID:
                    article.is_anchor = True      # Labeled sample
                    self.stdout.write(self.style.SUCCESS(f"⚓ ANCHOR #{article.id}"))
                else:
                    article.is_anchor = False     # PPI reference
                    self.stdout.write(self.style.WARNING(f"📊 REFERENCE #{article.id}"))

                results_to_update.append(article)
                backup_data.append({
                    'id': article.id,
                    'intent': final_intent,
                    'tone': article.tone,
                    'conf': article.confidence,
                    'is_anchor': article.is_anchor
                })

                if (i + 1) % 500 == 0:
                    pd.DataFrame(backup_data).to_csv(backup_file, index=False)
                    self.stdout.write(self.style.WARNING(f"💾 Backup: {backup_file}"))

            except Exception as e:
                self.stderr.write(self.style.ERROR(f"❌ ID {article.id} failed: {e}"))

        if results_to_update:
            self.stdout.write(self.style.SUCCESS(f"🏁 Syncing {len(results_to_update)} to RDS..."))
            pd.DataFrame(backup_data).to_csv(backup_file, index=False)

            chunk_size = 1000
            for j in range(0, len(results_to_update), chunk_size):
                chunk = results_to_update[j : j + chunk_size]
                with transaction.atomic():
                    MediaNarrative.objects.bulk_update(
                        chunk, 
                        ['strategic_intent', 'tone', 'confidence', 'prediction_source', 'is_anchor']
                    )
                self.stdout.write(f"✅ Chunk {j//chunk_size + 1}")

        end_time = time.time()
        self.stdout.write(self.style.SUCCESS(f"🎉 PPI Pipeline complete: {(end_time - start_time) / 60:.2f} mins"))
