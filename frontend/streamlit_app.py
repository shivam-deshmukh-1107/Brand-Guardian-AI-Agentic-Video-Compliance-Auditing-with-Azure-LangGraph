# Brand Guardian AI — Streamlit Dashboard (Single-Page, No Scroll)
# Schema: POST /audit → { session_id, video_id, status, final_report,
#                          compliance_results: [{category, severity, description}] }

import html
import json
import threading
import time
from datetime import datetime

import requests
import streamlit as st


# ──────────────────────────────────────────────
#  Constants
# ──────────────────────────────────────────────

API_BASE_URL = "http://localhost:8000"

SEVERITY_CONFIG = {
    "CRITICAL": {"color": "#f87171", "bg": "#3b0a0a", "border": "#dc2626", "icon": "🔴", "order": 0},
    "HIGH":     {"color": "#fb923c", "bg": "#3b1a06", "border": "#ea580c", "icon": "🟠", "order": 1},
    "MEDIUM":   {"color": "#facc15", "bg": "#3b2a06", "border": "#ca8a04", "icon": "🟡", "order": 2},
    "LOW":      {"color": "#86efac", "bg": "#052e16", "border": "#16a34a", "icon": "🟢", "order": 3},
    "WARNING":  {"color": "#facc15", "bg": "#3b2a06", "border": "#ca8a04", "icon": "⚠️", "order": 2},
}

PIPELINE_STAGES = [
    (12, "⬇️ Downloading via yt-dlp..."),
    (28, "☁️ Uploading to Azure Blob Storage..."),
    (48, "🔍 Azure Video Indexer: speech-to-text + OCR..."),
    (63, "⏳ Polling Video Indexer..."),
    (76, "📚 RAG retrieval from compliance knowledge base..."),
    (89, "🤖 LangGraph + Azure OpenAI auditing transcript..."),
    (96, "📋 Building compliance report..."),
]

# Shared inline styles — avoids CSS class dependency issues
PANEL_STYLE = (
    "max-height:calc(100vh - 235px);"
    "overflow-y:auto;"
    "background:#0d1117;"
    "border:1px solid #21262d;"
    "border-radius:10px;"
    "padding:0.75rem 0.9rem;"
)


# ──────────────────────────────────────────────
#  Page Config
# ──────────────────────────────────────────────

st.set_page_config(
    page_title="Brand Guardian AI",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ──────────────────────────────────────────────
#  Session State
# ──────────────────────────────────────────────

if "audit_history" not in st.session_state:
    st.session_state.audit_history = []
if "result" not in st.session_state:
    st.session_state.result = None


# ──────────────────────────────────────────────
#  Global CSS  
# ──────────────────────────────────────────────

st.markdown("""
<style>
    .block-container {
        padding: 0.8rem 1.2rem 0.5rem !important;
        max-width: 100% !important;
    }
    [data-testid="stSidebar"] {
        background: #0d1117;
        min-width: 230px !important;
        max-width: 230px !important;
    }
    [data-testid="stSidebar"] .block-container {
        padding: 1rem 0.8rem !important;
    }
    #MainMenu, footer, header { visibility: hidden; }
    [data-testid="stToolbar"]  { display: none; }

    /* Input box */
    .stTextInput input {
        background: #161b22 !important;
        border: 1px solid #30363d !important;
        border-radius: 8px !important;
        color: #e6edf3 !important;
        font-size: 0.9rem !important;
        padding: 0.5rem 0.8rem !important;
    }
    .stTextInput input:focus {
        border-color: #3b82f6 !important;
        box-shadow: 0 0 0 2px rgba(59,130,246,0.2) !important;
    }

    /* Submit button */
    div[data-testid="stFormSubmitButton"] button {
        background: linear-gradient(135deg, #1d4ed8, #2563eb) !important;
        color: white !important;
        border: none !important;
        border-radius: 8px !important;
        font-weight: 600 !important;
        font-size: 0.88rem !important;
        width: 100% !important;
    }
    div[data-testid="stFormSubmitButton"] button:hover {
        background: linear-gradient(135deg, #2563eb, #3b82f6) !important;
    }

    /* Streamlit tab styling */
    [data-testid="stTabs"] [data-baseweb="tab"] {
        font-size: 0.82rem !important;
        padding: 0.3rem 0.7rem !important;
    }
    [data-testid="stTabs"] [data-baseweb="tab-panel"] {
        padding: 0.5rem 0 0 !important;
    }
</style>
""", unsafe_allow_html=True)


# ──────────────────────────────────────────────
#  HTML Builder Functions
#  All functions return COMPLETE html strings.
#  No string is ever split across st.markdown() calls.
# ──────────────────────────────────────────────

def _pill(status: str) -> str:
    s    = status.upper()
    icon = {"PASS": "✅", "FAIL": "❌"}.get(s, "❓")
    styles = {
        "PASS":    "background:#052e16;color:#4ade80;border:1px solid #16a34a;",
        "FAIL":    "background:#450a0a;color:#f87171;border:1px solid #dc2626;",
        "UNKNOWN": "background:#1e293b;color:#94a3b8;border:1px solid #475569;",
    }
    st_str = styles.get(s, styles["UNKNOWN"])
    return (
        f'<span style="display:inline-block;padding:0.2rem 0.9rem;border-radius:999px;'
        f'font-weight:700;font-size:0.82rem;letter-spacing:0.05em;{st_str}">'
        f'{icon} {s}</span>'
    )


def _sev_badge(sev: str) -> str:
    cfg = SEVERITY_CONFIG.get(sev, {"color":"#94a3b8","bg":"#1e293b","border":"#475569","icon":"•"})
    return (
        f'<span style="background:{cfg["bg"]};color:{cfg["color"]};'
        f'border:1px solid {cfg["border"]};border-radius:10px;'
        f'padding:1px 8px;font-size:0.68rem;font-weight:700;white-space:nowrap;">'
        f'{cfg["icon"]} {sev}</span>'
    )


def build_violations_panel(violations_sorted: list) -> str:
    """
    Returns one complete HTML string for the entire violations scroll panel.
    All LLM text is passed through html.escape() before insertion.
    """
    if not violations_sorted:
        inner = (
            '<div style="display:flex;flex-direction:column;align-items:center;'
            'justify-content:center;height:80%;color:#3d444d;text-align:center;padding:2rem;">'
            '<div style="font-size:2rem;margin-bottom:0.6rem;">✅</div>'
            '<div style="font-size:0.85rem;line-height:1.6;">No violations detected.<br>'
            'Video passed all compliance checks.</div>'
            '</div>'
        )
    else:
        cards = []
        for issue in violations_sorted:
            sev  = issue.get("severity", "MEDIUM").upper()
            cfg  = SEVERITY_CONFIG.get(sev, SEVERITY_CONFIG["MEDIUM"])
            # html.escape every piece of LLM-generated text
            cat  = html.escape(str(issue.get("category",    "Unknown Category")))
            desc = html.escape(str(issue.get("description", "No description.")))
            ts   = issue.get("timestamp")
            ts_html = (
                f'<span style="color:#6e7681;font-size:0.7rem;margin-left:auto;">'
                f'⏱ {html.escape(str(ts))}</span>'
            ) if ts else ""

            cards.append(
                f'<div style="background:{cfg["bg"]};border-left:3px solid {cfg["border"]};'
                f'border-radius:8px;padding:0.65rem 0.85rem;margin-bottom:0.5rem;'
                f'word-break:break-word;overflow-wrap:break-word;">'

                # top row: badge + category + optional timestamp
                f'<div style="display:flex;align-items:center;gap:0.5rem;'
                f'flex-wrap:wrap;margin-bottom:0.35rem;">'
                f'{_sev_badge(sev)}'
                f'<span style="color:#e6edf3;font-weight:600;font-size:0.88rem;">{cat}</span>'
                f'{ts_html}'
                f'</div>'

                # description — pre-wrap preserves LLM line breaks; escape prevents tag injection
                f'<div style="color:#8b949e;font-size:0.82rem;line-height:1.55;'
                f'white-space:pre-wrap;word-break:break-word;overflow-wrap:break-word;">'
                f'{desc}'
                f'</div>'

                f'</div>'
            )
        inner = "\n".join(cards)

    return f'<div style="{PANEL_STYLE}">{inner}</div>'


def build_summary_panel(final_report: str) -> str:
    """
    Returns one complete HTML string for the summary scroll panel.
    final_report is escaped so LLM markdown symbols render as text safely.
    """
    label = (
        '<div style="color:#6e7681;font-size:0.7rem;text-transform:uppercase;'
        'letter-spacing:0.1em;margin-bottom:0.6rem;font-weight:600;">'
        'AI Compliance Summary</div>'
    )
    body = (
        f'<div style="color:#c9d1d9;font-size:0.87rem;line-height:1.8;'
        f'word-break:break-word;overflow-wrap:break-word;white-space:pre-wrap;">'
        f'{html.escape(final_report)}'
        f'</div>'
    )
    return f'<div style="{PANEL_STYLE}">{label}{body}</div>'


def build_metric_strip(video_id, session_id, n_violations, elapsed_str, audit_status) -> str:
    items = [
        ("Video ID",   html.escape(str(video_id))),
        ("Session",    html.escape(str(session_id)[:13]) + "…"),
        ("Violations", str(n_violations)),
        ("Duration",   html.escape(str(elapsed_str))),
        ("Status",     _pill(audit_status)),
    ]
    cells = "".join(
        f'<div style="background:#161b22;border:1px solid #21262d;border-radius:8px;'
        f'padding:0.45rem 0.7rem;text-align:center;flex:1;">'
        f'<div style="color:#6e7681;font-size:0.65rem;text-transform:uppercase;'
        f'letter-spacing:0.1em;">{lbl}</div>'
        f'<div style="color:#e6edf3;font-size:0.92rem;font-weight:600;'
        f'word-break:break-all;margin-top:0.1rem;">{val}</div>'
        f'</div>'
        for lbl, val in items
    )
    return (
        f'<div style="display:flex;gap:0.5rem;margin-bottom:0.6rem;">'
        f'{cells}'
        f'</div>'
    )


# ──────────────────────────────────────────────
#  API Helpers
# ──────────────────────────────────────────────

def check_health() -> tuple[bool, str]:
    try:
        r = requests.get(f"{API_BASE_URL}/health", timeout=4)
        return (True, r.json().get("service", "Online")) if r.status_code == 200 else (False, f"HTTP {r.status_code}")
    except requests.exceptions.ConnectionError:
        return False, "Connection refused"
    except Exception as e:
        return False, str(e)


def run_audit_api(video_url: str, holder: dict):
    try:
        r = requests.post(f"{API_BASE_URL}/audit", json={"video_url": video_url}, timeout=420)
        r.raise_for_status()
        holder["data"]   = r.json()
        holder["status"] = "ok"
    except requests.exceptions.Timeout:
        holder["status"] = "timeout"
    except requests.exceptions.ConnectionError:
        holder["status"] = "connection_error"
    except requests.exceptions.HTTPError as e:
        holder["status"] = "http_error"
        holder["detail"] = f"HTTP {e.response.status_code}: {e.response.text[:300]}"
    except Exception as e:
        holder["status"] = "error"
        holder["detail"] = str(e)


# ──────────────────────────────────────────────
#  Sidebar
# ──────────────────────────────────────────────

api_ok, api_msg = check_health()

with st.sidebar:
    st.markdown(
        '<p style="color:#e6edf3;font-size:1rem;font-weight:700;margin:0 0 0.1rem;">🛡️ Brand Guardian AI</p>'
        '<p style="color:#6e7681;font-size:0.72rem;margin:0 0 0.8rem;">Video Compliance Auditing</p>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<hr style="border:none;border-top:1px solid #21262d;margin:0.5rem 0;">',
        unsafe_allow_html=True,
    )

    # API status
    st.markdown(
        '<div style="color:#6e7681;font-size:0.7rem;text-transform:uppercase;'
        'letter-spacing:0.1em;margin-bottom:0.4rem;font-weight:600;">API Server</div>',
        unsafe_allow_html=True,
    )
    if api_ok:
        st.markdown(
            f'<div style="background:#052e16;border:1px solid #16a34a;border-radius:7px;'
            f'padding:0.4rem 0.6rem;color:#4ade80;font-size:0.78rem;">'
            f'✅ Connected — {html.escape(api_msg)}</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f'<div style="background:#450a0a;border:1px solid #dc2626;border-radius:7px;'
            f'padding:0.4rem 0.6rem;color:#f87171;font-size:0.78rem;">'
            f'❌ Offline — {html.escape(api_msg)}</div>',
            unsafe_allow_html=True,
        )
        st.code("uvicorn backend.src.api.server:app --reload", language="bash")

    st.markdown(
        '<hr style="border:none;border-top:1px solid #21262d;margin:0.6rem 0;">',
        unsafe_allow_html=True,
    )

    # Audit history
    st.markdown(
        '<div style="color:#6e7681;font-size:0.7rem;text-transform:uppercase;'
        'letter-spacing:0.1em;margin-bottom:0.4rem;font-weight:600;">Audit History</div>',
        unsafe_allow_html=True,
    )
    history = st.session_state.audit_history
    if not history:
        st.markdown(
            '<p style="color:#3d444d;font-size:0.78rem;">No audits yet.</p>',
            unsafe_allow_html=True,
        )
    else:
        rows = []
        for h in reversed(history[-6:]):
            icon = "🟢" if h["status"] == "PASS" else "🔴"
            url_safe = html.escape(h["video_url"])
            rows.append(
                f'<div style="background:#161b22;border:1px solid #21262d;border-radius:6px;'
                f'padding:0.4rem 0.6rem;margin-bottom:0.3rem;">'
                f'<div style="color:#58a6ff;font-size:0.72rem;overflow:hidden;'
                f'text-overflow:ellipsis;white-space:nowrap;max-width:170px;">{url_safe}</div>'
                f'<div style="display:flex;justify-content:space-between;'
                f'margin-top:3px;color:#6e7681;font-size:0.72rem;">'
                f'<span>{html.escape(h["time"])}</span>'
                f'<span>{icon} {html.escape(h["status"])} · {len(h["violations"])}v</span>'
                f'</div></div>'
            )
        # All history rows in ONE st.markdown call
        st.markdown("\n".join(rows), unsafe_allow_html=True)

        if st.button("🗑️ Clear", use_container_width=True):
            st.session_state.audit_history = []
            st.session_state.result = None
            st.rerun()

    st.markdown(
        f'<hr style="border:none;border-top:1px solid #21262d;margin:0.6rem 0;">'
        f'<p style="color:#3d444d;font-size:0.7rem;margin:0;">'
        f'{datetime.now().strftime("%b %d, %Y  %H:%M")}</p>',
        unsafe_allow_html=True,
    )


# ──────────────────────────────────────────────
#  Top Bar
# ──────────────────────────────────────────────

status_dot = "🟢" if api_ok else "🔴"
st.markdown(
    f'<div style="background:linear-gradient(90deg,#0f2027 0%,#2c5364 100%);'
    f'border-radius:10px;padding:0.55rem 1.2rem;display:flex;align-items:center;'
    f'justify-content:space-between;margin-bottom:0.65rem;border:1px solid #1e3a4a;">'
    f'<div style="display:flex;align-items:center;gap:0.6rem;">'
    f'<span style="font-size:1.2rem;">🛡️</span>'
    f'<div>'
    f'<span style="color:#f1f5f9;font-size:1.05rem;font-weight:700;">Brand Guardian AI</span>'
    f'<span style="color:#7fb3c8;font-size:0.75rem;margin-left:0.7rem;">'
    f'Azure Video Indexer · RAG · LangGraph · Azure OpenAI</span>'
    f'</div></div>'
    f'<div style="color:#7fb3c8;font-size:0.78rem;">{status_dot} API {html.escape(api_msg)}</div>'
    f'</div>',
    unsafe_allow_html=True,
)


# ──────────────────────────────────────────────
#  Input Row
# ──────────────────────────────────────────────

with st.form("audit_form", clear_on_submit=False):
    col_url, col_btn = st.columns([6, 1])
    with col_url:
        video_url_input = st.text_input(
            label="url",
            placeholder="Paste YouTube URL here — e.g. https://youtu.be/...",
            label_visibility="collapsed",
        )
    with col_btn:
        submitted = st.form_submit_button(
            "🔍 Audit",
            use_container_width=True,
            disabled=not api_ok,
        )


# ──────────────────────────────────────────────
#  Audit Execution
# ──────────────────────────────────────────────

if submitted:
    if not video_url_input.strip():
        st.warning("⚠️ Please enter a YouTube URL.")
        st.stop()

    video_url = video_url_input.strip()
    prog  = st.progress(0, text="🚀 Starting pipeline...")
    timer = st.empty()
    holder: dict = {}
    start = time.time()

    thread = threading.Thread(target=run_audit_api, args=(video_url, holder), daemon=True)
    thread.start()

    stage_idx = 0
    while thread.is_alive():
        elapsed = int(time.time() - start)
        m, s = divmod(elapsed, 60)
        timer.caption(f"⏱️ {m}m {s:02d}s elapsed")
        if stage_idx < len(PIPELINE_STAGES):
            pct, msg = PIPELINE_STAGES[stage_idx]
            prog.progress(pct, text=msg)
            stage_idx += 1
        time.sleep(4)

    thread.join()
    prog.progress(100, text="✅ Done")
    time.sleep(0.3)
    prog.empty()
    timer.empty()

    rs = holder.get("status")
    if rs == "timeout":
        st.error("⏱️ Timed out. Try a shorter video or retry.")
        st.stop()
    elif rs == "connection_error":
        st.error("❌ Lost connection to API server.")
        st.stop()
    elif rs in ("http_error", "error"):
        st.error(f"❌ {holder.get('detail', 'Unknown error')}")
        st.stop()

    data    = holder["data"]
    elapsed = int(time.time() - start)
    m, s    = divmod(elapsed, 60)

    st.session_state.result = data
    st.session_state.audit_history.append({
        "video_url":  video_url,
        "status":     data.get("status", "UNKNOWN"),
        "violations": data.get("compliance_results", []),
        "time":       datetime.now().strftime("%H:%M:%S"),
        "elapsed":    f"{m}m {s:02d}s",
    })
    st.rerun()


# ──────────────────────────────────────────────
#  Results Dashboard
# ──────────────────────────────────────────────

result = st.session_state.result

if result is None:
    st.markdown(
        '<div style="display:flex;flex-direction:column;align-items:center;'
        'justify-content:center;height:60vh;color:#3d444d;text-align:center;padding:2rem;">'
        '<div style="font-size:3rem;margin-bottom:0.8rem;">🛡️</div>'
        '<div style="font-size:0.95rem;color:#4a5568;line-height:1.7;">'
        'Paste a YouTube URL above and click <strong style="color:#6e7681;">Audit</strong><br>'
        'to run a full brand compliance audit.</div>'
        '<div style="font-size:0.78rem;color:#2d3748;margin-top:0.8rem;">'
        'yt-dlp → Azure Blob → Video Indexer → RAG → LangGraph → Report</div>'
        '</div>',
        unsafe_allow_html=True,
    )

else:
    # Parse AuditResponse fields (matches server.py exactly)
    audit_status = result.get("status", "UNKNOWN")
    violations   = result.get("compliance_results", [])
    final_report = result.get("final_report", "No report generated.")
    session_id   = result.get("session_id", "—")
    video_id     = result.get("video_id",   "—")
    elapsed_str  = (
        st.session_state.audit_history[-1]["elapsed"]
        if st.session_state.audit_history else "—"
    )

    # Sort by severity order
    violations_sorted = sorted(
        violations,
        key=lambda x: SEVERITY_CONFIG.get(
            x.get("severity", "LOW").upper(), {"order": 99}
        )["order"],
    )

    # ── Metric strip (one complete HTML string) ──
    st.markdown(
        build_metric_strip(video_id, session_id, len(violations), elapsed_str, audit_status),
        unsafe_allow_html=True,
    )

    # ── Two-column layout ──
    left_col, right_col = st.columns([3, 2], gap="medium")

    with left_col:
        # Header row
        sev_counts: dict[str, int] = {}
        for v in violations:
            k = v.get("severity", "UNKNOWN").upper()
            sev_counts[k] = sev_counts.get(k, 0) + 1

        badge_parts = []
        for sev in ["CRITICAL", "HIGH", "MEDIUM", "LOW"]:
            c = sev_counts.get(sev, 0)
            if c:
                cfg = SEVERITY_CONFIG[sev]
                badge_parts.append(
                    f'<span style="color:{cfg["color"]};font-size:0.75rem;font-weight:600;">'
                    f'{cfg["icon"]} {c} {sev}</span>'
                )

        hdr_right = (
            f'<div style="display:flex;gap:0.8rem;">' + "".join(badge_parts) + "</div>"
            if badge_parts else ""
        )
        st.markdown(
            f'<div style="display:flex;justify-content:space-between;'
            f'align-items:center;margin-bottom:0.4rem;">'
            f'<span style="color:#94a3b8;font-size:0.75rem;font-weight:600;'
            f'text-transform:uppercase;letter-spacing:0.08em;">'
            f'🚨 Violations ({len(violations_sorted)})</span>'
            f'{hdr_right}</div>',
            unsafe_allow_html=True,
        )

        # Violations panel — ONE complete HTML string, no splits
        st.markdown(build_violations_panel(violations_sorted), unsafe_allow_html=True)

    with right_col:
        tab_summary, tab_debug = st.tabs(["📝 Summary", "🔧 Debug"])

        with tab_summary:
            # Summary panel — ONE complete HTML string
            st.markdown(build_summary_panel(final_report), unsafe_allow_html=True)

        with tab_debug:
            st.markdown(
                f'<div style="{PANEL_STYLE}">',
                # NOTE: this open-div is safe here because NO other st calls go inside it.
                # st.json and st.download_button render after this div closes in the DOM.
                unsafe_allow_html=True,
            )
            st.markdown(
                '<div style="color:#6e7681;font-size:0.7rem;text-transform:uppercase;'
                'letter-spacing:0.1em;margin-bottom:0.5rem;font-weight:600;">'
                'Raw API Response</div>',
                unsafe_allow_html=True,
            )
            st.json(result)
            st.download_button(
                label="⬇️ Download JSON",
                data=json.dumps(result, indent=2),
                file_name=f"audit_{video_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                mime="application/json",
            )