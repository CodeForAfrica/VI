import pandas as pd
import json
from sqlalchemy import create_engine, text
from newsapi import NewsApiClient
from groq import Groq
import streamlit as st

class DataManager:
    def __init__(self):
        # 1. Exact Constants from your contextual_all_intent.py
        self.GDP = {"Senegal":33.6e9, "DRC":70.75e9, "CoteIvoire":86.54e9, "Ethiopia":125.0e9}
        self.FSI_NORM = {"Senegal":0.618, "DRC":0.889, "CoteIvoire":0.711, "Ethiopia":0.845} 
        self.L_ENFORCEMENT = {"Senegal":0.90, "DRC":0.20, "CoteIvoire":0.20, "Ethiopia":0.95}

        # Debt, Resource, and Military Matrices (Pulled from Streamlit Secrets)
        self.DEBT_MATRIX = st.secrets.get("DEBT_MATRIX", {}) 
        self.G_RES_MATRIX = st.secrets.get("G_RES_MATRIX", {})
        self.G_MIL_MATRIX = st.secrets.get("G_MIL_MATRIX", {})
        
        # Connection Setup
        try:
            self.db_url = st.secrets["DB_URL"]
            self.engine = create_engine(self.db_url)
            self.newsapi = NewsApiClient(api_key=st.secrets["NEWS_API_KEY"])
            self.groq = Groq(api_key=st.secrets["GROQ_API_KEY"])
        except Exception as e:
            st.error(f"Initialization Error: {e}")

    def calculate_ca_score(self, actor, country, intent_type):
        """The core math from your script: CA = sum(w[f]*g[a][c][f])"""
        # 1. Helper: Presence Factor
        # Simplified: If they have debt, resources, or military, presence is 1.0
        presence = 1.0 if (actor in self.DEBT_MATRIX and country in self.DEBT_MATRIX.get(actor, {})) else 0.5
        
        # 2. Compute G-Factors for this specific news event
        # g_debt
        debt_amt = self.DEBT_MATRIX.get(actor, {}).get(country, 0.0)
        g_debt = min(1.0, debt_amt / self.GDP.get(country, 1e10))
        
        # g_res & g_mil
        g_res = self.G_RES_MATRIX.get(actor, {}).get(country, 0.0)
        g_mil = self.G_MIL_MATRIX.get(actor, {}).get(country, 0.0)
        
        # g_lgbt (using your formula: (1-L) * index)
        g_lgbt = (1 - self.L_ENFORCEMENT.get(country, 0.5)) * 0.5 
        
        # 3. Intent Weighting (Matches your INTENT_FACTORS)
        intents = {
            "Economic": {"factors": [g_debt, g_res], "weights": [0.6, 0.4]},
            "MilitaryPresence": {"factors": [g_mil, g_debt], "weights": [0.7, 0.3]},
            "SocialFragility": {"factors": [self.FSI_NORM.get(country, 0.5), g_debt], "weights": [0.5, 0.5]}
        }
        
        data = intents.get(intent_type, intents["Economic"])
        # Final CA Calculation
        ca_score = sum(f * w for f, w in zip(data["factors"], data["weights"]))
        
        # Final Risk Formula: avg_base + (1 - avg_base) * CA
        avg_base = 0.40 
        final_risk = avg_base + (1.0 - avg_base) * ca_score
        return round(float(final_risk), 2)

    def update_news(self):
        """Pipeline to fetch, analyze, and save to database with new metadata columns"""
        keywords = "(Senegal OR DRC OR 'Cote d'Ivoire' OR Ethiopia) AND (China OR Russia OR France OR 'United States' OR UAE)"
        raw_news = self.newsapi.get_everything(q=keywords, language='en', sort_by='publishedAt', page_size=10)
        
        new_count = 0
        for art in raw_news['articles']:
            # LLM extracts metadata tags
            facts = self.extract_tags(art['title'], art['description'])
            
            # Calculate the Strategic Score using the CA Logic
            strat_score = self.calculate_ca_score(facts['actor'], facts['country'], facts['intent'])
            
            # Insert into database with the new columns: actor, country, intent_type
            query = text("""
                INSERT INTO articles (
                    title, url, image_url, media_outlet, 
                    published_at, raw_text, contextual_score,
                    actor, country, intent_type
                )
                VALUES (
                    :t, :u, :i, :m, 
                    :d, :s, :sc,
                    :actor, :country, :intent
                ) 
                ON CONFLICT (url) DO NOTHING
            """)
            
            try:
                with self.engine.begin() as conn:
                    conn.execute(query, {
                        "t": art['title'], 
                        "u": art['url'], 
                        "i": art['urlToImage'],
                        "m": art['source']['name'], 
                        "d": art['publishedAt'],
                        "s": facts['summary'], 
                        "sc": strat_score,
                        "actor": facts['actor'],
                        "country": facts['country'],
                        "intent": facts['intent']
                    })
                new_count += 1
            except Exception as e:
                print(f"Database Insert Error: {e}")
        
        return new_count

    def extract_tags(self, title, desc):
        """Forces LLM to return specific tags for the math formula"""
        prompt = f"""
        Extract JSON from news: {title}. 
        Content: {desc}. 
        Return JSON ONLY with: 
        "actor" (Russia/China/France/UnitedStates/UAE/General), 
        "country" (Senegal/DRC/CoteIvoire/Ethiopia/General), 
        "intent" (Economic/MilitaryPresence/SocialFragility), 
        "summary" (1-sentence strategic summary).
        """
        try:
            res = self.groq.chat.completions.create(
                messages=[{"role":"user","content":prompt}], 
                model="llama-3.3-70b-versatile", 
                response_format={"type":"json_object"}
            )
            return json.loads(res.choices[0].message.content)
        except:
            return {"actor":"General", "country":"General", "intent":"Economic", "summary":"Analysis failed."}

    def fetch_articles(self, limit=10):
        """Fetches data for the UI"""
        query = text("SELECT * FROM articles ORDER BY published_at DESC LIMIT :l")
        try:
            with self.engine.connect() as conn:
                return pd.read_sql(query, conn, params={"l": limit})
        except Exception as e:
            st.error(f"Fetch Error: {e}")
            return pd.DataFrame()
