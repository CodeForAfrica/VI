import torch
import os
from transformers import AutoTokenizer, AutoModelForSequenceClassification
from django.core.management.base import BaseCommand
from django.db import transaction, connection
from dashboard.models import MediaNarrative
from dashboard.management.commands.update_vulnerability_indexes import map_raw_intent_to_contextual

# --- Local Paths ---
STRATEGIC_MODEL_PATH = "/Users/hannateshager/Vulnerability_index_tool/model_cache/strategic_model"
TONE_MODEL_PATH = "/Users/hannateshager/Vulnerability_index_tool/model_cache/tone_model"
BASE_MODEL_PATH = "/Users/hannateshager/Vulnerability_index_tool/microsoft_mdeberta-v3-base"

class Command(BaseCommand):
    help = 'Runs high-speed local inference using MPS'

    def handle(self, *args, **options):
        # A. Detect Mac GPU (MPS)
        device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
        self.stdout.write(f"🚀 Using Device: {device}")

        # B. Load Models
        self.stdout.write("📦 Loading models from local paths...")
        tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL_PATH)
        strategic_model = AutoModelForSequenceClassification.from_pretrained(STRATEGIC_MODEL_PATH).to(device)
        tone_model = AutoModelForSequenceClassification.from_pretrained(TONE_MODEL_PATH).to(device)
        
        strategic_model.eval()
        tone_model.eval()

        # C. Fetch Articles
        articles_query = MediaNarrative.objects.filter(
            strategic_intent__isnull=True
        ).exclude(article_text='')

        total = articles_query.count()
        self.stdout.write(self.style.SUCCESS(f"🧐 Found {total} articles to process."))

        results_to_update = []
        batch_size = 100

        # D. Processing Loop
        for i, article in enumerate(articles_query):
            try:
                inputs = tokenizer(article.article_text, return_tensors="pt", truncation=True, max_length=512).to(device)
                
                with torch.no_grad():
                    # 1. Strategic Intent
                    strat_outputs = strategic_model(**inputs)
                    strat_probs = torch.nn.functional.softmax(strat_outputs.logits, dim=-1)
                    strat_idx = torch.argmax(strat_probs).item()
                    raw_intent_label = strategic_model.config.id2label[strat_idx]
                    
                    # 2. Tone
                    tone_outputs = tone_model(**inputs)
                    tone_probs = torch.nn.functional.softmax(tone_outputs.logits, dim=-1)
                    tone_idx = torch.argmax(tone_probs).item()
                    tone_label = tone_model.config.id2label[tone_idx]

                # E. Funneling
                final_intent = map_raw_intent_to_contextual(raw_intent_label) or "SocialFragility"

                article.strategic_intent = final_intent
                article.tone = tone_label
                article.confidence = float(torch.max(strat_probs).item())
                article.prediction_source = "local_mdeberta_mps"

                results_to_update.append(article)

                # F. Batch Save
                if len(results_to_update) >= batch_size:
                    with transaction.atomic():
                        MediaNarrative.objects.bulk_update(
                            results_to_update, 
                            ['strategic_intent', 'tone', 'confidence', 'prediction_source']
                        )
                    self.stdout.write(f"📈 Progress: {i+1}/{total} saved...")
                    results_to_update = []
                    connection.close_if_unusable_or_obsolete()

            except Exception as e:
                self.stderr.write(f"❌ ID {article.id} failed: {e}")

        # Final Save
        if results_to_update:
            MediaNarrative.objects.bulk_update(results_to_update, ['strategic_intent', 'tone', 'confidence', 'prediction_source'])
        
        self.stdout.write(self.style.SUCCESS("🎉 Finished local inference run!"))
