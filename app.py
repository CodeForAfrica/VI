import streamlit as st
from data_manager import StrategicManager

st.set_page_config(page_title="Narrative Monitor", layout="wide")
mgr = StrategicManager()

# --- UI HEADER ---
st.title("🛡️ Narrative Intelligence & Vulnerability Index")
st.markdown("---")

# --- GRID LAYOUT ---
if 'page' not in st.session_state: st.session_state.page = 0
df = mgr.fetch_articles(offset=st.session_state.page * 6)

cols = st.columns(3)
for i, row in df.iterrows():
    # Process visibility and scoring
    analysis = mgr.get_llm_analysis(row['raw_text'])
    score = mgr.get_contextual_score(analysis['actor'], analysis['country'], analysis['intent'])
    
    # Tone styling
    tone_map = {"Alarmist": "#ff4b4b", "Sensationalist": "#ffa500", "Factual": "#00c853", "Cynical": "#757575"}
    color = tone_map.get(analysis['tone'], "#1e88e5")

    with cols[i % 3]:
        with st.container(border=True):
            st.image(row['image_url'], use_column_width=True)
            st.markdown(f"**{row['title'][:70]}...**")
            
            # Badges
            st.markdown(f"<span style='background:{color}22; color:{color}; padding:2px 8px; border-radius:10px; font-size:12px;'>🎭 {analysis['tone']}</span>", unsafe_allow_html=True)
            
            # Clean Summary
            st.write(analysis['summary'])
            
            # Contextual Score UI
            st.divider()
            c1, c2 = st.columns(2)
            c1.metric("Influence Score", score)
            c2.caption(f"**Actor:** {analysis['actor']}\n\n**Intent:** {analysis['intent']}")
            st.link_button("View Article", row['url'], use_container_width=True)

# --- NAVIGATION ---
prev, mid, nxt = st.columns([1, 2, 1])
if prev.button("⬅️ Previous") and st.session_state.page > 0:
    st.session_state.page -= 1
    st.rerun()
if nxt.button("Next ➡️"):
    st.session_state.page += 1
    st.rerun()
