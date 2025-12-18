import pandas as pd
import json
from sqlalchemy import create_engine, text
from newsapi import NewsApiClient
from groq import Groq
import streamlit as st

class DataManager:
    def __init__(self):
        # Configuration & Seed Data
        self.countries = ["Senegal", "DRC", "CoteIvoire", "Ethiopia"]
        self.actors = ["China", "France", "UnitedStates", "Russia", "Rwanda", "Saudi", "Turkey", "UAE", "Israel", "Iran", "NonState"]
        
        self.GDP = {"Senegal": 33.6e9, "DRC": 70.75e9, "CoteIvoire": 86.54e9, "Ethiopia": 125.0e9}
        self.FSI_RAW = {"Senegal": 74.2, "DRC": 106.7, "CoteIvoire": 85.3, "Ethiopia": 98.1}
        self.L = {"Senegal": 0.90, "DRC": 0.20, "CoteIvoire": 0.20, "Ethiopia": 0.95}
        
        # Load Matrices from Secrets
        self.DEBT = st.secrets.get("DEBT", {})
        self.G_RES = st.secrets.get("G_RES", {})
        self.G_MIL = st.secrets.get("G_MIL", {})
        self.ACTOR_ELEC = st.secrets.get("ACTOR_ELEC", {})
        self.ACTOR_LGBTQ = st.secrets.get("ACTOR_LGBTQ", {})
        self.ACTOR_DISINFO = st.secrets.get("ACTOR_DISINFO", {})

        self.INTENT_FACTORS = {
            "Economic": ["debt", "res"],
            "Sovereignty": ["debt", "mil", "elec"],
            "LGBTQ": ["lgbt", "elec"],
            "MilitaryPresence": ["mil", "debt"],
            "ResourceDependency": ["res", "debt"],
            "SocialFragility": ["frag", "debt", "mil"]
        }

        # Setup Connections
        try:
            self.db_url = st.secrets["DB_URL"]
            self.engine = create_engine(self.db_url)
            self.newsapi = NewsApiClient(api_key=st.secrets["NEWS_API_KEY"])
            self.groq = Groq(api_key=st.secrets["GROQ_API_KEY"])
        except Exception as e:
            st.error(f"Initialization Error: {e}")

    def calculate_v2_risk(self, a, c, intent):
        """Core math formula: avg_base + (1 - avg_base) * CA"""
        debt = self.DEBT.get(a, {}).get(c, 0.0)
        g_debt = min(1.0, debt / self.GDP.get(c, 1e10))
        g_res = self.G_RES.get(a, {}).get(c, 0.0)
        g_mil = self.G_MIL.get(a, {}).get(c, 0.0)
        
        # Mapping factors to scores
        factors_map = {"debt": g_debt, "res": g_res, "mil": g_mil, "frag": 0.5, "elec": 0.4, "lgbt": 0.3}
        factors = self.INTENT_FACTORS.get(intent, ["debt", "res"])
        ca_score = sum(factors_map.get(f, 0.0) for f in factors) / len(factors)
        
        avg_base = 0.40
        return round(max(0.0, min(1.0, avg_base + (1.0 - avg_base) * ca_score)), 2)

    def update_news(self):
        query_str = f"({' OR '.join(self.countries)}) AND (China OR Russia OR France OR Wagner)"
        raw_news = self.newsapi.get_everything(q=query_str, language='en', sort_by='publishedAt', page_size=8)
        
        count = 0
        for art in raw_news['articles']:
            facts = self.extract_tags(art['title'], art['description'])
            score = self.calculate_v2_risk(facts['actor'], facts['country'], facts['intent'])
            
            sql = text("""
                INSERT INTO articles (title, url, image_url, media_outlet, published_at, raw_text, contextual_score, actor, country, intent_type)
                VALUES (:t, :u, :i, :m, :d, :s, :sc, :actor, :country, :intent)
                ON CONFLICT (url) DO NOTHING
            """)
            try:
                with self.engine.begin() as conn:
                    conn.execute(sql, {
                        "t": art['title'], "u": art['url'], "i": art['urlToImage'],
                        "m": art['source']['name'], "d": art['publishedAt'],
                        "s": facts['summary'], "sc": score,
                        "actor": facts['actor'], "country": facts['country'], "intent": facts['intent']
                    })
                count += 1
            except: continue
        return count

    def extract_tags(self, title, desc):
        prompt = f"Extract JSON from news: {title}. Return actor, country, intent, summary. Use categories: {self.actors} and {self.countries}."
        try:
            res = self.groq.chat.completions.create(messages=[{"role":"user","content":prompt}], model="llama-3.3-70b-versatile", response_format={"type":"json_object"})
            return json.loads(res.choices[0].message.content)
        except:
            return {"actor":"General", "country":"General", "intent":"Economic", "summary":"Analysis pending."}

    @st.cache_data(ttl=600)
    def fetch_articles(_self, limit=15):
        query = text("SELECT * FROM articles ORDER BY published_at DESC LIMIT :l")
        try:
            with _self.engine.connect() as conn:
                return pd.read_sql(query, conn, params={"l": limit})
        except:
            return pd.DataFrame()
