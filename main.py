import streamlit as st
import pandas as pd
import numpy as np
import data_loader  # Using your database-ready loader
from datetime import datetime

# --- PAGE CONFIG ---
st.set_page_config(page_title="Narrative Intelligence Portal", layout="wide")

# --- CUSTOM CSS (Professional "Dark Mode" Accents) ---
st.markdown("""
<style>
    /* Global Background */
    .stApp { background-color: #0e1117; color: #e0e0e0; }
    
    /* Article Card */
    .article-card {
        background: #1c2128;
        border: 1px solid #30363d;
        border-radius: 10px;
        padding: 0;
        margin-bottom: 20px;
        height: 520px; /* Fixed height for grid symmetry */
        transition: transform 0.2s, border-color 0.2s;
        display: flex;
        flex-direction: column;
    }
    .article-card:hover {
        transform: translateY(-5px);
        border-color: #58a6ff;
    }
    
    /* Image Section */
    .card-img {
        width: 100%;
        height: 180px;
        object-fit: cover;
        border-radius: 10px 10px 0 0;
    }
    
    /* Text Content */
    .card-body { padding: 15px; flex-grow: 1; }
    .card-title { font-size: 1.1rem; font-weight: 600; color: #f0f6fc; margin-bottom: 8px; line-height: 1.3; }
    .card-summary { font-size: 0.85rem; color: #8b949e; line-height: 1.5; height: 80px; overflow: hidden; }
    
    /* Metadata Badges */
    .badge {
        display: inline-block;
        padding: 2px 8px;
        border-radius: 12px;
        font-size: 0.7rem;
        font-weight: bold;
        margin-right: 5px;
        background: #21262d;
        color: #c9d1d9;
    }
    .tone-factual { border-left: 4px solid #238636; }
    .tone-sensational { border-left: 4px solid #d29922; }
    .tone-hostile { border-left: 4px solid #f85149; }
    
    /* Influence Meter */
    .influence-box {
        background: #161b22;
        padding: 10px;
        border-top: 1px solid #30363d;
        text-align: center;
        border-radius: 0 0 10px 10px;
    }
</style>
""", unsafe_allow_html=True)

# --- APP HEADER ---
col_l, col_r = st.columns([1, 4])
with col_l:
    st.image("https://raw.githubusercontent.com/hanna-tes/CfA-media-narrtives-monitoring/main/CFA_Logo.png", width=120)
with col_r:
    st.title("🛡️ Real-time Narrative Monitoring")
    st.caption(f"Last Intelligence Sync: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

# --- SIDEBAR FILTERS ---
with st.sidebar:
    st.header("🔍 Filter Intelligence")
    selected_actor = st.selectbox("Inferred Actor", ["All"] + data_loader.get_actors())
    selected_country = st.selectbox("Target Country", ["All"] + data_loader.get_countries())
    selected_intent = st.selectbox("Strategic Intent", ["All", "Information Warfare", "Economic Coercion", "Diplomatic Pressure"])
    
    st.markdown("---")
    st.subheader("Vulnerability Threshold")
    min_score = st.slider("Min Contextual Influence", 0.0, 1.0, 0.0)

# --- DATA PROCESSING (PostgreSQL / Efficient Loading) ---
# We only load 6 articles per page
ARTICLES_PER_PAGE = 6
if "page" not in st.session_state: st.session_state.page = 0

# Fetching only the counts and IDs first for speed
total_articles = 15000 # This would come from data_loader.get_total_count(...)
offset = st.session_state.page * ARTICLES_PER_PAGE

# Simulated Fetch: Replace with data_loader.load_filtered_data(...)
df = data_loader.load_raw_data().sample(6) # Replace with DB-limited query
df = data_loader.enrich_with_scraping_and_llm(df) # Enrich only these 6

# --- TOP LEVEL KPI DASHBOARD ---
st.markdown("### 📊 Strategic Vulnerability Index")
k1, k2, k3, k4 = st.columns(4)
k1.metric("Active Narratives", f"{total_articles:,}", "+12% vs last week")
k2.metric("Avg Tone Score", "0.42", "-0.05", delta_color="inverse")
k3.metric("Critical Actors", "3", "Russia, China, Turkey")
k4.metric("Risk Level", "MEDIUM", "Based on Volume", delta_color="off")

st.markdown("---")

# --- ARTICLE GRID (2 columns x 3 rows) ---
cols = st.columns(2)
for i, (idx, row) in enumerate(df.iterrows()):
    col_idx = i % 2
    with cols[col_idx]:
        # Determine Tone Color Class
        tone_class = "tone-factual"
        if row['tone'] == 'Sensationalist': tone_class = "tone-sensational"
        elif row['tone'] in ['Hostile', 'Cynical']: tone_class = "tone-hostile"
        
        # Calculate a mock Contextual Influence Score for display
        influence_score = np.random.uniform(0.3, 0.95) 
        
        st.markdown(f"""
        <div class="article-card {tone_class}">
            <img src="{row['urlToImage']}" class="card-img">
            <div class="card-body">
                <div class="card-title">{row['article_text'][:80]}...</div>
                <div style="margin-bottom:10px;">
                    <span class="badge">👤 {row['inferred_actor']}</span>
                    <span class="badge">📍 {row['target_country']}</span>
                    <span class="badge">📅 {row['posting_time']}</span>
                </div>
                <div class="card-summary">
                    {row['article_text'][:200]}...
                </div>
                <a href="{row['URL']}" target="_blank" style="color:#58a6ff; font-size:0.8rem;">Read Source Data →</a>
            </div>
            <div class="influence-box">
                <span style="font-size:0.7rem; color:#8b949e; display:block; text-transform:uppercase;">Contextual Influence Score</span>
                <span style="font-size:1.2rem; font-weight:bold; color: {'#f85149' if influence_score > 0.7 else '#d29922'}">
                    {influence_score:.2f}
                </span>
            </div>
        </div>
        """, unsafe_allow_html=True)

# --- PAGINATION ---
st.markdown("<br>", unsafe_allow_html=True)
p_prev, p_info, p_next = st.columns([1, 2, 1])
with p_prev:
    if st.button("⬅️ Previous Intelligence") and st.session_state.page > 0:
        st.session_state.page -= 1
        st.rerun()
with p_info:
    st.markdown(f"<p style='text-align:center'>Viewing Report {st.session_state.page + 1} of {total_articles // ARTICLES_PER_PAGE}</p>", unsafe_allow_html=True)
with p_next:
    if st.button("Next Intelligence ➡️"):
        st.session_state.page += 1
        st.rerun()
