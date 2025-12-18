import pandas as pd
import json
from sqlalchemy import create_engine, text
from groq import Groq
import streamlit as st
from datetime import datetime
import requests
from bs4 import BeautifulSoup
import time

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

        # Initialize engine as None
        self.engine = None
        self.groq = None

        try:
            # Database setup
            self.db_url = st.secrets["DB_URL"]
            self.engine = create_engine(self.db_url)
            
            # LLM setup
            self.groq = Groq(api_key=st.secrets["GROQ_API_KEY"])
            
        except Exception as e:
            st.error(f"Initialization Error: {e}")
            # App will run but DB features disabled

    # --- Risk Calculation (UNCHANGED) ---
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

    # --- LLM Extraction (UNCHANGED) ---
    def extract_tags(self, title, desc):
        prompt = f"""Analyze this news: {title}. Return JSON with:
        actor, country, intent, summary, 
        tone (Choose one: Sensationalist, Alarmist, Factual, Cynical).
        ONLY use actors from: {self.actors}
        ONLY use countries from: {self.countries}
        If the article is not about any of these countries or actors, return actor='General', country='General'."""
        try:
            if self.groq is None:
                return {"actor":"General", "country":"General", "intent":"Economic", "summary":"...", "tone":"Factual"}
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

    # --- AFRICA NEWS SCRAPER (REPLACES GDELT/NEWSAPI) ---
    def update_news(self, days=7):  # Shorter window for freshness
        if self.engine is None:
            st.error("Database not available — cannot sync")
            return 0
    
        import requests
        import time
        import json as json_lib
    
        all_articles = []
        headers = {"User-Agent": "Mozilla/5.0"}
    
        # Fetch general recent articles (no query = global news)
        gdel_url = (
            "https://api.gdeltproject.org/api/v2/doc/doc?"
            "mode=artlist&"
            "format=json&"
            "maxrecords=200&"      # Get 200 latest global articles
            f"timespan={days}D"
        )
        
        try:
            st.sidebar.write("📡 Fetching 200 latest global articles from GDELT...")
            response = requests.get(gdel_url, headers=headers, timeout=12)
            
            if not response.content.strip():
                st.sidebar.error("❌ Empty response from GDELT")
                return 0
    
            # Check for HTML error pages
            if response.text.strip().startswith('<'):
                st.sidebar.error("❌ GDELT returned HTML (likely blocked)")
                st.sidebar.write(response.text[:200])
                return 0
    
            data = response.json()
            articles = data.get('articles', [])
            st.sidebar.write(f"✅ Got {len(articles)} global articles from GDELT")
            all_articles = articles[:200]  # Safe limit
    
        except json_lib.JSONDecodeError as e:
            st.sidebar.error(f"❌ JSON decode failed: {str(e)}")
            st.sidebar.write(f"Raw response: {response.text[:300]}")
            return 0
        except Exception as e:
            st,sidebar.error(f"❌ Request failed: {repr(e)}")
            return 0
    
        # --- Now let LLM filter for relevance ---
        count = 0
        seen_urls = set()
        for art in all_articles:
            url = art.get('url')
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            
            title = art.get('title', 'No title')
            snippet = art.get('snippet', '')
            
            # Let LLM decide if it's about our countries/actors
            facts = self.extract_tags(title, snippet)
            
            # ONLY keep if BOTH are in our target lists
            if facts['country'] in self.countries and facts['actor'] in self.actors:
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
                            "t": title,
                            "u": url,
                            "i": art.get('image', ''),
                            "m": art.get('domain', 'Unknown'),
                            "d": art.get('date', datetime.utcnow().isoformat()),
                            "s": extra_data,
                            "sc": score,
                            "actor": facts['actor'],
                            "country": facts['country'],
                            "intent": facts['intent']
                        })
                    count += 1
                except Exception as e:
                    st.sidebar.error(f"DB error: {e}")
    
        st.sidebar.success(f"✅ Synced {count} relevant articles from {len(all_articles)} global!")
        return count
    # --- Fetch Articles (with engine check) ---
    @st.cache_data(ttl=300, show_spinner=False)
    def fetch_articles(_self, limit=500, _cache_version="v3_scraper_final"):
        if _self.engine is None:
            return pd.DataFrame()
        query = text("SELECT * FROM articles ORDER BY published_at DESC LIMIT :l")
        with _self.engine.connect() as conn:
            return pd.read_sql(query, conn, params={"l": limit})

    # --- Clear DB (with engine check) ---
    def clear_db(self):
        if self.engine is None:
            st.error("Database not available")
            return
        with self.engine.begin() as conn:
            conn.execute(text("DELETE FROM articles"))
        st.cache_data.clear()
