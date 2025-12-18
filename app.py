import streamlit as st
import pandas as pd
from data_manager import DataManager
import plotly.graph_objects as go

# --- Page Config ---
st.set_page_config(page_title="Strategic Vulnerability Index", page_icon="🛡️", layout="wide")

# --- Custom Styling (Dark theme, no white cards) ---
st.markdown("""
    <style>
    .stApp { background-color: #0e1117; color: #fafafa; }
    [data-testid="stVerticalBlockBorderWrapper"] {
        background-color: #161b22 !important;
        border: 1px solid #30363d !important;
        border-radius: 10px !important;
        padding: 20px !important;
        margin-bottom: 15px !important;
    }
    h1, h2, h3 { color: #ffffff !important; }
    .stButton>button { width: 100%; border-radius: 5px; }
    </style>
""", unsafe_allow_html=True)

# Initialize Manager
if "mgr" not in st.session_state:
    st.session_state.mgr = DataManager()
mgr = st.session_state.mgr

# --- Radar Chart Function ---
def create_radar_chart(score, intent_type):
    categories = ['Debt', 'Military', 'Resources', 'Fragility']
    # Dynamic values based on intent
    if intent_type == "Economic": values = [score, score*0.3, score*0.9, score*0.2]
    elif intent_type == "MilitaryPresence": values = [score*0.4, score, score*0.2, score*0.7]
    else: values = [score*0.5, score*0.5, score*0.4, score]
    
    fig = go.Figure(data=go.Scatterpolar(r=values, theta=categories, fill='toself', line_color='#ff4b4b'))
    fig.update_layout(
        polar=dict(radialaxis=dict(visible=False, range=[0, 1])),
        showlegend=False, height=180, margin=dict(l=30, r=30, t=20, b=20),
        paper_bgcolor='rgba(0,0,0,0)', font_color="white"
    )
    return fig

# --- Header ---
st.title("🛡️ Geopolitical Vulnerability Index")
st.markdown("Real-time tracking of foreign influence and strategic vulnerability.")

# --- Sidebar ---
with st.sidebar:
    st.header("Intelligence Controls")
    if st.button("🔄 Sync Live Intelligence"):
        with st.status("Fetching global signals...") as status:
            mgr.update_news()
            st.cache_data.clear() # Clear cache to show new results
            status.update(label="Sync Complete!", state="complete")
        st.rerun()

st.divider()
st.subheader("📰 Strategic Intelligence Feed")

# --- Feed Grid ---
df = mgr.fetch_articles(limit=15)

if df.empty:
    st.info("No intelligence signals found. Click 'Sync' in the sidebar.")
else:
    # FIXED: Loop with idx to avoid NameError and duplicate Plotly IDs
    for idx, row in df.iterrows():
        with st.container():
            col1, col2, col3 = st.columns([1, 2, 1])
            
            with col1:
                img = row['image_url'] if row['image_url'] else "https://via.placeholder.com/400x250?text=Geopolitical+Intelligence"
                st.image(img, use_container_width=True)
            
            with col2:
                risk_color = "🔴" if row['contextual_score'] >= 0.75 else "🟡" if row['contextual_score'] >= 0.45 else "🟢"
                st.markdown(f"### {risk_color} {row['title']}")
                st.caption(f"**SOURCE:** {row['media_outlet']} | **DATE:** {row['published_at']}")
                st.write(row['raw_text'])
                
                # Tags
                t1, t2, t3 = st.columns(3)
                t1.button(f"👤 {row['actor']}", key=f"a_{idx}", disabled=True)
                t2.button(f"📍 {row['country']}", key=f"c_{idx}", disabled=True)
                t3.button(f"🎯 {row['intent_type']}", key=f"i_{idx}", disabled=True)
                st.link_button("Read Source Intelligence", row['url'])

            with col3:
                st.markdown("<p style='text-align:center; font-size:0.9em; color:#888;'>RISK DIMENSIONS</p>", unsafe_allow_html=True)
                radar_fig = create_radar_chart(row['contextual_score'], row['intent_type'])
                st.plotly_chart(radar_fig, use_container_width=True, key=f"radar_plt_{idx}", config={'displayModeBar': False})
                
                # Score display
                score_pct = int(row['contextual_score'] * 100)
                st.markdown(f"<h2 style='text-align:center;'>{score_pct}%</h2>", unsafe_allow_html=True)
                st.markdown("<p style='text-align:center; font-size:0.8em;'>VULNERABILITY INDEX</p>", unsafe_allow_html=True)

st.markdown("---")
st.caption("Strategic Intelligence Framework | Developed for Geopolitical Risk Assessment")
