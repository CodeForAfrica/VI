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

MEDIACLOUD_API_KEY = os.getenv("MEDIACLOUD_API_KEY", "42caaa0601bd290fc5adada8bb804cdfc0604a7a")
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
    "Ethiopia": '(("infrastructure project" OR "debt relief" OR "railway" OR "industrial park" OR "investment" OR "foreign aid" OR "trade" OR "mining" OR "manufacturing" OR "energy project" OR "military cooperation" OR "arms sale" OR "defense pact" OR "peacekeeping" OR "security partnership" OR "diplomatic relations" OR "election" OR "governance" OR "anti-corruption" OR "state visit" OR "Confucius Institute" OR "cultural exchange" OR "language school" OR "scholarship" OR "digital Silk Road" OR "5G" OR "Huawei" OR "surveillance" OR "cybersecurity" OR "AI" OR "vaccine" OR "pandemic aid" OR "hospital construction" OR "education" OR "university" OR "climate change" OR "hydropower" OR "agriculture" OR "land lease" OR "energy cooperation" OR "mosque" OR "church" OR "religious diplomacy" OR "propaganda" OR "disinformation" OR "social media campaign" OR "broadcast in Amharic") OR ("ኢትዮጵያ" OR "አዲስ አበባ" OR "ኦሮሚያ " OR "ትግራይ" OR "አማራ" OR "የአፍሪካ ቀንድ"))',
    "Senegal": '(("multipartisme" OR "teranga" OR "TER" OR "FAS" OR "DAGE" OR "élection" OR "présidentielle" OR "scrutin" OR "politique" OR "campagne" OR "gouvernance" OR "francophonie" OR "investissement" OR "commerce" OR "prêt" OR "dette" OR "route" OR "routière" OR "port" OR "rail" OR "oléoduc" OR "militaire" OR "arme" OR "défense" OR "paix" OR "terrorisme" OR "mercenaires" OR "bourse" OR "conficius" OR "université" OR "aide" OR "sanitaire" OR "cinéma" OR "théâtre" OR "jeune" OR "propagande" OR "désinformation" OR "réseaux sociaux" OR "fausses informations" OR "influenceur" OR "média" OR "5G" OR "Huawei" OR "IA" OR "Intelligence Artificielle" OR "cybersécurité" OR "internet" OR "satellite" OR "surveillance" OR "vaccin" OR "pandémique" OR "hôpital" OR "subvention" OR "agriculture" OR "énergie" OR "cacao" OR "renouvelable" OR "hydraulique" OR "mosqué" OR "église" OR "séminaire" OR "pélérinage" OR "anti-extrémisme" OR "islam" OR "christianisme" OR "chiite" OR "alliance" OR "sunnite") AND ("Senegal" OR "Senegalais" OR "Dakar" OR "Diamniadio" OR "Macky Sall" OR "Ousmane Sonko" OR "Bassirou Diomaye Faye" OR "Abdourahmane Diouf" OR "Khalifa Sall" OR "Fatma Gueye" OR "Abass Fall" OR "Ngoné Mbengue")) OR (("tàmbali" OR "jàngoro" OR "politique" OR "kampaañ" OR "goubernans" OR "wulli" OR "jàppale" OR "fàtt" OR "tali" OR "militéer" OR "guddi" OR "defaans" OR "jàmm" OR "teyat" OR "ndaw" OR "bataaxal bu dëppoo"OR "bataaxal yu dëppoo" OR "vaksin" OR "ndimbal" OR "ñàg" OR "kaku" OR "moské" OR "njàng" OR "kristiyaan") AND ("Senegaal"))',
    "DRC": '(("élection" OR "présidentielle" OR "scrutin" OR "politique" OR "campagne" OR "gouvernance" OR "francophonie" OR "investissement" OR "commerce" OR "prêt" OR "dette" OR "route" OR "routière" OR "port" OR "rail" OR "oléoduc" OR "militaire" OR "arme" OR "défense" OR "paix" OR "terrorisme" OR "mercenaires" OR "bourse" OR "conficius" OR "université" OR "aide" OR "sanitaire" OR "cinéma" OR "théâtre" OR "jeune" OR "propagande" OR "désinformation" OR "réseaux sociaux" OR "fausses informations" OR "influenceur" OR "média" OR "5G" OR "Huawei" OR "IA" OR "Intelligence Artificielle" OR "cybersécurité" OR "internet" OR "satellite" OR "surveillance" OR "vaccin" OR "pandémique" OR "hôpital" OR "subvention" OR "agriculture" OR "énergie" OR "cacao" OR "renouvelable" OR "hydraulique" OR "mosqué" OR "église" OR "séminaire" OR "pélérinage" OR "anti-extrémisme" OR "islam" OR "christianisme" OR "chiite" OR "alliance" OR "sunnite") AND ("République démocratique du Congo" OR "RDC" OR "Kinshasa" OR "Congolais" OR "Kisangani" OR "Lubumbashi" OR "Kolwezi" OR "Kivu" OR "Kokolo" OR "Goma" OR "Corneille Nnanga" OR "Bertrand Bisimwa" OR "Sultani Makenga" OR "Willy Ngoma" OR "Lawrence Kanyuka" OR "Jean-Jacques Mamba" OR "Éric Nkuba" OR "Joseph Kabila" OR "Félix Tshisekedi")) OR (("bobongisi maponami" OR "maponami" OR "politiki" OR "kampanyi" OR "boyangeli" OR "mbongo na mosala" OR "libaku ya mbongo" OR "nzela" OR "ya nzela" OR "mibundu" OR "liboke ya bitumba" OR "bokengi" OR "kimia" OR "banyama ya liboma" OR "lisungi" OR "ya bokolongono" OR "elenga" OR "nsango ya lokuta" OR "nsango ya lokuta" OR "influenceur" OR "media" OR "5G" OR "Huawei" OR "IA" OR "vaksin" OR "lopitalo" OR "bilanga" OR "kura" OR "misiri" OR "ndako ya Nzambe" OR "kristoya") AND ("RDC"))',
    "SA": '(("South Africa" OR "Pretoria" OR "Johannesburg" OR "Cape Town" OR "Durban" OR "ANC" OR "Ramaphosa" OR "BRICS") AND ("trade" OR "investment" OR "economic cooperation" OR "mining" OR "energy" OR "infrastructure" OR "military" OR "defense" OR "peace" OR "terrorism" OR "propaganda" OR "disinformation" OR "5G" OR "Huawei" OR "AI" OR "vaccine")) OR (("iNingizimu Afrika" OR "iPitoli" OR "iKapa" OR "iGoli" OR "iTheku" OR "iANC" OR "uRamaphosa" OR "iBRICS") AND ("uhwebo" OR "utshalo-mali" OR "ubambiswano" OR "ingqalasizinda" OR "ezempi" OR "ukuthula" OR "imfundo" OR "ezempilo"))'
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

if __name__ == "__main__":
    main()
