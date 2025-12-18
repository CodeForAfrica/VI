import streamlit as st
import pandas as pd
from data_manager import DataManager
import plotly.graph_objects as go
from datetime import datetime, timedelta
from fpdf import FPDF, XPos, YPos
import re

# --- Page Config ---
st.set_page_config(page_title="Strategic Vulnerability Index", page_icon="🛡️", layout="wide")

# --- Robust PDF Cleaning ---
def safe_text(text):
    if not text: return ""
    text = str(text)
    # Strip non-ASCII to prevent PDF encoding crashes
    text = re.sub(r'[‘’]', "'", text); text = re.sub(r'[“”]', '"', text); text = re.sub(r'[—–]', "-", text)
    return text.encode('ascii', 'ignore').decode('ascii')

def generate_pdf(df, month_str):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("helvetica", 'B', 16)
    pdf.cell(0, 10, f"Strategic Brief: {month_str}", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='C')
    pdf.ln(10)
    pdf.set_font("helvetica", '', 10)
    for _, row in df.iterrows():
        pdf.set_font("helvetica", 'B', 11)
        pdf.multi_cell(0, 7, safe_text(row['title']))
        pdf.set_font("helvetica", 'I', 8)
        pdf.cell(0, 5, safe_text(f"Source: {row['media_outlet']} | Risk: {int(row['contextual_score']*100)}%"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(5)
        if pdf.get_y() > 260: pdf.add_page()
    return pdf.output()

# --- Initialize Manager ---
if "mgr" not in st.session_state:
    st.session_state.mgr = DataManager()
mgr = st.session_state.mgr

# --- Monthly Navigation Logic ---
if "current_date" not in st.session_state:
    st.session_state.current_date = datetime.now().replace(day=1)

def fetch_monthly_intelligence():
    start_date = st.session_state.current_date.strftime("%Y-%m-%d")
    with st.spinner(f"📡 Requesting API data for {st.session_state.current_date.strftime('%B %Y')}..."):
        mgr.update_news(start_date=start_date)
        st.cache_data.clear()

# --- Layout ---
st.title("🛡️ Africa Geopolitical Vulnerability Index")

# Navigation Bar
n1, n2, n3 = st.columns([1, 2, 1])
with n1:
    # 30-Day Check for Free Tier
    earliest_allowed = (datetime.now() - timedelta(days=28)).replace(day=1)
    target_prev = (st.session_state.current_date - timedelta(days=1)).replace(day=1)
    
    if target_prev >= earliest_allowed:
        if st.button("⬅️ Previous Month"):
            st.session_state.current_date = target_prev
            fetch_monthly_intelligence()
            st.rerun()
    else:
        st.button("⬅️ Plan Limit", help="Free tier only allows last 30 days", disabled=True)

with n2:
    st.markdown(f"<h2 style='text-align:center;'>📅 {st.session_state.current_date.strftime('%B %Y')}</h2>", unsafe_allow_html=True)

with n3:
    if st.button("Next Month ➡️"):
        st.session_state.current_date = (st.session_state.current_date + timedelta(days=32)).replace(day=1)
        fetch_monthly_intelligence()
        st.rerun()

# Data Display
raw_df = mgr.fetch_articles(limit=1000)
if not raw_df.empty:
    raw_df['published_at'] = pd.to_datetime(raw_df['published_at'])
    df_view = raw_df[(raw_df['published_at'].dt.month == st.session_state.current_date.month) & 
                     (raw_df['published_at'].dt.year == st.session_state.current_date.year)]

    # Filter Bar
    st.markdown('<div style="background:#1c2128; padding:15px; border-radius:10px; border:1px solid #444; margin-bottom:20px;">', unsafe_allow_html=True)
    c1, c2, c3, c4 = st.columns([2, 2, 2, 1])
    with c1: f_actor = st.selectbox("Actor", ["All"] + mgr.actors)
    with c2: f_country = st.selectbox("Country", ["All"] + mgr.countries)
    with c3: f_intent = st.selectbox("Intent", ["All"] + list(mgr.INTENT_FACTORS.keys()))
    
    if f_actor != "All": df_view = df_view[df_view['actor'] == f_actor]
    if f_country != "All": df_view = df_view[df_view['country'] == f_country]

    with c4:
        st.write("")
        if not df_view.empty:
            try:
                pdf_bytes = generate_pdf(df_view, st.session_state.current_date.strftime('%B %Y'))
                st.download_button("📥 PDF", data=pdf_bytes, file_name=f"Report_{st.session_state.current_date.month}.pdf")
            except: st.error("PDF Error")
    st.markdown('</div>', unsafe_allow_html=True)

    for idx, row in df_view.iterrows():
        with st.container():
            col1, col2, col3 = st.columns([1, 2, 1])
            with col1:
                st.image(row['image_url'] if row['image_url'] else "https://via.placeholder.com/400", use_container_width=True)
            with col2:
                st.markdown(f"### {row['title']}")
                st.caption(f"**{row['media_outlet']}** | {row['published_at'].strftime('%Y-%m-%d')}")
                st.write(row['raw_text'][:400] + "...")
                st.link_button("Read Source", row['url'])
            with col3:
                score = int(row['contextual_score'] * 100)
                st.markdown(f"<h1 style='text-align:center;'>{score}%</h1>", unsafe_allow_html=True)
                st.markdown("<p style='text-align:center;'>VULNERABILITY</p>", unsafe_allow_html=True)
else:
    st.info("No data in DB. Try clicking 'Next/Previous' to fetch from API.")
