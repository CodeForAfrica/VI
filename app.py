import streamlit as st
import pandas as pd
from data_manager import DataManager
import plotly.graph_objects as go
from datetime import datetime, timedelta
import json

st.set_page_config(page_title="Strategic Vulnerability Index", layout="wide")

# --- Initialize ---
if "mgr" not in st.session_state:
    st.session_state.mgr = DataManager()
mgr = st.session_state.mgr

# --- Metric Explanation Helper ---
def show_metric_legend():
    with st.expander("ℹ️ Understanding the Vulnerability Metrics & Scores"):
        st.write("**Score Meaning:** 0-40% (Stable), 41-70% (At Risk), 71-100% (Critical Vulnerability).")
        st.write("**Debt/Res/Mil:** Measures the ratio of foreign-held debt, resource concessions, and military agreements against national GDP.")
        st.write("**Tone Categories:** **Sensationalist** (Emotional bias), **Alarmist** (Urgent threats), **Factual** (Data-driven), **Cynical** (Distrustful of motives).")

# --- Radar Visual ---
def create_radar(score, tone):
    categories = ['Debt Depth', 'Resource Control', 'Military Presence', 'Sovereignty']
    # Radar shape shifts slightly based on tone
    mod = 1.2 if tone == "Alarmist" else 1.0
    r_values = [score * mod, score * 0.7, score * 0.5, score * 0.8]
    fig = go.Figure(data=go.Scatterpolar(r=r_values, theta=categories, fill='toself', fillcolor='rgba(255, 75, 75, 0.3)', line=dict(color='#ff4b4b')))
    fig.update_layout(polar=dict(radialaxis=dict(visible=False, range=[0, 1.5])), showlegend=False, height=180, margin=dict(l=20, r=20, t=20, b=20), paper_bgcolor='rgba(0,0,0,0)')
    return fig

st.title("🛡️ Africa Geopolitical Vulnerability Dashboard")
show_metric_legend()

# --- Database Management (The 'Unstick' Tools) ---
admin_col1, admin_col2 = st.columns(2)
with admin_col1:
    if st.button("🔄 Full API Sync (Fetch 100 Articles)"):
        with st.spinner("Syncing..."):
            count = mgr.update_news()
            st.success(f"Successfully added {count} new reports.")
            st.cache_data.clear(); st.rerun()
with admin_col2:
    if st.button("🗑️ Reset Database (Clear Stuck Data)"):
        mgr.clear_db(); st.cache_data.clear(); st.success("Database cleared."); st.rerun()

# --- Main Dashboard ---
df = mgr.fetch_articles()
if not df.empty:
    df['published_at'] = pd.to_datetime(df['published_at'])
    
    for _, row in df.iterrows():
        try:
            extra = json.loads(row['raw_text'])
            tone = extra.get('tone', 'Factual')
            summary = extra.get('summary', 'No summary available.')
        except: tone = "Factual"; summary = row['raw_text']

        with st.container(border=True):
            c1, c2, c3 = st.columns([1, 2.5, 1.2])
            with c1: st.image(row['image_url'] if row['image_url'] else "https://via.placeholder.com/400")
            with c2:
                st.subheader(row['title'])
                # Layout Badges
                st.markdown(f"`{row['country']}` `{row['actor']}` `{row['intent_type']}`")
                st.write(f"**Strategic Insight:** {summary}")
                
                # Dynamic Tone Color
                t_colors = {"Alarmist": "#ff4b4b", "Sensationalist": "#ffa500", "Cynical": "#9b59b6", "Factual": "#2ecc71"}
                st.markdown(f"**Media Tone:** <span style='color:{t_colors.get(tone, '#fff')}; font-weight:bold;'>{tone.upper()}</span>", unsafe_allow_html=True)
                st.caption(f"Outlet: {row['media_outlet']} | Date: {row['published_at'].strftime('%Y-%m-%d')}")
            with c3:
                st.plotly_chart(create_radar(row['contextual_score'], tone), use_container_width=True)
                score = int(row['contextual_score'] * 100)
                st.markdown(f"<h1 style='text-align:center; color:#ff4b4b;'>{score}%</h1>", unsafe_allow_html=True)
                st.markdown("<p style='text-align:center; font-size:0.7em;'>VULNERABILITY LEVEL</p>", unsafe_allow_html=True)
else:
    st.info("No data found. Click 'Full API Sync' to fetch reports.")
