# dashboard/management/commands/fill_missing_intents.py
import time
from django.core.management.base import BaseCommand
from django.db import transaction
from dashboard.models import MediaNarrative
# Use your existing service helper
from dashboard.services.ml_inference_service import get_ml_service
# Use the mapping logic from utils (assuming map_to_canonical_intent is the correct function name)
from dashboard.utils import map_to_canonical_intent # Import the mapping function
from django.db.models import Q # Import Q for complex queries

class Command(BaseCommand):
    help = 'Runs ML inference to fill missing strategic_intent values in MediaNarrative'

    def add_arguments(self, parser):
        # Optional argument to specify number of articles to process (for testing)
        parser.add_argument(
            '--limit',
            type=int,
            help='Limit the number of articles to process (useful for testing)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Simulate the process without actually updating the database',
        )

    def handle(self, *args, **options):
        import pandas as pd # Ensure pandas is available

        # Get the ML service instance
        self.stdout.write("📦 Initializing ML Inference Service...")
        start_time = time.time()
        ml_service = get_ml_service() 

        # Build the queryset to find articles with missing strategic_intent
        # NOW CHECKS FOR BOTH NULL AND EMPTY STRING
        articles_query = MediaNarrative.objects.filter(
            Q(strategic_intent__isnull=True) | Q(strategic_intent='') # Checks for NULL or empty string
        ).exclude(
            article_text__isnull=True # Exclude if article_text is NULL too
        ).exclude(
            article_text='' # Exclude if article_text is empty string
        ).exclude(
            article_text__iexact='no content available' # Exclude if article_text is a default placeholder
        )

        # Apply limit if specified (useful for testing)
        limit = options.get('limit')
        if limit:
            articles_query = articles_query[:limit]

        total = articles_query.count()
        self.stdout.write(self.style.SUCCESS(f"🧐 Found {total} articles with missing strategic_intent to process."))

        if total == 0:
            self.stdout.write(self.style.WARNING("No articles found requiring processing. Exiting."))
            return

        # Dry run check
        if options.get('dry_run'):
            self.stdout.write(self.style.WARNING("*** DRY RUN MODE ***"))
            self.stdout.write(self.style.WARNING("Database will NOT be modified."))
            # Just print a few IDs to confirm the query works
            sample_ids = list(articles_query.values_list('id', flat=True)[:5])
            self.stdout.write(f"Sample IDs that would be processed: {sample_ids}")
            return

        # Storage for results to update in bulk
        results_to_update = []
        backup_data = [] # For optional CSV backup
        backup_file = f"inference_backup_missing_intents_{int(time.time())}.csv"

        # 3. Processing Loop
        processed_count = 0
        failed_count = 0
        
        for i, article in enumerate(articles_query):
            try:
                # Run inference on the article text using the CORRECT method
                # perform_inference returns a dictionary
                inference_result = ml_service.perform_inference(article.article_text) # <-- USE perform_inference

                if i % 10 == 0: # Print progress every 10 articles
                    elapsed = time.time() - start_time
                    avg_speed = (i + 1) / elapsed
                    remaining = (total - (i + 1)) / avg_speed if avg_speed > 0 else 0
                    self.stdout.write(f"🔍 ID: {article.id} | Progress: {i+1}/{total} | Est. Remaining: {remaining/60:.1f} mins")

                # Extract raw prediction and apply canonical mapping
                raw_intent = inference_result.get('strategic_intent', 'Unknown') # Get raw intent from result dict, default to 'Unknown'
                canonical_intent = map_to_canonical_intent(raw_intent) # Apply the mapping function

                # Update the article object in memory with the results from the inference
                article.strategic_intent = canonical_intent # Save the canonical form
                article.confidence = inference_result.get('confidence', 0.0) # Update confidence from result dict
                article.tone = inference_result.get('tone', 'Factual') # Update tone from result dict
                # Optionally update other fields like inferred_actor, target_country if needed
                # article.inferred_actor = inference_result.get('inferred_actor', article.inferred_actor) # Keep original if not found
                # article.target_country = inference_result.get('target_country', article.target_country) # Keep original if not found

                results_to_update.append(article)
                processed_count += 1
                
                # Update Backup List
                backup_data.append({
                    'id': article.id,
                    'raw_intent': raw_intent,
                    'canonical_intent': canonical_intent,
                    'confidence': article.confidence,
                    'tone': article.tone, # Add tone to backup if updated
                    'status': 'SUCCESS'
                })

                # LOCAL BACKUP ONLY (Every 500) - No RDS call here
                if (i + 1) % 500 == 0:
                    pd.DataFrame(backup_data).to_csv(backup_file, index=False)
                    self.stdout.write(self.style.WARNING(f"💾 Safety Backup saved to {backup_file}"))

            except Exception as e:
                # Log the error for this specific article, increment failed counter, continue processing
                self.stderr.write(self.style.ERROR(f"❌ Error processing article ID {article.id}: {e}"))
                failed_count += 1
                # Optionally add failed record to backup too
                backup_data.append({
                    'id': article.id,
                    'error': str(e),
                    'status': 'FAILED'
                })
                continue # Move to the next article

        # 4. FINAL MASSIVE SAVE (Outside the loop)
        if results_to_update:
            self.stdout.write(self.style.SUCCESS(f"🏁 Inference complete. Syncing {len(results_to_update)} articles to the database..."))
            
            # Final CSV save (CORRECTED)
            if backup_data: # <-- CORRECTED: Check if backup_data list is not empty, added ':'
                pd.DataFrame(backup_data).to_csv(backup_file, index=False)
                self.stdout.write(f"💾 Final backup saved to {backup_file}")

            # Use bulk_update to efficiently save all changes
            # Chunked save might be needed for very large datasets, but bulk_update often handles this well internally
            chunk_size = 1000
            for j in range(0, len(results_to_update), chunk_size): # <-- CORRECTED: Added missing ']' and ':'
                chunk = results_to_update[j : j + chunk_size]
                with transaction.atomic():
                    # Bulk update the specified fields for the chunk
                    # Include 'tone' and 'confidence' if you updated them above
                    fields_to_update = ['strategic_intent', 'confidence', 'tone'] # Add other fields updated above if any
                    MediaNarrative.objects.bulk_update(
                        chunk,
                        fields_to_update,
                        batch_size=chunk_size # Use batch_size argument of bulk_update
                    )
                self.stdout.write(f"✅ Synced chunk {j//chunk_size + 1} ({len(chunk)} articles)")

            self.stdout.write(self.style.SUCCESS(f"🎉 Bulk update of {len(results_to_update)} articles completed successfully."))
        else:
            self.stdout.write(self.style.WARNING("No articles were successfully processed for update."))

        # Final Summary
        end_time = time.time()
        duration_minutes = (end_time - start_time) / 60
        self.stdout.write(self.style.NOTICE(f"--- SUMMARY ---"))
        self.stdout.write(self.style.NOTICE(f"Total attempted: {total}"))
        self.stdout.write(self.style.NOTICE(f"Successfully processed: {processed_count}"))
        self.stdout.write(self.style.NOTICE(f"Failed to process: {failed_count}"))
        self.stdout.write(self.style.NOTICE(f"Duration: {duration_minutes:.2f} minutes"))
        self.stdout.write(self.style.SUCCESS(f"🎉 Command completed."))
