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
                # Example: Query for articles matching base_query within actor's media collection
                # on your *exact* MediaCloud collection setup.
                # If base_query is meant to search within the TARGET country's collection, you might need:
                # full_query = f"({base_query})"  # Search in the target country's collection (if applicable)
                # And then specify the source collection differently, maybe via collection_ids parameter below.
                # If base_query is meant to search within the ACTOR's media collection, the tags_id_media might be correct.
                # Clarification on MediaCloud collection usage is key here.
                # Assuming for now, the query targets the actor's media:
                full_query = f"({base_query})"
                
                # CORRECTED: Pass date objects directly, not strings from isoformat()
                response = mc_search.story_list(
                    query=full_query,
                    start_date=START_DATE, # Pass date object
                    end_date=END_DATE,     # Pass date object
                    collection_ids=[actor_coll_id] # Specify the actor's media collection
                    # Add other relevant parameters like limit if needed
                )
                
                # Handle the response - it might be a generator or a paginated result
                # The exact method depends on the version of the mediacloud library
                # Assuming it returns an iterable object like a list or generator
                stories = list(response) # Convert to list to get count
                
                print(f"  Found {len(stories)} stories in {actor_name} media for {country}.")
                for s in stories:
                    record = {col: None for col in db_columns}
                    record.update({
                        "url": s.get("url"),
                        "posting_time": str(s.get("publish_date")), # Convert datetime to string if DB expects string
                        "media_outlet": s.get("media_name"),
                        "inferred_actor": actor_name,
                        "target_country": country,
                        "lang_detect": s.get("language"),
                        "confidence": s.get("score") or 0.0, # Handle potential None
                        "pseudo_kept": False, 
                        "pseudo_weight": 0.0, 
                        "use_afrolm": False
                    })
                    all_records.append(record)
            except Exception as e:
                print(f"  API Error for {actor_name} searching for {country}: {e}")

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
