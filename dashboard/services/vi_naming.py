"""Canonical actor and country names used by the Vulnerability Index.

Ingestion stores names such as "USA", "Saudi Arabia", "SA" and "Côte d'Ivoire",
while the contextual model and the VulnerabilityIndex table use "UnitedStates",
"Saudi", "South Africa" and "CoteIvoire". Every PPI/index computation goes
through these helpers so an article is never dropped because of spelling.
"""
import unicodedata

VI_COUNTRIES = ["Senegal", "DRC", "CoteIvoire", "Ethiopia", "South Africa"]
VI_ACTORS = ["China", "France", "UnitedStates", "Russia", "Rwanda",
             "Saudi", "Turkey", "UAE", "Israel", "Iran", "NonState"]


def _key(value):
    """Lower-case, strip accents, keep only letters and digits."""
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return "".join(ch for ch in text.lower() if ch.isalnum())


_COUNTRY_ALIASES = {
    "senegal": "Senegal",
    "drc": "DRC", "drcongo": "DRC", "democraticrepublicofthecongo": "DRC",
    "congokinshasa": "DRC",
    "coteivoire": "CoteIvoire", "cotedivoire": "CoteIvoire", "ivorycoast": "CoteIvoire",
    "ethiopia": "Ethiopia",
    "southafrica": "South Africa", "sa": "South Africa", "rsa": "South Africa",
}

_ACTOR_ALIASES = {
    "china": "China", "prc": "China",
    "france": "France",
    "unitedstates": "UnitedStates", "usa": "UnitedStates", "us": "UnitedStates",
    "unitedstatesofamerica": "UnitedStates",
    "russia": "Russia", "russianfederation": "Russia",
    "rwanda": "Rwanda",
    "saudi": "Saudi", "saudiarabia": "Saudi", "ksa": "Saudi",
    "turkey": "Turkey", "turkiye": "Turkey",
    "uae": "UAE", "unitedarabemirates": "UAE",
    "israel": "Israel",
    "iran": "Iran",
    "nonstate": "NonState", "nonstateactor": "NonState",
}


def normalize_country(value):
    """Return the canonical VI country name, or None if not a VI country."""
    return _COUNTRY_ALIASES.get(_key(value))


def normalize_actor(value):
    """Return the canonical VI actor name, or None if not a VI actor."""
    return _ACTOR_ALIASES.get(_key(value))
