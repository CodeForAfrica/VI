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
        articles = MediaNarrative.objects.filter(
            # Prerequisites must exist
            (Q(article_text__isnull=False) & ~Q(article_text='')) &
            (Q(target_country__isnull=False) & ~Q(target_country='')) &
            (Q(inferred_actor__isnull=False) & ~Q(inferred_actor=''))
            # AND at least one ML-derived field is missing
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

        # --- MAIN PROCESSING LOOP ---
        for article in articles:
            try:
                self.stdout.write(f"\n📄 Processing ID: {article.id}")
                self.stdout.write(f"   URL: {article.url[:60]}...")

                # --- ARTICLE TEXT EXTRACTION (Using trafilatura) ---
                # Determine the article text to use
                # Since the filter guarantees article_text exists and is not empty, we can use it directly
                # unless --skip-extraction is True and it's still needed for some reason (unlikely given filter).
                # The filter above ensures article_text is present.
                article_text = article.article_text # Should be present due to filter
                self.stdout.write(f"   📝 Using {len(article_text)} char text from DB.")

                # --- TARGET COUNTRY & INFERRED ACTOR DERIVATION & VALIDATION ---
                # These fields should now be available based on the filter
                target_country = article.target_country # Should be present due to filter
                inferred_actor = article.inferred_actor # Should be present due to filter

                # --- VALIDATE DERIVED VALUES (Optional but good practice) ---
                # Check if the retrieved values are in the expected lists.
                target_country_valid = target_country and any(tc.lower() in target_country.lower() for tc in valid_countries)
                inferred_actor_valid = inferred_actor and any(ia.lower() in inferred_actor.lower() for ia in valid_actors)

                if not target_country_valid:
                    # Log why it might be missing or not standard
                    if not target_country:
                        self.stdout.write(self.style.ERROR(f"   ❌ Target country is unexpectedly empty for {article.id} despite filter."))
                    else:
                        self.stdout.write(self.style.WARNING(f"   ⚠️ Retrieved target country '{target_country}' for {article.id} is not in the standard list: {valid_countries}."))
                    # Decide: Skip, Continue, or Log - Here we continue but log
                    # skipped += 1 # Uncomment if skipping is desired for invalid country
                    # continue
                if not inferred_actor_valid:
                    # Log why it might be missing or not standard
                    if not inferred_actor:
                        self.stdout.write(self.style.ERROR(f"   ❌ Inferred actor is unexpectedly empty for {article.id} despite filter."))
                    else:
                        self.stdout.write(self.style.WARNING(f"   ⚠️ Retrieved inferred actor '{inferred_actor}' for {article.id} is not in the standard list: {valid_actors}."))
                    # Decide: Skip, Continue, or Log - Here we continue but log
                    # skipped += 1 # Uncomment if skipping is desired for invalid actor
                    # continue

                # Final check if required fields are available after retrieval/filtering
                # This is mostly redundant now due to the filter, but acts as a safety net.
                if not target_country or not inferred_actor:
                    self.stdout.write(self.style.ERROR(f"⚠️ Skipping {article.id}: Missing required target_country ({target_country}) or inferred_actor ({inferred_actor}) after retrieval."))
                    skipped += 1
                    continue
                self.stdout.write(f"   ✅ Valid: {target_country} | Actor: {inferred_actor}")

                # --- ML INFERENCE (using the SINGLE instance created earlier) ---
                if not skip_ml:
                    # The ml_service instance will use its internal caching
                    # to avoid re-downloading models if they were already loaded
                    # during the processing of a previous article in this loop.
                    # It should prioritize local models over S3 downloads.
                    self.stdout.write("   🤖 Performing ML inference...")
                    results = ml_service.perform_inference(article_text)

                    strategic_intent = results['strategic_intent']
                    tone = results['tone']
                    confidence = results['confidence']
                    lang_detect = results['lang_detect']
                    # use_afrolm, strategic_intent_conf, strategic_intent_source are also available

                    self.stdout.write(f"   🧠 Intent: {strategic_intent} | Tone: {tone} | Conf: {confidence:.2f}")
                else:
                    # Fallback values if ML is skipped
                    strategic_intent = article.strategic_intent or 'Unknown'
                    tone = article.tone or 'neutral'
                    confidence = 0.0
                    lang_detect = article.lang_detect or 'en'
                    self.stdout.write(f"   🧠 ML Skipped, using fallbacks: Intent: {strategic_intent}, Tone: {tone}")

                # --- CALCULATE VULNERABILITY INDEX (using the SINGLE instance's cached methods) ---
                if not skip_ml:
                    self.stdout.write("   📊 Calculating Vulnerability Index...")
                    vulnerability_index = ml_service.calculate_vulnerability_index(
                        strategic_intent,
                        tone,
                        target_country,
                        inferred_actor,
                        confidence
                    )
                    self.stdout.write(f"   📊 Vulnerability Index: {vulnerability_index}")
                else:
                    vulnerability_index = article.vulnerability_index or 0.0
                    self.stdout.write(f"   📊 VI Skipped, using fallback: {vulnerability_index}")

                                # --- SAVE TO DATABASE (or dry run) ---
                if dry_run:
                    self.stdout.write(self.style.WARNING(f"   [DRY RUN] Would save:"))
                    self.stdout.write(f"       - article_text: {len(article_text)} chars")
                    self.stdout.write(f"       - target_country: {target_country}")
                    self.stdout.write(f"       - inferred_actor: {inferred_actor}")
                    self.stdout.write(f"       - strategic_intent: {strategic_intent}")
                    self.stdout.write(f"       - tone: {tone}")
                    self.stdout.write(f"       - vulnerability_index: {vulnerability_index}")
                    # Simulate saving logic
                    processed += 1
                    # CORRECTED LINE: Removed the extra ')'
                    self.stdout.write(self.style.WARNING(f"   [DRY RUN] Processed ID {article.id}"))
                else:
                    # Update the article object with results
                    # article_text was already present or derived, no need to update here
                    # Update target_country and inferred_actor if they were derived in this loop (they weren't in this version, taken from DB)
                    # article.target_country = target_country # Already in DB
                    # article.inferred_actor = inferred_actor # Already in DB
                    article.strategic_intent = strategic_intent
                    article.tone = tone
                    article.confidence = confidence
                    article.lang_detect = lang_detect
                    article.vulnerability_index = vulnerability_index
                    article.ml_processed_at = timezone.now() # Add timestamp if desired

                    # Save the updated article to the database
                    article.save()

                    self.stdout.write(f"   ✅ Saved: ID {article.id}")
                    processed += 1

            except Exception as e:
                logger.error(f"❌ Error processing article ID {article.id}: {e}")
                self.stdout.write(self.style.ERROR(f"   ❌ Error: {str(e)[:100]}"))
                errors += 1

        # --- CRITICAL CHANGE: Cleanup AFTER the loop ---
        # Call cleanup once at the very end of the command execution
        # This ensures any temporary files or resources held by the service are released.
        self.stdout.write("Cleaning up ML Inference Service resources...")
        ml_service.cleanup()
        self.stdout.write("Cleanup complete.")

        # Summary
        self.stdout.write(self.style.SUCCESS(f"\n--- Pipeline Complete ---"))
        self.stdout.write(f"Processed: {processed}")
        self.stdout.write(f"Skipped: {skipped}")
        self.stdout.write(f"Errors: {errors}")
