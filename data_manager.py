import os
import pandas as pd
import urllib.parse
from sqlalchemy import create_engine, text
from newsapi import NewsApiClient
from groq import Groq
import streamlit as st

class DataManager:
    def __init__(self):
        # 1. Database Connection with "Safe Password" Encoding
        try:
            user = st.secrets["DB_USER"]
            password = st.secrets["DB_PASS"]
            host = st.secrets["DB_HOST"]
            port = "6543"
            dbname = "postgres"
            
            # This handles special characters like @ or # in your password
            safe_password = urllib.parse.quote_plus(password)
            
            self.db_url = f"postgresql://{user}:{safe_password}@{host}:{port}/{dbname}?sslmode=require"
            self.engine = create_engine(self.db_url)
        except Exception as e:
            st.error(f"Database Configuration Error: {e}")

        # 2. API Clients
        self.newsapi = NewsApiClient(api_key=st.secrets["NEWS_API_KEY"])
        self.groq = Groq(api_key=st.secrets["GROQ_API_KEY"])

    def fetch_articles(self, offset=0, limit=6):
        """Gets processed articles from Supabase"""
        query = f"SELECT * FROM articles ORDER BY published_at DESC LIMIT {limit} OFFSET {offset}"
        try:
            return pd.read_sql(query, self.engine)
        except Exception as e:
            st.error(f"Error fetching from DB: {e}")
            return pd.DataFrame()

    def update_news(self):
        """The Pipeline: NewsAPI -> LLM -> Supabase"""
        # Search for strategic keywords
        keywords = "(Senegal OR DRC OR 'Ivory Coast') AND (Russia OR China OR investment OR Wagner)"
        raw_news = self.newsapi.get_everything(q=keywords, language='en', sort_by='publishedAt', page_size=10)
        
        new_entries = 0
        for art in raw_news['articles']:
            # Check if article already exists to avoid duplicates
            exists = pd.read_sql(f"SELECT id FROM articles WHERE url = '{art['url']}'", self.engine)
            if not exists.empty:
                continue
                
            # AI Analysis Logic
            analysis = self.analyze_with_llm(art['title'], art['description'])
            
            # Save to Database
            # Note: We use 'raw_text' to store the AI summary for the UI to parse
            query = text("""
                INSERT INTO articles (title, url, image_url, media_outlet, published_at, raw_text, media_name)
                VALUES (:title, :url, :img, :outlet, :date, :analysis, :actor)
            """)
            
            with self.engine.begin() as conn:
                conn.execute(query, {
                    "title": art['title'],
                    "url": art['url'],
                    "img": art['urlToImage'],
                    "outlet": art['source']['name'],
                    "date": art['publishedAt'],
                    "analysis": analysis,
                    "actor": "General" # You can refine this to extract the specific actor
                })
            new_entries += 1
            
        return new_entries

    def analyze_with_llm(self, title, description):
        """Analyzes sentiment and influence via Groq"""
        prompt = f"""
        Analyze this news: Title: {title}. Desc: {description}.
        Return ONLY in this format: 
        Summary: [1 sentence] | Score: [0.0 to 1.0] | Tone: [Factual/Aggressive/Critical]
        """
        try:
            chat_completion = self.groq.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model="llama-3.3-70b-versatile",
            )
            return chat_completion.choices[0].message.content
        except:
            return "Summary: Analysis unavailable | Score: 0.5 | Tone: Factual"
