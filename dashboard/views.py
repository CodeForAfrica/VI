# dashboard/views.py

from django.shortcuts import render
from django.core.cache import cache # <-- ADD THIS IMPORT IF NOT ALREADY PRESENT
from django.db.models import Q, Count, Avg
from django.core.paginator import Paginator
from .models import MediaNarrative, Journalist, MediaOutlet
from dashboard.services.summarizer import get_summary
from dashboard.services.ml_inference_service import get_ml_service # Changed to lazy loading function
import pandas as pd
import plotly.express as px
from math import isfinite
from django.http import HttpResponse, JsonResponse
from django.template.loader import get_template
from xhtml2pdf import pisa
from io import BytesIO
from datetime import datetime
import base64
import json
import logging
import urllib3
import matplotlib
matplotlib.use('Agg')  # Required for Django to prevent "main thread" GUI errors
import matplotlib.pyplot as plt
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from groq import Groq
from django.conf import settings
from django.db.models.functions import TruncMonth
from django.utils.dateparse import parse_date
import plotly.graph_objects as go
import os
import re

logger = logging.getLogger(__name__)

# =========================
# CONSTANTS
# =========================

COUNTRIES = [
    "Senegal", "Ethiopia", "Côte d'Ivoire", "DRC", "South Africa"
]
FOREIGN_ACTORS = [
    "US", "China", "France", "Russia", "UAE", "Saudi Arabia", "Turkey", "Israel", "Iran", "Rwanda"
]
# Each item must be a (Value, Label) pair
INTENT_CHOICES = [
    ('Economic', 'Economic'),
    ('Sovereignty', 'Sovereignty'),
    ('LGBTQ', 'LGBTQ'),
    ('Religious', 'Religious'),
    ('Military presence', 'Military presence'),
    ('Resource Dependency', 'Resource Dependency'),
    ('Social Fragility', 'Social Fragility'),
]

# =========================
# CHATBOT ASSISTANCE SYSTEM (Enhanced with Consistent Calculation)
# =========================
class DisinfoAnalysisChatbot:
    def __init__(self):
        # Initializing the Groq client with your specific Llama 4 model
        self.client = Groq(api_key=settings.GROQ_API_KEY)
        self.model = "meta-llama/llama-4-scout-17b-16e-instruct"

    def process_query(self, query):
        query_l = query.lower().strip()
        
        # Quick Relevance Check - Focus on foreign influence
        irrelevant = ['football', 'soccer', 'entertainment', 'music', 'celebrity', 'local sport', 'local entertainment']
        if any(word in query_l for word in irrelevant) and not any(actor in query_l for actor in ['france', 'china', 'usa', 'us', 'russia', 'united states', 'unitedstates']):
            return "I specialize in foreign influence analysis and vulnerability indices. I don't track local sports or entertainment unless they involve foreign actors."
    
        # Handle multiple questions about ANY country with foreign actor focus
        import re
        country_pattern = r'(?:around|about|for|on)\s+(senegal|drc|coted\'ivoire|cote d\'ivoire|cote ivoire|ivory coast|ethiopia|south africa|southafrica)'
        match = re.search(country_pattern, query_l, re.IGNORECASE)
        
        if match and ('how many' in query_l or 'analyze' in query_l or 'articles' in query_l):
            country_mentioned = match.group(1).lower()
            
            # EXACT database format matching
            db_country = None
            if 'south' in country_mentioned and 'africa' in country_mentioned:
                db_country = 'South Africa'
            elif country_mentioned in ['senegal', 'senegal']:
                db_country = 'Senegal'
            elif country_mentioned in ['drc', 'democratic republic of congo', 'congo']:
                db_country = 'DRC'
            elif any(x in country_mentioned for x in ['cote', 'ivoire', 'ivory']):
                db_country = 'Côte d\'Ivoire'
            elif country_mentioned in ['ethiopia', 'ethopia']:
                db_country = 'Ethiopia'
            
            if db_country:
                # COUNT articles for this country that mention foreign actors
                country_articles = MediaNarrative.objects.filter(
                    target_country__iexact=db_country
                ).exclude(
                    inferred_actor__in=['', 'local', 'Local', 'LOCAL', 'domestic', 'Domestic']
                ).count()
                
                # GET key narratives for this country WITH FOREIGN ACTORS
                country_narratives = MediaNarrative.objects.filter(
                    target_country__iexact=db_country
                ).exclude(
                    inferred_actor__in=['', 'local', 'Local', 'LOCAL', 'domestic', 'Domestic']
                ).exclude(
                    strategic_intent__in=['', None, 'unknown', 'Unknown']
                ).values('strategic_intent', 'inferred_actor').annotate(
                    count=Count('id')
                ).order_by('-count')[:5]
                
                narratives_list = [f"• {item['strategic_intent']} by {item['inferred_actor']}: {item['count']} articles" for item in country_narratives]
                narratives_str = "\n".join(narratives_list) if narratives_list else "• No foreign influence narratives identified"
                
                return (f"{db_country} Foreign Influence Analysis:\n"
                       f"• Total articles with foreign actor involvement: {country_articles:,}\n"
                       f"• Key foreign influence narratives:\n{narratives_str}")
    
        # Dashboard-specific queries
        if any(word in query_l for word in ['dashboard', 'interface', 'how to', 'help', 'navigate', 'filter']):
            return "Our dashboard analyzes foreign influence in African media. You can filter by country, foreign actor, or strategic intent. Each article has a vulnerability index score showing foreign influence risk."
    
        # Statistical queries
        if any(word in query_l for word in ['how many', 'count', 'total', 'number', 'statistics']):
            # Count only articles with foreign actor mentions
            total = MediaNarrative.objects.exclude(
                inferred_actor__in=['', 'local', 'Local', 'LOCAL', 'domestic', 'Domestic']
            ).exclude(article_text__icontains='football').count()
            return f"The database contains {total:,} articles analyzing foreign influence (excluding local content)."
    
        # Vulnerability index queries
        if any(word in query_l for word in ['vulnerability', 'index', 'score', 'risk']):
            # Calculate average for articles with foreign actors only
            avg_vulnerability = MediaNarrative.objects.exclude(
                inferred_actor__in=['', 'local', 'Local', 'LOCAL', 'domestic', 'Domestic']
            ).exclude(vulnerability_index__isnull=True).aggregate(Avg('vulnerability_index'))['vulnerability_index__avg']
            avg_str = f"{avg_vulnerability:.3f}" if avg_vulnerability else "0.000 (not yet calculated for all records)"
            return f"The vulnerability index measures foreign influence risk (0-1). Current average: {avg_str}"
    
        # Default AI analysis
        context = self.get_context_from_db(query)
        return self.get_insights_from_ai(query, context)
        
    def get_context_from_db(self, query):
        # Filter out irrelevant topics before sending context to the AI
        articles = MediaNarrative.objects.exclude(article_text__icontains='football').order_by('-posting_time')[:5]
        context = ""
        for art in articles:
            # Safety check for empty text fields
            text_snippet = (art.article_text[:200] + "...") if art.article_text else "No text content."
            context += f"Source: {art.media_outlet} | Country: {art.target_country} | Intent: {art.strategic_intent} | Text: {text_snippet}\n\n"
        return context

    def get_insights_from_ai(self, query, context):
        system_prompt = """
        You are an expert analyst explaining media narratives and vulnerability indices in Africa.
        Analyze the context provided and answer the query concisely. 
        Focus strictly on foreign influence and strategic narratives.
        """
        try:
            chat_completion = self.client.chat.completions.create(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Context:\n{context}\n\nQuery: '{query}'"}
                ],
                model=self.model,
                temperature=0.1,
            )
            return chat_completion.choices[0].message.content
        except Exception as e:
            return f"AI Error: {str(e)}"

# Instantiate the chatbot once
chatbot_instance = DisinfoAnalysisChatbot()

@csrf_exempt
@require_http_methods(["POST"])
def chatbot_response(request):
    try:
        data = json.loads(request.body)
        user_message = data.get('message', '').strip()
        
        # KEY FIX: Using 'reply' to match your JavaScript fetch expectation
        bot_reply = chatbot_instance.process_query(user_message)
        
        return JsonResponse({
            'reply': bot_reply, 
            'success': True
        })
    except Exception as e:
        return JsonResponse({
            'reply': f"Error: {str(e)}", 
            'success': False
        })

def calculate_contextual_score(target_country, foreign_actor, intent_filter=None):
    """Direct lookup from your CSV file - reads final_risk_by_actor_intent_country.csv"""
    # This function is already efficient as it reads the CSV once per call.
    # Consider caching its results based on inputs if called frequently.
    cache_key = f"cvi_{target_country}_{foreign_actor}_{intent_filter or 'none'}"
    cached_result = cache.get(cache_key)
    if cached_result:
        logger.info(f"Cache HIT for CVI: {cache_key}")
        return cached_result

    try:
        import pandas as pd
        import os
        current_dir = os.path.dirname(os.path.abspath(__file__))
        csv_file = os.path.join(current_dir, '..', 'final_risk_by_actor_intent_country.csv')
        if not os.path.exists(csv_file):
            logger.error(f"CSV file not found: {csv_file}")
            return 0.5, "Unknown"
        df = pd.read_csv(csv_file)
        country_mapping = {
            "south africa": "South Africa",
            "senegal": "Senegal",
            "drc": "DRC",
            "cote d'ivoire": "CoteIvoire",
            "cote ivoire": "CoteIvoire",
            "ivory coast": "CoteIvoire",
            "ethiopia": "Ethiopia"
        }
        actor_mapping = {
            "uae": "UAE",
            "china": "China",
            "france": "France",
            "us": "UnitedStates",
            "united states": "UnitedStates",
            "russia": "Russia",
            "saudi": "Saudi",
            "turkey": "Turkey",
            "israel": "Israel",
            "iran": "Iran"
        }
        formatted_country = country_mapping.get(target_country.lower(), target_country)
        formatted_actor = actor_mapping.get(foreign_actor.lower(), foreign_actor)
        matching_rows = df[(df['country'] == formatted_country) & (df['actor'] == formatted_actor)]

        if not matching_rows.empty:
            if intent_filter:
                specific_match = matching_rows[matching_rows['intent'].str.lower() == intent_filter.lower()]
                if not specific_match.empty:
                    row = specific_match.iloc[0]
                    result = float(row['FinalRisk']), row['intent']
                    cache.set(cache_key, result, timeout=60*60*24) # Cache for 24 hours
                    return result
            max_row = matching_rows.loc[matching_rows['FinalRisk'].idxmax()]
            max_score = max_row['FinalRisk']
            max_intent = max_row['intent']
            logger.info(f"Found max score {max_score} for {formatted_country}-{formatted_actor} in {max_intent}")
            result = float(max_score), max_intent
            cache.set(cache_key, result, timeout=60*60*24) # Cache for 24 hours
            return result
        else:
            matching_rows = df[
                (df['country'].str.lower() == formatted_country.lower()) &
                (df['actor'].str.lower() == formatted_actor.lower())
            ]
            if not matching_rows.empty:
                if intent_filter:
                    specific_match = matching_rows[matching_rows['intent'].str.lower() == intent_filter.lower()]
                    if not specific_match.empty:
                        row = specific_match.iloc[0]
                        result = float(row['FinalRisk']), row['intent']
                        cache.set(cache_key, result, timeout=60*60*24) # Cache for 24 hours
                        return result
                max_row = matching_rows.loc[matching_rows['FinalRisk'].idxmax()]
                max_score = max_row['FinalRisk']
                max_intent = max_row['intent']
                logger.info(f"Found case-insensitive max score {max_score} for {formatted_country}-{formatted_actor} in {max_intent}")
                result = float(max_score), max_intent
                cache.set(cache_key, result, timeout=60*60*24) # Cache for 24 hours
                return result

        logger.info(f"No score found for {target_country}-{foreign_actor} in CSV, using default")
        result = 0.5, "Unknown"
        cache.set(cache_key, result, timeout=60*60*24) # Cache for 24 hours
        return result
    except FileNotFoundError:
        logger.error(f"CSV file not found at: {csv_file}")
        result = 0.5, "Unknown"
        cache.set(cache_key, result, timeout=60*60*24) # Cache for 24 hours
        return result
    except Exception as e:
        logger.error(f"Contextual score lookup error: {e}")
        result = 0.5, "Unknown"
        cache.set(cache_key, result, timeout=60*60*24) # Cache for 24 hours
        return result # Default fallback


def overview(request):
    # 1. Initialize Safety Defaults
    chart = "<div>No data available</div>"
    country_list = []
    top_subjects = []
    cvi_score = None
    cvi_intent = None

    # 2. Capture Inputs (Intent, Actor, Country)
    calc_target_country = request.GET.get('calc_target_country', '').strip()
    calc_foreign_actor = request.GET.get('calc_foreign_actor', '').strip()
    calc_strategic_intent = request.GET.get('calc_strategic_intent', '').strip()

    # --- CACHE KEYS FOR FILTERED QUERYSETS AND STATS ---
    # Base queryset without sports (potentially expensive to build)
    base_qs_cache_key = "overview_base_qs_no_sports"
    base_qs = cache.get(base_qs_cache_key)
    if base_qs is None:
        logger.info("Cache MISS for base_qs, rebuilding...")
        exclude_keywords = [
            'football', 'soccer', 'sport', 'sports', 'match', 'game',
            'tournament', 'championship', 'olympic', 'cricket', 'basketball',
            'tennis', 'golf', 'athletics', 'rugby', 'boxing', 'mma', 'fight',
            'league', 'team', 'player', 'coach', 'stadium'
        ]
        base_qs = MediaNarrative.objects.all()
        for word in exclude_keywords:
          
            # Consider moving this exclusion to ingestion time if possible.
            base_qs = base_qs.exclude(article_text__icontains=word)
        
        # Instead, cache the *fact* that the exclusion list was applied.
        # We'll cache the results of operations performed on the filtered queryset below.
        cache.set(f"{base_qs_cache_key}_excluded", True, timeout=60*60*24) # Cache the exclusion logic flag
    else:
        logger.info(f"Cache HIT for base_qs exclusion logic: {base_qs_cache_key}")


    # Apply filters based on user input to the base queryset
    # Create a cache key specific to the current filters
    filtered_qs_cache_key = f"overview_filtered_qs_{calc_target_country}_{calc_foreign_actor}"
    full_stats_qs = cache.get(filtered_qs_cache_key)
    if full_stats_qs is None:
        logger.info(f"Cache MISS for filtered_qs: {filtered_qs_cache_key}")
        # Rebuild the base queryset excluding sports (could be optimized further if cached efficiently)
        exclude_keywords = [
            'football', 'soccer', 'sport', 'sports', 'match', 'game',
            'tournament', 'championship', 'olympic', 'cricket', 'basketball',
            'tennis', 'golf', 'athletics', 'rugby', 'boxing', 'mma', 'fight',
            'league', 'team', 'player', 'coach', 'stadium'
        ]
        temp_base_qs = MediaNarrative.objects.all()
        for word in exclude_keywords:
             temp_base_qs = temp_base_qs.exclude(article_text__icontains=word)

        # Apply user filters
        if calc_target_country:
            temp_base_qs = temp_base_qs.filter(target_country__iexact=calc_target_country)
        if calc_foreign_actor:
            temp_base_qs = temp_base_qs.filter(inferred_actor__iexact=calc_foreign_actor)

        # Order by posting time - this is also expensive if the result set is huge
        full_stats_qs = temp_base_qs.order_by('-posting_time')
        # Again, DO NOT cache the full QuerySet object itself.
        # We'll cache the results of operations performed on it.
    else:
        logger.info(f"Cache HIT for filtered_qs: {filtered_qs_cache_key}")

    # 4. CALCULATOR LOGIC (Uses cached calculate_contextual_score)
    if calc_target_country and calc_foreign_actor:
        cvi_score, cvi_intent = calculate_contextual_score(
            calc_target_country,
            calc_foreign_actor,
            intent_filter=calc_strategic_intent
        )
        # Filter display list to match selection actor and country selection (already done above implicitly via cache key)
    else:
        calc_target_country = ""
        calc_foreign_actor = ""
        calc_strategic_intent = ""

    # --- CACHING FOR EXPENSIVE OPERATIONS ON THE FILTERED QUERYSET ---
    # Cache total articles count
    total_articles_cache_key = f"overview_total_articles_{calc_target_country}_{calc_foreign_actor}"
    total_articles = cache.get(total_articles_cache_key)
    if total_articles is None:
        logger.info(f"Cache MISS for total_articles: {total_articles_cache_key}")
        # Perform the count on the filtered queryset
        if full_stats_qs is not None:
             total_articles = full_stats_qs.count()
        else:
            # Fallback if full_stats_qs wasn't cached and had to be rebuilt
            exclude_keywords = [
                'football', 'soccer', 'sport', 'sports', 'match', 'game',
                'tournament', 'championship', 'olympic', 'cricket', 'basketball',
                'tennis', 'golf', 'athletics', 'rugby', 'boxing', 'mma', 'fight',
                'league', 'team', 'player', 'coach', 'stadium'
            ]
            temp_base_qs = MediaNarrative.objects.all()
            for word in exclude_keywords:
                 temp_base_qs = temp_base_qs.exclude(article_text__icontains=word)
            if calc_target_country:
                temp_base_qs = temp_base_qs.filter(target_country__iexact=calc_target_country)
            if calc_foreign_actor:
                temp_base_qs = temp_base_qs.filter(inferred_actor__iexact=calc_foreign_actor)
            total_articles = temp_base_qs.count()

        cache.set(total_articles_cache_key, total_articles, timeout=60*60) # Cache for 1 hour
    else:
        logger.info(f"Cache HIT for total_articles: {total_articles_cache_key}")


    # Rebuild full_stats_qs if it wasn't cached (necessary to perform other operations)
    if full_stats_qs is None:
        exclude_keywords = [
            'football', 'soccer', 'sport', 'sports', 'match', 'game',
            'tournament', 'championship', 'olympic', 'cricket', 'basketball',
            'tennis', 'golf', 'athletics', 'rugby', 'boxing', 'mma', 'fight',
            'league', 'team', 'player', 'coach', 'stadium'
        ]
        temp_base_qs = MediaNarrative.objects.all()
        for word in exclude_keywords:
             temp_base_qs = temp_base_qs.exclude(article_text__icontains=word)
        if calc_target_country:
            temp_base_qs = temp_base_qs.filter(target_country__iexact=calc_target_country)
        if calc_foreign_actor:
            temp_base_qs = temp_base_qs.filter(inferred_actor__iexact=calc_foreign_actor)
        full_stats_qs = temp_base_qs.order_by('-posting_time')


    # 5. Global Stats & Averages (Cache these!)
    stats_cache_key = f"overview_global_stats_{calc_target_country}_{calc_foreign_actor}"
    global_stats_cached = cache.get(stats_cache_key)
    if global_stats_cached is None:
        logger.info(f"Cache MISS for global_stats: {stats_cache_key}")
        # Perform expensive aggregations on the filtered queryset
        unique_outlets = full_stats_qs.values('media_outlet').distinct().count()
        unique_intents = full_stats_qs.exclude(strategic_intent__in=['', 'Unknown', None]).values('strategic_intent').distinct().count()
        unique_actors = full_stats_qs.exclude(inferred_actor__in=['', 'Unknown', None]).values('inferred_actor').distinct().count()
        avg_stats = full_stats_qs.aggregate(Avg('vulnerability_index'), Avg('confidence'))
        avg_vulnerability = avg_stats['vulnerability_index__avg'] or 0.0
        avg_confidence = avg_stats['confidence__avg'] or 0.0
        global_stats_cached = {
            'unique_outlets': unique_outlets,
            'unique_intents': unique_intents,
            'unique_actors': unique_actors,
            'avg_vulnerability': round(avg_vulnerability, 3),
            'avg_confidence': round(avg_confidence, 3),
        }
        cache.set(stats_cache_key, global_stats_cached, timeout=60*60) # Cache for 1 hour
    else:
        logger.info(f"Cache HIT for global_stats: {stats_cache_key}")

    unique_outlets = global_stats_cached['unique_outlets']
    unique_intents = global_stats_cached['unique_intents']
    unique_actors = global_stats_cached['unique_actors']
    avg_vulnerability = global_stats_cached['avg_vulnerability']
    avg_confidence = global_stats_cached['avg_confidence']


    # 6. Volume Chart (Cache the HTML output!)
    chart_cache_key = f"overview_volume_chart_{calc_target_country}_{calc_foreign_actor}"
    cached_chart = cache.get(chart_cache_key)
    if cached_chart:
        logger.info(f"Cache HIT for volume chart: {chart_cache_key}")
        chart = cached_chart
    else:
        logger.info(f"Cache MISS for volume chart: {chart_cache_key}")
        try:
            # Use the filtered queryset for chart data, limit to 500 for performance
            limited_for_chart = full_stats_qs.exclude(posting_time__isnull=True)[:500]
            if limited_for_chart.exists():
                df = pd.DataFrame.from_records(limited_for_chart.values('posting_time'))
                df['date'] = pd.to_datetime(df['posting_time'], utc=True).dt.date # FIXED: Use 'posting_time'
                daily_counts = df['date'].value_counts().sort_index().reset_index(name='count')
                if not daily_counts.empty:
                    fig = px.line(daily_counts, x='date', y='count', template="plotly_white")
                    fig.update_layout(margin=dict(l=10, r=10, t=10, b=10), height=300)
                    chart = fig.to_html(full_html=False, include_plotlyjs='cdn')
                    cache.set(chart_cache_key, chart, timeout=60*60) # Cache chart for 1 hour
        except Exception as e:
            logger.error(f"Volume Chart Error: {e}")
            cache.set(chart_cache_key, chart, timeout=60*15) # Cache error for 15 mins


    # 7. Optimized Data Lists (Cache these too!)
    country_list_cache_key = f"overview_country_list_{calc_target_country}_{calc_foreign_actor}"
    country_list = cache.get(country_list_cache_key)
    if country_list is None:
        logger.info(f"Cache MISS for country_list: {country_list_cache_key}")
        # Perform aggregation on the filtered queryset
        country_list = full_stats_qs.exclude(target_country__in=['', 'Unknown', None]).values('target_country').annotate(total=Count('id')).order_by('-total')[:10]
        cache.set(country_list_cache_key, country_list, timeout=60*60) # Cache for 1 hour

    top_subjects_cache_key = f"overview_top_subjects_{calc_target_country}_{calc_foreign_actor}"
    top_subjects = cache.get(top_subjects_cache_key)
    if top_subjects is None:
        logger.info(f"Cache MISS for top_subjects: {top_subjects_cache_key}")
        # Perform aggregation on the filtered queryset
        top_subjects = full_stats_qs.exclude(strategic_intent__in=['', None]).values('strategic_intent', 'inferred_actor', 'target_country').annotate(total=Count('id')).order_by('-total')[:5]
        cache.set(top_subjects_cache_key, top_subjects, timeout=60*60) # Cache for 1 hour


    # 8. Pagination (This is inherently fast as it limits the final result set)
    # Use the filtered queryset for pagination
    paginator = Paginator(full_stats_qs, 10) # Use the filtered queryset
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)


    # 9. PROCESS ARTICLES (Vulnerability Index + Title + Summary)
    # --- OPTIMIZATION: Use Lazy Loading Function ---
    ml_service = get_ml_service() # Load ML service only when needed

    # --- OPTIMIZATION: Define extract_title_from_text once before the loop ---
    def extract_title_from_text(text):
        if not text:
            return "No Content Available"
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return "Empty Article"
        for candidate in lines[:3]:
            is_metadata = re.match(r'^(By|On|Updated|Source:|Published|https?://|.*\d{4})', candidate, re.IGNORECASE)
            if not is_metadata and 5 <= len(candidate) <= 250:
                return candidate
        words = text.split()
        fallback = " ".join(words[:20])
        return f"{fallback}..." if len(words) > 20 else fallback

    for article in page_obj.object_list:
        article.display_title = extract_title_from_text(article.article_text)
        if hasattr(article, 'ai_summary') and article.ai_summary:
            article.display_summary = article.ai_summary
        else:
            text = article.article_text.replace('\n', ' ').strip()
            if len(text) > 500:
                cut = text[:500].rfind(' ')
                article.display_summary = (text[:cut] + '…') if cut > 0 else text[:500] + '…'
            else:
                article.display_summary = text

        # --- COMMENT OUT INEFFICIENT PER-ARTICLE CALCULATION ---
        # This calculation should ideally happen during ingestion, not display.
        # C. Individual Article Vulnerability Score
        # if article.vulnerability_index is None:
        #     vi_score = ml_service.calculate_vulnerability_index(
        #         article.strategic_intent or 'neutral',
        #         article.tone or 'neutral',
        #         article.target_country,
        #         article.inferred_actor,
        #         article.confidence or 0.5
        #     )
        #     article.vulnerability_index = float(vi_score) if vi_score else 0.0
        # else:
        #     article.vulnerability_index = float(article.vulnerability_index)


    # 10. Methodology / Description (Can be cached if static)
    # ... (keep your existing methodology string logic) ...
    actor_label = calc_foreign_actor if calc_foreign_actor else "[Foreign Actor]"
    target_label = calc_target_country if calc_target_country else "[Target Country]"
    vulnerability_methodology = (
        f"1. Content Signal: Measures the intensity of strategic narratives pushed by {actor_label} "
        f"toward {target_label} on a specific factor (e.g., economic, elections, sovereignty, etc.). "
        "It is estimated using advanced ML models and statistically corrected using human labels via "
        "Prediction-powered Inference (PPI) to ensure reliable measurement.\n\n"
        f"2. Contextual Signal: Captures the structural susceptibility of {target_label} to influence "
        f"from {actor_label} on that specific factor. It incorporates measurable actor×country conditions "
        "such as debt exposure, military presence, resource dependencies, election timing, or policy "
        "environment that may increase vulnerability."
    )


    # 11. Context Assembly
    context = {
        'chart': chart,
        'page_obj': page_obj,
        'total_articles': total_articles,
        'unique_outlets': unique_outlets,
        'unique_intents': unique_intents,
        'unique_actors': unique_actors,
        'avg_vulnerability': avg_vulnerability,
        'avg_confidence': avg_confidence,
        'african_countries': COUNTRIES,
        'foreign_actors': FOREIGN_ACTORS,
        'country_list': country_list,
        'top_subjects': top_subjects,
        'cvi_score': cvi_score,
        'cvi_intent': cvi_intent,
        'selected_country': calc_target_country,
        'selected_actor': calc_foreign_actor,
        'selected_intent': calc_strategic_intent,
        'intent_choices': INTENT_CHOICES,
        'vulnerability_description': vulnerability_methodology,
    }
    return render(request, 'overview.html', context)


def media(request):
    outlet_name = request.GET.get('outlet', '').strip()
    
    # 1. Main Queryset with correct ordering
    qs = MediaNarrative.objects.all().order_by('-posting_time')
    if outlet_name: 
        qs = qs.filter(media_outlet_fk__name__iexact=outlet_name)

    # 2. Sidebar: Count articles per outlet using correct reverse relationship
    # If this still fails, try .annotate(article_count=Count('medianarrative'))
    top_outlets = MediaOutlet.objects.annotate(
        article_count=Count('medianarrative') 
    ).order_by('-article_count')[:10]

    # Initialize variables
    publisher_chart = "<p class='text-center py-5 text-muted'>No publishing data available</p>"
    subject_chart = "<p class='text-center py-5 text-muted'>No subject data available</p>"
    actor_country_chart = "<p class='text-center py-5 text-muted'>No actor-country pairing data available</p>"

    # - 1. Top African Countries -
    top_publishers = MediaNarrative.objects.exclude(
        target_country__in=['', 'Unknown', None]
    ).values('target_country').annotate(article_count=Count('id')).order_by('-article_count')[:10]

    if top_publishers:
        df_pub = pd.DataFrame(list(top_publishers))
        if not df_pub.empty:
            df_pub = df_pub.rename(columns={'target_country': 'Country', 'article_count': 'Articles'}).sort_values('Articles')
            fig_pub = px.bar(df_pub, x='Articles', y='Country', orientation='h', template="plotly_white")
            fig_pub.update_traces(marker_color='#2563eb', texttemplate='%{x}', textposition='outside')
            fig_pub.update_layout(height=400, margin=dict(l=20, r=20, t=20, b=20))
            publisher_chart = fig_pub.to_html(full_html=False, include_plotlyjs='cdn')

    # - 2. Top Foreign Actors -
    top_subjects = MediaNarrative.objects.exclude(
        inferred_actor__in=['', 'Unknown', None, 'local', 'Local']
    ).values('inferred_actor').annotate(mention_count=Count('id')).order_by('-mention_count')[:10]

    if top_subjects:
        df_sub = pd.DataFrame(list(top_subjects))
        if not df_sub.empty:
            df_sub = df_sub.rename(columns={'inferred_actor': 'Actor', 'mention_count': 'Mentions'}).sort_values('Mentions')
            fig_sub = px.bar(df_sub, x='Mentions', y='Actor', orientation='h', template="plotly_white")
            fig_sub.update_traces(marker_color='#f59e0b', texttemplate='%{x}', textposition='outside')
            fig_sub.update_layout(height=400, margin=dict(l=20, r=20, t=20, b=20))
            subject_chart = fig_sub.to_html(full_html=False, include_plotlyjs='cdn')

    # - 3. Actor-Country Pairings -
    ac_pairings = MediaNarrative.objects.exclude(
        target_country__in=['', 'Unknown', None]
    ).exclude(
        inferred_actor__in=['', 'Unknown', None, 'local', 'Local']
    ).values('target_country', 'inferred_actor').annotate(count=Count('id')).order_by('-count')[:10]

    if ac_pairings:
        df_ac = pd.DataFrame(list(ac_pairings))
        if not df_ac.empty:
            df_ac['Label'] = df_ac['target_country'] + " - " + df_ac['inferred_actor']
            df_ac = df_ac.sort_values('count')
            fig_ac = px.bar(df_ac, x='count', y='Label', orientation='h', template="plotly_white")
            fig_ac.update_traces(marker_color='#6366f1', texttemplate='%{x}', textposition='outside')
            fig_ac.update_layout(height=400, margin=dict(l=20, r=20, t=20, b=20))
            actor_country_chart = fig_ac.to_html(full_html=False, include_plotlyjs='cdn')

    context = {
        'top_outlets': top_outlets,
        'page_obj': Paginator(qs, 10).get_page(request.GET.get('page')),
        'selected_name': outlet_name or "All Outlets",
        'publisher_chart': publisher_chart,
        'subject_chart': subject_chart,
        'actor_country_chart': actor_country_chart,
        'target_countries': COUNTRIES,
    }
    return render(request, 'media.html', context)

def generate_report(request):
    selected_country = request.GET.get('country')
    selected_actors = request.GET.getlist('actors')

    # 1. Handle Form Display
    if not selected_country or not selected_actors:
        context = {
            'african_countries': COUNTRIES,
            'foreign_actors': FOREIGN_ACTORS,
        }
        return render(request, 'report_form.html', context)

    # 2. Setup Mapping Logics
    csv_country_map = {
        "ethiopia": "Ethiopia", "senegal": "Senegal", "drc": "DRC",
        "democratic republic of the congo": "DRC", "cote d'ivoire": "CoteIvoire",
        "coteivoire": "CoteIvoire", "ivory coast": "CoteIvoire", "south africa": "South Africa"
    }
    csv_actor_map = {
        "us": "UnitedStates", "unitedstates": "UnitedStates", "saudi": "Saudi",
        "saudi arabia": "Saudi", "uae": "UAE", "russia": "Russia", "france": "France",
        "china": "China", "rwanda": "Rwanda", "turkey": "Turkey", "israel": "Israel",
        "iran": "Iran", "nonstate": "NonState"
    }
    db_country_map = {
        "coteivoire": "Côte d'Ivoire", "cote d'ivoire": "Côte d'Ivoire",
        "drc": "DRC", "ethiopia": "Ethiopia", "senegal": "Senegal", "south africa": "South Africa"
    }
    db_actor_map = {
        "unitedstates": "US", "us": "US", "saudi": "Saudi Arabia",
        "saudi arabia": "Saudi Arabia", "uae": "UAE", "russia": "Russia",
        "france": "France", "china": "China", "rwanda": "Rwanda",
        "turkey": "Turkey", "israel": "Israel", "iran": "Iran"
    }

    report_data = []

    # --- 3. Process Data ---
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        csv_file = os.path.join(current_dir, '..', 'final_risk_by_actor_intent_country.csv')

        if not os.path.exists(csv_file):
            csv_file = os.path.join(current_dir, 'final_risk_by_actor_intent_country (1).csv')

        if not os.path.exists(csv_file):
             logger.error(f"Neither 'final_risk_by_actor_intent_country.csv' nor 'final_risk_by_actor_intent_country (1).csv' found in {os.path.dirname(csv_file)}")
             return HttpResponse("Error: Risk data file not found.", status=500)

        df = pd.read_csv(csv_file)
        search_country = csv_country_map.get(selected_country.lower(), selected_country.title())
        db_country = db_country_map.get(selected_country.lower(), selected_country.title())

        for actor in selected_actors:
            # A. CSV Risk Score Logic
            search_actor = csv_actor_map.get(actor.lower(), actor.title())
            matching_rows = df[
                (df['country'].str.strip() == search_country) &
                (df['actor'].str.strip() == search_actor)
            ]

            # B. Database Chart Logic for THIS specific actor
            db_actor = db_actor_map.get(actor.lower(), actor)
            chart_qs = MediaNarrative.objects.filter(
                target_country__iexact=db_country,
                inferred_actor__iexact=db_actor
            )

            volume_chart = None
            if chart_qs.exists():
                # Use matplotlib for PDF charts (more reliable than plotly HTML embeds in PDFs)
                try:
                    plt.figure(figsize=(10, 4))
                    df_chart = pd.DataFrame(list(chart_qs.values('posting_time')))
                    df_chart['date'] = pd.to_datetime(df_chart['posting_time']).dt.date
                    counts = df_chart['date'].value_counts().sort_index()

                    plt.plot(counts.index, counts.values, color='#2563eb', marker='o')
                    plt.title(f"Volume: {actor} in {db_country}")
                    plt.grid(True, alpha=0.3)

                    buf = BytesIO()
                    plt.savefig(buf, format='png', bbox_inches='tight')
                    buf.seek(0)
                    volume_chart_bytes = buf.read()
                    if volume_chart_bytes: # Ensure the buffer is not empty
                        volume_chart = base64.b64encode(volume_chart_bytes).decode('utf-8')
                    else:
                        logger.warning(f"Generated chart for {actor} in {db_country} is empty.")
                        volume_chart = None # Or assign a default image data URI
                    buf.close() # Close buffer
                    plt.close() # Close figure to free memory
                except Exception as e_chart:
                    logger.error(f"Chart generation error for {actor} in {db_country}: {e_chart}")
                    volume_chart = None # Assign None on error to prevent template issues
            else:
                 logger.info(f"No data found for chart for {actor} in {db_country}.")

            # C. Combine results
            if not matching_rows.empty:
                max_row = matching_rows.loc[matching_rows['FinalRisk'].idxmax()]
                report_data.append({
                    'actor': actor,
                    'cvi_score': round(float(max_row['FinalRisk']), 3),
                    'risk_level': "High" if max_row['FinalRisk'] > 0.7 else "Medium" if max_row['FinalRisk'] > 0.4 else "Low",
                    'primary_threat': max_row['intent'],
                    'chart': volume_chart
                })
            else:
                report_data.append({
                    'actor': actor, 'cvi_score': 0.0, 'risk_level': "N/A",
                    'primary_threat': "No Data Found", 'chart': volume_chart
                })

        report_data.sort(key=lambda x: x['cvi_score'], reverse=True)

    except FileNotFoundError:
        logger.error(f"Risk data CSV file not found.")
        return HttpResponse("Error: Risk data file not found.", status=500)
    except Exception as e:
        logger.error(f"Report Generation Data Processing Error: {e}")
        return HttpResponse(f"Error: {e}", status=500)

    # --- 4. Get Key Narratives & AI Insights ---
    key_narratives = []
    ai_insights = ""

    # Identify the top actor to focus the narrative analysis
    highest_risk_actor = report_data[0]['actor'] if report_data else "None"
    # Map it to the DB name (e.g., 'UnitedStates' -> 'US')
    top_db_actor = db_actor_map.get(highest_risk_actor.lower(), highest_risk_actor)

    try:
        exclude_list = ['football', 'soccer', 'sport', 'sports', 'match', 'game', 'tournament', 'championship']

        # FIX: Filter by mapped DB country AND the top actor found in Step 3
        base_query = MediaNarrative.objects.filter(
            target_country__iexact=db_country,
            inferred_actor__iexact=top_db_actor
        )

        for term in exclude_list:
            base_query = base_query.exclude(article_text__icontains=term)

        articles_count = base_query.count()

        display_articles = base_query.exclude(
            strategic_intent__in=['', None, 'Unknown', 'unknown']
        ).order_by('-posting_time')[:4]

        # Import Groq inside the try block to handle potential import errors gracefully
        from groq import Groq
        groq_api_key = os.environ.get('GROQ_API_KEY') # Prefer environment variable
        if not groq_api_key:
            # Fallback to Django settings if environment variable is not set
            groq_api_key = getattr(settings, 'GROQ_API_KEY', None)
        client = Groq(api_key=groq_api_key, timeout=20.0) if groq_api_key else None

        # ---: INDIVIDUAL AI SUMMARIES FOR EACH NARRATIVE ---
        for article in display_articles:
            ai_summary = article.article_text[:100] + "..." # Default fallback

            if client:
                try:
                    # Prompting for a specific, short AI summary per article
                    sum_prompt = f"Summarize this news article in 2 concise sentences for a risk report: {article.article_text[:1500]}"
                    sum_response = client.chat.completions.create(
                        messages=[{"role": "user", "content": sum_prompt}],
                        model="meta-llama/llama-4-scout-17b-16e-instruct",
                        max_tokens=1024
                    )
                    ai_summary = sum_response.choices[0].message.content.strip()
                except Exception as e:
                    logger.error(f"Article summary error for article ID {getattr(article, 'id', 'unknown')}: {e}")
                    # ai_summary remains the fallback

            key_narratives.append({
                'intent': article.strategic_intent,
                'tone': article.tone,
                'url': article.url,
                'media_outlet': article.media_outlet,
                'posting_time': article.posting_time.strftime("%Y-%m-%d") if article.posting_time else "Unknown",
                'summary': ai_summary # uses AI-generated summary
            })

        # --- EXECUTIVE AI INSIGHTS ---
        all_articles_for_ai = base_query.exclude(article_text__isnull=True).order_by('-posting_time')[:15]
        full_context_data = [f"Source: {art.media_outlet} | Intent: {art.strategic_intent} | Content: {art.article_text[:500]}" for art in all_articles_for_ai]
        all_text_context = "\n---\n".join(full_context_data)

        if client and all_text_context.strip(): # Ensure client exists and context is not empty/whitespace
            insight_prompt = f"""
            Analyze the following media narratives for {selected_country} as a Senior Geopolitical Analyst.
            Your objective is to evaluate these articles for signs of foreign influence and structural vulnerability.

            STRICT FORMATTING RULES:
            - NO markdown symbols. DO NOT use '###' or '**'.
            - Use plain text headers in ALL CAPS for sections.
            - Use simple dashes (-) for bullet points.
            - Keep the language formal and professional.

            STRUCTURE:
            ### 📊 Narrative Summary
            (Provide a high-level summary of the media volume, dominant sentiment, and primary themes/narratives found in the dataset.)

            ### 🛡️ Key Actors & Influence
            (List the primary foreign actors mentioned and their apparent strategic goals or intents as inferred from the narratives.)

            ### ⚠️ Influence Threat Analysis
            (Assess the overall likelihood and severity of the influence threat to {selected_country}. Consider if narratives are exploiting local divisions, economic ties, or social fragility.)

            DATASET:
            {all_text_context}
            """

            try:
                chat_completion = client.chat.completions.create(
                    messages=[{"role": "user", "content": insight_prompt}],
                    model="meta-llama/llama-4-scout-17b-16e-instruct"
                )
                ai_insights = chat_completion.choices[0].message.content
            except Exception as e_insight:
                 logger.error(f"AI Insights generation error: {e_insight}")
                 ai_insights = f"AI analysis could not be completed. (Error: {str(e_insight)[:50]})" # Short error message
        else:
             if not client:
                 logger.warning("Groq client not initialized for AI insights.")
                 ai_insights = "AI analysis skipped: API key not configured."
             elif not all_text_context.strip():
                 logger.info("Insufficient text context for AI analysis.")
                 ai_insights = "Insufficient data for AI analysis."

    except ImportError:
        logger.error("Groq library not found.")
        ai_insights = "AI analysis skipped: Groq library not installed."
    except Exception as e:
        logger.error(f"Narrative/AI processing error: {str(e)}")
        ai_insights = f"Narrative and AI analysis could not be completed. (Error: {str(e)[:50]})" # Short error message


    ## --- CHARTS FOR PDF ---
    volume_chart_base64 = ""
    factor_chart_base64 = ""
    primary_intent = "General Influence"

    try:
        volume_data = base_query.values('posting_time__date').annotate(count=Count('id')).order_by('posting_time__date')
        if volume_data.exists():
            df_vol = pd.DataFrame(list(volume_data)).rename(columns={'posting_time__date': 'date', 'count': 'articles'})
            df_vol = df_vol.dropna(subset=['date']).sort_values('date').reset_index(drop=True)

            fig, ax = plt.subplots(figsize=(8, 4))
            ax.plot(df_vol['date'], df_vol['articles'], marker='o', color='#2563eb')
            ax.set_xlabel('Date')
            ax.set_ylabel('Number of Articles')
            ax.set_title(f'Volume of Articles Over Time for {db_country}')
            ax.grid(True, linestyle='--', alpha=0.6)
            plt.xticks(rotation=45, ha="right") # Rotate x-axis labels
            plt.tight_layout()

            buf = BytesIO()
            plt.savefig(buf, format='png', dpi=150) # Save as PNG buffer
            buf.seek(0)
            volume_chart_bytes = buf.read()
            if volume_chart_bytes: # Ensure the buffer is not empty
                volume_chart_base64 = base64.b64encode(volume_chart_bytes).decode('utf-8')
            else:
                logger.warning("Main volume chart generated is empty.")
                volume_chart_base64 = "" # Or assign a default image data URI
            buf.close() # Close buffer
            plt.close(fig) # Close the figure to free memory
        else:
            logger.info("No data available for the main volume chart.")
            volume_chart_base64 = "" # Or assign a default image/data URI

        # Factor chart logic
        intent_counts = base_query.exclude(
            strategic_intent__in=['', None, 'Unknown']
        ).values('strategic_intent').annotate(count=Count('id')).order_by('-count')[:5]

        if intent_counts.exists():
            primary_intent = intent_counts[0]['strategic_intent']
            df_f = pd.DataFrame(list(intent_counts)).rename(columns={'strategic_intent': 'Factor', 'count': 'Val'})
            df_f = df_f.sort_values('Val', ascending=True).reset_index(drop=True) # Horizontal bar chart needs ascending order for top-down

            fig, ax = plt.subplots(figsize=(7, 4))
            ax.barh(df_f['Factor'], df_f['Val'], color='#38bdf8')
            ax.set_xlabel('Count')
            ax.set_ylabel('Strategic Intent')
            ax.set_title(f'Top Strategic Intents for {db_country}')
            plt.tight_layout()

            buf = BytesIO()
            plt.savefig(buf, format='png', dpi=150) # Save as PNG buffer
            buf.seek(0)
            factor_chart_bytes = buf.read()
            if factor_chart_bytes: # Ensure the buffer is not empty
                factor_chart_base64 = base64.b64encode(factor_chart_bytes).decode('utf-8')
            else:
                logger.warning("Factor chart generated is empty.")
                factor_chart_base64 = "" # Or assign a default image data URI
            buf.close() # Close buffer
            plt.close(fig) # Close the figure to free memory
        else:
            logger.info("No data available for the factor chart.")
            factor_chart_base64 = "" # Or assign a default image/data URI
            # Keep primary_intent as default if no intents found

    except Exception as e_chart:
        logger.error(f"Chart Generation Error in PDF: {e_chart}")
        # Assign empty strings or default image URIs on chart error
        volume_chart_base64 = ""
        factor_chart_base64 = ""
        # Do not raise the exception here, let the PDF generation proceed with missing charts


    # --- FINAL CONTEXT ---
    context = {
        'country': db_country,
        'primary_intent': primary_intent,
        'articles_count': articles_count if 'articles_count' in locals() else 0,
        'volume_chart': volume_chart_base64,
        'factor_chart': factor_chart_base64,
        'report_data': report_data,  # This contains the CSV scores
        'key_narratives': key_narratives,
        'ai_insights': ai_insights,
        'highest_risk_actor': highest_risk_actor,
        'date_generated': datetime.now().strftime("%B %d, %Y"),
    }

    ## --- ENHANCED ERROR HANDLING FOR PDF RENDERING ---
    try:
        template = get_template('report_pdf.html') # Ensure this template exists and is valid
        html = template.render(context)

        result = BytesIO()
        # Log the HTML length or a snippet for debugging if needed (be careful with sensitive data)
        # logger.debug(f"HTML length for PDF: {len(html)}, Snippet: {html[:500]}")

        pdf = pisa.pisaDocument(BytesIO(html.encode("UTF-8")), result)

        if not pdf.err:
            response = HttpResponse(result.getvalue(), content_type='application/pdf')
            response['Content-Disposition'] = f'attachment; filename="CVI_Report_{selected_country}.pdf"'
            return response
        else:
            logger.error(f"xhtml2pdf Error: {pdf.err}, Log: {pdf.log}")
            return HttpResponse("Error generating PDF: Internal processing error.", status=500)

    except Exception as e_pdf:
        logger.error(f"PDF Rendering Error: {e_pdf}")
        return HttpResponse(f"Error generating PDF: {e_pdf}", status=500)

    # This line should theoretically not be reached due to the returns above,
    # but included for completeness.
    return HttpResponse("Unexpected error during report generation.", status=500)



def countries(request):
    selected_country = request.GET.get('country', '').strip()
    qs = MediaNarrative.objects.all()
    if selected_country:
        qs = qs.filter(target_country__iexact=selected_country)

    # Aggregate by country
    country_stats = qs.values('target_country').annotate(
        total_articles=Count('id'),
        avg_vulnerability=Avg('vulnerability_index'),
        avg_confidence=Avg('confidence')
    ).order_by('-total_articles')

    # Prepare data for chart
    chart_data = []
    for stat in country_stats:
        chart_data.append({
            'country': stat['target_country'],
            'articles': stat['total_articles'],
            'avg_vulnerability': stat['avg_vulnerability'] or 0.0,
            'avg_confidence': stat['avg_confidence'] or 0.0,
        })

    # Convert to DataFrame for Plotly
    df = pd.DataFrame(chart_data)
    if not df.empty:
        fig = px.bar(df, x='country', y='articles', title="Articles by Country")
        chart_html = fig.to_html(full_html=False, include_plotlyjs='cdn')
    else:
        chart_html = "<p>No data available for chart</p>"

    context = {
        'country_list': country_stats,
        'chart_html': chart_html,
        'selected_country': selected_country,
        'african_countries': COUNTRIES,
    }
    return render(request, 'dashboard/countries.html', context)


def authors(request):
    selected_author = request.GET.get('author', '').strip()
    qs = MediaNarrative.objects.all()
    
    # FIX: Use journalist_fk instead of journalist
    if selected_author:
        qs = qs.filter(journalist_fk__name__iexact=selected_author)

    # Aggregate using the correct relationship path
    author_stats = qs.values('journalist_fk__name').annotate(
        total_articles=Count('id'),
        avg_vulnerability=Avg('vulnerability_index'),
        avg_confidence=Avg('confidence')
    ).order_by('-total_articles')

    chart_data = []
    for stat in author_stats:
        # Use the correct dictionary key from the values() call
        name = stat['journalist_fk__name']
        if name: 
            chart_data.append({
                'author': name,
                'articles': stat['total_articles'],
                'avg_vulnerability': float(stat['avg_vulnerability']) if stat['avg_vulnerability'] else 0.0,
                'avg_confidence': float(stat['avg_confidence']) if stat['avg_confidence'] else 0.0,
            })

    df = pd.DataFrame(chart_data)
    if not df.empty:
        fig = px.bar(df, x='author', y='articles', title="Articles by Author", template="plotly_white")
        fig.update_traces(marker_color='#2563eb')
        chart_html = fig.to_html(full_html=False, include_plotlyjs='cdn')
    else:
        chart_html = "<p class='text-center py-5 text-muted'>No author data available for chart</p>"

    context = {
        'author_list': author_stats,
        'chart_html': chart_html,
        'selected_author': selected_author,
    }
    return render(request, 'authors.html', context)
    
def articles_view(request):
    search_query = request.GET.get("q", "")

    articles = MediaNarrative.objects.all().order_by("-posting_time")  # Fixed: was Article.objects

    if search_query:
        articles = articles.filter(article_text__icontains=search_query)

    paginator = Paginator(articles, 10)  # 10 articles per page
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    context = {
        "page_obj": page_obj,
        "search_query": search_query,
    }
    return render(request, "articles.html", context)
    
def intents(request):
    intent_name = request.GET.get('intent', '').strip()
    qs = MediaNarrative.objects.all().order_by('-posting_time')
    if intent_name: qs = qs.filter(strategic_intent__iexact=intent_name)
    
    top_intents = MediaNarrative.objects.exclude(strategic_intent__in=['', None]).values('strategic_intent').annotate(article_count=Count('strategic_intent')).order_by('-article_count')[:10]
    context = {
        'top_intents': top_intents,
        'page_obj': Paginator(qs, 10).get_page(request.GET.get('page')),
    }
    return render(request, 'intents.html', context)
    
def all_articles(request):
    # Apply filters if present
    target_country = request.GET.get('target_country', '').strip()
    inferred_actor = request.GET.get('inferred_actor', '').strip()
    strategic_intent = request.GET.get('strategic_intent', '').strip()
    start_date = request.GET.get('start_date', '').strip()
    end_date = request.GET.get('end_date', '').strip()

    # Build queryset
    qs = MediaNarrative.objects.all().order_by('-posting_time')

    if target_country:
        qs = qs.filter(target_country__iexact=target_country)
    if inferred_actor:
        qs = qs.filter(inferred_actor__iexact=inferred_actor)
    if strategic_intent:
        qs = qs.filter(strategic_intent__iexact=strategic_intent)
    if start_date:
        parsed_start = parse_date(start_date)
        if parsed_start:
            qs = qs.filter(posting_time__date__gte=parsed_start)
    if end_date:
        parsed_end = parse_date(end_date)
        if parsed_end:
            qs = qs.filter(posting_time__date__lte=parsed_end)

    # Pagination
    paginator = Paginator(qs, 20)  # Show 20 articles per page
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    # Process articles for display (title, summary)
    for article in page_obj:
        # Use cached title/summary if available, otherwise compute
        if not hasattr(article, 'display_title'):
            lines = [line.strip() for line in article.article_text.splitlines() if line.strip()]
            article.display_title = lines[0][:100] if lines else "No Title Available"
        if not hasattr(article, 'display_summary'):
            text = article.article_text.replace('\n', ' ').strip()
            if len(text) > 500:
                cut = text[:500].rfind(' ')
                article.display_summary = (text[:cut] + '…') if cut > 0 else text[:500] + '…'
            else:
                article.display_summary = text

    context = {
        'page_obj': page_obj,
        'filters_applied': bool(target_country or inferred_actor or strategic_intent or start_date or end_date),
        'target_countries': COUNTRIES,
        'foreign_actors': FOREIGN_ACTORS,
        'strategic_intents': INTENT_CHOICES,
        'selected_filters': {
            'target_country': target_country,
            'inferred_actor': inferred_actor,
            'strategic_intent': strategic_intent,
            'start_date': start_date,
            'end_date': end_date,
        }
    }
    return render(request, 'dashboard/all_articles.html', context)


def dashboard_home(request):
    # Quick stats for the home dashboard page
    total_articles = MediaNarrative.objects.count()
    total_countries = MediaNarrative.objects.exclude(target_country__in=['', 'Unknown', None]).values('target_country').distinct().count()
    total_actors = MediaNarrative.objects.exclude(inferred_actor__in=['', 'Unknown', None]).values('inferred_actor').distinct().count()
    latest_article = MediaNarrative.objects.order_by('-posting_time').first()

    # Recent activity (last 10 articles)
    recent_articles = MediaNarrative.objects.order_by('-posting_time')[:10]

    # Top countries by article count
    top_countries = MediaNarrative.objects.exclude(target_country__in=['', 'Unknown', None]).values('target_country').annotate(count=Count('id')).order_by('-count')[:5]

    # Top actors by article count
    top_actors = MediaNarrative.objects.exclude(inferred_actor__in=['', 'Unknown', None]).values('inferred_actor').annotate(count=Count('id')).order_by('-count')[:5]

    context = {
        'total_articles': total_articles,
        'total_countries': total_countries,
        'total_actors': total_actors,
        'latest_article': latest_article,
        'recent_articles': recent_articles,
        'top_countries': top_countries,
        'top_actors': top_actors,
    }
    return render(request, 'dashboard/home.html', context)

