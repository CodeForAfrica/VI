import pandas as pd
import requests
import psycopg2
from sqlalchemy import create_engine
import google.generativeai as genai
import streamlit as st
from contextual_all_intents_v2 import compute_gs, compute_R, compute_CAs

# --- CONFIGURATION ---
# Use Streamlit Secrets for deployment
NEWS_API_KEY = st.secrets["NEWS_API_KEY"]
GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
DB_URL = st.secrets["DB_URL"] # format: postgresql://user:pass@host:port/dbname

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-1.5-flash')

class NarrativeIntelligence:
    def __init__(self):
        self.engine = create_engine(DB_URL)
        # Pre-compute Contextual Influence Map from your script
        g = compute_gs()
        r = compute_R(g)
        self.ca_map = compute_CAs(g, r)

    def fetch_live_news(self, query="Foreign Influence Africa"):
        """Fetches from NewsAPI and saves to DB."""
        url = f"https://newsapi.org/v2/everything?q={query}&apiKey={NEWS_API_KEY}&pageSize=10"
        r = requests.get(url).json()
        articles = r.get('articles', [])
        
        for a in articles:
            # Check if exists to avoid duplicates
            with self.engine.connect() as conn:
                exists = conn.execute(f"SELECT 1 FROM articles WHERE url = '{a['url']}'").fetchone()
                if not exists:
                    # Initial insert with placeholders; LLM will enrich later
                    df = pd.DataFrame([{
                        'url': a['url'], 'title': a['title'], 'image_url': a['urlToImage'],
                        'published_at': a['publishedAt'], 'source': a['source']['name'],
                        'raw_text': a['description'] or a['title']
                    }])
                    df.to_sql('articles', self.engine, if_exists='append', index=False)

    def enrich_with_llm(self, text):
        """Classifies tone and intent as requested."""
        prompt = f"""
        Analyze this news snippet: "{text}"
        Return ONLY a JSON with these keys:
        - summary: 2-sentence clean summary.
        - tone: Exactly one of [Alarmist, Factual, Cynical, Sensationalist]
        - intent: Exactly one of [Economic, Sovereignty, LGBTQ, Religious, ElectionInfluence, MilitaryPresence, ResourceDependency, SocialFragility]
        - actor: The foreign country/entity exerting influence.
        - target_country: The African country being influenced.
        """
        response = model.generate_content(prompt)
        try:
            return eval(response.text.replace("```json", "").replace("```", ""))
        except:
            return {"tone": "Factual", "summary": text[:100], "actor": "Unknown", "target_country": "Global", "intent": "SocialFragility"}

    def get_influence_score(self, actor, country, intent):
        """Pulls score from your contextual_all_intents logic."""
        try:
            score = self.ca_map[intent][actor][country]
            return round(score, 2)
        except:
            return 0.45 # Default middle-ground risk

    def get_display_data(self, limit=6, offset=0):
        """Fetch 6 articles for the current page."""
        query = f"SELECT * FROM articles ORDER BY published_at DESC LIMIT {limit} OFFSET {offset}"
        return pd.read_sql(query, self.engine)
