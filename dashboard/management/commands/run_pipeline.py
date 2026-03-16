# dashboard/management/commands/run_pipeline.py

import logging
import trafilatura
from django.core.management.base import BaseCommand
from django.db.models import Q
from dashboard.models import MediaNarrative
# Import the function that creates the service instance
from dashboard.services.ml_inference_service import get_ml_service
import cloudscraper
from django.utils import timezone
import re

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Runs the end-to-end ML pipeline on newly ingested articles: Extraction -> Target/Actor Extraction -> ML Inference -> Scoring -> Storage'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Test without saving')
        # Remove default=50 to process ALL articles unless explicitly limited
        parser.add_argument('--limit', type=int, default=None, help='Number of articles to process (default: process all)')
        parser.add_argument('--skip-extraction', action='store_true', help='Skip text extraction')
        parser.add_argument('--skip-ml', action='store_true', help='Skip ML inference')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        limit = options['limit']
        skip_extraction = options['skip_extraction']
        skip_ml = options['skip_ml']

        # --- CRITICAL CHANGE: Create MLInferenceService instance ONCE ---
        # Call the function ONCE to get the service instance.
        # This instance will handle caching internally.
        self.stdout.write("Initializing ML Inference Service...")
        ml_service = get_ml_service()
        self.stdout.write("ML Inference Service initialized.")

        # Initialize scraper
        scraper = cloudscraper.create_scraper()

        self.stdout.write(self.style.SUCCESS(f"--- Starting Full Pipeline (Dry Run: {dry_run}) ---"))
        # Clarify the limit in the output
        if limit is not None:
            self.stdout.write(f"Limit: {limit} articles (or fewer if less exist needing processing)")
        else:
            self.stdout.write(f"Limit: None (Processing all articles needing processing)")

        # Define valid lists here for validation if needed later in the loop
        valid_countries = ['senegal', 'drc', 'ethiopia', 'cote d\'ivoire', 'ivory coast', 'south africa']
        valid_actors = ['china', 'france', 'russia', 'usa', 'saudi', 'turkey', 'uae', 'israel', 'iran', 'rwanda']

        # Step 1: Get articles that need processing (NEWLY INGESTED, MISSING DERIVED FIELDS)
        # Filter for articles where key *derived* fields are missing.
        # 'id' or 'created_at'/'posting_time' indicates recent ingestion.
        # Filter based on missing derived fields.
        # Vulnerability index is calculated *after* other fields, so don't filter on it initially.
        # Focus on articles needing ML inference (strategic_intent, tone)
        # Also filter for articles that have the *prerequisites* for ML: article_text, target_country, inferred_actor
        TARGET_COUNTRIES = ["Senegal", "DRC", "Ethiopia", "Côte d'Ivoire", "South Africa"]

        # Then use this list in the filter query:
        articles = MediaNarrative.objects.filter(
            # Prerequisites must exist
            (Q(article_text__isnull=False) & ~Q(article_text='')) &
            (Q(target_country__isnull=False) & ~Q(target_country='')) &
            (Q(inferred_actor__isnull=False) & ~Q(inferred_actor=''))
            # AND target country is one of the desired ones
            &
            (Q(target_country__in=TARGET_COUNTRIES)) # <-- Use the canonical names list
            # AND at least one ML-derived field is missing (or remove this if re-processing all)
            &
            (
                (Q(strategic_intent__isnull=True) | Q(strategic_intent='')) # Needs ML
                |
                (Q(tone__isnull=True) | Q(tone='')) # Needs ML
                # Potentially also filter by vulnerability_index if it's calculated here and needs updating
                # | (Q(vulnerability_index__isnull=True) | Q(vulnerability_index='')) # Calculated later
            )
        )

        # Get unique articles and limit if specified
        articles = articles.distinct().order_by('id') # Order consistently
        if limit is not None: # Only apply limit if it was explicitly provided
             articles = articles[:limit]

        total_count = articles.count()
        self.stdout.write(f"Found {total_count} articles to process")

        if total_count == 0:
            self.stdout.write(self.style.WARNING("No articles found needing processing"))
            # Still call cleanup even if no articles, though it might be a no-op
            ml_service.cleanup()
            return

        processed = 0
        skipped = 0
        errors = 0

        # --- BATCH PROCESSING LOOP ---
        batch_size = 20
        articles_list = list(articles) 

        for i in range(0, len(articles_list), batch_size):
            chunk = articles_list[i : i + batch_size]
            chunk_texts = [a.article_text for a in chunk]
            
            self.stdout.write(f"\n📦 Processing Batch: {i//batch_size + 1} ({len(chunk)} articles)")

            # --- 1. THE BIG SPEED BOOST ---
            try:
                ml_results = ml_service.perform_strategic_intent_batch(chunk_texts)
            except Exception as e:
                self.stderr.write(self.style.ERROR(f"❌ Batch inference failed: {e}"))
                errors += len(chunk)
                continue

            # --- 2. INDIVIDUAL ARTICLE PROCESSING ---
            for article, (ml_intent, ml_conf) in zip(chunk, ml_results):
                try:
                    self.stdout.write(f"📄 Processing ID: {article.id}")
                    
                    target_country = article.target_country
                    inferred_actor = article.inferred_actor
                    
                    # Normalization
                    normalized_db_country = target_country.lower().replace("côte d'ivoire", "cote d'ivoire") if target_country else ""
                    
                    if normalized_db_country not in valid_countries:
                         self.stdout.write(self.style.WARNING(f"⚠️ Invalid country {target_country}"))
                         skipped += 1
                         continue

                    # --- 3. DECIDE: BATCH OR LLM FALLBACK ---
                    # We use 0.6 as a threshold for the model's confidence
                    if ml_conf < 0.6:
                        result_dict = ml_service.perform_inference(article.article_text)
                        strategic_intent = result_dict.get('strategic_intent', 'unknown')
                        si_confidence = result_dict.get('strategic_intent_conf', 0.0)
                        si_source = "llm"
                    else:
                        strategic_intent = ml_intent
                        si_confidence = ml_conf
                        si_source = "model"

                    # --- 4. SCORING & SAVING ---
                    tone = ml_service._get_tone(article.article_text)
                    vi_score = ml_service.calculate_vulnerability_index(
                        strategic_intent, tone, target_country, inferred_actor, si_confidence
                    )

                    if not dry_run:
                        article.strategic_intent = strategic_intent
                        article.tone = tone
                        article.confidence = si_confidence
                        article.prediction_source = si_source
                        article.vulnerability_index = vi_score
                        article.save()
                        processed += 1
                        self.stdout.write(self.style.SUCCESS(f"   ✅ Saved: ID {article.id} (Source: {si_source})"))
                    else:
                        self.stdout.write(f"   🧪 Dry Run: Would save ID {article.id}")

                except Exception as e:
                    self.stderr.write(f"   ❌ Error on ID {article.id}: {e}")
                    errors += 1

        # --- 5. CLEANUP & SUMMARY (CRITICAL) ---
        self.stdout.write("\nCleaning up ML Inference Service resources...")
        ml_service.cleanup()
        
        self.stdout.write(self.style.SUCCESS(f"\n--- Pipeline Complete ---"))
        self.stdout.write(f"Processed: {processed}")
        self.stdout.write(f"Skipped:   {skipped}")
        self.stdout.write(f"Errors:    {errors}")
