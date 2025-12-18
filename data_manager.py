import pandas as pd
import json
from sqlalchemy import create_engine, text
from newsapi import NewsApiClient
from groq import Groq
import streamlit as st

class DataManager:
    def __init__(self):
        # --- 1. CONFIG & SEED DATA (From contextual_all_intents_v2.py) ---
        self.countries = ["Senegal", "DRC", "CoteIvoire", "Ethiopia"]
        self.actors = ["China", "France", "UnitedStates", "Russia", "Rwanda", "Saudi", "Turkey", "UAE", "Israel", "Iran", "NonState"]
        
        self.GDP = {"Senegal": 33.6e9, "DRC": 70.75e9, "CoteIvoire": 86.54e9, "Ethiopia": 125.0e9}
        self.FSI_RAW = {"Senegal": 74.2, "DRC": 106.7, "CoteIvoire": 85.3, "Ethiopia": 98.1}
        self.L = {"Senegal": 0.90, "DRC": 0.20, "CoteIvoire": 0.20, "Ethiopia": 0.95}
        
        # Load Matrices from Secrets or Defaults
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

    # --- 2. CORE MATH LOGIC ---
    def clip(self, x): return max(0.0, min(1.0, float(x)))

    def get_g_factors(self, a, c):
        """Computes the current g-values for a specific actor-country pair"""
        # Debt
        debt = self.DEBT.get(a, {}).get(c, 0.0)
        g_debt = self.clip(debt / self.GDP[c]) if c in self.GDP else 0.0
        
        # Elec (simplified time logic)
        months_to_elec = 3 if c == "CoteIvoire" else 999
        g_elec_time = 1 - min(months_to_elec, 24)/24 if months_to_elec < 999 else 0.25
        g_elec = self.ACTOR_ELEC.get(a, {}).get(c, 0.0) * g_elec_time
        
        # LGBTQ & Fragility
        g_lgbt = (1 - self.L.get(c, 0.5)) * self.ACTOR_LGBTQ.get(a, {}).get(c, 0.0)
        fsi_norm = self.clip((self.FSI_RAW.get(c, 70) - 22.0) / (120.0 - 22.0))
        g_frag = fsi_norm * self.ACTOR_DISINFO.get(a, {}).get(c, 0.0)

        return {
            "debt": g_debt,
            "res": self.G_RES.get(a, {}).get(c, 0.0),
            "mil": self.G_MIL.get(a, {}).get(c, 0.0),
            "elec": g_elec,
            "lgbt": g_lgbt,
            "frag": g_frag
        }

    def calculate_v2_risk(self, a, c, intent):
        """Implementation of CA calculation and FinalRisk formula"""
        if a not in self.actors or c not in self.countries:
            return 0.45 # Baseline for unknown pairs

        g = self.get_g_factors(a, c)
        factors = self.INTENT_FACTORS.get(intent, ["debt", "res"])
        
        # Simple weighted average for the dashboard (relative R-factors are 
        # complex for single articles, so we use normalized G-values)
        ca_score = sum(g[f] for f in factors) / len(factors)
        
        # Final Formula: avg_base + (1 - avg_base) * ca
        avg_base = 0.40
        final_risk = avg_base + (1.0 - avg_base) * ca_score
        return round(self.clip(final_risk), 2)

    # --- 3. PIPELINE ---
    def update_news(self):
        query_str = f"({' OR '.join(self.countries)}) AND ({' OR '.join(self.actors[:5])})"
        raw_news = self.newsapi.get_everything(q=query_str, language='en', sort_by='publishedAt', page_size=10)
        
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
            except Exception as e:
                print(f"DB Error: {e}")
        return count

    def extract_tags(self, title, desc):
        prompt = f"Analyze news: {title}. Return JSON with actor, country, intent, summary. Use exact names from: {self.actors} and {self.countries}."
        try:
            res = self.groq.chat.completions.create(
                messages=[{"role":"user","content":prompt}], 
                model="llama-3.3-70b-versatile", 
                response_format={"type":"json_object"}
            )
            return json.loads(res.choices[0].message.content)
        except:
            return {"actor":"General", "country":"General", "intent":"Economic", "summary":"Analysis timed out."}

    def fetch_articles(self, limit=10):
        query = text("SELECT * FROM articles ORDER BY published_at DESC LIMIT :l")
        with self.engine.connect() as conn:
            return pd.read_sql(query, conn, params={"l": limit})
