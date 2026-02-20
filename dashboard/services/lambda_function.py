import json
import boto3
import psycopg2
from psycopg2.extras import RealDictCursor
import os
from datetime import datetime
import sys
import logging

# Add the dashboard directory to Python path
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(CURRENT_DIR)

# Import your existing services
from dashboard.services.mediacloud_ingestion_service import main as run_mediacloud_ingestion
from dashboard.services.ml_inference_service import get_ml_service

logger = logging.getLogger(__name__)

# Use the table name from your ingestion script
TABLE_NAME = "dashboard_medianarrative" 

def get_db_connection():
    return psycopg2.connect(
        host=os.environ.get('DB_HOST'),
        database=os.environ.get('DB_NAME'),
        user=os.environ.get('DB_USER'),
        password=os.environ.get('DB_PASSWORD'),
        port=os.environ.get('DB_PORT', '5432')
    )

def lambda_handler(event, context):
    conn = None
    try:
        # Map Environment Variables (Ensures consistency)
        os.environ['MEDIA_CLOUD_API_KEY'] = os.environ.get('MEDIACLOUD_API_KEY', '')
        
        conn = get_db_connection()
        
        # 1. Initial State
        initial_count = get_count(conn)
        logger.info(f"Starting ingestion. Current count: {initial_count}")
        
        # 2. Run Ingestion (This calls your scraping/mediacloud logic)
        run_mediacloud_ingestion()
        
        # 3. Run ML Inference
        new_articles_processed = run_ml_inference_on_new_articles(conn)
        
        # 4. Final Validation
        run_quality_validation(conn)
        
        final_count = get_count(conn)
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'Success',
                'initial_count': initial_count,
                'processed': new_articles_processed,
                'final_count': final_count
            })
        }
        
    except Exception as e:
        logger.error(f"Lambda Failure: {str(e)}")
        return {'statusCode': 500, 'body': json.dumps({'error': str(e)})}
    finally:
        if conn:
            conn.close()

def get_count(conn):
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {TABLE_NAME}")
        return cur.fetchone()[0]

def run_ml_inference_on_new_articles(conn):
    processed_count = 0
    with conn.cursor() as cursor:
        # SQL Fixed: Use TABLE_NAME and match the ingestion date filter
        cursor.execute(f"""
            SELECT id, article_text, target_country, inferred_actor 
            FROM {TABLE_NAME} 
            WHERE (strategic_intent IS NULL OR strategic_intent = '' OR strategic_intent = 'Unknown')
            AND posting_time >= NOW() - INTERVAL '2 days'
            LIMIT 500
        """)
        
        articles = cursor.fetchall()
        if not articles: return 0
        
        ml_service = get_ml_service()
        for art_id, text, country, actor in articles:
            try:
                res = ml_service.perform_inference(text)
                v_index = ml_service.calculate_vulnerability_index(
                    res['strategic_intent'], res['tone'], country, actor, res['confidence']
                )
                
                cursor.execute(f"""
                    UPDATE {TABLE_NAME} SET 
                    strategic_intent = %s, tone = %s, confidence = %s, 
                    vulnerability_index = %s, lang_detect = %s, ml_processed_at = NOW()
                    WHERE id = %s
                """, (res['strategic_intent'], res['tone'], res['confidence'], v_index, res['lang_detect'], art_id))
                processed_count += 1
            except Exception as e:
                logger.error(f"Art {art_id} error: {e}")
        
        conn.commit()
    return processed_count

def run_quality_validation(conn):
    with conn.cursor() as cursor:
        cursor.execute(f"""
            UPDATE {TABLE_NAME} SET pseudo_kept = TRUE, pseudo_weight = 1.0
            WHERE pseudo_kept IS NULL AND article_text IS NOT NULL AND LENGTH(article_text) > 100
        """)
        conn.commit()
