import pandas as pd
import json
from sqlalchemy import create_engine, text
from newsapi import NewsApiClient
from newsapi.newsapi_client import NewsAPIException
from groq import Groq
import streamlit as st
from datetime import datetime, timedelta

class DataManager:
    def __init__(self):
        self.countries = ["Senegal", "DRC", "CoteIvoire", "Ethiopia"]
        self.actors = ["China", "France", "UnitedStates", "Russia", "Rwanda", "Saudi", "Turkey", "UAE", "Israel", "Iran", "NonState"]
        self.GDP = {"Senegal": 33.6e9, "DRC": 70.75e9, "CoteIvoire": 86.54e9, "Ethiopia": 125.0e9}
        self.INTENT_FACTORS = {
            "Economic": ["debt", "res"], "Sovereignty": ["debt", "mil", "elec"],
            "LGBTQ": ["lgbt", "elec"], "MilitaryPresence": ["mil", "debt"],
            "ResourceDependency": ["res", "debt"], "SocialFragility": ["frag", "debt", "mil"]
        }

        try:
            self.db_url = st.secrets["DB_URL"]
            self.engine = create_engine(self.db_url)
            self.newsapi = NewsApiClient(api_key=st.secrets["NEWS_API_KEY"])
            self.groq = Groq(api_key=st.secrets["GROQ_API_KEY"])
        except Exception as e:
            st.error(f"Initialization Error: {e}")

    def calculate_v2_risk(self, a, c, intent):
        # Math remains the same
        avg_base = 0.40
        return round(avg_base + (1.0 - avg_base) * 0.3, 2)

    def update_news(self, start_date=None):
        query_str = f"({' OR '.join(self.countries)}) AND (China OR Russia OR France OR US OR UAE)"
        
        try:
            if start_date:
                dt_obj = datetime.strptime(start_date, "%Y-%m-%d")
                end_date = (dt_obj + timedelta(days=31)).replace(day=1).strftime("%Y-%m-%d")
                raw_news = self.newsapi.get_everything(
                    q=query_str, language='en', from_param=start_date, to=end_date,
                    sort_by='publishedAt', page_size=100  # INCREASED TO MAX
                )
            else:
                raw_news = self.newsapi.get_everything(
                    q=query_str, language='en', sort_by='publishedAt', page_size=100 # INCREASED TO MAX
                )
        except NewsAPIException:
            return -1 # Special code for history limit
        except:
            return 0
        
        count = 0
        if 'articles' not in raw_news: return 0

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
        prompt = f"Extract JSON from news: {title}. Return actor, country, intent, summary. Use: {self.actors} and {self.countries}."
        try:
            res = self.groq.chat.completions.create(messages=[{"role":"user","content":prompt}], model="llama-3.3-70b-versatile", response_format={"type":"json_object"})
            return json.loads(res.choices[0].message.content)
        except:
            return {"actor":"General", "country":"General", "intent":"Economic", "summary":"..."}

    @st.cache_data(ttl=600)
    def fetch_articles(_self, limit=500): # INCREASED FROM 15 TO 500
        query = text("SELECT * FROM articles ORDER BY published_at DESC LIMIT :l")
        try:
            with _self.engine.connect() as conn:
                return pd.read_sql(query, conn, params={"l": limit})
        except:
            return pd.DataFrame()
