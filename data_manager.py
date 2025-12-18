import streamlit as st
import pandas as pd
import requests
from sqlalchemy import create_engine, text
from groq import Groq
from datetime import datetime

class DataManager:
    def __init__(self):
        """Initializes database connection and AI client."""
        # We use pool_pre_ping to ensure the connection is alive 
        # before trying to use it, preventing 'Redacted' errors.
        self.engine = create_engine(
            st.secrets["DB_URL"],
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,
            connect_args={"connect_timeout": 10}
        )
        self.groq_client = Groq(api_key=st.secrets["GROQ_API_KEY"])

    def fetch_articles(self, offset=0, limit=6):
        """Retrieves articles from Supabase for the dashboard."""
        query = f"""
            SELECT * FROM articles 
            ORDER BY published_at DESC 
            LIMIT {limit} OFFSET {offset}
        """
        try:
            return pd.read_sql(query, self.engine)
        except Exception as e:
            st.error(f"Error fetching data: {e}")
            return pd.DataFrame()

    def update_news(self):
        """Scans NewsAPI, runs Groq Analysis, and saves to Supabase."""
        # 1. Configuration
        actors = ["China", "Russia", "France", "UAE", "United States", "USA"]
        countries = ["Senegal", "DRC", "Ivory Coast", "Ethiopia"]
        api_key = st.secrets["NEWS_API_KEY"]
        
        all_processed = []

        # 2. News Fetching Loop
        for country in countries:
            # Look for country name + any of our target actors
            query_str = f'("{country}") AND ({" OR ".join([f'"{a}"' for a in actors])})'
            url = f"https://newsapi.org/v2/everything?q={query_str}&language=en&sortBy=publishedAt&pageSize=10&apiKey={api_key}"
            
            try:
                response = requests.get(url).json()
                articles = response.get("articles", [])
                
                for art in articles:
                    # Skip if missing title or URL
                    if not art.get("title") or not art.get("url"):
                        continue
                        
                    # 3. Groq AI Strategic Analysis
                    analysis = self._analyze_with_groq(art["title"], country)
                    
                    if analysis:
                        all_processed.append({
                            "url": art["url"],
                            "title": art["title"],
                            "media_outlet": art["source"]["name"],
                            "published_at": art["publishedAt"],
                            "image_url": art.get("urlToImage"),
                            "raw_text": f"Actor: {analysis['actor']} | Score: {analysis['score']} | Tone: {analysis['tone']}",
                            "media_name": analysis['actor'] # Used for filtering by Actor
                        })
            except Exception as e:
                st.warning(f"Error processing {country}: {e}")

        # 4. Save to Database
        if all_processed:
            df = pd.DataFrame(all_processed)
            self._save_to_db(df)
            return len(all_processed)
        return 0

    def _analyze_with_groq(self, title, country):
        """Internal helper to categorize news using LLM."""
        prompt = f"""
        Analyze this news headline for strategic influence in {country}:
        Headline: "{title}"
        
        1. Main Actor: (China, Russia, France, UAE, or US)
        2. Intent: (Sovereignty, Economic, Infrastructure, Social, or Security)
        3. Tone: (Neutral, Diplomatic, Aggressive, or Critical)
        4. Influence Score (0.0 to 1.0): Level of strategic impact.
        
        Return ONLY a JSON object: 
        {{"actor": "...", "intent": "...", "tone": "...", "score": 0.0}}
        """
        try:
            chat = self.groq_client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model="llama-3.3-70b-versatile",
                response_format={"type": "json_object"}
            )
            import json
            return json.loads(chat.choices[0].message.content)
        except:
            return None

    def _save_to_db(self, df):
        """Saves data while avoiding duplicate URLs."""
        for _, row in df.iterrows():
            try:
                # We use 'ON CONFLICT (url) DO NOTHING' logic
                row.to_frame().T.to_sql('articles', self.engine, if_exists='append', index=False)
            except:
                pass # Silently skip duplicates
