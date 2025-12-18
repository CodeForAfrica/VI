import streamlit as st
import pandas as pd
from data_manager import DataManager
import plotly.graph_objects as go
import plotly.express as px
import json
from datetime import datetime
from fpdf import FPDF
import io

# --- Page Config ---
st.set_page_config(page_title="Strategic Vulnerability Command", layout="wide")

# --- Initialize Data Manager ---
if "mgr" not in st.session_state:
    st.session_state.mgr = DataManager()
mgr = st.session_state.mgr

# --- Luxury Styling ---
st.markdown("""
    <style>
    .stApp { background-color: #0b0e14; color: #e6edf3; }
    
    /* Dossier Card Design */
    .dossier-card {
        background: rgba(22, 27, 34, 0.7);
        border: 1px solid #30363d;
        border-left: 5px solid #ff4b4b;
        border-radius: 10px;
        padding: 20px;
        margin-bottom: 20px;
        box-shadow: 0 4px 15px rgba(0,0,0,0.3);
    }
    
    /* Neon Badge */
    .metric-badge {
        display: inline-block;
        padding: 2px 10px;
        border-radius: 4px;
        font-size: 0.75rem;
        font-weight: bold;
        text-transform: uppercase;
        background: #161b22;
        border: 1px solid #444c56;
        margin-right: 5px;
    }
    </style>
""", unsafe_allow_html=True)

# --- PDF Intelligence Brief Utility ---
def create_pdf_brief(row, tone, summary):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_fill_color(11, 14, 20)
    pdf.rect(0, 0, 210, 297, 'F')
    
    pdf.set_text_color(255, 75, 75)
    pdf.set_font("Arial", 'B', 20)
    pdf.cell(0, 20, "STRATEGIC INTELLIGENCE BRIEF", ln=True, align='C')
    
    pdf.set_text_color(200, 200, 200)
    pdf.set_font("Arial", 'B', 12)
    pdf.cell(0, 10, f"TARGET NATION: {row['country']}", ln=True)
    pdf.cell(0, 10, f"FOREIGN ACTOR: {row['actor']}", ln=True)
    pdf.cell(0, 10, f"VULNERABILITY SCORE: {int(row['contextual_score']*100)}%", ln=True)
    pdf.cell(0, 10, f"MEDIA TONE: {tone.upper()}", ln=True)
    
    pdf.ln(10)
    pdf.set_font("Arial", '', 11)
    pdf.set_text_color(255, 255, 255)
    pdf.multi_cell(0, 10, f"ANALYSIS SUMMARY:\n{summary}")
    return pdf.output(dest='S').encode('latin-1')

# --- Metric Legend ---
def show_metric_legend():
    with st.expander("ℹ️ Understanding Vulnerability Metrics & Matrix Factors"):
        st.markdown("""
        ### Strategic Metrics Breakdown
        * **Vulnerability Score:** A weighted index (0-100%) where **>70%** indicates critical foreign influence.
        * **Matrix Factors:** Based on Debt-to-GDP ratios, Resource concessions, and Military agreements.
        * **Media Tones:** <span style='color:#2ecc71'>Factual</span>, <span style='color:#ffa500'>Sensationalist</span>, <span style='color:#ff4b4b'>Alarmist</span>, <span style='color:#9b59b6'>Cynical</span>.
        """, unsafe_allow_html=True)

# --- Radar Visual ---
def create_radar(score, tone):
    categories = ['Debt Depth', 'Resource Control', 'Military Presence', 'Sovereignty']
    mod = 1.1 if tone == "Alarmist" else 1.0
    r_values = [score * mod, score * 0.7, score * 0.5, score * 0.8]
    fig = go.Figure(data=go.Scatterpolar(r=r_values, theta=categories, fill='toself', fillcolor='rgba(255, 75, 75, 0.3)', line=dict(color='#ff4b4b', width=2)))
    fig.update_layout(polar=dict(radialaxis=dict(visible=False, range=[0, 1.2]), angularaxis=dict(tickfont=dict(size=10, color="#888"))), showlegend=False, height=220, margin=dict(l=40, r=40, t=20, b=20), paper_bgcolor='rgba(0,0,0,0)')
    return fig

# --- Main Interface ---
st.title("🛡️ Geopolitical Intelligence Command")
show_metric_legend()

# --- Command Center (Filters) ---
with st.container(border=True):
    st.markdown("### 🔍 Strategic Filters")
    c1, c2, c3, c4 = st.columns(4)
    with c1: f_country = st.selectbox("📍 Nation", ["All Nations"] + mgr.countries)
    with c2: f_actor = st.selectbox("👤 Actor", ["All Actors"] + mgr.actors)
    with c3: f_intent = st.selectbox("🎯 Intent", ["All Intents"] + list(mgr.INTENT_FACTORS.keys()))
    with c4: f_tone = st.selectbox("🎭 Tone", ["All Tones", "Factual", "Alarmist", "Sensationalist", "Cynical"])
    
    sync1, sync2, _ = st.columns([2, 2, 4])
    if sync1.button("🔄 Sync Global Intel", use_container_width=True):
        mgr.update_news(); st.cache_data.clear(); st.rerun()
    if sync2.button("🗑️ Reset Database", use_container_width=True):
        mgr.clear_db(); st.cache_data.clear(); st.rerun()

# --- Logic & Processing ---
df = mgr.fetch_articles(limit=500)
if not df.empty:
    df['published_at'] = pd.to_datetime(df['published_at'])
    
    def extract_extra(row):
        try:
            data = json.loads(row['raw_text'])
            return pd.Series([data.get('tone', 'Factual'), data.get('summary', '...')])
        except: return pd.Series(['Factual', row['raw_text']])
    df[['tone', 'summary']] = df.apply(extract_extra, axis=1)

    # Filtering
    f_df = df.copy()
    if f_country != "All Nations": f_df = f_df[f_df['country'] == f_country]
    if f_actor != "All Actors": f_df = f_df[f_df['actor'] == f_actor]
    if f_intent != "All Intents": f_df = f_df[f_df['intent_type'] == f_intent]
    if f_tone != "All Tones": f_df = f_df[f_df['tone'] == f_tone]

    # --- Trend Chart ---
    if not f_df.empty:
        st.subheader("📈 Vulnerability Velocity")
        trend = f_df.groupby(f_df['published_at'].dt.date)['contextual_score'].mean().reset_index()
        fig_trend = px.line(trend, x='published_at', y='contextual_score', template="plotly_dark")
        fig_trend.update_traces(line_color='#ff4b4b', line_width=3, mode='lines+markers')
        fig_trend.update_layout(height=250, margin=dict(l=0, r=0, t=10, b=0))
        st.plotly_chart(fig_trend, use_container_width=True)

    # --- Pagination ---
    items_per_page = 6
    if "page" not in st.session_state: st.session_state.page = 1
    total_pages = max(1, (len(f_df) // items_per_page) + (1 if len(f_df)%items_per_page > 0 else 0))
    
    st.markdown("---")
    p1, p2, p3 = st.columns([1, 4, 1])
    if p1.button("⬅️ Prev") and st.session_state.page > 1: st.session_state.page -= 1; st.rerun()
    p2.write(f"Record {st.session_state.page} of {total_pages} | Intelligence Count: {len(f_df)}")
    if p3.button("Next ➡️") and st.session_state.page < total_pages: st.session_state.page += 1; st.rerun()

    # --- Intelligence Feed (Dossier View) ---
    start = (st.session_state.page - 1) * items_per_page
    for idx, row in f_df.iloc[start : start + items_per_page].iterrows():
        st.markdown(f"""
            <div class="dossier-card">
                <div style="display: flex; justify-content: space-between;">
                    <div style="width: 70%;">
                        <span class="metric-badge">📍 {row['country']}</span>
                        <span class="metric-badge">👤 {row['actor']}</span>
                        <span class="metric-badge">🎯 {row['intent_type']}</span>
                        <h2 style="margin: 10px 0; color: #fff;">{row['title']}</h2>
                        <p style="color: #8b949e; font-size: 0.95rem;">{row['summary']}</p>
                    </div>
                    <div style="text-align: right; width: 25%;">
                        <h1 style="color: #ff4b4b; margin: 0; font-size: 3.5rem;">{int(row['contextual_score']*100)}%</h1>
                        <small style="letter-spacing: 2px;">VULNERABILITY</small>
                    </div>
                </div>
            </div>
        """, unsafe_allow_html=True)
        
        # Visuals & Actions Row
        col_r, col_act = st.columns([1.5, 3])
        with col_r:
            st.plotly_chart(create_radar(row['contextual_score'], row['tone']), key=f"r_{idx}", use_container_width=True)
        with col_act:
            st.markdown(f"**Media Tone:** `{row['tone']}` | **Outlet:** `{row['media_outlet']}`")
            st.link_button("🌐 Open Source Dossier", row['url'], use_container_width=True)
            pdf_data = create_pdf_brief(row, row['tone'], row['summary'])
            st.download_button("📥 Export Intelligence Brief (PDF)", pdf_data, f"Intel_Brief_{idx}.pdf", "application/pdf", use_container_width=True)
        st.markdown("<br>", unsafe_allow_html=True)

    with st.expander("🗄️ Raw Article Database"):
        st.dataframe(f_df, use_container_width=True)
else:
    st.info("System idle. No intelligence signals detected.")
