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

def calculate_contextual_score(target_country, foreign_actor):
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
            # Find the row with the HIGHEST FinalRisk score for this country-actor combination
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
    cvi_intent = None  # NEW: Store the intent with highest score
    
    # 2. Capture Inputs
    calc_target_country = request.GET.get('calc_target_country', '').strip()
    calc_foreign_actor = request.GET.get('calc_foreign_actor', '').strip()
    
    # 3. Get total count first (without limit)
    total_articles = MediaNarrative.objects.exclude(
        article_text__icontains='football'
    ).exclude(
        article_text__icontains='soccer'
    ).exclude(
        article_text__icontains='sport'
    ).exclude(
        article_text__icontains='sports'
    ).exclude(
        article_text__icontains='match'
    ).exclude(
        article_text__icontains='game'
    ).exclude(
        article_text__icontains='tournament'
    ).exclude(
        article_text__icontains='championship'
    ).exclude(
        article_text__icontains='olympic'
    ).exclude(
        article_text__icontains='cricket'
    ).exclude(
        article_text__icontains='basketball'
    ).exclude(
        article_text__icontains='tennis'
    ).exclude(
        article_text__icontains='golf'
    ).exclude(
        article_text__icontains='athletics'
    ).exclude(
        article_text__icontains='rugby'
    ).exclude(
        article_text__icontains='boxing'
    ).exclude(
        article_text__icontains='mma'
    ).count()  # This gets all non-sport articles (15,166)
    
    # 4. FOR MAIN DISPLAY: Show ALL articles (no filter) for display - EXCLUDE SPORTS
    full_stats_qs = MediaNarrative.objects.exclude(
        article_text__icontains='football'
    ).exclude(
        article_text__icontains='soccer'
    ).exclude(
        article_text__icontains='sport'
    ).exclude(
        article_text__icontains='sports'
    ).exclude(
        article_text__icontains='match'
    ).exclude(
        article_text__icontains='game'
    ).exclude(
        article_text__icontains='tournament'
    ).exclude(
        article_text__icontains='championship'
    ).exclude(
        article_text__icontains='olympic'
    ).exclude(
        article_text__icontains='cricket'
    ).exclude(
        article_text__icontains='basketball'
    ).exclude(
        article_text__icontains='tennis'
    ).exclude(
        article_text__icontains='golf'
    ).exclude(
        article_text__icontains='athletics'
    ).exclude(
        article_text__icontains='rugby'
    ).exclude(
        article_text__icontains='boxing'
    ).exclude(
        article_text__icontains='mma'
    ).exclude(
        article_text__icontains='fight'
    ).exclude(
        article_text__icontains='league'
    ).exclude(
        article_text__icontains='team'
    ).exclude(
        article_text__icontains='player'
    ).exclude(
        article_text__icontains='coach'
    ).exclude(
        article_text__icontains='stadium'
    ).order_by('-posting_time')

    # 5. Apply calculator filters only if both parameters are provided
    if calc_target_country and calc_foreign_actor:
        # Skip ML inference and use direct CSV lookup
        cvi_score, cvi_intent = calculate_contextual_score(calc_target_country, calc_foreign_actor)
    else:
        # When no calculator parameters, show all articles
        calc_target_country = ""
        calc_foreign_actor = ""

    # 6. Apply calculator filters to main queryset for display if needed
    if calc_target_country and calc_foreign_actor:
        full_stats_qs = full_stats_qs.filter(
            target_country__iexact=calc_target_country,
            inferred_actor__iexact=calc_foreign_actor
        )

    # 7. Global Stats (optimized)
    irrelevant_keywords = ['football', 'soccer', 'entertainment', 'music', 'celebrity', 'fashion']
    
    # 8. Optimized averages
    from django.db.models import Avg
    avg_vulnerability = 0.0
    avg_confidence = 0.0
    
    # 9. Optimized volume chart - Use limited data for performance
    try:
        limited_for_chart = full_stats_qs.exclude(
            posting_time__isnull=True
        )[:500]
        
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

    # 10. Optimized lists - Use counts without limits
    country_list = full_stats_qs.exclude(
        target_country__in=['', 'Unknown', None]
    ).values('target_country').annotate(total=Count('id')).order_by('-total')[:10]
    
    # FIXED: Top Strategic Intents with actor-country relationships
    top_subjects = full_stats_qs.exclude(
        strategic_intent__in=['', None]
    ).exclude(
        inferred_actor__in=['', 'Unknown', None]
    ).exclude(
        target_country__in=['', 'Unknown', None]
    ).values('strategic_intent', 'inferred_actor', 'target_country').annotate(
        total=Count('id')
    ).order_by('-total')[:5]

    # 11. Optimized pagination - Increase page size for more articles per page
    paginator = Paginator(full_stats_qs, 10)  # Increased from 5 to 10
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
            article.vulnerability_index = float(article.vulnerability_index) if article.vulnerability_index is not None else 0.0
        articles_with_vi.append(article)
    page_obj.object_list = articles_with_vi

    # 13. Context - Use the actual total count
    context = {
        'chart': chart,
        'page_obj': page_obj,
        'total_articles': total_articles,  # non-sport articles count
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
        'cvi_intent': cvi_intent,  # Pass the intent to template
        'selected_country': calc_target_country,
        'selected_actor': calc_foreign_actor,
    }
    return render(request, 'overview.html', context)
        
def generate_report(request):
    selected_country = request.GET.get('country')
    selected_actors = request.GET.getlist('actors')

    # 1. Handle Form Display
    if not selected_country or not selected_actors:
        context = {
            'african_countries': COUNTRIES,
            'foreign_actors': FOREIGN_ACTORS,
        }
        return render(request, 'dashboard/report_form.html', context)

    # 2. Setup Data & Normalization
    actor_map = {"US": "UnitedStates"}
    report_data = []

    # 3. Calculate CVI Risk Scores 
    try:
        import pandas as pd
        import os
        
        # Load the CSV file Check multiple possible locations
        current_dir = os.path.dirname(os.path.abspath(__file__))
        
        # Try different possible file locations
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
        
        # Normalize country names
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
            
            # Get scores from  country-actor pair 
            matching_rows = df[(df['country'] == formatted_country) & (df['actor'] == formatted_actor)]
            
            if not matching_rows.empty:
                # Get the highest score across all intents for this country-actor 
                max_row = matching_rows.loc[matching_rows['FinalRisk'].idxmax()]
                max_score = max_row['FinalRisk']
                max_intent = max_row['intent']
                
                risk_level = "High" if max_score > 0.7 else "Medium" if max_score > 0.4 else "Low"
                
                # scores
                report_data.append({
                    'actor': actor,
                    'cvi_score': round(float(max_score), 3),  
                    'risk_level': risk_level,
                    'primary_threat': max_intent
                })
                
                logger.info(f"Report: {formatted_country}-{formatted_actor} = {max_score} ({max_intent})")
            else:
                logger.warning(f"No data found in CSV for {formatted_country}-{formatted_actor}")
                report_data.append({
                    'actor': actor,
                    'cvi_score': 0.0,  # This appears when no data found
                    'risk_level': "N/A",
                    'primary_threat': "No Data"
                })
    except Exception as e:
        logger.error(f"CVI calculation error: {e}")
        report_data = [{'actor': a, 'cvi_score': 0.0, 'risk_level': "N/A", 'primary_threat': "Error"} for a in selected_actors]

    # 4. Get Key Narratives (FIXED: Slicing to exactly 4 and removing truncation for AI context)
    key_narratives = []
    try:
        articles_count = MediaNarrative.objects.filter(
            target_country__iexact=selected_country
        ).exclude(
            article_text__icontains='football'
        ).exclude(
            article_text__icontains='soccer'
        ).exclude(
            article_text__icontains='sport'
        ).exclude(
            strategic_intent__in=['', None, 'Unknown', 'unknown']
        ).count()
        
        country_articles = MediaNarrative.objects.filter(
            target_country__iexact=selected_country
        ).exclude(
            article_text__icontains='football'
        ).exclude(
            article_text__icontains='soccer'
        ).exclude(
            article_text__icontains='sport'
        ).exclude(
            strategic_intent__in=['', None, 'Unknown', 'unknown']
        ).order_by('-intent')[:4]  # STICK TO 4 SAMPLES
        
        for article in country_articles:
            # FIXED: Removed the 500 character limit for 'summary' to provide full context to AI
            full_context = article.article_text if article.article_text else ""
            
            narrative_data = {
                'intent': article.strategic_intent,
                'tone': article.tone,
                #'vulnerability_score': round(float(article.vulnerability_index or 0), 3),
                'url': article.url,
                'title': article.article_text[:100] + "..." if len(article.article_text) > 100 else article.article_text,
                'media_outlet': article.media_outlet,
                'posting_time': article.posting_time.strftime("%Y-%m-%d") if article.posting_time else "Unknown",
                'summary': full_context  # USE FULL ARTICLE FOR ANALYTICAL DEPTH
            }
            key_narratives.append(narrative_data)
    except Exception as e:
        logger.error(f"Key narratives error: {e}")
        key_narratives = []
        articles_count = 0

    # 5. Generate Narrative Volume Chart
    volume_data = MediaNarrative.objects.filter(
        target_country__iexact=selected_country
    ).exclude(
        article_text__icontains='football'
    ).exclude(
        article_text__icontains='soccer'
    ).exclude(
        article_text__icontains='sport'
    ).values('posting_time__date').annotate(count=Count('id')).order_by('posting_time__date')

    volume_chart_base64 = ""
    if volume_data.exists():
        df_vol = pd.DataFrame(list(volume_data))
        df_vol = df_vol.rename(columns={'posting_time__date': 'date', 'count': 'articles'})
        df_vol = df_vol.dropna(subset=['date']).sort_values('date')

        fig, ax = plt.subplots(figsize=(8, 5))
        ax.plot(df_vol['date'], df_vol['articles'], marker='o', color='royalblue')
        ax.set_title(f'Narrative Volume Over Time - {selected_country}')
        ax.grid(True, linestyle='--', alpha=0.7)
        plt.xticks(rotation=45)
        
        img_buffer = BytesIO()
        plt.savefig(img_buffer, format='png', bbox_inches='tight')
        img_buffer.seek(0)
        volume_chart_base64 = base64.b64encode(img_buffer.read()).decode('utf-8')
        plt.close(fig)

    # 6. Generate Factor Contribution Chart
    factor_chart_base64 = ""
    try:
        intent_counts = MediaNarrative.objects.filter(
            target_country__iexact=selected_country
        ).exclude(
            article_text__icontains='football'
        ).exclude(
            article_text__icontains='soccer'
        ).exclude(
            article_text__icontains='sport'
        ).exclude(
            strategic_intent__in=['', None, 'Unknown', 'unknown']
        ).values('strategic_intent').annotate(
            count=Count('id')
        ).order_by('-count')[:5]
        
        if intent_counts.exists():
            df_f = pd.DataFrame(list(intent_counts))
            df_f = df_f.rename(columns={'strategic_intent': 'Factor', 'count': 'Val'})
            
            fig, ax = plt.subplots(figsize=(7, 5))
            ax.barh(df_f['Factor'], df_f['Val'], color='skyblue')
            ax.set_title(f'Top Strategic Intents - {selected_country}')
            
            img_buffer = BytesIO()
            plt.savefig(img_buffer, format='png', bbox_inches='tight')
            img_buffer.seek(0)
            factor_chart_base64 = base64.b64encode(img_buffer.read()).decode('utf-8')
            plt.close(fig)
    except Exception as e:
        logger.error(f"Factor chart error: {e}")

    # 7. Generate AI Insights (FIXED: Using full context, exactly 4 articles, and robust key retrieval)
    ai_insights = ""
    try:
        from groq import Groq
        import os
        
        groq_api_key = os.getenv("GROQ_API_KEY")
        if not groq_api_key:
            ai_insights = "Please configure your GROQ_API key in environment variables."
        elif key_narratives:
            client = Groq(api_key=groq_api_key)
            
            # Using the full article context for the 4 retrieved articles
            article_summaries = []
            for article in key_narratives:
                summary = f"Title: {article['title']}\nIntent: {article['intent']}\nFull Context: {article['summary'][:3000]}\n\n"
                article_summaries.append(summary)
            
            joined_summaries = "\n".join(article_summaries)
            
            prompt = f"""
            Analyze the following 4 media narratives for {selected_country} and provide a concise summary.
            Strictly document main themes, key actors, and recommended actions based only on the provided text.
            
            Articles:
            {joined_summaries}
            """
            
            chat_completion = client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model="meta-llama/llama-4-scout-17b-16e-instruct",  
                timeout=25  # Increased timeout for full text processing
            )
            
            ai_insights = chat_completion.choices[0].message.content
        else:
            ai_insights = "No articles available for analysis."
            
    except Exception as e:
        logger.error(f"Groq API error: {e}")
        ai_insights = f"AI insights temporarily unavailable due to API error: {str(e)}"
    
    # 8. Render PDF
    context = {
        'country': selected_country,
        'report_data': report_data,
        'articles_count': articles_count,
        'date_generated': datetime.now().strftime("%B %d, %Y"),
        'volume_chart_base64': volume_chart_base64,
        'factor_chart_base64': factor_chart_base64,
        'key_narratives': key_narratives,
        'ai_insights': ai_insights,
    }
    
    template = get_template('report_pdf.html')
    html = template.render(context)
    result = BytesIO()
    pdf = pisa.pisaDocument(BytesIO(html.encode("UTF-8")), result)
    
    if not pdf.err:
        response = HttpResponse(result.getvalue(), content_type='application/pdf')
        filename = f"CVI_Report_{selected_country}_{datetime.now().strftime('%Y%m%d')}.pdf"
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response
    
    return HttpResponse("Error generating PDF.", status=500)
    
# =========================
# OTHER PAGES (Countries, Authors, Media, Intents)
# =========================

def countries(request):
    selected_country = request.GET.get('country', '').strip()

    qs = MediaNarrative.objects.all().order_by('-posting_time')

    if selected_country:
        qs = qs.filter(target_country__iexact=selected_country)

    # 1. Top African countries by total articles (target_country)
    top_publishers = MediaNarrative.objects.exclude(
        target_country__in=['', 'Unknown', None]
    ).values('target_country').annotate(
        article_count=Count('id')
    ).order_by('-article_count')[:10]

    publisher_chart = "<p class='text-center py-5 text-muted fs-3'>No publishing data</p>"
    if top_publishers.exists():
        df = pd.DataFrame(list(top_publishers))
        df = df.rename(columns={'target_country': 'Country', 'article_count': 'Articles'})
        df = df.sort_values('Articles')
        
        fig = px.bar(
            df,
            x='Articles',
            y='Country',
            orientation='h',
            title='Top African Countries by Articles Published',
            text='Articles',
            color='Country',
            color_discrete_sequence=px.colors.qualitative.Bold
        )
        fig.update_traces(textposition='outside')
        fig.update_layout(height=500, showlegend=False, template="plotly_white")
        publisher_chart = fig.to_html(full_html=False, include_plotlyjs='cdn')

    # 2. Top subjects mentioned (inferred_actor)
    top_subjects = MediaNarrative.objects.exclude(
        inferred_actor__in=['', 'Unknown', None]
    ).values('inferred_actor').annotate(
        mention_count=Count('id')
    ).order_by('-mention_count')[:10]

    subject_chart = "<p class='text-center py-5 text-muted fs-3'>No subject data</p>"
    if top_subjects.exists():
        df = pd.DataFrame(list(top_subjects))
        df = df.rename(columns={'inferred_actor': 'Actor', 'mention_count': 'Mentions'})
        df = df.sort_values('Mentions')
        
        fig = px.bar(
            df,
            x='Mentions',
            y='Actor',
            orientation='h',
            title='Top Foreign Actors Mentioned in Articles',
            text='Mentions',
            color='Actor',
            color_discrete_sequence=px.colors.qualitative.Set3
        )
        fig.update_traces(textposition='outside')
        fig.update_layout(height=500, showlegend=False, template="plotly_white")
        subject_chart = fig.to_html(full_html=False, include_plotlyjs='cdn')

    # 3. NEW: Top Strategic Intents by Target Country and Actor
    target_country_actor_intents = MediaNarrative.objects.exclude(
        target_country__in=['', 'Unknown', None]
    ).exclude(
        inferred_actor__in=['', 'Unknown', None]
    ).exclude(
        strategic_intent__in=['', 'Unknown', None]
    ).values('target_country', 'inferred_actor', 'strategic_intent').annotate(
        count=Count('id')
    ).order_by('-count')[:20]  # Top 20 combinations

    intent_country_actor_chart = "<p class='text-center py-5 text-muted fs-3'>No intent data by country and actor</p>"
    if target_country_actor_intents.exists():
        df_intent = pd.DataFrame(list(target_country_actor_intents))
        df_intent = df_intent.rename(columns={
            'target_country': 'Country', 
            'inferred_actor': 'Actor', 
            'strategic_intent': 'Intent',
            'count': 'Count'
        })
        
        # Create a combined label for visualization
        df_intent['Combined'] = df_intent['Country'] + ' - ' + df_intent['Actor'] + ': ' + df_intent['Intent']
        
        fig_intent = px.bar(
            df_intent,
            x='Count',
            y='Combined',
            orientation='h',
            title='Top Strategic Intents by Target Country and Actor',
            text='Count',
            color='Intent',
            color_discrete_sequence=px.colors.qualitative.Set2
        )
        fig_intent.update_traces(textposition='outside')
        fig_intent.update_layout(height=600, showlegend=True, template="plotly_white")
        intent_country_actor_chart = fig_intent.to_html(full_html=False, include_plotlyjs='cdn')

    # Simple table of top publishers (since target coverage is 0)
    coverage_table = list(top_publishers)

    sample_articles = qs[:5]

    context = {
        'publisher_chart': publisher_chart,
        'subject_chart': subject_chart,
        'intent_country_actor_chart': intent_country_actor_chart,  # NEW: Include the new chart
        'coverage_table': coverage_table,
        'sample_articles': sample_articles,
        'selected_country': selected_country or "All Countries",
        'african_countries': COUNTRIES,
    }
    return render(request, 'countries.html', context)
    
def authors(request):
    journalist_name = request.GET.get('journalist', '').strip()
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

    paginator = Paginator(articles, 5)  # ✅ 5 articles per page
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
        return render(request, 'dashboard/report_form.html', context)

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
        # Shortened Exclude List
        exclude_list = ['football', 'soccer', 'sport', 'sports', 'match', 'game', 'tournament', 'championship']
        
        # Base Query
        base_query = MediaNarrative.objects.filter(target_country__iexact=selected_country)
        for term in exclude_list:
            base_query = base_query.exclude(article_text__icontains=term)

        articles_count = base_query.count()
        
        # Top 4 for Display (Sorted by strategic_intent)
        display_articles = base_query.exclude(
            strategic_intent__in=['', None, 'Unknown', 'unknown']
        ).order_by('strategic_intent')[:4]
        
        for article in display_articles:
            key_narratives.append({
                'intent': article.strategic_intent,
                'tone': article.tone,
                #'vulnerability_score': round(float(article.vulnerability_index or 0), 3),
                'url': article.url,
                'title': article.article_text, # Full Context
                'media_outlet': article.media_outlet,
                'posting_time': article.posting_time.strftime("%Y-%m-%d") if article.posting_time else "Unknown",
                'summary': article.article_text # Full Context
            })

        # Generate AI Insights from ALL articles
        from groq import Groq
        groq_api_key = os.environ.get('GROQ_API_KEY')
        
        all_articles_for_ai = base_query.exclude(article_text__isnull=True)
        full_context_data = []
        for art in all_articles_for_ai:
            full_context_data.append(f"Source: {art.media_outlet} | Intent: {art.strategic_intent}\nContent: {art.article_text}\n")
        
        all_text_context = "\n---\n".join(full_context_data)

        if groq_api_key and all_text_context:
            client = Groq(api_key=groq_api_key)
            prompt = f"""
            Analyze ALL the following media narratives for {selected_country}.
            These represent the entire dataset for this country. 

            DATASET:
            {all_text_context}

            Provide a comprehensive summary of main themes, key events, key actors and recommended actions based on the FULL context provided above.
            """
            # No timeout, No character limits
            chat_completion = client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model="meta-llama/llama-4-scout-17b-16e-instruct"
            )
            ai_insights = chat_completion.choices[0].message.content
        else:
            ai_insights = "No articles found to analyze or API key missing."

    except Exception as e:
        logger.error(f"Narrative/AI error: {e}")
        ai_insights = "AI insights temporarily unavailable."

    # 5. Generate Narrative Volume Chart
    volume_chart_base64 = ""
    try:
        volume_data = base_query.values('posting_time__date').annotate(count=Count('id')).order_by('posting_time__date')
        if volume_data.exists():
            df_vol = pd.DataFrame(list(volume_data))
            df_vol = df_vol.rename(columns={'posting_time__date': 'date', 'count': 'articles'})
            df_vol = df_vol.dropna(subset=['date']).sort_values('date')

            fig, ax = plt.subplots(figsize=(8, 5))
            ax.plot(df_vol['date'], df_vol['articles'], marker='o', color='royalblue')
            ax.set_title(f'Narrative Volume Over Time - {selected_country}')
            ax.grid(True, linestyle='--', alpha=0.7)
            plt.xticks(rotation=45)
            
            img_buffer = BytesIO()
            plt.savefig(img_buffer, format='png', bbox_inches='tight')
            img_buffer.seek(0)
            volume_chart_base64 = base64.b64encode(img_buffer.read()).decode('utf-8')
            plt.close(fig)
    except Exception as e:
        logger.error(f"Volume chart error: {e}")

    # 6. Generate Factor Contribution Chart
    factor_chart_base64 = ""
    try:
        intent_counts = base_query.exclude(
            strategic_intent__in=['', None, 'Unknown', 'unknown']
        ).values('strategic_intent').annotate(count=Count('id')).order_by('-count')[:5]
        
        if intent_counts.exists():
            df_f = pd.DataFrame(list(intent_counts))
            df_f = df_f.rename(columns={'strategic_intent': 'Factor', 'count': 'Val'})
            
            fig, ax = plt.subplots(figsize=(7, 5))
            ax.barh(df_f['Factor'], df_f['Val'], color='skyblue')
            ax.set_title(f'Top Strategic Intents - {selected_country}')
            
            img_buffer = BytesIO()
            plt.savefig(img_buffer, format='png', bbox_inches='tight')
            img_buffer.seek(0)
            factor_chart_base64 = base64.b64encode(img_buffer.read()).decode('utf-8')
            plt.close(fig)
    except Exception as e:
        logger.error(f"Factor chart error: {e}")

    # 8. Render PDF
    context = {
        'country': selected_country,
        'report_data': report_data,
        'articles_count': articles_count,
        'date_generated': datetime.now().strftime("%B %d, %Y"),
        'volume_chart_base64': volume_chart_base64,
        'factor_chart_base64': factor_chart_base64,
        'key_narratives': key_narratives,
        'ai_insights': ai_insights,
    }

    template = get_template('report_pdf.html')
    html = template.render(context)
    result = BytesIO()
    pdf = pisa.pisaDocument(BytesIO(html.encode("UTF-8")), result)

    if not pdf.err:
        response = HttpResponse(result.getvalue(), content_type='application/pdf')
        filename = f"CVI_Report_{selected_country}_{datetime.now().strftime('%Y%m%d')}.pdf"
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response

    return HttpResponse("Error generating PDF.", status=500)
