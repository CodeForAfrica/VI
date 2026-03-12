import sys
import os
import pandas as pd
import numpy as np
import time
import socket
import trafilatura
import cloudscraper 
import requests
import logging
import django
from datetime import date, timedelta
from sqlalchemy import create_engine, text
from urllib.parse import urlparse
from django.conf import settings 
import mediacloud.api

# 1. SETUP PATHS
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# 2. DATA DICTIONARIES (Moved to Top for Scope)
ACTOR_COLLECTION_IDS = {
    "USA": 34412234, "France": 34412146, "China": 34412193,
    "Russia": 34412232, "Turkey": 34412131, "Saudi Arabia": 34412050, "Israel": 34412391, "Iran": 34412284, "UAE": 34412114,
}

TARGET_COLLECTION_IDS = {
    "Ethiopia": 34412034, "Senegal": 38380807, "DRC": 34412042,
    "South Africa": 34412238, "Côte d'Ivoire": 34412173,
}

START_DATE = date(2026, 1, 1)
END_DATE   = date(2026, 3, 11)

# --- NEW STRUCTURE FOR SPECIFIC QUERIES ---
ACTOR_TERMS = {
    "USA": ["United States", "USA", "US", "America", "Biden", "Washington", "American", "US Embassy"],
    "France": ["France", "French", "Paris", "Macron", "French Embassy", "Français"],
    "China": ["China", "Chinese", "Beijing", "Xi Jinping", "PRC", "Chinese Embassy", "中", "中国"],
    "Russia": ["Russia", "Russian", "Moscow", "Putin", "Russian Embassy", "Россия"],
    "Turkey": ["Turkey", "Turkish", "Ankara", "Erdogan", "Turkish Embassy", "Türkiye"],
    "Saudi Arabia": ["Saudi Arabia", "Saudi", "Riyadh", "Mohammed bin Salman", "Saudi Embassy", "السعودية"],
    "Israel": ["Israel", "Israeli", "Tel Aviv", "Netanyahu", "Israeli Embassy", "ישראל"],
    "Iran": ["Iran", "Iranian", "Tehran", "Supreme Leader", "Ayatollah", "Khamenei", "Iranian Embassy", "ایران"],
    "UAE": ["UAE", "Emirati", "Abu Dhabi", "Dubai", "Mohammed bin Zayed", "UAE Embassy", "الإمارات"]
}

TARGET_TERMS = {
    "Ethiopia": ["Ethiopia", "Ethiopian", "Addis Ababa", "Abiy Ahmed", "Tigray", "Oromo", "Amhara", "EPRDF", "TPLF"],
    "Senegal": ["Senegal", "Senegalese", "Dakar", "Macky Sall", "Ousmane Sonko", "Bassirou Diomaye Faye", "Sénégal", "Sénégalais", "Sénégalaise"],
    "DRC": ["DRC", "Democratic Republic of Congo", "Congo-Kinshasa", "Kinshasa", "edi", "Joseph Kabila", "Kabila", "Lubumbashi", "Kisangani", "Kolwezi", "Kivu", "République démocratique du Congo", "Congo"],
    "Côte d'Ivoire": ["Côte d'Ivoire", "Ivory Coast", "Abidjan", "Yamoussoukro", "Alassane Ouattara", "Laurent Gbagbo", "Henri Konan Bédié", "Cote d'Ivoire", "Cote d'Ivoire", "Ivoirian"],
    "South Africa": ["South Africa", "South African", "Pretoria", "Cape Town", "Johannesburg", "ANC", "Ramaphosa", "Jacob Zuma", "Mandela", "Nelson Mandela", "iNingizimu Afrika", "uRamaphosa"]
}

INFLUENCE_KEYWORDS = {
    "Economic": [
        "investment", "debt relief", "loan", "trade agreement", "mining contract", "mining rights", "economic partnership",
        "financial aid", "development finance", "Belt AND Road", "BRI", "Silk Road", "digital Silk Road",
        "resource dependency", "land lease", "agricultural cooperation", "cocoa", "uranium", "cobalt", "copper", "oil",
        "infrastructure project", "railway", "road", "port", "airport", "power plant", "hydroelectric", "hydropower",
        "industrial park", "special economic zone", "manufacturing", "energy project", "renewable energy", "solar", "wind"
    ],
    "MilitarySecurity": [
        "military cooperation", "arms sale", "weapons deal", "defense pact", "peacekeeping", "security partnership",
        "military base", "troop deployment", "training mission", "intelligence sharing", "naval cooperation",
        "joint exercises", "anti-terrorism", "counter-terrorism", "militia support", "proxy warfare", "mercenary"
    ],
    "PoliticalDiplomatic": [
        "diplomatic relations", "embassy", "consulate", "state visit", "high-level meeting", "diplomatic recognition",
        "election interference", "electoral support", "governance model", "anti-corruption", "rule of law", "democracy",
        "human rights", "civil society", "political party", "lobbying", "influence campaign", "soft power"
    ],
    "CulturalEducational": [
        "Confucius Institute", "cultural exchange", "language school", "scholarship program", "student exchange",
        "academic cooperation", "research partnership", "cultural diplomacy", "art exhibition", "film festival",
        "book donation", "library", "educational cooperation", "university partnership", "alumni network"
    ],
    "TechnologySurveillance": [
        "technology transfer", "telecom cooperation", "5G", "Huawei", "ZTE", "AI", "artificial intelligence",
        "surveillance technology", "spyware", "cybersecurity", "data security", "digital infrastructure",
        "internet governance", "social media platform", "vaccination campaign", "health initiative",
        "hospital construction", "medical aid", "pandemic response", "public health"
    ],
    "InformationNarrative": [
        "media cooperation", "journalist training", "news agency", "propaganda", "disinformation", "fake news",
        "narrative shaping", "public opinion", "social media campaign", "influencer", "blog", "podcast", "radio station",
        "television channel", "broadcast", "Amharic", "local language", "press freedom", "media ownership"
    ],
    "Religious": [
        "religious diplomacy", "interfaith dialogue", "religious institution", "mosque", "church", "temple",
        "religious leader", "faith-based organization", "religious minority", "religious freedom", "atheism",
        "secularism", "religious law", "Sharia", "Halakha", "orthodoxy", "sectarianism", "religious extremism",
        "religious moderation", "religious tolerance"
    ]
}

def build_query(actor, target):
    """
    Builds a query string for a specific actor-target pair
    using predefined terms.
    """
    # Get terms for the actor and target
    actor_terms = ACTOR_TERMS.get(actor, [])
    target_terms = TARGET_TERMS.get(target, [])

    if not actor_terms or not target_terms:
        print(f"Warning: Missing terms for actor '{actor}' or target '{target}'. Skipping query.")
        return None

    # Build the core query parts
    target_phrase = "(" + " OR ".join(target_terms) + ")"
    actor_phrase = "(" + " OR ".join(actor_terms) + ")"

    # Combine target and actor with AND
    core_phrase = f"({target_phrase} AND {actor_phrase})"

    # Combine influence keywords from relevant categories with OR
    # You can customize which categories are included based on the actor-target pair
    all_influence_keywords = []
    for category_keywords in INFLUENCE_KEYWORDS.values():
        all_influence_keywords.extend(category_keywords)
    
    influence_phrase = "(" + " OR ".join(all_influence_keywords) + ")"

    # Final query: (Target AND Actor) AND (Influence Keywords)
    # This ensures the article mentions both the target country and the actor,
    # and discusses one of the influence-related topics.
    final_query = f"({core_phrase} AND {influence_phrase})"
    
    # Optional: Add parentheses around the core phrase for clarity if needed by the API
    # final_query = f"(({target_phrase} AND {actor_phrasefluence_phrase}))"

    return final_query

# --- END NEW STRUCTURE ---

db_columns = [
    "article_text", "posting_time", "media_outlet", "inferred_actor", 
    "target_country", "url", "lang_detect", "strategic_intent",
    "sector", "tone", "confidence", "use_afrolm", "llm_strat", 
    "llm_strat_conf", "llm_strat_notes", "pseudo_kept", "pseudo_weight", 
    "llm_strat_id", "strategic_intent_id"
]

# 3. DJANGO CONFIG
if not settings.configured:
    DB_USER = os.getenv('DB_USER', 'postgres')
    DB_PASSWORD = os.getenv('DB_PASSWORD')
    DB_HOST = os.getenv('DB_HOST', 'localhost').strip()
    DB_PORT = os.getenv('DB_PORT', '5432')
    DB_NAME = os.getenv('DB_NAME', 'postgres')

    settings.configure(
        DATABASES = {'default': {'ENGINE': 'django.db.backends.postgresql','NAME': DB_NAME,'USER': DB_USER,'PASSWORD': DB_PASSWORD,'HOST': DB_HOST,'PORT': DB_PORT}},
        INSTALLED_APPS=['dashboard'], USE_TZ=True,
    )
    django.setup()

from django.db import connection

# 4. DATABASE ENGINE
try:
    engine = create_engine(f'postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}', future=True)
except Exception as e:
    engine = None

# 5. HELPER FUNCTIONS
def url_exists(url):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM dashboard_medianarrative WHERE url = %s", [url])
            return cursor.fetchone()[0] > 0
    except: return False

def scrape_full_text_robust(url):
    try:
        downloaded = trafilatura.fetch_url(url)
        if downloaded:
            content = trafilatura.extract(downloaded)
            if content and len(content) > 100: return content.strip()
        return f"Failed to scrape {url}"
    except Exception as e: return str(e)

def verify_dns(host):
    try:
        socket.gethostbyname(host)
        return True
    except: return False

# 6. MAIN PROCESS - CORRECTED VERSION
def main():
    api_key = os.getenv('MEDIACLOUD_API_KEY')
    if not api_key: 
        return print("ERROR: No API Key")
    
    try:
        mc_search = mediacloud.api.SearchApi(api_key)
    except Exception as e:
        print(f"Error initializing MediaCloud API: {e}")
        return

    all_records = []

    # Iterate through target countries
    for country in TARGET_TERMS.keys(): # Use keys from TARGET_TERMS to ensure consistency
        # Iterate through actors
        for actor_name, actor_coll_id in ACTOR_COLLECTION_IDS.items():
            print(f"Searching {actor_name} media for {country}...")
            
            # Build the specific query for this actor-target pair
            base_query = build_query(actor_name, country)
            
            if not base_query: # Skip if query could not be built
                print(f"  Skipping query for {actor_name} -> {country} due to missing terms.")
                continue
                
            try:
                # Construct the full query string for the API call
                full_query = f"({base_query})" # Wrap in parentheses as needed by the API

                print(f"  Query: {full_query[:100]}...") # Log the query being used (first 100 chars)

                # CORRECTED: mc_search.story_list returns (stories_list, total_count)
                # Pass date objects directly
                stories_list, total_count_from_api = mc_search.story_list(
                    query=full_query,
                    start_date=START_DATE, # Pass date object
                    end_date=END_DATE,     # Pass date object
                    collection_ids=[actor_coll_id] # Specify the actor's media collection
                    # Add other relevant parameters like limit if needed
                )
                
                # 'stories_list' should now be a list of dictionaries
                print(f"  Found {len(stories_list)} stories in {actor_name} media for {country} (API reported total: {total_count_from_api}).")
                for story_dict in stories_list: # Iterate over the list of story dicts
                    # 'story_dict' should be a dictionary like {'url': '...', 'publish_date': ..., ...}
                    record = {col: None for col in db_columns}
                    record.update({
                        "url": story_dict.get("url"),
                        "posting_time": str(story_dict.get("publish_date")), # Convert datetime to string if DB expects string
                        "media_outlet": story_dict.get("media_name"),
                        "inferred_actor": actor_name,
                        "target_country": country,
                        "lang_detect": story_dict.get("language"),
                        "confidence": story_dict.get("score") or 0.0, # Handle potential None
                        "pseudo_kept": False, 
                        "pseudo_weight": 0.0, 
                        "use_afrolm": False
                    })
                    all_records.append(record)
            except Exception as e:
                print(f"  API Error for {actor_name} searching for {country}: {e}")
                print(f"    Exception Type: {type(e).__name__}") # Added for debugging

    # Processing and Database Logic
    df = pd.DataFrame(all_records).head(200) # Limit to 200 as before
    if df.empty: 
        print("No articles found or fetched from API.")
        return # Exit if no data

    print(f"Processing {len(df)} fetched articles...")
    processed_count = 0
    for idx, row in df.iterrows():
        if url_exists(row['url']): 
            print(f"URL exists, skipping: {row['url'][:50]}...")
            continue
        
        content = scrape_full_text_robust(row['url'])
        if "Failed" not in content and "Error" not in content: # Check for success
            row_data = row.to_dict()
            row_data['article_text'] = content
            
            # --- INSERTION LOGIC ---
            # Use SQLAlchemy engine if available
            if engine:
                try:
                    final_df = pd.DataFrame([row_data])[db_columns]
                    with engine.begin() as conn:
                        final_df.to_sql('dashboard_medianarrative', conn, if_exists='append', index=False)
                    print(f"Saved (SQLAlchemy): {row['url'][:50]}...")
                    processed_count += 1
                except Exception as e_sql:
                    logging.error(f"SQLAlchemy DB Insert Error for {row['url']}: {e_sql}")
                    print(f"DB Insert Error (SQLAlchemy): {e_sql}")
            else: # Fallback to Django ORM
                try:
                    from dashboard.models import MediaNarrative
                    model_data = {
                        'url': row_data.get('url'),
                        'posting_time': pd.to_datetime(row_data.get('posting_time'), errors='coerce'), # Ensure datetime
                        'media_outlet': row_data.get('media_outlet'),
                        'inferred_actor': row_data.get('inferred_actor'),
                        'target_country': row_data.get('target_country'),
                        'article_text': row_data.get('article_text'),
                        'lang_detect': row_data.get('lang_detect'),
                        # ... map other fields as needed, providing defaults for NOT NULL fields
                        'pseudo_kept': row_data.get('pseudo_kept', False),
                        'pseudo_weight': row_data.get('pseudo_weight', 0.0),
                        'use_afrolm': row_data.get('use_afrolm', False),
                        'confidence': row_data.get('confidence', 0.0),
                        # Fields expected to be filled by ML later can be left as NULL/default
                        # e.g., 'strategic_intent': None,
                        # e.g., 'tone': None,
                        # e.g., 'vulnerability_index': None,
                        # e.g., 'sector': None, # Will be inferred from article_text later
                        # e.g., 'llm_strat': None,
                        # e.g., 'ml_processed_at': None,
                    }
                    narrative_instance = MediaNarrative(**model_data)
                    narrative_instance.save()
                    print(f"Saved (ORM): {row['url'][:50]}...")
                    processed_count += 1
                except Exception as e_orm:
                    logging.error(f"Django ORM DB Insert Error for {row['url']}: {e_orm}")
                    print(f"DB Insert Error (ORM): {e_orm}")
        else:
            print(f"Failed scraping: {content[:50]} for {row['url'][:30]}...")

    print(f"\n--- Ingestion Summary ---")
    print(f"Total Fetched from API: {len(df)}")
    print(f"Successfully Processed (Scraped & Saved): {processed_count}")
    print(f"Already Existed (Skipped): {len(df) - processed_count}") # Rough estimate if all failures are skips
    print("-------------------------")

if __name__ == "__main__":
    main()
