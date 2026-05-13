# dashboard/views.py
import os
import re
import io
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from django.shortcuts import render
from django.db.models import Q, Avg, Count
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.core.cache import cache
import boto3
import requests
from .models import MediaNarrative, Journalist, MediaOutlet, VulnerabilityIndex
from dashboard.services.summarizer import get_summary
from dashboard.services.ml_inference_service import get_ml_service # Changed to lazy loading function
<<<<<<< hanna-tes-patch-2
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go 
=======

>>>>>>> main
from math import isfinite
from django.http import HttpResponse, JsonResponse
from django.template.loader import get_template
from xhtml2pdf import pisa
from io import BytesIO
from datetime import datetime
import base64

import json
import base64
import logging
import requests
import urllib3
import boto3
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import matplotlib
matplotlib.use('Agg') 
import matplotlib.pyplot as plt

from datetime import datetime
from io import BytesIO
from math import isfinite
from botocore.exceptions import ClientError, NoCredentialsError
from groq import Groq
from xhtml2pdf import pisa

from django.shortcuts import render
from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.template.loader import get_template
from django.core.cache import cache 
from django.db.models import Q, Count, Avg, Sum
from django.db.models.functions import TruncMonth
from django.utils.dateparse import parse_date
from botocore.exceptions import ClientError, NoCredentialsError
from dashboard.services.ml_inference_service import MLInferenceService
from .utils import calculate_contextual_score, map_to_canonical_intent



logger = logging.getLogger(__name__)

print("---------------------------------------------")
print(f"SERVER STARTING IN: {os.getcwd()}")
print(f"FILES VISIBLE HERE: {os.listdir(os.getcwd())}")
print("---------------------------------------------")


def clear_cache_view(request):
    """
    Clears the Django cache to ensure the 821 new articles 
    from the data migration show up on the dashboard.
    """
    cache.clear()
    return HttpResponse("Cache cleared successfully! Refresh your dashboard now.")


# =========================
# CONSTANTS
# =========================

COUNTRIES = [
    "Senegal", "Ethiopia", "Côte d'Ivoire", "DRC", "South Africa"
]
FOREIGN_ACTORS = [
    "US", "China", "France", "Russia", "UAE",
    "Saudi Arabia", "Turkey", "Israel", "Iran", "Rwanda",
    "NonState" 
]
# Each item must be a (Value, Label) pair
INTENT_CHOICES = [
    ('Economic', 'Economic'),
    ('Sovereignty', 'Sovereignty'),
    ('LGBTQ', 'LGBTQ'),
    ('Religious', 'Religious'),
    ('MilitaryPresence', 'Military presence'),
    ('ResourceDependency', 'Resource Dependency'),
    ('SocialFragility', 'Social Fragility'),
    ('ElectionInfluence', 'Election Influence'),
]

# intent_mapping DICTIONARY
intent_mapping = {
    # Direct matches (if ML outputs match CSV exactly)
    "economic": "Economic",
    "sovereignty": "Sovereignty",
    "lgbtq": "LGBTQ",
    "religious": "Religious",
    "electioninfluence": "ElectionInfluence",
    "militarypresence": "MilitaryPresence",
    "resourcedependency": "ResourceDependency",
    "socialfragility": "SocialFragility",

    # Common variations/spelling from ML model output (case-insensitive)
    "economic dependency": "Economic",
    "sovereignty erosion": "Sovereignty",
    "sovereignty threat": "Sovereignty",
    "lgbtq rights": "LGBTQ",
    "lgbt advocacy": "LGBTQ",
    "religious influence": "Religious",
    "religious polarisation": "Religious",
    "election influence": "ElectionInfluence",
    "election interference": "ElectionInfluence",
    "electoral interference": "ElectionInfluence",
    "military presence": "MilitaryPresence",
    "military base": "MilitaryPresence",
    "resource dependency": "ResourceDependency",
    "resource control": "ResourceDependency",
    "social fragility": "SocialFragility",
    "social unrest": "SocialFragility",
    "information warfare": "SocialFragility",
    "human rights advocacy": "LGBTQ",
    "debt trap diplomacy": "Economic",
    "cultural influence": "SocialFragility",
    "centralization of power": "Sovereignty",
    "cultural exchange": "Economic",
    "cultural hegemony": "Sovereignty",
    "democratic interference": "ElectionInfluence",
    "diplomatic cooperation": "Economic",
    "diplomatic influence": "Sovereignty",
}
    
# =========================
# CHATBOT ASSISTANCE SYSTEM (Enhanced with Consistent Calculation)
# =========================
class DisinfoAnalysisChatbot:
    def __init__(self):
        # Initializing the Groq client with your specific Llama 4 model
        self.client = Groq(api_key=settings.GROQ_API_KEY)
        self.model = "meta-llama/llama-4-scout-17b-16e-instruct"
        self.country_mapping = {
            "côte d'ivoire": "Côte d'Ivoire", "cote d'ivoire": "Côte d'Ivoire",
            "ivory coast": "Côte d'Ivoire", "south africa": "South Africa",
            "senegal": "Senegal", "drc": "DRC", "ethiopia": "Ethiopia"
        }
        self.actor_mapping = {
            "uae": "UAE", "china": "China", "france": "France", "us": "United States",
            "united states": "United States", "russia": "Russia", "saudi": "Saudi Arabia"
        }
        # Define intent_mapping similarly if needed by get_context_from_db
        # Make sure the keys match the exact lowercase text you expect in queries
        # and the values match the canonical names used in the MediaNarrative model/db
        self.intent_mapping = {
            # Example mappings (adjust based on your canonical intents and query keywords)
            # Keys should be lowercase versions of keywords users might search for
            # Values should be the exact canonical intent names used in MediaNarrative.strategic_intent
            # Direct matches (if ML outputs match CSV exactly)
            "economic": "Economic",
            "sovereignty": "Sovereignty",
            "lgbtq": "LGBTQ",
            "religious": "Religious",
            "electioninfluence": "ElectionInfluence",
            "militarypresence": "MilitaryPresence",
            "resourcedependency": "ResourceDependency",
            "socialfragility": "SocialFragility",
        
            # Common variations/spelling from ML model output (case-insensitive)
            "economic dependency": "Economic",
            "sovereignty erosion": "Sovereignty",
            "sovereignty threat": "Sovereignty",
            "lgbtq rights": "LGBTQ",
            "lgbt advocacy": "LGBTQ",
            "religious influence": "Religious",
            "religious polarisation": "Religious",
            "election influence": "ElectionInfluence",
            "election interference": "ElectionInfluence",
            "electoral interference": "ElectionInfluence",
            "military presence": "MilitaryPresence",
            "military base": "MilitaryPresence",
            "resource dependency": "ResourceDependency",
            "resource control": "ResourceDependency",
            "social fragility": "SocialFragility",
            "social unrest": "SocialFragility",
            "information warfare": "SocialFragility",
            "human rights advocacy": "LGBTQ",
            "debt trap diplomacy": "Economic",
            "cultural influence": "SocialFragility",
            "centralization of power": "Sovereignty",
            "cultural exchange": "Economic",
            "cultural hegemony": "Sovereignty",
            "democratic interference": "ElectionInfluence",
            "diplomatic cooperation": "Economic",
            "diplomatic influence": "Sovereignty",
        }

    def process_query(self, query):
        query_l = query.lower().strip()

        
        country_pattern = r'(senegal|drc|cote d\'ivoire|cote ivoire|ivory coast|ethiopia|south africa)'
        actor_pattern = r'(china|france|usa|united states|us|russia|saudi|turkey|uae|israel|iran|rwanda)'
        
         # Triggered if country + narrative-related keywords are mentioned
        country_match = re.search(country_pattern, query_l, re.IGNORECASE)
        actor_match = re.search(actor_pattern, query_l, re.IGNORECASE)
        
        if country_match and any(kw in query_l for kw in ['narrative', 'talked about', 'claims', 'topic', 'involving', 'analyze', 'about']):
            db_country = self.country_mapping.get(country_match.group(1).lower())
            db_actor = None
            if actor_match:
                db_actor = self.actor_mapping.get(actor_match.group(1).lower())

            queryset = MediaNarrative.objects.filter(target_country__iexact=db_country).exclude(
                article_text=''
            )
            
            if db_actor:
                queryset = queryset.filter(inferred_actor__iexact=db_actor)
            else:
                queryset = queryset.exclude(inferred_actor__in=['Local', 'Domestic', ''])

            articles = queryset.order_by('-posting_time')[:12]

            if articles.exists():
                context_parts = []
                for i, a in enumerate(articles):
                    snippet = a.article_text[:450].replace('\n', ' ')
                    context_parts.append(
                        f"SOURCE: {a.media_outlet} | ACTOR: {a.inferred_actor} | INTENT: {a.strategic_intent} | TEXT: {snippet}"
                    )
                
                context_text = "\n\n".join(context_parts)

                prompt = f"""You are a Senior Geopolitical Analyst. 
                TASK: Synthesize the actual NARRATIVE (the story and specific claims) regarding {db_country} {f'and {db_actor}' if db_actor else ''}.
                
                INSTRUCTIONS:
                1. Identify specific events, names, and allegations (e.g., corruption, judicial cases, infrastructure deals).
                2. Explain how these stories link to foreign actors.
                3. Mention 2-3 specific media sources from the data.
                4. USE PLAIN TEXT ONLY AND - FOR NEW LINES. NO BOLDING (**). NO ASTERISKS (*). NO EMOJIS. NO MARKDOWN.
                
                DATA:
                {context_text}
                """
                
                try:
                    response = self.client.chat.completions.create(
                        model=self.model,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.1
                    )
                    return response.choices[0].message.content.strip()
                except Exception:
                    return f"I found {articles.count()} articles for {db_country}, but the AI synthesis is currently unavailable. Most stories involve {articles[0].inferred_actor}."
            else:
                return f"I couldn't find any recent foreign influence narratives for {db_country} in the database."

        # ============================================
        # General Narratives Overview #####################
        # ============================================
        
        narrative_keywords = ['key narratives', 'narratives', 'strategic intent', 'what narratives', 'main narratives', 'list narratives']
        if any(keyword in query_l for keyword in narrative_keywords):
            from django.db.models import Count
            narratives = MediaNarrative.objects.filter(
                strategic_intent__isnull=False
            ).exclude(strategic_intent='').exclude(strategic_intent='Unknown')
            
            narrative_stats = narratives.values('strategic_intent').annotate(
                count=Count('id'),
                countries=Count('target_country', distinct=True),
                actors=Count('inferred_actor', distinct=True)
            ).order_by('-count')
            
            narrative_list = []
            for item in narrative_stats:
                narrative_list.append(
                    f"• **{item['strategic_intent']}**: {item['count']} articles "
                    f"(across {item['countries']} countries, {item['actors']} actors)"
                )
            
            total = sum(item['count'] for item in narrative_stats)
            
            return f"""📊 Key Narratives in Our Database:

We've identified {len(narrative_list)} main strategic narratives across {total} articles:

{chr(10).join(narrative_list)}

💡 **What each narrative means:**
• Economic: Trade, investment, debt, infrastructure projects, economic influence
• Sovereignty: Political influence, governance, territorial issues, autonomy
• SocialFragility: Social unrest, human rights, cultural tensions, information warfare
• MilitaryPresence: Military bases, security cooperation, arms, defense partnerships
• ResourceDependency: Natural resources, energy, minerals, resource control
• ElectionInfluence: Electoral interference, democratic processes, voting
• Religious: Religious influence, cultural values, faith-based narratives
• LGBTQ: LGBTQ rights narratives, social values

💬 **Try asking:**
• "What are Economic narratives around Ethiopia?"
• "Which countries use Sovereignty narratives most?"
• "Narratives involving Senegal and France"
"""

        
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

        # 1. Actor Stats Query
        if 'statistics' in query_l or 'stats' in query_l or 'most active' in query_l:
            # Extract country if mentioned
            country = None
            countries = ['senegal', 'ethiopia', 'drc', 'coteivoire', 'ivory coast', 'south africa']
            for c in countries:
                if c in query_l:
                    country = c.title()
                    break
            
            stats = self.get_actor_stats(country)
            if stats:
                lines = ["📊 Actor Statistics" + (f" for {country}" if country else "")]
                for s in stats[:5]:
                    lines.append(f"• {s['inferred_actor']}: {s['article_count']} articles, "
                              f"Avg Vulnerability: {s['avg_vulnerability']:.3f}")
                return "\n".join(lines)
            return "No statistics available."
        
        # 2. Compare Actors
        if 'compare' in query_l:
            actors = ['china', 'russia', 'france', 'usa', 'saudi', 'turkey', 'uae', 'israel', 'iran', 'rwanda']
            found_actors = [a.title() for a in actors if a in query_l]
            
            country = None
            countries = ['senegal', 'ethiopia', 'drc', 'coteivoire', 'ivory coast', 'south africa']
            for c in countries:
                if c in query_l:
                    country = c.title()
                    break
            
            if len(found_actors) >= 2:
                result = self.compare_actors(found_actors[0], found_actors[1], country)
                lines = [f"⚖️ Comparison: {found_actors[0]} vs {found_actors[1]}" + (f" in {country}" if country else "")]
                for actor, data in result.items():
                    lines.append(f"\n{actor}:")
                    lines.append(f"  Articles: {data['articles']}")
                    lines.append(f"  Avg Vulnerability: {data['avg_vulnerability']}")
                    lines.append(f"  Top Intent: {data['top_intent']}")
                return "\n".join(lines)
        
        # 3. Tone Breakdown
        if 'tone' in query_l or 'coverage' in query_l:
            actors = ['china', 'russia', 'france', 'usa', 'saudi', 'turkey', 'uae', 'israel', 'iran', 'rwanda']
            found_actor = None
            for a in actors:
                if a in query_l:
                    found_actor = a.title()
                    break
            
            country = None
            countries = ['senegal', 'ethiopia', 'drc', 'coteivoire', 'ivory coast', 'south africa']
            for c in countries:
                if c in query_l:
                    country = c.title()
                    break
            
            if found_actor:
                tones = self.get_tone_breakdown(found_actor, country)
                lines = [f"📻 Tone Analysis: {found_actor}" + (f" in {country}" if country else "")]
                for t in tones:
                    lines.append(f"  {t['tone']}: {t['count']} articles")
                return "\n".join(lines)

        # Quick Relevance Check - Focus on foreign influence
        irrelevant = ['football', 'soccer', 'entertainment', 'music', 'celebrity', 'local sport', 'local entertainment']
        if any(word in query_l for word in irrelevant) and not any(actor in query_l for actor in ['france', 'china', 'usa', 'us', 'russia', 'united states', 'unitedstates']):
            return "I specialize in foreign influence analysis and vulnerability indices. I don't track local sports or entertainment unless they involve foreign actors."
    
        # Handle multiple questions about ANY country with foreign actor focus
       
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
        # Vulnerability index queries
        if any(word in query_l for word in ['vulnerability', 'index', 'score', 'risk']):
            # Query the NEW table where the scores actually live
            from .models import VulnerabilityIndex
            
            avg_data = VulnerabilityIndex.objects.aggregate(avg_vi=Avg('final_risk'))
            avg_vulnerability = avg_data['avg_vi']
            
            avg_str = f"{avg_vulnerability:.3f}" if avg_vulnerability else "0.000"
            return f"The vulnerability index measures foreign influence risk (0-1). The current average across all monitored country-actor pairs is: {avg_str}"
    
        # Default AI analysis
        context = self.get_context_from_db(query)
        return self.get_insights_from_ai(query, context)
        
    def get_context_from_db(self, query):
        """Generate clean, readable context for AI - NO formatting errors!"""
        from django.db.models import Count # Import moved here
        query_lower = query.lower()

        # Determine target country from query
        target_country = None
        # Use the class attributes (defined in __init__ or as class vars)
        for key, value in self.country_mapping.items(): # <--- Uses self.
            if key in query_lower:
                target_country = value
                break

        # Determine foreign actor from query
        foreign_actor = None
        # Use the class attributes (defined in __init__ or as class vars)
        for key, value in self.actor_mapping.items(): # <--- Uses self.
            if key in query_lower:
                foreign_actor = value
                break

        # Determine intent from query (simplified mapping)
        intent = None
        # Use the class attributes (defined in __init__ or as class vars)
        for key, value in self.intent_mapping.items(): # <--- Uses self.
            if key in query_lower:
                intent = value
                break

        # Build context based on detected elements
        context_parts = []

        # 1. General Statistics (Always included if no specific filters)
        if not target_country and not foreign_actor and not intent:
            total_articles = MediaNarrative.objects.count()
            # Use self. attributes here too for consistency
            context_parts.append(f"DATABASE OVERVIEW: Total articles analyzed: {total_articles}. "
                                 f"Monitored countries: {list(self.country_mapping.values())}. " # <--- Uses self.
                                 f"Monitored foreign actors: {list(self.actor_mapping.values())}. " # <--- Uses self.
                                 f"Strategic intent categories: Economic, Sovereignty, LGBTQ, Religious, ElectionInfluence, MilitaryPresence, ResourceDependency, SocialFragility.")

        # 2. Filtered Statistics based on detected query terms
        base_query = MediaNarrative.objects.filter(target_country__in=COUNTRIES) # Apply focus country filter here too, if applicable globally

        if target_country:
            base_query = base_query.filter(target_country__iexact=target_country)
            context_parts.append(f"TARGET COUNTRY: {target_country}.")
            # Add specific stats for this country if needed

        if foreign_actor:
            base_query = base_query.filter(inferred_actor__iexact=foreign_actor)
            context_parts.append(f"FOREIGN ACTOR: {foreign_actor}.")
            # Add specific stats for this actor if needed

        if intent:
            base_query = base_query.filter(strategic_intent__iexact=intent)
            context_parts.append(f"STRATEGIC INTENT: {intent}.")
            # Add specific stats for this intent if needed

        # Add counts or summaries based on the filtered query
        filtered_count = base_query.count()
        context_parts.append(f"FILTERED ARTICLE COUNT: {filtered_count}.")

        # Example: Top intents for the target country (only if target_country is specified and intent is not already specified)
        if target_country and not intent:
            top_intents = base_query.exclude(strategic_intent__in=['', None]).values('strategic_intent').annotate(c=Count('id')).order_by('-c')[:3]
            top_intent_list = [f"{item['strategic_intent']} ({item['c']} articles)" for item in top_intents]
            if top_intent_list:
                context_parts.append(f"TOP INTENTS FOR {target_country}: {', '.join(top_intent_list)}.")

        # Example: Top actors for the target country (only if target_country is specified and foreign_actor is not already specified)
        if target_country and not foreign_actor:
            top_actors = base_query.exclude(inferred_actor__in=['', None]).values('inferred_actor').annotate(c=Count('id')).order_by('-c')[:3]
            top_actor_list = [f"{item['inferred_actor']} ({item['c']} articles)" for item in top_actors]
            if top_actor_list:
                context_parts.append(f"TOP ACTORS FOR {target_country}: {', '.join(top_actor_list)}.")

        # Example: Top countries for the foreign actor (only if foreign_actor is specified and target_country is not already specified)
        if foreign_actor and not target_country:
            top_countries = base_query.exclude(target_country__in=['', None]).values('target_country').annotate(c=Count('id')).order_by('-c')[:3]
            top_country_list = [f"{item['target_country']} ({item['c']} articles)" for item in top_countries]
            if top_country_list:
                context_parts.append(f"TOP TARGET COUNTRIES FOR {foreign_actor}: {', '.join(top_country_list)}.")

        # *** REVISED BLOCK TO GENERATE KEY NARRATIVES FOR COUNTRY-ACTOR COMBINATIONS ***
        # This addresses the specific query "What are the key narratives around Senegal and France?"
        if target_country and foreign_actor:
            # Find top strategic intents for this specific country-actor combination
            narrative_combinations = base_query.exclude(strategic_intent__in=['', None]).values('strategic_intent').annotate(count=Count('id')).order_by('-count')[:5]
            narrative_list = [f"{item['strategic_intent']} ({item['count']} articles)" for item in narrative_combinations]
            if narrative_list:
                # Construct a more specific summary based on the top narratives
                top_narrative = narrative_combinations.first()
                if top_narrative:
                    summary_detail = f"Primarily driven by {top_narrative['strategic_intent']} narratives ({top_narrative['count']} articles)"
                else:
                    summary_detail = "No specific dominant narrative identified" # Should not happen if narrative_list exists

                context_parts.append(f"KEY NARRATIVES FOR {target_country} INVOLVING {foreign_actor}: {', '.join(narrative_list)}. SUMMARY: Articles predominantly discuss {summary_detail} between {foreign_actor} and {target_country}. RECOMMENDATION: Focus analysis on the areas represented by the top narrative(s) ({top_narrative['strategic_intent'] if top_narrative else 'N/A'}) for strategic insights regarding this relationship.")
            else:
                # Even if no specific narratives are found for the combo, report the count
                context_parts.append(f"No specific top narratives found for {target_country} involving {foreign_actor} in the top 5. FILTERED ARTICLE COUNT: {filtered_count}.")

        # *** NEW SECTION: Add Sample Articles to Context (Limited Details) ***
        # This aims to provide more specific, example-based information to the AI
        # Only add samples if we have a specific filter (country, actor, or intent)
        if target_country or foreign_actor or intent:
            # Fetch a limited number of recent articles matching the current filters
            sample_articles = base_query.exclude(article_text__isnull=True).exclude(article_text='').order_by('-posting_time')[:3] # Limit to 3 samples
            sample_details = []
            for article in sample_articles:
                # Extract limited, relevant details from each sample article
                source = getattr(article, 'media_outlet', 'N/A')
                target = getattr(article, 'target_country', 'N/A')
                actor = getattr(article, 'inferred_actor', 'N/A')
                article_intent = getattr(article, 'strategic_intent', 'N/A')
                # Take a short snippet of the article text (e.g., first 100 chars)
                text_snippet = getattr(article, 'article_text', '')[:100] + "..." if getattr(article, 'article_text', '') else "No content available"
                # Append a concise line representing this sample
                sample_details.append(f"SOURCE: {source} | INTENT: {article_intent} | ACTOR: {actor} | SNIPPET: {text_snippet}")

            if sample_details:
                # Add the sample details to the context
                context_parts.append(f"SAMPLE ARTICLES FOR FILTERS (Country: {target_country or 'Any'}, Actor: {foreign_actor or 'Any'}, Intent: {intent or 'Any'}):")
                context_parts.extend(sample_details) # Add each sample line as a separate part
            else:
                context_parts.append(" for the applied filters.")


        # Combine all parts
        context = " ".join(context_parts)
        return context if context.strip() else "No specific data found for the query terms in the database."

    
        #def safe_article_line(article):
         #   """Safe line builder - fetches VI from the new table based on article metadata"""
        #    text_snippet = (article.article_text[:150] + "...") if getattr(article, 'article_text', '') else "No content"
            
        #    # 1. FIELD EXTRACTION
        #    source = getattr(article, 'media_outlet', 'N/A')
        #    target = getattr(article, 'target_country', 'N/A')
        #    actor = getattr(article, 'inferred_actor', 'N/A')
        #    intent = getattr(article, 'strategic_intent', 'N/A')
        #    tone = getattr(article, 'tone', 'N/A')
            
         #   # 2. DYNAMIC VI LOOKUP (The part you are changing)
         #   vi_score = "N/A"
         #   if target != 'N/A' and actor != 'N/A':
         #       # Normalize the intent to match the Anchor CSV/Table categories
                # Note: map_to_canonical_intent should be accessible here
         #       canonical_intent = map_to_canonical_intent(intent, getattr(article, 'title', ''))
                
           #     try:
           #         from .models import VulnerabilityIndex
           #         # Find the risk score for this specific combo
           #         record = VulnerabilityIndex.objects.filter(
           #             country__iexact=target,
           #             actor__iexact=actor,
           #             intent__iexact=canonical_intent
           #         ).first()
                    
           #         if record:
           #             vi_score = f"{float(record.final_risk):.3f}"
            #    except:
            #        vi_score = "N/A"
           # 
            # 3. CLEAN, READABLE FORMAT
           # return f"{source} | {target} | {actor} | {intent} | {tone} | VI:{vi_score} | {text_snippet}"
    
    def get_insights_from_ai(self, query, context):
        # Updated system prompt
        system_prompt = """You are a Senior Geopolitical Analyst specializing in Foreign Influence and Media Narrative Analysis.
    You have access to a database of analyzed articles from specific African countries and foreign actors.
    INSTRUCTIONS:
    1. ALWAYS examine the CONTEXT provided first.
    2. ONLY use information FROM the CONTEXT to answer the query.
    3. If the CONTEXT does not contain the specific information requested, clearly state: "The database context does not contain specific information about [aspect requested]."
    4. NEVER use general knowledge beyond the provided context.
    5. NEVER invent statistics, numbers, or details not present in the context.
    6. When the CONTEXT contains sections like "KEY NARRATIVES FOR [COUNTRY] INVOLVING [ACTOR]", "TOP INTENTS FOR [COUNTRY]", "TOP ACTORS FOR [COUNTRY]", or "SAMPLE ARTICLES FOR [COUNTRY] INVOLVING [ACTOR]", use these to understand the types of narratives, intents, actors, and sources involved.
    7. Synthesize a coherent narrative explanation based on the prevalent intents and actors identified in the context. Mention representative sources if available in the "SAMPLE ARTICLES" section.
    8. Exclude analysis of sports or entertainment content unless explicitly related to foreign influence by a named actor.
    9. Format your response in plain text.
    10. Use simple dashes (-) for bullet points if needed.
    11. Use short sentences.
    12. Include numbers where possible.
    13. Use CAPITALS for country and actor names.
    14. Separate sections (SUMMARY, KEY FINDINGS, RECOMMENDATION) with clear line breaks.

    FORMAT:
    1. SUMMARY (1 sentence): Provide a high-level synthesis of the dominant narrative theme.
    2. KEY FINDINGS (3-5 bullets max): Highlight specific intents, actors, sources, or trends derived from the context.
    3. RECOMMENDATION (1 sentence): Suggest an analytical focus or implication based on the synthesized narrative.

    EXAMPLE (Synthesizing narrative from context patterns, mentioning sources if available):
    The key narrative involving SENEGAL appears to be one of vulnerability to foreign influence, particularly from FRANCE, through reputational damage. Multiple sources (Viralmag, Afrik, and Tv5Monde) report on a judicial affair involving Aliou Sall, brother of former President Macky Sall, and allegations of corruption, blanchiment de capitaux, and traffic d'influence. This narrative could be used to undermine the reputation of Senegal's leadership and create an environment conducive to foreign influence. In contrast, SAUDI ARABIA's involvement (as reported by okaz.com.sa) seems to focus on economic dependency, but with a lower Vulnerability Index (VI) score of 0.018, indicating a relatively lower level of influence. Overall, the dominant narrative involving SENEGAL seems to be one of reputational damage and potential vulnerability to French influence.
    # Note: This example demonstrates synthesizing a narrative from the types of intents (reputational damage, judicial affairs), actors (FRANCE, SAUDI ARABIA), and specific sources found in the context."""

        try:
            chat_completion = self.client.chat.completions.create(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Context:\n{context}\n\nQuery: '{query}'"}
                ],
                model=self.model,
                temperature=0.1,
            )
        

            # --- CHECKS  ---
            # Check if the API response object itself is None (unlikely but possible if library fails)
            if chat_completion is None:
                print("DEBUG: chat_completion object is None") # Add logging
                return "AI Error: The model returned an empty response object."

            # Check if the 'choices' attribute exists and is not empty
            if not hasattr(chat_completion, 'choices') or not chat_completion.choices:
                print("DEBUG: chat_completion.choices is empty or missing") # Add logging
                return "AI Error: The model returned an unexpected response format (no choices)."

            # Check if the first choice exists
            if len(chat_completion.choices) == 0:
                print("DEBUG: chat_completion.choices list is empty") # Add logging
                return "AI Error: The model returned an empty choices list."

            first_choice = chat_completion.choices[0]

            # Check if the 'message' attribute exists in the first choice
            if not hasattr(first_choice, 'message'): 
                 print("DEBUG: first_choice.message is missing") 
                 return "AI Error: The model returned an unexpected response format (no message in choice)." 

            # Check if the 'content' attribute exists in the message
            if not hasattr(first_choice.message, 'content'): 
                 print("DEBUG: first_choice.message.content is missing") # 
                 return "AI Error: The model returned an unexpected response format (no content in message)." 

            # Finally, get the content
            content = first_choice.message.content

            # Check if the content itself is None (possible if API processed but returned nothing)
            if content is None:
                print("DEBUG: content within message is None") 
                return "AI Error: The model did not generate a response."

            # If all checks pass, return the content
            print(f"DEBUG: Successfully retrieved content (type: {type(content)}, length: {len(content) if content else 0})") 
            return content

        except Exception as e:
            # Catch any exception during the API call or processing
            print(f"DEBUG: Exception in get_insights_from_ai: {e}, Type: {type(e).__name__}") 
            return f"AI Error: {str(e)}"

            
    def get_actor_stats(self, country=None):
        """Get aggregated stats for actors"""
        # Change Avg('vulnerability_index') to a placeholder or a join
        qs = MediaNarrative.objects.exclude(
            inferred_actor__in=['', 'Unknown', None]
        )
        if country:
            qs = qs.filter(target_country__iexact=country)
        
        return qs.values('inferred_actor').annotate(
            article_count=Count('id'),
            avg_confidence=Avg('confidence')
        ).order_by('-article_count')
    
    def get_intent_breakdown(self, actor, country=None):
        """Get intent breakdown for an actor"""
        qs = MediaNarrative.objects.filter(
            inferred_actor__iexact=actor
        ).exclude(strategic_intent__in=['', None])
        
        if country:
            qs = qs.filter(target_country__iexact=country)
        
        return qs.values('strategic_intent').annotate(
            count=Count('id')
        ).order_by('-count')
    
    def get_tone_breakdown(self, actor, country=None):
        """Get tone breakdown for an actor"""
        qs = MediaNarrative.objects.filter(
            inferred_actor__iexact=actor
        ).exclude(tone__in=['', None])
        
        if country:
            qs = qs.filter(target_country__iexact=country)
        
        return qs.values('tone').annotate(
            count=Count('id')
        ).order_by('-count')
    
    def compare_actors(self, actor1, actor2, country=None):
        """Compare two actors in a country"""
        stats = {}
        for actor in [actor1, actor2]:
            qs = MediaNarrative.objects.filter(inferred_actor__iexact=actor)
            if country:
                qs = qs.filter(target_country__iexact=country)
            
            top_intent = qs.exclude(strategic_intent__in=['', None]).values(
                'strategic_intent'
            ).annotate(c=Count('id')).order_by('-c').first()
            
            stats[actor] = {
                'articles': qs.count(),
                # CHANGE THIS: vulnerability_index doesn't exist here
                'avg_vulnerability': "See Index Table", 
                'top_intent': top_intent['strategic_intent'] if top_intent else 'N/A'
            }
        return stats
        
# Instantiate the chatbot once
chatbot_instance = DisinfoAnalysisChatbot()

@csrf_exempt
def chat_view(request):
    # 1. If the user is SENDING a message (AJAX)
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            user_message = data.get('message', '').strip()
            bot_reply = chatbot_instance.process_query(user_message)
            return JsonResponse({'reply': bot_reply, 'success': True})
        except Exception as e:
            return JsonResponse({'reply': f"Error: {str(e)}", 'success': False})

    # 2. If the IFRAME is just loading for the first time
    # This provides the HTML structure for the typing area
    return render(request, 'chat_inline.html') 
    
@csrf_exempt
def chatbot_response(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            user_message = data.get('message', '').strip()
            bot_reply = chatbot_instance.process_query(user_message)
            return JsonResponse({'reply': bot_reply, 'success': True})
        except Exception as e:
            return JsonResponse({'reply': f"Error: {str(e)}", 'success': False})

    # When the iframe loads via GET (Initial load or Quick Chips)
    user_query = request.GET.get('q', '').strip()
    reply = ""
    if user_query:
        reply = chatbot_instance.process_query(user_query)
    
    # Ensure this return is indented correctly (4 spaces from the 'def')
    return render(request, 'chat_iframe.html', {
        'reply': reply,
        'query': user_query
    })
        

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

    # Base queryset without sports (potentially expensive to build)
    # --- STEP 1: GET CLEAN ARTICLE IDS (No Sports) ---
    base_ids_cache_key = "global_clean_article_ids"
    clean_ids = cache.get(base_ids_cache_key)

    if clean_ids is None:
        logger.info("🚀 Cache MISS: Performing heavy text exclusion for the first time...")
        exclude_keywords = [
            'football', 'soccer', 'sport', 'sports', 'match', 'game',
            'tournament', 'championship', 'olympic', 'cricket', 'basketball',
            'tennis', 'golf', 'athletics', 'rugby', 'boxing', 'mma', 'fight',
            'league', 'team', 'player', 'coach', 'stadium'
        ]
        qs = MediaNarrative.objects.all()
        for word in exclude_keywords:
            qs = qs.exclude(article_text__icontains=word)
        
        clean_ids = list(qs.values_list('id', flat=True))
        cache.set(base_ids_cache_key, clean_ids, timeout=60*60*24)
        logger.info(f"✅ Cache SET: Stored {len(clean_ids)} clean article IDs.")
    else:
        logger.info(f"🎯 Cache HIT: Using {len(clean_ids)} pre-filtered article IDs.")

    # --- STEP 2: BUILD FILTERED QUERYSET ---
    # This is your "Source of Truth" for the rest of the function
    full_stats_qs = MediaNarrative.objects.filter(id__in=clean_ids)
    full_stats_qs = full_stats_qs.filter(target_country__in=COUNTRIES)

    if calc_target_country:
        full_stats_qs = full_stats_qs.filter(target_country__iexact=calc_target_country)
    if calc_foreign_actor:
        full_stats_qs = full_stats_qs.filter(inferred_actor__iexact=calc_foreign_actor)
    if calc_strategic_intent:
        full_stats_qs = full_stats_qs.filter(strategic_intent__iexact=calc_strategic_intent)

    # Final sort for display
    full_stats_qs = full_stats_qs.order_by('-posting_time')
    
    # --- STEP 3: CALCULATOR LOGIC ---
    if calc_target_country and calc_foreign_actor:
        cvi_score, cvi_intent = calculate_contextual_score(
            calc_target_country,
            calc_foreign_actor,
            intent_filter=calc_strategic_intent
        )

    # --- STEP 4: CACHING STATS (Count only) ---
    total_articles_cache_key = f"overview_total_articles_{calc_target_country}_{calc_foreign_actor}_{calc_strategic_intent}"
    total_articles = cache.get(total_articles_cache_key)
    
    if total_articles is None:
        logger.info(f"🚀 Cache MISS for total_articles: {total_articles_cache_key}")
        total_articles = full_stats_qs.count()
        cache.set(total_articles_cache_key, total_articles, timeout=60*60)
    else:
        logger.info(f"🎯 Cache HIT for total_articles: {total_articles_cache_key}")
        
    # --- STEP 5: GLOBAL STATS & AVERAGES ---
    stats_cache_key = f"overview_global_stats_{calc_target_country}_{calc_foreign_actor}_{calc_strategic_intent}"
    global_stats_cached = cache.get(stats_cache_key)
    
    if global_stats_cached is None:
        logger.info(f"Cache MISS for global_stats")
        # Use full_stats_qs directly - no need to rebuild it!
        unique_outlets = full_stats_qs.values('media_outlet').distinct().count()
        unique_intents = full_stats_qs.exclude(strategic_intent__in=['', 'Unknown', None]).values('strategic_intent').distinct().count()
        unique_actors = full_stats_qs.exclude(inferred_actor__in=['', 'Unknown', None]).values('inferred_actor').distinct().count()
        
        # Only aggregate confidence here since vulnerability_index is gone
        avg_stats = full_stats_qs.aggregate(avg_conf=Avg('confidence'))
        avg_confidence = avg_stats['avg_conf'] or 0.0
    
        # B. Fetch Average Vulnerability from the NEW table
        # We filter the VulnerabilityIndex table based on the user's dashboard selection
        vi_qs = VulnerabilityIndex.objects.all()
        if calc_target_country:
            vi_qs = vi_qs.filter(country__iexact=calc_target_country)
        if calc_foreign_actor:
            vi_qs = vi_qs.filter(actor__iexact=calc_foreign_actor)
        
        vi_stats = vi_qs.aggregate(avg_vi=Avg('final_risk'))
        avg_vulnerability = vi_stats['avg_vi'] or 0.0
    
        global_stats_cached = {
            'unique_outlets': unique_outlets,
            'unique_intents': unique_intents,
            'unique_actors': unique_actors,
            'avg_vulnerability': round(float(avg_vulnerability), 3),
            'avg_confidence': round(float(avg_confidence), 3),
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
        # DATA IS FETCHED HERE
        top_subjects = list(full_stats_qs.exclude(
            strategic_intent__in=['', None]
        ).values('strategic_intent', 'inferred_actor', 'target_country').annotate(total=Count('id')).order_by('-total')[:5])
        
        cache.set(top_subjects_cache_key, top_subjects, timeout=60*60)

    canonical_top_subjects = []
    for sub in top_subjects:
        # Now top_subjects is a list of dictionaries, so this will work:
        canonical_intent = map_to_canonical_intent(sub['strategic_intent'])
        
        canonical_top_subjects.append({
            'canonical_intent': canonical_intent,
            'inferred_actor': sub['inferred_actor'],
            'target_country': sub['target_country'],
            'total': sub['total']
        }) # Cache for 1 hour

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

    # CORRECTLY INDENTED LOOP - aligned with ml_service, paginator, etc.
    for article in page_obj.object_list: # <-- This should be at the same level as ml_service, paginator
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

        # This ensures the displayed value matches the canonical form from INTENT_CHOICES
        article.canonical_strategic_intent = map_to_canonical_intent(article.strategic_intent) 
        
    # 10. Methodology / Description
    actor_label = calc_foreign_actor if calc_foreign_actor else "[Foreign Actor]"
    target_label = calc_target_country if calc_target_country else "[Target Country]"

    vulnerability_methodology = (
        f"1. Content Signal: Measures the intensity of strategic narratives pushed by {actor_label} "
        f"toward {target_label} on a specific factor (e.g., economic, elections, sovereignty, etc.). "
        "It is estimated using advanced ML models and statistically corrected using human labels via "
        "Prediction-powered Inference (PPI) to ensure reliable measurement. \n\n"
        
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
        'avg_vulnerability': round(avg_vulnerability, 3),
        'avg_confidence': round(avg_confidence, 3),
        'african_countries': COUNTRIES,
        'foreign_actors': FOREIGN_ACTORS,
        'country_list': country_list,
        'top_subjects': canonical_top_subjects,
        'cvi_score': cvi_score,
        'cvi_intent': cvi_intent,
        'selected_country': calc_target_country,
        'selected_actor': calc_foreign_actor,
        'selected_intent': calc_strategic_intent,
        'intent_choices': INTENT_CHOICES,
        'vulnerability_description': vulnerability_methodology,  
        
        # dropdown state management
        'selected_country': calc_target_country,
        'selected_actor': calc_foreign_actor,
        'selected_intent': calc_strategic_intent,
        
        # filter persistence for pagination links
        'selected_filters': {
            'target_country': calc_target_country,
            'inferred_actor': calc_foreign_actor,
            'strategic_intent': calc_strategic_intent,
        }
    }
    return render(request, 'overview.html', context)        
   

def media(request):
    # 1. Get the filter parameter
    outlet_name = request.GET.get('outlet', '').strip()

    # 2. START WITH ALL NARRATIVES
    # We remove select_related here to be safe if the FKs are empty
    qs = MediaNarrative.objects.all().order_by('-posting_time')

    # 3. SMART FILTER (Checks both the link and the text field)
    if outlet_name and outlet_name != "All Outlets":
        qs = qs.filter(
            Q(media_outlet_fk__name__iexact=outlet_name) |
            Q(media_outlet__iexact=outlet_name)
        )

    # Initialize variables for new charts/stats
    media_chart = None # Main chart (Top Outlets)
    # outlet_risk_chart = None # Average Risk per Outlet (when no specific outlet is selected) 
    outlet_intent_chart = None # Top Intents for Selected Outlet
    outlet_actor_chart = None # Top Actors covered by Selected Outlet
    outlet_tone_chart = None # Tone distribution for Selected Outlet
    outlet_country_chart = None # Top Countries covered by Selected Outlet
    outlet_stats = None # Summary stats for Selected Outlet

    # 4. SIDEBAR STATS & MAIN CHART (Counts based on the text field 'media_outlet')
    top_outlets = MediaNarrative.objects.values('media_outlet').annotate(
        name=F('media_outlet'), # F('media_outlet') gets the field value
        article_count=Count('id')
    ).filter(
        # Ensure we only count articles where 'media_outlet' is not null or empty string
        Q(media_outlet__isnull=False) & ~Q(media_outlet='')
    ).order_by('-article_count')[:10]

    # Generate the main chart HTML using the top_outlets data
    if top_outlets:
        # Convert the QuerySet (list of dicts) to a Pandas DataFrame
        df = pd.DataFrame(list(top_outlets))

        if not df.empty and len(df) > 0: # Double-check DataFrame is not empty
            # Create the Plotly bar chart
            # Sort by article_count for a cleaner display (ascending for horizontal bar if desired)
            df_sorted = df.sort_values(by='article_count', ascending=True) # Ascending for horizontal bar (top = highest)
            fig = px.bar(
                df_sorted,
                x='article_count', # X-axis: count
                y='name',          # Y-axis: outlet name
                orientation='h',    # Horizontal bars
                title='Top Media Outlets by Article Count', # Chart title
                labels={'name': 'Media Outlet', 'article_count': 'Number of Articles'}, # Axis labels
                template="plotly_white" # Styling
            )
            # Optional: Adjust layout
            fig.update_layout(height=400, margin=dict(l=10, r=10, t=30, b=10))
            # Convert the figure to HTML string
            media_chart = fig.to_html(full_html=False, include_plotlyjs='cdn')
        else:
            # If DataFrame is empty after conversion, set media_chart to None or a default message
            media_chart = "<p class='text-center py-5 text-muted'>No data available for the main chart.</p>"
    else:
        # If top_outlets QuerySet was empty, set media_chart to None or a default message
        media_chart = "<p class='text-center py-5 text-muted'>No data available for the main chart.</p>"

    # 5. ENHANCED CHARTS & STATS (Conditional based on outlet selection)
    if outlet_name and outlet_name != "All Outlets":
        # Get the queryset for the specific selected outlet
        selected_outlet_qs = qs # qs is already filtered by outlet_name if provided

        # Calculate stats for the selected outlet
        outlet_stats = selected_outlet_qs.aggregate(
            total_articles=Count('id'),
            avg_confidence=Avg('confidence'), # Average confidence of predictions for this outlet
            # Potentially avg tone score if applicable
        )

        # Chart: Top Strategic Intents for Selected Outlet
        outlet_intents = selected_outlet_qs.exclude(
            strategic_intent__in=['', 'Unknown', None]
        ).values('strategic_intent').annotate(
            count=Count('id')
        ).order_by('-count')[:10]

        if outlet_intents.exists():
            df_intent = pd.DataFrame(list(outlet_intents))
            if not df_intent.empty:
                fig_intent = px.pie(
                    df_intent, values='count', names='strategic_intent',
                    title=f"Strategic Intent Distribution for {outlet_name}",
                    template="plotly_white"
                )
                # Optional: Add a legend outside the plot
                fig_intent.update_layout(height=400, margin=dict(l=20, r=20, t=40, b=20))
                outlet_intent_chart = fig_intent.to_html(full_html=False, include_plotlyjs='cdn')

        # Chart: Top Foreign Actors Covered by Selected Outlet
        outlet_actors = selected_outlet_qs.exclude(
            inferred_actor__in=['', 'Unknown', None]
        ).values('inferred_actor').annotate(
            count=Count('id')
        ).order_by('-count')[:10]

        if outlet_actors.exists():
            df_actor = pd.DataFrame(list(outlet_actors))
            if not df_actor.empty:
                fig_actor = px.bar(
                    df_actor, x='count', y='inferred_actor', orientation='h',
                    title=f"Top Foreign Actors Mentioned by {outlet_name}",
                    labels={'count': 'Mentions', 'inferred_actor': 'Foreign Actor'},
                    template="plotly_white"
                )
                fig_actor.update_layout(height=400, margin=dict(l=20, r=20, t=40, b=20))
                outlet_actor_chart = fig_actor.to_html(full_html=False, include_plotlyjs='cdn')

        # Chart: Tone Distribution for Selected Outlet
        outlet_tones = selected_outlet_qs.exclude(
            tone__in=['', 'Unknown', None]
        ).values('tone').annotate(
            count=Count('id')
        ).order_by('-count')

        if outlet_tones.exists():
            df_tone = pd.DataFrame(list(outlet_tones))
            if not df_tone.empty:
                fig_tone = px.pie(
                    df_tone, values='count', names='tone',
                    title=f"Tone Distribution for {outlet_name}",
                    template="plotly_white"
                )
                fig_tone.update_layout(height=400, margin=dict(l=20, r=20, t=40, b=20))
                outlet_tone_chart = fig_tone.to_html(full_html=False, include_plotlyjs='cdn')

        # Chart: Top Countries Covered by Selected Outlet
        outlet_countries = selected_outlet_qs.exclude(
            target_country__in=['', 'Unknown', None]
        ).values('target_country').annotate(
            count=Count('id')
        ).order_by('-count')[:10]

        if outlet_countries.exists():
            df_country = pd.DataFrame(list(outlet_countries))
            if not df_country.empty:
                fig_country = px.bar(
                    df_country, x='count', y='target_country', orientation='h',
                    title=f"Top Countries Covered by {outlet_name}",
                    labels={'count': 'Article Count', 'target_country': 'Target Country'},
                    template="plotly_white"
                )
                fig_country.update_layout(height=400, margin=dict(l=20, r=20, t=40, b=20))
                outlet_country_chart = fig_country.to_html(full_html=False, include_plotlyjs='cdn')

    # NOTE: The 'else' block for 'outlet_risk_chart' is REMOVED HERE.
    # No chart is calculated for the overall view now.

    # 6. HANDLE PAGINATION
    paginator = django.core.paginator.Paginator(qs, 10) # Show 10 per page
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    context = {
        'top_outlets': top_outlets,
        'media_chart': media_chart, # Main Top Outlets Chart
        # 'outlet_risk_chart': outlet_risk_chart, # REMOVED FROM CONTEXT
        'outlet_intent_chart': outlet_intent_chart, # Intent Distribution (selected outlet view)
        'outlet_actor_chart': outlet_actor_chart, # Actor Mentions (selected outlet view)
        'outlet_tone_chart': outlet_tone_chart, # Tone Distribution (selected outlet view)
        'outlet_country_chart': outlet_country_chart, # Country Coverage (selected outlet view)
        'outlet_stats': outlet_stats, # Summary stats (selected outlet view)
        'page_obj': page_obj,
        'selected_name': outlet_name if outlet_name else "All Outlets",
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
    db_actor_map = {
        "united states": "US",
        "unitedstates": "US",
        "usa": "US",
        "russia": "Russia",
        "china": "China",
        "france": "France",
        "turkey": "Turkey",
        "uae": "UAE",
        "united arab emirates": "UAE"
    }
    # Get all unique country names actually present in your database
    db_countries = MediaNarrative.objects.values_list('target_country', flat=True).distinct()
    
    # Create a helper map: { "lower_case_name": "Official DB Name" }
    # Example: {"senegal": "Senegal", "ethiopia": "Ethiopia"}
    db_country_map = {c.lower(): c for c in db_countries if c}

    # Add manual aliases for tricky names that might not match perfectly
    aliases = {
        "democratic republic of the congo": "DRC",
        "ivory coast": "Cote d'Ivoire",
        "cote d'ivoire": "Cote d'Ivoire",
        "coteivoire": "Cote d'Ivoire",
    }
    db_country_map.update(aliases)

    # Use the map to find the "Official" name for the search
    # If not found in the map, we just Title Case it as a backup
    db_country = db_country_map.get(selected_country.lower(), selected_country.title())

    report_data = []

# --- 3. Process Data ---
    try:
        for actor in selected_actors:
            # 1. Setup Mapping for this actor
            db_actor = db_actor_map.get(actor.lower(), actor)
            
            # 2. Generate the Chart FIRST so it's ready to be saved
            volume_chart = None
            chart_qs = MediaNarrative.objects.filter(
                target_country__iexact=db_country,
                inferred_actor__iexact=db_actor
            )

            if chart_qs.exists():
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
                    volume_chart = base64.b64encode(buf.read()).decode('utf-8')
                    buf.close()
                    plt.close()
                except Exception as e_chart:
                    logger.error(f"Chart error for {actor}: {e_chart}")
                    volume_chart = None

            # 3. Get Risk Data from VulnerabilityIndex (replaces ContextualRisk)
            vi_country_map = {
                "cote d'ivoire": "CoteIvoire",
                "côte d'ivoire": "CoteIvoire",
                "ivory coast": "CoteIvoire",
                "coteivoire": "CoteIvoire",
            }
            vi_actor_map = {
                "us": "UnitedStates",
                "usa": "UnitedStates",
                "united states": "UnitedStates",
                "unitedstates": "UnitedStates",
                "saudi arabia": "Saudi",
                "saudi": "Saudi",
            }

            vi_country = vi_country_map.get(str(db_country).lower(), db_country)
            vi_actor = vi_actor_map.get(str(db_actor).lower(), db_actor)

            risk_record = VulnerabilityIndex.objects.filter(
                country__iexact=vi_country,
                actor__iexact=vi_actor,
            ).order_by('-final_risk').first()

            # 4. Append to results
            if risk_record:
                score = float(risk_record.final_risk)
                report_data.append({
                    'actor': actor,
                    'cvi_score': round(score, 3),
                    'risk_level': "High" if score > 0.7 else "Medium" if score > 0.4 else "Low",
                    'primary_threat': risk_record.intent,
                    'chart': volume_chart
                })
            else:
                report_data.append({
                    'actor': actor,
                    'cvi_score': 0.0,
                    'risk_level': "N/A",
                    'primary_threat': "No Data Found",
                    'chart': volume_chart
                })

        # Sort all actors by risk score (Highest first) after the loop finishes
        report_data.sort(key=lambda x: x['cvi_score'], reverse=True)

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
            1. NO MARKDOWN SYMBOLS. Do not use asterisks (**), hashes (###), or underscores (_).
            2. USE PLAIN TEXT HEADERS in ALL CAPS.
            3. Use simple dashes (-) for bullet points.
            4. Do not use emojis.
            
            REQUIRED STRUCTURE:
            
            NARRATIVE SUMMARY
            (Provide a high-level summary of the media volume, dominant sentiment, and primary themes/narratives found in the dataset.)
            
            KEY ACTORS AND INFLUENCE
            (List the primary foreign actors mentioned and their apparent strategic goals as inferred.)
            
            INFLUENCE THREAT ANALYSIS
            (Assess the overall likelihood and severity of the influence threat to {selected_country}.)
            
            DATASET:
            {all_text_context}
            
            FINAL REMINDER: Generate the report in PLAIN TEXT only. Do not use any markdown formatting characters.
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
    # 1. Get the raw selected country from the request
    selected_country_raw = request.GET.get('country', '').strip()

    # 2. Define the mapping from MediaNarrative.target_country format to VulnerabilityIndex.country format
    VI_COUNTRY_MAP = {
        # From MediaNarrative.target_country -> VulnerabilityIndex.country
        "Côte d'Ivoire": "CoteIvoire", 
        "Cote d'Ivoire": "CoteIvoire", # Without ô accent, with space and apostrophe -> CoteIvoire
        "côte d'ivoire": "CoteIvoire", # Lowercase with ô -> CoteIvoire
        "cote d'ivoire": "CoteIvoire", # Lowercase without ô -> CoteIvoire
        "ivory coast": "CoteIvoire", # Alternative mapping if someone types/sends "ivory coast" -> CoteIvoire
        "Ivory Coast": "CoteIvoire", # Capitalized alternative -> CoteIvoire
        "Senegal": "Senegal", # Assuming these match, adjust if VI format differs
        "senegal": "Senegal",
        "DRC": "DRC",
        "drc": "DRC",
        "Ethiopia": "Ethiopia",
        "ethiopia": "Ethiopia",
        "South Africa": "SouthAfrica",
        "south africa": "SouthAfrica",
    }

    # 3. Get the mapped country name for VulnerabilityIndex queries
    # Use the mapping, defaulting to the raw input lowercased if no mapping is found
    selected_country_for_vi = VI_COUNTRY_MAP.get(selected_country_raw, selected_country_raw.lower())

    # 4. Query MediaNarrative using the raw selected country name
    qs = MediaNarrative.objects.all().order_by('-posting_time')
    if selected_country_raw: # Use raw name for MediaNarrative filtering
        qs = qs.filter(target_country__iexact=selected_country_raw)

    # Initialize variables with placeholders to prevent NameErrors
    publisher_chart = "<p class='text-center py-5 text-muted'>No publishing data available</p>"
    subject_chart = "<p class='text-center py-5 text-muted'>No subject data available</p>"
    actor_country_chart = "<p class='text-center py-5 text-muted'>No actor-country pairing data available</p>"
    # *** NEW: Initialize additional chart variables ***
    risk_per_country_chart = "<p class='text-center py-5 text-muted'>No risk data available</p>"
    risk_per_actor_chart = "<p class='text-center py-5 text-muted'>No risk data available</p>"
    intent_distribution_chart = "<p class='text-center py-5 text-muted'>No intent data available</p>"
    volume_over_time_chart = "<p class='text-center py-5 text-muted'>No volume data available</p>"

    # 5. Aggregate Risk Scores from VulnerabilityIndex Table
    # This shows the *calculated risk* per country/actor combination, not just raw article counts.
    # It uses the pre-calculated scores from the VulnerabilityIndex model.
    # Use the MAPPED name for VulnerabilityIndex queries
    risk_scores_per_combo = VulnerabilityIndex.objects.all()
    if selected_country_for_vi: # Use the mapped name
        risk_scores_per_combo = risk_scores_per_combo.filter(country__iexact=selected_country_for_vi)

    # Chart 1: Risk Score Distribution by Country (if no specific country is selected via VI mapping)
    # Use the MAPPED name for VulnerabilityIndex queries
    if not selected_country_for_vi: # Check the mapped name for "All Countries"
        country_risk_data = risk_scores_per_combo.values('country').annotate(
            avg_risk=Avg('final_risk')
        ).order_by('-avg_risk') # Highest risk first

        if country_risk_data.exists():
            df_risk_country = pd.DataFrame(list(country_risk_data))
            if not df_risk_country.empty:
                fig_risk_country = px.bar(
                    df_risk_country, x='avg_risk', y='country', orientation='h',
                    title="Average Calculated Risk by Country",
                    labels={'avg_risk': 'Avg. Risk Score', 'country': 'Country'},
                    template="plotly_white"
                )
                fig_risk_country.update_traces(marker_color='red', textposition='outside', texttemplate='%{x:.3f}') # Color based on risk
                fig_risk_country.update_layout(height=400, margin=dict(l=20, r=20, t=40, b=20))
                risk_per_country_chart = fig_risk_country.to_html(full_html=False, include_plotlyjs='cdn')

    # Chart 2: Risk Score Distribution by Actor (for the selected country via VI mapping)
    # Use the MAPPED name for VulnerabilityIndex queries and the RAW name for display
    if selected_country_for_vi: # Check the mapped name for specific country
        actor_risk_data = risk_scores_per_combo.values('actor').annotate(
            avg_risk=Avg('final_risk')
        ).order_by('-avg_risk') # Highest risk first

        if actor_risk_data.exists():
            df_risk_actor = pd.DataFrame(list(actor_risk_data))
            if not df_risk_actor.empty:
                fig_risk_actor = px.bar(
                    df_risk_actor, x='avg_risk', y='actor', orientation='h',
                    title=f"Average Calculated Risk by Actor for {selected_country_raw}", # Use raw name for display
                    labels={'avg_risk': 'Avg. Risk Score', 'actor': 'Foreign Actor'},
                    template="plotly_white"
                )
                fig_risk_actor.update_traces(marker_color='orange', textposition='outside', texttemplate='%{x:.3f}') # Color based on risk
                fig_risk_actor.update_layout(height=400, margin=dict(l=20, r=20, t=40, b=20))
                risk_per_actor_chart = fig_risk_actor.to_html(full_html=False, include_plotlyjs='cdn')

    # --- 1. Top African Countries by total articles (Original) ---
    # This remains relevant as a baseline for volume.
    # Uses selected_country_raw (unchanged)
    top_publishers = MediaNarrative.objects.exclude(
        target_country__in=['', 'Unknown', None]
    ).values('target_country').annotate(
        article_count=Count('id')
    ).order_by('-article_count')[:10]

    if top_publishers.exists():
        df = pd.DataFrame(list(top_publishers))
        if not df.empty:
            df = df.rename(columns={'target_country': 'Country', 'article_count': 'Articles'})
            df = df.sort_values('Articles', ascending=True)
            import plotly.graph_objects
            fig = plotly.graph_objects.Figure(plotly.graph_objects.Bar(
                x=df['Articles'],
                y=df['Country'],
                orientation='h',
                marker=dict(color='#2563eb'),
                text=df['Articles'],
                textposition='outside'
            ))
            fig.update_layout(height=400, template="plotly_white", margin=dict(l=20, r=20, t=20, b=20))
            publisher_chart = fig.to_html(full_html=False, include_plotlyjs='cdn')

    # --- 2. Top Foreign Actors Mentioned (Original) ---
    # Shows overall activity by actors.
    # Uses selected_country_raw (unchanged)
    top_subjects = MediaNarrative.objects.exclude(
        inferred_actor__in=['', 'Unknown', None]
    ).values('inferred_actor').annotate(
        mention_count=Count('id')
    ).order_by('-mention_count')[:10]

    if top_subjects.exists():
        df_sub = pd.DataFrame(list(top_subjects))
        if not df_sub.empty:
            df_sub = df_sub.rename(columns={'inferred_actor': 'Actor', 'mention_count': 'Mentions'})
            df_sub = df_sub.sort_values('Mentions', ascending=True)
            fig_sub = px.bar(df_sub, x='Mentions', y='Actor', orientation='h', template="plotly_white")
            fig_sub.update_traces(marker_color='#f59e0b', textposition='outside')
            fig_sub.update_layout(height=400, margin=dict(l=20, r=20, t=20, b=20))
            subject_chart = fig_sub.to_html(full_html=False, include_plotlyjs='cdn')

    # --- 3. Top Actor-Country Pairings (Original) ---
    # Shows the most frequent topic combinations.
    # Uses selected_country_raw (unchanged)
    ac_pairings = MediaNarrative.objects.exclude(
        target_country__in=['', 'Unknown', None]
    ).exclude(
        inferred_actor__in=['', 'Unknown', None]
    ).values('target_country', 'inferred_actor').annotate(
        count=Count('id')
    ).order_by('-count')[:10]

    if ac_pairings.exists():
        df_ac = pd.DataFrame(list(ac_pairings))
        if not df_ac.empty:
            df_ac['Label'] = df_ac['target_country'] + " - " + df_ac['inferred_actor']
            df_ac = df_ac.sort_values('count', ascending=True)
            fig_ac = px.bar(df_ac, x='count', y='Label', orientation='h', template="plotly_white")
            fig_ac.update_traces(marker_color='#6366f1', textposition='outside')
            fig_ac.update_layout(height=400, margin=dict(l=20, r=20, t=20, b=20))
            actor_country_chart = fig_ac.to_html(full_html=False, include_plotlyjs='cdn')

    # *** NEW: Intent Distribution for the Selected Country ***
    # Shows what types of strategic influence topics are most prevalent for the selected country.
    # Uses selected_country_raw (unchanged)
    intent_distribution_chart = "<p class='text-center py-5 text-muted'>No intent data available</p>" # Ensure it's initialized
    intent_distribution = []
    if selected_country_raw: 
        # ✅ ENHANCED: Fetch intent counts excluding NULL, empty, and null-like values
        raw_intent_counts = MediaNarrative.objects.filter(
            target_country__iexact=selected_country_raw
        ).exclude(
            Q(strategic_intent__isnull=True) |
            Q(strategic_intent='') |
            Q(strategic_intent__iexact='null') |
            Q(strategic_intent__iexact='none') |
            Q(strategic_intent__iexact='n/a') |
            Q(strategic_intent__iexact='unknown') |
            Q(strategic_intent__iexact='tbd') |
            Q(strategic_intent__regex=r'^\s*$')  # whitespace-only strings
        ).values('strategic_intent').annotate(
            count=Count('id')
        ).order_by('-count')
    
        # Create a dictionary to hold canonical intent counts
        canonical_intent_counts = {}
        for item in raw_intent_counts:
            raw_intent = item['strategic_intent']
            count = item['count']
            # Skip if intent is still None or empty after filtering
            if not raw_intent or raw_intent.strip() == '':
                continue
            # Map the raw intent to its canonical form
            canonical_intent = map_to_canonical_intent(raw_intent)

            # Check for literal strings that Plotly might pick up
            if not canonical_intent or str(canonical_intent).lower().strip() in ['null', 'none', '', 'nan']:
                continue
            # Skip if mapping returns None or empty
            if not canonical_intent or canonical_intent.strip() == '':
                continue
            # Add the count to the canonical intent bucket
            if canonical_intent in canonical_intent_counts:
                canonical_intent_counts[canonical_intent] += count
            else:
                canonical_intent_counts[canonical_intent] = count
    
        # Convert the dictionary back to a list of dictionaries for the DataFrame
        processed_intent_data = [{'strategic_intent': k, 'count': v} for k, v in canonical_intent_counts.items()]
    
        # Sort the processed data by count descending
        processed_intent_data.sort(key=lambda x: x['count'], reverse=True)
    
        if processed_intent_data:
            df_intent = pd.DataFrame(processed_intent_data)
            
            # 🛡️ AGGRESSIVE PLOTLY SANITIZATION
            # 1. Drop Python None/NaN rows first
            df_intent = df_intent.dropna(subset=['strategic_intent'])
            
            # 2. Convert to string to standardize types
            df_intent['strategic_intent'] = df_intent['strategic_intent'].astype(str)
            
            # 3. Filter out literal "null", "none", "unknown", etc. (case-insensitive)
            invalid_strings = {'null', 'none', 'None', 'nan', 'NaN', 'na', 'n/a', 'unknown', 'tbd', ''}
            df_intent = df_intent[~df_intent['strategic_intent'].str.lower().str.strip().isin(invalid_strings)]
            
            # 4. Final cleanup: strip whitespace & drop empties
            df_intent['strategic_intent'] = df_intent['strategic_intent'].str.strip()
            df_intent = df_intent[df_intent['strategic_intent'] != '']
            
            # 🔍 DEBUG: Print exactly what Plotly will render
            print(f"📊 PLOTLY DATA [{selected_country_raw}]: {df_intent['strategic_intent'].tolist()}")

            if not df_intent.empty:
                # Use explicit lists to prevent px.pie auto-grouping of 'null'
                labels = df_intent['strategic_intent'].tolist()
                values = df_intent['count'].tolist()
                
                fig_intent = go.Figure(data=[go.Pie(
                    labels=labels,
                    values=values,
                    hole=0.3,
                    textinfo='label+percent',
                    hoverinfo='label+percent+value',
                    marker=dict(colors=px.colors.qualitative.Set3)
                )])
                fig_intent.update_layout(
                    title=f"Strategic Intent Distribution for {selected_country_raw}",
                    template="plotly_white",
                    height=400, 
                    margin=dict(l=20, r=20, t=40, b=20),
                    showlegend=True
                )
                intent_distribution_chart = fig_intent.to_html(full_html=False, include_plotlyjs='cdn')
                
    # Volume of Articles Over Time for the Selected Country
    # Shows trends - are certain topics or actors becoming more prominent?
    # Uses selected_country_raw (unchanged)
    volume_over_time_data = []
    if selected_country_raw: # Use raw name for MN filtering # <-- CORRECT INDENTATION: Align with volume_over_time_data =
        # Filter and prepare data for the chart
        articles_for_country = qs.exclude(posting_time__isnull=True).values('posting_time') # qs is already filtered by selected_country_raw
        if articles_for_country.exists():
            # 1. Correctly assign the DataFrame
            df_time = pd.DataFrame(articles_for_country)
            # 2. Process the DataFrame
            df_time['date'] = pd.to_datetime(df_time['posting_time'], utc=True).dt.date
            # 3. Create the daily counts series, then convert to DataFrame
            daily_counts_series = df_time['date'].value_counts().sort_index()
            daily_counts = daily_counts_series.reset_index(name='count')
            # 4. Check if the DataFrame for the chart has data
            if not daily_counts.empty:
                # 5. Define the figure, ensuring all parentheses match for px.line
                fig_time = px.line(
                    daily_counts,
                    x='date',
                    y='count',
                    title=f"Daily Article Volume for {selected_country_raw}", # Use raw name for display
                    labels={'count': 'Number of Articles', 'date': 'Date'},
                    template="plotly_white"
                )
                # 6. Update the layout, ensuring parentheses match for update_layout and margin
                fig_time.update_layout(
                    height=400,
                    margin=dict(l=20, r=20, t=40, b=20)
                )
                # 7. Convert figure to HTML
                volume_over_time_chart = fig_time.to_html(full_html=False, include_plotlyjs='cdn')

    # Additional Stats for Selected Country
    # Uses selected_country_raw (unchanged)
    country_stats = None
    if selected_country_raw: # Use raw name for MN filtering
        country_stats = qs.aggregate( # qs is already filtered by selected_country_raw
            total_articles=Count('id'),
            avg_confidence=Avg('confidence'), # Average confidence of predictions
            # Potentially avg tone score if applicable
        )

    # Sample articles (limit for display)
    # Uses selected_country_raw (unchanged)
    sample_articles = qs[:10] # qs is already filtered by selected_country_raw

    context = {
        'publisher_chart': publisher_chart,
        'subject_chart': subject_chart,
        'actor_country_chart': actor_country_chart,

        'risk_per_country_chart': risk_per_country_chart,
        'risk_per_actor_chart': risk_per_actor_chart, # This should now show data if VI has it for the mapped country
        'intent_distribution_chart': intent_distribution_chart,
        'volume_over_time_chart': volume_over_time_chart,
        'sample_articles': sample_articles,
        # Pass the RAW name for display in the template
        'selected_country': selected_country_raw or "All Countries",
        'african_countries': COUNTRIES,

        'country_stats': country_stats,
    }
    return render(request, 'countries.html', context)
    
def authors(request):
    # 1. Capture the selected journalist name from URL
    journalist_name = request.GET.get('journalist', '').strip()
    search_query = request.GET.get('search', '').strip()
    author_page = request.GET.get('author_page', 1)  # ✅ NEW: Pagination param

    # 2. CACHE logic for the Sidebar and Chart (The "Heavy" Data)
    # We use a unique key to store the top journalists and the chart HTML
    cache_key = f"authors_sidebar_and_chart_{search_query}_{author_page}"  # ✅ Include pagination in cache key
    cached_data = cache.get(cache_key)

    if cached_data:
        authors_page_obj = cached_data['authors_page_obj']  # ✅ Renamed for clarity
        authors_chart = cached_data['authors_chart']
        top_journalists = cached_data['top_journalists']  # Keep for chart
    else:
        # ✅ Get ALL authors (not just top 10), with optional search filter
        all_authors_raw = MediaNarrative.objects.exclude(
            author__in=['', None, 'Unknown', 'unknown', 'N/A', 'Staff', 'Editor', 'Anonymous', 'By', 'Agency', 'Reuters', 'AFP']
        ).exclude(
            author__regex=r'^https?://'
        ).values('author').annotate(
            article_count=Count('id'),
            avg_strategic_confidence=Avg('confidence'),
        ).filter(
            author__isnull=False,
            author__regex=r'^[A-Za-z\s\.\'\-]+$',
            article_count__gte=1  # Show ALL authors with ≥1 article
        )
        
        # ✅ Apply search filter if user typed something
        if search_query:
            all_authors_raw = all_authors_raw.filter(author__icontains=search_query)
        
        # ✅ Order alphabetically for pagination (or by count if preferred)
        all_authors_raw = all_authors_raw.order_by('author')
        
        # ✅ PAGINATE: 30 authors per page (adjust as needed)
        author_paginator = Paginator(all_authors_raw, 30)
        authors_page_obj = author_paginator.get_page(author_page)
        
        # Convert current page to list of dicts (for chart + template)
        top_journalists = []
        for item in authors_page_obj:
            name = item['author'].strip()
            if name and len(name) > 2:
                top_journalists.append({
                    'name': name,
                    'article_count': item['article_count'],
                    'avg_strategic_confidence': item['avg_strategic_confidence'] or 0.0
                })
        
        # Generate the Plotly Chart HTML (uses top_journalists)
        authors_chart = None
        if top_journalists:
            df = pd.DataFrame(list(top_journalists))
            if not df.empty:
                fig = px.bar(
                    df, x='article_count', y='name', orientation='h',
                    color='article_count', color_continuous_scale='Blues',
                    labels={'article_count': 'Number of Articles', 'name': 'Journalist'},
                    title="Top Journalists by Number of Articles"
                )
                fig.update_layout(
                    height=350, margin=dict(l=10, r=10, t=30, b=10),
                    paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
                    yaxis={'categoryorder': 'total ascending'}
                )
                authors_chart = fig.to_html(full_html=False, include_plotlyjs='cdn')

        # Store in cache for 1 hour
        cached_data = {
            'authors_page_obj': authors_page_obj,  # ✅ Paginated object
            'authors_chart': authors_chart,
            'top_journalists': top_journalists  # For chart
        }
        cache.set(cache_key, cached_data, 60 * 60)

    # 3. Dynamic Logic (Not cached, changes based on user click) - ALL ORIGINAL INSIGHTS PRESERVED
    qs = MediaNarrative.objects.all().order_by('-posting_time')
    selected_journalist = None

    # Initialize variables for selected journalist stats (original structure)
    journalist_stats = None
    common_intents = []
    common_countries = []
    common_actors = []
    journalist_intent_chart = None

    if journalist_name:
        # ✅ Filter on MediaNarrative.author field
        qs = qs.filter(author__iexact=journalist_name)
        selected_journalist = {'name': journalist_name}

        # *** ENHANCED LOGIC: Calculate stats for the selected journalist ***
        journalist_stats = MediaNarrative.objects.filter(
            author__iexact=journalist_name
        ).aggregate(
            total_articles=Count('id'),
            avg_confidence=Avg('confidence'),
        )
        
        # Get most common intents
        common_intents = MediaNarrative.objects.filter(
            author__iexact=journalist_name
        ).exclude(
            strategic_intent__in=['', 'Unknown', None]
        ).values('strategic_intent').annotate(
            count=Count('id')
        ).order_by('-count')[:5]

        # Get most common target countries
        common_countries = MediaNarrative.objects.filter(
            author__iexact=journalist_name
        ).exclude(
            target_country__in=['', 'Unknown', None]
        ).values('target_country').annotate(
            count=Count('id')
        ).order_by('-count')[:5]

        # Get most common inferred actors
        common_actors = MediaNarrative.objects.filter(
            author__iexact=journalist_name
        ).exclude(
            inferred_actor__in=['', 'Unknown', None]
        ).values('inferred_actor').annotate(
            count=Count('id')
        ).order_by('-count')[:5]

        # Generate Mini Chart for Selected Journalist
        if common_intents:
            df_intent = pd.DataFrame(list(common_intents))
            if not df_intent.empty:
                fig_intent = go.Figure(data=[go.Pie(
                    labels=df_intent['strategic_intent'],
                    values=df_intent['count'],
                    hole=0.3,
                    textinfo='label+percent',
                    hoverinfo='label+percent+value',
                    marker=dict(colors=px.colors.qualitative.Set3)
                )])
                fig_intent.update_layout(
                    title=f"Focus Areas for {journalist_name}",
                    template="plotly_white",
                    height=300, 
                    margin=dict(l=10, r=10, t=30, b=10),
                    showlegend=True
                )
                journalist_intent_chart = fig_intent.to_html(full_html=False, include_plotlyjs='cdn')

    # 4. Pagination for Articles (ORIGINAL)
    paginator = Paginator(qs, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    # 5. Context Assembly - ALL ORIGINAL KEYS PRESERVED + NEW PAGINATION
    context = {
        'authors_page': authors_page_obj,      # ✅ NEW: Paginated author directory
        'top_journalists': top_journalists,    # For chart (current page only)
        'authors_chart': authors_chart,
        'page_obj': page_obj,                  # Article pagination (original)
        'selected_name': journalist_name or "All Journalists",
        'selected_journalist': selected_journalist,
        'journalist_stats': journalist_stats,
        'common_intents': common_intents,
        'common_countries': common_countries,
        'common_actors': common_actors,
        'journalist_intent_chart': journalist_intent_chart,
        'search_query': search_query,
    }
    return render(request, 'authors.html', context)

def articles_view(request):
    search_query = request.GET.get("q", "")

    articles = MediaNarrative.objects.all().order_by("-posting_time")  

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
        article.canonical_strategic_intent = map_to_canonical_intent(article.strategic_intent)
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

def clear_cache_view(request):
    cache.clear()
    return HttpResponse("Cache cleared successfully! Refresh your dashboard now.")
