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
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR)) 

# 2. Configure Django
if not settings.configured:
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'vulnerability_index.settings') # Use your actual project name
    settings.configure(
        DATABASES={
            'default': {
                'ENGINE': 'django.db.backends.postgresql',
                'NAME': os.getenv('DB_NAME', 'postgres'),
                'USER': os.getenv('DB_USER', 'postgres'),
                'PASSWORD': os.getenv('DB_PASSWORD'),
                'HOST': os.getenv('DB_HOST'),
                'PORT': os.getenv('DB_PORT', '5432'),
            }
        },
        INSTALLED_APPS=['dashboard'],
        USE_TZ=True,
    )
    django.setup()

from django.db import connection
from django.core.cache import cache

# Database configuration using Django settings
DB_USER = os.getenv('DB_USER', 'postgres')
DB_PASSWORD = os.getenv('DB_PASSWORD')
DB_HOST = os.getenv('DB_HOST', 'rds-vulnerabilityindex-euwest-01.cfgmtx8ishfx.eu-west-1.rds.amazonaws.com').strip()
DB_PORT = os.getenv('DB_PORT', '5432')
DB_NAME = os.getenv('DB_NAME', 'postgres')

logging.basicConfig(
    filename='scraping_log.txt',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

db_columns = [
    "article_text", "posting_time", "media_outlet", "inferred_actor", 
    "target_country", "url", "lang_detect", "strategic_intent",
    "sector", "tone", "confidence", "use_afrolm", "llm_strat", 
    "llm_strat_conf", "llm_strat_notes", "pseudo_kept", "pseudo_weight", 
    "llm_strat_id", "strategic_intent_id"
]

# Database engine using Django-compatible settings
try:
    engine = create_engine(f'postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}', future=True)
except:
    print("Database engine creation failed - using Django ORM instead")

def url_exists(url):
    """Check if URL already exists in database using Django ORM"""
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) FROM dashboard_medianarrative WHERE url = %s", [url])
            return cursor.fetchone()[0] > 0
    except:
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
                    'limit': 100,
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
                    return stories, count
                elif response.status_code in [404, 403, 401]:
                    continue  # Try next endpoint
            except:
                continue
        
        # If all endpoints fail, return empty results
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
    if not verify_dns(DB_HOST):
        return
    try:
        engine = create_engine(
            f'postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}', 
            future=True
        )
        print("Database connection initialized.")
    except Exception as e:
        print(f"Database engine creation failed: {e}")
        return
    all_records = []
    print("Querying MediaCloud API...")
    
    # Use environment variable for API key
    api_key = os.getenv('MEDIACLOUD_API_KEY')
    if not api_key:
        print("MEDIACLOUD_API_KEY not set. Continuing with existing data only.")
        print("Your existing 15,166 records remain accessible.")
        return
    
    # Try to use the original MediaCloud library if available, otherwise use safe method
    try:
        # If the original mediacloud library is available and working
        import mediacloud.api
        mc_search = mediacloud.api.SearchApi(api_key)
        
        # Search each target country's query across each actor country's media collection
        for country, base_query in QUERY_BY_COUNTRY.items():
            for actor_name, coll_id in ACTOR_COLLECTION_IDS.items():
                try:
                    stories, _ = mc_search.story_list(base_query, START_DATE, END_DATE, collection_ids=[coll_id])
                    for s in stories:
                        record = {col: None for col in db_columns}
                        record['pseudo_kept'] = False
                        record['pseudo_weight'] = 0.0
                        record.update({
                            "url": s.get("url"),
                            "posting_time": str(s.get("publish_date")),
                            "media_outlet": s.get("media_name"),
                            "inferred_actor": actor_name,
                            "target_country": country,
                            "lang_detect": s.get("language"),
                            "confidence": s.get("score")
                        })
                        all_records.append(record)
                except Exception as e:
                    logging.error(f"MediaCloud Error {country}/{actor_name}: {e}")

    except ImportError:
        # If original library not available, use safe method
        print("Using safe API method...")
        for country, base_query in QUERY_BY_COUNTRY.items():
            for actor_name, coll_id in ACTOR_COLLECTION_IDS.items():
                try:
                    stories, _ = safe_mediacloud_search(base_query, START_DATE, END_DATE, [coll_id], api_key)
                    for s in stories:
                        record = {col: None for col in db_columns}
                        record['pseudo_kept'] = False
                        record['pseudo_weight'] = 0.0
                        record.update({
                            "url": s.get("url"),
                            "posting_time": str(s.get("publish_date")),
                            "media_outlet": s.get("media_name"),
                            "inferred_actor": actor_name,
                            "target_country": country,
                            "lang_detect": s.get("language"),
                            "confidence": s.get("score")
                        })
                        all_records.append(record)
                except Exception as e:
                    logging.error(f"Safe API Error {country}/{actor_name}: {e}")
    
    df = pd.DataFrame(all_records)
    if df.empty:
        print("No articles found or API unavailable. Using existing data.")
        print("Your existing 15,166 records remain accessible in the dashboard.")
        return

    MAX_ARTICLES_PER_RUN = 200
    MAX_RUNTIME_SECONDS = 800
    df = df.head(MAX_ARTICLES_PER_RUN)
    print(f"Found {len(df)} articles (capped at {MAX_ARTICLES_PER_RUN}). Starting Scraper...")

    loop_start = time.time()
    for idx, row in df.iterrows():
        if time.time() - loop_start > MAX_RUNTIME_SECONDS:
            print(f"Time budget reached at article {idx}. Stopping to avoid Lambda timeout.")
            break

        url = row['url']
        if not url or not isinstance(url, str) or url_exists(url):
            continue

        content = scrape_full_text_robust(url)

        if "Failed" not in content and "Error" not in content:
            row_data = row.to_dict()
            row_data['article_text'] = content

            if "nytimes.com" in url or row['media_outlet'] == 'The New York Times':
                row_data['inferred_actor'] = 'USA'

            try:
                final_df = pd.DataFrame([row_data])[db_columns]
                with engine.begin() as conn:
                    final_df.to_sql('dashboard_medianarrative', conn, if_exists='append', index=False)
                print(f"[{idx+1}/{len(df)}] Saved ({row['target_country']}): {url[:40]}...")
            except Exception as e:
                logging.error(f"DB Insert Error: {e}")
        else:
            print(f"[{idx+1}/{len(df)}] Failed: {content} for {str(url)[:30]}")

        time.sleep(0.5)

    print("\nFinished. Check your database now.")

    # These lines must be indented to match the 'for' loop above
    print("🧹 Cleaning dashboard cache...")
    try:
        cache.clear()
        print("✅ Cache cleared successfully!")
    except Exception as e:
        print(f"⚠️ Cache clear failed: {e}")
        
# api key
API_KEY = os.getenv('MEDIACLOUD_API_KEY')

# MediaCloud collection IDs — actor countries whose media is searched for narratives about target countries
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

# MediaCloud collection IDs — target African countries being monitored
TARGET_COLLECTION_IDS = {
    "Ethiopia":       34412034,
    "Senegal":        38380807,
    "DRC":            34412042,
    "SA":             34412238,
    "Côte d'Ivoire":  34412173,
}

START_DATE = date.today() - timedelta(days=1)
END_DATE   = date.today()

# Use your exact queries for all 4 countries
QUERY_BY_COUNTRY = {
    "Ethiopia": '(("infrastructure project" OR "debt relief" OR "railway" OR "industrial park" OR "investment" OR "foreign aid" OR "trade" OR "mining" OR "manufacturing" OR "energy project" OR "military cooperation" OR "arms sale" OR "defense pact" OR "peacekeeping" OR "security partnership" OR "diplomatic relations" OR "election" OR "governance" OR "anti-corruption" OR "state visit" OR "Confucius Institute" OR "cultural exchange" OR "language school" OR "scholarship" OR "digital Silk Road" OR "5G" OR "Huawei" OR "surveillance" OR "cybersecurity" OR "AI" OR "vaccine" OR "pandemic aid" OR "hospital construction" OR "education" OR "university" OR "climate change" OR "hydropower" OR "agriculture" OR "land lease" OR "energy cooperation" OR "mosque" OR "church" OR "religious diplomacy" OR "propaganda" OR "disinformation" OR "social media campaign" OR "broadcast in Amharic") OR ("ኢትዮጵያ" OR "አዲስ አበባ" OR "ኦሮሚያ " OR "ትግራይ" OR "አማራ" OR "የአፍሪካ ቀንድ"))',
    "Senegal": '(("multipartisme" OR "teranga" OR "TER" OR "FAS" OR "DAGE" OR "élection" OR "présidentielle" OR "scrutin" OR "politique" OR "campagne" OR "gouvernance" OR "francophonie" OR "investissement" OR "commerce" OR "prêt" OR "dette" OR "route" OR "routière" OR "port" OR "rail" OR "oléoduc" OR "militaire" OR "arme" OR "défense" OR "paix" OR "terrorisme" OR "mercenaires" OR "bourse" OR "conficius" OR "université" OR "aide" OR "sanitaire" OR "cinéma" OR "théâtre" OR "jeune" OR "propagande" OR "désinformation" OR "réseaux sociaux" OR "fausses informations" OR "influenceur" OR "média" OR "5G" OR "Huawei" OR "IA" OR "Intelligence Artificielle" OR "cybersécurité" OR "internet" OR "satellite" OR "surveillance" OR "vaccin" OR "pandémique" OR "hôpital" OR "subvention" OR "agriculture" OR "énergie" OR "cacao" OR "renouvelable" OR "hydraulique" OR "mosqué" OR "église" OR "séminaire" OR "pélérinage" OR "anti-extrémisme" OR "islam" OR "christianisme" OR "chiite" OR "alliance" OR "sunnite") AND ("Senegal" OR "Senegalais" OR "Dakar" OR "Diamniadio" OR "Macky Sall" OR "Ousmane Sonko" OR "Bassirou Diomaye Faye" OR "Abdourahmane Diouf" OR "Khalifa Sall" OR "Fatma Gueye" OR "Abass Fall" OR "Ngoné Mbengue")) OR (("tàmbali" OR "jàngoro" OR "politique" OR "kampaañ" OR "goubernans" OR "wulli" OR "jàppale" OR "fàtt" OR "tali" OR "militéer" OR "guddi" OR "defaans" OR "jàmm" OR "teyat" OR "ndaw" OR "bataaxal bu dëppoo"OR "bataaxal yu dëppoo" OR "vaksin" OR "ndimbal" OR "ñàg" OR "kaku" OR "moské" OR "njàng" OR "kristiyaan") AND ("Senegaal"))',
    "DRC": '(("élection" OR "présidentielle" OR "scrutin" OR "politique" OR "campagne" OR "gouvernance" OR "francophonie" OR "investissement" OR "commerce" OR "prêt" OR "dette" OR "route" OR "routière" OR "port" OR "rail" OR "oléoduc" OR "militaire" OR "arme" OR "défense" OR "paix" OR "terrorisme" OR "mercenaires" OR "bourse" OR "conficius" OR "université" OR "aide" OR "sanitaire" OR "cinéma" OR "théâtre" OR "jeune" OR "propagande" OR "désinformation" OR "réseaux sociaux" OR "fausses informations" OR "influenceur" OR "média" OR "5G" OR "Huawei" OR "IA" OR "Intelligence Artificielle" OR "cybersécurité" OR "internet" OR "satellite" OR "surveillance" OR "vaccin" OR "pandémique" OR "hôpital" OR "subvention" OR "agriculture" OR "énergie" OR "cacao" OR "renouvelable" OR "hydraulique" OR "mosqué" OR "église" OR "séminaire" OR "pélérinage" OR "anti-extrémisme" OR "islam" OR "christianisme" OR "chiite" OR "alliance" OR "sunnite") AND ("République démocratique du Congo" OR "RDC" OR "Kinshasa" OR "Congolais" OR "Kisangani" OR "Lubumbashi" OR "Kolwezi" OR "Kivu" OR "Kokolo" OR "Goma" OR "Corneille Nnanga" OR "Bertrand Bisimwa" OR "Sultani Makenga" OR "Willy Ngoma" OR "Lawrence Kanyuka" OR "Jean-Jacques Mamba" OR "Éric Nkuba" OR "Joseph Kabila" OR "Félix Tshisekedi")) OR (("bobongisi maponami" OR "maponami" OR "politiki" OR "kampanyi" OR "boyangeli" OR "mbongo na mosala" OR "libaku ya mbongo" OR "nzela" OR "ya nzela" OR "mibundu" OR "liboke ya bitumba" OR "bokengi" OR "kimia" OR "banyama ya liboma" OR "lisungi" OR "ya bokolongono" OR "elenga" OR "nsango ya lokuta" OR "nsango ya lokuta" OR "influenceur" OR "media" OR "5G" OR "Huawei" OR "IA" OR "vaksin" OR "lopitalo" OR "bilanga" OR "kura" OR "misiri" OR "ndako ya Nzambe" OR "kristoya") AND ("RDC"))',
    "SA": '(("South Africa" OR "Pretoria" OR "Johannesburg" OR "Cape Town" OR "Durban" OR "ANC" OR "Ramaphosa" OR "BRICS") AND ("trade" OR "investment" OR "economic cooperation" OR "mining" OR "energy" OR "infrastructure" OR "military" OR "defense" OR "peace" OR "terrorism" OR "propaganda" OR "disinformation" OR "5G" OR "Huawei" OR "AI" OR "vaccine")) OR (("iNingizimu Afrika" OR "iPitoli" OR "iKapa" OR "iGoli" OR "iTheku" OR "iANC" OR "uRamaphosa" OR "iBRICS") AND ("uhwebo" OR "utshalo-mali" OR "ubambiswano" OR "ingqalasizinda" OR "ezempi" OR "ukuthula" OR "imfundo" OR "ezempilo"))'
}

# FIXED THE MAIN CHECK
if __name__ == "__main__":
    main()
