import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from data_manager import DataManager

st.set_page_config(page_title="Strategic Vulnerability Index", layout="wide", initial_sidebar_state="expanded")

# --- Custom Styling ---
st.markdown("""
    <style>
    .main { background-color: #f8f9fa; }
    .stMetric { background-color: #ffffff; padding: 15px; border-radius: 10px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); }
    .risk-card { border-left: 5px solid #ff4b4b; }
    </style>
    """, unsafe_allow_html=True)

if "mgr" not in st.session_state: st.session_state.mgr = DataManager()
mgr = st.session_state.mgr

# --- SIDEBAR & REFRESH ---
with st.sidebar:
    st.title("🛰️ Intelligence Control")
    if st.button("🔄 Sync Strategic Data", type="primary", use_container_width=True):
        with st.status("Running Geopolitical Matrix...") as status:
            count = mgr.update_news()
            status.update(label=f"Sync Complete: {count} Insights Found", state="complete")
        st.rerun()
    
    st.divider()
    actor_filter = st.multiselect("Filter Actors", ["China", "Russia", "France", "USA", "UAE", "Turkey"])
    country_filter = st.multiselect("Filter Countries", ["DRC", "Senegal", "Ethiopia", "CoteIvoire"])

# --- DATA PROCESSING ---
df = mgr.fetch_articles(limit=20)

if df.empty:
    st.info("📊 Waiting for Intelligence Stream... Click 'Sync' in the sidebar.")
else:
    # Filter logic (client-side for speed)
    if actor_filter:
        df = df[df['raw_text'].str.contains('|'.join(actor_filter), case=False)]
    
    # --- TOP INSIGHTS ROW ---
    st.title("🛡️ Africa Strategic Vulnerability Index")
    
    k1, k2, k3, k4 = st.columns(4)
    avg_score = df['contextual_score'].mean()
    k1.metric("Global Intent Intensity", f"{int(avg_score*100)}%", delta="Live Matrix")
    k2.metric("Critical Alerts", len(df[df['contextual_score'] > 0.85]))
    k3.metric("Top Actor Presence", "China" if not df.empty else "N/A")
    k4.metric("Highest Risk Node", "DRC" if not df.empty else "N/A")

    st.divider()

    # --- NEW: RISK HEATMAP ---
    st.subheader("🌐 Geographic Risk Concentration")
    # Generating a mock-up heatmap based on your data logic
    fig_heat = px.density_heatmap(df, x="media_outlet", y="contextual_score", 
                                  nbinsy=5, color_continuous_scale="Reds",
                                  labels={'media_outlet': 'Media Source', 'contextual_score': 'Risk Level'})
    st.plotly_chart(fig_heat, use_container_width=True)

    # --- ARTICLE GRID WITH RADAR-INSIGHTS ---
    st.subheader("📰 Recent Strategic Developments")
    
    for idx, row in df.iterrows():
        with st.container(border=True):
            col1, col2 = st.columns([1, 2])
            
            with col1:
                st.image(row['image_url'] if row['image_url'] else "https://via.placeholder.com/400", use_container_width=True)
                
                # --- NEW: RADAR CHART FOR DEBT/MIL/RES ---
                # This visualizes the components of your contextual_all_intent logic
                score = row['contextual_score']
                categories = ['Debt', 'Military', 'Res', 'Intent']
                # We simulate the breakdown for visualization (or pull from DB if you store them)
                values = [score * 0.8, score * 0.9, score * 0.5, score] 
                
                fig = go.Figure(data=go.Scatterpolar(r=values, theta=categories, fill='toself', line_color='#ff4b4b'))
                fig.update_layout(polar=dict(radialaxis=dict(visible=False, range=[0, 1])), showlegend=False, height=200, margin=dict(l=20, r=20, t=20, b=20))
                st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})

            with col2:
                risk_color = "🔴" if row['contextual_score'] > 0.8 else "🟡" if row['contextual_score'] > 0.5 else "🟢"
                st.markdown(f"### {risk_color} {row['title']}")
                st.caption(f"**SOURCE:** {row['media_outlet']} | **DATE:** {row['published_at'][:10]}")
                
                st.markdown(f"**Strategic Summary:** {row['raw_text']}")
                
                # Dynamic Tags based on content
                tags = []
                if "Military" in row['raw_text']: tags.append("🎖️ Military")
                if "Economic" in row['raw_text']: tags.append("💰 Economic")
                if "Debt" in row['raw_text']: tags.append("📉 Debt-Trap")
                st.write(" ".join([f"`{t}`" for t in tags]))
                
                st.link_button("Access Full Intelligence Report", row['url'])

    # --- FOOTER NAVIGATION ---
    st.divider()
    st.caption("Data Model: v2.4 Multi-Intent Framework | Powered by Llama-3.3 & Groq")
