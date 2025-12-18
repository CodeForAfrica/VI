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

        try:
            self.db_url = st.secrets["DB_URL"]
            self.engine = create_engine(self.db_url)
            self.newsapi = NewsApiClient(api_key=st.secrets["NEWS_API_KEY"])
            self.groq = Groq(api_key=st.secrets["GROQ_API_KEY"])
        except Exception as e:
            st.error(f"Initialization Error: {e}")

    def calculate_v2_risk(self, a, c, intent):
        debt = self.DEBT.get(a, {}).get(c, 0.1)
        g_debt = min(1.0, debt / self.GDP.get(c, 1e10))
        g_res = self.G_RES.get(a, {}).get(c, 0.2)
        g_mil = self.G_MIL.get(a, {}).get(c, 0.1)
        avg_base = 0.40
        ca_score = (g_debt + g_res + g_mil) / 3
        return round(max(0.0, min(1.0, avg_base + (1.0 - avg_base) * ca_score)), 2)

    def update_news(self, start_date=None):
        query_str = f"({' OR '.join(self.countries)}) AND (China OR Russia OR France OR Wagner)"
        try:
            raw_news = self.newsapi.get_everything(
                q=query_str, language='en', sort_by='publishedAt', page_size=100,
                from_param=start_date if start_date else (datetime.now() - timedelta(days=28)).strftime("%Y-%m-%d")
            )
            count = 0
            for art in raw_news.get('articles', []):
                facts = self.extract_tags(art['title'], art['description'])
                score = self.calculate_v2_risk(facts['actor'], facts['country'], facts['intent'])
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
        except: return 0

    def extract_tags(self, title, desc):
        # Using your 4 specific categories
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
        """Used to clear the 'stuck' 10 articles and start fresh"""
        with self.engine.begin() as conn:
            conn.execute(text("DELETE FROM articles"))
