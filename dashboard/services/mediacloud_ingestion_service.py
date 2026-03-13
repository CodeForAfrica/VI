import pandas as pd
import time
import logging
import socket # Need this for verify_dns
from datetime import date, timedelta # Import timedelta
from sqlalchemy import create_engine, text
import mediacloud.api
import trafilatura
import cloudscraper
import sys
import os
import django # Need this for cache clearing
from django.conf import settings # Need this for cache clearing
from django.core.cache import cache # Need this for cache clearing


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
    "llmm_strat_notes", "pseudo_kept", "pseudo_weight",
    "llm_strat_id", "strategic_intent_id"
]

logging.basicConfig(
    filename='scraping_log.txt',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

engine = create_engine(f'postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}', future=True)
API_KEY = os.getenv('MEDIACLOUD_API_KEY') # Use environment variable
if not API_KEY:
    print("ERROR: MEDIACLOUD_API_KEY environment variable not set.")
    sys.exit(1) # Exit if no key

mc_search = mediacloud.api.SearchApi(API_KEY)


# START_DATE = date(2026, 1, 1) # use for specific dates
START_DATE = date.today() - timedelta(days=1) # Yesterday


END_DATE = date.today() # Use date object consistently

ACTOR_COLLECTION_IDS = {
    "USA":           34412234,
    "France":        34412146,
    "China":         34412193,
    "Russia":        34412232,
    "Turkey":        34412131,
    "Saudi Arabia":  34412050,
    "Israel":        34412391,
    "Iran":          34412284,
    "UAE":           34412114,
}

# Note: TARGET_COLLECTION_IDS is defined here but not used in the fetching loop below.
# The loop iterates TARGET_COLLECTION_IDS.keys() to get country names for queries,
# and uses ACTOR_COLLECTION_IDS.values() to search *within* actor collections.
TARGET_COLLECTION_IDS = {
    "Ethiopia":       34412034,
    "Senegal":        38380807,
    "DRC":            34412042,
    "SA":             34412238,
    "Côte d'Ivoire":  34412173,
}

# --- QUERY_BY_COUNTRY: Combining Structure with Comprehensive Terms ---
QUERY_BY_COUNTRY = {
    "Ethiopia": '''(
        ("Ethiopia" OR "ኢትዮጵያ" OR "አዲስ አበባ" OR "ኦሮሚያ" OR "ትግራይ" OR "አማራ" OR "የአፍሪካ ቀንድ" OR "Addis Ababa" OR "Abiy Ahmed" OR "GERD" OR "Grand Ethiopian Renaissance Dam" OR "Tigray" OR "Amhara" OR "Oromia")
        AND (
            ("narrative*" OR "public opinion" OR " "policy shift" OR "state media" OR "foreign influence")
            OR ("weaponized" OR "information warfare" OR "disinformation" OR "fake news" OR "propaganda" OR "media campaign" OR "social media amplification" OR "broadcast in Amharic")
            OR ("investment" OR "infrastructure project" OR "debt relief" OR "foreign aid" OR "trade" OR "mining" OR "manufacturing" OR "energy project" OR "military cooperation" OR "arms sale" OR "defense pact" OR "peacekeeping" OR "security partnership" OR "diplomatic relations" OR "election" OR "governance" OR "anti-corruption" OR "state visit" OR "Confucius Institute" OR "cultural exchange" OR "language school" OR "scholarship" OR "digital Silk Road" OR "5G" OR "Huawei" OR "surveillance" OR "cybersecurity" OR "AI" OR "vaccine" OR "pandemic aid" OR "hospital construction" OR "education" OR "university" OR "climate change" OR "hydropower" OR "agriculture" OR "land lease" OR "energy cooperation" OR "mosque" OR "church" OR "religious coopration")
            OR ("instability" OR "ethnic tension" OR "protest" OR "insurgency" ORgeopolitical competition")
        )
        AND NOT ("sports" OR "football results" OR "travel guide" OR "cooking" OR "entertainment news")
    )''',

    "Senegal": '''(
        ("Senegal" OR "Sénégal" OR "Dakar" OR "Macky Sall" OR "Ousmane Sonko" OR "Bassirou Diomaye Faye" OR "Abdourahmane Diouf" OR "Khalifa Sall" OR "Fatma Gueye" OR "Abass Fall" OR "Ngoné Mbengue" OR "tàmbali" OR "jàngoro" OR "kampaañ" OR "goubernans" OR "wulli" OR "jàppale" OR "fàtt" OR "tali" OR "militéer" OR "guddi" OR "defaans" OR "jàmm" OR "teyat" OR "ndaw" OR "bataaxal bu dëppoo" OR "bataaxal yu dëppoo" OR "vaksin" OR "ndimbal" OR "ñàg" OR "kaku" OR "moské" OR "njàng" OR "kristiyaan")
        AND (
            ("narrative*" OR "souveraineté" OR "souveraineté économique" OR "sentiment anti-français" OR sentiment" OR "perceptions publiques" OR "opinion publique" OR "public opinion")
            OR ("weaponized" OR "manipulation" OR "disinformation" OR "coordonné" OR "fake news" OR "propaganda" OR "désinformation" OR "influence étrangère" OR "coordonnée" OR "ingérence" OR "multipartisme" OR "teranga" OR "TER" OR "FAS" OR "DAGE" OR "élection" OR "présidentielle" OR "scrutin" OR "politique" OR "campagne" OR "gouvernance" OR "francophonie" OR "conficius" OR "université" OR "sanitaire" OR "cinéma" OR "théâtre" OR "jeune" OR "réseaux sociaux" OR "fausses informations" OR "influenceur" OR "média" OR "IA" OR "Intelligence Artificielle" OR "cybersécurité" OR "internet" OR "satellite" OR "surveillance" OR "vaccin" OR "pandémique" OR "hôpital" OR "subvention" OR "renouvelable" OR "hydraulique" OR "mosqué" OR "église" OR "séminaire" OR "pélérinage" OR "anti-extrémisme" OR "islam" OR "christianisme" OR "chiite" OR "alliance" OR "sunnite")
            OR ("investment" OR "infrastructure project" OR "projets pétroliers" OR "ressources naturelles" OR "investissements directs" OR "dette" OR "prêt" OR "debt" OR "foreign aid" OR "aide étrangère" OR "commerce" OR "route" OR "routière" OR "port" OR "rail" OR "oléoduc" OR "militaire" OR "arme" OR "paix" OR "terrorisme" OR "mercenaires" OR "bourse" OR "aide" OR "cacao" OR "énergie" OR "agriculture")
            OR ("instability" OR "tensions politiques" OR "manifestations" OR "protests" OR "terrorisme" OR "sécurité régionale" OR "Sahel" OR "AES")
        )
    )''',

    "SA": '''( # Using 'SA' key to match your query definition
        ("South Africa" OR "Suid-Afrika" OR "Mzansi" OR "Pretoria" OR "Johannesburg" OR "Cape Town" OR "Durban" OR "ANC" OR "Ramaphosa" OR "BRICS" OR "iNingizimu Afrika" OR "iPitoli" OR "iKapa" OR "iGoli" OR "iTheku" OR "iANC" OR "uRamaphosa" OR "iBRICS" OR "uhwebo" OR "utshalo-mali" OR "ubambiswano" OR "ingqalasizinda" OR "ezempi" OR "ukuthula" OR "imfundo" OR "ezempilo")
        AND (
            ("narrative*" OR "GNU" OR "Government of National Unity" OR "coalition" OR "non-aligned" OR "alignment" OR "strategic autonomy" OR "koalisie" OR "nasionale eenheid")
            OR ("weaponized" OR "disinformation" OR "deepfake" OR "troll farm" OR "bot network" OR "fopnuus" OR "propaganda" OR "information manipulation"" OR "propaganda" OR "disinformation" OR "social media campaign" OR "5G" OR "Huawei" OR "AI" OR "vaccine")
            OR ("energy crisis" OR "load shedding" OR "Eskom" OR "just energy transition" OR "nuclear deal" OR "Chinese investment" OR "Russian influence" OR "kragkrisis" OR "trade" OR "investment" OR "economic cooperation" OR "mining" OR "energy" OR "infrastructure" OR "military" OR "defense" OR "peace" OR "terrorism")
            OR ("service delivery protest" OR "xenophobia" OR "social unrest" OR "polarization" OR "stoking" OR "incitement" OR "betoging" OR "mislukking")
        )
    )''',

    "DRC": '''(
        ("Democratic Republic of the Congo" OR "République Démocratique du Congo" OR "RDC" OR "Kinshasa" OR "Tshisekedi" OR "Congolais" OR "Kisangani" OR "Lubumbashi" OR "Kolwezi" OR "Kivu" OR "Kokolo" OR "Goma" OR "Corneille Nnanga" OR "Bertrand Bisimwa" OR "Sultani Makenga" OR "Willy Ngoma" OR "Lawrence Kanyuka" OR "Jean-Jacques Mamba" OR "Éric Nkuba" OR "Joseph Kabila" OR "Félix Tshisekedi" OR "bobongisi maponami" OR "maponami" OR "politiki" OR "kampanyi" OR "boyangeli" OR "mbongo na mosala" OR "libaku ya mbongo" OR "nzela" OR "ya nzela" OR "mibundu" OR "liboke ya bitumba" OR "bokengi" OR "kimia" OR "banyama ya liboma" OR "lisungi" OR "ya bokolongono" OR "elenga" OR "nsango ya lokuta" OR "influenceur" OR "media" OR "vaksin" OR "lopitalo" OR "bilanga" OR "kura" OR "misiri" OR "ndako ya Nzambe" OR "kristoya")
        AND (
            ("critical minerals" OR "cobalt" OR "lithium" OR "minerais stratégiques" OR "souveraineté minière" OR "Gecamines" OR "contrats chinois" OR "US-DRC partnership" OR "maadini" OR "mumbanda" OR "élection" OR "présidentielle" OR "scrutin" OR "politique" OR "campagne" OR "gouvernance" OR "francophonie" OR "investissement" OR "commerce" OR "prêt" OR "dette" OR "route" OR "routière" OR "port" OR "rail" OR "oléoduc" OR "militaire" OR "arme" OR "défense" OR "paix" OR "terrorisme" OR "mercenaires" OR "bourse"us" OR "université" OR "aide" OR "sanitaire" OR "cinéma" OR "théâtre" OR "jeune" OR "propagande" OR "désinformation" OR "réseaux sociaux" OR "fausses informations" OR "influenceur" OR "média" OR "5G" OR "Huawei" OR "IA" OR "Intelligence Artificielle" OR "cybersécurité" OR "internet" OR "satellite" OR "surveillance" OR "vaccin" OR "pandémique" OR "hôpital" OR "subvention" OR "agriculture" OR "énergie" OR "cacao" OR "renouvelable" OR "hydraulique" OR "mosqué" OR "église" OR "séminaire" OR "pélérinage" OR "anti-extrémisme" OR "islam" OR "christianisme" OR "chiite" OR "alliance" OR "sunnite")
            OR ("weaponized" OR "disinformation" OR "fake news" OR "propaganda" OR "désinformation" OR "ingérence" OR "manipulation de l'information" OR "lokuta" OR "habari za uongo")
            OR ("M23" OR "Wazalendo" OR "East" OR "Est" OR "Kivu" OR "Ituri" OR "Goma" OR "security-for-minerals" OR "balkanisation" OR "bitumba" OR "vita")
            OR ("élections" OR "human rights" OR "droits de l'homme" OR "corruption" OR "liberté de la presse" OR "bokonzi" OR "demokrasi")
        )
    )''',

    "Côte d'Ivoire": '''( # Using the key as defined
        ("Côte d\'Ivoire" OR "Cote d'Ivoire" OR "Ivory Coast" OR "Abidjan" OR "Yamoussoukro" OR "Alassane Ouattara" OR "Laurent Gbagbo" OR "Henri Konan Bédié" OR "Robert Daudelin" OR "Emmanuel Etiennette" OR "Marcel Amon Tanoh" OR "Kandia Camara" OR "Amadou Gon Coulibaly" OR "Hamed Bakayoko" OR "Adama Bictogo" OR "Charles Blé Goudé" OR "baoulé" OR "baoule" OR "dioula" OR "dyula" OR "senufo" OR "lobi" OR "loby" OR "lobyi" OR "lobyie" OR "lobyien" OR "lobyienne" OR "lobyiens" OR "lobyienes" OR "lobyien(ne)" OR "lobyien(ne)s" OR "lobyien.ne" OR "lobyien.ne.s" OR "lobyien.ne.s." OR "lobyien.ne.s.." OR "lobyien.ne.s...")
        AND (
            ("leadership régional" OR "regional leadership" OR "cacao" OR "cocoa diplomacy" OR "PND 2026" OR "National Development Plan" OR "CFA Franc" OR "Eco" OR "souveraineté monétaire" OR "monetary sovereignty" OR "élection" OR "présidentielle" OR "scrutin" OR "politique" OR "campagne" OR "gouvernance" OR "francophonie" OR "investissement" OR "commerce" OR "prêt" OR "dette" OR "route" OR "routière" OR "port" OR "rail" OR "oléoduc" OR "militaire" OR "arme" OR "défense" OR "paix" OR "terrorisme" OR "mercenaires" OR "bourse" OR "conficius" OR "université" OR "aide" OR "sanitaire" OR "cinéma" OR "théâtre" OR "jeune" OR "propagande" OR "désinformation" OR "réseaux sociaux" OR "fausses informations" OR "influenceur" OR "média" OR "5G" OR "Huawei" OR "IA" OR "Intelligence Artificielle" OR "cybersécurité" OR "internet" OR "satellite" OR "surveillance" OR "vaccin" OR "pandémique" OR "hôpital" OR "subvention" OR "agriculture" OR "énergie" OR "cacao" OR "renouvelable" OR "hydraulique" OR "mosqué" OR "église" OR "séminaire" OR "pélérinage" OR "anti-extrémisme" OR "islam" OR "christianisme" OR "chiite" OR "alliance" OR "sunnite" OR "aide sanitaire")
            OR ("weaponized" OR "désinformation" OR "disinformation" OR "rumors" OR "rumeurs" OR "destabilisation" OR "fake news" OR "propaganda" OR "cybercriminalité" OR "ingérence étrangère" OR "manipulation")
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

def verify_dns(host):
    """Checks if the RDS endpoint is reachable before trying to connect."""
    try:
        socket.gethostbyname(host)
        return True
    except socket.gaierror:
        print(f"DNS Error: Cannot resolve {host}")
        print("Check if your RDS instance is 'Publicly Accessible' or if you are on the correct VPN/Network.")
        return False

def is_article_relevant(article_content, target_country_name):
    """
    Checks if the target country name is mentioned in the article content.
    Performs a simple, case-insensitive substring search.
    """
    if not article_content or not target_country_name:
        return False # Consider empty inputs as irrelevant

    # case-insensitive check
    # You might want to make this more robust (e.g., check for whole words only)
    # using regular expressions if partial matches are an issue.
    return target_country_name.lower() in article_content.lower()

def main():
    all_records = []
    print("🛰️ Querying MediaCloud API...")
    # iteration to use TARGET_COLLECTION_IDS and ACTOR_COLLECTION_IDS
    for country, country_coll_id in TARGET_COLLECTION_IDS.items():
        base_query = QUERY_BY_COUNTRY.get(country)
        for actor, actor_coll_id in ACTOR_COLLECTION_IDS.items():
            try:
                time.sleep(0.5)
                stories, _ = mc_search.story_list(base_query, START_DATE, END_DATE, collection_ids=[actor_coll_id])
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
                        "use_afrolm": False
                    })
                    all_records.append(record)
            except Exception as e:
                logging.error(f"MediaCloud Error {country}-{actor}: {e}")

    df = pd.DataFrame(all_records)
    if df.empty:
        print("❌ No articles found.")
        print("Your existing records remain accessible in the dashboard.")
        return

    # --- ADD AUTOMATION SAFEGUARDS ---
    MAX_ARTICLES_PER_RUN = 200
    MAX_RUNTIME_SECONDS = 800 # Example limit, adjust as needed for Lambda
    df = df.head(MAX_ARTICLES_PER_RUN) # Cap the number of articles processed
    print(f"✅ Found {len(df)} articles (capped at {MAX_ARTICLES_PER_RUN}). Starting Scraper...")
    # --- END ADD AUTOMATION SAFEGUARDS ---

    # --- ADD TIME BUDGET CHECK ---
    loop_start = time.time()
    saved_count = 0
    failed_count = 0 # Initialize counter
    # --- END ADD TIME BUDGET CHECK ---

    for idx, row in df.iterrows():
        # --- CHECK TIME BUDGET INSIDE LOOP ---
        if time.time() - loop_start > MAX_RUNTIME_SECONDS:
            print(f"\n⏰ Time budget ({MAX_RUNTIME_SECONDS}s) reached at article {idx}. Stopping to avoid Lambda timeout.")
            break # Exit the loop gracefully
        # --- END CHECK TIME BUDGET INSIDE LOOP ---

        url = row['url']
        if not url or not isinstance(url, str) or url_exists(url):
            failed_count += 1 # Increment failed counter for skipped URLs
            continue

        content = scrape_full_text_robust(url)

        # --- CHECK CONTENT QUALITY  ---
        is_not_error = not content.startswith("Failed:") and not content.startswith("Error:")
        has_content = len(content) > 1000 # Increased minimum length check

        # --- RELEVANCE CHECK (Integrated Logic) ---
        # Extract the target country from the row
        target_country_from_query = row['target_country'] # Use the country from the query loop

        # Perform the relevance check: is the target country mentioned in the scraped content?
        is_relevant = is_article_relevant(content, target_country_from_query)

        if is_not_error and has_content and is_relevant:
            # All checks passed: quality and relevance
            print(f"[{idx+1}/{len(df)}] ✅ Relevant Article: Processing {url[:40]}... (Target: {target_country_from_query})")

            row_data = row.to_dict()
            row_data['article_text'] = content

            if "nytimes.com" in url or row['media_outlet'] == 'The New York Times':
                row_data['inferred_actor'] = 'USA'

            try:
                final_df = pd.DataFrame([row_data])[db_columns]
                with engine.begin() as conn:
                    final_df.to_sql(DB_TABLE, conn, if_exists='append', index=False) # Use DB_TABLE constant
                saved_count += 1
                print(f"[{idx+1}/{len(df)}] Saved ({row['target_country']}): {url[:40]}...")
            except Exception as e:
                logging.error(f"DB Insert Error for {url}: {e}")
                failed_count += 1 # Increment failed counter for DB errors
        elif not is_relevant:
            # Relevance check failed
            print(f"[{idx+1}/{len(df)}] 🚫 Irrelevant Article: Skipping {url[:40]}... (Target: {target_country_from_query}, not found in text)")
            failed_count += 1 # Consider this a "failure" to meet the relevance criterion
            continue # Explicitly continue, though not strictly necessary here due to elif structure
        else:
            # Either scraping failed or content was too short
            failed_count += 1 # Increment failed counter for scraping errors/low content
            print(f"[{idx+1}/{len(df)}] ❌ Real Failure: {content[:50]}... for {str(url)[:30]}")

        time.sleep(0.5) # Respectful delay

    print(f"\n\n🏁 Finished. Saved: {saved_count}, Failed/Timed-out: {failed_count}. Check your database now.")

    # --- ADD CACHE CLEARING ---
    print("🧹 Cleaning dashboard cache...")
    try:
        cache.clear()
        print("✅ Cache cleared successfully!")
    except Exception as e:
        print(f"⚠️ Cache clear failed: {e}")
    # --- END ADD CACHE CLEARING ---


if __name__ == "__main__":
    main()
