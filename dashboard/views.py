from django.shortcuts import render
from django.db.models import Q, Count, Avg
from django.core.paginator import Paginator
from .models import MediaNarrative, Journalist, MediaOutlet
from dashboard.services.summarizer import get_summary
from dashboard.services.ml_inference_service import MLInferenceService  # Changed to lazy loading
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

logger = logging.getLogger(__name__)

# =========================
# CONSTANTS
# =========================
COUNTRIES = ["Senegal", "DRC", "CoteIvoire", "Ethiopia", "South Africa"]
FOREIGN_ACTORS = ['France', 'China', 'UAE', 'Russia', 'US', 'Turkey', 'Saudi Arabia', 'Israel', 'Iran']
TARGET_COUNTRIES = ['France', 'China', 'UAE', 'Russia', 'US', 'Turkey', 'Saudi Arabia', 'Israel', 'Iran']

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
    try:
        import pandas as pd
        import os
        
        # Load YOUR CSV file with the exact path
        current_dir = os.path.dirname(os.path.abspath(__file__))
        csv_file = os.path.join(current_dir, '..', 'final_risk_by_actor_intent_country.csv')
        
        # Verify the file exists
        if not os.path.exists(csv_file):
            logger.error(f"CSV file not found: {csv_file}")
            logger.error(f"Files in parent directory: {os.listdir(os.path.join(current_dir, '..'))}")
            return 0.5, "Unknown"  # Default fallback
        
        # Read YOUR CSV file
        df = pd.read_csv(csv_file)
        
        # Normalize country and actor names to match your CSV format
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
        
        # Format the inputs to match YOUR CSV format
        formatted_country = country_mapping.get(target_country.lower(), target_country)
        formatted_actor = actor_mapping.get(foreign_actor.lower(), foreign_actor)
        
        # Find all rows matching this country-actor combination in YOUR CSV
        matching_rows = df[(df['country'] == formatted_country) & (df['actor'] == formatted_actor)]
        
        if not matching_rows.empty:
            # 1. If user selected an intent, try to find that specific one
            if intent_filter:
                specific_match = matching_rows[matching_rows['intent'].str.lower() == intent_filter.lower()]
                if not specific_match.empty:
                    row = specific_match.iloc[0]
                    return float(row['FinalRisk']), row['intent']

            # 2. Fallback: Find the row with the HIGHEST score 
            max_row = matching_rows.loc[matching_rows['FinalRisk'].idxmax()]
            max_score = max_row['FinalRisk']
            max_intent = max_row['intent']
            
            logger.info(f"Found max score {max_score} for {formatted_country}-{formatted_actor} in {max_intent}")
            return float(max_score), max_intent
        else:
            # If no exact match found, try case-insensitive match
            matching_rows = df[
                (df['country'].str.lower() == formatted_country.lower()) & 
                (df['actor'].str.lower() == formatted_actor.lower())
            ]
            
            if not matching_rows.empty:
                # Apply intent filter logic even in case-insensitive fallback
                if intent_filter:
                    specific_match = matching_rows[matching_rows['intent'].str.lower() == intent_filter.lower()]
                    if not specific_match.empty:
                        row = specific_match.iloc[0]
                        return float(row['FinalRisk']), row['intent']

                max_row = matching_rows.loc[matching_rows['FinalRisk'].idxmax()]
                max_score = max_row['FinalRisk']
                max_intent = max_row['intent']
                
                logger.info(f"Found case-insensitive max score {max_score} for {formatted_country}-{formatted_actor} in {max_intent}")
                return float(max_score), max_intent
        
        # If no match found, return default
        logger.info(f"No score found for {target_country}-{foreign_actor} in CSV, using default")
        return 0.5, "Unknown"
        
    except FileNotFoundError:
        logger.error(f"CSV file not found at: {csv_file}")
        return 0.5, "Unknown"
    except Exception as e:
        logger.error(f"Contextual score lookup error: {e}")
        return 0.5, "Unknown"  # Default fallback
        
def overview(request):
    # 1. Initialize Safety Defaults
    chart = "<div>No data available</div>"
    country_list = []
    top_subjects = []
    cvi_score = None
    cvi_intent = None
    
    # 2. Capture Inputs
    calc_target_country = request.GET.get('calc_target_country', '').strip()
    calc_foreign_actor = request.GET.get('calc_foreign_actor', '').strip()
    calc_strategic_intent = request.GET.get('calc_strategic_intent', '').strip()  # NEW: Added Intent Filter
    
    # Shortened Exclude List for maintenance and speed
    exclude_keywords = [
        'football', 'soccer', 'sport', 'sports', 'match', 'game', 
        'tournament', 'championship', 'olympic', 'cricket', 'basketball', 
        'tennis', 'golf', 'athletics', 'rugby', 'boxing', 'mma', 'fight', 
        'league', 'team', 'player', 'coach', 'stadium'
    ]

    # 3. Get total count first (without limit) - REDUCED EXCLUDE LIST
    base_qs = MediaNarrative.objects.all()
    for word in exclude_keywords:
        base_qs = base_qs.exclude(article_text__icontains=word)
    
    total_articles = base_qs.count()
    
    # 4. FOR MAIN DISPLAY: Show ALL articles (no filter) - EXCLUDE SPORTS
    full_stats_qs = base_qs.order_by('-posting_time')

    # 5. Apply calculator filters - UPDATED: Logic for Strategic Intent
    if calc_target_country and calc_foreign_actor:
        cvi_score, cvi_intent = calculate_contextual_score(
            calc_target_country, 
            calc_foreign_actor, 
            intent_filter=calc_strategic_intent
        )
        
        # Filter the article display list based on calculation params
        full_stats_qs = full_stats_qs.filter(
            target_country__iexact=calc_target_country,
            inferred_actor__iexact=calc_foreign_actor
        )
        if calc_strategic_intent:
            full_stats_qs = full_stats_qs.filter(strategic_intent__iexact=calc_strategic_intent)
    else:
        calc_target_country = ""
        calc_foreign_actor = ""
        calc_strategic_intent = ""

    # 6. Apply calculator filters to main queryset for display if needed
    if calc_target_country and calc_foreign_actor:
        full_stats_qs = full_stats_qs.filter(
            target_country__iexact=calc_target_country,
            inferred_actor__iexact=calc_foreign_actor
        )
        # Further narrow display results if user filtered by intent
        if calc_strategic_intent:
            full_stats_qs = full_stats_qs.filter(strategic_intent__iexact=calc_strategic_intent)

    # 7. Global Stats (optimized)
    irrelevant_keywords = ['football', 'soccer', 'entertainment', 'music', 'celebrity', 'fashion']
    
    # 8. Optimized averages
    from django.db.models import Avg
    avg_vulnerability = 0.0
    avg_confidence = 0.0
    
    # 9. Optimized volume chart
    try:
        limited_for_chart = full_stats_qs.exclude(posting_time__isnull=True)[:500]
        if limited_for_chart.exists():
            df = pd.DataFrame.from_records(limited_for_chart.values('posting_time'))
            df = df.dropna(subset=['posting_time'])
            df['date'] = pd.to_datetime(df['posting_time'], utc=True).dt.date
            daily_counts = df['date'].value_counts().sort_index().reset_index(name='count')
            if not daily_counts.empty:
                fig = px.line(daily_counts, x='date', y='count', template="plotly_white")
                fig.update_layout(margin=dict(l=10, r=10, t=10, b=10), height=500)
                chart = fig.to_html(full_html=False, include_plotlyjs='cdn')
    except Exception as e:
        logger.error(f"Volume Chart Error: {e}")

    # 10. Optimized lists
    country_list = full_stats_qs.exclude(
        target_country__in=['', 'Unknown', None]
    ).values('target_country').annotate(total=Count('id')).order_by('-total')[:10]
    
    top_subjects = full_stats_qs.exclude(
        strategic_intent__in=['', None]
    ).exclude(
        inferred_actor__in=['', 'Unknown', None]
    ).exclude(
        target_country__in=['', 'Unknown', None]
    ).values('strategic_intent', 'inferred_actor', 'target_country').annotate(
        total=Count('id')
    ).order_by('-total')[:5]

    # 11. Pagination
    paginator = Paginator(full_stats_qs, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    # 12. Process articles with vulnerability index
    ml_service = MLInferenceService()
    articles_with_vi = []
    for article in page_obj.object_list:
        if article.vulnerability_index is None:
            vi_score = ml_service.calculate_vulnerability_index(
                article.strategic_intent or 'neutral',
                article.tone or 'neutral',
                article.target_country,
                article.inferred_actor,
                article.confidence or 0.5
            )
            article.vulnerability_index = float(vi_score) if vi_score else 0.0
        else:
            article.vulnerability_index = float(article.vulnerability_index)
        articles_with_vi.append(article)
    page_obj.object_list = articles_with_vi

    # NEW: Methodology Description (Fancy version)
    vulnerability_methodology = (
        "The Vulnerability Index is a score between 0.00 and 1.00 that summarizes how vulnerable "
        "a target country is to influence from a selected foreign actor on a specific strategic factor. "
        "The score combines: (1) a content signal — how much collected media and posts say the actor is pushing "
        "strategic intents corresponding to a specific factor (corrected using human labels via Prediction-powered Inference, PPI), "
        "and (2) a contextual signal — measurable country-level or actor×country factors (debt exposure, military presence, "
        "resource ties, election timing, etc...) that make the country more susceptible. Higher values indicate greater "
        "potential influence risk and suggested priority for investigation."
    )

    # 13. Context
    context = {
        'chart': chart,
        'page_obj': page_obj,
        'total_articles': total_articles,
        'unique_outlets': full_stats_qs.values('media_outlet').distinct().count(),
        'unique_intents': full_stats_qs.exclude(strategic_intent__in=['', 'Unknown', None]).values('strategic_intent').distinct().count(),
        'unique_actors': full_stats_qs.exclude(inferred_actor__in=['', 'Unknown', None]).values('inferred_actor').distinct().count(),
        'avg_vulnerability': round(avg_vulnerability, 3) if avg_vulnerability else 0,
        'avg_confidence': round(avg_confidence, 3) if avg_confidence else 0,
        'african_countries': COUNTRIES,
        'foreign_actors': FOREIGN_ACTORS,
        'country_list': country_list,
        'top_subjects': top_subjects,
        'cvi_score': cvi_score,
        'cvi_intent': cvi_intent,
        'selected_country': calc_target_country,
        'selected_actor': calc_foreign_actor,
        'selected_intent': calc_strategic_intent,
        'vulnerability_description': vulnerability_methodology, # Pass to template
    }
    return render(request, 'overview.html', context)
        
   
# =========================
# OTHER PAGES (Countries, Authors, Media, Intents)
# ========================= 

def countries(request):
    selected_country = request.GET.get('country', '').strip()
    qs = MediaNarrative.objects.all().order_by('-posting_time')

    if selected_country:
        qs = qs.filter(target_country__iexact=selected_country)

    # --- 1. Top African countries by total articles (THE PROBLEM CHART) ---
    top_publishers = MediaNarrative.objects.exclude(
        target_country__in=['', 'Unknown', None]
    ).values('target_country').annotate(
        article_count=Count('id')
    ).order_by('-article_count')[:10]

    publisher_chart = "<p class='text-center py-5 text-muted fs-3'>No publishing data</p>"
    
    if top_publishers.exists():
        df = pd.DataFrame(list(top_publishers))
        if not df.empty:
            df = df.rename(columns={'target_country': 'Country', 'article_count': 'Articles'})
            df['Country'] = df['Country'].astype(str).str.strip()
            df = df.sort_values('Articles', ascending=True).reset_index(drop=True)

            # WE SWITCH TO GRAPH OBJECTS (go.Bar) TO AVOID THE KEYERROR
            fig = go.Figure(go.Bar(
                x=df['Articles'],
                y=df['Country'],
                orientation='h',
                text=df['Articles'],
                textposition='outside',
                marker=dict(color='#2563eb') # Solid blue to avoid color-mapping crashes
            ))
            
            fig.update_layout(
                title='Top African Countries by Articles Published',
                height=500,
                template="plotly_white",
                xaxis_title="Articles",
                yaxis_title="Country",
                margin=dict(l=20, r=20, t=40, b=20)
            )
            publisher_chart = fig.to_html(full_html=False, include_plotlyjs='cdn')

    # --- 2. Top subjects mentioned (inferred_actor) ---
    top_subjects = MediaNarrative.objects.exclude(
        inferred_actor__in=['', 'Unknown', None]
    ).values('inferred_actor').annotate(
        mention_count=Count('id')
    ).order_by('-mention_count')[:10]

    subject_chart = "<p class='text-center py-5 text-muted fs-3'>No subject data</p>"
    if top_subjects.exists():
        df_sub = pd.DataFrame(list(top_subjects))
        if not df_sub.empty:
            df_sub = df_sub.rename(columns={'inferred_actor': 'Actor', 'mention_count': 'Mentions'})
            df_sub['Actor'] = df_sub['Actor'].astype(str).str.strip()
            df_sub = df_sub.sort_values('Mentions', ascending=True).reset_index(drop=True)
            
            # Using Express here, but if this crashes too, switch it to go.Bar like above
            fig_sub = px.bar(
                df_sub,
                x='Mentions',
                y='Actor',
                orientation='h',
                title='Top Foreign Actors Mentioned',
                text='Mentions',
                template="plotly_white"
            )
            fig_sub.update_traces(marker_color='#f59e0b', textposition='outside')
            subject_chart = fig_sub.to_html(full_html=False, include_plotlyjs='cdn')

    # --- 3. Top Strategic Intents by Target Country and Actor ---
    target_country_actor_intents = MediaNarrative.objects.exclude(
        target_country__in=['', 'Unknown', None]
    ).exclude(
        inferred_actor__in=['', 'Unknown', None]
    ).exclude(
        strategic_intent__in=['', 'Unknown', None]
    ).values('target_country', 'inferred_actor', 'strategic_intent').annotate(
        count=Count('id')
    ).order_by('-count')[:10]

    intent_country_actor_chart = "<p class='text-center py-5 text-muted fs-3'>No intent data</p>"
    if target_country_actor_intents.exists():
        df_intent = pd.DataFrame(list(target_country_actor_intents))
        if not df_intent.empty:
            df_intent = df_intent.rename(columns={
                'target_country': 'Country', 'inferred_actor': 'Actor', 
                'strategic_intent': 'Intent', 'count': 'Count'
            })
            df_intent['Combined'] = df_intent['Country'] + ' - ' + df_intent['Actor'] + ': ' + df_intent['Intent']
            df_intent = df_intent.sort_values('Count', ascending=True).reset_index(drop=True)
            
            fig_intent = px.bar(
                df_intent,
                x='Count',
                y='Combined',
                orientation='h',
                title='Top Strategic Intents',
                text='Count',
                template="plotly_white"
            )
            fig_intent.update_traces(marker_color='#10b981', textposition='outside')
            intent_country_actor_chart = fig_intent.to_html(full_html=False, include_plotlyjs='cdn')

    # --- Context Preparation ---
    coverage_table = list(top_publishers)
    sample_articles = qs[:5]

    context = {
        'publisher_chart': publisher_chart,
        'subject_chart': subject_chart,
        'intent_country_actor_chart': intent_country_actor_chart,
        'coverage_table': coverage_table,
        'sample_articles': sample_articles,
        'selected_country': selected_country or "All Countries",
        'african_countries': COUNTRIES,
    }
    return render(request, 'countries.html', context)
    
def authors(request):
    journalist_name = request.GET.get('journalist', '').strip()
    sports_keywords = ['sport', 'sports', 'match', 'tournament', 'olympics', 'fifa']
    qs = MediaNarrative.objects.all().order_by('-posting_time')
    if journalist_name: qs = qs.filter(journalist_fk__name__iexact=journalist_name)
    
    top_journalists = Journalist.objects.annotate(article_count=Count('articles')).order_by('-article_count')[:10]
    context = {
        'top_journalists': top_journalists,
        'page_obj': Paginator(qs, 5).get_page(request.GET.get('page')),
        'selected_name': journalist_name or "All Journalists",
    }
    return render(request, 'dashboard/authors.html', context)
    
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

def media(request):
    outlet_name = request.GET.get('outlet', '').strip()
    qs = MediaNarrative.objects.all().order_by('-posting_time')
    if outlet_name: qs = qs.filter(media_outlet_fk__name__iexact=outlet_name)
    
    top_outlets = MediaOutlet.objects.annotate(article_count=Count('articles')).order_by('-article_count')[:10]
    context = {
        'top_outlets': top_outlets,
        'page_obj': Paginator(qs, 10).get_page(request.GET.get('page')),
        'selected_name': outlet_name or "All Outlets",
        'target_countries': TARGET_COUNTRIES,
    }
    return render(request, 'dashboard/media.html', context)

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
    qs = MediaNarrative.objects.all().order_by('-posting_time')

    # Date range filter
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')

    if start_date:
        parsed_start = parse_date(start_date)
        if parsed_start:
            qs = qs.filter(posting_time__date__gte=parsed_start)

    if end_date:
        parsed_end = parse_date(end_date)
        if parsed_end:
            qs = qs.filter(posting_time__date__lte=parsed_end)

    # Pagination
    paginator = Paginator(qs, 10)  # 10 articles per page
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    context = {
        'page_obj': page_obj,
        'start_date': start_date,
        'end_date': end_date,
    }
    return render(request, 'all_articles.html', context)
    
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

    # 2. Setup Data & Normalization
    actor_map = {"US": "UnitedStates"}
    report_data = []

    # 3. Calculate CVI Risk Scores from CSV
    try:
        import pandas as pd
        import os
        
        current_dir = os.path.dirname(os.path.abspath(__file__))
        
        possible_paths = [
            os.path.join(current_dir, '..', 'final_risk_by_actor_intent_country (1).csv'),
            os.path.join(current_dir, '..', 'final_risk_by_actor_intent_country.csv'),
            os.path.join(current_dir, 'final_risk_by_actor_intent_country (1).csv'),
            os.path.join(current_dir, '..', '..', 'final_risk_by_actor_intent_country (1).csv'),
        ]
        
        csv_file = None
        for path in possible_paths:
            if os.path.exists(path):
                csv_file = path
                logger.info(f"Found CSV file at: {path}")
                break
        
        if csv_file is None:
            logger.error(f"CSV file not found in any of these locations: {possible_paths}")
            return HttpResponse("CSV file not found in any expected location", status=500)
        
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
        
        formatted_country = country_mapping.get(selected_country.lower(), selected_country)
        
        for actor in selected_actors:
            formatted_actor = actor_mapping.get(actor.lower(), actor)
            matching_rows = df[(df['country'] == formatted_country) & (df['actor'] == formatted_actor)]
            
            if not matching_rows.empty:
                max_row = matching_rows.loc[matching_rows['FinalRisk'].idxmax()]
                max_score = max_row['FinalRisk']
                max_intent = max_row['intent']
                
                risk_level = "High" if max_score > 0.7 else "Medium" if max_score > 0.4 else "Low"
                
                report_data.append({
                    'actor': actor,
                    'cvi_score': round(float(max_score), 3),
                    'risk_level': risk_level,
                    'primary_threat': max_intent
                })
            else:
                report_data.append({
                    'actor': actor,
                    'cvi_score': 0.0,
                    'risk_level': "N/A",
                    'primary_threat': "No Data"
                })
    except Exception as e:
        logger.error(f"CVI calculation error: {e}")
        report_data = [{'actor': a, 'cvi_score': 0.0, 'risk_level': "N/A", 'primary_threat': "Error"} for a in selected_actors]

    
    # 4. Get Key Narratives & AI Insights
    key_narratives = []
    ai_insights = ""
    try:
        exclude_list = ['football', 'soccer', 'sport', 'sports', 'match', 'game', 'tournament', 'championship']
        base_query = MediaNarrative.objects.filter(target_country__iexact=selected_country)
        for term in exclude_list:
            base_query = base_query.exclude(article_text__icontains=term)
    
        articles_count = base_query.count()
        
        display_articles = base_query.exclude(
            strategic_intent__in=['', None, 'Unknown', 'unknown']
        ).order_by('-posting_time')[:4]
    
        from groq import Groq
        groq_api_key = os.environ.get('GROQ_API_KEY')
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
                    logger.error(f"Article summary error: {e}")
    
            key_narratives.append({
                'intent': article.strategic_intent,
                'tone': article.tone,
                'url': article.url,
                'title': article.article_text[:100] + "...", 
                'media_outlet': article.media_outlet,
                'posting_time': article.posting_time.strftime("%Y-%m-%d") if article.posting_time else "Unknown",
                'summary': ai_summary # uses AI-generated summary
            })
    
        # --- EXECUTIVE AI INSIGHTS ---
        all_articles_for_ai = base_query.exclude(article_text__isnull=True).order_by('-posting_time')[:15]
        full_context_data = [f"Source: {art.media_outlet} | Intent: {art.strategic_intent} | Content: {art.article_text[:500]}" for art in all_articles_for_ai]
        all_text_context = "\n---\n".join(full_context_data)
    
        if client and all_text_context:
            insight_prompt = f"""
            Analyze the following media narratives for {selected_country} as a Senior Geopolitical Analyst.
            Your objective is to evaluate these articles for signs of foreign influence and structural vulnerability.
        
            FORMATTING RULES:
            - Use '###' for clear section headers.
            - Use bold '*' for key terms, actors, and specific intents.
            - Use bullet points for readability.
        
            STRUCTURE:
            ### 📊 Narrative Summary
            (Provide a high-level summary of the media volume, dominant sentiment, and primary themes found in the dataset.)
        
            ### 🛡️ Key Actors & Influence
            (List the primary foreign actors mentioned and their apparent strategic goals or intents as inferred from the narratives.)
        
            ### ⚠️ Influence Threat Analysis
            (Assess the overall likelihood and severity of the influence threat to {selected_country}. Consider if narratives are exploiting local divisions, economic ties, or social fragility.)
            
            DATASET:
            {all_text_context}
            """
            
            chat_completion = client.chat.completions.create(
                messages=[{"role": "user", "content": insight_prompt}],
                model="meta-llama/llama-4-scout-17b-16e-instruct"
            )
            ai_insights = chat_completion.choices[0].message.content
        else:
            ai_insights = "Insufficient data for AI analysis."
    
    except Exception as e:
        logger.error(f"Narrative/AI error: {str(e)}")
        ai_insights = f"AI analysis could not be completed. (Error: {str(e)[:50]})"
        
    # ---  ENSURE CHARTS RENDER IN PDF ---
    volume_chart_base64 = ""
    factor_chart_base64 = ""
    primary_intent = "General Influence"
    
    try:
        # VOLUME CHART
        volume_data = base_query.values('posting_time__date').annotate(count=Count('id')).order_by('posting_time__date')
        if volume_data.exists():
            df_vol = pd.DataFrame(list(volume_data)).rename(columns={'posting_time__date': 'date', 'count': 'articles'})
            df_vol = df_vol.dropna(subset=['date']).sort_values('date').reset_index(drop=True)
            
            fig, ax = plt.subplots(figsize=(8, 4))
            ax.plot(df_vol['date'], df_vol['articles'], marker='o', color='#2563eb')
            plt.xticks(rotation=45)
            
            buf = BytesIO()
            plt.savefig(buf, format='png', bbox_inches='tight')
            volume_chart_base64 = f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode('utf-8')}"
            plt.close(fig)

        # FACTOR CHART
        intent_counts = base_query.exclude(
            strategic_intent__in=['', None, 'Unknown']
        ).values('strategic_intent').annotate(count=Count('id')).order_by('-count')[:5]        
        
        if intent_counts.exists():
            primary_intent = intent_counts[0]['strategic_intent']
            
            df_f = pd.DataFrame(list(intent_counts)).rename(columns={'strategic_intent': 'Factor', 'count': 'Val'})
            df_f = df_f.sort_values('Val', ascending=True).reset_index(drop=True)
            
            fig, ax = plt.subplots(figsize=(7, 4))
            ax.barh(df_f['Factor'], df_f['Val'], color='#38bdf8')
            
            buf = BytesIO()
            plt.savefig(buf, format='png', bbox_inches='tight')
            factor_chart_base64 = f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode('utf-8')}"
            plt.close(fig)

    except Exception as e:
        logger.error(f"Chart Generation Error: {e}")
        # We don't return here; we let the function continue so the PDF is still made

    # --- contexts ---
    context = {
        'country': selected_country,
        'primary_intent': primary_intent,
        'articles_count': articles_count,
        'volume_chart': volume_chart_base64,
        'factor_chart': factor_chart_base64,
        'key_narratives': key_narratives,
        'ai_insights': ai_insights,
        'date_generated': datetime.now().strftime("%B %d, %Y"),
    }

    # Render PDF logic
    template = get_template('report_pdf.html')
    html = template.render(context)
    result = BytesIO()
    pdf = pisa.pisaDocument(BytesIO(html.encode("UTF-8")), result)
    
    if not pdf.err:
        response = HttpResponse(result.getvalue(), content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="CVI_Report_{selected_country}.pdf"'
        return response
    
    return HttpResponse("Error generating PDF", status=500)
