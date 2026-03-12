import os
import pandas as pd
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
# Same list as used in the deletion script
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
        "Tshisekedi", "Joseph Kabila", "Félix Tshisekedi", "Goma", "Kivu", "Kisangani", "Lubumbashi",
        "Kolwezi", "Kokolo", "Corneille Nnanga", "Bertrand Bisimwa", "Sultani Makenga", "Willy Ngoma",
        "Lawrence Kanyuka", "Jean-Jacques Mamba", "Éric Nkuba", "Congolais", "bobongisi maponami", "maponami",
        "politiki", "kampanyi", "boyangeli", "mbongo na mosala", "libaku ya mbongo", "nzela", "ya nzela",
        "mibundu", "liboke ya bitumba", "bokengi", "kimia", "banyama ya liboma", "lisungi", "ya bokolongono",
        "elenga", "nsango ya lokuta", "influenceur", "media", "vaksin", "lopitalo", "bilanga", "kura",
        "misiri", "ndako ya Nzambe", "kristoya"
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

# --- BUILD THE SQL COUNT QUERY (Similar logic to deletion) ---
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

# Combine all the NOT clauses with AND
# e.g., NOT (terms_for_Ethiopia) AND NOT (terms_for_Senegal) AND ...
combined_where = " AND ".join(where_conditions)

# Define the start date for 'old' data
OLD_DATA_CUTOFF_DATE = '2023-10-16' # Use the date you found

# Final SQL query to COUNT rows matching the deletion criteria (excluding NULL/empty/failed text part for now)
# This focuses on the "old AND not mentioning country" part
count_query_str = f"""
SELECT COUNT(*) AS count_of_old_non_target_articles
FROM {DB_TABLE}
WHERE posting_time < :cutoff_date
  AND ( {combined_where} );
"""

print(f"--- Executing Check Query ---")
print(f"Query:\n{count_query_str}")
print(f"Parameters: {{'cutoff_date': '{OLD_DATA_CUTOFF_DATE}'}}")
print(f"This checks for articles older than {OLD_DATA_CUTOFF_DATE} that do not mention a target country.")
print(f"-----------------------------------\n")

# --- EXECUTE THE COUNT QUERY ---
try:
    with engine.connect() as conn: # Use connect() for read-only query
        # Execute the query and fetch the result into a pandas DataFrame
        df_result = pd.read_sql_query(text(count_query_str), conn, params={"cutoff_date": OLD_DATA_CUTOFF_DATE})
        count = df_result.iloc[0]['count_of_old_non_target_articles'] # Extract the count from the result
        print(f"--- Query Result ---")
        print(f"Number of old articles (before {OLD_DATA_CUTOFF_DATE}) NOT mentioning any target country: {count}")
        print(f"---------------------")

        if count > 0:
            print("\n--- Finding Sample Row ---")
            # If count > 0, let's find ONE sample row to confirm
            sample_query_str = f"""
            SELECT posting_time, target_country, inferred_actor, media_outlet, url, article_text
            FROM {DB_TABLE}
            WHERE posting_time < :cutoff_date
              AND ( {combined_where} )
            LIMIT 1;
            """
            df_sample = pd.read_sql_query(text(sample_query_str), conn, params={"cutoff_date": OLD_DATA_CUTOFF_DATE})
            if not df_sample.empty:
                sample_row = df_sample.iloc[0]
                print(f"Sample row found matching criteria:")
                print(f"  Posting Time: {sample_row['posting_time']}")
                print(f"  Target Country: {sample_row['target_country']}")
                print(f"  Inferred Actor: {sample_row['inferred_actor']}")
                print(f"  Media Outlet: {sample_row['media_outlet']}")
                print(f"  URL: {sample_row['url']}")
                print(f"  Article Text (first 200 chars): {sample_row['article_text'][:200]}...")
            else:
                print("WARNING: Count was > 0, but could not fetch a sample row. This is unexpected.")
        else:
            print("\nNo old articles found that do not mention a target country, according to the defined keywords.")

except Exception as e:
    logging.error(f"Error executing count query: {e}")
    print(f"An error occurred during the check: {e}")

print("\n--- Check Complete ---")
