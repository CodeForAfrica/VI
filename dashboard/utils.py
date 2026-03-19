# dashboard/utils.py
from .models import ContextualRisk

# The mapping dictionary for your intents
intent_mapping = {
    "economic": "Economic", "sovereignty": "Sovereignty",
    "lgbtq": "LGBTQ", "religious": "Religious",
    "electioninfluence": "ElectionInfluence", "militarypresence": "MilitaryPresence",
    "resourcedependency": "ResourceDependency", "socialfragility": "SocialFragility",
    "economic dependency": "Economic", "sovereignty erosion": "Sovereignty",
    "lgbtq rights": "LGBTQ", "election interference": "ElectionInfluence",
    "military presence": "MilitaryPresence", "resource control": "ResourceDependency",
    "social fragility": "SocialFragility", "debt trap diplomacy": "Economic",
}

def map_to_canonical_intent(stored_intent_str, article_title=""):
    """Normalizes raw intent strings into canonical database categories."""
    if not stored_intent_str:
        return None

    normalized_input = stored_intent_str.strip().lower()
    canonical = intent_mapping.get(normalized_input)
    
    if canonical:
        return canonical

    # Keyword recovery logic for 'Other' intents using the article title
    if (normalized_input == 'other' or not canonical) and article_title:
        title_l = article_title.lower()
        if any(w in title_l for w in ['trade', 'investment', 'debt', 'economy']): return "Economic"
        if any(w in title_l for w in ['military', 'security', 'defense', 'army']): return "MilitaryPresence"
        if any(w in title_l for w in ['election', 'vote', 'policy', 'government']): return "ElectionInfluence"
            
    return None

def calculate_contextual_score(target_country, foreign_actor, intent_filter=None):
    """Queries the ContextualRisk model for a specific baseline score."""
    country_mapping = {
        "côte d'ivoire": "Côte d'Ivoire", "cote d'ivoire": "Côte d'Ivoire", 
        "ivory coast": "Côte d'Ivoire", "coteivoire": "Côte d'Ivoire",
        "south africa": "South Africa", "senegal": "Senegal", 
        "drc": "DRC", "ethiopia": "Ethiopia",
    }
    actor_mapping = {
        "uae": "UAE", "china": "China", "france": "France", "us": "US",
        "united states": "US", "russia": "Russia", "saudi": "Saudi Arabia",
        "turkey": "Turkey", "israel": "Israel", "iran": "Iran", "rwanda": "Rwanda",
    }

    c_term = target_country.lower().strip() if target_country else ""
    a_term = foreign_actor.lower().strip() if foreign_actor else ""
    
    db_country = country_mapping.get(c_term, target_country)
    db_actor = actor_mapping.get(a_term, foreign_actor)

    query = ContextualRisk.objects.filter(
        country__iexact=db_country, 
        actor__iexact=db_actor
    )
    
    if intent_filter and intent_filter != "All":
        query = query.filter(intent__iexact=intent_filter)
    
    match = query.order_by('-risk_score').first()
    
    if match:
        return round(float(match.risk_score), 4), match.intent
        
    return 0.0, "No Risk Data Found"
