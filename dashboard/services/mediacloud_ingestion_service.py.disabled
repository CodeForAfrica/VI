import requests
import json
import logging
import pandas as pd
from datetime import datetime
from django.conf import settings
from dashboard.models import MediaNarrative
from dashboard.services.ml_inference_service import MLInferenceService
from .summarizer import get_summary

logger = logging.getLogger(__name__)

class MediaCloudIngestionService:
    
    def __init__(self):
        self.ml_service = MLInferenceService()
        self.api_key = settings.MEDIACLOUD_API_KEY
        self.base_url = "https://api.mediacloud.org/api/v2"
    
    def extract_article_content(self, url):
        """Extract article text from URL"""
        try:
            import requests
            from bs4 import BeautifulSoup
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }
            
            response = requests.get(url, headers=headers, timeout=30)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Remove script and style elements
            for script in soup(["script", "style"]):
                script.decompose()
            
            # Try to get article content from common selectors
            article_selectors = [
                'article', 'main', '[role="main"]', '.article-body', 
                '.post-content', '.entry-content', '#content', '.content'
            ]
            
            text_content = ""
            for selector in article_selectors:
                element = soup.select_one(selector)
                if element:
                    text_content = element.get_text()
                    break
            
            if not text_content:
                text_content = soup.get_text()
            
            # Clean up text
            lines = (line.strip() for line in text_content.splitlines())
            chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
            text = ' '.join(chunk for chunk in chunks if chunk)
            
            return text[:4000]  # Limit text length as in notebook
        except Exception as e:
            logger.error(f"Error extracting content from {url}: {e}")
            return ""
    
    def determine_target_country(self, article_text):
        """Determine target country from article content - MATCHING EXACT FORMAT FROM CONTEXTUAL MODULE"""
        text_lower = article_text.lower()
        
        # Use the EXACT country names from your contextual_all_intents_v2.py:
        # countries = ["Senegal","DRC","CoteIvoire","Ethiopia"] plus "South Africa" from data
        country_variations = {
            'Senegal': ['senegal', 'senegalese'],
            'DRC': ['drc', 'democratic republic of congo', 'congo', 'kinshasa', 'goma'],
            'CoteIvoire': ['cote ivoire', 'cote-ivoire', 'coteivoire', 'cotedivoire', 'ivory coast', 'abidjan'],  # NO APOSTROPHE IN YOUR MODULE
            'Ethiopia': ['ethiopia', 'ethiopian', 'addis ababa'],
            'South Africa': ['south africa', 'south african', 'cape town', 'johannesburg', 'pretoria']
        }
        
        for country, variations in country_variations.items():
            for variation in variations:
                if variation.lower() in text_lower:
                    return country  # Return EXACT format from your module
        
        return "Unknown"
    
    def is_relevant_article(self, article_data, article_text):
        """Check if article discusses target country (relevance filtering)"""
        target_country = self.determine_target_country(article_text)
        article_data['target_country'] = target_country
        
        # Only keep articles that mention target country
        return target_country != "Unknown"
    
    def infer_actor_from_media(self, media_name):
        """Derive inferred_actor from media_outlet"""
        media_lower = media_name.lower()
        
        # Use actors from your contextual module
        actors = ['china', 'france', 'unitedstates', 'russia', 'rwanda', 'saudi', 'turkey', 'uae', 'israel', 'iran', 'nonstate']
        
        # Look for actor mentions in media name
        for actor in actors:
            if actor.lower() in media_lower:
                return actor.title()
        
        # Example mapping - customize based on your notebook logic
        government_keywords = ['government', 'official', 'ministry', 'state', 'president', 'prime minister', 'minister']
        opposition_keywords = ['opposition', 'party', 'protest', 'dissent', 'critic', 'party']
        media_keywords = ['news', 'press', 'media', 'reporter', 'journalist']
        
        if any(keyword in media_lower for keyword in government_keywords):
            return 'Government'
        elif any(keyword in media_lower for keyword in opposition_keywords):
            return 'Opposition'
        else:
            return 'Media'
    
    def determine_sector(self, article_text):
        """Determine sector from article content"""
        text_lower = article_text.lower()
        
        sectors = {
            'politics': ['politic', 'election', 'government', 'minister', 'parliament', 'policy', 'vote', 'campaign'],
            'economy': ['econom', 'finance', 'bank', 'market', 'trade', 'budget', 'investment', 'currency', 'debt'],
            'health': ['health', 'hospital', 'medical', 'disease', 'treatment', 'vaccine', 'doctor', 'patient'],
            'education': ['educat', 'school', 'university', 'student', 'teacher', 'academic', 'degree'],
            'security': ['militar', 'army', 'police', 'security', 'conflict', 'violence', 'war', 'peace'],
            'resource': ['oil', 'gas', 'mineral', 'gold', 'diamond', 'resource', 'extractive'],
            'lgbtq': ['lgbt', 'gay', 'lesbian', 'transgender', 'equality', 'rights'],
            'religion': ['religion', 'church', 'mosque', 'temple', 'faith', 'belief', 'islam', 'christian', 'hindu']
        }
        
        for sector, keywords in sectors.items():
            if any(keyword in text_lower for keyword in keywords):
                return sector.title()
        
        return 'General'
    
    def process_article(self, raw_article):
        """Process individual article: extract, filter, infer, summarize, index"""
        try:
            # Basic article data
            article_info = {
                'id': raw_article.get('stories_id'),
                'indexed_date': raw_article.get('indexed_date'),
                'language': raw_article.get('language'),
                'media_name': raw_article.get('media_name'),
                'media_url': raw_article.get('media_url'),
                'publish_date': raw_article.get('publish_date'),
                'title': raw_article.get('title'),
                'url': raw_article.get('url'),
            }
            
            # Extract full article text
            article_text = self.extract_article_content(article_info['url'])
            
            # Skip if no content extracted
            if not article_text.strip():
                logger.warning(f"No content extracted from {article_info['url']}")
                return None
            
            # Relevance check
            if not self.is_relevant_article(article_info, article_text):
                logger.info(f"Article {article_info['id']} filtered out - not relevant to target country")
                return None
            
            # Generate summary (your existing feature)
            summary = get_summary(article_text)
            
            # Perform ML inference (new sophisticated ML from notebook)
            inference_results = self.ml_service.perform_inference(article_text)
            
            # Determine inferred actor from media outlet
            inferred_actor = self.infer_actor_from_media(article_info['media_name'])
            
            # Determine sector
            sector = self.determine_sector(article_text)
            
            # Calculate vulnerability index using contextual module
            vulnerability_index = self.ml_service.calculate_vulnerability_index(
                strategic_intent=inference_results['strategic_intent'],
                tone=inference_results['tone'],
                target_country=article_info['target_country'],
                inferred_actor=inferred_actor,
                confidence=inference_results['confidence']
            )
            
            # Prepare final article data for database
            final_article = {
                'article_text': article_text,
                'summary': summary,  # Include summary in the record
                'posting_time': article_info['publish_date'],
                'media_outlet': article_info['media_name'],
                'inferred_actor': inferred_actor,
                'strategic_intent': inference_results['strategic_intent'],
                'sector': sector,
                'tone': inference_results['tone'],
                'target_country': article_info['target_country'],
                'url': article_info['url'],
                'confidence': inference_results['confidence'],
                'lang_detect': inference_results['lang_detect'],
                'use_afrolm': inference_results['use_afrolm'],
                'vulnerability_index': vulnerability_index
            }
            
            return final_article
            
        except Exception as e:
            logger.error(f"Error processing article {raw_article.get('stories_id', 'unknown')}: {e}")
            return None
    
    def fetch_articles(self, query_params=None):
        """Fetch articles from MediaCloud API"""
        if query_params is None:
            # Customize this query based on your monitoring needs
            query_params = {
                'q': 'ethiopia OR nigeria OR kenya',  # Replace with your actual monitoring terms
                'limit': 100,
                'format': 'json'
            }
        
        headers = {'Authorization': f'ApiKey {self.api_key}'}
        
        try:
            response = requests.get(
                f"{self.base_url}/story/list",
                params=query_params,
                headers=headers
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Error fetching articles from MediaCloud: {e}")
            return []
    
    def save_article_to_db(self, article_data):
        """Save processed article to RDS"""
        try:
            MediaNarrative.objects.create(**article_data)
            logger.info(f"Saved article: {article_data['url']}")
        except Exception as e:
            logger.error(f"Error saving article to DB: {e}")
    
    def ingest_batch(self, query_params=None):
        """Main ingestion method - fetch, process, and save articles"""
        logger.info("Starting MediaCloud ingestion batch")
        
        # Fetch raw articles
        raw_articles = self.fetch_articles(query_params)
        logger.info(f"Fetched {len(raw_articles)} articles from MediaCloud")
        
        processed_count = 0
        saved_count = 0
        
        for raw_article in raw_articles:
            processed_article = self.process_article(raw_article)
            
            if processed_article:
                self.save_article_to_db(processed_article)
                saved_count += 1
            
            processed_count += 1
            
            if processed_count % 100 == 0:
                logger.info(f"Processed {processed_count} articles, saved {saved_count}")
        
        logger.info(f"Ingestion complete: {processed_count} processed, {saved_count} saved")
        return {"processed": processed_count, "saved": saved_count}
