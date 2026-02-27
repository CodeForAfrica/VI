import pandas as pd
import time
import logging
from datetime import date
from sqlalchemy import create_engine, text
import mediacloud.api
import trafilatura
import cloudscraper

# ────────────────────────────────────────────────
# CONFIG
# ────────────────────────────────────────────────
DB_USER = "postgres"
DB_PASSWORD = "cLUgQCdGpBmUiJcjFz"
DB_HOST = "rds-vulnerabilityindex-euwest-01.cfgmtx8ishfx.eu-west-1.rds.amazonaws.com"
DB_PORT = "5432"
DB_NAME = "postgres"
DB_TABLE = "dashboard_medianarrative"

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

engine = create_engine(f'postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}', future=True)
# Pull from environment to match settings.py and mediacloud_ingestion_service.py

API_KEY = os.getenv("MEDIACLOUD_API_KEY", "42caaa0601bd290fc5adada8bb804cdfc0604a7a")
mc_search = mediacloud.api.SearchApi(MEDIACLOUD_API_KEY)
START_DATE = date(2025, 1, 1)
END_DATE = date.today()

# Keep your original COLLECTIONS and QUERY_BY_COUNTRY...
COLLECTIONS = {
    "Ethiopia": {"China": 111, "Russia": 222, "USA": 333, "France": 444},
    "Senegal": {"China": 555, "Russia": 666, "USA": 777, "France": 888},
    "DRC": {"China": 999, "Russia": 000, "USA": 121, "France": 131},
    "SA": {"China": 141, "Russia": 151, "USA": 161, "France": 171}
}

QUERY_BY_COUNTRY = {
    "Ethiopia": '''
        (Ethiopia OR "Addis Ababa" OR Amhara OR Oromia OR Tigray) 
        AND 
        (China OR Chinese OR Russia OR Russian OR USA OR American OR France OR French 
         OR Saudi OR Turkish OR Turkey OR UAE OR Israel OR Israeli OR Iran OR Iranian 
         OR Rwanda OR Rwandan)
        AND NOT (Olympics OR "World Cup" OR "football" OR "soccer" OR "Premier League" 
                 OR "Champions League" OR "tennis" OR "marathon" OR "athletics")
    ''',
    "Senegal": '''
        (Senegal OR Dakar OR "Macky Sall" OR "Bassirou Diomaye Faye" OR "Ousmane Sonko") 
        AND 
        (China OR Chinese OR Russia OR Russian OR USA OR American OR France OR French 
         OR Saudi OR Turkish OR Turkey OR UAE OR Israel OR Israeli OR Iran OR Iranian 
         OR Rwanda OR Rwandan)
        AND NOT (Olympics OR "World Cup" OR "football" OR "soccer" OR "Premier League" 
                 OR "Champions League" OR "tennis" OR "marathon" OR "athletics")
    ''',
    "DRC": '''
        (DRC OR "Democratic Republic of Congo" OR Kinshasa OR Congo OR Kivu) 
        AND 
        (China OR Chinese OR Russia OR Russian OR USA OR American OR France OR French 
         OR Saudi OR Turkish OR Turkey OR UAE OR Israel OR Israeli OR Iran OR Iranian 
         OR Rwanda OR Rwandan)
        AND NOT (Olympics OR "World Cup" OR "football" OR "soccer" OR "Premier League" 
                 OR "Champions League" OR "tennis" OR "marathon" OR "athletics")
    ''',
    "CoteIvoire": '''
        (CoteIvoire OR "Ivory Coast" OR Abidjan OR "Yamoussoukro" OR "Alassane Ouattara") 
        AND 
        (China OR Chinese OR Russia OR Russian OR USA OR American OR France OR French 
         OR Saudi OR Turkish OR Turkey OR UAE OR Israel OR Israeli OR Iran OR Iranian 
         OR Rwanda OR Rwandan)
        AND NOT (Olympics OR "World Cup" OR "football" OR "soccer" OR "Premier League" 
                 OR "Champions League" OR "tennis" OR "marathon" OR "athletics")
    ''',
    "SouthAfrica": '''
        (SouthAfrica OR "South Africa" OR Pretoria OR Johannesburg OR "Cape Town" OR Ramaphosa OR ANC) 
        AND 
        (China OR Chinese OR Russia OR Russian OR USA OR American OR France OR French 
         OR Saudi OR Turkish OR Turkey OR UAE OR Israel OR Israeli OR Iran OR Iranian 
         OR Rwanda OR Rwandan)
        AND NOT (Olympics OR "World Cup" OR "football" OR "soccer" OR "Premier League" 
                 OR "Champions League" OR "tennis" OR "marathon" OR "athletics")
    '''
}

scraper = cloudscraper.create_scraper()

def url_exists(url):
    query = text(f"SELECT 1 FROM {DB_TABLE} WHERE url = :url LIMIT 1")
    try:
        with engine.connect() as conn:
            return conn.execute(query, {"url": url}).fetchone() is not None
    except Exception as e:
        return False

def scrape_full_text_robust(url):
    for attempt in range(2):
        try:
            response = scraper.get(url, timeout=20)
            if response.status_code == 200:
                text_extracted = trafilatura.extract(response.text)
                return text_extracted if text_extracted else "Failed: Empty Content"
            return f"Failed: HTTP {response.status_code}"
        except Exception as e:
            if attempt < 1:
                time.sleep(3)
                continue
            return f"Error: {str(e)}"

def print_progress(current, total, saved, failed):
    percent = int((current / total) * 100)
    bar_length = 25
    filled = int(bar_length * current // total)
    bar = '█' * filled + ' ' * (bar_length - filled)
    print(f"\rProcessing: {percent:3d}% |{bar}| {current}/{total} (Saved: {saved}, Failed: {failed})", end='', flush=True)

def main():
    all_records = []
    print("🛰️ Querying MediaCloud API...")       
    for country, actor_dict in COLLECTIONS.items():
        base_query = QUERY_BY_COUNTRY.get(country)
        for actor, coll_id in actor_dict.items():
            try:
                # Add a tiny sleep to avoid slamming the API
                time.sleep(0.5) 
                stories, _ = mc_search.story_list(base_query, START_DATE, END_DATE, collection_ids=[coll_id])
                for s in stories:
                    # FIX: Provide defaults for NOT NULL columns (pseudo_kept, etc.)
                    record = {col: None for col in db_columns}
                    record.update({
                        "url": s.get("url"),
                        "posting_time": str(s.get("publish_date")),
                        "media_outlet": s.get("media_name"),
                        "inferred_actor": actor,
                        "target_country": country,
                        "lang_detect": s.get("language"),
                        "pseudo_kept": True,      # SET DEFAULT VALUE FOR NOT-NULL CONSTRAINT
                        "pseudo_weight": 1.0,     # SET DEFAULT VALUE FOR NOT-NULL CONSTRAINT
                        "use_afrolm": False       # SET DEFAULT VALUE FOR NOT-NULL CONSTRAINT
                    })
                    all_records.append(record)
            except Exception as e:
                logging.error(f"MediaCloud Error {country}-{actor}: {e}")

    df = pd.DataFrame(all_records)
    if df.empty:
        print("❌ No articles found.")
        return

    total_attempted = len(df)
    saved_count = 0
    failed_count = 0
    print(f"✅ Found {total_attempted} articles. Starting Scraper...")

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
            
            # FIX: Use engine.begin() INSIDE the loop for individual commits
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

        if idx % 5 == 0 or idx == total_attempted:
            print_progress(idx + 1, total_attempted, saved_count, failed_count)
        
        time.sleep(3.0)

    print(f"\n\n🏁 Finished. Saved: {saved_count}, Failed: {failed_count}")

def lambda_handler(event, context):
    """
    AWS Lambda starts here.
    """
    # 1. Pull the keys you standardized
    os.environ['MEDIACLOUD_API_KEY'] = os.environ.get('MEDIACLOUD_API_KEY')
    
    # 2. Execute the ingestion
    try:
        main() 
        return {
            'statusCode': 200,
            'body': 'Daily ingestion completed successfully'
        }
    except Exception as e:
        return {
            'statusCode': 500,
            'body': f'Error during ingestion: {str(e)}'
        }

if __name__ == "__main__":
    main()

