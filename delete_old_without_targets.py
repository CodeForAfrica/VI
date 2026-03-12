import os
from sqlalchemy import create_engine, text
import logging

# --- CONFIG (Mirror your script's config for DB connection) ---
DB_USER = os.getenv('DB_USER', 'postgres')
DB_PASSWORD = os.getenv('DB_PASSWORD')
DB_HOST = os.getenv('DB_HOST', 'vulnerabilityindex-euwest-01.cfgmtx8ishfx.eu-west-1.rds.amazonaws.com').strip()
DB_PORT = os.getenv('DB_PORT', '5432')
DB_NAME = os.getenv('DB_NAME', 'postgres')
DB_TABLE = "dashboard_medianarrative"

# --- LOGGING ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- DATABASE ENGINE ---
try:
    engine = create_engine(f'postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}', future=True)
    print("--- Database Engine Created Successfully ---\n")
except Exception as e:
    print(f"Error creating database engine: {e}")
    exit(1)

# --- DEFINE TARGET COUNTRY TERMS (Case-insensitive search patterns) ---
# Using a list of tuples for easier management and potential future expansion
# Format: (Country_Name, [list_of_search_terms_for_that_country])
TARGET_TERMS = [
    ("Ethiopia", [
        "Ethiopia", "ኢትዮጵያ", "አዲስ አበባ", "Addis Ababa", "Abiy Ahmed", "GERD", "Grand Ethiopian Renaissance Dam",
        "Tigray", "Amhara", "Oromia", "EPRDF", "TPLF", "Oromo", "Ethiopian"
    ]),
    ("Senegal", [
        "Senegal", "Sénégal", "Dakar", "Macky Sall", "Ousmane Sonko", "Bassirou Diomaye Faye",
        "Senegalais", "Senegalese", "Sénégalaise", "Abdourahmane Diouf", "Khalifa Sall", "Fatma Gueye",
        "Abass Fall", "Ngoné Mbengue", "tàmbali", "jàngoro", "kampaañ", "goubernans", "wulli", "jàppale",
        "fàtt", "militéer", "guddi", "defaans", "jàmm", "teyat", "ndaw", "bataaxal bu dëppoo", "bataaxal yu dëppoo",
        "vaksin", "ndimbal", "ñàg", "kaku", "moské", "njàng", "kristiyaan", "Senegaal"
    ]),
    ("DRC", [
        "DRC", "Democratic Republic of the Congo", "République Démocratique du Congo", "RDC", "Kinshasa",
       "shisekedi", "Joseph Kabila", "Félix Tshisekedi", "Goma", "Kivu", "Kisangani", "Lubumbashi",
        "Kolwezi", "Kokolo", "Corneille Nnanga", "Bertrand Bisimwa", "Sultani Makenga", "Willy Ngoma",
        "Lawrence Kanyuka", "Jean-Jacques Mamba", "Éric Nkuba", "Congolais", "bobongisi maponami", "maponami",
        "politiki", "kampanyi", "boyangeli", "mbongo na mosala", "libaku ya mbongo", "nzela", "ya nzela",
        "mibundu", "liboke ya bitumba", "bokengi", "kimia", "banyama ya liboma", "lisungi", "ya bokolongono",
        "elenga", "nsango ya lokuta", "influenceur", "media", "vaksin", "lopitalo", "bilanga", "kura",
        "misiri", "Nzambe", "kristoya"
    ]),
    ("SA", [
        "South Africa", "Suid-Afrika", "Mzansi", "Pretoria", "Cape Town", "Johannesburg", "Durban", "ANC",
        "Ramaphosa", "BRICS", "iNingizimu Afrika", "iPitoli", "iKapa", "iGoli", "iTheku", "iANC", "uRamaphosa",
        "iBRICS", "uhwebo", "utshalo-mali", "ubambiswano", "ingqalasizinda", "ezempi", "ukuthula", "imfundo",
        "ezempilo", "Government of National Unity", "GNU", "coalition", "non-aligned", "alignment", "strategic autonomy",
        "koalisie", "nasionale eenheid", "energy crisis", "load shedding", "Eskom", "just energy transition",
        "nuclear deal", "Chinese investment", "Russian influence", "kragkrisis", "service delivery protest",
        "xenophobia", "social unrest", "polarization", "stoking", "incitement", "betoging", "mislukking",
        "South African"
    ]),
    ("Côte d'Ivoire", [
        "Côte d'Ivoire", "Cote d'Ivoire", "Ivory Coast", "Abidjan", "Yamoussoukro", "Alassane Ouattara",
        "Laurent Gbagbo", "Henri Konan Bédié", "Robert Daudelin", "Emmanuel Etiennette", "Marcel Amon Tanoh",
        "Kandia Camara", "Amadou Gon Coulibaly", "Hamed Bakayoko", "Adama Bictogo", "Charles Blé Goudé",
        "baoulé", "baoule", "dioula", "dyula", "senufo", "lobi", "loby", "lobyi", "lobyie", "lobyien", "lobyienne",
        "lobyiens", "lobyienes", "lobyien(ne)", "lobyien(ne)s", "lobyien.ne", "lobyien.ne.s", "lobyien.ne.s.",
        "lobyien.ne.s..", "lobyien.ne.s..."
    ])
]

# --- BUILD THE SQL DELETE QUERY ---
# We need to check if article_text contains ANY term from ANY target country list.
# If it contains NO terms from ANY list, it matches the condition for deletion.
# The logic will be: NOT (term_from_country1 OR term_from_country2 OR ...)
# This is equivalent to: NOT term_from_country1 AND2 AND ...

# Start building the WHERE clause for terms NOT present
where_conditions = []

for country_name, terms in TARGET_TERMS:
    # Create an OR clause for all terms within a single country
    # e.g., (article_text ILIKE '%Ethiopia%' OR article_text ILIKE '%Addis Ababa%' ...)
    or_clause_parts = [f"article_text ILIKE '%%{term}%%'" for term in terms]
    or_clause = "(" + " OR ".join(or_clause_parts) + ")"
    # Add the negation of this OR clause to the main list
    # e.g., NOT (article_text ILIKE '%Ethiopia%' OR article_text ILIKE '%Addis Ababa%' ...)
    where_conditions.append(f"NOT ({or_clause})")

# --- ADD MISSING LINE HERE ---
# Combine all the NOT clauses with AND
# e.g., NOT (terms_for_Ethiopia) AND NOT (terms_for_Senegal) AND ...
combined_where = " AND ".join(where_conditions) # This line was missing!
# --- END ADD MISSING LINE ---

# Define the start date for 'old' data (adjust as needed, e.g., yesterday or the day before ingestion)
OLD_DATA_CUTOFF_DATE = '2023-10-16' # Adjust this date - Using the earliest date found

# Final SQL query
# Include checks for NULL/empty/failed text as well
delete_query_str = f"""
DELETE FROM {DB_TABLE}
WHERE
   -- Criteria for NULL, Empty, Failed, or Error text
   (article_text IS NULL
    OR TRIM(article_text) = ''
    OR article_text LIKE 'Failed:%'
    OR article_text LIKE 'Error:%'
    OR article_text = 'Failed to extract content (page might be dynamic or empty)'
    OR article_text = 'Failed: Empty Content')
   -- OR Criteria for articles that do NOT mention ANY target country AND are old
   OR (
       posting_time < :cutoff_date -- Parameterized for safety
       AND ( {combined_where} )
   );
"""

print(f"--- Preparing to Execute Deletion ---")
print(f"Query:\n{delete_query_str}")
print(f"Parameters: {{'cutoff_date': '{OLD_DATA_CUTOFF_DATE}'}}")
print(f"This will delete articles older than {OLD_DATA_CUTOFF_DATE} that do not mention a target country,")
print(f"and also any articles with NULL/empty/failed text regardless of date.")
print(f"------------------------\n")

# --- EXECUTE THE DELETE ---
try:
    with engine.begin() as conn: # Use begin() for transaction
        # Execute the query, passing the cutoff date as a parameter
        # IMPORTANT: Pass the parameters dictionary as the second argument to execute
        result = conn.execute(text(delete_query_str), {"cutoff_date": OLD_DATA_CUTOFF_DATE})
        logging.info(f"Deletion of old, irrelevant articles completed. Rows affected: {result.rowcount}")
except Exception as e:
    logging.error(f"Error executing delete query: {e}")
    print(f"An error occurred during deletion: {e}")
    exit(1)

print(f"\n--- Deletion Summary ---")
print(f"Articles older than {OLD_DATA_CUTOFF_DATE} without any target country terms were deleted.")
print(f"Additionally, any articles with NULL/empty/failed text were also deleted.")
print(f"Total rows removed: {result.rowcount}")
print("------------------------")
