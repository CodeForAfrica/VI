import logging
import trafilatura
import django.utils.timezone
from django.core.management.base import BaseCommand
from dashboard.models import MediaNarrative
from dashboard.services.ml_inference_service import get_ml_service
import cloudscraper
from django.db.models import Q

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Runs the end-to-end ML pipeline: Extraction -> Validation -> Target/Actor Extraction -> ML Inference -> Scoring -> Storage'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Test without saving')
        parser.add_argument('--limit', type=int, default=50, help='Number of articles to process')
        parser.add_argument('--skip-extraction', action='store_true', help='Skip text extraction')
        parser.add_argument('--skip-ml', action='store_true', help='Skip ML inference')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        limit = options['limit']
        skip_extraction = options['skip_extraction']
        skip_ml = options['skip_ml']
        ml_service = get_ml_service()
        
        scraper = cloudscraper.create_scraper()
        
        self.stdout.write(self.style.SUCCESS(f"--- Starting Full Pipeline (Dry Run: {dry_run}) ---"))
        self.stdout.write(f"Limit: {limit} articles")
        
        # Step 1: Get articles that need processing (UNLABELED only)
        articles = MediaNarrative.objects.filter(
            Q(strategic_intent__isnull=True) |
            Q(tone__isnull=True) |
            Q(target_country__isnull=True) |
            Q(inferred_actor__isnull=True)
        )
        
        # ✅ Add debug logging BEFORE filtering
        total_articles = MediaNarrative.objects.count()
        labeled_articles = MediaNarrative.objects.filter(
            Q(strategic_intent__isnull=False) &
            Q(tone__isnull=False) &
            Q(target_country__isnull=False) &
            Q(inferred_actor__isnull=False)
        ).count()
        unlabeled_articles = articles.count()
        
        self.stdout.write(f"📊 Total articles in RDS: {total_articles}")
        self.stdout.write(f"✅ Already labeled: {labeled_articles}")
        self.stdout.write(f"🔍 New unlabeled to process: {unlabeled_articles}")
        
        # ✅ Add relevance check BEFORE fetching (only for articles with values)
        valid_countries = ['senegal', 'drc', 'ethiopia', 'cote d\'ivoire', 'ivory coast', 'south africa']
        valid_actors = ['china', 'france', 'russia', 'usa', 'saudi', 'turkey', 'uae', 'israel', 'iran', 'rwanda']
        
        # Filter by valid target_country (only if NOT NULL)
        articles = articles.filter(
            Q(target_country__isnull=True) |
            Q(target_country__iexact='senegal') |
            Q(target_country__iexact='drc') |
            Q(target_country__iexact='ethiopia') |
            Q(target_country__iexact='cote d\'ivoire') |
            Q(target_country__iexact='ivory coast') |
            Q(target_country__iexact='south africa')
        )
        
        # Filter by valid inferred_actor (only if NOT NULL)
        articles = articles.filter(
            Q(inferred_actor__isnull=True) |
            Q(inferred_actor__iexact='china') |
            Q(inferred_actor__iexact='france') |
            Q(inferred_actor__iexact='russia') |
            Q(inferred_actor__iexact='usa') |
            Q(inferred_actor__iexact='saudi') |
            Q(inferred_actor__iexact='turkey') |
            Q(inferred_actor__iexact='uae') |
            Q(inferred_actor__iexact='israel') |
            Q(inferred_actor__iexact='iran') |
            Q(inferred_actor__iexact='rwanda')
        )
        
        # Get unique articles and limit
        articles = articles.distinct()[:limit]
        
        total_count = articles.count()
        self.stdout.write(f"Found {total_count} articles to process")
        
        if total_count == 0:
            self.stdout.write(self.style.WARNING("No articles found needing processing"))
            return
        
        processed = 0
        skipped = 0
        errors = 0
        
        for article in articles:
            try:
                self.stdout.write(f"\n📄 Processing ID: {article.id}")
                self.stdout.write(f"   URL: {article.url[:60]}...")
                
                # Step 2: Extract full article text from URL
                if not skip_extraction:
                    try:
                        response = scraper.get(article.url, timeout=30)
                        
                        if response.status_code == 200:
                            text = trafilatura.extract(response.text)
                            
                            if text and len(text.strip()) >= 50:
                                self.stdout.write(self.style.SUCCESS(f"✅ Extracted {len(text)} characters"))
                            else:
                                self.stdout.write(self.style.ERROR(f"⚠️ Skipping {article.id}: Extraction failed or empty"))
                                skipped += 1
                                continue
                        else:
                            self.stdout.write(self.style.ERROR(f"⚠️ Skipping {article.id}: HTTP {response.status_code}"))
                            skipped += 1
                            continue
                            
                    except Exception as e:
                        self.stdout.write(self.style.ERROR(f"⚠️ Skipping {article.id}: Error {str(e)[:50]}"))
                        skipped += 1
                        continue
                else:
                    text = article.article_text
                
                # Step 3: Get target country from MediaCloud DB
                target_country = article.target_country or 'Unknown'
                
                # Step 3.1: Get inferred actor (3-step priority)
                inferred_actor = article.inferred_actor
                
                if not inferred_actor or inferred_actor == 'Unknown':
                    media_outlet = article.media_outlet or ''
                    inferred_actor = ml_service.get_actor_from_media_outlet(media_outlet)
                
                if not inferred_actor or inferred_actor == 'Unknown':
                    entities = ml_service.extract_entities_from_content(text)
                    extracted_orgs = entities.get('organizations', [])
                    inferred_actor = ml_service.extract_actor_from_content(text, organizations=extracted_orgs)
                
                if not inferred_actor or inferred_actor == 'Unknown':
                    inferred_actor = article.inferred_actor or 'Unknown'
                
                # Step 3.2: VALIDATE (handle case-insensitive)
                if target_country.lower().replace(' ', '') not in [c.replace(' ', '') for c in valid_countries]:
                    self.stdout.write(self.style.WARNING(f"⚠️ Skipping {article.id}: Target={target_country} (not in valid list)"))
                    skipped += 1
                    continue
                
                if inferred_actor.lower().replace(' ', '') not in [a.replace(' ', '') for a in valid_actors]:
                    self.stdout.write(self.style.WARNING(f"⚠️ Skipping {article.id}: Actor={inferred_actor} (not in valid list)"))
                    skipped += 1
                    continue
                
                self.stdout.write(f"   ✅ Valid: {target_country} | Actor: {inferred_actor}")
                
                # Step 4: ML Inference (if not skipped)
                if not skip_ml:
                    results = ml_service.perform_inference(text)
                    
                    strategic_intent = results['strategic_intent']
                    tone = results['tone']
                    confidence = results['confidence']
                    lang_detect = results['lang_detect']
                    
                    self.stdout.write(f"   🧠 Intent: {strategic_intent} | Tone: {tone} | Conf: {confidence:.2f}")
                else:
                    strategic_intent = article.strategic_intent or 'Unknown'
                    tone = article.tone or 'neutral'
                    confidence = 0.0
                    lang_detect = article.lang_detect or 'en'
                
                # Step 5: Calculate Vulnerability Index
                if not skip_ml:
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
                
                # Step 6: Save to database (or dry run)
                if dry_run:
                    self.stdout.write(self.style.WARNING(f"   [DRY RUN] Would save:"))
                    self.stdout.write(f"       - article_text: {len(text)} chars")
                    self.stdout.write(f"       - strategic_intent: {strategic_intent}")
                    self.stdout.write(f"       - tone: {tone}")
                    self.stdout.write(f"       - vulnerability_index: {vulnerability_index}")
                else:
                    article.article_text = text
                    article.target_country = target_country
                    article.inferred_actor = inferred_actor
                    article.strategic_intent = strategic_intent
                    article.tone = tone
                    article.confidence = confidence
                    article.lang_detect = lang_detect
                    article.vulnerability_index = vulnerability_index
                    article.ml_processed_at = django.utils.timezone.now()
                    article.save()
                    
                    self.stdout.write(self.style.SUCCESS(f"   ✅ Saved: ID {article.id}"))
                
                processed += 1
                
            except Exception as e:
                logger.error(f"❌ Error processing {article.id}: {e}")
                self.stdout.write(self.style.ERROR(f"   ❌ Error: {str(e)[:100]}"))
                errors += 1
        
        # Cleanup
        ml_service.cleanup()
        
        # Summary
        self.stdout.write(self.style.SUCCESS(f"\n--- Pipeline Complete ---"))
        self.stdout.write(f"Processed: {processed}")
        self.stdout.write(f"Skipped: {skipped}")
        self.stdout.write(f"Errors: {errors}")
