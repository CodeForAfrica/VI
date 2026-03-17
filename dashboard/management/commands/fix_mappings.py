from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q
from dashboard.models import MediaNarrative

class Command(BaseCommand):
    help = 'Updates strategic_intent labels only, without touching vulnerability_index'

    def handle(self, *args, **options):
        # 1. THE COMPLETE MAPPING DICTIONARY
        intent_mapping = {
            # Economic
            "economic": "Economic",
            "economic dependency": "Economic",
            "trade dominance": "Economic",
            "economic impact": "Economic",
            "debt trap diplomacy": "Economic",
            # Sovereignty
            "sovereignty": "Sovereignty",
            "sovereignty erosion": "Sovereignty",
            "reputation damage": "Sovereignty",
            # Social Fragility
            "socialfragility": "SocialFragility", 
            "social fragility": "SocialFragility", 
            "destabilization": "SocialFragility",
            "migration control": "SocialFragility",
            "political destabilization": "SocialFragility",
            "health project": "SocialFragility",
            "medical": "SocialFragility",
            # Others
            "lgbtq": "LGBTQ",
            "religious": "Religious",
            "electioninfluence": "ElectionInfluence",
            "militarypresence": "MilitaryPresence",
            "resourcedependency": "ResourceDependency",
        }

        # 2. HALLUCINATION KEYWORDS
        general_keywords = [
            # Nature & Science (The Crocodile Fix)
            'crocodile', 'fossil', 'archaeology', 'dinosaur', 'astronomy', 
            'species', 'wildlife', 'biodiversity', 'discovery', 'space', 'galaxy',
            # Sports
            'football', 'soccer', 'olympics', 'match', 'tournament', 'player', 'scored',
            # Entertainment & Lifestyle
            'celebrity', 'hollywood', 'movie', 'actor', 'fashion', 'music', 'concert',
            # General History/Non-Strategic
            'prehistoric', 'ancient', 'artifact', 'museum', 'exhibition'
        ]

        self.stdout.write(self.style.SUCCESS("🚀 Starting Intent-Only Cleanup..."))

        # --- STEP A: Clean Hallucinations ---
        hall_query = Q()
        for word in general_keywords:
            hall_query |= Q(article_text__icontains=word)
        
        hallucinated = MediaNarrative.objects.filter(hall_query)
        if hallucinated.exists():
            h_count = hallucinated.count()
            hallucinated.update(strategic_intent="General News")
            self.stdout.write(self.style.WARNING(f"🐊 Cleaned {h_count} Science records to 'General News'."))

        # --- STEP B: Apply Intent Mappings ---
        total_fixed = 0
        for wrong_val, correct_val in intent_mapping.items():
            # Use update() directly for speed and to ensure vulnerability_index isn't touched
            articles = MediaNarrative.objects.filter(
                Q(strategic_intent__iexact=wrong_val) | 
                (Q(article_text__icontains=wrong_val) if wrong_val in ["medical", "health project"] else Q(pk__in=[]))
            ).exclude(strategic_intent=correct_val)

            count = articles.count()
            if count > 0:
                articles.update(strategic_intent=correct_val)
                self.stdout.write(f"🛠️ Fixed {count} records: '{wrong_val}' -> '{correct_val}'")
                total_fixed += count

        self.stdout.write(self.style.SUCCESS(f"🎉 Done! {total_fixed} intents updated. RDS Risk scores remain unchanged."))
