import pandas as pd
import time
from datetime import date, timedelta # Import timedelta
from sqlalchemy import create_engine, text
import mediacloud.api
import trafilatura
import cloudscraper
import sys
import os
import json
import hashlib
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit
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
    "llm_strat_notes", "pseudo_kept", "pseudo_weight",
    "llm_strat_id", "strategic_intent_id", "inference_status",
    "inference_attempts",
]

engine = create_engine(
    f'postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}',
    future=True,
    hide_parameters=True,
)
API_KEY = os.getenv('MEDIACLOUD_API_KEY') # Use environment variable
mc_search = mediacloud.api.SearchApi(API_KEY) if API_KEY else None
MEDIACLOUD_TIMEOUT_SECONDS = max(
    1.0, float(os.getenv("MEDIACLOUD_TIMEOUT_SECONDS", "30"))
)
if mc_search is not None:
    # MediaCloud's BaseApi uses this value for every requests call.
    mc_search.TIMEOUT_SECS = MEDIACLOUD_TIMEOUT_SECONDS


# Daily ingester: only pull a recent window (not a full re-scan every run).
# 3 days gives overlap so a missed/failed day is still caught; url_exists() dedupes.
# For a one-off backfill, temporarily set START_DATE = date(2026, 1, 1).
START_DATE = date.today() - timedelta(days=3)
END_DATE = date.today()

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
            ("narrative*" OR "public opinion" OR "policy shift" OR "state media" OR "foreign influence")
            OR ("weaponized" OR "information warfare" OR "disinformation" OR "fake news" OR "propaganda" OR "media campaign" OR "social media amplification" OR "broadcast in Amharic")
            OR ("investment" OR "infrastructure project" OR "debt relief" OR "foreign aid" OR "trade" OR "mining" OR "manufacturing" OR "energy project" OR "military cooperation" OR "arms sale" OR "defense pact" OR "peacekeeping" OR "security partnership" OR "diplomatic relations" OR "election" OR "governance" OR "anti-corruption" OR "state visit" OR "Confucius Institute" OR "cultural exchange" OR "language school" OR "scholarship" OR "digital Silk Road" OR "5G" OR "Huawei" OR "surveillance" OR "cybersecurity" OR "AI" OR "vaccine" OR "pandemic aid" OR "hospital construction" OR "education" OR "university" OR "climate change" OR "hydropower" OR "agriculture" OR "land lease" OR "energy cooperation" OR "mosque" OR "church" OR "religious coopration")
            OR ("instability" OR "ethnic tension" OR "protest" OR "insurgency" OR "geopolitical competition")
        )
        AND NOT ("sports" OR "football results" OR "travel guide" OR "cooking" OR "entertainment news")
    )''',

    "Senegal": '''(
        ("Senegal" OR "Sénégal" OR "Dakar" OR "Macky Sall" OR "Ousmane Sonko" OR "Bassirou Diomaye Faye" OR "Abdourahmane Diouf" OR "Khalifa Sall" OR "Fatma Gueye" OR "Abass Fall" OR "Ngoné Mbengue" OR "tàmbali" OR "jàngoro" OR "kampaañ" OR "goubernans" OR "wulli" OR "jàppale" OR "fàtt" OR "tali" OR "militéer" OR "guddi" OR "defaans" OR "jàmm" OR "teyat" OR "ndaw" OR "bataaxal bu dëppoo" OR "bataaxal yu dëppoo" OR "vaksin" OR "ndimbal" OR "ñàg" OR "kaku" OR "moské" OR "njàng" OR "kristiyaan")
        AND (
            ("narrative*" OR "souveraineté" OR "souveraineté économique" OR "sentiment anti-français" OR "sentiment" OR "perceptions publiques" OR "opinion publique" OR "public opinion")
            OR ("weaponized" OR "manipulation" OR "disinformation" OR "coordonné" OR "fake news" OR "propaganda" OR "désinformation" OR "influence étrangère" OR "coordonnée" OR "ingérence" OR "multipartisme" OR "teranga" OR "TER" OR "FAS" OR "DAGE" OR "élection" OR "présidentielle" OR "scrutin" OR "politique" OR "campagne" OR "gouvernance" OR "francophonie" OR "conficius" OR "université" OR "sanitaire" OR "cinéma" OR "théâtre" OR "jeune" OR "réseaux sociaux" OR "fausses informations" OR "influenceur" OR "média" OR "IA" OR "Intelligence Artificielle" OR "cybersécurité" OR "internet" OR "satellite" OR "surveillance" OR "vaccin" OR "pandémique" OR "hôpital" OR "subvention" OR "renouvelable" OR "hydraulique" OR "mosqué" OR "église" OR "séminaire" OR "pélérinage" OR "anti-extrémisme" OR "islam" OR "christianisme" OR "chiite" OR "alliance" OR "sunnite")
            OR ("investment" OR "infrastructure project" OR "projets pétroliers" OR "ressources naturelles" OR "investissements directs" OR "dette" OR "prêt" OR "debt" OR "foreign aid" OR "aide étrangère" OR "commerce" OR "route" OR "routière" OR "port" OR "rail" OR "oléoduc" OR "militaire" OR "arme" OR "paix" OR "terrorisme" OR "mercenaires" OR "bourse" OR "aide" OR "cacao" OR "énergie" OR "agriculture")
            OR ("instability" OR "tensions politiques" OR "manifestations" OR "protests" OR "terrorisme" OR "sécurité régionale" OR "Sahel" OR "AES")
        )
    )''',

    "SA": '''( # Using 'SA' key to match your query definition
        ("South Africa" OR "Suid-Afrika" OR "Mzansi" OR "Pretoria" OR "Johannesburg" OR "Cape Town" OR "Durban" OR "ANC" OR "Ramaphosa" OR "BRICS" OR "iNingizimu Afrika" OR "iPitoli" OR "iKapa" OR "iGoli" OR "iTheku" OR "iANC" OR "uRamaphosa" OR "iBRICS" OR "uhwebo" OR "utshalo-mali" OR "ubambiswano" OR "ingqalasizinda" OR "ezempi" OR "ukuthula" OR "imfundo" OR "ezempilo")
        AND (
            ("narrative*" OR "GNU" OR "Government of National Unity" OR "coalition" OR "non-aligned" OR "alignment" OR "strategic autonomy" OR "koalisie" OR "nasionale eenheid")
            OR ("weaponized" OR "disinformation" OR "deepfake" OR "troll farm" OR "bot network" OR "fopnuus" OR "propaganda" OR "information manipulation" OR "propaganda" OR "disinformation" OR "social media campaign" OR "5G" OR "Huawei" OR "AI" OR "vaccine")
            OR ("energy crisis" OR "load shedding" OR "Eskom" OR "just energy transition" OR "nuclear deal" OR "Chinese investment" OR "Russian influence" OR "kragkrisis" OR "trade" OR "investment" OR "economic cooperation" OR "mining" OR "energy" OR "infrastructure" OR "military" OR "defense" OR "peace" OR "terrorism")
            OR ("service delivery protest" OR "xenophobia" OR "social unrest" OR "polarization" OR "stoking" OR "incitement" OR "betoging" OR "mislukking")
        )
    )''',

    "DRC": '''(
        ("Democratic Republic of the Congo" OR "République Démocratique du Congo" OR "RDC" OR "Kinshasa" OR "Tshisekedi" OR "Congolais" OR "Kisangani" OR "Lubumbashi" OR "Kolwezi" OR "Kivu" OR "Kokolo" OR "Goma" OR "Corneille Nnanga" OR "Bertrand Bisimwa" OR "Sultani Makenga" OR "Willy Ngoma" OR "Lawrence Kanyuka" OR "Jean-Jacques Mamba" OR "Éric Nkuba" OR "Joseph Kabila" OR "Félix Tshisekedi" OR "bobongisi maponami" OR "maponami" OR "politiki" OR "kampanyi" OR "boyangeli" OR "mbongo na mosala" OR "libaku ya mbongo" OR "nzela" OR "ya nzela" OR "mibundu" OR "liboke ya bitumba" OR "bokengi" OR "kimia" OR "banyama ya liboma" OR "lisungi" OR "ya bokolongono" OR "elenga" OR "nsango ya lokuta" OR "influenceur" OR "media" OR "vaksin" OR "lopitalo" OR "bilanga" OR "kura" OR "misiri" OR "ndako ya Nzambe" OR "kristoya")
        AND (
            ("critical minerals" OR "cobalt" OR "lithium" OR "minerais stratégiques" OR "souveraineté minière" OR "Gecamines" OR "contrats chinois" OR "US-DRC partnership" OR "maadini" OR "mumbanda" OR "élection" OR "présidentielle" OR "scrutin" OR "politique" OR "campagne" OR "gouvernance" OR "francophonie" OR "investissement" OR "commerce" OR "prêt" OR "dette" OR "route" OR "routière" OR "port" OR "rail" OR "oléoduc" OR "militaire" OR "arme" OR "défense" OR "paix" OR "terrorisme" OR "mercenaires" OR "bourse" OR "université" OR "aide" OR "sanitaire" OR "cinéma" OR "théâtre" OR "jeune" OR "propagande" OR "désinformation" OR "réseaux sociaux" OR "fausses informations" OR "influenceur" OR "média" OR "5G" OR "Huawei" OR "IA" OR "Intelligence Artificielle" OR "cybersécurité" OR "internet" OR "satellite" OR "surveillance" OR "vaccin" OR "pandémique" OR "hôpital" OR "subvention" OR "agriculture" OR "énergie" OR "cacao" OR "renouvelable" OR "hydraulique" OR "mosqué" OR "église" OR "séminaire" OR "pélérinage" OR "anti-extrémisme" OR "islam" OR "christianisme" OR "chiite" OR "alliance" OR "sunnite")
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
    except Exception:
        # A lookup failure is not proof that the URL is new. Let the caller log
        # and count the database failure instead of risking a duplicate insert.
        raise

def scrape_full_text_robust(url, deadline=None):
    for attempt in range(2):
        if deadline is not None and time.time() >= deadline - 1:
            return None, {
                "error_code": "scrape_time_budget_exhausted",
                "error_type": "DeadlineExceeded",
                "attempts": attempt,
            }
        try:
            timeout = 20
            if deadline is not None:
                timeout = max(1, min(timeout, int(deadline - time.time() - 1)))
            response = scraper.get(url, timeout=timeout)
            if response.status_code == 200:
                text_extracted = trafilatura.extract(response.text)
                if text_extracted:
                    return text_extracted, {"attempts": attempt + 1,
                                            "http_status": response.status_code}
                return None, {"error_code": "scrape_extraction_empty",
                              "error_type": "ExtractionError",
                              "http_status": response.status_code,
                              "attempts": attempt + 1}
            return None, {"error_code": "scrape_http_error",
                          "error_type": "HttpError",
                          "http_status": response.status_code,
                          "attempts": attempt + 1}
        except Exception as e:
            if attempt < 1:
                delay = 3
                if deadline is not None:
                    delay = min(delay, max(0, deadline - time.time() - 1))
                if delay <= 0:
                    return None, {"error_code": "scrape_time_budget_exhausted",
                                  "error_type": "DeadlineExceeded",
                                  "attempts": attempt + 1}
                time.sleep(delay)
                continue
            return None, {"error_code": "scrape_request_failed",
                          "error_type": type(e).__name__,
                          "error_detail": str(e).replace(url, "[REDACTED_URL]")[:500],
                          "attempts": attempt + 1}

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

def _structured_log(level, event, **fields):
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
        "level": level,
        "service": "vi-ingestion-lambda",
        "event": event,
    }
    sensitive_parts = ("api_key", "accepted_key", "password", "secret", "authorization",
                       "access_key", "session_token", "article_text")
    secrets = [value for name, value in os.environ.items()
               if value and any(part in name.lower() for part in sensitive_parts)]
    for key, value in fields.items():
        normalized = str(key).lower().replace("-", "_")
        if normalized == "key" or any(part in normalized for part in sensitive_parts):
            entry[key] = "[REDACTED]"
            continue
        if isinstance(value, str):
            for secret in secrets:
                value = value.replace(secret, "[REDACTED]")
            value = re.sub(r"(://[^:/\s]+:)[^@/\s]+@",
                           r"\1[REDACTED]@", value)
        entry[key] = value
    stream = sys.stderr if level in ("WARNING", "ERROR") else sys.stdout
    stream.write(json.dumps(entry) + "\n")
    stream.flush()


def _url_log_fields(url):
    """Safe URL identity: enough to correlate, without logging paths/queries."""
    return {
        "url_host": (urlsplit(url).hostname or "unknown")[:255],
        "url_hash": hashlib.sha256(url.encode("utf-8")).hexdigest()[:16],
    }


def _mediacloud_request_timeout(deadline=None):
    """Bound one MediaCloud request, preserving time for orderly shutdown."""
    if deadline is None:
        return MEDIACLOUD_TIMEOUT_SECONDS
    remaining = deadline - time.time() - 1
    if remaining <= 0:
        return None
    return min(MEDIACLOUD_TIMEOUT_SECONDS, remaining)


def _database_error_code(exc):
    """Return a useful driver code without serializing SQL bound parameters."""
    original = getattr(exc, "orig", None)
    return (getattr(original, "sqlstate", None)
            or getattr(original, "pgcode", None)
            or "unknown")


def main(deadline=None, event_logger=None):
    emit = event_logger or _structured_log
    if mc_search is None:
        emit("ERROR", "mediacloud_query_failed", error_type="ConfigurationError",
             error_code="mediacloud_api_key_missing")
        raise RuntimeError("MediaCloud API key is not configured")
    all_records = []
    found_count = skipped_count = inserted_count = failed_count = 0
    query_attempts = query_failure_count = 0
    emit("INFO", "mediacloud_query_started")
    # Time-budget the query-gathering phase so it can't eat the whole Lambda
    # runtime before scraping/inserting begins (ponytail: this loop had no guard).
    QUERY_BUDGET_SECONDS = 300
    query_start = time.time()
    # iteration to use TARGET_COLLECTION_IDS and ACTOR_COLLECTION_IDS
    for country, country_coll_id in TARGET_COLLECTION_IDS.items():
        if (time.time() - query_start > QUERY_BUDGET_SECONDS
                or (deadline is not None and time.time() >= deadline)):
            emit("WARNING", "mediacloud_query_stopped", reason="time_budget_exhausted",
                 articles_found=len(all_records))
            break
        base_query = QUERY_BY_COUNTRY.get(country)
        for actor, actor_coll_id in ACTOR_COLLECTION_IDS.items():
            if (time.time() - query_start > QUERY_BUDGET_SECONDS
                    or (deadline is not None and time.time() >= deadline)):
                break
            attempt_started = time.time()
            query_attempts += 1
            try:
                time.sleep(0.5)
                request_timeout = _mediacloud_request_timeout(deadline)
                if request_timeout is None:
                    emit("WARNING", "mediacloud_query_stopped",
                         reason="time_budget_exhausted",
                         articles_found=len(all_records))
                    break
                mc_search.TIMEOUT_SECS = request_timeout
                emit("INFO", "mediacloud_query_attempt_started",
                     target_country=country, inferred_actor=actor,
                     attempt=query_attempts,
                     timeout_seconds=round(request_timeout, 3))
                stories, _ = mc_search.story_list(base_query, START_DATE, END_DATE, collection_ids=[actor_coll_id])
                emit("INFO", "mediacloud_query_attempt_completed",
                     target_country=country, inferred_actor=actor,
                     attempt=query_attempts, articles_found=len(stories),
                     duration_ms=int((time.time() - attempt_started) * 1000))
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
                        "inference_status": "pending",
                        "inference_attempts": 0,
                    })
                    all_records.append(record)
            except Exception as e:
                query_failure_count += 1
                emit("ERROR", "mediacloud_query_failed", target_country=country,
                     inferred_actor=actor, error_type=type(e).__name__,
                     error_code="mediacloud_query_failed",
                     error_detail=str(e)[:500], attempt=query_attempts,
                     duration_ms=int((time.time() - attempt_started) * 1000))

    df = pd.DataFrame(all_records)
    found_count = len(df)
    emit("INFO", "mediacloud_query_completed", articles_found=found_count,
         query_attempts=query_attempts, query_failed=query_failure_count,
         duration_ms=int((time.time() - query_start) * 1000))
    if query_attempts and query_failure_count == query_attempts:
        raise RuntimeError("all MediaCloud queries failed")
    if df.empty:
        return {"found": 0, "skipped": 0, "inserted": 0,
                "scrape_failed": 0, "deferred": 0,
                "query_failed": query_failure_count}

    # --- ADD AUTOMATION SAFEGUARDS ---
    MAX_ARTICLES_PER_RUN = 200
    MAX_RUNTIME_SECONDS = 800 # Example limit, adjust as needed for Lambda
    df = df.head(MAX_ARTICLES_PER_RUN) # Cap the number of articles processed
    emit("INFO", "article_scrape_batch_started", articles=len(df),
         limit=MAX_ARTICLES_PER_RUN)
    # --- END ADD AUTOMATION SAFEGUARDS ---

    # --- ADD TIME BUDGET CHECK ---
    loop_start = time.time()
    processed_count = 0
    # --- END ADD TIME BUDGET CHECK ---

    for idx, row in df.iterrows():
        # --- CHECK TIME BUDGET INSIDE LOOP ---
        if (time.time() - loop_start > MAX_RUNTIME_SECONDS
                or (deadline is not None and time.time() >= deadline)):
            emit("WARNING", "article_scrape_stopped", reason="time_budget_exhausted",
                 remaining=max(0, len(df) - int(idx)))
            break # Exit the loop gracefully
        # --- END CHECK TIME BUDGET INSIDE LOOP ---
        processed_count += 1

        url = row['url']
        if not url or not isinstance(url, str):
            skipped_count += 1
            emit("INFO", "article_skipped", source_index=int(idx),
                 reason="missing_or_invalid_url")
            continue
        url_fields = _url_log_fields(url)
        try:
            duplicate = url_exists(url)
        except Exception as e:
            failed_count += 1
            emit("ERROR", "article_duplicate_check_failed", source_index=int(idx),
                 database_operation="duplicate_check", error_type=type(e).__name__,
                 error_code="database_duplicate_check_failed",
                 database_error_code=_database_error_code(e),
                 error_detail="Database duplicate check failed",
                 **url_fields)
            continue
        if duplicate:
            skipped_count += 1
            emit("INFO", "duplicate_article_skipped", source_index=int(idx),
                 reason="duplicate", **url_fields)
            continue

        scrape_started = time.time()
        emit("INFO", "article_scrape_started", source_index=int(idx), **url_fields)
        content, scrape_meta = scrape_full_text_robust(url, deadline=deadline)

        # --- CHECK CONTENT QUALITY  ---
        has_content = bool(content and len(content) > 1000)

        # --- RELEVANCE CHECK (Integrated Logic) ---
        # Extract the target country from the row
        target_country_from_query = row['target_country'] # Use the country from the query loop

        # Perform the relevance check: is the target country mentioned in the scraped content?
        is_relevant = bool(
            content
            and isinstance(target_country_from_query, str)
            and is_article_relevant(content, target_country_from_query)
        )

        if has_content and is_relevant:
            # All checks passed: quality and relevance
            emit("INFO", "article_scrape_completed", source_index=int(idx),
                 target_country=target_country_from_query,
                 duration_ms=int((time.time() - scrape_started) * 1000),
                 attempts=scrape_meta.get("attempts"), **url_fields)

            row_data = row.to_dict()
            row_data['article_text'] = content

            if "nytimes.com" in url or row['media_outlet'] == 'The New York Times':
                row_data['inferred_actor'] = 'USA'

            try:
                final_df = pd.DataFrame([row_data])[db_columns]
                with engine.begin() as conn:
                    final_df.to_sql(DB_TABLE, conn, if_exists='append', index=False) # Use DB_TABLE constant
                inserted_count += 1
                emit("INFO", "article_inserted", source_index=int(idx),
                     target_country=row['target_country'], **url_fields)
            except Exception as e:
                emit("ERROR", "article_insert_failed", source_index=int(idx),
                     database_operation="insert_article",
                     error_type=type(e).__name__, error_code="database_insert_failed",
                     database_error_code=_database_error_code(e),
                     error_detail="Database rejected the article insert",
                     **url_fields)
                failed_count += 1
        elif content and has_content and not is_relevant:
            # Relevance check failed
            skipped_count += 1
            emit("INFO", "article_skipped", source_index=int(idx), reason="irrelevant",
                 target_country=target_country_from_query, **url_fields)
            continue # Explicitly continue, though not strictly necessary here due to elif structure
        else:
            # Either scraping failed or content was too short
            failed_count += 1 # Increment failed counter for scraping errors/low content
            failure = scrape_meta if not content else {
                "error_code": "scrape_content_too_short",
                "error_type": "ContentQualityError",
                "content_length": len(content),
                "attempts": scrape_meta.get("attempts"),
            }
            emit("ERROR", "article_scrape_failed", source_index=int(idx),
                 duration_ms=int((time.time() - scrape_started) * 1000),
                 **failure, **url_fields)

        time.sleep(0.5) # Respectful delay

    deferred_count = max(0, found_count - processed_count)
    emit("INFO", "article_scrape_batch_completed", found=found_count,
         processed=processed_count, skipped=skipped_count, inserted=inserted_count,
         scrape_failed=failed_count, deferred=deferred_count,
         query_failed=query_failure_count)

    # --- ADD CACHE CLEARING ---
    try:
        cache.clear()
        emit("INFO", "dashboard_cache_cleared")
    except Exception as e:
        emit("WARNING", "dashboard_cache_clear_failed", error_type=type(e).__name__,
             error_code="cache_clear_failed", error_detail=str(e)[:500])

    return {
        "found": found_count,
        "skipped": skipped_count,
        "inserted": inserted_count,
        "scrape_failed": failed_count,
        "deferred": deferred_count,
        "query_failed": query_failure_count,
    }
    # --- END ADD CACHE CLEARING ---


if __name__ == "__main__":
    main()
