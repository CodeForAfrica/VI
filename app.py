import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from data_manager import DataManager

# --- Page Config ---
st.set_page_config(
    page_title="Strategic Vulnerability Index",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- Custom CSS for Professional Look ---
st.markdown("""
    <style>
    .stMetric { background-color: #ffffff; padding: 15px; border-radius: 10px; border: 1px solid #e6e9ef; }
    .css-1r6slb0 { background-color: #f0f2f6; }
    .risk-high { border-left: 5px solid #ff4b4b; }
    .risk-low { border-left: 5px solid #00c853; }
    </style>
    """, unsafe_allow_html=True)

# --- Initialization ---
if "mgr" not in st.session_state:
    st.session_state.mgr = DataManager()
mgr = st.session_state.mgr

# --- Helper Function: Radar Chart ---
def create_radar_chart(score, intent_type):
    # Simulated breakdown based on your contextual_all_intent logic components
    categories = ['Debt', 'Military', 'Resources', 'Social Fragility']
    
    # Logic to shape the radar based on the Intent Type extracted by LLM
    if intent_type == "Economic":
        values = [score, score * 0.4, score * 0.8, score * 0.3]
    elif intent_type == "MilitaryPresence":
        values = [score * 0.5, score, score * 0.3, score * 0.6]
    else: # SocialFragility
        values = [score * 0.4, score * 0.5, score * 0.2, score]

    fig = go.Figure(data=go.Scatterpolar(
        r=values,
        theta=categories,
        fill='toself',
        line_color='#FF4B4B'
    ))

    fig.update_layout(
        polar=dict(radialaxis=dict(visible=False, range=[0, 1])),
        showlegend=False,
        height=200,
        margin=dict(l=30, r=30, t=10, b=10),
        paper_bgcolor='rgba(0,0,0,0)',
    )
    return fig

# --- Sidebar ---
with st.sidebar:
    st.title("🛡️ Intelligence Hub")
    st.markdown("---")
    
    if st.button("🔄 Sync Live Intelligence", type="primary", use_container_width=True):
        with st.status("Recalculating Strategic Matrices...") as status:
            new_count = mgr.update_news()
            status.update(label=f"Analysis Complete: {new_count} news updated", state="complete")
        st.rerun()

    st.markdown("### Filters")
    actor_filter = st.multiselect("Actors", ["China", "Russia", "USA", "France", "UAE", "Turkey"])
    country_filter = st.multiselect("Target Countries", ["Senegal", "DRC", "CoteIvoire", "Ethiopia"])
    
    st.divider()
    st.caption("Model Version: v2.4 (Contextual Intent Logic)")

# --- Main Dashboard ---
st.title("🛡️ Geopolitical Vulnerability Index")
st.markdown("Real-time tracking of foreign influence via Debt, Military, and Resource Dependency data.")

# Fetch Data
df = mgr.fetch_articles(limit=18)

if df.empty:
    st.warning("No data found in the database. Please use the 'Sync' button in the sidebar to fetch news.")
else:
    # 1. KPI Row
    avg_score = df['contextual_score'].mean()
    high_risks = len(df[df['contextual_score'] >= 0.80])
    
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Avg. Regional Risk", f"{int(avg_score*100)}%", delta="Strategic")
    k2.metric("Critical Alerts", high_risks, delta_color="inverse")
    k3.metric("Nodes Monitored", df['country'].nunique() if 'country' in df.columns else "4")
    k4.metric("Active Sources", df['media_outlet'].nunique())

    # 2. Risk Heatmap (Actor vs Country)
    st.subheader("🌐 Strategic Heatmap: Influence Concentration")
    if 'actor' in df.columns and 'country' in df.columns:
        fig_heat = px.density_heatmap(
            df, x="country", y="actor", z="contextual_score",
            color_continuous_scale="Reds",
            labels={'contextual_score': 'Risk Intensity'}
        )
        st.plotly_chart(fig_heat, use_container_width=True)

    st.divider()

    # 3. News Feed Grid
    st.subheader("📰 Strategic Intelligence Feed")
    
    # Apply Filters (Client Side)
    display_df = df.copy()
    if actor_filter:
        display_df = display_df[display_df['actor'].isin(actor_filter)]
    if country_filter:
        display_df = display_df[display_df['country'].isin(country_filter)]

    for idx, row in display_df.iterrows():
        with st.container(border=True):
            col1, col2, col3 = st.columns([1, 2, 1])
            
            # Left: Image
            with col1:
                st.image(row['image_url'] if row['image_url'] else "https://via.placeholder.com/400", use_container_width=True)
            
            # Middle: Summary & Meta
            with col2:
                risk_emoji = "🔴" if row['contextual_score'] >= 0.80 else "🟡" if row['contextual_score'] >= 0.50 else "🟢"
                st.markdown(f"### {risk_emoji} {row['title']}")
                st.caption(f"**SOURCE:** {row['media_outlet']} | **DATE:** {row['published_at']}")
                st.write(row['raw_text']) # This is the 1-sentence summary
                
                # Tags for Intent
                intent = row.get('intent_type', 'General')
                st.markdown(f"`Intent: {intent}` `Actor: {row.get('actor', 'N/A')}`")
                st.link_button("View Source Article", row['url'])

            # Right: Radar Chart
            with col3:
                st.write("**Risk Dimension Analysis**")
                radar_fig = create_radar_chart(row['contextual_score'], ...)

                # FIX: Add a unique 'key' using the loop index and replace 'use_container_width'
                st.plotly_chart(
                    radar_fig, 
                    width="stretch",           # Replaces use_container_width=True
                    key=f"radar_{index}",      # FIXES the Duplicate ID Error
                    config={'displayModeBar': False}
                )

# Navigation Footer
st.markdown("---")
st.caption("Strategic Vulnerability Index Framework | User: admin | Data updated via NewsAPI & Groq LLM.")
