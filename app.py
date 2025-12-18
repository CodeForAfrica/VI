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

# --- Metric Explanation Legend ---
def show_metric_legend():
    with st.expander("ℹ️ Understanding the Vulnerability Metrics & Scores"):
        st.markdown("""
        ### Strategic Metrics Breakdown
        * **Vulnerability Score:** A weighted index (0-100%) where **>70%** indicates critical foreign influence.
        * **Matrix Factors:** Based on Debt-to-GDP ratios, Resource concessions, and Military agreements.
        * **Media Tone:**
            * **Factual:** Neutral, data-heavy reporting.
            * **Sensationalist:** Emphasizes emotion or shock over data.
            * **Alarmist:** Focuses on immediate, extreme threats to stability.
            * **Cynical:** Questions the underlying motives of foreign actors.
        """)

# --- Radar Visual (FIXED Plotly Property Path) ---
def create_radar(score, tone):
    categories = ['Debt Depth', 'Resource Control', 'Military Presence', 'Sovereignty']
    mod = 1.1 if tone == "Alarmist" else 1.0
    r_values = [score * mod, score * 0.7, score * 0.5, score * 0.8]
    
    fig = go.Figure(data=go.Scatterpolar(
        r=r_values, 
        theta=categories, 
        fill='toself', 
        fillcolor='rgba(255, 75, 75, 0.3)', 
        line=dict(color='#ff4b4b', width=2)
    ))
    
    fig.update_layout(
        polar=dict(
            radialaxis=dict(visible=False, range=[0, 1.2]),
            angularaxis=dict(
                tickfont=dict(size=10), # FIXED: Moved size into tickfont dictionary
                color="#888"
            )
        ),
        showlegend=False, 
        height=200, 
        margin=dict(l=30, r=30, t=30, b=30), 
        paper_bgcolor='rgba(0,0,0,0)'
    )
    return fig

# --- Header ---
st.title("🛡️ Geopolitical Vulnerability Index")
show_metric_legend()

# --- Fancy Command Center (Filters) ---
st.markdown("""
    <style>
    .filter-box {
        background-color: #161b22;
        padding: 20px;
        border-radius: 12px;
        border: 1px solid #30363d;
        margin-bottom: 25px;
    }
    </style>
""", unsafe_allow_html=True)

with st.container():
    st.markdown('<div class="filter-box">', unsafe_allow_html=True)
    st.markdown("### 🔍 Strategic Command Center")
    c1, c2, c3, c4 = st.columns(4)
    with c1: f_country = st.selectbox("📍 Target Nation", ["All Nations"] + mgr.countries)
    with c2: f_actor = st.selectbox("👤 Foreign Actor", ["All Actors"] + mgr.actors)
    with c3: f_intent = st.selectbox("🎯 Primary Intent", ["All Intents"] + list(mgr.INTENT_FACTORS.keys()))
    with c4: f_tone = st.selectbox("🎭 Media Tone", ["All Tones", "Factual", "Alarmist", "Sensationalist", "Cynical"])
    
    st.markdown("---")
    sync_c1, sync_c2, _ = st.columns([2, 2, 4])
    with sync_c1:
        if st.button("🔄 Sync Global Intelligence", use_container_width=True):
            mgr.update_news()
            st.cache_data.clear()
            st.rerun()
    with sync_c2:
        if st.button("🗑️ Reset Database", use_container_width=True):
            mgr.clear_db()
            st.cache_data.clear()
            st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

# --- Data Fetching and Processing ---
df = mgr.fetch_articles(limit=500)

if not df.empty:
    df['published_at'] = pd.to_datetime(df['published_at'])
    
    def extract_extra(row):
        try:
            data = json.loads(row['raw_text'])
            return pd.Series([data.get('tone', 'Factual'), data.get('summary', '...')])
        except:
            return pd.Series(['Factual', row['raw_text']])
    df[['tone', 'summary']] = df.apply(extract_extra, axis=1)

    # Filter Logic
    filtered_df = df.copy()
    if f_country != "All Nations": filtered_df = filtered_df[filtered_df['country'] == f_country]
    if f_actor != "All Actors": filtered_df = filtered_df[filtered_df['actor'] == f_actor]
    if f_intent != "All Intents": filtered_df = filtered_df[filtered_df['intent_type'] == f_intent]
    if f_tone != "All Tones": filtered_df = filtered_df[filtered_df['tone'] == f_tone]

    # --- Trend Analysis ---
    if not filtered_df.empty:
        st.subheader("📈 Vulnerability Trend Analysis")
        trend_data = filtered_df.groupby(filtered_df['published_at'].dt.date)['contextual_score'].mean().reset_index()
        fig_trend = px.line(trend_data, x='published_at', y='contextual_score', template="plotly_dark")
        fig_trend.update_traces(line_color='#ff4b4b', line_width=3, mode='lines+markers')
        fig_trend.update_layout(height=250, margin=dict(l=0, r=0, t=0, b=0))
        st.plotly_chart(fig_trend, width='stretch', key="vulnerability_trend_chart")
    
    st.markdown("---")

    # --- Pagination (6 per page) ---
    items_per_page = 6
    if "page" not in st.session_state: st.session_state.page = 1
    total_pages = max(1, (len(filtered_df) // items_per_page) + (1 if len(filtered_df) % items_per_page > 0 else 0))
    
    p_col1, p_col2, p_col3 = st.columns([1, 4, 1])
    if p_col1.button("⬅️ Previous") and st.session_state.page > 1:
        st.session_state.page -= 1
        st.rerun()
    p_col2.write(f"Page {st.session_state.page} of {total_pages} ({len(filtered_df)} total reports)")
    if p_col3.button("Next ➡️") and st.session_state.page < total_pages:
        st.session_state.page += 1
        st.rerun()

    # --- Article Feed ---
    start_idx = (st.session_state.page - 1) * items_per_page
    page_df = filtered_df.iloc[start_idx : start_idx + items_per_page]

    for idx, row in page_df.iterrows():
        with st.container(border=True):
            c_img, c_body, c_risk = st.columns([1, 2.5, 1.2])
            with c_img:
                st.image(row['image_url'] if row['image_url'] else "https://via.placeholder.com/400")
            with c_body:
                st.subheader(row['title'])
                st.markdown(f"`📍 {row['country']}` `👤 {row['actor']}` `🎯 {row['intent_type']}`")
                st.write(f"**Strategic Insight:** {row['summary']}")
                t_colors = {"Alarmist": "#ff4b4b", "Sensationalist": "#ffa500", "Cynical": "#9b59b6", "Factual": "#2ecc71"}
                t_color = t_colors.get(row['tone'], "#ffffff")
                st.markdown(f"**Media Tone:** <span style='color:{t_color}; font-weight:bold;'>{row['tone'].upper()}</span>", unsafe_allow_html=True)
                st.caption(f"{row['media_outlet']} | {row['published_at'].strftime('%Y-%m-%d')}")
                st.link_button("View Source", row['url'])
            with c_risk:
                # UNIQUE KEY FOR STABILITY
                st.plotly_chart(create_radar(row['contextual_score'], row['tone']), width='stretch', key=f"radar_chart_{idx}")
                score_pct = int(row['contextual_score'] * 100)
                st.markdown(f"<h1 style='text-align:center; color:#ff4b4b; margin-bottom:0;'>{score_pct}%</h1>", unsafe_allow_html=True)
                st.markdown("<p style='text-align:center; font-size:0.7em;'>VULNERABILITY INDEX</p>", unsafe_allow_html=True)

    # --- Raw Database Inspector ---
    with st.expander("🗄️ Raw Database Inspector"):
        st.dataframe(df[['published_at', 'country', 'actor', 'title', 'contextual_score']], use_container_width=True)
else:
    st.info("Intelligence database is empty. Please Sync to begin.")
