import os
import mediacloud.api
from datetime import date

# Get the API key from the environment
api_key = os.getenv('MEDIACLOUD_API_KEY')

if not api_key:
    print("ERROR: MEDIACLOUD_API_KEY environment variable not set.")
else:
    print("API Key found in environment.")
    try:
        # Initialize the API client
        mc = mediacloud.api.SearchApi(api_key)
        print("MediaCloud API client initialized successfully.")

        # --- Use the specific query structure ---
        ACTOR_COLLECTION_IDS = {
            "USA": 34412234, # This worked previously (found 0 stories)
            "China": 34412193, # This failed previously (JSONDecodeError)
        }

        TARGET_COUNTRY = "Ethiopia" # Test with one target country

        ACTOR_TERMS = {
            "USA": ["United States", "USA", "US", "America", "Biden", "Washington", "American", "US Embassy"],
            "China": ["China", "Chinese", "Beijing", "Xi Jinping", "PR Embassy", "中", "中国"],
            # Add other actors if needed later
        }

        TARGET_TERMS = {
            "Ethiopia": ["Ethiopia", "Ethiopian", "Addis Ababa", "Abiy Ahmed", "Tigray", "Oromo", "Amhara", "EPRDF", "TPLF", "የአፍሪካ ቀንድ", "ኢትዮጵያ", "አዲስ አበባ"],
            # Add other targets if needed later
        }

        INFLUENCE_KEYWORDS = {
            "Economic": [
                "investment", "debt relief", "loan", "trade agreement", "mining contract", "mining rights", "economic partnership",
                "financial aid", "development finance", "Belt AND Road", "BRI", "Silk Road", "digital Silk Road",
                "resource dependency", "land lease", "agricultural cooperation", "cocoa", "uranium", "cobalt", "copper", "oil",
                "infrastructure project", "railway", "road", "port", "airport", "power plant", "hydroelectric", "hydropower",
                "industrial park", "special economic zone", "manufacturing", "energy project", "renewable energy", "solar", "wind"
            ],
            "MilitarySecurity": [
                "military cooperation", "arms sale", "weapons deal", "defense pact", "peacekeeping", "security partnership",
                "military base", "troop deployment", "training mission", "intelligence sharing", "naval cooperation",
                "joint exercises", "anti-terrorism", "counter-terrorism", "militia support", "proxy warfare", "mercenary"
            ],
            "PoliticalDiplomatic": [
                "diplomatic relations", "embassy", "consulate", "state visit", "high-level meeting", "diplomatic recognition",
                "election interference", "electoral support", "governance model", "anti-corruption", "rule of law", "democracy",
                "human rights", "civil society", "political party", "lobbying", "influence campaign", "soft power"
            ],
            "CulturalEducational": [
                "Confucius Institute", "cultural exchange", "language school", "scholarship program", "student exchange",
                "academic cooperation", "research partnership", "cultural diplomacy", "art exhibition", "film festival",
                "book donation", "library", "educational cooperation", "university partnership", "alumni network"
            ],
            "TechnologySurveillance": [
                "technology transfer", "telecom cooperation", "5G", "Huawei", "ZTE", "AI", "artificial intelligence",
                "surveillance technology", "spyware", "cybersecurity", "data security", "digital infrastructure",
                "internet governance", "social media platform", "vaccination campaign", "health initiative",
                "hospital construction", "medical aid", "pandemic response", "public health"
            ],
            "InformationNarrative": [
                "media cooperation", "journalist training", "news agency", "propaganda", "disinformation", "fake news",
                "narrative shaping", "public opinion", "social media campaign", "influencer", "blog", "podcast", "radio station",
                "television channel", "broadcast", "Amharic", "local language", "press freedom", "media ownership"
            ],
": [
                "religious diplomacy", "interfaith dialogue", "religious institution", "mosque", "church", "temple",
                "religious leader", "faith-based organization", "religious minority", "religious freedom", "atheism",
                "secularism", "religious law", "Sharia", "Halakha", "orthodoxy", "sectarianism", "religious extremism",
                "religious moderation", "religious tolerance"
            ]
        }

        def build_query(actor, target):
            """
            Builds a query string for a specific actor-target pair
            using predefined terms.
            """
            actor_terms = ACTOR_TERMS.get(actor, [])
            target_terms = TARGET_TERMS.get(target, [])

            if not actor_terms or not target_terms:
                print(f"Warning: Missing terms for actor '{actor}' or target '{target}'. Skipping query.")
                return None

            target_phrase = "(" + " OR ".join(target_terms) + ")"
            actor_phrase = "(" + " OR ".join(actor_terms) + ")"
            core_phrase = f"({target_phrase} AND {actor all_influence_keywords = []
            for category_keywords in INFLUENCE_KEYWORDS.values():
                all_influence_keywords.extend(category_keywords)

            influence_phrase = "(" + " OR ".join(all_influence_keywords) + ")"
            final_query = f"({core_phrase} AND {influence_phrase})"
            return final_query

        # --- Test Loop ---
        START_DATE = date(2026, 1, 1)
        END_DATE = date(2026, 3, 11)

        for actor_name, actor_coll_id in ACTOR_COLLECTION_IDS.items():
            print(f"\n--- Testing {actor_name} Collection ({actor_coll_id}) for {TARGET_COUNTRY} ---")
            base_query = build_query(actor_name, TARGET_COUNTRY)

            if not base_query:
                print(f"  Skipping query for {actor_name} -> {TARGET_COUNTRY} due to missing terms.")
                continue

            print(f"  Generated Query: {base_query[:150]}...") # Print first 150 chars of query

            try:
                # Call the API with the specific query and collection ID
                result = mc.story_list(
                    query=base_query,
                    start_date=START_DATE,  # Use date object
                    end_date=END_DATE,      # Use date object
                    collection_ids=[actor_coll_id]
                )

                print(f"  Type of result: {type(result)}")
                if isinstance(result, tuple) and len(result) == 2:
                    stories, count = result
                    print(f"  Number of stories returned: {len(stories)}")
                    print(f"  API reported total count: {count}")
                    if stories:
                         print(f"  First story keys: {list(stories[0].keys()) if isinstance(stories[0], dict) else 'Not a dict'}")
                else:
                    print(f"  Result is not the expected (stories_list, count) tuple.")

            except Exception as e:
                print(f"  API Error for {actor_name} (ID {actor_coll_id}) searching for {TARGET_COUNTRY}: {e}")
                print(f"    Exception type: {type(e).__name__}")
                # Attempt to print status code if available (requires catching lower-level exception)
                if hasattr(e, 'response'):
                    print(f"    Response status code: {e.response.status_code}")
                    print(f"    Response text (first 200 chars): {e.response.text[:200]}")

    except Exception as e:
        print(f"Error during API initialization: {e}")
        print(f"Exception type: {type(e).__name__}")
