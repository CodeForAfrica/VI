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

# --- QUERIES ---
queries_to_run = [
    {
        "name": "Total Article Count",
        "sql": "SELECT COUNT(*) AS total_articles FROM dashboard_medianarrative;"
    },
    {
        "name": "Recent Articles Sample (Last 5)",
        "sql": """
            SELECT posting_time, target_country, inferred_actor, media_outlet, url, LENGTH(article_text) AS text_length
            FROM dashboard_medianarrative
            ORDER BY posting_time DESC
            LIMIT 5;
        """
    },
    {
        "name": "Article Counts by Actor/Target (Since Yesterday)",
        "sql": """
            SELECT target_country, inferred_actor, COUNT(*) AS article_count
            FROM dashboard_medianarrative
            WHERE posting_time >= CURRENT_DATE - INTERVAL '1 day' -- Adjust if needed
            GROUP BY target_country, inferred_actor
            ORDER BY target_country, inferred_actor;
        """
    },
    # Add more queries here as needed
     {
         "name": "Sample Article Text (Recent & Substantial)",
         "sql": """
             SELECT article_text
             FROM dashboard_medianarrative
             WHERE posting_time >= CURRENT_DATE - INTERVAL '1 day' -- Adjust date if needed
             AND LENGTH(article_text) > 500 -- Only look at somewhat substantial texts
             LIMIT 1;
         """
     },
     {
         "name": "Check for Specific Irrelevant Terms (Example: Israel, non-SA)",
         "sql": """
             SELECT target_country, inferred_actor, url, posting_time
             FROM dashboard_medianarrative
             WHERE posting_time >= CURRENT_DATE - INTERVAL '1 day' -- Adjust date if needed
             AND article_text ILIKE '%Israel%' -- Use ILIKE for case-insensitive search
             AND target_country NOT IN ('SA'); -- Example exclusion
         """
     },
    # NEW QUERY: Check for old data with corrupted/incomplete article text
    {
        "name": "Count of Corrupted/Incomplete Article Text (ALL Data)",
        "sql": """
            SELECT COUNT(*) AS corrupted_article_count
            FROM dashboard_medianarrative
            WHERE article_text IS NULL
               OR TRIM(article_text) = ''
               OR article_text LIKE 'Failed:%'
               OR article_text LIKE 'Error:%'
               OR article_text = 'Failed to extract content (page might be dynamic or empty)'
               OR article_text = 'Failed: Empty Content';
        """
    },
    # Optional: List some examples of corrupted articles (LIMIT to avoid huge output)
    {
        "name": "Examples of Corrupted/Incomplete Article Text (First 3)",
        "sql": """
            SELECT posting_time, target_country, inferred_actor, media_outlet, url, article_text
            FROM dashboard_medianarrative
            WHERE article_text IS NULL
               OR TRIM(article_text) = ''
               OR article_text LIKE 'Failed:%'
               OR article_text LIKE 'Error:%'
               OR article_text = 'Failed to extract content (page might be dynamic or empty)'
               OR article_text = 'Failed: Empty Content'
            LIMIT 3;
        """
    }
]
# --- EXECUTE QUERIES ---
for query_info in queries_to_run:
    print(f"--- {query_info['name']} ---")
    try:
        with engine.connect() as conn:
            # Execute the query and fetch results into a pandas DataFrame
            df = pd.read_sql_query(text(query_info['sql']), conn)
            if not df.empty:
                print(df.to_string(index=False)) # Print DataFrame without row indices
            else:
                print("No results found for this query.")
    except Exception as e:
        print(f"Error executing query '{query_info['name']}': {e}")
    print("\n") # Add spacing between results

print("--- Database Inspection Complete ---")
