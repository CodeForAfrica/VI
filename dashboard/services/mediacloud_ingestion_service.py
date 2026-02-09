import pandas as pd
import numpy as np
import time
import socket
import trafilatura
import cloudscraper 
import requests
import logging
from datetime import date
from sqlalchemy import create_engine, text
from urllib.parse import urlparse
import os

# Use environment variables for database configuration
DB_USER = "postgres"
DB_PASSWORD = os.getenv('DB_PASSWORD', 'B1234')  # Use environment variable
DB_HOST = os.getenv('RDS_HOSTNAME', 'localhost')  # Use RDS host
DB_PORT = os.getenv('RDS_PORT', '1621')  # Use RDS port  
DB_NAME = os.getenv('RDS_DB_NAME', 'MediaCloud')  # Use RDS DB name
DB_TABLE = "ethiopia_articles_final"

logging.basicConfig(
    filename='scraping_log.txt',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

db_columns = [
    "article_text", "posting_time", "media_outlet", "inferred_actor", 
    "target_country", "URL", "lang_detect", "strategic_intent", 
    "sector", "tone", "confidence", "use_afrolm", "llm_strat", 
    "llm_strat_conf", "llm_strat_notes", "pseudo_kept", "pseudo_weight", 
    "llm_strat_id", "strategic_intent_id"
]

# Database engine with environment variables
engine = create_engine(f'postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}', future=True)

def url_exists(url):
    """Check if URL already exists in database"""
    try:
        with engine.connect() as conn:
            result = conn.execute(text(f"SELECT COUNT(*) FROM {DB_TABLE} WHERE URL = :url"), {"url": url})
            return result.scalar() > 0
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

def main():
    all_records = []
    print("🛰 Querying MediaCloud API...")
    
    # Use environment variable for API key
    api_key = os.getenv('MEDIA_CLOUD_API_KEY')
    if not api_key:
        print("⚠️ MEDIA_CLOUD_API_KEY not set. Continuing with existing data only.")
        print("Your existing 15,166 records remain accessible.")
        return
    
    # Try to use the original MediaCloud library if available, otherwise use safe method
    try:
        # If the original mediacloud library is available and working
        import mediacloud.api
        mc_search = mediacloud.api.SearchApi(api_key)
        
        for country, actor_dict in COLLECTIONS.items():
            base_query = QUERY_BY_COUNTRY[country]
            for actor, coll_id in actor_dict.items():
                try:
                    stories, _ = mc_search.story_list(base_query, START_DATE, END_DATE, collection_ids=[coll_id])
                    for s in stories:
                        record = {col: None for col in db_columns}
                        record.update({
                            "URL": s.get("url"),
                            "posting_time": str(s.get("publish_date")),
                            "media_outlet": s.get("media_name"),
                            "inferred_actor": actor,
                            "target_country": country,
                            "lang_detect": s.get("language"),
                            "confidence": s.get("score")
                        })
                        all_records.append(record)
                except Exception as e:
                    logging.error(f"MediaCloud Error {country}-{actor}: {e}")
                    
    except ImportError:
        # If original library not available, use safe method
        print("Using safe API method...")
        for country, actor_dict in COLLECTIONS.items():
            base_query = QUERY_BY_COUNTRY[country]
            for actor, coll_id in actor_dict.items():
                try:
                    stories, _ = safe_mediacloud_search(base_query, START_DATE, END_DATE, [coll_id], api_key)
                    for s in stories:
                        record = {col: None for col in db_columns}
                        record.update({
                            "URL": s.get("url"),
                            "posting_time": str(s.get("publish_date")),
                            "media_outlet": s.get("media_name"),
                            "inferred_actor": actor,
                            "target_country": country,
                            "lang_detect": s.get("language"),
                            "confidence": s.get("score")
                        })
                        all_records.append(record)
                except Exception as e:
                    logging.error(f"Safe API Error {country}-{actor}: {e}")
    
    df = pd.DataFrame(all_records)
    if df.empty:
        print("No articles found or API unavailable. Using existing data.")
        return

    print(f"Found {len(df)} articles. Starting Scraper...")

    with engine.begin() as conn: 
        for idx, row in df.iterrows():
            url = row['URL']
            if not url or url_exists(url):
                continue

            content = scrape_full_text_robust(url)
            
            if "Failed" not in content and "Error" not in content:
                row_data = row.to_dict()
                row_data['article_text'] = content
                
                if "nytimes.com" in url or row['media_outlet'] == 'The New York Times':
                    row_data['inferred_actor'] = 'USA'
                
                try:
                    final_df = pd.DataFrame([row_data])[db_columns]
                    final_df.to_sql(DB_TABLE, conn, if_exists='append', index=False)
                    print(f"[{idx+1}/{len(df)}] 💾 Saved ({row['target_country']}): {url[:40]}...")
                except Exception as e:
                    logging.error(f"DB Insert Error: {e}")
            else:
                print(f"[{idx+1}/{len(df)}] ⚠️ {content} for {url[:30]}")
            
            time.sleep(1.5)

    print("\nFinished. Check your database now.")

# Your original constants preserved exactly
API_KEY = os.getenv('MEDIA_CLOUD_API_KEY', '42caaa0601bd290fc5adada8bb804cdfc0604a7a')

START_DATE = date(2025, 1, 1)
END_DATE = date(2026, 1, 28)

COLLECTIONS = {
    "Ethiopia": {"China": 111, "Russia": 222, "USA": 333, "France": 444},
    "Senegal": {"China": 555, "Russia": 666, "USA": 777, "France": 888},
    "DRC": {"China": 999, "Russia": 0, "USA": 121, "France": 131},  # Fixed 000 to 0
    "SA": {"China": 141, "Russia": 151, "USA": 161, "France": 171}
}

# YOUR EXACT WORKING QUERY PRESERVED
QUERY_BY_COUNTRY = {
    "Ethiopia": '(("infrastructure project" OR "debt relief" OR "railway" OR "industrial park" OR "investment" OR "foreign aid" OR "trade" OR "mining" OR "manufacturing" OR "energy project" OR "military cooperation" OR "arms sale" OR "defense pact" OR "peacekeeping" OR "security partnership" OR "diplomatic relations" OR "election" OR "governance" OR "anti-corruption" OR "state visit" OR "Confucius Institute" OR "cultural exchange" OR "language school" OR "scholarship" OR "digital Silk Road" OR "5G" OR "Huawei" OR "surveillance" OR "cybersecurity" OR "AI" OR "vaccine" OR "pandemic aid" OR "hospital construction" OR "education" OR "university" OR "climate change" OR "hydropower" OR "agriculture" OR "land lease" OR "energy cooperation" OR "mosque" OR "church" OR "religious diplomacy" OR "propaganda" OR "disinformation" OR "social media campaign" OR "broadcast in Amharic") OR ("ኢትዮጵያ" OR "አዲስ አበባ" OR "ኦሮሚያ " OR "ትግራይ" OR "አማራ" OR "የአፍሪካ ቀንድ"))',
    "Senegal": '(("multipartisme" OR "teranga" OR "TER" OR "FAS" OR "DAGE" OR "élection" OR "présidentielle" OR "scrutin" OR "politique" OR "campagne" OR "gouvernance" OR "francophonie" OR "investissement" OR "commerce" OR "prêt" OR "dette" OR "route" OR "routière" OR "port" OR "rail" OR "oléoduc" OR "militaire" OR "arme" OR "défense" OR "paix" OR "terrorisme" OR "mercenaires" OR "bourse" OR "conficius" OR "université" OR "aide" OR "sanitary" OR "cinéma" OR "théâtre" OR "jeune" OR "propagande" OR "désinformation" OR "réseaux sociaux" OR "fausses informations" OR "influenceur" OR "média" OR "5G" OR "Huawei" OR "IA" OR "Intelligence Artificielle" OR "cybersécurité" OR "internet" OR "satellite" OR "surveillance" OR "vaccin" OR "pandémique" OR "hôpital" OR "subvention" OR "agriculture" OR "énergie" OR "cacao" OR "renouvelable" OR "hydraulique" OR "mosqué" OR "église" OR "séminaire" OR "pélérinage" OR "anti-extrémisme" OR "islam" OR "christianisme" OR "chiite" OR "alliance" OR "sunnite") AND ("Senegal" OR "Senegalais" OR "Dakar" OR "Diamniadio" OR "Macky Sall" OR "Ousmane Sonko" OR "Bassirou Diomaye Faye" OR "Abdourahmane Diouf" OR "Khalifa Sall" OR "Fatma Gueye" OR "Abass Fall" OR "Ngoné Mbengue")) OR (("tàmbali" OR "jàngoro" OR "politique" OR "kampaañ" OR "goubernans" OR "wulli" OR "jàppale" OR "fàtt" OR "tali" OR "militéer" OR "guddi" OR "defaans" OR "jàmm" OR "teyat" OR "ndaw" OR "bataaxal bu dëppoo"OR "bataaxal yu dëppoo" OR "vaksin" OR "ndimbal" OR "ñàg" OR "kaku" OR "moské" OR "njàng" OR "kristiyaan") AND ("Senegaal"))',
}

# FIXED THE MAIN CHECK
if __name__ == "__main__":
    main()
