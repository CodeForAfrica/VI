import pandas as pd
import numpy as np
import time
import socket
import trafilatura
import cloudscraper 
import requests
import logging
from datetime import date, timedelta
from sqlalchemy import create_engine, text
from urllib.parse import urlparse
import os
import django
from django.conf import settings
import sys 

# Configure Django settings for database access (similar to your original script)
if not settings.configured:
    # Read DB details from environment variables
    DB_USER = os.getenv('DB_USER', 'postgres')
    DB_PASSWORD = os.getenv('DB_PASSWORD')
    DB_HOST = os.getenv('DB_HOST', 'localhost') 
    DB_PORT = os.getenv('DB_PORT', '5432')
    DB_NAME = os.getenv('DB_NAME', 'postgres')

    # Basic validation
    if not DB_PASSWORD:
        print("Error: DB_PASSWORD environment variable not set.")
        sys.exit(1) # Exit if password is missing
    if not DB_HOST or DB_HOST == 'localhost':
        print("Warning: DB_HOST is set to 'localhost'. Ensure this is correct for RDS access.")
        # Optionally, you could require it and exit if it's the default:
        # print("Error: DB_HOST environment variable not set to an RDS endpoint.")
        # sys.exit(1)

    settings.configure(
        DATABASES = {
            'default': {
                'ENGINE': 'django.db.backends.postgresql',
                'NAME': DB_NAME,
                'USER': DB_USER,
                'PASSWORD': DB_PASSWORD,
                'HOST': DB_HOST,
                'PORT': DB_PORT,
            }
        },
        INSTALLED_APPS=['dashboard'], 
        USE_TZ=True,
    )
    django.setup()

from django.db import connection

# Database configuration using Django settings (reads from env vars via settings)
DB_USER = os.getenv('DB_USER', 'postgres')
DB_PASSWORD = os.getenv('DB_PASSWORD')
DB_HOST = os.getenv('DB_HOST', 'localhost').strip() # Gets the host from env or defaults to localhost (warning above)
DB_PORT = os.getenv('DB_PORT', '5432')
DB_NAME = os.getenv('DB_NAME', 'postgres')

logging.basicConfig(
    filename='scraping_log.txt',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Ensure all columns required by your DB are listed here
db_columns = [
    "article_text", "posting_time", "media_outlet", "inferred_actor", 
    "target_country", "url", "lang_detect", "strategic_intent",
    "sector", "tone", "confidence", "use_afrolm", "llm_strat", 
    "llm_strat_conf", "llm_strat_notes", "pseudo_kept", "pseudo_weight", 
    "llm_strat_id", "strategic_intent_id"
]

# Database engine using Django-compatible settings (reads from env vars via settings)
try:
    engine = create_engine(f'postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}', future=True)
except Exception as e:
    print(f"Database engine creation failed - using Django ORM instead: {e}")
    engine = None # Indicate that direct engine failed

def url_exists(url):
    """Check if URL already exists in database using Django ORM"""
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) FROM dashboard_medianarrative WHERE url = %s", [url])
            return cursor.fetchone()[0] > 0
    except Exception as e:
        print(f"Error checking if URL exists: {e}")
        return False 

def scrape_full_text_robust(url):
    """Scrape full text with multiple fallback methods"""
    try:
        # Method 1: Trafilatura (most reliable)
        downloaded = trafilatura.fetch_url(url)
        if downloaded:
            text_content = trafilatura.extract(downloaded)
            if text_content and len(text_content.strip()) > 100:
                return text_content.strip()
        
        # Method 2: CloudScraper as backup
        scraper = cloudscraper.create_scraper()
        response = scraper.get(url, timeout=15)
        if response.status_code == 200:
            text_content = trafilatura.extract(response.text)
            if text_content:
                return text_content.strip()
        
        return f"Failed to scrape content from {url}"
    except Exception as e:
        return f"Error scraping {url}: {str(e)}"

def safe_mediacloud_search(query, start_date, end_date, collection_ids, api_key):
    """Safely search MediaCloud API with fallback"""
    try:
        # Try different endpoints
        endpoints = [
            f"https://www.mediacloud.org/api/v2/stories_public/list",
            f"https://mediacloud.org/api/v2/stories_public/list",
            f"https://api.mediacloud.org/api/v2/stories_public/list"
        ]
        
        for endpoint in endpoints:
            try:
                params = {
                    'q': query,
                    'start_date': start_date.strftime('%Y-%m-%d'),
                    'end_date': end_date.strftime('%Y-%m-%d'),
                    'collection_ids': ','.join(map(str, collection_ids)),
                    'limit': 100, # Increased limit per request
                    'format': 'json',
                    'key': api_key
                }
                
                response = requests.get(
                    endpoint,
                    params=params,
                    headers={'Authorization': f'ApiKey {api_key}'},
                    timeout=30
                )
                
                if response.status_code == 200:
                    data = response.json()
                    stories = data.get('stories', [])
                    count = data.get('count', 0)
                    print(f"   Retrieved {len(stories)} stories from {endpoint.split('/')[2]}")
                    return stories, count
                elif response.status_code in [404, 403, 401]:
                    print(f"   Endpoint {endpoint.split('/')[2]} returned {response.status_code}, trying next...")
                    continue  # Try next endpoint
                else:
                    print(f"   Endpoint {endpoint.split('/')[2]} returned {response.status_code}, trying next...")
                    continue # Try next endpoint
            except requests.exceptions.RequestException as e:
                print(f"   Request failed for {endpoint.split('/')[2]}: {e}, trying next...")
                continue
            except Exception as e:
                print(f"   Unexpected error for {endpoint.split('/')[2]}: {e}, trying next...")
                continue
        
        # If all endpoints fail, return empty results
        print("   All endpoints failed.")
        return [], 0
        
    except Exception as e:
        logging.error(f"MediaCloud API Error: {e}")
        return [], 0

def verify_dns(host):
    """Checks if the RDS endpoint is reachable before trying to connect."""
    try:
        socket.gethostbyname(host)
        return True
    except socket.gaierror:
        print(f"DNS Error: Cannot resolve {host}")
        print("Check if your RDS instance is 'Publicly Accessible' or if you are on the correct VPN/Network.")
        return False

def main():
    print("Starting MediaCloud Ingestion Process...")
    print(f"Target Date Range: {START_DATE} to {END_DATE}")
    print(f"Target Countries: {list(QUERY_BY_COUNTRY.keys())}")
    print(f"Target Actors: {list(ACTOR_COLLECTION_IDS.keys())}")

    # Validate environment variables before proceeding
    api_key = os.getenv('MEDIA_CLOUD_API_KEY')
    if not api_key:
        print("ERROR: MEDIA_CLOUD_API_KEY environment variable not set.")
        return
    print("MediaCloud API Key found.")

    if not verify_dns(DB_HOST):
        print("ERROR: Could not resolve DB_HOST. Exiting.")
        return

    try:
        if engine is None: # If direct engine creation failed
            print("Database engine creation failed initially. Proceeding with Django ORM for inserts.")
        else:
            print("Database connection initialized via SQLAlchemy.")
    except Exception as e:
        print(f"Database engine creation failed: {e}")
        return

    all_records = []
    print("Querying MediaCloud API...")

    # Try to use the original mediacloud library if available, otherwise use safe method
    try:
        # If the original mediacloud library is available and working
        import mediacloud.api
        print("Using original MediaCloud API library...")
        mc_search = mediacloud.api.SearchApi(api_key)
        
        # Search each target country's query across each actor country's media collection
        for country, base_query in QUERY_BY_COUNTRY.items():
            actor_collection_id = TARGET_COLLECTION_IDS.get(country) # Get the target country's collection ID
            if not actor_collection_id:
                print(f"Warning: No collection ID found for target country {country}. Skipping.")
                continue

            for actor_name, actor_coll_id in ACTOR_COLLECTION_IDS.items():
                print(f"  Searching for articles in {actor_name} media ({actor_coll_id}) about {country} using query: {base_query[:50]}...")
                try:
                    # Use the target country query, search within the actor's collection
                    stories, count = mc_search.story_list(
                        query=base_query, # The query contains the target country name and keywords
                        start_date=START_DATE,
                        end_date=END_DATE,
                        collection_ids=[actor_coll_id] # Search in the actor's media collection
                    )
                    print(f"    Found {len(stories)} stories.")
                    for s in stories:
                        # --- CREATE THE RECORD DICTIONARY ---
                        # Start with all columns set to None
                        record = {col: None for col in db_columns}
                        # Set default values for specific columns that are NOT NULL in the database
                        # but might not be provided by the MediaCloud API directly.
                        # These defaults are placeholders until the full text is scraped and ML runs.
                        record['pseudo_kept'] = False
                        record['pseudo_weight'] = 0.0
                        record['use_afrolm'] = False
                        # Update the dictionary with data from the MediaCloud API response
                        record.update({
                            "url": s.get("url"),
                            "posting_time": str(s.get("publish_date")), # Ensure it's a string or datetime object as expected by the DB
                            "media_outlet": s.get("media_name"),
                            "inferred_actor": actor_name, # Comes from the loop variable
                            "target_country": country,   # Comes from the outer loop variable
                            "lang_detect": s.get("language"),
                            "confidence": s.get("score"), # Assuming 'score' from MC API maps to 'confidence' in DB
                            # 'article_text' is NOT set here, it will be scraped later during insertion
                            # Other fields like strategic_intent, tone, vulnerability_index, sector, ml_processed_at
                            # are also not set here; they will be filled by the ML pipeline later.
                        })
                        # Append the completed record dictionary to the list
                        all_records.append(record)
                except Exception as e:
                    logging.error(f"MediaCloud Error {country}/{actor_name} (Actor Coll {actor_coll_id}): {e}")
                    print(f"    Error for {country}/{actor_name}: {e}")

    except ImportError:
        print("Original MediaCloud library not found. Using safe API method...")
        # If original library not available, use safe method
        # Loop structure adjusted for clarity - search actor collections for target country queries
        for country, base_query in QUERY_BY_COUNTRY.items():
            actor_collection_id = TARGET_COLLECTION_IDS.get(country) # Get the target country's collection ID (might not be used directly in safe method)
            if not actor_collection_id:
                print(f"Warning: No collection ID found for target country {country}. Skipping.")
                continue

            for actor_name, actor_coll_id in ACTOR_COLLECTION_IDS.items():
                print(f"  Searching for articles in {actor_name} media ({actor_coll_id}) about {country} using query: {base_query[:50]}...")
                try:
                    # Use the target country query, search within the actor's collection
                    stories, count = safe_mediacloud_search(base_query, START_DATE, END_DATE, [actor_coll_id], api_key)
                    print(f"    Found {len(stories)} stories via safe method.")
                    for s in stories:
                        # --- CREATE THE RECORD DICTIONARY ---
                        # Start with all columns set to None
                        record = {col: None for col in db_columns}
                        # Set default values for specific columns that are NOT NULL in the database
                        # but might not be provided by the MediaCloud API directly.
                        # These defaults are placeholders until the full text is scraped and ML runs.
                        record['pseudo_kept'] = False
                        record['pseudo_weight'] = 0.0
                        record['use_afrolm'] = False
                        # Update the dictionary with data from the MediaCloud API response
                        record.update({
                            "url": s.get("url"),
                            "posting_time": str(s.get("publish_date")), # Ensure it's a string or datetime object as expected by the DB
                            "media_outlet": s.get("media_name"),
                            "inferred_actor": actor_name, # Comes from the loop variable
                            "target_country": country,   # Comes from the outer loop variable
                            "lang_detect": s.get("language"),
                            "confidence": s.get("score"), # Assuming 'score' from MC API maps to 'confidence' in DB
                            # 'article_text' is NOT set here, it will be scraped later during insertion
                            # Other fields like strategic_intent, tone, vulnerability_index, sector, ml_processed_at
                            # are also not set here; they will be filled by the ML pipeline later.
                        })
                        # Append the completed record dictionary to the list
                        all_records.append(record)
                except Exception as e:
                    logging.error(f"Safe API Error {country}/{actor_name} (Actor Coll {actor_coll_id}): {e}")
                    print(f"    Error for {country}/{actor_name} (safe method): {e}")
    
    df = pd.DataFrame(all_records)
    if df.empty:
        print("No articles found or API unavailable. Using existing data.")
        print("Your existing records remain accessible in the dashboard.")
        return

    MAX_ARTICLES_PER_RUN = 200 # Limit per run to manage resources/time
    MAX_RUNTIME_SECONDS = 800 # Time limit to prevent timeouts in Lambda
    df = df.head(MAX_ARTICLES_PER_RUN)
    print(f"Found {len(df)} articles (capped at {MAX_ARTICLES_PER_RUN}). Starting Scraper...")

    loop_start = time.time()
    scraped_count = 0
    skipped_count = 0
    failed_scrape_count = 0
    db_error_count = 0

    for idx, row in df.iterrows():
        if time.time() - loop_start > MAX_RUNTIME_SECONDS:
            print(f"Time budget ({MAX_RUNTIME_SECONDS}s) reached at article {idx}. Stopping.")
            break

        url = row['url']
        if not url or not isinstance(url, str):
            print(f"Skipping invalid URL at index {idx}: {url}")
            skipped_count += 1
            continue

        if url_exists(url): # Check if already in DB using Django ORM
            print(f"URL already exists, skipping: {url[:50]}...")
            skipped_count += 1
            continue

        # SCRAPE THE ARTICLE TEXT FOR THE CURRENT URL
        content = scrape_full_text_robust(url)

        if "Failed" not in content and "Error" not in content:
            row_data = row.to_dict()
            # INSERT THE SCRAPED CONTENT INTO row_data BEFORE INSERTION
            row_data['article_text'] = content

            # Override inferred_actor based on media outlet if needed (example)
            if "nytimes.com" in url or row['media_outlet'] == 'The New York Times':
                row_data['inferred_actor'] = 'USA'

            # Insert using Django ORM or SQLAlchemy engine
            if engine: # Use SQLAlchemy engine if available
                try:
                    final_df = pd.DataFrame([row_data])[db_columns]
                    with engine.begin() as conn:
                        final_df.to_sql('dashboard_medianarrative', conn, if_exists='append', index=False)
                    scraped_count += 1
                    print(f"  [{scraped_count}] Saved ({row['target_country']} -> {row['inferred_actor']}): {url[:40]}...")
                except Exception as e:
                    logging.error(f"SQLAlchemy DB Insert Error for {url}: {e}")
                    db_error_count += 1
            else: # Fallback to Django ORM for insertion (requires converting DataFrame row to model instance)
                try:
                    from dashboard.models import MediaNarrative # Import inside try block
                    # Prepare data, ensuring types match model
                    model_data = {
                        'url': row_data.get('url'),
                        'posting_time': pd.to_datetime(row_data.get('posting_time'), errors='coerce'), # Convert to datetime
                        'media_outlet': row_data.get('media_outlet'),
                        'inferred_actor': row_data.get('inferred_actor'),
                        'target_country': row_data.get('target_country'),
                        'article_text': row_data.get('article_text'), # NOW contains the scraped content
                        'lang_detect': row_data.get('lang_detect'),
                    
                        # Set default values for fields not coming from MC API but required by DB and NOT NULL
                        'pseudo_kept': row_data.get('pseudo_kept', False), # Provide default if not in row_data
                        'pseudo_weight': row_data.get('pseudo_weight', 0.0), # Provide default if not in row_data
                        'use_afrolm': row_data.get('use_afrolm', False), # Provide default if not in row_data
                        'confidence': row_data.get('confidence') or 0.0, # Expected to be filled by ML, default if MC API doesn't provide it
                        
                        # Fields likely to be filled by ML later (strategic_intent, tone, vulnerability_index) can remain NULL initially
                        'strategic_intent': None, # Expected to be filled by ML
                        'tone': None, # Expected to be filled by ML
                        'vulnerability_index': None, # Expected to be filled by ML process
                        'ml_processed_at': None, # Expected to be filled by ML process
                        'sector': None, # Expected to be filled by ML process based on article_text
                        'llm_strat': None, # Expected to be filled by LLM later
                        'llm_strat_conf': None, # Expected to be filled by LLM later
                        'llm_strat_notes': None, # Expected to be filled by LLM later
                        'llm_strat_id': None, # Expected to be filled by LLM later
                        'strategic_intent_id': None, # Expected to be filled by LLM later
                    }
                    
                    # Create and save the instance
                    narrative = MediaNarrative(**model_data)
                    narrative.save()
                    scraped_count += 1
                    print(f"  [{scraped_count}] Saved (ORM) ({row['target_country']} -> {row['inferred_actor']}): {url[:40]}...")
                except Exception as e:
                    logging.error(f"Django ORM DB Insert Error for {url}: {e}")
                    db_error_count += 1
                    print(f"    DB Error for {url}: {e}")

        else:
            failed_scrape_count += 1
            print(f"  [{failed_scrape_count}] Failed scraping: {content[:50]} for {str(url)[:30]}...")

        time.sleep(0.5) 

    print(f"\n--- Ingestion Summary ---")
    print(f"Total Articles Fetched from API: {len(df)}")
    print(f"Successfully Scraped and Saved: {scraped_count}")
    print(f"Already Existed (Skipped): {skipped_count}")
    print(f"Failed to Scrape: {failed_scrape_count}")
    print(f"Database Errors: {db_error_count}")
    print("-------------------------")

# correct collection IDs
ACTOR_COLLECTION_IDS = {
    "USA":          34412234,
    "France":       34412146,
    "China":        34412193,
    "Russia":       34412232,
    "Turkey":       34412131,
    "Saudi Arabia": 34412050,
    "Israel":       34412391,
    "Iran":         34412284,
    "UAE":          34412114,
}

TARGET_COLLECTION_IDS = {
    "Ethiopia":       34412034,
    "Senegal":        38380807,
    "DRC":            34412042,
    "South Africa":   34412238,
    "Côte d'Ivoire":  34412173,
    
}

# Define START_DATE and END_DATE
START_DATE = date.today() - timedelta(days=1) # Yesterday
END_DATE   = date.today() # Today

# Use your exact, complex queries for all target countries
QUERY_BY_COUNTRY = {
    "Ethiopia": '(("infrastructure project" OR "debt relief" OR "railway" OR "industrial park" OR "investment" OR "foreign aid" OR "trade" OR "mining" OR "manufacturing" OR "energy project" OR "military cooperation" OR "arms sale" OR "defense pact" OR "peacekeeping" OR "security partnership" OR "diplomatic relations" OR "election" OR "governance" OR "anti-corruption" OR "state visit" OR "Confucius Institute" OR "cultural exchange" OR "language school" OR "scholarship" OR "digital Silk Road" OR "5G" OR "Huawei" OR "surveillance" OR "cybersecurity" OR "AI" OR "vaccine" OR "pandemic aid" OR "hospital construction" OR "education" OR "university" OR "climate change" OR "hydropower" OR "agriculture" OR "land lease cooperation" OR "mosque" OR "church" OR "religious diplomacy" OR "propaganda" OR "disinformation" OR "social media campaign" OR "broadcast in Amharic") OR ("ኢትዮጵያ" OR "አዲስ አበባ" OR "ኦሮሚያ " OR "ትግራይ" OR "አማራ" OR "የአፍሪካ ቀንድ"))',
    "Senegal": '(("multipartisme" OR "teranga" OR "TER" OR "FAS" OR "DAGE" OR "élection" OR "présidentielle" OR "scrutin" OR "politique" OR "campagne" OR "gouvernance" OR "francophonie" OR "investissement" OR "commerce" OR "prêt" OR "dette" OR "route" OR "routière" OR "port" OR "rail" OR "oléoduc" OR "militaire" OR "arme" OR "défense" OR "paix" OR "terrorisme" OR "mercenaires" OR "bourse" OR "conficius" OR "université" OR "aide" OR "sanitaire" OR "cinéma" OR "théâtre" OR "jeune" OR "propagande" OR "désinformation" OR "réseaux sociaux" OR "fausses informations" OR "influenceur" OR "média" OR "5G" OR "Huawei" OR "IA" OR "Intelligence Artificielle" OR "cybersécurité" OR "internet" OR "satellite" OR "surveillance" OR "vaccin" OR "pandémique" OR "hôpital" OR "subvention" OR "agriculture" OR "énergie" OR "cacao" OR "renouvelable" OR "hydraulique" OR "mosqué" OR "église" OR "séminaire" OR "pélérinage" OR "anti-extrémisme" OR "islam" OR "christianisme" OR "chiite" OR "alliance" OR "sunnite") AND ("Senegal" OR "Senegalais" OR "Dakar" OR "Diamniadio" OR "Macky Sall" OR "Ousmane Sonko" OR "Bassirou Diomaye Faye" OR "Abdourahmane Diouf" OR "Khalifa Sall" OR "Fatma Gueye" OR "Abass Fall" OR "Ngoné Mbengue")) OR (("tàmbali" OR "jàngoro" OR "politique" OR "kampaañ" OR "goubernans" OR "wulli" OR "jàppale" OR "fàtt" OR "tali" OR "militéer" OR "guddi" OR "defaans" OR "jàmm" OR "teyat" OR "ndaw" OR "bataaxal bu dëppoo"OR "bataaxal yu dëppoo" OR "vaksin" OR "ndimbal" OR "ñàg" OR "kaku" OR "moské" OR "njàng" OR "kristiyaan") AND ("Senegaal"))',
    "DRC": '(("élection" OR "présidentielle" OR "scrutin" OR "politique" OR "campagne" OR "gouvernance" OR "francophonie" OR "investissement" OR "commerce" OR "prêt" OR "dette" OR "route" OR "routière" OR "port" OR "rail" OR "oléoduc" OR "militaire" OR "arme" OR "défense" OR "paix" OR "terrorisme" OR "mercenaires" OR "bourse" OR "conficius" OR "université" OR "aide" OR "sanitaire" OR "cinéma" OR "théâtre" OR "jeune" OR "propagande" OR "désinformation" OR "réseaux sociaux" OR "fausses informations" OR "influenceur" OR "média" OR "5G" OR "Huawei" OR "IA" OR "Intelligence Artificielle" OR "cybersécurité" OR "internet" OR "satellite" OR "surveillance" OR "vaccin" OR "pandémique" OR "hôpital" OR "subvention" OR "agriculture" OR "énergie" OR "cacao" OR "renouvelable" OR "hydraulique" OR "mosqué" OR "église" OR "séminaire" OR "pélérinage" OR "anti-extrémisme" OR "islam" OR "christianisme" OR "chiite" OR "alliance" OR "sunnite") AND ("République démocratique du Congo" OR "RDC" OR "Kinshasa" OR "Congolais" OR "Kisangani" OR "Lubumbashi" OR "Kolwezi" OR "Kivu" OR "Kokolo" OR "Goma" OR "Corneille Nnanga" OR "Bertrand Bisimwa" OR "Sultani Makenga" OR "Willy Ngoma" OR "Lawrence Kanyuka" OR "Jean-Jacques Mamba" OR "Éric Nkuba" OR "Joseph Kabila" OR "Félix Tshisekedi")) OR (("bobongami" OR "maponami" OR "politiki" OR "kampanyi" OR "boyangeli" OR "mbongo na mosala" OR "libaku ya mbongo" OR "nzela" OR "ya nzela" OR "mibundu" OR "liboke ya bitumba" OR "bokengi" OR "kimia" OR "banyama ya liboma" OR "lisungi" OR "ya bokolongono" OR "elenga" OR "nsango ya lokuta" OR "nsango ya lokuta" OR "influenceur" OR "media" OR "5G" OR "Huawei" OR "IA" OR "vaksin" OR "lopitalo" OR "bilanga" OR "kura" OR "misiri" OR "ndako ya Nzambe" OR "kristoya") AND ("RDC"))',
    "Côte d'Ivoire": '(("élection" OR "présidentielle" OR "scrutin" OR "politique" OR "campagne" OR "gouvernance" OR "francophonie" OR "investissement" OR "commerce" OR "prêt" OR "dette" OR "route" OR "routière" OR "port" OR "rail" OR "oléoduc" OR "militaire" OR "arme" OR "défense" OR "paix" OR "terrorisme" OR "mercenaires" OR "bourse" OR "conficius" OR "université" OR "aide"sanitaire" OR "cinéma" OR "théâtre" OR "jeune" OR "propagande" OR "désinformation" OR "réseaux sociaux" OR "fausses informations" OR "influenceur" OR "média" OR "5G" OR "Huawei" OR "IA" OR "Intelligence Artificielle" OR "cybersécurité" OR "internet" OR "satellite" OR "surveillance" OR "vaccin" OR "pandémique" OR "hôpital" OR "subvention" OR "agriculture" OR "énergie" OR "cacao" OR "renouvelable" OR "hydraulique" OR "mosqué" OR "église" OR "séminaire" OR "pélérinage" OR "anti-extrémisme" OR "islam" OR "christianisme" OR "chiite" OR "alliance" OR "sunnite") AND ("Côte d\'Ivoire" OR "Cote d\'Ivoire" OR "Cote d'Ivoire" OR "Ivory Coast" OR "Abidjan" OR "Yamoussoukro" OR "Alassane Ouattara" OR "Laurent Gbagbo" OR "Henri Konan Bédié" OR "Robert Daudelin" OR "Emmanuel Etiennette" OR "Marcel Amon Tanoh" OR "Kandia Camara" OR "Amadou Gon Coulibaly" OR "Hamed Bakayoko" OR "Adama Bictogo" OR "Charles Blé Goudé")) OR (("baoulé" OR "baoule" OR "dioula" OR "dyula" OR "senufo" OR "lobi" OR "loby" OR "lobyi" OR "lobyie" OR "lobyien" OR "lobyienne" OR "lobyiens" OR "lobyienes" OR "lobyien(ne)" OR "lobyien(ne)s" OR "lobyien.ne" OR "lobyien.ne.s" OR "lobyien.ne.s." OR "lobyien.ne.s.." OR "lobyien.ne.s...") AND ("Côte d\'Ivoire"))', # Example for Cote d'Ivoire, expand as needed
    "South Africa": '(("South Africa" OR "Pretoria" OR "Johannesburg" OR "Cape Town" OR "Durban" OR "ANC" OR "Ramaphosa" OR "BRICS") AND ("trade" OR "investment" OR "economic cooperation" OR "mining" OR "energy" OR "infrastructure" OR "military" OR "defense" OR "peace" OR "terrorism" OR "propaganda" OR "disinformation" OR "5G" OR "Huawei" OR "AI" OR "vaccine")) OR (("iNingizimu Afrika" OR "iPitoli" OR "iKapa" OR "iGoli" OR "iTheku" OR "iANC" OR "uRamaphosa" OR "iBRICS") AND ("uhwebo" OR "utshalo-mali" OR "ubambiswano" OR "ingqalasizinda" OR "ezempi" OR "ukuthula" OR "imfundo" OR "ezempilo"))' # Example for SA, expand as needed
}

# THE MAIN CHECK
if __name__ == "__main__":
    main()
