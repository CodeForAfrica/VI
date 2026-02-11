from django.shortcuts import render
from django.db.models import Q, Count, Avg
from django.core.paginator import Paginator
from .models import MediaNarrative, Journalist, MediaOutlet
from dashboard.services.summarizer import get_summary
import pandas as pd
import plotly.express as px
from math import isfinite
from django.utils.dateparse import parse_date
from django.http import HttpResponse
from django.template.loader import get_template
from xhtml2pdf import pisa
from io import BytesIO
from datetime import datetime
import base64
import json
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse
import re
from collections import defaultdict
from django.db.models.functions import TruncMonth, TruncYear
from django.db.models import DateTimeField
from django.views.decorators.http import require_http_methods
from groq import Groq
from django.conf import settings
from .models import MediaNarrative, MediaOutlet

# =========================
# CII CONSTANTS & HELPERS
# =========================

COUNTRIES = ["Senegal", "DRC", "CoteIvoire", "Ethiopia", "South Africa"]
ACTORS = ["China", "France", "UnitedStates", "Russia", "Rwanda", "Saudi", "Turkey", "UAE", "Israel", "Iran", "NonState"]
FOREIGN_ACTORS = ['France', 'China', 'UAE', 'Russia', 'US', 'Turkey', 'Saudi Arabia', 'Israel', 'Iran']
TARGET_COUNTRIES = ['France', 'China', 'UAE', 'Russia', 'US', 'Turkey', 'Saudi Arabia', 'Israel', 'Iran']

GDP = {
    "Senegal": 33.6e9, "DRC": 70.75e9, "CoteIvoire": 86.54e9, "Ethiopia": 125.0e9, "South Africa": 400.26e9
}

DEBT = {
    "China": {"Senegal": 1410666722.69, "DRC": 2029900000.0, "CoteIvoire": 793390000.0, "Ethiopia": 4000000000.0, "South Africa": 21300000000.0},
    "France": {"Senegal": 280800000.0, "DRC": 0.0, "CoteIvoire": 523800000.0, "Ethiopia": 200000000.0, "South Africa": 756000000.0},
    "UnitedStates": {"Senegal": 91500000.0, "DRC": 0.0, "CoteIvoire": 0.0, "Ethiopia": 100000000.0, "South Africa": 0.0},
    "Saudi": {"Senegal": 110000000.0, "DRC": 0.0, "CoteIvoire": 0.0, "Ethiopia": 50000000.0, "South Africa": 0.0},
    "UAE": {"Senegal": 65600000.0, "DRC": 0.0, "CoteIvoire": 0.0, "Ethiopia": 100000000.0, "South Africa": 0.0},
    "Russia": {"Senegal": 0.0, "DRC": 0.0, "CoteIvoire": 0.0, "Ethiopia": 50000000.0, "South Africa": 0.0},
    "Turkey": {"Senegal": 0.0, "DRC": 0.0, "CoteIvoire": 0.0, "Ethiopia": 200000000.0, "South Africa": 0.0},
    "Israel": {"Senegal": 0.0, "DRC": 0.0, "CoteIvoire": 0.0, "Ethiopia": 50000000.0, "South Africa": 0.0},
    "Iran": {"Senegal": 0.0, "DRC": 0.0, "CoteIvoire": 0.0, "Ethiopia": 10000000.0, "South Africa": 0.0},
    "Rwanda": {"Senegal": 0.0, "DRC": 0.0, "CoteIvoire": 0.0, "Ethiopia": 0.0, "South Africa": 0.0},
    "NonState": {"Senegal": 0.0, "DRC": 0.0, "CoteIvoire": 0.0, "Ethiopia": 0.0, "South Africa": 0.0}
}

G_RES = {
    "China": {"Senegal": 0.10, "DRC": 0.60, "CoteIvoire": 0.09, "Ethiopia": 0.70, "South Africa": 0.20},
    "France": {"Senegal": 0.05, "DRC": 0.05, "CoteIvoire": 0.20, "Ethiopia": 0.10, "South Africa": 0.08},
    "UnitedStates": {"Senegal": 0.40, "DRC": 0.05, "CoteIvoire": 0.0, "Ethiopia": 0.15, "South Africa": 0.10},
    "Russia": {"Senegal": 0.0, "DRC": 0.10, "CoteIvoire": 0.0, "Ethiopia": 0.10, "South Africa": 0.03},
    "NonState": {"Senegal": 0.05, "DRC": 0.05, "CoteIvoire": 0.05, "Ethiopia": 0.05, "South Africa": 0.89},
    "Saudi": {"Senegal": 0.0, "DRC": 0.0, "CoteIvoire": 0.0, "Ethiopia": 0.20, "South Africa": 0.04},
    "UAE": {"Senegal": 0.0, "DRC": 0.0, "CoteIvoire": 0.0, "Ethiopia": 0.50, "South Africa": 0.09},
    "Turkey": {"Senegal": 0.0, "DRC": 0.0, "CoteIvoire": 0.0, "Ethiopia": 0.60, "South Africa": 0.05},
    "Israel": {"Senegal": 0.0, "DRC": 0.0, "CoteIvoire": 0.0, "Ethiopia": 0.10, "South Africa": 0.06},
    "Iran": {"Senegal": 0.0, "DRC": 0.0, "CoteIvoire": 0.0, "Ethiopia": 0.0, "South Africa": 0.03},
    "Rwanda": {"Senegal": 0.0, "DRC": 0.0, "CoteIvoire": 0.0, "Ethiopia": 0.0, "South Africa": 0.0}
}

G_MIL = {
    "China": {"Senegal": 0.33, "DRC": 0.33, "CoteIvoire": 0.0, "Ethiopia": 0.33, "South Africa": 0.33},
    "France": {"Senegal": 0.0, "DRC": 0.33, "CoteIvoire": 0.33, "Ethiopia": 0.0, "South Africa": 0.66},
    "UnitedStates": {"Senegal": 0.66, "DRC": 0.33, "CoteIvoire": 0.66, "Ethiopia": 0.66, "South Africa": 0.66},
    "Russia": {"Senegal": 0.0, "DRC": 0.33, "CoteIvoire": 0.10, "Ethiopia": 0.50, "South Africa": 0.33},
    "Rwanda": {"Senegal": 0.0, "DRC": 0.33, "CoteIvoire": 0.0, "Ethiopia": 0.0, "South Africa": 0.00},
    "NonState": {"Senegal": 0.0, "DRC": 1.00, "CoteIvoire": 0.0, "Ethiopia": 0.33, "South Africa": 0.33},
    "Saudi": {"Senegal": 0.0, "DRC": 0.0, "CoteIvoire": 0.0, "Ethiopia": 0.33, "South Africa": 0.00},
    "UAE": {"Senegal": 0.0, "DRC": 0.0, "CoteIvoire": 0.0, "Ethiopia": 0.33, "South Africa": 0.33},
    "Turkey": {"Senegal": 0.0, "DRC": 0.0, "CoteIvoire": 0.0, "Ethiopia": 0.33, "South Africa": 0.00},
    "Israel": {"Senegal": 0.0, "DRC": 0.0, "CoteIvoire": 0.0, "Ethiopia": 0.33, "South Africa": 0.33},
    "Iran": {"Senegal": 0.0, "DRC": 0.0, "CoteIvoire": 0.0, "Ethiopia": 0.0, "South Africa": 0.00}
}

FSI_RAW = {"Senegal": 74.2, "DRC": 106.7, "CoteIvoire": 85.3, "Ethiopia": 98.1, "South Africa": 69.6}
FSI_MIN, FSI_MAX = 22.0, 120.0
FSI_NORM = {c: max(0.0, min(1.0, (FSI_RAW[c] - FSI_MIN) / (FSI_MAX - FSI_MIN))) for c in COUNTRIES}

L = {"Senegal": 0.90, "DRC": 0.20, "CoteIvoire": 0.20, "Ethiopia": 0.95, "South Africa": 0.05}

ACTOR_DISINFO = {
    "China": {"Senegal": 0.46, "DRC": 0.50, "CoteIvoire": 0.40, "Ethiopia": 0.35, "South Africa": 0.64},
    "France": {"Senegal": 0.84, "DRC": 0.44, "CoteIvoire": 0.82, "Ethiopia": 0.60, "South Africa": 0.32},
    "UnitedStates": {"Senegal": 0.58, "DRC": 0.24, "CoteIvoire": 0.34, "Ethiopia": 0.50, "South Africa": 0.62},
    "Russia": {"Senegal": 0.24, "DRC": 0.50, "CoteIvoire": 0.20, "Ethiopia": 0.65, "South Africa": 0.72},
    "Rwanda": {"Senegal": 0.12, "DRC": 0.56, "CoteIvoire": 0.12, "Ethiopia": 0.05, "South Africa": 0.22},
    "Saudi": {"Senegal": 0.25, "DRC": 0.01, "CoteIvoire": 0.02, "Ethiopia": 0.10, "South Africa": 0.46},
    "UAE": {"Senegal": 0.25, "DRC": 0.02, "CoteIvoire": 0.02, "Ethiopia": 0.60, "South Africa": 0.64},
    "Turkey": {"Senegal": 0.20, "DRC": 0.02, "CoteIvoire": 0.02, "Ethiopia": 0.40, "South Africa": 0.46},
    "Israel": {"Senegal": 0.10, "DRC": 0.02, "CoteIvoire": 0.02, "Ethiopia": 0.20, "South Africa": 0.72},
    "Iran": {"Senegal": 0.08, "DRC": 0.02, "CoteIvoire": 0.02, "Ethiopia": 0.02, "South Africa": 0.42},
    "NonState": {"Senegal": 0.42, "DRC": 0.64, "CoteIvoire": 0.44, "Ethiopia": 0.55, "South Africa": 0.64}
}

ACTOR_ELEC = {
    "China": {"Senegal": 0.32, "DRC": 0.50, "CoteIvoire": 0.10, "Ethiopia": 0.40, "South Africa": 0.54},
    "France": {"Senegal": 0.68, "DRC": 0.08, "CoteIvoire": 0.80, "Ethiopia": 0.60, "South Africa": 0.34},
    "UnitedStates": {"Senegal": 0.46, "DRC": 0.06, "CoteIvoire": 0.06, "Ethiopia": 0.75, "South Africa": 0.60},
    "Russia": {"Senegal": 0.10, "DRC": 0.25, "CoteIvoire": 0.01, "Ethiopia": 0.40, "South Africa": 0.54},
    "Rwanda": {"Senegal": 0.10, "DRC": 0.70, "CoteIvoire": 0.05, "Ethiopia": 0.0, "South Africa": 0.14},
    "NonState": {"Senegal": 0.02, "DRC": 0.10, "CoteIvoire": 0.05, "Ethiopia": 0.30, "South Africa": 0.52},
    "Saudi": {"Senegal": 0.05, "DRC": 0.01, "CoteIvoire": 0.01, "Ethiopia": 0.20, "South Africa": 0.44},
    "UAE": {"Senegal": 0.05, "DRC": 0.02, "CoteIvoire": 0.02, "Ethiopia": 0.50, "South Africa": 0.56},
    "Turkey": {"Senegal": 0.02, "DRC": 0.02, "CoteIvoire": 0.02, "Ethiopia": 0.20, "South Africa": 0.38},
    "Israel": {"Senegal": 0.03, "DRC": 0.02, "CoteIvoire": 0.02, "Ethiopia": 0.30, "South Africa": 0.56},
    "Iran": {"Senegal": 0.02, "DRC": 0.02, "CoteIvoire": 0.02, "Ethiopia": 0.02, "South Africa": 0.36}
}

ACTOR_LGBTQ = {
    "UnitedStates": {"Senegal": 0.70, "DRC": 0.14, "CoteIvoire": 0.14, "Ethiopia": 0.80, "South Africa": 0.85},
    "France": {"Senegal": 0.65, "DRC": 0.13, "CoteIvoire": 0.65, "Ethiopia": 0.70, "South Africa": 0.55},
    "China": {"Senegal": 0.05, "DRC": 0.05, "CoteIvoire": 0.05, "Ethiopia": 0.05, "South Africa": 0.03},
    "Russia": {"Senegal": 0.02, "DRC": 0.02, "CoteIvoire": 0.02, "Ethiopia": 0.02, "South Africa": 0.00},
    "NonState": {"Senegal": 0.00, "DRC": 0.00, "CoteIvoire": 0.00, "Ethiopia": 0.00, "South Africa": 0.50},
    "Saudi": {"Senegal": 0.01, "DRC": 0.01, "CoteIvoire": 0.01, "Ethiopia": 0.01, "South Africa": 0.00},
    "UAE": {"Senegal": 0.02, "DRC": 0.02, "CoteIvoire": 0.02, "Ethiopia": 0.02, "South Africa": 0.00},
    "Turkey": {"Senegal": 0.02, "DRC": 0.02, "CoteIvoire": 0.02, "Ethiopia": 0.02, "South Africa": 0.08},
    "Israel": {"Senegal": 0.01, "DRC": 0.01, "CoteIvoire": 0.01, "Ethiopia": 0.01, "South Africa": 0.38},
    "Iran": {"Senegal": 0.01, "DRC": 0.01, "CoteIvoire": 0.01, "Ethiopia": 0.01, "South Africa": 0.00},
    "Rwanda": {"Senegal": 0.02, "DRC": 0.02, "CoteIvoire": 0.02, "Ethiopia": 0.02, "South Africa": 0.08}
}

INTENT_FACTORS = {
    "Economic": ["debt", "res"],
    "Sovereignty": ["debt", "mil", "elec"],
    "LGBTQ": ["lgbt", "elec"],
    "Religious": ["elec", "mil"],
    "ElectionInfluence": ["elec", "debt", "mil"],
    "MilitaryPresence": ["mil", "debt"],
    "ResourceDependency": ["res", "debt"],
    "SocialFragility": ["frag", "debt", "mil"]
}

def clip(x):
    return max(0.0, min(1.0, float(x)))

def presence_factor(a, c):
    if DEBT.get(a, {}).get(c, 0.0) > 0:
        return 1.0
    if G_MIL.get(a, {}).get(c, 0.0) >= 0.33:
        return 1.0
    if G_RES.get(a, {}).get(c, 0.0) >= 0.10:
        return 1.0
    if any([
        G_MIL.get(a, {}).get(c, 0.0) > 0,
        G_RES.get(a, {}).get(c, 0.0) > 0,
        ACTOR_DISINFO.get(a, {}).get(c, 0.0) > 0.15
    ]):
        return 0.5
    return 0.2

def raw_metrics(a, c, g):
    return {
        "debt": DEBT.get(a, {}).get(c, 0.0),
        "mil": G_MIL.get(a, {}).get(c, 0.0),
        "res": G_RES.get(a, {}).get(c, 0.0),
        "elec": g[a][c]["elec"],
        "lgbt": g[a][c]["lgbt"],
        "frag": g[a][c]["frag"]
    }

def compute_gs():
    g = {a: {c: {} for c in COUNTRIES} for a in ACTORS}
    for a in ACTORS:
        for c in COUNTRIES:
            debt = DEBT.get(a, {}).get(c, 0.0)
            g_debt = clip(debt / GDP[c]) if GDP[c] > 0 else 0.0
            g_res = G_RES.get(a, {}).get(c, 0.0)
            g_mil = G_MIL.get(a, {}).get(c, 0.0)
            months_to_elec = 3 if c == "CoteIvoire" else 999
            g_elec_time = 1 - min(months_to_elec, 24) / 24 if months_to_elec < 999 else 0.0
            elec_index = ACTOR_ELEC.get(a, {}).get(c, 0.0)
            base = 0.25 * presence_factor(a, c)
            g_elec = elec_index * max(g_elec_time, base)
            lgbt_index = ACTOR_LGBTQ.get(a, {}).get(c, 0.0)
            g_lgbt = (1 - L[c]) * lgbt_index
            disinfo_index = ACTOR_DISINFO.get(a, {}).get(c, 0.0)
            g_frag = FSI_NORM[c] * disinfo_index
            g[a][c] = {"debt": g_debt, "res": g_res, "mil": g_mil, "elec": g_elec, "lgbt": g_lgbt, "frag": g_frag}
    return g

def compute_R(g):
    R = {a: {c: {} for c in COUNTRIES} for a in ACTORS}
    for a in ACTORS:
        max_per = {}
        for f in ["debt", "mil", "res", "elec", "lgbt", "frag"]:
            vals = [raw_metrics(a, c, g)[f] for c in COUNTRIES]
            max_per[f] = max(vals) if vals else 0.0
        for c in COUNTRIES:
            m = raw_metrics(a, c, g)
            for f in ["debt", "mil", "res", "elec", "lgbt", "frag"]:
                R[a][c][f] = (m[f] / max_per[f]) if max_per[f] > 0 else 0.0
    return R

def compute_CAs(g, R):
    CA = {intent: {a: {c: 0.0 for c in COUNTRIES} for a in ACTORS} for intent in INTENT_FACTORS}
    for intent, factors in INTENT_FACTORS.items():
        for a in ACTORS:
            for c in COUNTRIES:
                denom = sum(R[a][c].get(f, 0.0) for f in factors)
                if denom == 0:
                    w = {f: 1.0 / len(factors) for f in factors}
                else:
                    w = {f: (R[a][c].get(f, 0.0) / denom) for f in factors}
                CA_val = sum(w[f] * g[a][c].get(f, 0.0) for f in factors)
                CA[intent][a][c] = clip(CA_val)
    return CA

def compute_finalrisk(CA, avg_base=0.4):
    final = {}
    for intent in CA:
        final[intent] = {}
        for a in ACTORS:
            final[intent][a] = {}
            for c in COUNTRIES:
                avg = clip(avg_base)
                ca = CA[intent][a][c]
                final[intent][a][c] = clip(avg + (1.0 - avg) * ca)
    return final

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
    
    EXAMPLE OUTPUT:
    SUMMARY: China increases infrastructure commitments in DRC while France strengthens cultural ties in Côte d'Ivoire ahead of elections.
    
    KEY THEMES:
    • Economic Influence: Chinese firms sign $2B mining deal in DRC
    • Cultural Diplomacy: France launches education exchange in Côte d'Ivoire  
    • Election Monitoring: EU observers deployed to Senegal polls
    
    STRATEGIC IMPLICATIONS:
    • China gains deeper resource access in mineral-rich DRC
    • France builds soft power ahead of Côte d'Ivoire elections
    • EU presence may reduce electoral fraud risks in Senegal
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

        # 1. ROUTING: Check for hardcoded "Exact Data" queries first
        if any(word in query_l for word in ['how many', 'count', 'total', 'amount']):
            return self._handle_count_query(query_l)
        
        if any(word in query_l for word in ['trend', 'recent', 'latest']):
            # We can combine! Get recent titles AND ask AI to summarize them
            recent_data = self._handle_trend_query(query_l)
            return self.get_insights_from_ai(query, recent_data)

        # 2. FALLBACK: Use Groq for everything else (insights/narratives)
        context = self.get_context_from_db(query)
        return self.get_insights_from_ai(query, context)

    def get_context_from_db(self, query):
        # Improved: Filter context by keywords in the query
        # This makes the AI much smarter about specific countries
        keywords = query.split()
        filter_query = Q()
        for word in keywords:
            if len(word) > 3: # Ignore small words like 'the', 'is'
                filter_query |= Q(article_text__icontains=word) | Q(target_country__icontains=word)
        
        articles = MediaNarrative.objects.filter(filter_query).order_by('-posting_time')[:5]
        
        if not articles.exists():
            articles = MediaNarrative.objects.all().order_by('-posting_time')[:5]

        context = ""
        for art in articles:
            context += f"Source: {art.media_outlet} | Country: {art.target_country} | Text: {art.article_text[:300]}...\n\n"
        return context
        
    def _handle_count_query(self, query):
        """Handle queries about counts and numbers"""
        if 'articles' in query or 'narratives' in query:
            total = MediaNarrative.objects.count()
            recent = MediaNarrative.objects.filter(posting_time__isnull=False).order_by('-posting_time').first()
            if recent:
                recent_date = recent.posting_time.strftime('%Y-%m-%d') if recent.posting_time else 'Unknown'
                return f"We have analyzed {total:,} articles in our database. The most recent article was published on {recent_date}."
            else:
                return f"We have analyzed {total:,} articles in our database."
        
        elif 'countries' in query:
            countries = MediaNarrative.objects.exclude(target_country__in=['', 'Unknown', None]).values_list('target_country', flat=True).distinct().count()
            return f"We monitor disinformation activities across {countries} African countries: {', '.join(COUNTRIES)}."
        
        elif 'actors' in query:
            actors = MediaNarrative.objects.exclude(inferred_actor__in=['', 'Unknown', None]).values_list('inferred_actor', flat=True).distinct().count()
            return f"We track {actors} foreign actors: {', '.join(FOREIGN_ACTORS)}."
        
        elif 'outlets' in query:
            outlets = MediaNarrative.objects.exclude(media_outlet__in=['', 'Unknown', None]).values_list('media_outlet', flat=True).distinct().count()
            return f"We monitor content from {outlets} different media outlets."
        
        else:
            return "I can provide information about article counts, country coverage, and actor tracking. What specific count would you like to know?"
    
    def _handle_trend_query(self, query):
        """Handle queries about trends and time series"""
        if 'recent' in query or 'last' in query:
            recent_articles = MediaNarrative.objects.exclude(title__isnull=True).order_by('-posting_time')[:3]
            if recent_articles.exists():
                recent_titles = []
                for article in recent_articles:
                    title_preview = article.title[:50] + '...' if len(article.title) > 50 else article.title
                    date_str = article.posting_time.strftime('%Y-%m-%d') if article.posting_time else 'Unknown'
                    recent_titles.append(f"'{title_preview}' ({date_str})")
                return f"Recent articles: {', '.join(recent_titles)}"
        
        elif 'monthly' in query or 'trend' in query:
            monthly_data = MediaNarrative.objects.annotate(
                month=TruncMonth('posting_time')
            ).values('month').annotate(count=Count('id')).order_by('-month')[:6]
            
            if monthly_data:
                trend_desc = "Based on recent data, disinformation activity has been "
                data_list = list(monthly_data)
                if len(data_list) >= 2:
                    current_month = data_list[0]['count']
                    prev_month = data_list[1]['count']
                    if current_month > prev_month:
                        trend_desc += f"increasing (current: {current_month}, previous: {prev_month})"
                    elif current_month < prev_month:
                        trend_desc += f"decreasing (current: {current_month}, previous: {prev_month})"
                    else:
                        trend_desc += f"stable at around {current_month} articles per month"
                else:
                    trend_desc += f"at {data_list[0]['count']} articles per month"
                
                return f"{trend_desc}. We track monthly trends to identify patterns in disinformation campaigns."
            else:
                return "I don't have enough data to show monthly trends yet."
        
        else:
            return "I can analyze trends in disinformation activity over time. Would you like to know about recent trends or monthly patterns?"
    
    def _handle_average_query(self, query):
        """Handle queries about averages"""
        if 'confidence' in query:
            avg_confidence = MediaNarrative.objects.aggregate(Avg('confidence'))['confidence__avg']
            if avg_confidence:
                return f"The average confidence score of our ML predictions is {avg_confidence:.3f} (scale 0-1). Higher scores indicate more reliable predictions."
        
        elif 'vulnerability' in query or 'risk' in query:
            avg_vulnerability = MediaNarrative.objects.aggregate(Avg('vulnerability_index'))['vulnerability_index__avg']
            if avg_vulnerability:
                return f"The average vulnerability index across all articles is {avg_vulnerability:.3f} (scale 0-1). This combines ML analysis with geopolitical risk factors."
        
        else:
            return "I can provide average values for confidence scores and vulnerability indices. What specific metric would you like to know about?"
    
    def _handle_comparison_query(self, query):
        """Handle comparison queries"""
        if any(word in query for word in ['actors', 'actor', 'foreign']):
            actor_stats = MediaNarrative.objects.exclude(inferred_actor__in=['', 'Unknown', None]).values('inferred_actor').annotate(count=Count('id')).order_by('-count')[:5]
            if actor_stats:
                comparisons = [f"{stat['inferred_actor']}: {stat['count']} articles" for stat in actor_stats]
                return f"Most active actors: {', '.join(comparisons)}"
        
        elif any(word in query for word in ['countries', 'country', 'target']):
            country_stats = MediaNarrative.objects.exclude(target_country__in=['', 'Unknown', None]).values('target_country').annotate(count=Count('id')).order_by('-count')[:5]
            if country_stats:
                comparisons = [f"{stat['target_country']}: {stat['count']} articles" for stat in country_stats]
                return f"Most targeted countries: {', '.join(comparisons)}"
        
        elif any(word in query for word in ['intents', 'intent', 'strategic']):
            intent_stats = MediaNarrative.objects.exclude(strategic_intent__in=['', 'Unknown', None]).values('strategic_intent').annotate(count=Count('id')).order_by('-count')[:5]
            if intent_stats:
                comparisons = [f"{stat['strategic_intent']}: {stat['count']} articles" for stat in intent_stats]
                return f"Most common strategic intents: {', '.join(comparisons)}"
        
        else:
            return "I can compare activity levels between different actors, countries, and strategic intents. What comparison would you like to see?"
    
    def _handle_cii_query(self, query):
        """Handle queries about Country Influence Index"""
        return f"The Country Influence Index (CII) measures foreign influence impact on African countries. It uses factors like debt, military presence, resource control, and election influence. Our model tracks {len(ACTORS)} major actors across {len(COUNTRIES)} African countries using sophisticated geopolitical risk models. Each actor-country pair gets a risk score between 0-1."
    
    def _handle_specific_entity_query(self, query):
        """Handle queries about specific entities"""
        if 'actor' in query:
            # Find specific actor mentions
            for actor in FOREIGN_ACTORS:
                if actor.lower() in query:
                    count = MediaNarrative.objects.filter(inferred_actor__iexact=actor).count()
                    return f"Articles mentioning {actor}: {count} articles. {actor} is typically associated with {self._get_actor_profile(actor)}."
        
        elif 'country' in query:
            # Find specific country mentions
            for country in COUNTRIES:
                if country.lower() in query.replace(' ', ''):
                    count = MediaNarrative.objects.filter(target_country__iexact=country).count()
                    return f"Articles targeting {country}: {count} articles. {country} is monitored for foreign influence activities."
        
        elif 'media' in query or 'outlet' in query:
            # Find specific media outlet mentions
            outlets = MediaNarrative.objects.exclude(media_outlet__in=['', 'Unknown', None]).values_list('media_outlet', flat=True).distinct()[:5]
            return f"We monitor these media outlets: {', '.join(outlets)}. Each outlet is analyzed for disinformation content."
        
        return "I can provide detailed information about specific actors, countries, or media outlets. What would you like to know?"
    
    def _get_actor_profile(self, actor):
        """Get profile information about an actor"""
        profiles = {
            'France': 'focuses on economic ties and cultural influence',
            'China': 'emphasizes infrastructure investments and trade partnerships',
            'US': 'concentrates on security cooperation and democratic institutions',
            'Russia': 'prioritizes military partnerships and energy deals',
            'UAE': 'invests heavily in telecommunications and real estate',
            'Turkey': 'engages in infrastructure projects and diplomatic initiatives',
            'Saudi Arabia': 'focuses on religious and economic partnerships',
            'Israel': 'emphasizes technology and security cooperation',
            'Iran': 'seeks regional influence and economic ties'
        }
        return profiles.get(actor, 'various international activities')
    
    def _handle_general_query(self, query):
        """Handle general informational queries"""
        if any(word in query for word in ['what', 'tell me', 'explain', 'how', 'why']):
            if 'vulnerability' in query or 'risk' in query:
                return "The vulnerability index combines our ML predictions (strategic intent, tone) with geopolitical risk factors (debt, military presence, resource dependency) to assess potential impact of disinformation campaigns. Scores range from 0 (low risk) to 1 (high risk)."
            elif 'strategic intent' in query or 'intent' in query:
                return "Strategic intent refers to the underlying purpose behind narratives. Our ML models classify intents like Economic, Sovereignty, ElectionInfluence, MilitaryPresence, etc., based on content analysis and geopolitical context."
            elif 'tone' in query:
                return "Tone analysis determines the sentiment and emotional framing of articles. This helps identify whether content is positive, negative, critical, or supportive, which indicates potential manipulation tactics."
            elif 'confidence' in query:
                return "Confidence scores (0-1 scale) indicate the reliability of our ML predictions. Higher scores mean the model is more certain about its classifications. We use calibrated ensembles for more accurate confidence estimates."
            elif 'database' in query or 'data' in query:
                return f"Our database contains {MediaNarrative.objects.count():,} articles with rich metadata. We track articles from {MediaNarrative.objects.exclude(media_outlet__in=['', 'Unknown', None]).values_list('media_outlet', flat=True).distinct().count()} media outlets across {len(COUNTRIES)} African countries."
            else:
                return "I can help you understand our disinformation monitoring platform. I can explain our analytics, answer questions about data, and provide insights about trends. What would you like to know?"
        
        return "I'm here to help you understand our disinformation monitoring platform. You can ask me about article counts, trends, confidence scores, vulnerability indices, or any aspect of our analysis. Try asking 'How many articles do you have?' or 'What are the most active actors?'"

# Global chatbot instance
chatbot = DisinfoAnalysisChatbot()

@csrf_exempt
@require_http_methods(["POST"])
def chatbot_response(request):
    """API endpoint for chatbot responses"""
    try:
        data = json.loads(request.body)
        user_message = data.get('message', '').strip()
        
        if user_message:
            bot_response = chatbot.process_query(user_message)
            return JsonResponse({
                'response': bot_response,
                'success': True
            })
        else:
            return JsonResponse({
                'response': 'Please provide a message.',
                'success': False
            })
    except json.JSONDecodeError:
        return JsonResponse({
            'response': 'Invalid JSON format.',
            'success': False
        })
    except Exception as e:
        return JsonResponse({
            'response': f'Error processing your request: {str(e)}',
            'success': False
        })

# =========================
# OVERVIEW PAGE
# =========================
def overview(request):
    # Get filter parameters
    media_outlet = request.GET.get('media_outlet', '').strip()
    target_country = request.GET.get('target_country', '').strip()
    foreign_actor = request.GET.get('foreign_actor', '').strip()
    intent = request.GET.get('intent', '').strip()
    tone = request.GET.get('tone', '').strip()
    search_query = request.GET.get('q', '').strip()

    # Base queryset for filtered articles
    qs = MediaNarrative.objects.all().order_by('-posting_time')

    # Apply filters to articles list only
    if media_outlet:
        qs = qs.filter(media_outlet_fk__name__iexact=media_outlet)
    if target_country:
        qs = qs.filter(target_country__iexact=target_country)
    if foreign_actor:
        qs = qs.filter(inferred_actor__iexact=foreign_actor)
    if intent:
        qs = qs.filter(strategic_intent__iexact=intent)
    if tone:
        qs = qs.filter(tone__iexact=tone)
    if search_query:
        qs = qs.filter(Q(article_text__icontains=search_query))

    total_articles = qs.count()

    # Key Stats (always from full dataset)
    full_stats_qs = MediaNarrative.objects.all()
    unique_outlets = full_stats_qs.values('media_outlet').distinct().count()
    unique_intents = full_stats_qs.exclude(strategic_intent__exact='').values('strategic_intent').distinct().count()
    unique_actors = full_stats_qs.exclude(inferred_actor__exact='').values('inferred_actor').distinct().count()

    # Chart: ALWAYS full narrative volume over time (unaffected by filters)
    chart_qs = MediaNarrative.objects.all()
    if chart_qs.exists():
        df = pd.DataFrame.from_records(chart_qs.values('posting_time'))
        df['date'] = pd.to_datetime(df['posting_time'], utc=True).dt.date
        daily_counts = df['date'].value_counts().sort_index().reset_index(name='count')

        if not daily_counts.empty:
            fig = px.line(
                daily_counts,
                x='date',
                y='count',
                title='Narrative Volume Over Time',
                labels={'date': 'Date', 'count': 'Number of Articles'}
            )
            fig.update_layout(height=500, template="plotly_white")
            chart = fig.to_html(full_html=False, include_plotlyjs='cdn')
        else:
            chart = "<div class='text-center py-5'><p class='text-muted fs-4'>No date data available for chart.</p></div>"
    else:
        chart = "<div class='text-center py-5'><p class='text-muted fs-4'>No articles in database.</p></div>"

    # Pagination for filtered articles
    paginator = Paginator(qs, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    # Filter choices for dropdowns
    outlet_choices = MediaNarrative.objects.values('media_outlet').distinct().order_by('media_outlet')
    country_choices = MediaNarrative.objects.values('target_country').distinct().order_by('target_country')
    intent_choices = MediaNarrative.objects.values('strategic_intent').distinct().order_by('strategic_intent')
    tone_choices = MediaNarrative.objects.values('tone').distinct().order_by('tone')

    # CVI Calculation + Factor Bar Chart (matplotlib - forced 0-100% scale)
    cii_result = None
    selected_african_country = request.GET.get('african_country')
    selected_foreign_actor = request.GET.get('foreign_actor')
    factor_chart_base64 = None

    if selected_african_country and selected_foreign_actor:
        actor_map = {"US": "UnitedStates", "USA": "UnitedStates"}
        norm_actor = actor_map.get(selected_foreign_actor, selected_foreign_actor)

        if selected_african_country in COUNTRIES and norm_actor in ACTORS:
            try:
                g = compute_gs()
                R = compute_R(g)
                CA = compute_CAs(g, R)
                final = compute_finalrisk(CA)

                # Safe access to CVI score
                economic_data = final.get("Economic", {})
                actor_data = economic_data.get(norm_actor, {})
                score = actor_data.get(selected_african_country, 0.0)
                cii_result = round(score, 3) if score else "N/A"

                # Real factor scores (from your debug structure)
                factors_raw = {}
                for factor_key in ['Economic', 'Sovereignty', 'LGBTQ', 'Religious', 'ElectionInfluence', 'MilitaryPresence', 'ResourceDependency', 'SocialFragility']:
                    factor_data = final.get(factor_key, {})
                    actor_factor_data = factor_data.get(norm_actor, {})
                    factors_raw[factor_key] = actor_factor_data.get(selected_african_country, 0.0)

                # Remove zero factors
                factors_raw = {k: v for k, v in factors_raw.items() if v > 0}

                # Normalize to 100%
                total_raw = sum(factors_raw.values())
                percentages = {}
                if total_raw > 0:
                    percentages = {k: round(v / total_raw * 100, 1) for k, v in factors_raw.items()}

                # Generate bar chart with matplotlib - forced 0-100% scale
                if percentages:
                    import matplotlib.pyplot as plt

                    df = pd.DataFrame({
                        'Factor': list(percentages.keys()),
                        'Percentage': list(percentages.values())
                    }).sort_values('Percentage', ascending=True)

                    fig, ax = plt.subplots(figsize=(6, 5))
                    bars = ax.barh(df['Factor'], df['Percentage'], color='skyblue')
                    ax.set_xlabel('Contribution (%)')
                    ax.set_title('CVI Factor Contributions (100% Total)')
                    ax.invert_yaxis()
                    ax.grid(axis='x', linestyle='--', alpha=0.7)

                    # Force x-axis to always go from 0 to 100%
                    ax.set_xlim(0, 100)

                    # Add percentage labels on bars
                    for bar in bars:
                        width = bar.get_width()
                        ax.text(width + 1, bar.get_y() + bar.get_height()/2,
                                f'{width}%', va='center', fontsize=10)

                    img_buffer = BytesIO()
                    plt.savefig(img_buffer, format='png', bbox_inches='tight')
                    img_buffer.seek(0)
                    factor_chart_base64 = base64.b64encode(img_buffer.read()).decode('utf-8')
                    plt.close(fig)
                else:
                    factor_chart_base64 = None

            except Exception as e:
                cii_result = f"Calculation error: {str(e)}"
                print(f"CVI calculation failed: {e}")

    context = {
        'chart': chart,
        'page_obj': page_obj,
        'total_articles': total_articles,
        'unique_outlets': unique_outlets,
        'unique_intents': unique_intents,
        'unique_actors': unique_actors,

        'outlet_choices': outlet_choices,
        'country_choices': country_choices,
        'intent_choices': intent_choices,
        'tone_choices': tone_choices,
        'foreign_actors': FOREIGN_ACTORS,

        'selected_outlet': media_outlet,
        'selected_country': target_country,
        'selected_actor': foreign_actor,
        'selected_intent': intent,
        'selected_tone': tone,
        'search_query': search_query,

        # CVI variables
        'african_countries': COUNTRIES,
        'foreign_actors': FOREIGN_ACTORS,
        'selected_african_country': selected_african_country,
        'selected_foreign_actor': selected_foreign_actor,
        'cii_result': cii_result,
        'factor_chart_base64': factor_chart_base64,
    }

    return render(request, 'overview.html', context)
# =========================
# COUNTRIES PAGE
# =========================
def countries(request):
    selected_country = request.GET.get('country', '').strip()

    qs = MediaNarrative.objects.all().order_by('-posting_time')

    if selected_country:
        qs = qs.filter(target_country__iexact=selected_country)

    # 1. Top African countries by total articles (target_country)
    top_publishers = MediaNarrative.objects.exclude(target_country__in=['', 'Unknown', None]).values('target_country').annotate(
        article_count=Count('target_country')
    ).order_by('-article_count')[:10]

    publisher_chart = "<p class='text-center py-5 text-muted fs-3'>No publishing data</p>"
    if top_publishers:
        df = pd.DataFrame(top_publishers)
        df['target_country'] = df['target_country'].astype(str) # Force string type
        df = df.sort_values('article_count')
        
        fig = px.bar(
            df,
            x='article_count',
            y='target_country',
            orientation='h',
            title='Top African Countries by Articles Published',
            text='article_count',
            template="plotly_white" # Removed color='target_country' to prevent KeyError
        )
        # Apply colors safely via traces
        fig.update_traces(marker_color=px.colors.qualitative.Bold, textposition='outside')
        fig.update_layout(height=500, showlegend=False, template="plotly_white")
        publisher_chart = fig.to_html(full_html=False, include_plotlyjs='cdn')

    # 2. Top subjects mentioned (inferred_actor)
    top_subjects = MediaNarrative.objects.exclude(inferred_actor__in=['', 'Unknown', None]).values('inferred_actor').annotate(
        mention_count=Count('inferred_actor')
    ).order_by('-mention_count')[:10]

    subject_chart = "<p class='text-center py-5 text-muted fs-3'>No subject data</p>"
    if top_subjects:
        df = pd.DataFrame(top_subjects)
        df['inferred_actor'] = df['inferred_actor'].astype(str) # Force string type
        df = df.sort_values('mention_count')
        
        fig = px.bar(
            df,
            x='mention_count',
            y='inferred_actor',
            orientation='h',
            title='Top Subjects Mentioned in Articles',
            text='mention_count',
            template="plotly_white" # Removed color='inferred_actor'
        )
        # Apply colors safely via traces
        fig.update_traces(marker_color=px.colors.qualitative.Set3, textposition='outside')
        fig.update_layout(height=500, showlegend=False, template="plotly_white")
        subject_chart = fig.to_html(full_html=False, include_plotlyjs=False)

    # Simple table of top publishers
    coverage_table = list(top_publishers)

    sample_articles = qs[:5]

    context = {
        'publisher_chart': publisher_chart,
        'subject_chart': subject_chart,
        'coverage_table': coverage_table,
        'sample_articles': sample_articles,
        'selected_country': selected_country or "All Countries",
    }
    return render(request, 'dashboard/countries.html', context)

# =========================
# AUTHORS PAGE
# =========================
def authors(request):
    journalist_name = request.GET.get('journalist', '').strip()

    qs = MediaNarrative.objects.all().order_by('-posting_time')

    if journalist_name:
        qs = qs.filter(journalist_fk__name__iexact=journalist_name)

    # Top 10 Journalists from the Journalist model
    top_journalists = Journalist.objects.annotate(
        article_count=Count('articles')
    ).order_by('-article_count')[:10]

    # Chart: Top Journalists by Articles
    if top_journalists.exists():
        # Convert to DataFrame
        df = pd.DataFrame(list(top_journalists.values('name', 'article_count')))
        
        # 1. CLEAN DATA: Remove any rows with empty names and ensure names are strings
        df = df.dropna(subset=['name'])
        df['name'] = df['name'].astype(str)
        df = df.sort_values('article_count')

        # 2. CREATE FIGURE: We remove 'color' from the initial call to avoid the KeyError
        fig = px.bar(
            df,
            x='article_count',
            y='name',
            orientation='h',
            title='Top 10 Journalists by Articles Published',
            text='article_count',
            template="plotly_white"
        )
        
        # 3. APPLY STYLING: Add the color sequence safely without grouping by name
        fig.update_traces(
            marker_color=px.colors.qualitative.Bold,
            textposition='outside'
        )
        
        fig.update_layout(
            height=500,
            showlegend=False,
            xaxis_title="Number of Articles",
            yaxis_title="Journalist",
            margin=dict(l=150) # Gives space for longer names like 'Saphiétou Mbengue'
        )
        
        authors_chart = fig.to_html(full_html=False, include_plotlyjs='cdn')
    else:
        authors_chart = "<p class='text-center text-muted py-5'>No journalist data available.</p>"
    # Pagination
    paginator = Paginator(qs, 5)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    # Selected Journalist Profile
    selected_journalist = None
    if journalist_name:
        selected_journalist = Journalist.objects.filter(name__iexact=journalist_name).first()

    context = {
        'authors_chart': authors_chart,
        'top_journalists': top_journalists,
        'page_obj': page_obj,
        'selected_journalist': selected_journalist,
        'selected_name': journalist_name or "All Journalists",
    }
    return render(request, 'dashboard/authors.html', context)
    
def actors(request):
    actor_name = request.GET.get('actor', '').strip()

    # Filter articles if an actor is selected
    qs = MediaNarrative.objects.all().order_by('-posting_time')
    if actor_name:
        qs = qs.filter(inferred_actor__iexact=actor_name)

    # Aggregate top actors
    top_actors_data = (
        MediaNarrative.objects
        .exclude(inferred_actor__in=['', 'Unknown', None])
        .values('inferred_actor')
        .annotate(article_count=Count('id'))
        .order_by('-article_count')[:10]
    )

    actor_chart = ""
    if top_actors_data:
        df = pd.DataFrame(list(top_actors_data))
        # Ensure data is clean for Plotly
        df['inferred_actor'] = df['inferred_actor'].astype(str)
        df = df.sort_values('article_count')

        fig = px.bar(
            df,
            x='article_count',
            y='inferred_actor',
            orientation='h',
            title='Top Actors by Mention Count',
            labels={'article_count': 'Number of Mentions', 'inferred_actor': 'Actor'},
            text='article_count',
            template="plotly_white"
        )
        # Apply colors safely via traces to avoid KeyError
        fig.update_traces(marker_color='indianred', textposition='outside')
        fig.update_layout(height=500, showlegend=False)
        actor_chart = fig.to_html(full_html=False, include_plotlyjs='cdn')
    else:
        actor_chart = "<p class='text-center py-5 text-muted'>No actor data available.</p>"

    # Pagination
    paginator = Paginator(qs, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    context = {
        'actor_chart': actor_chart,
        'page_obj': page_obj,
        'selected_name': actor_name or "All Actors",
        'top_actors_list': top_actors_data,
    }
    return render(request, 'dashboard/actors.html', context)
    
def articles_view(request):
    search_query = request.GET.get("q", "")

    # Using MediaNarrative instead of Article
    articles = MediaNarrative.objects.all().order_by("-posting_time")

    if search_query:
        articles = articles.filter(article_text__icontains=search_query)

    paginator = Paginator(articles, 5)  # 5 articles per page
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    context = {
        "page_obj": page_obj,
        "search_query": search_query,
    }
    return render(request, "articles.html", context)

def media(request):
    outlet_name = request.GET.get('outlet', '').strip()

    # 1. Top 10 Outlets (The "Leaderboard")
    # This remains the core value of the page: seeing market share of narratives.
    top_outlets = MediaOutlet.objects.annotate(
        article_count=Count('articles')
    ).order_by('-article_count')[:10]

    # 2. Simplified Chart Logic
    media_chart = ""
    if top_outlets.exists():
        data = list(top_outlets.values('name', 'article_count'))
        df = pd.DataFrame(data)
        df['name'] = df['name'].astype(str)
        df = df.sort_values('article_count', ascending=True)

        fig = px.bar(
            df,
            x='article_count',
            y='name',
            orientation='h',
            title='Most Frequent Media Outlets',
            labels={'article_count': 'Total Articles', 'name': 'Source'},
            text='article_count',
            template="plotly_white"
        )
        fig.update_traces(marker_color='#1a2a6c', textposition='outside')
        fig.update_layout(height=450, margin=dict(l=20, r=20, t=50, b=20))
        media_chart = fig.to_html(full_html=False, include_plotlyjs='cdn')

    # 3. Selected Outlet Profile (Quick Insights)
    selected_outlet = None
    top_intents = []
    
    if outlet_name:
        selected_outlet = MediaOutlet.objects.filter(name__iexact=outlet_name).first()
        
        # New Insight: Show the top 3 themes (Strategic Intents) for this outlet
        # This tells the user: "When this outlet speaks, it's usually about [X]"
        top_intents = MediaNarrative.objects.filter(media_outlet_fk__name__iexact=outlet_name) \
                        .values('strategic_intent') \
                        .annotate(intent_count=Count('id')) \
                        .order_by('-intent_count')[:3]

    context = {
        'media_chart': media_chart,
        'top_outlets': top_outlets,
        'selected_outlet': selected_outlet,
        'selected_name': outlet_name or "All Media Sources",
        'top_intents': top_intents,
    }
    return render(request, 'dashboard/media.html', context)
    
def intents(request):
    intent_name = request.GET.get('intent', '').strip()

    qs = MediaNarrative.objects.all().order_by('-posting_time')

    if intent_name:
        qs = qs.filter(strategic_intent__iexact=intent_name)

    # Top 10 Strategic Intents
    top_intents = (
        MediaNarrative.objects
        .exclude(strategic_intent__in=['', 'Unknown', None])
        .values('strategic_intent')
        .annotate(article_count=Count('strategic_intent'))
        .order_by('-article_count')[:10]
    )

    # Chart 1: Top 10 Intents
    if top_intents:
        df = pd.DataFrame(top_intents).sort_values('article_count')
        fig = px.bar(
            df,
            x='article_count',
            y='strategic_intent',
            orientation='h',
            title='Top 10 Strategic Intents by Articles',
            text='article_count',
            color='strategic_intent',
            color_discrete_sequence=px.colors.qualitative.Bold
        )
        fig.update_traces(textposition='outside')
        fig.update_layout(height=500, showlegend=False, template="plotly_white")
        intents_chart = fig.to_html(full_html=False, include_plotlyjs='cdn')
    else:
        intents_chart = "<p class='text-center text-muted'>No strategic intent data available.</p>"

    # Chart 2: Top 5 Strategic Intents per Target Country
    # Get top 5 intents overall (from full dataset)
    top_5_intents = (
        MediaNarrative.objects
        .exclude(strategic_intent__in=['', 'Unknown', None])
        .values('strategic_intent')
        .annotate(count=Count('strategic_intent'))
        .order_by('-count')[:5]
    )

    top_intent_list = [item['strategic_intent'] for item in top_5_intents]

    # Get data for target countries and top 5 intents (using filtered qs if intent selected)
    intents_per_country_data = qs.filter(
        inferred_actor__in=TARGET_COUNTRIES,
        strategic_intent__in=top_intent_list
    ).values('inferred_actor', 'strategic_intent').annotate(
        count=Count('id')
    )

    country_chart = "<p class='text-center text-muted py-5 fs-4'>No data for top intents in target countries.</p>"
    if intents_per_country_data.exists():
        df_country = pd.DataFrame(intents_per_country_data)

        # Pivot with sum to handle duplicates
        pivot_df = df_country.pivot_table(
            index='inferred_actor',
            columns='strategic_intent',
            values='count',
            aggfunc='sum',
            fill_value=0
        ).reset_index()

        # Ensure all top 5 intents are columns
        for intent in top_intent_list:
            if intent not in pivot_df.columns:
                pivot_df[intent] = 0

        fig_country = px.bar(
            pivot_df,
            x='inferred_actor',
            y=top_intent_list,
            title='Top 5 Strategic Intents per Target Country',
            barmode='group',
            color_discrete_sequence=px.colors.qualitative.Bold
        )
        fig_country.update_layout(
            height=500,
            template="plotly_white",
            xaxis_title="Target Country",
            yaxis_title="Number of Articles",
            legend_title="Strategic Intent"
        )
        country_chart = fig_country.to_html(full_html=False, include_plotlyjs=False)

    # Pagination
    paginator = Paginator(qs, 5)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    context = {
        'intents_chart': intents_chart,
        'country_chart': country_chart,
        'top_intents': top_intents,
        'page_obj': page_obj,
        'selected_intent': intent_name or "All Intents",
    }
    return render(request, 'dashboard/intents.html', context)

def cii(request):
    cii_result = None
    cii_intent = "Economic"  # We use Economic as main intent for CII
    selected_country = request.GET.get('african_country')
    selected_actor = request.GET.get('foreign_actor')

    if selected_country and selected_actor:
        # Normalize US
        actor_map = {"US": "UnitedStates", "USA": "UnitedStates"}
        norm_actor = actor_map.get(selected_actor, selected_actor)

        if selected_country in COUNTRIES and norm_actor in ACTORS:
            try:
                g = compute_gs()
                R = compute_R(g)
                CA = compute_CAs(g, R)
                final = compute_finalrisk(CA)
                score = final["Economic"][norm_actor][selected_country]
                cii_result = round(score, 3)
            except Exception:
                cii_result = "Calculation error"

    context = {
        'cii_result': cii_result,
        'selected_country': selected_country,
        'selected_actor': selected_actor,
        'african_countries': COUNTRIES,
        'foreign_actors': FOREIGN_ACTORS,
        'cii_intent': cii_intent,
    }
    return render(request, 'dashboard/cii.html', context)

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
    selected_actors = request.GET.getlist('actors')  # Multiple selection allowed

    # Show form if no selection made
    if not selected_country or not selected_actors:
        context = {
            'african_countries': COUNTRIES,
            'foreign_actors': FOREIGN_ACTORS,
        }
        return render(request, 'dashboard/report_form.html', context)

    # Normalize actor names (e.g., US → UnitedStates)
    actor_map = {"US": "UnitedStates"}
    
    # Calculate CVI for each selected actor
    report_data = []
    g = compute_gs()
    R = compute_R(g)
    CA = compute_CAs(g, R)
    final = compute_finalrisk(CA)

    for actor in selected_actors:
        norm_actor = actor_map.get(actor, actor)
        if selected_country in COUNTRIES and norm_actor in ACTORS:
            score = final["Economic"][norm_actor][selected_country]
            risk_level = "High" if score > 0.7 else "Medium" if score > 0.4 else "Low"
            report_data.append({
                'actor': actor,
                'cvi_score': round(score, 3),
                'risk_level': risk_level,
            })

    # Total articles for the country
    articles_count = MediaNarrative.objects.filter(
        target_country__iexact=selected_country
    ).count()

    # Narrative Volume Over Time Chart
    volume_data = MediaNarrative.objects.filter(
        target_country__iexact=selected_country
    ).values('posting_time__date')\
     .annotate(count=Count('id'))\
     .order_by('posting_time__date')

    volume_chart_base64 = ""
    if volume_data:
        df = pd.DataFrame(list(volume_data))
        df = df.rename(columns={'posting_time__date': 'date', 'count': 'articles'})
        df = df.dropna(subset=['date'])
        df = df.sort_values('date')

        fig = px.line(
            df,
            x='date',
            y='articles',
            title=f'Narrative Volume Over Time - {selected_country}',
            labels={'date': 'Date', 'articles': 'Number of Articles'}
        )
        fig.update_layout(
            height=450,
            template="plotly_white",
            margin=dict(l=50, r=50, t=80, b=50)
        )

        # Save chart as PNG and encode to base64 for PDF
        img_buffer = BytesIO()
        fig.write_image(img_buffer, format="png", engine="kaleido")
        img_buffer.seek(0)
        volume_chart_base64 = base64.b64encode(img_buffer.read()).decode('utf-8')
    else:
        volume_chart_base64 = None  # No chart if no data

    # Context for PDF template
    context = {
        'country': selected_country,
        'report_data': report_data,
        'articles_count': articles_count,
        'date_generated': datetime.now().strftime("%B %d, %Y"),
        'volume_chart_base64': volume_chart_base64,
    }

    # Generate PDF
    template = get_template('dashboard/report_pdf.html')
    html = template.render(context)
    result = BytesIO()
    pdf = pisa.pisaDocument(BytesIO(html.encode("UTF-8")), result, encoding='UTF-8')

    if not pdf.err:
        response = HttpResponse(result.getvalue(), content_type='application/pdf')
        filename = f"CVI_Report_{selected_country}_{datetime.now().strftime('%Y%m%d')}.pdf"
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response

    return HttpResponse("Error generating PDF. Please try again.", status=500)
