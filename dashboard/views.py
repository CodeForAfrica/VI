from django.shortcuts import render
from django.db.models import Q, Count, Avg
from django.core.paginator import Paginator
from .models import MediaNarrative, Journalist, MediaOutlet
from dashboard.services.summarizer import get_summary
import pandas as pd
import plotly.express as px
from math import isfinite
from django.utils.dateparse import parse_date
from django.http import HttpResponse, JsonResponse
from django.template.loader import get_template
from xhtml2pdf import pisa
from io import BytesIO
from datetime import datetime
import base64
import json
import re
from collections import defaultdict
from django.views.decorators.csrf import csrf_exempt
from django.db.models.functions import TruncMonth, TruncYear
from django.views.decorators.http import require_http_methods
from groq import Groq
from django.conf import settings

# =========================
# CONSTANTS
# =========================

COUNTRIES = ["Senegal", "DRC", "CoteIvoire", "Ethiopia", "South Africa"]
FOREIGN_ACTORS = ['France', 'China', 'UAE', 'Russia', 'US', 'Turkey', 'Saudi Arabia', 'Israel', 'Iran']

# =========================
# CHATBOT ASSISTANCE SYSTEM
# =========================

class DisinfoAnalysisChatbot:
    def __init__(self):
        self.client = Groq(api_key=settings.GROQ_API_KEY)
        self.model = "llama-3.1-8b-instant"

    def get_insights_from_ai(self, query, context):
        """Pure AI Logic using Groq"""
        system_prompt = """
    You are a Strategic Intelligence Analyst for 'Africa Influence Monitor'. 
    Extract geopolitical insights about foreign influence, election integrity, and social stability.
    
    OUTPUT FORMAT (strict):
    SUMMARY: [One sentence: Most critical geopolitical development]
    
    KEY THEMES:
    • [Theme 1]: [Brief description with actor/target country]
    • [Theme 2]: [Brief description with actor/target country]  
    • [Theme 3]: [Brief description with actor/target country]
    
    STRATEGIC IMPLICATIONS:
    • [Impact 1]: [Why this matters for regional influence]
    • [Impact 2]: [Why this matters for stability]
    
    IGNORE: Entertainment, sports, minor market reports, personal stories.
    """
        try:
            chat_completion = self.client.chat.completions.create(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Context:\n{context}\n\nAnalyze: {query}"}
                ],
                model=self.model,
                temperature=0.1,
            )
            return chat_completion.choices[0].message.content
        except Exception as e:
            return f"AI Error: {str(e)}" 
        
    def process_query(self, query):
        query_l = query.lower()

        if any(word in query_l for word in ['how many', 'count', 'total', 'amount']):
            return self._handle_count_query(query_l)
        
        if any(word in query_l for word in ['trend', 'recent', 'latest']):
            recent_data = self._handle_trend_query(query_l)
            return self.get_insights_from_ai(query, recent_data)

        context = self.get_context_from_db(query)
        return self.get_insights_from_ai(query, context)

    def get_context_from_db(self, query):
        keywords = query.split()
        filter_query = Q()
        for word in keywords:
            if len(word) > 3:
                filter_query |= Q(article_text__icontains=word) | Q(target_country__icontains=word)
        
        articles = MediaNarrative.objects.filter(filter_query).order_by('-posting_time')[:5]
        
        if not articles.exists():
            articles = MediaNarrative.objects.all().order_by('-posting_time')[:5]

        context = ""
        for art in articles:
            context += f"Source: {art.media_outlet} | Country: {art.target_country} | Text: {art.article_text[:300]}...\n\n"
        return context
        
    def _handle_count_query(self, query):
        if 'articles' in query or 'narratives' in query:
            total = MediaNarrative.objects.count()
            recent = MediaNarrative.objects.filter(posting_time__isnull=False).order_by('-posting_time').first()
            recent_date = recent.posting_time.strftime('%Y-%m-%d') if recent and recent.posting_time else 'Unknown'
            return f"We have analyzed {total:,} articles. Latest: {recent_date}."
        
        elif 'countries' in query:
            countries = MediaNarrative.objects.exclude(target_country__in=['', 'Unknown', None]).values('target_country').distinct().count()
            return f"Monitoring {countries} countries: {', '.join(COUNTRIES)}."
        
        return "I can provide counts for articles, countries, or actors."

    def _handle_trend_query(self, query):
        recent_articles = MediaNarrative.objects.exclude(title__isnull=True).order_by('-posting_time')[:3]
        if recent_articles.exists():
            titles = [f"'{a.title[:50]}...'" for a in recent_articles]
            return f"Recent activity: {', '.join(titles)}"
        return "No recent data available."

# Global chatbot instance
chatbot = DisinfoAnalysisChatbot()

@csrf_exempt
@require_http_methods(["POST"])
def chatbot_response(request):
    try:
        data = json.loads(request.body)
        user_message = data.get('message', '').strip()
        if user_message:
            return JsonResponse({'response': chatbot.process_query(user_message), 'success': True})
        return JsonResponse({'response': 'Please provide a message.', 'success': False})
    except Exception as e:
        return JsonResponse({'response': f'Error: {str(e)}', 'success': False})

# =========================
# OVERVIEW PAGE
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

    stats = qs.aggregate(
        avg_vulnerability=Avg('vulnerability_index'),
        avg_confidence=Avg('confidence'),
        total=Count('id')
    )

    context = {
        'total_articles': stats['total'],
        'avg_vulnerability': stats['avg_vulnerability'] or 0.0,
        'avg_confidence': stats['avg_confidence'] or 0.0,
        'articles': qs[:10], # Latest 10
    }
    return render(request, 'dashboard/overview.html', context)
