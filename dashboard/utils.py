# dashboard/utils.py
# ContextualRisk has been deprecated in favor of VulnerabilityIndex.
from .models import VulnerabilityIndex

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

def calculate_contextual_score(target_country, foreign_actor, intent_filter=None):
    """
    Replaces the old ContextualRisk lookup.

    1. Normalizes input (Mappings).
    2. Queries VulnerabilityIndex (DB-driven scores).
    3. Returns score and intent.
    """

    # These mappings must match the values stored in VulnerabilityIndex
    # (which are imported from final_risk_by_actor_intent_country.csv).
    country_mapping = {
        "côte d'ivoire": "CoteIvoire",
        "cote d'ivoire": "CoteIvoire",
        "cote ivoire": "CoteIvoire",
        "ivory coast": "CoteIvoire",
        "coteivoire": "CoteIvoire",
        "southafrica": "South Africa",
        "south africa": "South Africa",
        "senegal": "Senegal",
        "drc": "DRC",
        "ethiopia": "Ethiopia",
    }

    actor_mapping = {
        "us": "UnitedStates",
        "united states": "UnitedStates",
        "unitedstates": "UnitedStates",
        "usa": "UnitedStates",

        "saudi": "Saudi",
        "saudi arabia": "Saudi",

        "uae": "UAE",
        "china": "China",
        "france": "France",
        "russia": "Russia",
        "turkey": "Turkey",
        "israel": "Israel",
        "iran": "Iran",
        "rwanda": "Rwanda",
        "nonstate": "NonState",
        "non-state": "NonState",
    }

    c_term = str(target_country).lower().strip() if target_country else ""
    a_term = str(foreign_actor).lower().strip() if foreign_actor else ""

    db_country = country_mapping.get(c_term, target_country)
    db_actor = actor_mapping.get(a_term, foreign_actor)

    query = VulnerabilityIndex.objects.filter(
        country__iexact=db_country,
        actor__iexact=db_actor,
    )

    # If the user selected an intent in the dropdown, filter by it
    if intent_filter and intent_filter != "All":
        query = query.filter(intent__iexact=intent_filter)

    match = query.order_by('-final_risk').first()

    if match:
        return round(float(match.final_risk), 4), match.intent

    return 0.0, "No Risk Data Found"
