import streamlit as st
import pandas as pd
from data_manager import DataManager
import plotly.graph_objects as go
from datetime import datetime
from fpdf import FPDF, XPos, YPos
from io import BytesIO

# --- Page Config ---
st.set_page_config(page_title="Strategic Vulnerability Index", page_icon="🛡️", layout="wide")

# --- Helper: Force Latin-1 Compatibility for PDF ---
def clean_for_pdf(text):
    if not text: return ""
    # Hard-replace common characters that break PDF engines
    text = text.replace('\u2019', "'").replace('\u2018', "'").replace('\u201c', '"').replace('\u201d', '"')
    text = text.replace('\u2014', '-').replace('\u2013', '-')
    return text.encode('latin-1', 'ignore').decode('latin-1')

# --- PDF Generation Function ---
def generate_pdf(df, month_str, actor_f, country_f):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("helvetica", 'B', 16)
    pdf.cell(0, 10, f"Strategic Report: {month_str}", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='C')
    pdf.set_font("helvetica", '', 10)
    pdf.cell(0, 10, f"Scope: {actor_f} | {country_f}", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='C')
    pdf.ln(10)

    for i, row in df.iterrows():
        pdf.set_font("helvetica", 'B', 11)
        pdf.multi_cell(0, 8, clean_for_pdf(f"{row['title']}"))
        pdf.set_font("helvetica", 'I', 8)
        pdf.cell(0, 5, clean_for_pdf(f"Source: {row['media_outlet']} | Risk: {int(row['contextual_score']*100)}%"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(4)
        if pdf.get_y() > 250: pdf.add_page()
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
    }
    .filter-container { background-color: #1c2128; padding: 15px; border-radius: 10px; border: 1px solid #444; margin-bottom: 20px; }
    </style>
""", unsafe_allow_html=True)

if "mgr" not in st.session_state:
    st.session_state.mgr = DataManager()
mgr = st.session_state.mgr

# --- Radar Chart Function ---
def create_radar_chart(score, intent_type):
    categories = ['Debt', 'Military', 'Resources', 'Fragility']
    if intent_type == "Economic": values = [score, score*0.2, score*0.9, score*0.3]
    elif intent_type == "MilitaryPresence": values = [score*0.3, score, score*0.2, score*0.6]
    else: values = [score*0.5, score*0.5, score*0.4, score]
    fig = go.Figure(data=go.Scatterpolar(r=values, theta=categories, fill='toself', fillcolor='rgba(255, 75, 75, 0.4)', line=dict(color='#ff4b4b', width=2)))
    fig.update_layout(polar=dict(radialaxis=dict(visible=False, range=[0, 1]), angularaxis=dict(gridcolor="#444")),
        showlegend=False, height=180, margin=dict(l=40, r=40, t=20, b=20), paper_bgcolor='rgba(0,0,0,0)', font_color="#aaa")
    return fig

# --- Header ---
st.title("🛡️ Africa Geopolitical Vulnerability Index")

# --- INCREASED DATA POOL ---
# We change this to 500 so the Month filter actually sees all data
raw_df = mgr.fetch_articles(limit=500) 

if not raw_df.empty:
    raw_df['published_at'] = pd.to_datetime(raw_df['published_at'])
    raw_df['month_year'] = raw_df['published_at'].dt.to_period('M')
    available_months = sorted(raw_df['month_year'].unique(), reverse=True)

    # State management for Month Index
    if "m_idx" not in st.session_state:
        st.session_state.m_idx = 0

    # --- Filters ---
    st.markdown('<div class="filter-container">', unsafe_allow_html=True)
    f1, f2, f3, f4 = st.columns([2, 2, 2, 1])
    with f1: f_actor = st.selectbox("Foreign Actor", ["All Actors"] + mgr.actors)
    with f2: f_country = st.selectbox("Target Nation", ["All Countries"] + mgr.countries)
    with f3: f_intent = st.selectbox("Primary Intent", ["All Intents"] + list(mgr.INTENT_FACTORS.keys()))

    # --- DATA FILTERING ---
    current_m = available_months[st.session_state.m_idx]
    df_view = raw_df[raw_df['month_year'] == current_m]
    
    if f_actor != "All Actors": df_view = df_view[df_view['actor'] == f_actor]
    if f_country != "All Countries": df_view = df_view[df_view['country'] == f_country]
    if f_intent != "All Intents": df_view = df_view[df_view['intent_type'] == f_intent]

    with f4:
        st.write("") 
        if not df_view.empty:
            try:
                pdf_out = generate_pdf(df_view, current_m.strftime('%B %Y'), f_actor, f_country)
                st.download_button("📥 Export PDF", data=pdf_out, file_name=f"Report_{current_m}.pdf", mime="application/pdf")
            except: st.error("PDF Fail")
    st.markdown('</div>', unsafe_allow_html=True)

    # --- MONTH NAVIGATION ---
    nb1, nb2, nb3 = st.columns([1, 4, 1])
    with nb1: 
        if st.button("⬅️ Previous Month"):
            if st.session_state.m_idx < len(available_months) - 1:
                st.session_state.m_idx += 1
                st.rerun() # Forces the UI to update immediately
    with nb3:
        if st.button("Next Month ➡️"):
            if st.session_state.m_idx > 0:
                st.session_state.m_idx -= 1
                st.rerun() # Forces the UI to update immediately
            
    st.subheader(f"📅 Intelligence Brief: {current_m.strftime('%B %Y')} ({len(df_view)} articles)")

    # --- FEED RENDERING ---
    for idx, row in df_view.iterrows():
        with st.container():
            col1, col2, col3 = st.columns([1, 2, 1])
            with col1:
                st.image(row['image_url'] if row['image_url'] else "https://via.placeholder.com/400", use_container_width=True)
            with col2:
                st.markdown(f"### {row['title']}")
                st.caption(f"**{row['media_outlet']}** | {row['published_at'].strftime('%Y-%m-%d')}")
                st.write(row['raw_text'][:500] + "...")
                st.link_button("View Source", row['url'])
            with col3:
                st.plotly_chart(create_radar_chart(row['contextual_score'], row['intent_type']), use_container_width=True, key=f"r_{idx}")
                score_pct = int(row['contextual_score'] * 100)
                st.markdown(f"<h1 style='text-align:center;'>{score_pct}%</h1>", unsafe_allow_html=True)
else:
    st.info("No data found.")
