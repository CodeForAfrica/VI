import streamlit as st
import pandas as pd
from data_manager import DataManager
from datetime import datetime, timedelta
from fpdf import FPDF, XPos, YPos
import re

# --- Page Config ---
st.set_page_config(page_title="Strategic Vulnerability Index", page_icon="🛡️", layout="wide")

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
    # Important: Ensure the output is converted to bytes for Streamlit
    return bytes(pdf.output())

if "mgr" not in st.session_state:
    st.session_state.mgr = DataManager()
mgr = st.session_state.mgr

if "view_date" not in st.session_state:
    st.session_state.view_date = datetime.now().replace(day=1)

# --- Header & Navigation ---
st.title("🛡️ Geopolitical Vulnerability Index")

n1, n2, n3 = st.columns([1, 2, 1])

with n1:
    if st.button("⬅️ Fetch Previous Month"):
        st.session_state.view_date = (st.session_state.view_date - timedelta(days=1)).replace(day=1)
        target = st.session_state.view_date.strftime("%Y-%m-%d")
        with st.spinner("API: Fetching historical news..."):
            mgr.update_news(start_date=target)
            st.cache_data.clear()
            st.rerun()

with n2:
    st.markdown(f"<h2 style='text-align:center;'>📅 {st.session_state.view_date.strftime('%B %Y')}</h2>", unsafe_allow_html=True)

with n3:
    if st.button("Fetch Current Month ➡️"):
        st.session_state.view_date = datetime.now().replace(day=1)
        with st.spinner("API: Syncing latest news..."):
            mgr.update_news()
            st.cache_data.clear()
            st.rerun()

# --- Data Logic ---
raw_df = mgr.fetch_articles(limit=500)

if not raw_df.empty:
    raw_df['published_at'] = pd.to_datetime(raw_df['published_at'])
    df_view = raw_df[(raw_df['published_at'].dt.month == st.session_state.view_date.month) & 
                     (raw_df['published_at'].dt.year == st.session_state.view_date.year)]

    # Filter Bar
    st.markdown('<div style="background:#1c2128; padding:15px; border-radius:10px; border:1px solid #444; margin-bottom:20px;">', unsafe_allow_html=True)
    f1, f2, f3, f4 = st.columns([2, 2, 2, 1])
    with f1: f_actor = st.selectbox("Actor", ["All"] + mgr.actors)
    with f2: f_country = st.selectbox("Country", ["All"] + mgr.countries)
    with f3: f_intent = st.selectbox("Intent", ["All"] + list(mgr.INTENT_FACTORS.keys()))
    with f4:
        if not df_view.empty:
            pdf_data = generate_pdf(df_view, st.session_state.view_date.strftime('%B %Y'))
            st.download_button(label="📥 Download PDF", data=pdf_data, file_name=f"Intelligence_Report_{st.session_state.view_date.month}.pdf", mime="application/pdf")
    st.markdown('</div>', unsafe_allow_html=True)

    if df_view.empty:
        st.info(f"No local data for {st.session_state.view_date.strftime('%B')}. Click 'Fetch' above to check the API.")
    else:
        if f_actor != "All": df_view = df_view[df_view['actor'] == f_actor]
        if f_country != "All": df_view = df_view[df_view['country'] == f_country]
        
        st.write(f"Total Intelligence Reports: **{len(df_view)}**")
        for idx, row in df_view.iterrows():
            with st.container():
                c1, c2, c3 = st.columns([1, 2, 1])
                with c1: st.image(row['image_url'] if row['image_url'] else "https://via.placeholder.com/400")
                with c2:
                    st.subheader(row['title'])
                    st.caption(f"{row['media_outlet']} | {row['published_at'].strftime('%Y-%m-%d')}")
                    st.write(row['raw_text'][:500] + "...")
                    st.link_button("Source", row['url'])
                with c3:
                    st.metric("Vulnerability Score", f"{int(row['contextual_score']*100)}%")
else:
    st.warning("Database is empty. Please use the fetch buttons to pull intelligence from the NewsAPI.")
