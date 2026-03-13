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
        parser.add_argument('--limit', type=int, default=None, help='Number of articles to process')
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
        self.stdout.write(f"Limit: {limit} articles")

        # Define valid lists here for validation if needed later in the loop
        valid_countries = ['senegal', 'drc', 'ethiopia', 'cote d\'ivoire', 'ivory coast', 'south africa']
        valid_actors = ['china', 'france', 'russia', 'usa', 'saudi', 'turkey', 'uae', 'israel', 'iran', 'rwanda']

        # Step 1: Get articles that need processing (NEWLY INGESTED, MISSING DERIVED FIELDS)
        # Filter for articles where key *derived* fields are missing.
        # 'id' or 'created_at'/'posting_time' indicates recent ingestion.
        # Filter based on missing derived fields.
        # Vulnerability index is calculated *after* other fields, so don't filter on it initially.
        articles = MediaNarrative.objects.filter(
            # missing initially: article_text (needs extraction), strategic_intent, tone (need ML)
            # other missing: target_country, inferred_actor (need derivation)
            # Filter for missing article_text, strategic_intent, tone.
            # Target/inferred_actor derivation happens *within* the loop based on media_outlet and article_text.
            (Q(article_text__isnull=True) | Q(article_text='')) # Text is needed first for ML and target derivation
            |
            (Q(strategic_intent__isnull=True) | Q(strategic_intent='')) # Needs ML
            |
            (Q(tone__isnull=True) | Q(tone='')) # Needs ML
            # Add other conditions if needed, e.g., if vulnerability_index is calculated and stored
            # and is missing for new articles, but it's calculated *after* other fields.
            # | (Q(vulnerability_index__isnull=True) | Q(vulnerability_index='')) # Calculated later
        )

        # Get unique articles and limit
        articles = articles.distinct().order_by('id') # Order consistently
        if limit:
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
                article_text = article.article_text # Start with existing text

                # If text is missing and extraction is not skipped, try to get it from the URL
                if (not article_text or article_text.strip() == '') and not skip_extraction:
                    self.stdout.write("   🔍 Article text missing, attempting extraction...")
                    try:
                        response = scraper.get(article.url, timeout=30)

                        if response.status_code == 200:
                            # Use trafilatura to extract the main text
                            extracted_text = trafilatura.extract(response.text)

                            if extracted_text and len(extracted_text.strip()) >= 50: # Minimum length check
                                article_text = extracted_text
                                self.stdout.write(self.style.SUCCESS(f"✅ Extracted {len(article_text)} characters"))
                            else:
                                self.stdout.write(self.style.ERROR(f"⚠️ Skipping {article.id}: Extraction failed or text too short (< 50 chars)"))
                                skipped += 1
                                continue
                        else:
                            self.stdout.write(self.style.ERROR(f"⚠️ Skipping {article.id}: HTTP {response.status_code}"))
                            skipped += 1
                            continue

                    except Exception as e:
                        self.stdout.write(self.style.ERROR(f"⚠️ Skipping {article.id}: Extraction error {str(e)[:50]}"))
                        skipped += 1
                        continue
                elif (not article_text or article_text.strip() == '') and skip_extraction:
                     # No text available and extraction is skipped
                     self.stdout.write(self.style.ERROR(f"⚠️ Skipping {article.id}: No article_text available and extraction skipped"))
                     skipped += 1
                     continue
                # If article_text was already present, proceed

                # --- TARGET COUNTRY & INFERRED ACTOR DERIVATION & VALIDATION ---
                # These fields are derived NOW from the extracted text or known source info.
                # Start with existing values if any (might be from ingestion)
                target_country = article.target_country
                inferred_actor = article.inferred_actor

                # --- DERIVE TARGET COUNTRY from ARTICLE TEXT ---
                # This is the "from the context" part.
                # Implement logic to find country names/entities in the article_text.
                # Here, we implement a simple keyword search based on valid_countries.
                if not target_country or target_country.strip() == '':
                     self.stdout.write("   🔍 Deriving target country from article text...")
                     found_country = None
                     for country_name in valid_countries:
                         # Simple check, might need refinement (e.g., case-insensitive, whole words, NER)
                         if country_name.lower() in article_text.lower():
                             found_country = country_name.title() # Use title case for consistency
                             self.stdout.write(f"   ✅ Found country '{found_country}' in text.")
                             break
                     if found_country:
                         target_country = found_country
                     else:
                         self.stdout.write(self.style.ERROR(f"❌ Could not derive target country from text for {article.id}. Skipping."))
                         skipped += 1
                         continue # Cannot proceed without target country

                # --- DERIVE INFERRED ACTOR from MEDIA OUTLET ---
                # This is the "from media outlet column" part.
                # Use the ml_service function based on the article's media_outlet.
                if not inferred_actor or inferred_actor.strip() == '':
                    self.stdout.write("   🔍 Deriving inferred actor from media outlet...")
                    media_outlet = article.media_outlet or '' # Get the source outlet
                    inferred_actor_from_source = ml_service.get_actor_from_media_outlet(media_outlet)

                    if not inferred_actor_from_source or inferred_actor_from_source == 'Unknown':
                        self.stdout.write(self.style.ERROR(f"❌ Could not derive inferred actor from media outlet '{media_outlet}' for {article.id}. Skipping."))
                        skipped += 1
                        continue # Cannot proceed without inferred actor
                    else:
                        inferred_actor = inferred_actor_from_source
                        self.stdout.write(f"   ✅ Derived inferred_actor: {inferred_actor} from outlet: {media_outlet}")

                # --- VALIDATE DERIVED VALUES ---
                # Check if the derived values are in the expected lists.
                target_country_valid = target_country and any(tc.lower() in target_country.lower() for tc in valid_countries)
                inferred_actor_valid = inferred_actor and any(ia.lower() in inferred_actor.lower() for ia in valid_actors)
                
                if not target_country_valid:
                    # Log why it might be missing or not standard
                    if not target_country:
                        self.stdout.write(self.style.WARNING(f"   ⚠️ Target country is empty for {article.id}. Derivation might have failed or no match found in text."))
                    else:
                        self.stdout.write(self.style.WARNING(f"   ⚠️ Derived target country '{target_country}' for {article.id} is not in the standard list: {valid_countries}."))
                    # Decide: Skip, Continue, or Log - Here we continue but log
                    # skipped += 1 # Uncomment if skipping is desired for invalid country
                    # continue
                if not inferred_actor_valid:
                    # Log why it might be missing or not standard
                    if not inferred_actor:
                        self.stdout.write(self.style.WARNING(f"   ⚠️ Inferred actor is empty for {article.id}. Derivation from media outlet '{article.media_outlet}' might have failed."))
                    else:
                        self.stdout.write(self.style.WARNING(f"   ⚠️ Derived inferred actor '{inferred_actor}' for {article.id} is not in the standard list: {valid_actors}."))
                    # Decide: Skip, Continue, or Log - Here we continue but log
                    # skipped += 1 # Uncomment if skipping is desired for invalid actor
                    # continue
                
                # Final check if required fields are now available after derivation and validation
                # Even if they are not in the standard list, they might still be valid non-standard values.
                # to skip if they are empty/None after the derivation attempt.
                if not target_country or not inferred_actor:
                    # This check primarily catches the scenario where derivation itself failed (returned None/empty string)
                    # and was not caught earlier in the derivation logic itself.
                    # If derivation succeeded but produced a non-standard value, this check will pass.
                    self.stdout.write(self.style.ERROR(f"⚠️ Skipping {article.id}: Missing required target_country ({target_country}) or inferred_actor ({inferred_actor}) after derivation attempt."))
                    skipped += 1
                    continue
                self.stdout.write(f"   ✅ Valid: {target_country} | Actor: {inferred_actor}")

                # --- ML INFERENCE (using the SINGLE instance created earlier) ---
                if not skip_ml:
                    # The ml_service instance will use its internal caching
                    # to avoid re-downloading models if they were already loaded
                    # during the processing of a previous article in this loop.
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
                    # Only update article_text if it was extracted in this run
                    if article.article_text != article_text:
                        article.article_text = article_text
                    # Update target_country and inferred_actor derived here
                    article.target_country = target_country
                    article.inferred_actor = inferred_actor
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
