import streamlit as st
import pandas as pd
from data_manager import DataManager
import plotly.graph_objects as go
from datetime import datetime
from fpdf import FPDF  # Added for PDF generation
from io import BytesIO

# --- Page Config ---
st.set_page_config(page_title="Strategic Vulnerability Index", page_icon="🛡️", layout="wide")

# --- PDF Generation Function (New Functionality) ---
def generate_pdf(df, month_str, actor_f, country_f):
    pdf = FPDF()
    pdf.add_page()
    
    # Title
    pdf.set_font("Arial", 'B', 16)
    pdf.cell(0, 10, f"Geopolitical Vulnerability Report: {month_str}", ln=True, align='C')
    
    # Filter Metadata
    pdf.set_font("Arial", '', 10)
    pdf.cell(0, 10, f"Filters: Actor: {actor_f} | Country: {country_f} | Generated: {datetime.now().strftime('%Y-%m-%d')}", ln=True, align='C')
    pdf.ln(10)
    
    # Executive Summary Calculation
    if not df.empty:
        avg_risk = df['contextual_score'].mean() * 100
        top_intent = df['intent_type'].mode()[0] if not df.empty else "N/A"
        
        pdf.set_font("Arial", 'B', 12)
        pdf.cell(0, 10, "1. Executive Risk Summary", ln=True)
        pdf.set_font("Arial", '', 11)
        summary = (f"During this period, the analyzed region showed an average vulnerability index of {avg_risk:.1f}%. "
                   f"The primary strategic intent identified across signals was '{top_intent}'.")
        pdf.multi_cell(0, 10, summary)
        pdf.ln(5)

    # Intelligence Feed
    pdf.set_font("Arial", 'B', 12)
    pdf.cell(0, 10, "2. Intelligence Signal Log", ln=True)
    pdf.set_font("Arial", '', 9)
    
    for i, row in df.iterrows():
        pdf.set_font("Arial", 'B', 10)
        pdf.multi_cell(0, 8, f"{i+1}. {row['title']} (Risk: {int(row['contextual_score']*100)}%)")
        pdf.set_font("Arial", 'I', 8)
        pdf.cell(0, 5, f"Source: {row['media_outlet']} | Actor: {row['actor']} | Intent: {row['intent_type']}", ln=True)
        pdf.set_font("Arial", '', 9)
        # Clean text for PDF encoding
        clean_text = row['raw_text'][:400].encode('latin-1', 'ignore').decode('latin-1')
        pdf.multi_cell(0, 5, f"{clean_text}...")
        pdf.ln(5)
        
        if pdf.get_y() > 260:  # Page break handling
            pdf.add_page()
            
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

# --- Filters Section (Updated with Export Button) ---
st.markdown('<div class="filter-container">', unsafe_allow_html=True)
f1, f2, f3, f4 = st.columns([2, 2, 2, 1])
with f1: f_actor = st.selectbox("Foreign Actor", ["All Actors"] + mgr.actors)
with f2: f_country = st.selectbox("Target Nation", ["All Countries"] + mgr.countries)
with f3: f_intent = st.selectbox("Primary Intent", ["All Intents"] + list(mgr.INTENT_FACTORS.keys()))

# --- Monthly Pagination Logic ---
raw_df = mgr.fetch_articles(limit=100)
if not raw_df.empty:
    raw_df['published_at'] = pd.to_datetime(raw_df['published_at'])
    raw_df['month_year'] = raw_df['published_at'].dt.to_period('M')
    
    available_months = sorted(raw_df['month_year'].unique(), reverse=True)
    
    if "m_idx" not in st.session_state: st.session_state.m_idx = 0
    
    # Filter Data for current view
    current_m = available_months[st.session_state.m_idx]
    df_view = raw_df[raw_df['month_year'] == current_m]
    if f_actor != "All Actors": df_view = df_view[df_view['actor'] == f_actor]
    if f_country != "All Countries": df_view = df_view[df_view['country'] == f_country]
    if f_intent != "All Intents": df_view = df_view[df_view['intent_type'] == f_intent]

    # --- PDF Button in Filter Bar ---
    with f4:
        st.write("") # Spacer
        if not df_view.empty:
            pdf_out = generate_pdf(df_view, current_m.strftime('%B %Y'), f_actor, f_country)
            st.download_button(
                label="📥 Export PDF",
                data=pdf_out,
                file_name=f"Vulnerability_Report_{current_m}.pdf",
                mime="application/pdf"
            )
    st.markdown('</div>', unsafe_allow_html=True)

    # Nav Buttons
    nb1, nb2, nb3 = st.columns([1, 4, 1])
    with nb1: 
        if st.button("⬅️ Previous Month") and st.session_state.m_idx < len(available_months) - 1:
            st.session_state.m_idx += 1
    with nb3:
        if st.button("Next Month ➡️") and st.session_state.m_idx > 0:
            st.session_state.m_idx -= 1
            
    st.subheader(f"📅 Intelligence for {current_m.strftime('%B %Y')}")

    # --- Feed ---
    for idx, row in df_view.iterrows():
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
                        st.info(f"**Insight:** This move primarily leverages financial instruments. High dependency on **Debt** and **Resources** detected.")
                    elif row['intent_type'] == "MilitaryPresence":
                        st.warning(f"**Insight:** Physical security footprint detected. This increases long-term **Military** and **Fragility** risks.")
                    else:
                        st.success(f"**Insight:** General soft-power engagement. Monitoring for shifts in **Social Fragility**.")

                st.link_button("View Intelligence Source", row['url'])

            with col3:
                st.markdown("<p style='text-align:center;' class='metric-text'>Risk Distribution</p>", unsafe_allow_html=True)
                st.plotly_chart(create_radar_chart(row['contextual_score'], row['intent_type']), use_container_width=True, key=f"r_{idx}", config={'displayModeBar': False})
                
                # Score display
                score_pct = int(row['contextual_score'] * 100)
                color = "#ff4b4b" if score_pct > 70 else "#ffa500" if score_pct > 40 else "#00ff00"
                st.markdown(f"<h1 style='text-align:center; color:{color}; margin-bottom:0;'>{score_pct}%</h1>", unsafe_allow_html=True)
                st.markdown("<p style='text-align:center;' class='metric-text'>Vulnerability Index</p>", unsafe_allow_html=True)
else:
    st.markdown('</div>', unsafe_allow_html=True)
    st.info("No data found for the current selection.")

st.markdown("---")
st.caption("Strategic Intelligence Framework | Developed for Geopolitical Risk Assessment")
