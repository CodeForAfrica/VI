import pandas as pd
import time
import os
import logging
import socket
from datetime import date
from sqlalchemy import create_engine, text
import mediacloud.api
import trafilatura
import cloudscraper
import requests

# ────────────────────────────────────────────────
# CONFIG (Updated with your credentials)
# ────────────────────────────────────────────────
DB_USER = os.getenv('DB_USER', 'postgres')
DB_PASSWORD = os.getenv('DB_PASSWORD')
DB_HOST = os.getenv('DB_HOST', 'localhost').strip()
DB_PORT = os.getenv('DB_PORT', '5432')
DB_NAME = os.getenv('DB_NAME', 'postgres')
DB_TABLE = "dashboard_medianarrative"

API_KEY = os.getenv('MEDIACLOUD_API_KEY', '42caaa0601bd290fc5adada8bb804cdfc0604a7a')

# Ensure all columns required by your DB are listed here
db_columns = [
    "article_text", "posting_time", "media_outlet", "inferred_actor",
    "target_country", "url", "lang_detect", "strategic_intent",
    "sector", "tone", "confidence", "use_afrolm", "llm_strat",
    "llm_strat_conf", "llm_strat_notes", "pseudo_kept", "pseudo_weight",
    "llm_strat_id", "strategic_intent_id"
]

logging.basicConfig(
    filename='scraping_log.txt',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Initialize Engine
engine = create_engine(f'postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}', future=True)
mc_search = mediacloud.api.SearchApi(API_KEY)

# Date Range
#START_DATE = date.today() - timedelta(days=1)
START_DATE = date(2026, 3, 25)
END_DATE = date.today()

# ────────────────────────────────────────────────
# COLLECTIONS & QUERIES
# ────────────────────────────────────────────────

ACTOR_COLLECTION_IDS = {
    "USA": 34412234, "France": 34412146, "China": 34412193,
    "Russia": 34412232, "Turkey": 34412131, "Saudi Arabia": 34412050,
    "Israel": 34412391, "Iran": 34412284, "UAE": 34412114,
}

TARGET_COLLECTION_IDS = {
    "Ethiopia":       34412034,
    "Senegal":        38380807,
    "DRC":            34412042,
    "South_Africa":   34412238,      
    "Côte d'Ivoire":   34412173,     
}

QUERY_BY_COUNTRY = {
    "Ethiopia": "(Ethiopia OR 'Addis Ababa' OR 'Abiy Ahmed') AND (investment OR infrastructure OR security OR military OR drone OR diplomacy OR economy OR health)",
    
    "Senegal": "(Sénégal OR Dakar OR 'Bassirou Diomaye Faye' OR 'Ousmane Sonko') AND (investissement OR infrastructure OR sécurité OR militaire OR économie OR diplomatie OR ressources OR health)",
    
    "DRC": "('République Démocratique du Congo' OR RDC OR Kinshasa OR 'Felix Tshisekedi') AND (investissement OR infrastructure OR sécurité OR militaire OR 'matières premières' OR mines OR économie OR diplomatie OR health)",
    
    "South_Africa": "('South Africa' OR Pretoria OR Johannesburg OR 'Cyril Ramaphosa') AND (investment OR infrastructure OR security OR military OR economy OR diplomacy OR energy OR eskom OR health)",
    
    "Côte d'Ivoire": "('Côte d'Ivoire' OR Abidjan OR 'Alassane Ouattara') AND (investissement OR infrastructure OR sécurité OR militaire OR économie OR diplomatie OR cacao OR 'matières premières' OR health)",
}

scraper = cloudscraper.create_scraper()

# ────────────────────────────────────────────────
# HELPERS
# ────────────────────────────────────────────────
def check_collection_health():
    """Diagnostic tool to see if the IDs are returning any data at all."""
    print("\n--- 🏥 Collection Health Check (Total Stories since Jan 1) ---")
    mc = mediacloud.api.SearchApi(API_KEY)
    
    print("Checking Target Countries (National Collections):")
    for name, coll_id in TARGET_COLLECTION_IDS.items():
        try:
            res = mc.story_count(query="*", start_date=START_DATE, end_date=END_DATE, collection_ids=[coll_id])
            print(f"  Target ID {coll_id} ({name}): {res['total']} stories total")
        except Exception as e:
            print(f"  Target ID {coll_id} ({name}): ❌ Error: {str(e)[:30]}")
            
    print("\nChecking Actor Sources (International Collections):")
    for name, coll_id in ACTOR_COLLECTION_IDS.items():
        try:
            res = mc.story_count(query="*", start_date=START_DATE, end_date=END_DATE, collection_ids=[coll_id])
            print(f"  Actor ID {coll_id} ({name}): {res['total']} stories total")
        except Exception as e:
            print(f"  Actor ID {coll_id} ({name}): ❌ Error: {str(e)[:30]}")
    print("-----------------------------------------------------------\n")
    
def url_exists(url):
    query = text(f"SELECT 1 FROM {DB_TABLE} WHERE url = :url LIMIT 1")
    try:
        with engine.connect() as conn:
            return conn.execute(query, {"url": url}).fetchone() is not None
    except:
        return False

def scrape_full_text_robust(url):
    try:
        response = scraper.get(url, timeout=20)
        if response.status_code == 200:
            text_extracted = trafilatura.extract(response.text)
            return text_extracted if text_extracted else "Failed: Empty Content"
        return f"Failed: HTTP {response.status_code}"
    except Exception as e:
        return f"Error: {str(e)}"

def print_progress(current, total, saved, failed):
    percent = int((current / total) * 100)
    bar = '█' * int(25 * current // total) + ' ' * (25 - int(25 * current // total))
    print(f"\rProcessing: {percent:3d}% |{bar}| {current}/{total} (Saved: {saved}, Failed: {failed})", end='', flush=True)

# ────────────────────────────────────────────────
# AUTHOR EXTRACTION HELPER
# ────────────────────────────────────────────────
def extract_author_from_url(url, html_content=None):
    """
    Extract author name from article URL by fetching and parsing HTML.
    Returns author name (str) or None if not found.
    """
    from bs4 import BeautifulSoup
    import json
    import re
    
    try:
        # Fetch HTML if not provided
        if not html_content:
            response = requests.get(url, timeout=15, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            })
            if response.status_code != 200:
                return None
            html_content = response.text
        
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # Strategy 1: Meta tags (most reliable)
        for meta in soup.find_all('meta'):
            if meta.get('name') == 'author' and meta.get('content'):
                name = meta['content'].strip()
                if name and name.lower() not in ['unknown', 'none', 'n/a', 'staff', 'editor', 'by']:
                    return name
            if meta.get('property') == 'article:author' and meta.get('content'):
                name = meta['content'].strip()
                if name and name.lower() not in ['unknown', 'none', 'n/a', 'staff', 'editor', 'by']:
                    return name
        
        # Strategy 2: JSON-LD structured data
        for script in soup.find_all('script', type='application/ld+json'):
            try:
                data = json.loads(script.string)
                # Handle nested structures
                if isinstance(data, list):
                    data = next((item for item in data if isinstance(item, dict)), {})
                if isinstance(data, dict):
                    author = data.get('author')
                    if isinstance(author, dict) and author.get('name'):
                        name = author['name'].strip()
                        if name and name.lower() not in ['unknown', 'none', 'n/a', 'staff', 'editor', 'by']:
                            return name
                    elif isinstance(author, list) and author and isinstance(author[0], dict) and author[0].get('name'):
                        name = author[0]['name'].strip()
                        if name and name.lower() not in ['unknown', 'none', 'n/a', 'staff', 'editor', 'by']:
                            return name
            except:
                continue
        
        # Strategy 3: Common CSS selectors
        selectors = [
            '.byline', '.author-name', '[rel="author"]', 
            '.article-author', '.entry-author', '.post-author',
            'address[itemscope] [itemprop="name"]'
        ]
        for selector in selectors:
            elem = soup.select_one(selector)
            if elem and elem.text.strip():
                name = elem.text.strip()
                # Clean up common prefixes
                name = re.sub(r'^(By|by|BY)\s+', '', name, flags=re.IGNORECASE)
                if name and len(name) > 2 and name.lower() not in ['unknown', 'none', 'n/a', 'staff', 'editor']:
                    return name
        
        # Strategy 4: Look for "By [Name]" pattern in first 500 chars
        text_snippet = soup.get_text()[:500]
        by_match = re.search(r'(?:By|by)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z\.]+)+)', text_snippet)
        if by_match:
            name = by_match.group(1).strip()
            if name and len(name) > 2:
                return name
        
        # No author found - return None (NOT 'Unknown')
        return None
        
    except Exception as e:
        # Log but don't crash ingestion
        logging.debug(f"Author extraction failed for {url}: {e}")
        return None

# ────────────────────────────────────────────────
# MAIN
# ────────────────────────────────────────────────
def main():
    check_collection_health()
    all_records = []
    print(f"🛰️  Querying MediaCloud API...")       
    
    # Use a persistent session to help with headers/stability
    session = requests.Session()
    session.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})

    for target_country, target_coll_id in TARGET_COLLECTION_IDS.items():
        base_query = QUERY_BY_COUNTRY.get(target_country)
        if not base_query: continue
            
        for actor_name, actor_coll_id in ACTOR_COLLECTION_IDS.items():
            stories = None
            retries = 0
            max_retries = 3
            
            while retries <= max_retries:
                try:
                    # Longer delay for problematic actors
                    time.sleep(2.0 if retries == 0 else 10.0)
                    
                    # Re-init search API inside the loop
                    current_mc = mediacloud.api.SearchApi(API_KEY)
                    
                    stories, _ = current_mc.story_list(
                        query=base_query,
                        start_date=START_DATE,
                        end_date=END_DATE,
                        collection_ids=[actor_coll_id]
                    )
                    break 
                    
                except Exception as e:
                    err_msg = str(e)
                    retries += 1
                    
                    if retries <= max_retries:
                        wait_sec = (retries ** 2) * 10
                        print(f"  ⚠️  {actor_name} fail ({err_msg[:25]}). Retry in {wait_sec}s...")
                        time.sleep(wait_sec)
                    else:
                        logging.error(f"MediaCloud Permanent Failure {target_country}-{actor_name}: {e}")
                        print(f"  ❌ Failed {actor_name} after {max_retries} tries.")
                        break
            
            if stories:
                print(f"  ✅ Found {len(stories)} stories for {target_country} ({actor_name})")
                for s in stories:
                    record = {col: None for col in db_columns}
                    record.update({
                        "url": s.get("url"),
                        "posting_time": str(s.get("publish_date")),
                        "media_outlet": s.get("media_name"),
                        "inferred_actor": actor_name,
                        "target_country": target_country,
                        "lang_detect": s.get("language"),
                        "pseudo_kept": True,
                        "pseudo_weight": 1.0,
                        "confidence": 1.0,
                        "use_afrolm": False
                    })
                    all_records.append(record)
                
    df = pd.DataFrame(all_records)
    if df.empty:
        print("\n❌ No articles found.")
        return

    total_attempted = len(df)
    saved_count = 0
    failed_count = 0
    print(f"\n✅ Found {total_attempted} total articles. Starting Scraper...")

    for idx, row in df.iterrows():
        url = row['url']
        if not url or url_exists(url):
            failed_count += 1
            continue
        
        content = scrape_full_text_robust(url)
        if "Failed" not in content and "Error" not in content:
            row_data = row.to_dict()
            row_data['article_text'] = content
            
            if "nytimes.com" in url or row['media_outlet'] == 'The New York Times':
                row_data['inferred_actor'] = 'USA'
            
            try:
                with engine.begin() as conn:
                    final_df = pd.DataFrame([row_data])[db_columns]
                    final_df.to_sql(DB_TABLE, conn, if_exists='append', index=False)
                    saved_count += 1
            except Exception as e:
                logging.error(f"DB Insert Error for {url}: {e}")
                failed_count += 1
        else:
            failed_count += 1

        if idx % 5 == 0 or idx == total_attempted - 1:
            print_progress(idx + 1, total_attempted, saved_count, failed_count)
        
        time.sleep(1.2)

    print(f"\n\n🏁 Done. Saved: {saved_count}, Failed: {failed_count}")

if __name__ == "__main__":
    main()
