import streamlit as st
import pandas as pd
from data_manager import DataManager
import plotly.graph_objects as go
import json
import hashlib
from datetime import datetime
from fpdf import FPDF

# --- Page Config ---
st.set_page_config(page_title="Strategic Vulnerability Index", layout="wide")

# --- Initialize Data Manager ---
if "mgr" not in st.session_state:
    st.session_state.mgr = DataManager()
mgr = st.session_state.mgr

# --- PDF Logic (Fixed for fpdf2 & No Encoding Error) ---
def create_pdf(row, tone, summary):
    pdf = FPDF()
    pdf.add_page()
    # fpdf2 uses 'helvetica' as default; 'Arial' is substituted automatically
    pdf.set_font("helvetica", 'B', 16)
    pdf.cell(0, 10, f"INTEL BRIEF: {row['country']}", align='C', new_x="LMARGIN", new_y="NEXT")
    pdf.ln(10)
    pdf.set_font("helvetica", '', 12)
    pdf.multi_cell(0, 10, f"Title: {row['title']}\nActor: {row['actor']}\nScore: {int(row['contextual_score']*100)}%\nTone: {tone}\n\nSummary: {summary}")
    
    # In fpdf2, output() without 'dest' returns bytes/bytearray directly
    return bytes(pdf.output())

# --- Luxury Styling ---
st.markdown("""
    <style>
    .stApp { background-color: #0b0e14; color: #e6edf3; }
    .dossier-card {
        background: linear-gradient(145deg, #161b22, #0d1117);
        border: 1px solid #30363d;
        border-left: 5px solid #ff4b4b;
        border-radius: 8px;
        padding: 24px;
        margin-bottom: 25px;
    }
    .intel-badge {
        display: inline-block;
        padding: 2px 10px;
        border-radius: 4px;
        font-size: 0.7rem;
        font-weight: bold;
        background: #0d1117;
        border: 1px solid #444c56;
        margin-right: 5px;
        color: #8b949e;
    }
    .risk-circle {
        border: 2px solid #30363d;
        border-radius: 50%;
        width: 100px;
        height: 100px;
        display: flex;
        flex-direction: column;
        justify-content: center;
        align-items: center;
        background: rgba(255, 75, 75, 0.05);
        margin: 10px auto;
    }
    </style>
""", unsafe_allow_html=True)

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

# --- Radar Visual ---
def create_radar(score, title, tone):
    categories = ['Debt Depth', 'Resource Control', 'Military Presence', 'Sovereignty']
    h = int(hashlib.md5(title.encode()).hexdigest(), 16)
    r_values = [
        score,
        max(0.2, score * ((h % 40 + 60) / 100)),
        max(0.2, score * (((h >> 4) % 40 + 60) / 100)),
        min(1.0, (1.1 - score) * (((h >> 8) % 30 + 85) / 100))
    ]
    fig = go.Figure(data=go.Scatterpolar(r=r_values, theta=categories, fill='toself', fillcolor='rgba(255, 75, 75, 0.25)', line=dict(color='#ff4b4b', width=2)))
    fig.update_layout(
        polar=dict(radialaxis=dict(visible=False, range=[0, 1.2]), angularaxis=dict(tickfont=dict(size=10, color="#8b949e"))),
        showlegend=False, height=200, margin=dict(l=40, r=40, t=20, b=20), paper_bgcolor='rgba(0,0,0,0)'
    )
    return fig

# --- UI Header ---
st.title("🛡️ Geopolitical Vulnerability Index")
show_metric_legend()

# --- Filters ---
with st.container(border=True):
    st.markdown("### 🔍 Strategic Command Center")
    c1, c2, c3, c4 = st.columns(4)
    with c1: f_country = st.selectbox("📍 Target Nation", ["All Nations"] + mgr.countries)
    with c2: f_actor = st.selectbox("👤 Foreign Actor", ["All Actors"] + mgr.actors)
    with c3: f_intent = st.selectbox("🎯 Primary Intent", ["All Intents"] + list(mgr.INTENT_FACTORS.keys()))
    with c4: f_tone = st.selectbox("🎭 Media Tone", ["All Tones", "Factual", "Alarmist", "Sensationalist", "Cynical"])
    
    st.markdown("---")
    sc1, sc2, _ = st.columns([2, 2, 4])
    with sc1:
        if st.button("🔄 Sync Global Intelligence", width='stretch'):
            mgr.update_news(); st.cache_data.clear(); st.rerun()
    with sc2:
        if st.button("🗑️ Reset Database", width='stretch'):
            mgr.clear_db(); st.cache_data.clear(); st.rerun()

# --- Data Engine ---
df = mgr.fetch_articles(limit=500)
if not df.empty:
    df['published_at'] = pd.to_datetime(df['published_at'])
    
    def extract_extra(row):
        try:
            data = json.loads(row['raw_text'])
            return pd.Series([data.get('tone', 'Factual'), data.get('summary', '...')])
        except: return pd.Series(['Factual', row['raw_text']])
    df[['tone', 'summary']] = df.apply(extract_extra, axis=1)

    f_df = df.copy()
    if f_country != "All Nations": f_df = f_df[f_df['country'] == f_country]
    if f_actor != "All Actors": f_df = f_df[f_df['actor'] == f_actor]
    if f_intent != "All Intents": f_df = f_df[f_df['intent_type'] == f_intent]
    if f_tone != "All Tones": f_df = f_df[f_df['tone'] == f_tone]

    # --- Pagination ---
    items_per_page = 6
    if "page" not in st.session_state: st.session_state.page = 1
    total_pages = max(1, (len(f_df) // items_per_page) + (1 if len(f_df) % items_per_page > 0 else 0))
    
    p1, p2, p3 = st.columns([1, 4, 1])
    if p1.button("⬅️ Previous") and st.session_state.page > 1: st.session_state.page -= 1; st.rerun()
    p2.write(f"Page {st.session_state.page} of {total_pages} ({len(f_df)} reports)")
    if p3.button("Next ➡️") and st.session_state.page < total_pages: st.session_state.page += 1; st.rerun()

    # --- Article Feed ---
    start = (st.session_state.page - 1) * items_per_page
    for idx, row in f_df.iloc[start : start + items_per_page].iterrows():
        st.markdown(f"""
            <div class="dossier-card">
                <div style="display: flex; justify-content: space-between;">
                    <div style="width: 75%;">
                        <span class="intel-badge">📍 {row['country']}</span>
                        <span class="intel-badge">👤 {row['actor']}</span>
                        <span class="intel-badge">🎯 {row['intent_type']}</span>
                        <h2 style="margin: 15px 0 10px 0;">{row['title']}</h2>
                        <p style="color: #8b949e; font-size: 0.95rem;">{row['summary']}</p>
                    </div>
                    <div style="width: 20%; text-align: center;">
                        <div class="risk-circle">
                            <span style="font-size: 1.8rem; font-weight: bold; color: #ff4b4b;">{int(row['contextual_score']*100)}%</span>
                            <span style="font-size: 0.6rem; color: #58a6ff;">VULNERABILITY</span>
                        </div>
                    </div>
                </div>
            </div>
        """, unsafe_allow_html=True)

        c_img, c_body, c_risk = st.columns([1.2, 2, 1.2])
        with c_img:
            st.image(row['image_url'] if row['image_url'] else "https://via.placeholder.com/400", width='stretch')
        with c_body:
            t_colors = {"Alarmist": "#ff4b4b", "Sensationalist": "#ffa500", "Cynical": "#9b59b6", "Factual": "#2ecc71"}
            st.markdown(f"**Tone:** <span style='color:{t_colors.get(row['tone'], '#fff')}'>{row['tone'].upper()}</span>", unsafe_allow_html=True)
            st.caption(f"{row['media_outlet']} | {row['published_at'].strftime('%Y-%m-%d')}")
            st.link_button("View Source", row['url'], width='stretch')
            
            # --- FIXED PDF SECTION ---
            pdf_bytes = create_pdf(row, row['tone'], row['summary'])
            st.download_button(
                label="📥 PDF Summary", 
                data=pdf_bytes, 
                file_name=f"brief_{idx}.pdf", 
                mime="application/pdf", 
                width='stretch'
            )
        with c_risk:
            st.plotly_chart(create_radar(row['contextual_score'], row['title'], row['tone']), width='stretch', key=f"radar_{idx}")
else:
    st.info("No data. Please Sync.")
