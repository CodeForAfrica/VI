### for strategic_intent.py
import os
import time
import json
import re
import logging
import numpy as np
from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.conf import settings
from dashboard.models import MediaNarrative
from dashboard.services.ml_inference_service import get_ml_service

try:
    from groq import Groq
    have_groq = True
except ImportError:
    have_groq = False

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Run LLM and model strategic intent inference, merge results, and update database."

    def add_arguments(self, parser):
        parser.add_argument(
            '--batch-size',
            type=int,
            default=500,
            help='Number of records to update per bulk database operation.'
        )
        parser.add_argument(
            '--llm-only',
            action='store_true',
            help='Only run LLM pseudo‑labeling, skip model inference.'
        )
        parser.add_argument(
            '--model-only',
            action='store_true',
            help='Only run model inference, skip LLM.'
        )

    def handle(self, *args, **options):
        batch_size = options['batch_size']
        llm_only = options['llm_only']
        model_only = options['model_only']

        self.stdout.write("Initializing ML Inference Service...")
        ml_service = get_ml_service()

        # 1. Fetch articles that need processing (strategic_intent is null)
        queryset = MediaNarrative.objects.filter(
            strategic_intent__isnull=True,
            article_text__isnull=False
        ).exclude(article_text='')

        total = queryset.count()
        if total == 0:
            self.stdout.write(self.style.SUCCESS("No articles to process."))
            return

        self.stdout.write(self.style.SUCCESS(f"Found {total} articles to process."))

        # 2. Load all required fields into memory
        fields = ['id', 'article_text', 'llm_strat', 'llm_strat_conf']
        articles_data = list(queryset.values(*fields))

        # 3. LLM pseudo‑labeling (if not skipped)
        if have_groq and not model_only:
            self.stdout.write("Starting Groq pseudo‑labeling...")
            api_key = getattr(settings, 'GROQ_API_KEY', os.environ.get('GROQ_API_KEY'))
            if not api_key:
                self.stderr.write(self.style.ERROR("GROQ_API_KEY not set. Skipping LLM."))
            else:
                client = Groq(api_key=api_key)
                system_message = {
                    "role": "system",
                    "content": (
                        "Generate ONLY ONE short label for strategic_intent and a numeric confidence in [0,1]. "
                        "by strategic intent we mean the influence intent (Economic Dependency, Sovereignty Erosion, etc...) "
                        "that emerges from the text and is strategically used to influence the target country in the text. "
                        "Return JSON only with keys: 'strategic_intent','strategic_intent_conf','notes'."
                    )
                }
                for idx, article in enumerate(articles_data):
                    if article.get('llm_strat') is not None:
                        continue
                    text = article['article_text'][:4000]
                    try:
                        response = client.chat.completions.create(
                            model="meta-llama/llama-4-scout-17b-16e-instruct",
                            messages=[system_message, {"role": "user", "content": text}],
                            temperature=0.0
                        )
                        content = response.choices[0].message.content.strip()
                        match = re.search(r'\{.*\}', content, re.DOTALL)
                        if match:
                            data = json.loads(match.group())
                            article['llm_strat'] = data.get('strategic_intent')
                            article['llm_strat_conf'] = float(data.get('strategic_intent_conf', 0.0))
                        else:
                            self.stderr.write(f"Could not parse LLM response: {content[:200]}")
                    except Exception as e:
                        self.stderr.write(f"Groq error on article {article['id']}: {e}")
                    time.sleep(0.15)
                    if (idx + 1) % 50 == 0:
                        self.stdout.write(f"  LLM processed {idx + 1}/{total}")
            self.stdout.write("LLM labeling complete.")
        elif not have_groq:
            self.stdout.write("Groq not installed; skipping LLM.")

        # 4. Model inference (if not skipped)
        if not llm_only:
            self.stdout.write("Loading strategic intent model...")
            classifier = ml_service._load_strategic_classifier()
            label_encoder = ml_service._strategic_label_encoder

            texts = [a['article_text'] for a in articles_data]
            self.stdout.write(f"Running model inference on {len(texts)} texts...")
            model_preds, model_probs = classifier.predict(
                texts=texts,
                batch_size=8,
                calibrated=True,
                return_probs=True
            )
            model_labels = label_encoder.inverse_transform(model_preds)
            model_confidences = np.max(model_probs, axis=1)

            for i, article in enumerate(articles_data):
                article['model_strat'] = model_labels[i]
                article['model_conf'] = float(model_confidences[i])
            self.stdout.write("Model inference complete.")

        # 5. Merge LLM and model predictions (if both present)
        for article in articles_data:
            model_label = article.get('model_strat')
            model_conf = article.get('model_conf', 0.0)
            llm_label = article.get('llm_strat')
            llm_conf = article.get('llm_strat_conf', 0.0)

            if model_label is not None and llm_label is not None:
                if model_label == llm_label:
                    # Agreement – use higher confidence
                    if model_conf >= llm_conf:
                        final_label = model_label
                        final_conf = model_conf
                        source = 'both'
                    else:
                        final_label = llm_label
                        final_conf = llm_conf
                        source = 'both'
                else:
                    # Disagreement – use higher confidence
                    if model_conf >= llm_conf:
                        final_label = model_label
                        final_conf = model_conf
                        source = 'model'
                    else:
                        final_label = llm_label
                        final_conf = llm_conf
                        source = 'llm'
            elif model_label is not None:
                final_label = model_label
                final_conf = model_conf
                source = 'model'
            elif llm_label is not None:
                final_label = llm_label
                final_conf = llm_conf
                source = 'llm'
            else:
                continue  # no prediction

            article['final_strategic_intent'] = final_label
            article['final_confidence'] = final_conf
            article['final_source'] = source

        # 6. Tone inference using the service
        self.stdout.write("Running tone inference...")
        tone_classifier = ml_service._load_tone_classifier()
        if tone_classifier:
            texts = [a['article_text'] for a in articles_data]
            tone_probs = tone_classifier.predict_proba(texts, calibrated=True, batch_size=8)
            tone_pred_indices = np.argmax(tone_probs, axis=1)
            tone_labels = ml_service._tone_label_encoder.inverse_transform(tone_pred_indices)
            for i, article in enumerate(articles_data):
                article['tone'] = tone_labels[i]
        else:
            self.stderr.write("Tone classifier not available; tone will remain unchanged.")

        # 7. Bulk update database
        self.stdout.write(f"Updating database in batches of {batch_size}...")
        objects_to_update = []
        for article in articles_data:
            if 'final_strategic_intent' not in article:
                continue
            obj = MediaNarrative(
                id=article['id'],
                strategic_intent=article['final_strategic_intent'],
                confidence=article['final_confidence'],
                prediction_source=article['final_source'],
                tone=article.get('tone'),
            )
            objects_to_update.append(obj)

            if len(objects_to_update) >= batch_size:
                with transaction.atomic():
                    MediaNarrative.objects.bulk_update(
                        objects_to_update,
                        ['strategic_intent', 'confidence', 'prediction_source', 'tone']
                    )
                self.stdout.write(f"  Updated {len(objects_to_update)} records.")
                objects_to_update = []

        if objects_to_update:
            with transaction.atomic():
                MediaNarrative.objects.bulk_update(
                    objects_to_update,
                    ['strategic_intent', 'confidence', 'prediction_source', 'tone']
                )
            self.stdout.write(f"  Updated final {len(objects_to_update)} records.")

        self.stdout.write(self.style.SUCCESS("Strategic intent update complete."))
        ml_service.cleanup()
