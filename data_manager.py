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

    def compute_gs(self):
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
        if actor not in self.actors or country not in self.countries:
            return 0.4
        if intent not in self.INTENT_FACTORS:
            intent = "Economic"
        g = self.compute_gs()
        R = self.compute_R(g)
        factors = self.INTENT_FACTORS[intent]
        r_values = [R[actor][country].get(f, 0.0) for f in factors]
        denom = sum(r_values)
        if denom == 0:
            weights = {f: 1.0 / len(factors) for f in factors}
        else:
            weights = {f: R[actor][country][f] / denom for f in factors}
        ca = sum(weights[f] * g[actor][country][f] for f in factors)
        avg_base = 0.35
        return max(0.0, min(1.0, avg_base + (1.0 - avg_base) * ca))

    def extract_tags(self, title, desc):
        prompt = f"""Analyze this news: {title}. Return JSON with:
        actor, country, intent, summary, 
        tone (Choose one: Sensationalist, Alarmist, Factual, Cynical).
        ONLY use actors from: {self.actors}
        ONLY use countries from: {self.countries}
        If the article is not about any of these countries or actors, return actor='General', country='General'."""
        try:
            res = self.groq.chat.completions.create(
                messages=[{"role":"user","content":prompt}],
                model="llama-3.3-70b-versatile",
                response_format={"type":"json_object"}
            )
            data = json.loads(res.choices[0].message.content)
            actor = data.get('actor', 'General')
            country = data.get('country', 'General')
            intent = data.get('intent', 'Economic')
            summary = data.get('summary', '...')
            tone = data.get('tone', 'Factual')
            return {"actor": actor, "country": country, "intent": intent, "summary": summary, "tone": tone}
        except:
            return {"actor":"General", "country":"General", "intent":"Economic", "summary":"...", "tone":"Factual"}

    def update_news(self, start_date=None):
        query_str = f"({' OR '.join(self.countries)}) AND (China OR Russia OR France OR Wagner)"
        try:
            raw_news = self.newsapi.get_everything(
                q=query_str,
                language='en',
                sort_by='publishedAt',
                page_size=10,
                from_param=start_date if start_date else (datetime.now() - timedelta(days=28)).strftime("%Y-%m-%d")
            )
            articles = raw_news.get('articles', [])
            count = 0
            for art in articles:
                facts = self.extract_tags(art['title'], art['description'])
                
                # ONLY process articles about our target countries and actors
                if facts['country'] not in self.countries or facts['actor'] not in self.actors:
                    continue

                score = self.calculate_intent_risk(facts['actor'], facts['country'], facts['intent'])
                extra_data = json.dumps({"tone": facts['tone'], "summary": facts['summary']})
                sql = text("""
                    INSERT INTO articles (title, url, image_url, media_outlet, published_at, raw_text, contextual_score, actor, country, intent_type)
                    VALUES (:t, :u, :i, :m, :d, :s, :sc, :actor, :country, :intent)
                    ON CONFLICT (url) DO NOTHING
                """)
                with self.engine.begin() as conn:
                    conn.execute(sql, {
                        "t": art['title'], "u": art['url'], "i": art['urlToImage'],
                        "m": art['source']['name'], "d": art['publishedAt'],
                        "s": extra_data, "sc": score,
                        "actor": facts['actor'], "country": facts['country'], "intent": facts['intent']
                    })
                count += 1
            return count
        except Exception as e:
            st.error(f"NewsAPI error: {e}")
            return 0

    @st.cache_data(ttl=300, show_spinner=False)
    def fetch_articles(_self, limit=500, _cache_version="v2_intent_risk_final"):
        query = text("SELECT * FROM articles ORDER BY published_at DESC LIMIT :l")
        with _self.engine.connect() as conn:
            return pd.read_sql(query, conn, params={"l": limit})

    def clear_db(self):
        with self.engine.begin() as conn:
            conn.execute(text("DELETE FROM articles"))
        st.cache_data.clear()
