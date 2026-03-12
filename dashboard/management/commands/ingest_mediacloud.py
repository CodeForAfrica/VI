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
START_DATE = date(2026, 1, 1)
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
    "Ethiopia": 34412034, "Senegal": 38380807, "DRC": 34412042,
    "South_Africa": 34412238, "Côte d'Ivoire": 34412173,
}

QUERY_BY_COUNTRY = {
    "Ethiopia": "(Ethiopia OR 'Addis Ababa' OR 'Abiy Ahmed') AND (investment OR infrastructure OR security OR military OR trade OR deplomacy OR culture OR drone)",
    "Senegal": "(Senegal OR Sénégal OR Dakar) AND (élection OR politique OR investissement OR trade OR military OR deplomacy OR infrastructure OR culture)",
    # ... keep others simple for the first test
}

scraper = cloudscraper.create_scraper()

# ────────────────────────────────────────────────
# HELPERS
# ────────────────────────────────────────────────
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
# MAIN
# ────────────────────────────────────────────────
def main():
    all_records = []
    print(f"🛰️  Querying MediaCloud API from {START_DATE} to {END_DATE}...")       
    
    for country, country_coll_id in TARGET_COLLECTION_IDS.items():
        base_query = QUERY_BY_COUNTRY.get(country)
        if not base_query: continue
            
        for actor, actor_coll_id in ACTOR_COLLECTION_IDS.items():
            try:
                # Bulletproof Date handling
                s_date = START_DATE.isoformat() if hasattr(START_DATE, 'isoformat') else START_DATE
                e_date = END_DATE.isoformat() if hasattr(END_DATE, 'isoformat') else END_DATE
                
                # Fetch stories
                stories, _ = mc_search.story_list(base_query, s_date, e_date, collection_ids=[actor_coll_id])
                
                for s in stories:
                    record = {col: None for col in db_columns}
                    record.update({
                        "url": s.get("url"),
                        "posting_time": str(s.get("publish_date")),
                        "media_outlet": s.get("media_name"),
                        "inferred_actor": actor,
                        "target_country": country,
                        "lang_detect": s.get("language"),
                        "pseudo_kept": True,
                        "pseudo_weight": 1.0,
                        "use_afrolm": False,
                        "confidence": 1.0
                    })
                    all_records.append(record)
                time.sleep(0.5) 
            except Exception as e:
                logging.error(f"MediaCloud Error {country}-{actor}: {e}")

    df = pd.DataFrame(all_records)
    if df.empty:
        print("\n❌ No articles found.")
        return

    total_attempted = len(df)
    saved_count = 0
    failed_count = 0
    print(f"\n✅ Found {total_attempted} articles. Starting Scraper...")

    for idx, row in df.iterrows():
        url = row['url']
        if not url or url_exists(url):
            failed_count += 1
            continue
        
        content = scrape_full_text_robust(url)
        if "Failed" not in content and "Error" not in content:
            row_data = row.to_dict()
            row_data['article_text'] = content
            
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

        print_progress(idx + 1, total_attempted, saved_count, failed_count)
        time.sleep(1.2)

    print(f"\n\n🏁 Finished. Saved: {saved_count}, Failed: {failed_count}")

if __name__ == "__main__":
    main()
