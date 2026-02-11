from django.shortcuts import render
from django.db.models import Q, Count
from django.core.paginator import Paginator
from .models import MediaNarrative, Journalist, MediaOutlet
from dashboard.services.summarizer import get_summary
from dashboard.services.ml_inference_service import MLInferenceService
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
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from groq import Groq
from django.conf import settings

logger = logging.getLogger(__name__)

# =========================
# CONSTANTS
# =========================
COUNTRIES = ["Senegal", "DRC", "CoteIvoire", "Ethiopia", "South Africa"]
FOREIGN_ACTORS = ['France', 'China', 'UAE', 'Russia', 'US', 'Turkey', 'Saudi Arabia', 'Israel', 'Iran']
TARGET_COUNTRIES = ['France', 'China', 'UAE', 'Russia', 'US', 'Turkey', 'Saudi Arabia', 'Israel', 'Iran']

# =========================
# CHATBOT ASSISTANCE SYSTEM
# =========================
class DisinfoAnalysisChatbot:
    def __init__(self):
        self.client = Groq(api_key=settings.GROQ_API_KEY)
        self.model = "meta-llama/llama-4-scout-17b-16e-instruct"

    def process_query(self, query):
        query_l = query.lower()
        # Simple Logic for counts
        if any(word in query_l for word in ['how many', 'count', 'total']):
            total = MediaNarrative.objects.count()
            return f"The database contains {total:,} analyzed articles."
        
        # Geopolitical AI analysis
        context = self.get_context_from_db(query)
        return self.get_insights_from_ai(query, context)

    def get_insights_from_ai(self, query, context):
        system_prompt = """
        You are an expert analyst explaining media narratives and vulnerability indices related to foreign influence in African countries.
        Your task is to analyze the provided context information and answer the user's query accurately and concisely.
        The context includes retrieved articles and potentially dashboard metrics related to the user's request.
        Provide clear, insightful explanations based solely on the information provided.
        If the context doesn't fully answer the query, say so clearly.
        Cite relevant articles from the retrieved list if possible.
        """

        # Prepare the messages for the LLM
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Context Information:\n{context_from_search}\n\nUser Query: '{prompt}'"}
        ],
                model=self.model,
                temperature=0.1,
            )
            return chat_completion.choices[0].message.content
        except Exception as e:
            return f"AI Error: {str(e)}"

    def get_context_from_db(self, query):
        articles = MediaNarrative.objects.all().order_by('-posting_time')[:5]
        context = ""
        for art in articles:
            context += f"Source: {art.media_outlet} | Text: {art.article_text[:200]}...\n\n"
        return context

chatbot = DisinfoAnalysisChatbot()

@csrf_exempt
@require_http_methods(["POST"])
def chatbot_response(request):
    try:
        data = json.loads(request.body)
        user_message = data.get('message', '').strip()
        return JsonResponse({'response': chatbot.process_query(user_message), 'success': True})
    except Exception as e:
        return JsonResponse({'response': str(e), 'success': False})

# =========================
# OVERVIEW PAGE (Updated with ML VI Logic)
# =========================
def overview(request):
    media_outlet = request.GET.get('media_outlet', '').strip()
    target_country = request.GET.get('target_country', '').strip()
    foreign_actor = request.GET.get('foreign_actor', '').strip()
    intent = request.GET.get('intent', '').strip()
    tone = request.GET.get('tone', '').strip()
    search_query = request.GET.get('q', '').strip()

    qs = MediaNarrative.objects.all().order_by('-posting_time')

    if media_outlet: qs = qs.filter(media_outlet_fk__name__iexact=media_outlet)
    if target_country: qs = qs.filter(target_country__iexact=target_country)
    if foreign_actor: qs = qs.filter(inferred_actor__iexact=foreign_actor)
    if intent: qs = qs.filter(strategic_intent__iexact=intent)
    if tone: qs = qs.filter(tone__iexact=tone)
    if search_query: qs = qs.filter(Q(article_text__icontains=search_query))

    total_articles = qs.count()

    # Full stats for boxes
    full_stats_qs = MediaNarrative.objects.all()
    unique_outlets = full_stats_qs.values('media_outlet').distinct().count()
    unique_intents = full_stats_qs.exclude(strategic_intent='').values('strategic_intent').distinct().count()
    unique_actors = full_stats_qs.exclude(inferred_actor='').values('inferred_actor').distinct().count()

    # Narrative Volume Chart
    chart_qs = MediaNarrative.objects.all()
    if chart_qs.exists():
        df = pd.DataFrame.from_records(chart_qs.values('posting_time'))
        df['date'] = pd.to_datetime(df['posting_time'], utc=True).dt.date
        daily_counts = df['date'].value_counts().sort_index().reset_index(name='count')
        fig = px.line(daily_counts, x='date', y='count', title='Narrative Volume Over Time')
        chart = fig.to_html(full_html=False, include_plotlyjs='cdn')
    else:
        chart = "<div>No data</div>"

    # Pagination
    paginator = Paginator(qs, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    # ✅ VI Calculation: Use ML Service (Replaces old CII logic)
    ml_service = MLInferenceService()
    articles_with_vi = []
    for article in page_obj.object_list:
        try:
            vi_score = ml_service.calculate_vulnerability_index(
                strategic_intent=article.strategic_intent or 'neutral',
                tone=article.tone or 'neutral',
                target_country=article.target_country or 'Unknown',
                inferred_actor=article.inferred_actor or 'NonState',
                confidence=article.confidence or 0.5
            )
            article.vulnerability_index = vi_score
            articles_with_vi.append(article)
        except Exception as e:
            logger.error(f"VI error: {e}")
            article.vulnerability_index = 0.0
            articles_with_vi.append(article)

    page_obj.object_list = articles_with_vi
    avg_vulnerability = sum(a.vulnerability_index for a in articles_with_vi) / len(articles_with_vi) if articles_with_vi else 0.0

    # Tabs set to None to remove from display logic
    context = {
        'chart': chart,
        'page_obj': page_obj,
        'total_articles': total_articles,
        'avg_vulnerability': avg_vulnerability,
        'unique_outlets': unique_outlets,
        'unique_intents': unique_intents,
        'unique_actors': unique_actors,
        'african_countries': COUNTRIES,
        'foreign_actors': FOREIGN_ACTORS,
        'cii_result': None,
        'factor_chart_base64': None,
    }
    return render(request, 'dashboard/overview.html', context)

# =========================
# OTHER PAGES (Countries, Authors, Media, Intents)
# =========================

def countries(request):
    selected_country = request.GET.get('country', '').strip()
    qs = MediaNarrative.objects.all().order_by('-posting_time')
    if selected_country: qs = qs.filter(target_country__iexact=selected_country)
    
    top_publishers = MediaNarrative.objects.values('target_country').annotate(article_count=Count('target_country')).order_by('-article_count')[:10]
    publisher_chart = ""
    if top_publishers:
        df = pd.DataFrame(top_publishers)
        fig = px.bar(df, x='article_count', y='target_country', orientation='h', title='Top Publishers')
        publisher_chart = fig.to_html(full_html=False, include_plotlyjs='cdn')

    context = {
        'publisher_chart': publisher_chart,
        'sample_articles': qs[:5],
        'selected_country': selected_country or "All Countries",
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

def generate_pdf(request):
    qs = MediaNarrative.objects.all()[:50]
    template = get_template('dashboard/report_pdf.html')
    html = template.render({'articles': qs, 'date': datetime.now()})
    result = BytesIO()
    pisa.pisaDocument(BytesIO(html.encode("UTF-8")), result)
    return HttpResponse(result.getvalue(), content_type='application/pdf')
