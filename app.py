import streamlit as st
import pandas as pd
from data_manager import DataManager
import plotly.graph_objects as go

# --- Page Config ---
st.set_page_config(
    page_title="Strategic Vulnerability Index",
    page_icon="🛡️",
    layout="wide"
)

# --- Custom CSS (Removes white cards & styles containers) ---
st.markdown("""
    <style>
    .stApp { background-color: #0e1117; color: #fafafa; }
    [data-testid="stVerticalBlockBorderWrapper"] {
        background-color: #161b22 !important;
        border: 1px solid #30363d !important;
        border-radius: 10px !important;
        padding: 20px !important;
    }
    h1, h2, h3 { color: #ffffff !important; }
    </style>
""", unsafe_allow_html=True)

# Initialization
if "mgr" not in st.session_state:
    st.session_state.mgr = DataManager()
mgr = st.session_state.mgr

# --- Helper: Radar Chart ---
def create_radar_chart(score, intent_type):
    categories = ['Debt', 'Military', 'Resources', 'Social Fragility']
    # Logic matches your contextual_all_intent components
    if intent_type == "Economic":
        values = [score, score * 0.4, score * 0.8, score * 0.3]
    elif intent_type == "MilitaryPresence":
        values = [score * 0.5, score, score * 0.3, score * 0.6]
    else:
        values = [score * 0.4, score * 0.5, score * 0.2, score]

    fig = go.Figure(data=go.Scatterpolar(
        r=values, theta=categories, fill='toself', line_color='#ff4b4b'
    ))
    fig.update_layout(
        polar=dict(radialaxis=dict(visible=False, range=[0, 1])),
        showlegend=False, height=200, margin=dict(l=30, r=30, t=20, b=20),
        paper_bgcolor='rgba(0,0,0,0)', font_color="white"
    )
    return fig

# --- Sidebar ---
with st.sidebar:
    st.title("🛡️ Intelligence Hub")
    if st.button("🔄 Sync Live Intelligence", width="stretch"):
        with st.status("Recalculating Strategic Matrices...") as status:
            new_count = mgr.update_news()
            status.update(label=f"Analysis Complete: {new_count} news updated", state="complete")
        st.rerun()

# --- Main Body (Original Titles Retained) ---
st.title("🛡️ Geopolitical Vulnerability Index")
st.markdown("Real-time tracking of foreign influence via Debt, Military, and Resource Dependency data.")

st.divider()

st.subheader("📰 Strategic Intelligence Feed")

# Fetch Data
df = mgr.fetch_articles(limit=15)

if df.empty:
    st.info("No data found. Please sync intelligence from the sidebar.")
else:
    # FIXED: Using idx to prevent NameError and Duplicate ID Error
    for idx, row in df.iterrows():
        with st.container():
            col1, col2, col3 = st.columns([1, 2, 1])
            
            with col1:
                # Image handling
                img = row['image_url'] if row['image_url'] else "https://via.placeholder.com/400x250?text=No+Image"
                st.image(img, width="stretch")
            
            with col2:
                risk_emoji = "🔴" if row['contextual_score'] >= 0.8 else "🟡" if row['contextual_score'] >= 0.5 else "🟢"
                st.markdown(f"### {risk_emoji} {row['title']}")
                st.caption(f"**SOURCE:** {row['media_outlet']} | **DATE:** {row['published_at']}")
                st.write(row['raw_text'])
                
                # Metadata Buttons (Disabled for tag look)
                t1, t2, t3 = st.columns(3)
                t1.button(f"👤 {row['actor']}", key=f"act_{idx}", disabled=True)
                t2.button(f"📍 {row['country']}", key=f"cou_{idx}", disabled=True)
                t3.button(f"🎯 {row['intent_type']}", key=f"int_{idx}", disabled=True)
                
                st.link_button("View Source Article", row['url'])

            with col3:
                st.markdown("<p style='text-align:center; font-weight:bold;'>Risk Dimension Analysis</p>", unsafe_allow_html=True)
                radar_fig = create_radar_chart(row['contextual_score'], row['intent_type'])
                
                # FIXED: Unique key and new stretch width
                st.plotly_chart(
                    radar_fig, 
                    width="stretch", 
                    key=f"radar_plotly_{idx}", 
                    config={'displayModeBar': False}
                )
                
                # Score display instead of top cards
                score_pct = int(row['contextual_score'] * 100)
                st.markdown(f"<h2 style='text-align:center;'>{score_pct}%</h2>", unsafe_allow_html=True)
                st.markdown("<p style='text-align:center; font-size:0.8em;'>CONTEXTUAL SCORE</p>", unsafe_allow_html=True)

st.markdown("---")
st.caption("Strategic Vulnerability Index Framework | Data updated via NewsAPI & Groq LLM.")
