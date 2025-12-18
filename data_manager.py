import os
import json
import pandas as pd
import streamlit as st
from groq import Groq
from sqlalchemy import create_engine
# Import functions from your logic file
from contextual_all_intents_v2 import compute_gs, compute_R, compute_CAs

class StrategicManager:
    def __init__(self):
        # Database & API configuration from Streamlit Secrets
        self.engine = create_engine(st.secrets["DB_URL"])
        self.groq_client = Groq(api_key=st.secrets["GROQ_API_KEY"])
        
        # Pre-compute contextual influence map once
        g = compute_gs()
        r = compute_R(g)
        self.ca_map = compute_CAs(g, r)

    def get_llm_analysis(self, text):
        """Generates summary and classifies tone/intent via Groq."""
        prompt = f"""
        Analyze this article and return ONLY a JSON object:
        Article: {text[:1500]}
        Keys:
        - summary: 2-sentence clean summary.
        - tone: Exactly one of [Alarmist, Factual, Cynical, Sensationalist].
        - intent: Exactly one of [Economic, Sovereignty, LGBTQ, Religious, ElectionInfluence, MilitaryPresence, ResourceDependency, SocialFragility].
        - actor: Primary foreign actor (e.g. Russia, China).
        - country: Target African country.
        """
        try:
            chat = self.groq_client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model="llama3-70b-8192",
                response_format={"type": "json_object"}
            )
            return json.loads(chat.choices[0].message.content)
        except:
            return {"summary": "Error processing article.", "tone": "Factual", "intent": "SocialFragility", "actor": "Unknown", "country": "Global"}

    def get_contextual_score(self, actor, country, intent):
        """Look up scores based on Debt, GDP, and Military factors."""
        try:
            # Matches the INTENT_FACTORS defined in your script
            score = self.ca_map[intent][actor][country]
            return round(score, 2)
        except:
            return 0.45 # Default score for untracked actor/country pairs

    def fetch_articles(self, limit=6, offset=0):
        query = f"SELECT * FROM articles ORDER BY published_at DESC LIMIT {limit} OFFSET {offset}"
        return pd.read_sql(query, self.engine)
