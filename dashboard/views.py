from django.shortcuts import render
from django.db.models import Q, Count, Avg
from django.core.paginator import Paginator
from .models import MediaNarrative, Journalist, MediaOutlet
from dashboard.services.summarizer import get_summary
from dashboard.services.ml_inference_service import get_ml_service  # Changed to lazy loading
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
        query_l = query.lower()
        
        # 1. Quick Relevance Check: If user asks about sports/music, pivot back.
        irrelevant = ['football', 'soccer', 'entertainment', 'music', 'celebrity']
        if any(word in query_l for word in irrelevant):
            return "I specialize in geopolitical narratives and vulnerability indices. I don't track sports or entertainment data."

        # 2. Database Stats Logic (enhanced)
        if any(word in query_l for word in ['how many', 'count', 'total']):
            total = MediaNarrative.objects.exclude(article_text__icontains='football').count()
            return f"The database currently contains {total:,} relevant analyzed articles (excluding non-strategic content like sports)."
        
        # 3. Vulnerability Index Info (consistent with overview)
        if any(word in query_l for word in ['vulnerability', 'index', 'score', 'risk']):
            # Same calculation as overview function
            from django.db.models import Avg
            full_stats_qs = MediaNarrative.objects.all()
            filtered_qs = full_stats_qs.exclude(vulnerability_index__isnull=True)
            
            if filtered_qs.exists():
                stats = filtered_qs.aggregate(avg_vulnerability=Avg('vulnerability_index'))
                avg_vulnerability = stats['avg_vulnerability'] if stats['avg_vulnerability'] is not None else 0.0
                avg_str = f"{avg_vulnerability:.3f}"
            else:
                avg_str = "0.000 (scores not yet calculated)"
            
            return f"Vulnerability index: 0-1 scale. Current average: {avg_str}. Scores calculated using ML and geopolitical factors."

        # 4. Geopolitical AI analysis
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

# =========================
# OVERVIEW PAGE (Updated with ML VI Logic)
# =========================
def overview(request):
    # 1. Initialize Safety Defaults
    chart = "<div>No data available</div>"
    country_chart = ""
    country_list = []
    top_subjects = []
    cvi_score = None
    
    # 2. Capture Inputs (for calculator only, not for initial view)
    calc_target_country = request.GET.get('calc_target_country', '').strip()
    calc_foreign_actor = request.GET.get('calc_foreign_actor', '').strip()
    
    # 3. For initial view: SHOW ALL ARTICLES (no filters applied)
    full_stats_qs = MediaNarrative.objects.all().order_by('-posting_time')
    
    # 4. For calculator: Apply filters only when both values are provided
    if calc_target_country and calc_foreign_actor:
        calc_qs = MediaNarrative.objects.all()
        
        # Apply calculator filters
        if any(term in calc_target_country.lower() for term in ["ivoire", "ivory", "cote"]):
            calc_qs = calc_qs.filter(
                Q(target_country__icontains="Ivoire") | 
                Q(target_country__icontains="Cote") | 
                Q(target_country__icontains="Ivory")
            )
        else:
            calc_qs = calc_qs.filter(target_country__iexact=calc_target_country)

        calc_qs = calc_qs.filter(inferred_actor__iexact=calc_foreign_actor)
        calc_article = calc_qs.first()
        
        # Calculate score for calculator
        if calc_article:
            ml_service = get_ml_service()  # Use lazy loading
            cvi_score = ml_service.calculate_vulnerability_index(
                calc_article.strategic_intent or "neutral", 
                calc_article.tone or "neutral", 
                calc_target_country,
                calc_foreign_actor,
                calc_article.confidence or 0.5
            )
    else:
        # No calculator filters, show all articles
        calc_article = None

    # 5. Global Stats (from full dataset - ALL ARTICLES)
    total_articles = full_stats_qs.count()
    
    # Exclude non-relevant topics for all stats
    irrelevant_keywords = ['football', 'soccer', 'entertainment', 'music', 'celebrity', 'fashion']
    for word in irrelevant_keywords:
        full_stats_qs = full_stats_qs.exclude(article_text__icontains=word)

    unique_outlets = full_stats_qs.values('media_outlet').distinct().count()
    unique_intents = full_stats_qs.exclude(strategic_intent__in=['', 'Unknown', None]).values('strategic_intent').distinct().count()
    unique_actors = full_stats_qs.exclude(inferred_actor__in=['', 'Unknown', None]).values('inferred_actor').distinct().count()

    # 6. FIXED: Average vulnerability index and confidence - SAFE AGGREGATION
    from django.db.models import Avg
    try:
        # Filter out NULL values for safe aggregation
        filtered_qs = full_stats_qs.exclude(vulnerability_index__isnull=True)
        
        if filtered_qs.exists():
            stats = filtered_qs.aggregate(
                avg_vulnerability=Avg('vulnerability_index'),
                avg_confidence=Avg('confidence')
            )
            avg_vulnerability = stats['avg_vulnerability'] if stats['avg_vulnerability'] is not None else 0.0
            avg_confidence = stats['avg_confidence'] if stats['avg_confidence'] is not None else 0.0
        else:
            # If no records have vulnerability_index values, calculate them on-the-fly
            avg_vulnerability = 0.0
            avg_confidence = full_stats_qs.aggregate(Avg('confidence'))['confidence__avg'] or 0.0
    except Exception:
        # Fallback if anything goes wrong
        avg_vulnerability = 0.0
        avg_confidence = 0.0

    # 7. Volume Line Chart (show all articles)
    if full_stats_qs.exists():
        try:
            df = pd.DataFrame.from_records(full_stats_qs.values('posting_time'))
            df = df.dropna(subset=['posting_time'])
            df['date'] = pd.to_datetime(df['posting_time'], utc=True).dt.date
            daily_counts = df['date'].value_counts().sort_index().reset_index(name='count')
            
            if not daily_counts.empty:
                fig = px.line(daily_counts, x='date', y='count', template="plotly_white")
                fig.update_layout(margin=dict(l=10, r=10, t=10, b=10), height=300)
                chart = fig.to_html(full_html=False, include_plotlyjs='cdn')
        except Exception as e:
            logger.error(f"Volume Chart Error: {e}")

    # 8. Top Countries Table (show all articles)
    country_list = full_stats_qs.values('target_country') \
        .annotate(total=Count('id')) \
        .order_by('-total')[:10]

    # 9. Top Subjects (show all articles)
    top_subjects = full_stats_qs.exclude(strategic_intent__in=['', None]) \
        .values('strategic_intent') \
        .annotate(total=Count('id')) \
        .order_by('-total')[:5]

    # 10. Pagination (show all articles by default)
    paginator = Paginator(full_stats_qs, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    # 11. Add vulnerability index to articles (calculate if needed)
    ml_service = get_ml_service()  # Use lazy loading
    articles_with_vi = []
    for article in page_obj.object_list:
        # Calculate vulnerability index if it's NULL in the database
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

    # 12. Context
    context = {
        'chart': chart,
        'page_obj': page_obj,
        'total_articles': total_articles,
        'unique_outlets': unique_outlets,
        'unique_intents': unique_intents,
        'unique_actors': unique_actors,
        'avg_vulnerability': round(avg_vulnerability, 3) if avg_vulnerability else 0,
        'avg_confidence': round(avg_confidence, 3) if avg_confidence else 0,
        'african_countries': COUNTRIES,
        'foreign_actors': FOREIGN_ACTORS,
        'country_list': country_list,
        'top_subjects': top_subjects,
        'cvi_score': cvi_score,
        'selected_country': calc_target_country,
        'selected_actor': calc_foreign_actor,
    }
    return render(request, 'overview.html', context)    

# =========================
# OTHER PAGES (Countries, Authors, Media, Intents)
# =========================

def countries(request):
    # 1. Data for the table and bar chart
    top_publishers = MediaNarrative.objects.values('target_country') \
        .annotate(article_count=Count('id')) \
        .order_by('-article_count')[:10]
    
    # 2. Data for the "Top Subjects" chart
    top_subjects = MediaNarrative.objects.exclude(strategic_intent__in=['', None]) \
        .values('strategic_intent') \
        .annotate(total=Count('id')) \
        .order_by('-total')[:10]

    # Generate Publisher Chart
    publisher_chart = ""
    if top_publishers:
        df = pd.DataFrame(list(top_publishers))
        fig = px.bar(df, x='article_count', y='target_country', orientation='h', 
                     color_discrete_sequence=['#6366f1'])
        publisher_chart = fig.to_html(full_html=False, include_plotlyjs='cdn')

    # Generate Subject Chart (The empty box in your screenshot)
    subject_chart = ""
    if top_subjects:
        df_s = pd.DataFrame(list(top_subjects))
        fig_s = px.pie(df_s, values='total', names='strategic_intent', hole=.3)
        subject_chart = fig_s.to_html(full_html=False, include_plotlyjs='cdn')

    context = {
        'publisher_chart': publisher_chart,
        'subject_chart': subject_chart,
        'coverage_table': top_publishers, # This matches the name in your HTML
        'sample_articles': MediaNarrative.objects.all()[:5],
    }
    return render(request, 'dashboard/countries.html', context)
    
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
    return render(request, 'dashboard/intents.html', context)
    
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
    return render(request, 'dashboard/all_articles.html', context)

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
        # Note: Ensure these functions are imported or defined in your services
        g = compute_gs()
        R = compute_R(g)
        CA = compute_CAs(g, R)
        final = compute_finalrisk(CA)

        for actor in selected_actors:
            norm_actor = actor_map.get(actor, actor)
            # Check against your defined constants
            if selected_country in COUNTRIES:
                # Accessing the nested dictionary safely
                score = final.get("Economic", {}).get(norm_actor, {}).get(selected_country, 0.0)
                risk_level = "High" if score > 0.7 else "Medium" if score > 0.4 else "Low"
                report_data.append({
                    'actor': actor,
                    'cvi_score': round(score, 3),
                    'risk_level': risk_level,
                })
    except Exception as e:
        logger.error(f"CVI calculation error: {e}")
        report_data = [{'actor': a, 'cvi_score': 0.0, 'risk_level': "N/A"} for a in selected_actors]

    # 4. Generate Narrative Volume Chart
    articles_count = MediaNarrative.objects.filter(target_country__iexact=selected_country).count()
    volume_data = MediaNarrative.objects.filter(target_country__iexact=selected_country)\
        .values('posting_time__date').annotate(count=Count('id')).order_by('posting_time__date')

    volume_chart_base64 = ""
    if volume_
        df = pd.DataFrame(list(volume_data))
        df = df.rename(columns={'posting_time__date': 'date', 'count': 'articles'})
        df = df.dropna(subset=['date']).sort_values('date')

        fig, ax = plt.subplots(figsize=(8, 5))
        ax.plot(df['date'], df['articles'], marker='o', color='royalblue')
        ax.set_title(f'Narrative Volume Over Time - {selected_country}')
        ax.grid(True, linestyle='--', alpha=0.7)
        plt.xticks(rotation=45)
        
        img_buffer = BytesIO()
        plt.savefig(img_buffer, format='png', bbox_inches='tight')
        img_buffer.seek(0)
        volume_chart_base64 = base64.b64encode(img_buffer.read()).decode('utf-8')
        plt.close(fig)

    # 5. Generate Factor Contribution Chart
    factor_chart_base64 = ""
    try:
        factors = {'Economic': 0.35, 'Sovereignty': 0.18, 'Election': 0.11, 'Social': 0.07} # Sample
        df_f = pd.DataFrame({'Factor': list(factors.keys()), 'Val': list(factors.values())}).sort_values('Val')

        fig, ax = plt.subplots(figsize=(7, 5))
        ax.barh(df_f['Factor'], df_f['Val'], color='skyblue')
        ax.set_title(f'CVI Factor Contribution - {selected_country}')
        
        img_buffer = BytesIO()
        plt.savefig(img_buffer, format='png', bbox_inches='tight')
        img_buffer.seek(0)
        factor_chart_base64 = base64.b64encode(img_buffer.read()).decode('utf-8')
        plt.close(fig)
    except Exception as e:
        logger.error(f"Factor chart error: {e}")

    # 6. Render PDF
    context = {
        'country': selected_country,
        'report_data': report_data,
        'articles_count': articles_count,
        'date_generated': datetime.now().strftime("%B %d, %Y"),
        'volume_chart_base64': volume_chart_base64,
        'factor_chart_base64': factor_chart_base64,
    }

    template = get_template('dashboard/report_pdf.html')
    html = template.render(context)
    result = BytesIO()
    pdf = pisa.pisaDocument(BytesIO(html.encode("UTF-8")), result)

    if not pdf.err:
        response = HttpResponse(result.getvalue(), content_type='application/pdf')
        filename = f"CVI_Report_{selected_country}_{datetime.now().strftime('%Y%m%d')}.pdf"
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response

    return HttpResponse("Error generating PDF.", status=500)
