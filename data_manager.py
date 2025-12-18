import pandas as pd
import json
from sqlalchemy import create_engine, text
from newsapi import NewsApiClient
from groq import Groq
import streamlit as st
from datetime import datetime, timedelta

class DataManager:
    def __init__(self):
        self.countries = ["Senegal", "DRC", "CoteIvoire", "Ethiopia"]
        self.actors = ["China", "France", "UnitedStates", "Russia", "Rwanda", "Saudi", "Turkey", "UAE", "Israel", "Iran", "NonState"]
        self.GDP = {"Senegal": 33.6e9, "DRC": 70.75e9, "CoteIvoire": 86.54e9, "Ethiopia": 125.0e9}
        self.DEBT = st.secrets.get("DEBT", {})
        self.G_RES = st.secrets.get("G_RES", {})
        self.G_MIL = st.secrets.get("G_MIL", {})
        
        # Required for filters in app.py
        self.INTENT_FACTORS = {
            "Economic": ["debt", "res"],
            "Sovereignty": ["debt", "mil"],
            "MilitaryPresence": ["mil", "debt"],
            "ResourceDependency": ["res", "debt"]
        }

        try:
            self.db_url = st.secrets["DB_URL"]
            self.engine = create_engine(self.db_url)
            self.newsapi = NewsApiClient(api_key=st.secrets["NEWS_API_KEY"])
            self.groq = Groq(api_key=st.secrets["GROQ_API_KEY"])
        except Exception as e:
            st.error(f"Initialization Error: {e}")

    # --- NEW: Full contextual_v2 logic integrated ---
    def compute_gs(self):
        """Compute g-factors (debt, res, mil) per actor-country"""
        g = {}
        for a in self.actors:
            g[a] = {}
            for c in self.countries:
                debt = self.DEBT.get(a, {}).get(c, 0.0)
                g_debt = min(1.0, debt / self.GDP.get(c, 1)) if self.GDP.get(c, 0) > 0 else 0.0
                g_res = self.G_RES.get(a, {}).get(c, 0.0)
                g_mil = self.G_MIL.get(a, {}).get(c, 0.0)
                g[a][c] = {"debt": g_debt, "res": g_res, "mil": g_mil}
        return g

    def compute_R(self, g):
        """Compute R-normalization per factor across all actor-country pairs"""
        R = {a: {c: {} for c in self.countries} for a in self.actors}
        for factor in ["debt", "res", "mil"]:
            max_val = max(
                (g[a][c][factor] for a in self.actors for c in self.countries),
                default=1.0
            )
            max_val = max_val if max_val > 0 else 1.0
            for a in self.actors:
                for c in self.countries:
                    R[a][c][factor] = g[a][c][factor] / max_val
        return R

    def calculate_intent_risk(self, actor, country, intent):
        """Compute final risk using intent-aware weighting and normalization"""
        if actor not in self.actors or country not in self.countries:
            return 0.4  # fallback

        if intent not in self.INTENT_FACTORS:
            intent = "Economic"  # default fallback

        g = self.compute_gs()
        R = self.compute_R(g)
        factors = self.INTENT_FACTORS[intent]

        # Compute weights based on R-normalized values
        r_values = [R[actor][country].get(f, 0.0) for f in factors]
        denom = sum(r_values)
        if denom == 0:
            weights = {f: 1.0 / len(factors) for f in factors}
        else:
            weights = {f: R[actor][country][f] / denom for f in factors}

        # Compute Composite Actor (CA) score
        ca = sum(weights[f] * g[actor][country][f] for f in factors)

        # Final risk: base + (1 - base) * CA
        avg_base = 0.35  # calibrated base vulnerability
        final_score = avg_base + (1.0 - avg_base) * ca
        return max(0.0, min(1.0, final_score))

    # --- END contextual_v2 integration ---

    def update_news(self, start_date=None):
        query_str = f"({' OR '.join(self.countries)}) AND (China OR Russia OR France OR Wagner)"
        all_articles = []
        
        # Paginate up to 5 pages (free tier limit)
        for page in range(1, 6):
            try:
                batch = self.newsapi.get_everything(
                    q=query_str,
                    language='en',
                    sort_by='publishedAt',
                    page_size=20,  # Max per page on free tier
                    page=page,
                    from_param=start_date if start_date else (datetime.now() - timedelta(days=28)).strftime("%Y-%m-%d")
                )
                articles = batch.get('articles', [])
                if not articles:
                    break
                all_articles.extend(articles)
                if len(articles) < 20:  # Last page
                    break
            except Exception as e:
                st.warning(f"NewsAPI page {page} failed: {e}")
                break

        count = 0
        for art in all_articles:
            facts = self.extract_tags(art['title'], art['description'])
            # Use new risk calculator
            score = self.calculate_intent_risk(facts['actor'], facts['country'], facts['intent'])
            extra_data = json.dumps({"tone": facts['tone'], "summary": facts['summary']})
            
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
                        "s": extra_data, "sc": score,
                        "actor": facts['actor'], "country": facts['country'], "intent": facts['intent']
                    })
                count += 1
            except Exception as e:
                st.warning(f"Insert failed for {art['url']}: {e}")
                continue
        return count

    def extract_tags(self, title, desc):
        prompt = f"""Analyze this news: {title}. Return JSON with:
        actor, country, intent, summary, 
        tone (Choose one: Sensationalist, Alarmist, Factual, Cynical).
        Categories: {self.actors}, {self.countries}."""
        try:
            res = self.groq.chat.completions.create(messages=[{"role":"user","content":prompt}], model="llama-3.3-70b-versatile", response_format={"type":"json_object"})
            return json.loads(res.choices[0].message.content)
        except:
            return {"actor":"General", "country":"General", "intent":"Economic", "summary":"...", "tone":"Factual"}

    @st.cache_data(ttl=300)
    def fetch_articles(_self, limit=500):
        query = text("SELECT * FROM articles ORDER BY published_at DESC LIMIT :l")
        with _self.engine.connect() as conn:
            return pd.read_sql(query, conn, params={"l": limit})

    def clear_db(self):
        with self.engine.begin() as conn:
            conn.execute(text("DELETE FROM articles"))
