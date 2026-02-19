import json
import boto3
import psycopg2
from psycopg2.extras import RealDictCursor
import os
from datetime import datetime
import sys
import logging

# Add the dashboard directory to Python path
sys.path.insert(0, '/opt/python')
sys.path.insert(0, '/opt/dashboard')

# Import your existing services
from dashboard.services.mediacloud_ingestion_service import main as run_mediacloud_ingestion
from dashboard.services.ml_inference_service import get_ml_service

logger = logging.getLogger(__name__)

def lambda_handler(event, context):
    """Lambda function that runs your existing MediaCloud ingestion service"""
    
    try:
        # Set environment variables from Lambda configuration
        os.environ['MEDIA_CLOUD_API_KEY'] = os.environ.get('MEDIACLOUD_API_KEY')
        os.environ['DB_USER'] = os.environ.get('DB_USER')
        os.environ['DB_PASSWORD'] = os.environ.get('DB_PASSWORD')
        os.environ['DB_HOST'] = os.environ.get('DB_HOST')
        os.environ['DB_PORT'] = os.environ.get('DB_PORT', '5432')
        os.environ['DB_NAME'] = os.environ.get('DB_NAME')
        
        # Log the start
        logger.info("Starting MediaCloud ingestion via Lambda...")
        logger.info(f"Current database has {get_current_article_count()} articles")
        
        # Run your existing ingestion service
        run_mediacloud_ingestion()
        
        # Run ML inference on newly ingested articles
        new_articles_processed = run_ml_inference_on_new_articles()
        
        # Get updated count
        final_count = get_current_article_count()
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'MediaCloud ingestion completed successfully',
                'timestamp': datetime.now().isoformat(),
                'initial_article_count': get_initial_count(),
                'new_articles_processed': new_articles_processed,
                'final_article_count': final_count,
                'status': 'completed'
            })
        }
        
    except Exception as e:
        logger.error(f"Error in Lambda: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': str(e),
                'status': 'failed'
            })
        }

def get_initial_count():
    """Get initial article count"""
    try:
        db_host = os.environ.get('DB_HOST')
        db_name = os.environ.get('DB_NAME')
        db_user = os.environ.get('DB_USER')
        db_password = os.environ.get('DB_PASSWORD')
        
        conn = psycopg2.connect(
            host=db_host,
            database=db_name,
            user=db_user,
            password=db_password
        )
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM media_narratives")
        count = cursor.fetchone()[0]
        cursor.close()
        conn.close()
        return count
    except:
        return 0

def get_current_article_count():
    """Get current article count in database"""
    try:
        db_host = os.environ.get('DB_HOST')
        db_name = os.environ.get('DB_NAME')
        db_user = os.environ.get('DB_USER')
        db_password = os.environ.get('DB_PASSWORD')
        
        conn = psycopg2.connect(
            host=db_host,
            database=db_name,
            user=db_user,
            password=db_password
        )
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM media_narratives")
        count = cursor.fetchone()[0]
        cursor.close()
        conn.close()
        return count
    except:
        return 0

def run_ml_inference_on_new_articles():
    """Run ML inference on newly ingested articles"""
    try:
        # Get database connection info
        db_host = os.environ.get('DB_HOST')
        db_name = os.environ.get('DB_NAME')
        db_user = os.environ.get('DB_USER')
        db_password = os.environ.get('DB_PASSWORD')
        
        # Connect to database
        conn = psycopg2.connect(
            host=db_host,
            database=db_name,
            user=db_user,
            password=db_password
        )
        cursor = conn.cursor()
        
        # Get recently ingested articles (without ML inference results)
        # Focus on articles from the last 24 hours to avoid reprocessing everything
        cursor.execute("""
            SELECT id, article_text, target_country, inferred_actor 
            FROM media_narratives 
            WHERE (strategic_intent IS NULL OR strategic_intent = 'Unknown' OR strategic_intent = '')
            AND posting_time >= NOW() - INTERVAL '1 day'
            ORDER BY posting_time DESC 
            LIMIT 500
        """)
        
        articles = cursor.fetchall()
        
        if not articles:
            logger.info("No new articles to process with ML (within last 24 hours)")
            return 0
        
        # Get ML service instance
        ml_service = get_ml_service()
        
        # Process each article with ML
        processed_count = 0
        for article_id, article_text, target_country, inferred_actor in articles:
            try:
                # Perform ML inference
                result = ml_service.perform_inference(article_text)
                
                # Calculate vulnerability index
                vulnerability_index = ml_service.calculate_vulnerability_index(
                    result['strategic_intent'],
                    result['tone'],
                    target_country,
                    inferred_actor,
                    result['confidence']
                )
                
                # Update the database with ML results
                cursor.execute("""
                    UPDATE media_narratives 
                    SET 
                        strategic_intent = %s,
                        tone = %s,
                        confidence = %s,
                        vulnerability_index = %s,
                        lang_detect = %s,
                        ml_processed_at = NOW()
                    WHERE id = %s
                """, (
                    result['strategic_intent'],
                    result['tone'],
                    result['confidence'],
                    vulnerability_index,
                    result['lang_detect'],
                    article_id
                ))
                
                processed_count += 1
                
                # Log progress every 50 articles
                if processed_count % 50 == 0:
                    logger.info(f"Processed {processed_count}/{len(articles)} articles with ML inference")
                
            except Exception as e:
                logger.error(f"Error processing article {article_id}: {str(e)}")
                continue
        
        # Commit changes
        conn.commit()
        logger.info(f"Successfully processed {processed_count} articles with ML inference")
        
        return processed_count
        
    except Exception as e:
        logger.error(f"Error in ML inference: {str(e)}")
        return 0
    finally:
        if 'cursor' in locals():
            cursor.close()
        if 'conn' in locals():
            conn.close()

def run_quality_validation():
    """Run quality validation on all articles"""
    try:
        db_host = os.environ.get('DB_HOST')
        db_name = os.environ.get('DB_NAME')
        db_user = os.environ.get('DB_USER')
        db_password = os.environ.get('DB_PASSWORD')
        
        conn = psycopg2.connect(
            host=db_host,
            database=db_name,
            user=db_user,
            password=db_password
        )
        cursor = conn.cursor()
        
        # Update articles that might have been missed during initial processing
        cursor.execute("""
            UPDATE media_narratives 
            SET pseudo_kept = TRUE,
                pseudo_weight = 1.0
            WHERE pseudo_kept IS NULL
            AND article_text IS NOT NULL
            AND LENGTH(article_text) > 100
        """)
        
        updated_count = cursor.rowcount
        conn.commit()
        
        logger.info(f"Quality validation updated {updated_count} articles")
        return updated_count
        
    except Exception as e:
        logger.error(f"Quality validation error: {str(e)}")
        return 0
    finally:
        if 'cursor' in locals():
            cursor.close()
        if 'conn' in locals():
            conn.close()
