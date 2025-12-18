import streamlit as st
import pandas as pd
from data_manager import DataManager
import plotly.graph_objects as go
import plotly.express as px
import json
from datetime import datetime

# --- Page Config ---
st.set_page_config(
    page_title="Strategic Vulnerability Index",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# --- Custom Styling (Professional Theme) ---
st.markdown("""
<style>
    /* Global Font & Background */
    html, body, [class*="css"] {
        font-family: 'Segoe UI', 'Helvetica Neue', sans-serif;
        background-color: #0e1117;
        color: #e0e0e0;
    }

    /* Header & Titles */
    h1, h2, h3 {
        font-weight: 700;
        color: #ffffff;
    }

    /* Command Center */
    .command-center {
        background: linear-gradient(135deg, #1a1f29 0%, #11151c 100%);
        border-radius: 16px;
        padding: 24px;
        box-shadow: 0 4px 20px rgba(0, 0, 0, 0.5);
        margin-bottom: 32px;
        border: 1px solid #2d333f;
    }

    /* Article Card */
    .stContainer > div {
        border: 1px solid #2d333f !important;
        border-radius: 12px !important;
        background: #141922 !important;
        padding: 16px !important;
        margin-bottom: 20px !important;
        transition: transform 0.2s ease, box-shadow 0.2s ease;
    }
    .stContainer > div:hover {
        transform: translateY(-2px);
        box-shadow: 0 6px 16px rgba(0, 0, 0, 0.4);
    }

    /* Button Styling */
    .stButton > button {
        border-radius: 8px;
        font-weight: 600;
        padding: 8px 16px;
        border: none;
        transition: all 0.3s ease;
    }
    .stButton > button:hover {
        transform: translateY(-1px);
        box-shadow: 0 4px 12px rgba(255, 75, 75, 0.3);
    }

    /* Metric Badge */
    .metric-badge {
        display: inline-block;
        padding: 4px 10px;
        border-radius: 6px;
        font-size: 0.85em;
        font-weight: bold;
        margin-right: 8px;
    }

    /* Footer & Captions */
    .caption {
        color: #8a94a6;
        font-size: 0.9em;
    }

    /* Radar Chart Container */
    .risk-panel {
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        height: 100%;
        text-align: center;
    }
</style>
""", unsafe_allow_html=True)

# --- Initialize Data Manager ---
if "mgr" not in st.session_state:
    st.session_state.mgr = DataManager()
mgr = st.session_state.mgr

# --- Metric Explanation Legend (as modal) ---
def show_metric_legend():
    with st.expander("ℹ️ Understanding the Vulnerability Metrics & Scores", expanded=False):
        st.markdown("""
        <div style="line-height: 1.6;">
            <h4>Strategic Metrics Breakdown</h4>
            <ul>
                <li><b>Vulnerability Score:</b> A composite index (0–100%) where <span style="color:#ff4b4b;">>70%</span> signals critical foreign influence exposure.</li>
                <li><b>Matrix Factors:</b> Derived from <i>Debt-to-GDP</i>, <i>Resource concessions</i>, and <i>Military access agreements</i>.</li>
                <li><b>Media Tone Classification:</b>
                    <ul style="margin-top:8px;">
                        <li><span style="color:#2ecc71">Factual</span>: Neutral, evidence-based reporting.</li>
                        <li><span style="color:#ffa500">Sensationalist</span>: Emotion-driven narratives.</li>
                        <li><span style="color:#ff4b4b">Alarmist</span>: Urgent, destabilization-focused framing.</li>
                        <li><span style="color:#9b59b6">Cynical</span>: Skeptical of foreign actor motives.</li>
                    </ul>
                </li>
            </ul>
        </div>
        """, unsafe_allow_html=True)

# --- Radar Chart (Enhanced) ---
def create_radar(score, tone):
    categories = ['Debt Depth', 'Resource Control', 'Military Presence', 'Sovereignty']
    mod = 1.15 if tone == "Alarmist" else 1.0
    r_values = [score * mod, score * 0.7, score * 0.5, score * 0.8]

    fig = go.Figure(go.Scatterpolar(
        r=r_values,
        theta=categories,
        fill='toself',
        fillcolor='rgba(255, 75, 75, 0.25)',
        line=dict(color='#ff4b4b', width=2.2),
        hoverinfo='skip'
    ))

    fig.update_layout(
        polar=dict(
            radialaxis=dict(visible=False, range=[0, 1.2]),
            angularaxis=dict(tickfont=dict(size=9), color="#6c757d")
        ),
        showlegend=False,
        height=220,
        margin=dict(l=10, r=10, t=10, b=10),
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)'
    )
    return fig

# --- Header ---
st.title("🛡️ Geopolitical Vulnerability Index")
show_metric_legend()

# --- Command Center (Stylish Filter Panel) ---
st.markdown('<div class="command-center">', unsafe_allow_html=True)
st.subheader("🔍 Strategic Command Center")

c1, c2, c3, c4 = st.columns(4)
with c1: f_country = st.selectbox("📍 Target Nation", ["All Nations"] + mgr.countries, key="country_filter")
with c2: f_actor = st.selectbox("👤 Foreign Actor", ["All Actors"] + mgr.actors, key="actor_filter")
with c3: f_intent = st.selectbox("🎯 Primary Intent", ["All Intents"] + list(mgr.INTENT_FACTORS.keys()), key="intent_filter")
with c4: f_tone = st.selectbox("🎭 Media Tone", ["All Tones", "Factual", "Alarmist", "Sensationalist", "Cynical"], key="tone_filter")

st.markdown("<hr style='margin:16px 0; border-top:1px solid #2d333f;'>", unsafe_allow_html=True)

sync_c1, sync_c2 = st.columns([1, 1])
with sync_c1:
    if st.button("🔄 Sync Global Intelligence", use_container_width=True, type="secondary"):
        mgr.update_news()
        st.cache_data.clear()
        st.rerun()
with sync_c2:
    if st.button("🗑️ Reset Database", use_container_width=True, type="primary"):
        mgr.clear_db()
        st.cache_data.clear()
        st.rerun()

st.markdown('</div>', unsafe_allow_html=True)

# --- Fetch & Process Data ---
df = mgr.fetch_articles(limit=500)

if not df.empty:
    df['published_at'] = pd.to_datetime(df['published_at'])

    def extract_extra(row):
        try:
            data = json.loads(row['raw_text'])
            return pd.Series([data.get('tone', 'Factual'), data.get('summary', '...')])
        except:
            return pd.Series(['Factual', row['raw_text']])
    
    df[['tone', 'summary']] = df.apply(extract_extra, axis=1)

    # Apply Filters
    filtered_df = df.copy()
    if f_country != "All Nations": filtered_df = filtered_df[filtered_df['country'] == f_country]
    if f_actor != "All Actors": filtered_df = filtered_df[filtered_df['actor'] == f_actor]
    if f_intent != "All Intents": filtered_df = filtered_df[filtered_df['intent_type'] == f_intent]
    if f_tone != "All Tones": filtered_df = filtered_df[filtered_df['tone'] == f_tone]

    # --- Trend Visualization ---
    if not filtered_df.empty:
        st.subheader("📈 Vulnerability Trend Analysis")
        trend_data = filtered_df.groupby(filtered_df['published_at'].dt.date)['contextual_score'].mean().reset_index()
        fig_trend = px.line(
            trend_data,
            x='published_at',
            y='contextual_score',
            template="plotly_dark",
            line_shape='spline'
        )
        fig_trend.update_traces(
            line_color='#ff6b6b',
            line_width=3,
            mode='lines+markers',
            marker=dict(size=5, color='#ff4b4b')
        )
        fig_trend.update_layout(
            height=260,
            margin=dict(l=20, r=20, t=20, b=20),
            xaxis_title=None,
            yaxis_title="Avg. Vulnerability Score",
            hovermode="x unified"
        )
        st.plotly_chart(fig_trend, use_container_width=True, config={'displayModeBar': False})

    st.markdown("<hr style='margin:24px 0; border-top:1px solid #222833;'>", unsafe_allow_html=True)

    # --- Pagination ---
    items_per_page = 6
    if "page" not in st.session_state: st.session_state.page = 1
    total_pages = max(1, (len(filtered_df) + items_per_page - 1) // items_per_page)

    nav1, nav2, nav3 = st.columns([1, 4, 1])
    with nav1:
        if st.button("← Previous", disabled=(st.session_state.page == 1)):
            st.session_state.page -= 1
            st.rerun()
    with nav2:
        st.markdown(f"<div style='text-align:center; font-size:1.05em; font-weight:600; color:#cccccc;'>Page {st.session_state.page} of {total_pages} • {len(filtered_df)} Reports</div>", unsafe_allow_html=True)
    with nav3:
        if st.button("Next →", disabled=(st.session_state.page == total_pages)):
            st.session_state.page += 1
            st.rerun()

    # --- Article Feed (Professional Cards) ---
    start_idx = (st.session_state.page - 1) * items_per_page
    page_df = filtered_df.iloc[start_idx : start_idx + items_per_page]

    for idx, row in page_df.iterrows():
        with st.container():
            col_img, col_body, col_risk = st.columns([1, 3, 1.5])

            with col_img:
                img_url = row['image_url'] or "https://placehold.co/400x250/1e232c/4a5568?text=No+Image"
                st.image(img_url, use_column_width=True)

            with col_body:
                st.markdown(f"### {row['title']}")
                tone_color_map = {
                    "Factual": "#2ecc71", "Sensationalist": "#ffa500",
                    "Alarmist": "#ff4b4b", "Cynical": "#9b59b6"
                }
                tone_color = tone_color_map.get(row['tone'], "#ffffff")

                # Tags
                st.markdown(
                    f"""
                    <span class="metric-badge" style="background:#1f2937; color:#94a3b8;">📍 {row['country']}</span>
                    <span class="metric-badge" style="background:#1f2937; color:#94a3b8;">👤 {row['actor']}</span>
                    <span class="metric-badge" style="background:#1f2937; color:#94a3b8;">🎯 {row['intent_type']}</span>
                    <span class="metric-badge" style="background:{tone_color}20; color:{tone_color}; border:1px solid {tone_color}40;">{row['tone'].upper()}</span>
                    """,
                    unsafe_allow_html=True
                )

                st.write(f"**Insight**: {row['summary']}")
                st.markdown(f'<p class="caption">{row["media_outlet"]} • {row["published_at"].strftime("%Y-%m-%d")}</p>', unsafe_allow_html=True)
                st.link_button("🌐 View Source", row['url'], use_container_width=True)

            with col_risk:
                st.markdown('<div class="risk-panel">', unsafe_allow_html=True)
                st.plotly_chart(
                    create_radar(row['contextual_score'], row['tone']),
                    use_container_width=True,
                    config={'staticPlot': True}
                )
                score_pct = min(100, int(row['contextual_score'] * 100))
                risk_color = "#ff4b4b" if score_pct > 70 else "#ff9e4b" if score_pct > 50 else "#2ecc71"
                st.markdown(f"<h2 style='color:{risk_color}; margin:8px 0;'>{score_pct}%</h2>", unsafe_allow_html=True)
                st.markdown("<small style='color:#8a94a6;'>VULNERABILITY<br>INDEX</small>", unsafe_allow_html=True)
                st.markdown('</div>', unsafe_allow_html=True)

    # --- Raw Data Inspector (Collapsible) ---
    with st.expander("🗄️ Raw Intelligence Feed"):
        st.dataframe(
            df[['published_at', 'country', 'actor', 'title', 'tone', 'contextual_score']],
            use_container_width=True,
            height=400
        )

else:
    st.info("📭 Intelligence database is empty. Press **Sync Global Intelligence** to populate.")
