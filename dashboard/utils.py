# dashboard/utils.py
from .models import ContextualRisk

# The mapping dictionary for your intents
intent_mapping = {
    # Direct matches (if ML outputs match CSV exactly)
    "economic": "Economic",
    "sovereignty": "Sovereignty",
    "lgbtq": "LGBTQ",
    "religious": "Religious",
    "electioninfluence": "ElectionInfluence",
    "militarypresence": "MilitaryPresence",
    "resourcedependency": "ResourceDependency",
    "socialfragility": "SocialFragility",

    # Common variations/spelling from ML model output (case-insensitive)
    "economic dependency": "Economic",
    "sovereignty erosion": "Sovereignty",
    "sovereignty threat": "Sovereignty",
    "lgbtq rights": "LGBTQ",
    "lgbt advocacy": "LGBTQ",
    "religious influence": "Religious",
    "religious polarisation": "Religious",
    "election influence": "ElectionInfluence",
    "election interference": "ElectionInfluence",
    "electoral interference": "ElectionInfluence",
    "military presence": "MilitaryPresence",
    "military base": "MilitaryPresence",
    "resource dependency": "ResourceDependency",
    "resource control": "ResourceDependency",
    "social fragility": "SocialFragility",
    "social unrest": "SocialFragility",
    "information warfare": "SocialFragility",
    "human rights advocacy": "LGBTQ",
    "debt trap diplomacy": "Economic",
    "cultural influence": "SocialFragility",
    "centralization of power": "Sovereignty",
    "cultural exchange": "Economic",
    "cultural hegemony": "Sovereignty",
    "democratic interference": "ElectionInfluence",
    "diplomatic cooperation": "Economic",
    "diplomatic influence": "Sovereignty",
}

def map_to_canonical_intent(stored_intent_str, article_title=""):
    """
    Normalizes raw intent strings into canonical database categories.
    Returns None if no match is found to maintain data purity.
    """
    if not stored_intent_str:
        return None

    normalized_input = stored_intent_str.strip().lower()

    # Check match in the global intent_mapping dict
    canonical = intent_mapping.get(normalized_input)
    if canonical:
        return canonical

    # Recovery Logic for 'Other' using title keywords
    # 2. Recovery Logic for 'Other' or Unmapped Intents using title keywords
    if (normalized_input == 'other' or not canonical) and article_title:
        title_l = article_title.lower()
        
        # --- Economic ---
        if any(w in title_l for w in ['trade', 'investment', 'debt', 'economy', 'finance', 'infrastructure', 'business', 'aid', 'loan']): 
            return "Economic"
            
        # --- Military Presence ---
        if any(w in title_l for w in ['military', 'security', 'defense', 'army', 'base', 'troop', 'weapon', 'maritime', 'patrol']): 
            return "MilitaryPresence"
            
        # --- Election Influence ---
        if any(w in title_l for w in ['election', 'vote', 'policy', 'government', 'campaign', 'candidate', 'democratic', 'ballot']): 
            return "ElectionInfluence"
            
        # --- Sovereignty ---
        if any(w in title_l for w in ['sovereignty', 'border', 'territory', 'interference', 'independence', 'unilateral', 'autonomy']): 
            return "Sovereignty"
            
        # --- Resource Dependency ---
        if any(w in title_l for w in ['oil', 'gas', 'mining', 'mineral', 'gold', 'resource', 'energy', 'extract', 'commodity']): 
            return "ResourceDependency"
            
        # --- Social Fragility (Includes Religious/LGBTQ focus if not explicitly mapped) ---
        if any(w in title_l for w in ['protest', 'riot', 'unrest', 'religion', 'church', 'mosque', 'lgbt', 'rights', 'strike', 'conflict']): 
            return "SocialFragility"
    
    return None

# VULNERABILITY CALCULATION (DB-DRIVEN)

def calculate_contextual_score(target_country, foreign_actor, strategic_intent):
    # Query the database table we just created
    query = ContextualRisk.objects.filter(
        country__iexact=target_country.strip(),
        actor__iexact=foreign_actor.strip(),
        intent__iexact=strategic_intent.strip()
    )
    
    match = query.first()
    
    if match:
        return match.risk_score, match.intent
    
    return 0.0, strategic_intent
    
