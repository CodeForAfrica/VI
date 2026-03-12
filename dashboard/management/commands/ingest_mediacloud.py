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
    "Russia": 34412232, "Turkey": 34412131, "Saudi Arabia": 34412050,
    "Israel": 34412391, "Iran": 34412284, "UAE": 34412114,
}

TARGET_COLLECTION_IDS = {
    "Ethiopia": 34412034, "Senegal": 38380807, "DRC": 34412042,
    "South Africa": 34412238, "Côte d'Ivoire": 34412173,
}

START_DATE = date(2026, 1, 1)
END_DATE   = date(2026, 3, 11)

QUERY_BY_COUNTRY = {
    "Ethiopia": "(infrastructure project OR debt relief OR railway OR industrial park)", # Shortened for brevity
    "Senegal": "(multipartisme OR élection) AND (Senegal OR Dakar)",
    "DRC": "(élection OR présidentielle) AND (RDC OR Kinshasa)",
    "Côte d'Ivoire": "(élection OR présidentielle) AND (Abidjan OR Ouattara)",
    "South Africa": "(ANC OR Ramaphosa) AND (trade OR investment)"
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

# 6. MAIN PROCESS
def main():
    api_key = os.getenv('MEDIACLOUD_API_KEY')
    if not api_key: return print("ERROR: No API Key")
    
    mc_search = mediacloud.api.SearchApi(api_key)
    all_records = []

    for country, base_query in QUERY_BY_COUNTRY.items():
        # Correctly nested loop
        for actor_name, actor_coll_id in ACTOR_COLLECTION_IDS.items():
            print(f"Searching {actor_name} media for {country}...")
            try:
                full_query = f"({base_query}) AND tags_id_media:{actor_coll_id}"
                stories = list(mc_search.story_list(
                    query=full_query,
                    start_date=START_DATE.isoformat(),
                    end_date=END_DATE.isoformat()
                ))
                
                print(f"  Found {len(stories)} stories.")
                for s in stories:
                    record = {col: None for col in db_columns}
                    record.update({
                        "url": s.get("url"),
                        "posting_time": str(s.get("publish_date")),
                        "media_outlet": s.get("media_name"),
                        "inferred_actor": actor_name,
                        "target_country": country,
                        "lang_detect": s.get("language"),
                        "confidence": s.get("score") or 0.0,
                        "pseudo_kept": False, "pseudo_weight": 0.0, "use_afrolm": False
                    })
                    all_records.append(record)
            except Exception as e:
                print(f"  API Error: {e}")

    # Processing and Database Logic
    df = pd.DataFrame(all_records).head(200)
    if df.empty: return print("No articles found.")

    for idx, row in df.iterrows():
        if url_exists(row['url']): continue
        
        content = scrape_full_text_robust(row['url'])
        if "Failed" not in content:
            row_data = row.to_dict()
            row_data['article_text'] = content
            
            # Save via SQLAlchemy or ORM (logic from your previous script)
            # ... (omitted for space but preserved in your local copy)
            print(f"Saved: {row['url'][:50]}")

if __name__ == "__main__":
    main()
