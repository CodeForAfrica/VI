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

# --- COMPREHENSIVE PDF LOGIC ---
def create_comprehensive_report(df, country_filter, actor_filter):
    pdf = FPDF()
    
    def clean_text(text):
        if not text: return ""
        replacements = {'\u2018':"'", '\u2019':"'", '\u201c':'"', '\u201d':'"', '\u2013':'-', '\u2014':'-', '\u2026':'...'}
        for u, a in replacements.items(): text = text.replace(u, a)
        return text.encode('latin-1', 'ignore').decode('latin-1')

    pdf.add_page()
    pdf.set_fill_color(30, 35, 45)
    pdf.rect(0, 0, 210, 40, 'F')
    
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("helvetica", 'B', 22)
    pdf.cell(0, 20, "STRATEGIC INTEL DOSSIER", align='C', new_x="LMARGIN", new_y="NEXT")
    
    pdf.set_text_color(0, 0, 0)
    pdf.ln(20)
    pdf.set_font("helvetica", 'B', 14)
    pdf.cell(0, 10, "1. EXECUTIVE OVERVIEW", new_x="LMARGIN", new_y="NEXT")
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(5)
    
    avg_score = int(df['contextual_score'].mean() * 100) if not df.empty else 0
    pdf.set_font("helvetica", '', 12)
    pdf.multi_cell(0, 10, clean_text(
        f"This comprehensive report aggregates {len(df)} intelligence entries. "
        f"The average Strategic Vulnerability Index for this dataset is {avg_score}%. "
        f"Filters applied: Nation [{country_filter}] | Actor [{actor_filter}]. "
        f"Generated on {datetime.now().strftime('%Y-%m-%d %H:%M')}."
    ))
    
    pdf.ln(10)
    pdf.set_font("helvetica", 'B', 14)
    pdf.cell(0, 10, "2. CONSOLIDATED INTELLIGENCE LEDGER", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)

    for idx, row in df.iterrows():
        if pdf.get_y() > 240: pdf.add_page()
        pdf.set_font("helvetica", 'B', 11)
        pdf.set_fill_color(245, 245, 245)
        pdf.cell(0, 8, clean_text(f"ID-{idx+100}: {row['title']}"), fill=True, new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("helvetica", 'B', 9)
        pdf.cell(0, 6, f"SCORE: {int(row['contextual_score']*100)}% | ACTOR: {row['actor']} | TARGET: {row['country']}", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("helvetica", '', 10)
        pdf.multi_cell(0, 6, clean_text(row['summary']))
        pdf.ln(5)

    return bytes(pdf.output())

# --- Luxury Dossier Styling ---
st.markdown("""
    <style>
    .stApp { background-color: #0b0e14; color: #e6edf3; }
    .dossier-card {
        background: linear-gradient(145deg, #161b22, #0d1117);
        border: 1px solid #30363d;
        border-left: 5px solid #ff4b4b;
        border-radius: 8px; padding: 24px; margin-bottom: 25px;
    }
    .intel-badge {
        display: inline-block; padding: 2px 10px; border-radius: 4px;
        font-size: 0.7rem; font-weight: bold; background: #0d1117;
        border: 1px solid #444c56; margin-right: 5px; color: #8b949e;
    }
    .risk-circle {
        border: 2px solid #30363d; border-radius: 50%;
        width: 100px; height: 100px; display: flex; flex-direction: column;
        justify-content: center; align-items: center;
        background: rgba(255, 75, 75, 0.05); margin: 10px auto;
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
        * **Media Tones:** <span style='color:#2ecc71'>Factual</span>, <span style='color:#ffa500'>Sensationalist</span>, <span style='color:#ff4b4b'>Alarmist</span>, <span style='color:#9b59b6'>Cynical</span>.
        """, unsafe_allow_html=True)

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

# --- Header ---
st.title("🛡️ Strategic Vulnerability Command")
show_metric_legend()

# --- Command Center ---
with st.container(border=True):
    st.markdown("### 🔍 Strategic Filters")
    c1, c2, c3, c4 = st.columns(4)
    with c1: f_country = st.selectbox("📍 Target Nation", ["All Nations"] + mgr.countries)
    with c2: f_actor = st.selectbox("👤 Foreign Actor", ["All Actors"] + mgr.actors)
    with c3: f_intent = st.selectbox("🎯 Primary Intent", ["All Intents"] + list(mgr.INTENT_FACTORS.keys()))
    with c4: f_tone = st.selectbox("🎭 Media Tone", ["All Tones", "Factual", "Alarmist", "Sensationalist", "Cynical"])
    
    st.markdown("---")
    sc1, sc2, sc3 = st.columns([2, 2, 4])
    with sc1:
        if st.button("🔄 Sync Global Intelligence", use_container_width=True):
            count = mgr.update_news()
            st.cache_data.clear()
            st.rerun()
    with sc2:
        if st.button("🗑️ Reset Database", use_container_width=True):
            mgr.clear_db()
            st.rerun()

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

    # Filter Logic
    f_df = df.copy()
    if f_country != "All Nations": f_df = f_df[f_df['country'] == f_country]
    if f_actor != "All Actors": f_df = f_df[f_df['actor'] == f_actor]
    if f_intent != "All Intents": f_df = f_df[f_df['intent_type'] == f_intent]
    if f_tone != "All Tones": f_df = f_df[f_df['tone'] == f_tone]

    # MASTER PDF BUTTON
    with sc3:
        if not f_df.empty:
            master_pdf = create_comprehensive_report(f_df, f_country, f_actor)
            st.download_button(
                label=f"📥 Download Comprehensive Report ({len(f_df)} items)",
                data=master_pdf,
                file_name="Intelligence_Summary_Report.pdf",
                mime="application/pdf",
                use_container_width=True
            )

    # --- Pagination ---
    items_per_page = 6
    if "page" not in st.session_state: st.session_state.page = 1
    total_pages = max(1, (len(f_df) // items_per_page) + (1 if len(f_df) % items_per_page > 0 else 0))
    
    st.markdown("---")
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
                        <h2 style="margin: 15px 0 10px 0;">{row['title']}</h2>
                        <p style="color: #8b949e; font-size: 0.95rem;">{row['summary']}</p>
                    </div>
                    <div style="width: 20%; text-align: center;">
                        <div class="risk-circle">
                            <span style="font-size: 1.8rem; font-weight: bold; color: #ff4b4b;">{int(row['contextual_score']*100)}%</span>
                        </div>
                    </div>
                </div>
            </div>
        """, unsafe_allow_html=True)

        c1, c2, c3 = st.columns([1.2, 2, 1.2])
        with c1: st.image(row['image_url'] if row['image_url'] else "https://via.placeholder.com/400", use_container_width=True)
        with c2:
            st.write(f"**Tone:** {row['tone']} | **Source:** {row['media_outlet']}")
            st.link_button("View Source", row['url'], use_container_width=True)
        with c3:
            st.plotly_chart(create_radar(row['contextual_score'], row['title'], row['tone']), use_container_width=True, key=f"radar_{idx}")
else:
    st.info("No intelligence data found.")
