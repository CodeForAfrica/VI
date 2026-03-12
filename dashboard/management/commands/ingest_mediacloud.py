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
    "Russia": 34412232, "Turkey": 34412131, "Saudi Arabia": 34412050412391, "Iran": 34412284, "UAE": 34412114,
}

TARGET_COLLECTION_IDS =iopia": 34412034, "Senegal": 38380807, "DRC": 34412042,
    "South Africa": 34412238, "Côte d'Ivoire": 34412173,
}

START_DATE = date(2026, 1, 1)
END_DATE   = date(2026, 3, 11)

QUERY_BY_COUNTRY = {
    "Ethiopia": r'(("infrastructure project" OR "debt relief" OR "railway" OR "industrial park" OR "investment" OR "foreign aid" OR "trade" OR "mining" OR "manufacturing" OR "energy project" OR "military cooperation" OR "arms sale" OR "defense pact" OR "peacekeeping" OR "security partnership" OR "diplomatic relations" OR "election" OR "governance" OR "anti-corruption" OR "state visit" OR "Confucius Institute" OR "cultural exchange" OR "language school" OR "scholarship" OR "digital Silk Road" OR "5G" OR "Huawei" OR "surveillance" OR "cybersecurity" OR "AI" OR "vaccine" OR "pandemic aid" OR "hospital construction" OR "education" OR "university" OR "climate change" OR "hydropower" OR "agriculture" OR "land lease cooperation" OR "mosque" OR "church" OR "religious diplomacy" OR "propaganda" OR "disinformation" OR "social media campaign" OR "broadcast in Amharic") OR ("ኢትዮጵያ" OR "አዲስ አበባ" OR "ኦሮሚያ " OR "ትግራይ" OR "አማራ" OR "የአፍሪካ ቀንድ"))',
    "Senegal": r'(("multipartisme" OR "teranga" OR "TER" OR "FAS" OR "DAGE" OR "élection" OR "présidentielle" OR "scrutin" OR "politique" OR "campagne" OR "gouvernance" OR "francophonie" OR "investissement" OR "commerce" OR "prêt" OR "dette" OR "route" OR "routière" OR "port" OR "rail" OR "oléoduc" OR "militaire" OR "arme" OR "défense" OR "paix" OR "terrorisme" OR "mercenaires" OR "bourse" OR "conficius" OR "université" OR "aide" OR "sanitaire" OR "cinéma" OR "théâtre" OR "jeune" OR "propagande" OR "désinformation" OR "réseaux sociaux" OR "fausses informations" OR "influenceur" OR "média" OR "5G" OR "Huawei" OR "IA" OR "Intelligence Artificielle" OR "cybersécurité" OR "internet" OR "satellite" OR "surveillance" OR "vaccin" OR "pandémique" OR "hôpital" OR "subvention" OR "agriculture" OR "énergie" OR "cacao" OR "renouvelable" OR "hydraulique" OR "mosqué" OR "église" OR "séminaire" OR "pélérinage" OR "anti-extrémisme" OR "islam" OR "christianisme" OR "chiite" OR "alliance" OR "sunnite") AND ("Senegal" OR "Senegalais" OR "Dakar" OR "Diamniadio" OR "Macky Sall" OR "Ousmane Sonko" OR "Bassirou Diomaye Faye" OR "Abdourahmane Diouf" OR "Khalifa Sall" OR "Fatma Gueye" OR "Abass Fall" OR "Ngoné Mbengue")) OR (("tàmbali" OR "jàngoro" OR "politique" OR "kampaañ" OR "goubernans" OR "wulli" OR "jàppale" OR "fàtt" OR "tali" OR "militéer" OR "guddi" OR "defaans" OR "jàmm" OR "teyat" OR "ndaw" OR "bataaxal bu dëppoo"OR "bataaxal yu dëppoo" OR "vaksin" OR "ndimbal" OR "ñàg" OR "kaku" OR "moské" OR "njàng" OR "kristiyaan") AND ("Senegaal"))',
    "DRC": r'(("élection" OR "présidentielle" OR "scrutin" OR "politique" OR "campagne" OR "gouvernance" OR "francophonie" OR "investissement" OR "commerce" OR "prêt" OR "dette" OR "route" OR "routière" OR "port" OR "rail" OR "oléoduc" OR "militaire" OR "arme" OR "défense" OR "paix" OR "terrorisme" OR "mercenaires" OR "bourse" OR "conficius" OR "université" OR "aide" OR "sanitaire" OR "cinéma" OR "théâtre" OR "jeune" OR "propagande" OR "désinformation" OR "réseaux sociaux" OR "fausses informations" OR "influenceur" OR "média" OR "5G" OR "Huawei" OR "IA" OR "Intelligence Artificielle" OR "cybersécurité" OR "internet" OR "satellite" OR "surveillance" OR "vaccin" OR "pandémique" OR "hôpital" OR "subvention" OR "agriculture" OR "énergie" OR "cacao" OR "renouvelable" OR "hydraulique" OR "mosqué" OR "église" OR "séminaire" OR "pélérinage" OR "anti-extrémisme" OR "islam" OR "christianisme" OR "chiite" OR "alliance" OR "sunnite") AND ("République démocratique du Congo" OR "RDC" OR "Kinshasa" OR "Congolais" OR "Kisangani" OR "Lubumbashi" OR "Kolwezi" OR "Kivu" OR "Kokolo" OR "Goma" OR "Corneille Nnanga" OR "Bertrand Bisimwa" OR "Sultani Makenga" OR "Willy Ngoma" OR "Lawrence Kanyuka" OR "Jean-Jacques Mamba" OR "Éric Nkuba" OR "Joseph Kabila" OR "Félix Tshisekedi")) OR (("bobongisi maponami" OR "maponami" OR "politiki" OR "kampanyi" OR "boyangeli" OR "mbongo na mosala" OR "libaku ya mbongo" OR "nzela" OR "ya nzela" OR "mibundu" OR "liboke ya bitumba" OR "bokengi" OR "kimia" OR "banyama ya liboma" OR "lisungi" OR "ya bokolongono" OR "elenga" OR "nsango ya lokuta" OR "nsango ya lokuta" OR "influenceur" OR "media" OR "5G" OR "Huawei" OR "IA" OR "vaksin" OR "lopitalo" OR "bilanga" OR "kura" OR "misiri" OR "ndako ya Nzambe" OR "kristoya") AND ("RDC"))',
    "Côte d'Ivoire": r'(("élection" OR "présidentielle" OR "scrutin" OR "politique" OR "campagne" OR "gouvernance" OR "francophonie" OR "investissement" OR "commerce" OR "prêt" OR "dette" OR "route" OR "routière" OR "port" OR "rail" OR "oléoduc" OR "militaire" OR "arme" OR "défense" OR "paix" OR "terrorisme" OR "mercenaires" OR "bourse" OR "conficius" OR "université" OR "aide" OR "sanitaire" OR "cinéma" OR "théâtre" OR "jeune" OR "propagande" OR "désinformation" OR "réseaux sociaux" OR "fausses informations" OR "influenceur" OR "média" OR "5G" OR "Huawei" OR "IA" OR "Intelligence Artificielle" OR "cybersécurité" OR "internet" OR "satellite" OR "surveillance" OR "vaccin" OR "pandémique" OR "hôpital" OR "subvention" OR "agriculture" OR "énergie" OR "cacao" OR "renouvelable" OR "hydraulique" OR "mosqué" OR "église" OR "séminaire" OR "pélérinage" OR "anti-extrémisme" OR "islam" OR "christianisme" OR "chiite" OR "alliance" OR "sunnite") AND ("Côte d\'Ivoire" OR "Cote d\'Ivoire" OR "Cote d'Ivoire" OR "Ivory Coast" OR "Abidjan" OR "Yamoussoukro" OR "Alassane Ouattara" OR "Laurent Gbagbo" OR "Henri Konan Bédié" OR "Robert Daudelin" OR "Emmanuel Etiennette" OR "Marcel Amon Tanoh" OR "Kandia Camara" OR "Amadou Gon Coulibaly" OR "Hamed Bakayoko" OR "Adama Bictogo" OR "Charles Blé Goudé")) OR (("baoulé" OR "baoule" OR "dioula" OR "dyula" OR "senufo" OR "lobi" OR "loby" OR "lobyi" OR "lobyie" OR "lobyien" OR "lobyienne" OR "lobyiens" OR "lobyienes" OR "lobyien(ne)" OR "lobyien(ne)s" OR "lobyien.ne" OR "lobyien.ne.s" OR "lobyien.ne.s." OR "lobyien.ne.s.." OR "lobyien.ne.s...") AND ("Côte d\'Ivoire"))', # Example for Cote d'Ivoire, expand as needed
    "South Africa": r'(("South Africa" OR "Pretoria" OR "Johannesburg" OR "Cape Town" OR "Durban" OR "ANC" OR "Ramaphosa" OR "BRICS") AND ("trade" OR "investment" OR "economic cooperation" OR "mining" OR "energy" OR "infrastructure" OR "military" OR "defense" OR "peace" OR "terrorism" OR "propaganda" OR "disinformation" OR "5G" OR "Huawei" OR "AI" OR "vaccine")) OR (("iNingizimu Afrika" OR "iPitoli" OR "iKapa" OR "iGoli" OR "iTheku" OR "iANC" OR "uRamaphosa" OR "iBRICS") AND ("uhwebo" OR "utshalo-mali" OR "ubambiswano" OR "ingqalasizinda" OR "ezempi" OR "ukuthula" OR "imfundo" OR "ezempilo"))' # Example for SA, expand as needed
}

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

    for country, base_query in QUERY_BY_COUNTRY.items():
        # Correctly nested loop
        for actor_name, actor_coll_id in ACTOR_COLLECTION_IDS.items():
            print(f"Searching {actor_name} media for {country}...")
            try:
                # Construct the full query string
                # Searching within the actor's collection for target country terms
                full_query = f"({base_query})"

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
