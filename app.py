import streamlit as st
from data_manager import DataManager
import math

# --- Page Configuration ---
st.set_page_config(
    page_title="Vulnerability Index Tool",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- Initialize Data Manager ---
if "mgr" not in st.session_state:
    st.session_state.mgr = DataManager()
if "page" not in st.session_state:
    st.session_state.page = 0

mgr = st.session_state.mgr

# --- Custom Styling for Cards ---
st.markdown("""
    <style>
    .stMetric { background-color: #f8f9fb; padding: 15px; border-radius: 10px; border: 1px solid #e0e6ed; }
    [data-testid="stMetricValue"] { font-size: 28px; color: #1f2937; }
    </style>
    """, unsafe_allow_html=True)

# --- Sidebar: Intelligence Controls ---
with st.sidebar:
    st.title("🛡️ Risk Intelligence")
    st.caption("v2.5 Geopolitical Monitor")
    
    st.divider()
    
    st.header("Search Filters")
    country_filter = st.selectbox(
        "Target Country", 
        ["All", "Senegal", "DRC", "Ivory Coast", "Ethiopia"]
    )
    actor_filter = st.selectbox(
        "Foreign Actor", 
        ["All", "China", "Russia", "France", "UAE", "US"]
    )

    st.divider()
    
    # Update Button
    if st.button("🔄 Refresh Data Stream", use_container_width=True, type="primary"):
        with st.status("Scanning global media...", expanded=True) as status:
            new_count = mgr.update_news()
            status.update(label=f"Analysis Complete! {new_count} insights added.", state="complete", expanded=False)
        st.rerun()

    st.divider()
    st.info("""
    **Vulnerability Formula:**
    $$FinalRisk = avg\_base + (1.0 - avg\_base) \times CA$$
    *CA: Contextual Assessment*
    """)

# --- Main Dashboard Area ---
st.title("Africa Geopolitical Vulnerability Index")
st.markdown("Real-time monitoring of foreign strategic influence and media sentiment.")

# 1. KPI Row
m1, m2, m3, m4 = st.columns(4)
m1.metric("Tracked Countries", "4", "Regional")
m2.metric("Key Actors", "5", "Global")
m3.metric("Critical Alerts", "14", "High", delta_color="inverse")
m4.metric("AI Confidence", "92%", "Llama-3.3")

st.divider()

# 2. Fetch Data with Pagination
ARTICLES_PER_PAGE = 6
df = mgr.fetch_articles(offset=st.session_state.page * ARTICLES_PER_PAGE, limit=ARTICLES_PER_PAGE)

# 3. Filtering Logic (Client-Side)
if not df.empty:
    if country_filter != "All":
        df = df[df['title'].str.contains(country_filter, case=False, na=False)]
    
    if actor_filter != "All":
        # Handle US/USA variations
        if actor_filter == "US":
            df = df[df['media_name'].str.contains("US|USA|United States", case=False, na=False)]
        else:
            df = df[df['media_name'].str.contains(actor_filter, case=False, na=False)]

# 4. News Grid Display
if df is None or df.empty:
    st.warning("📡 No intelligence gathered yet. Use the sidebar to refresh the data stream.")
    st.image("https://via.placeholder.com/1000x300?text=Awaiting+Data+Input", use_container_width=True)
else:
    st.subheader(f"Latest Intelligence Highlights (Page {st.session_state.page + 1})")
    
    # Build the 3-column grid
    for i in range(0, len(df), 3):
        cols = st.columns(3)
        for j in range(3):
            if i + j < len(df):
                article = df.iloc[i + j]
                with cols[j]:
                    with st.container(border=True):
                        # Article Image
                        img_url = article['image_url'] if article['image_url'] else "https://via.placeholder.com/400x225?text=Strategic+Update"
                        st.image(img_url, use_container_width=True)
                        
                        # Headline & Meta
                        st.markdown(f"**{article['title'][:85]}...**")
                        st.caption(f"📢 {article['media_outlet']} | 📅 {str(article['published_at'])[:10]}")
                        
                        # AI Analysis Parsing
                        try:
                            # Format expected: "Actor: X | Score: Y | Tone: Z"
                            parts = article['raw_text'].split(" | ")
                            score = float(parts[1].split(": ")[1])
                            tone = parts[2].split(": ")[1]
                            
                            # Visual cues
                            tone_color = "red" if tone in ["Aggressive", "Critical"] else "blue"
                            st.markdown(f"**Tone:** :{tone_color}[{tone}]")
                            st.progress(score, text=f"Influence Intensity: {int(score*100)}%")
                        except:
                            st.write(article['raw_text'])
                        
                        st.link_button("View Source", article['url'], use_container_width=True)

# 5. Pagination
st.divider()
p1, p2, p3 = st.columns([1, 2, 1])
with p1:
    if st.session_state.page > 0:
        if st.button("⬅️ Previous", use_container_width=True):
            st.session_state.page -= 1
            st.rerun()
with p3:
    if not df.empty and len(df) == ARTICLES_PER_PAGE:
        if st.button("Next ➡️", use_container_width=True):
            st.session_state.page += 1
            st.rerun()

st.sidebar.caption("© 2025 Geopolitical Monitoring Suite")
