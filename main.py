import streamlit as st
from data_loader import NarrativeIntelligence

st.set_page_config(page_title="Narrative Monitor", layout="wide")
ni = NarrativeIntelligence()

# --- STYLING ---
st.markdown("""
<style>
    .article-card {
        background: #1c2128; border: 1px solid #30363d; border-radius: 12px;
        padding: 0px; height: 580px; margin-bottom: 25px; display: flex; flex-direction: column;
    }
    .status-badge {
        padding: 4px 10px; border-radius: 20px; font-size: 0.75rem; font-weight: bold;
    }
</style>
""", unsafe_allow_html=True)

# --- SIDEBAR & FILTERS ---
with st.sidebar:
    st.image("https://raw.githubusercontent.com/hanna-tes/CfA-media-narrtives-monitoring/main/CFA_Logo.png", width=150)
    st.title("Settings")
    if st.button("🔄 Sync Real-time Data"):
        with st.spinner("Fetching news..."):
            ni.fetch_live_news()
    
    st.divider()
    st.info("💡 **Vulnerability Index:** Calculated by mapping article intent against national debt, military presence, and FSI scores.")

# --- MAIN DASHBOARD ---
st.title("🛡️ Narrative Monitoring & Vulnerability Index")

if 'page' not in st.session_state: st.session_state.page = 0
articles = ni.get_display_data(offset=st.session_state.page * 6)

cols = st.columns(3)
for i, row in articles.iterrows():
    # Enriching only the 6 displayed articles to save API costs
    analysis = ni.enrich_with_llm(row['raw_text'])
    score = ni.get_influence_score(analysis['actor'], analysis['target_country'], analysis['intent'])
    
    tone_colors = {"Alarmist": "#f85149", "Sensationalist": "#d29922", "Cynical": "#8b949e", "Factual": "#238636"}
    accent = tone_colors.get(analysis['tone'], "#58a6ff")

    with cols[i % 3]:
        st.markdown(f"""
        <div class="article-card" style="border-top: 5px solid {accent}">
            <img src="{row['image_url']}" style="width:100%; height:180px; object-fit:cover; border-radius:12px 12px 0 0;">
            <div style="padding: 20px;">
                <h4 style="margin:0; color:white;">{row['title'][:65]}...</h4>
                <p style="color:#8b949e; font-size:0.85rem; margin-top:10px;">{analysis['summary']}</p>
                <div style="margin-top:10px;">
                    <span class="status-badge" style="background:{accent}33; color:{accent};">🎭 {analysis['tone']}</span>
                    <span class="status-badge" style="background:#21262d; color:#c9d1d9;">👤 {analysis['actor']}</span>
                </div>
                <div style="margin-top:20px; background:#0d1117; padding:15px; border-radius:10px; text-align:center;">
                    <p style="margin:0; font-size:0.65rem; color:#8b949e; text-transform:uppercase;">Contextual Influence Score</p>
                    <h2 style="margin:0; color:{accent};">{score}</h2>
                    <p style="margin:0; font-size:0.7rem; color:#58a6ff;">Target: {analysis['target_country']} | {analysis['intent']}</p>
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)

# --- PAGINATION ---
c1, c2, c3 = st.columns([1,1,1])
if c1.button("⬅️ Previous") and st.session_state.page > 0:
    st.session_state.page -= 1
    st.rerun()
if c3.button("Next ➡️"):
    st.session_state.page += 1
    st.rerun()
