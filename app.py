import streamlit as st
import pandas as pd
from data_manager import DataManager
import plotly.graph_objects as go
from datetime import datetime, timedelta
from fpdf import FPDF, XPos, YPos
import re

# --- Page Config ---
st.set_page_config(page_title="Strategic Vulnerability Index", page_icon="🛡️", layout="wide")

# --- PDF Helpers ---
def safe_text(text):
    if not text: return ""
    text = str(text)
    text = re.sub(r'[‘’]', "'", text); text = re.sub(r'[“”]', '"', text); text = re.sub(r'[—–]', "-", text)
    return text.encode('ascii', 'ignore').decode('ascii')

def generate_pdf(df, month_str):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("helvetica", 'B', 16)
    pdf.cell(0, 10, f"Strategic Brief: {month_str}", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='C')
    pdf.ln(10)
    for _, row in df.iterrows():
        pdf.set_font("helvetica", 'B', 11)
        pdf.multi_cell(0, 7, safe_text(row['title']))
        pdf.set_font("helvetica", 'I', 8)
        pdf.cell(0, 5, safe_text(f"Source: {row['media_outlet']} | Risk: {int(row['contextual_score']*100)}%"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(5)
        if pdf.get_y() > 260: pdf.add_page()
    return pdf.output()

# --- Custom Styling ---
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
    .filter-container {
        background-color: #1c2128;
        padding: 15px;
        border-radius: 10px;
        border: 1px solid #444;
        margin-bottom: 20px;
    }
    .metric-text { font-size: 0.85em; color: #888; text-transform: uppercase; }
    </style>
""", unsafe_allow_html=True)

# Initialize
if "mgr" not in st.session_state:
    st.session_state.mgr = DataManager()
mgr = st.session_state.mgr

# --- Radar Chart Function ---
def create_radar_chart(score, intent_type):
    categories = ['Debt', 'Military', 'Resources', 'Fragility']
    if intent_type == "Economic": values = [score, score*0.2, score*0.9, score*0.3]
    elif intent_type == "MilitaryPresence": values = [score*0.3, score, score*0.2, score*0.6]
    else: values = [score*0.5, score*0.5, score*0.4, score]
    
    fig = go.Figure(data=go.Scatterpolar(
        r=values, theta=categories, fill='toself', 
        fillcolor='rgba(255, 75, 75, 0.4)', line=dict(color='#ff4b4b', width=2)
    ))
    fig.update_layout(
        polar=dict(
            radialaxis=dict(visible=False, range=[0, 1]),
            angularaxis=dict(gridcolor="#444", linecolor="#444")
        ),
        showlegend=False, height=180, margin=dict(l=40, r=40, t=20, b=20),
        paper_bgcolor='rgba(0,0,0,0)', font_color="#aaa"
    )
    return fig

# --- Header ---
st.title("🛡️ Geopolitical Vulnerability Index")

# --- Filters Section ---
st.markdown('<div class="filter-container">', unsafe_allow_html=True)
f1, f2, f3, f4 = st.columns([2, 2, 2, 1])
with f1: f_actor = st.selectbox("Foreign Actor", ["All Actors"] + mgr.actors)
with f2: f_country = st.selectbox("Target Nation", ["All Countries"] + mgr.countries)
with f3: f_intent = st.selectbox("Primary Intent", ["All Intents"] + list(mgr.INTENT_FACTORS.keys()))

# Fetching Data
raw_df = mgr.fetch_articles(limit=500)
if not raw_df.empty:
    raw_df['published_at'] = pd.to_datetime(raw_df['published_at'])
    raw_df['month_year'] = raw_df['published_at'].dt.to_period('M')
    available_months = sorted(raw_df['month_year'].unique(), reverse=True)
    
    if "m_idx" not in st.session_state: st.session_state.m_idx = 0
    current_m = available_months[st.session_state.m_idx]

    # PDF Export in Filter Row
    with f4:
        st.write("")
        df_pdf = raw_df[raw_df['month_year'] == current_m]
        if not df_pdf.empty:
            try:
                pdf_bytes = generate_pdf(df_pdf, str(current_m))
                st.download_button("📥 Export PDF", data=pdf_bytes, file_name=f"Report_{current_m}.pdf")
            except: st.error("PDF Fail")
    st.markdown('</div>', unsafe_allow_html=True)

    # Nav Buttons
    nb1, nb2, nb3 = st.columns([1, 4, 1])
    with nb1: 
        if st.button("⬅️ Previous Month"):
            if st.session_state.m_idx < len(available_months) - 1:
                st.session_state.m_idx += 1
                # Trigger historical fetch for previous month if data might be missing
                target_date = (datetime.now().replace(day=1) - timedelta(days=30 * st.session_state.m_idx)).strftime("%Y-%m-%d")
                mgr.update_news(start_date=target_date)
                st.rerun()
    with nb3:
        if st.button("Next Month ➡️") and st.session_state.m_idx > 0:
            st.session_state.m_idx -= 1
            st.rerun()
            
    st.subheader(f"📅 Intelligence for {current_m.strftime('%B %Y')}")

    # Filtering for display
    df = raw_df[raw_df['month_year'] == current_m]
    if f_actor != "All Actors": df = df[df['actor'] == f_actor]
    if f_country != "All Countries": df = df[df['country'] == f_country]
    if f_intent != "All Intents": df = df[df['intent_type'] == f_intent]

    for idx, row in df.iterrows():
        with st.container():
            col1, col2, col3 = st.columns([1, 2, 1])
            with col1:
                st.image(row['image_url'] if row['image_url'] else "https://via.placeholder.com/400", use_container_width=True)
            with col2:
                st.markdown(f"### {row['title']}")
                st.caption(f"**SOURCE:** {row['media_outlet']} | **DATE:** {row['published_at'].strftime('%Y-%m-%d')}")
                st.write(row['raw_text'][:500] + "...")
                with st.expander("🔍 Strategic Insights", expanded=True):
                    if row['intent_type'] == "Economic":
                        st.info(f"**Insight:** High dependency on **Debt** and **Resources** detected.")
                    elif row['intent_type'] == "MilitaryPresence":
                        st.warning(f"**Insight:** This increases long-term **Military** and **Fragility** risks.")
                    else:
                        st.success(f"**Insight:** Monitoring for shifts in **Social Fragility**.")
                st.link_button("View Intelligence Source", row['url'])
            with col3:
                st.markdown("<p style='text-align:center;' class='metric-text'>Risk Distribution</p>", unsafe_allow_html=True)
                st.plotly_chart(create_radar_chart(row['contextual_score'], row['intent_type']), use_container_width=True, key=f"r_{idx}")
                score_pct = int(row['contextual_score'] * 100)
                color = "#ff4b4b" if score_pct > 70 else "#ffa500" if score_pct > 40 else "#00ff00"
                st.markdown(f"<h1 style='text-align:center; color:{color}; margin-bottom:0;'>{score_pct}%</h1>", unsafe_allow_html=True)
                st.markdown("<p style='text-align:center;' class='metric-text'>Vulnerability Index</p>", unsafe_allow_html=True)
else:
    st.info("No data found for the current selection.")
