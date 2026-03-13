import pandas as pd
import time
import logging
from datetime import date
from sqlalchemy import create_engine, text
import mediacloud.api
import trafilatura
import cloudscraper
import sys
import os

# ────────────────────────────────────────────────
# CONFIG
# ────────────────────────────────────────────────

# Database configuration using Django settings
DB_USER = os.getenv('DB_USER', 'postgres')
DB_PASSWORD = os.getenv('DB_PASSWORD')
DB_HOST = os.getenv('DB_HOST', 'rds-vulnerabilityindex-euwest-01.cfgmtx8ishfx.eu-west-1.rds.amazonaws.com').strip()
DB_PORT = os.getenv('DB_PORT', '5432')
DB_NAME = os.getenv('DB_NAME', 'postgres')
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
API_KEY = "42caaa0601bd290fc5adada8bb804cdfc0604a7a"
mc_search = mediacloud.api.SearchApi(API_KEY)
START_DATE = date(2026, 1, 1)
END_DATE = date.today()

ACTOR_COLLECTION_IDS = {
    "USA":           34412234,
    "France":        34412146,
    "China":         34412193,
    "Russia":        34412232,
    "Turkey":        34412131,
    "Saudi Arabia": 34412050,
    "Israel":        34412391,
    "Iran":          34412284,
    "UAE":           34412114,
}

TARGET_COLLECTION_IDS = {
    "Ethiopia":       34412034,
    "Senegal":        38380807,
    "DRC":            34412042,
    "South_Africa":   34412238,
    "Côte d'Ivoire":  34412173,
}

QUERY_BY_COUNTRY = {
    # FIX: Used triple quotes (''' ''') for multi-line string literals
    "Ethiopia": '''(
        (Ethiopia OR "Addis Ababa" OR "Abiy Ahmed" OR "GERD" OR "Grand Ethiopian Renaissance Dam" OR "Tigray" OR "Amhara" OR "Oromia")
        AND (
            (narrative* OR "public opinion" OR perception OR "policy shift" OR "state media" OR "foreign influence")
            OR (weaponized OR "information warfare" OR disinformation OR "fake news" OR propaganda OR "media campaign" OR "social media amplification")
            OR (investment OR "infrastructure project" OR "debt relief" OR "foreign aid" OR "security partnership" OR "military cooperation")
            OR (instability OR "ethnic tension" OR "protest" OR "insurgency" OR "border dispute" OR "geopolitical competition")
        )
        AND NOT (sports OR "football results" OR "travel guide" OR "cooking" OR "entertainment news")
    )''',

    "Senegal": '''(
        (Senegal OR Sénégal OR "Bassirou Diomaye Faye" OR "Ousmane Sonko" OR Dakar)
        AND (
            (narrative* OR souveraineté OR "souveraineté économique" OR "sentiment anti-français" OR "anti-French sentiment" OR "perceptions publiques" OR "opinion publique" OR "public opinion")
            OR (weaponized OR manipulation OR disinformation OR "coordonné" OR "fake news" OR propaganda OR "désinformation" OR "influence étrangère" OR "coordonnée" OR "ingérence")
            OR (investment OR "infrastructure project" OR "projets pétroliers" OR "ressources naturelles" OR "investissements directs" OR "dette" OR "prêt" OR debt OR "foreign aid" OR "aide étrangère")
            OR (instability OR instability OR "tensions politiques" OR "manifestations" OR protests OR "terrorisme" OR "sécurité régionale" OR "Sahel" OR "AES")
        )
    )''',

    "South_Africa": '''(
        ("South Africa" OR "Suid-Afrika" OR "Mzansi" OR "Ramaphosa" OR "G20 South Africa" OR "BRICS")
        AND (
            (narrative* OR "GNU" OR "Government of National Unity" OR "coalition" OR "non-aligned" OR "alignment" OR "strategic autonomy" OR "koalisie" OR "nasionale eenheid")
            OR (weaponized OR disinformation OR "deepfake" OR "troll farm" OR "bot network" OR "fopnuus" OR "propaganda" OR "information manipulation" OR "interference")
            OR ("energy crisis" OR "load shedding" OR "Eskom" OR "just energy transition" OR "nuclear deal" OR "Chinese investment" OR "Russian influence" OR "kragkrisis")
            OR ("service delivery protest" OR "xenophobia" OR "social unrest" OR "polarization" OR "stoking" OR "incitement" OR "betoging" OR "mislukking")
        )
    )''',

    "DRC": '''(
        ("Democratic Republic of the Congo" OR "République Démocratique du Congo" OR "RDC" OR "Kinshasa" OR "Tshisekedi")
        AND (
            ("critical minerals" OR "cobalt" OR "lithium" OR "minerais stratégiques" OR "souveraineté minière" OR "Gecamines" OR "contrats chinois" OR "US-DRC partnership" OR "maadini" OR "mumbanda")
            OR (weaponized OR disinformation OR "fake news" OR propaganda OR "désinformation" OR "ingérence" OR "manipulation de l'information" OR "lokuta" OR "habari za uongo")
            OR ("M23" OR "Wazalendo" OR "East" OR "Est" OR "Kivu" OR "Ituri" OR "Goma" OR "security-for-minerals" OR "balkanisation" OR "bitumba" OR "vita")
            OR ("élections" OR "human rights" OR "droits de l'homme" OR "corruption" OR "liberté de la presse" OR "bokonzi" OR "demokrasi")
        )
    )''',

    "Cote_dIvoire": '''(
        ("Côte d\'Ivoire" OR "Ivory Coast" OR "Abidjan" OR "Yamoussoukro" OR "Alassane Ouattara" OR "Adama Bictogo")
        AND (
            ("leadership régional" OR "regional leadership" OR "cacao" OR "cocoa diplomacy" OR "PND 2026" OR "National Development Plan" OR "CFA Franc" OR "Eco" OR "souveraineté monétaire" OR "monetary sovereignty")
            OR (weaponized OR "désinformation" OR disinformation OR "rumors" OR "rumeurs" OR "destabilisation" OR "fake news" OR propaganda OR "cybercriminalité" OR "ingérence étrangère" OR "manipulation")
            OR ("Sahel spillover" OR "Alliance des États du Sahel" OR "AES" OR "Mali border" OR "Burkina Faso border" OR "terrorisme" OR "sécurité frontalière" OR "jihadisme")
            OR ("succession" OR "youth unemployment" OR "chômage des jeunes" OR "cohésion nationale" OR "protestation" OR "manifestation" OR "Gen Z" OR "élections 2025" OR "élections 2026")
        )
    )'''
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
    
def is_article_relevant(article_content, target_country_name):
    """
    Checks if the target country name is mentioned in the article content.
    Performs a simple, case-insensitive substring search.
    """
    if not article_content or not target_country_name:
        return False # Consider empty inputs as irrelevant

    # Simple case-insensitive check
    # You might want to make this more robust (e.g., check for whole words only)
    # using regular expressions if partial matches are an issue.
    return target_country_name.lower() in article_content.lower()
    
def main():
    all_records = []
    print("🛰️ Querying MediaCloud API...")       
    # FIX: Corrected iteration to use TARGET_COLLECTION_IDS and ACTOR_COLLECTION_IDS
    for country, country_coll_id in TARGET_COLLECTION_IDS.items():
        base_query = QUERY_BY_COUNTRY.get(country)
        for actor, actor_coll_id in ACTOR_COLLECTION_IDS.items():
            try:
                time.sleep(0.5) 
                stories, _ = mc_search.story_list(base_query, START_DATE, END_DATE, collection_ids=[actor_coll_id])
                for s in stories:
                    # Initialize record with all required columns set to None
                    record = {col: None for col in db_columns}
                    # Update with fetched data, providing defaults for potentially missing keys
                    # Use the loop variables (country, actor) as the initial target_country and inferred_actor
                    record.update({
                        "url": s.get("url"),
                        "posting_time": str(s.get("publish_date")), # Keep original publish date
                        "media_outlet": s.get("media_name"),
                        "inferred_actor": actor, # Use the actor from the collection loop
                        "target_country": country, # Use the country from the collection loop
                        "lang_detect": s.get("language"),
                        "pseudo_kept": s.get("pseudo_kept", True),
                        "pseudo_weight": s.get("pseudo_weight", 1.0),
                        "use_afrolm": s.get("use_afrolm", False),
                        # Other fields like strategic_intent, tone, confidence, vulnerability_index remain None initially
                    })
                    all_records.append(record)
            except Exception as e:
                logging.error(f"MediaCloud Error {country}-{actor}: {e}")

    df = pd.DataFrame(all_records)
    if df.empty:
        print("❌ No articles found.")
        return

    # --- ADD AUTOMATION SAFEGUARDS ---
    MAX_ARTICLES_PER_RUN = 200
    MAX_RUNTIME_SECONDS = 800  # Example limit, adjust as needed
    total_found = len(df) # Store the original count before capping
    df = df.head(MAX_ARTICLES_PER_RUN)  # Cap the number of articles processed
    total_attempted = len(df) # Recalculate based on the capped dataframe
    print(f"✅ Found {total_found} articles (processing up to {MAX_ARTICLES_PER_RUN}). Starting Scraper...")
    # --- END ADD AUTOMATION SAFEGUARDS ---

    # --- ADD TIME BUDGET CHECK ---
    loop_start = time.time()
    saved_count = 0
    failed_count = 0  # Initialize counter for the capped run loop
    # --- END ADD TIME BUDGET CHECK ---

    for idx, row in df.iterrows():
        # --- CHECK TIME BUDGET INSIDE LOOP ---
        if time.time() - loop_start > MAX_RUNTIME_SECONDS:
            print(f"\n⏰ Time budget ({MAX_RUNTIME_SECONDS}s) reached at article {idx}. Stopping to avoid timeout.")
            break  # Exit the loop gracefully
        # --- END CHECK TIME BUDGET INSIDE LOOP ---

        url = row['url']
        if not url or not isinstance(url, str) or url_exists(url):
            failed_count += 1
            continue # Increment failed counter for skipped URLs / duplicates

        content = scrape_full_text_robust(url)

        # --- CHECK CONTENT QUALITY (Basic)  ---
        is_not_error = not content.startswith("Failed:") and not content.startswith("Error:")
        has_content = len(content) > 1000 # Increased minimum length check
        # --- END CHECK CONTENT QUALITY ---

        if is_not_error and has_content:
            # --- ADD RELEVANCE CHECK HERE ---
            # Extract the target country from the row (as determined by the MediaCloud query loop)
            target_country_from_query = row['target_country'] # Use the country assigned from the loop

            # Perform the relevance check: is the target country mentioned in the scraped content?
            if not is_article_relevant(content, target_country_from_query):
                print(f"[{idx+1}/{len(df)}] 🚫 Irrelevant Article: Skipping {url[:40]}... (Target: {target_country_from_query}, not found in text)")
                failed_count += 1 # Consider this a "failure" to meet the relevance criterion
                continue # Skip saving this article to the database
            else:
                print(f"[{idx+1}/{len(df)}] ✅ Relevant Article: Processing {url[:40]}... (Target: {target_country_from_query})")
            # --- END ADD RELEVANCE CHECK ---

            # Prepare row_data for database insertion
            row_data = row.to_dict()
            row_data['article_text'] = content # Add the scraped text

            # Potentially override inferred_actor based on media outlet name (simple rule)
            # This is a basic check; more complex logic could be added later if needed.
            if "nytimes.com" in url or row_data.get('media_outlet') == 'The New York Times':
                row_data['inferred_actor'] = 'USA'

            # DO NOT set defaults for strategic_intent, tone, confidence, vulnerability_index here
            # They should remain NULL/None for the ML pipeline to process later
            # The target_country and inferred_actor come from the initial fetch/query loop or simple overrides.
            # The article_text comes from scraping.

            try:
                # Ensure all required columns are present and have acceptable values for the database schema
                # This step relies on the DataFrame constructor and to_sql's handling based on db_columns.
                # It assumes the database table 'dashboard_medianarrative' can accept NULLs for fields
                # like strategic_intent, tone, etc., initially.

                # The primary check for saving is now just the relevance check (done above)
                # and basic content quality (done earlier).
                # We assume 'url', 'posting_time', 'media_outlet', 'target_country', 'inferred_actor', 'article_text'
                # are the minimal required fields, and they are populated by this point if the code reaches here.

                final_df = pd.DataFrame([row_data])[db_columns] # Create DF with correct schema
                with engine.begin() as conn:
                    final_df.to_sql(DB_TABLE, conn, if_exists='append', index=False) # Save to DB
                saved_count += 1 # Increment saved counter IF the save succeeds
                print(f"[{idx+1}/{len(df)}] Saved ({row_data['target_country']} | {row_data['inferred_actor']}): {url[:40]}...") # Log successful save
            except Exception as e:
                logging.error(f"DB Insert Error for {url}: {e}") # Log detailed error to file
                print(f"[{idx+1}/{len(df)}] ❌ DB Insert Error for {url[:40]}...: {e}") # Log brief error to console
                failed_count += 1 # Increment failed counter for DB errors
        else:
            failed_count += 1 # Increment failed counter for scraping errors/low content
            print(f"[{idx+1}/{len(df)}] ❌ Real Failure: {content[:50]}... for {str(url)[:30]}")

        # --- ADJUST PROGRESS PRINTING FOR CAPPED LOOP ---
        # Use the capped total_attempted and current counters, print every 5 or at the end
        if idx % 5 == 0 or idx == total_attempted - 1: # CORRECTED LINE: Added ':'
            print_progress(idx + 1, total_attempted, saved_count, failed_count)
        # --- END ADJUST PROGRESS PRINTING ---

        time.sleep(0.5) # Respectful delay

    print(f"\n\n🏁 Finished. Attempted: {total_attempted}, Saved: {saved_count}, Failed/Timed-out: {failed_count}")

if __name__ == "__main__":
    main()
