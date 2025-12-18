import streamlit as st
import pandas as pd
from data_manager import DataManager
import plotly.graph_objects as go
import plotly.express as px
import json
from datetime import datetime

# --- Page Config ---
st.set_page_config(page_title="Strategic Vulnerability Index", layout="wide")

# --- Initialize Data Manager ---
if "mgr" not in st.session_state:
    st.session_state.mgr = DataManager()
mgr = st.session_state.mgr

# --- Custom Styling (The "Eye-Catching" Layer) ---
st.markdown("""
    <style>
    /* Main Background & Font */
    .stApp { background-color: #0d1117; color: #c9d1d9; }
    
    /* Intelligence Dossier Card */
    .dossier-card {
        background: rgba(22, 27, 34, 0.8);
        border: 1px solid #30363d;
        border-radius: 12px;
        padding: 24px;
        margin-bottom: 20px;
        border-left: 6px solid #ff4b4b; /* Glow indicator */
        box-shadow: 0 4px 20px rgba(0,0,0,0.4);
    }
    
    /* Metadata Badges */
    .intel-badge {
        display: inline-block;
        padding: 2px 12px;
        border-radius: 20px;
        font-size: 0.75rem;
        font-weight: 700;
        text-transform: uppercase;
        background: #161b22;
        border: 1px solid #444c56;
        margin-right: 8px;
        color: #8b949e;
    }
    
    /* Large Risk Text */
    .risk-hero {
        font-family: 'Courier New', monospace;
        font-weight: 900;
        color: #ff4b4b;
        margin: 0;
        line-height: 1;
    }
    </style>
""", unsafe_allow_html=True)

# --- Metric Explanation Legend ---
def show_metric_legend():
    with st.expander("ℹ️ UNDERSTANDING THE VULNERABILITY MATRIX"):
        st.markdown("""
        ### Strategic Metrics Breakdown
        * **Vulnerability Score:** A weighted index (0-100%) where **>70%** indicates critical foreign influence.
        * **Matrix Factors:** Based on Debt-to-GDP ratios, Resource concessions, and Military agreements.
        * **Media Tone Definitions:**
            * <span style='color:#2ecc71'>**Factual:**</span> Neutral, data-heavy reporting.
            * <span style='color:#ffa500'>**Sensationalist:**</span> Emphasizes emotion or shock.
            * <span style='color:#ff4b4b'>**Alarmist:**</span> Focuses on immediate threats to stability.
            * <span style='color:#9b59b6'>**Cynical:**</span> Questions underlying foreign motives.
        """, unsafe_allow_html=True)

# --- Radar Visual ---
def create_radar(score, tone):
    categories = ['Debt Depth', 'Resource Control', 'Military Presence', 'Sovereignty']
    mod = 1.1 if tone == "Alarmist" else 1.0
    r_values = [score * mod, score * 0.7, score * 0.5, score * 0.8]
    
    fig = go.Figure(data=go.Scatterpolar(
        r=r_values, 
        theta=categories, 
        fill='toself', 
        fillcolor='rgba(255, 75, 75, 0.25)', 
        line=dict(color='#ff4b4b', width=2)
    ))
    fig.update_layout(
        polar=dict(
            radialaxis=dict(visible=False, range=[0, 1.2]),
            angularaxis=dict(tickfont=dict(size=10, color="#8b949e"), color="#30363d")
        ),
        showlegend=False, height=220, margin=dict(l=40, r=40, t=20, b=20),
        paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)'
    )
    return fig

# --- Header ---
st.title("🛡️ Geopolitical Intelligence Dashboard")
show_metric_legend()

# --- Command Center (Filters) ---
with st.container(border=True):
    st.markdown("### 🔍 Strategic Filters")
    c1, c2, c3, c4 = st.columns(4)
    with c1: f_country = st.selectbox("📍 Nation", ["All Nations"] + mgr.countries)
    with c2: f_actor = st.selectbox("👤 Actor", ["All Actors"] + mgr.actors)
    with c3: f_intent = st.selectbox("🎯 Intent", ["All Intents"] + list(mgr.INTENT_FACTORS.keys()))
    with c4: f_tone = st.selectbox("🎭 Tone", ["All Tones", "Factual", "Alarmist", "Sensationalist", "Cynical"])
    
    st.markdown("---")
    sync_c1, sync_c2, _ = st.columns([2, 2, 4])
    with sync_c1:
        if st.button("🔄 Sync Intelligence Feed", use_container_width=True):
            mgr.update_news(); st.cache_data.clear(); st.rerun()
    with sync_c2:
        if st.button("🗑️ Purge Local DB", use_container_width=True):
            mgr.clear_db(); st.cache_data.clear(); st.rerun()

# --- Data Fetching ---
df = mgr.fetch_articles(limit=500)

if not df.empty:
    df['published_at'] = pd.to_datetime(df['published_at'])
    
    def extract_extra(row):
        try:
            data = json.loads(row['raw_text'])
            return pd.Series([data.get('tone', 'Factual'), data.get('summary', '...')])
        except: return pd.Series(['Factual', row['raw_text']])
    df[['tone', 'summary']] = df.apply(extract_extra, axis=1)

    # Filter Logic
    f_df = df.copy()
    if f_country != "All Nations": f_df = f_df[f_df['country'] == f_country]
    if f_actor != "All Actors": f_df = f_df[f_df['actor'] == f_actor]
    if f_intent != "All Intents": f_df = f_df[f_df['intent_type'] == f_intent]
    if f_tone != "All Tones": f_df = f_df[f_df['tone'] == f_tone]

    # --- Trend Analysis ---
    if not f_df.empty:
        st.subheader("📈 Vulnerability Velocity (Time Series)")
        trend = f_df.groupby(f_df['published_at'].dt.date)['contextual_score'].mean().reset_index()
        fig_trend = px.line(trend, x='published_at', y='contextual_score', template="plotly_dark")
        fig_trend.update_traces(line_color='#ff4b4b', line_width=4, mode='lines+markers')
        fig_trend.update_layout(height=280, margin=dict(l=0, r=0, t=10, b=0), 
                              paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)')
        st.plotly_chart(fig_trend, use_container_width=True, key="trend_viz")
    
    st.markdown("---")

    # --- Pagination ---
    items_per_page = 6
    if "page" not in st.session_state: st.session_state.page = 1
    total_pages = max(1, (len(f_df) // items_per_page) + (1 if len(f_df) % items_per_page > 0 else 0))
    
    p1, p2, p3 = st.columns([1, 4, 1])
    if p1.button("⬅️ Previous") and st.session_state.page > 1: st.session_state.page -= 1; st.rerun()
    p2.markdown(f"<div style='text-align:center;'>Page <b>{st.session_state.page}</b> of {total_pages} | Reports: {len(f_df)}</div>", unsafe_allow_html=True)
    if p3.button("Next ➡️") and st.session_state.page < total_pages: st.session_state.page += 1; st.rerun()

    # --- Article Feed ---
    start_idx = (st.session_state.page - 1) * items_per_page
    for idx, row in f_df.iloc[start_idx : start_idx + items_per_page].iterrows():
        # High-End Dossier Card
        st.markdown(f"""
            <div class="dossier-card">
                <div style="display: flex; justify-content: space-between; align-items: flex-start;">
                    <div style="width: 75%;">
                        <span class="intel-badge">📍 {row['country']}</span>
                        <span class="intel-badge">👤 {row['actor']}</span>
                        <span class="intel-badge">🎯 {row['intent_type']}</span>
                        <h2 style="margin: 15px 0 10px 0; color: #f0f6fc;">{row['title']}</h2>
                        <p style="color: #8b949e; line-height: 1.6;">{row['summary']}</p>
                    </div>
                    <div style="width: 20%; text-align: right;">
                        <p style="font-size: 0.7rem; color: #58a6ff; letter-spacing: 2px; margin-bottom: 5px;">INDEX SCORE</p>
                        <h1 class="risk-hero">{int(row['contextual_score'] * 100)}%</h1>
                    </div>
                </div>
            </div>
        """, unsafe_allow_html=True)
        
        # Details & Data Row
        det_col1, det_col2, det_col3 = st.columns([1, 2, 1.2])
        with det_col1:
            st.image(row['image_url'] if row['image_url'] else "https://via.placeholder.com/400x250/161b22/8b949e?text=No+Image", use_container_width=True)
        with det_col2:
            t_colors = {"Alarmist": "#ff4b4b", "Sensationalist": "#ffa500", "Cynical": "#9b59b6", "Factual": "#2ecc71"}
            t_color = t_colors.get(row['tone'], "#ffffff")
            st.markdown(f"**Media Tone:** <span style='color:{t_color}; font-weight:bold;'>{row['tone'].upper()}</span>", unsafe_allow_html=True)
            st.caption(f"Source: {row['media_outlet']} | Logged: {row['published_at'].strftime('%Y-%m-%d')}")
            st.link_button("View Full Report", row['url'], use_container_width=True)
        with det_col3:
            st.plotly_chart(create_radar(row['contextual_score'], row['tone']), use_container_width=True, key=f"radar_{idx}")

    with st.expander("🗄️ Raw Data Archive"):
        st.dataframe(df, use_container_width=True)
else:
    st.info("System initialized. No intelligence signals detected.")
