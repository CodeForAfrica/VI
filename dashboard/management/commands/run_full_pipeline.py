# dashboard/management/commands/run_full_pipeline.py
from django.core.management.base import BaseCommand
from dashboard.models import Article
from dashboard.services.ml_inference_service import get_ml_service
# Import your extraction/validation logic here

class Command(BaseCommand):
    help = 'Executes the full pipeline for articles with missing data'

    def handle(self, *args, **options):
        ml_service = get_ml_service() # Initialize your service
        
        # 1. Fetch only NULL records
        articles = Article.objects.filter(full_text__isnull=True)
        
        for article in articles:
            # A. EXTRACTION
            # text = extract_article_content(article.url)
            
            # B. INFERENCE
            results = ml_service.perform_inference(text)
            
            # C. SCORING
            score = ml_service.calculate_vulnerability_index(
                results['strategic_intent'], 
                results['tone'], 
                article.target_country, 
                article.inferred_actor, 
                results['confidence']
            )
            
            # D. SAVE TO RDS (UI ready)
            article.full_text = text
            article.strategic_intent = results['strategic_intent']
            article.tone = results['tone']
            article.vulnerability_score = score
            article.save()
            
            self.stdout.write(f"Processed: {article.title} | Score: {score}")
