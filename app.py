"""Paystand Business Review — unified app (deck + dashboard).

Section flow mirrors the V2 frameworks exactly:
  TOF Review:     Exec Summary · Market · GTM · Product · Product×Market ·
                  Strategic Priorities · Trends · Actions & Decisions
  Booking Review: Exec Forecast (W/M/Q) · Forecast Movement · Market · Product · GTM ·
                  Product×Market · Strategic Priorities · Pod Reviews · Actions & Decisions

Present mode = a true click-through slide deck (one slide at a time, big visuals,
talking points). Drill-down = the full scrolling dashboard with supporting detail
(watchlist, wins, full pipeline). Everything is dynamic off the snapshot date.
"""
import datetime as dt
import re
import pandas as pd
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
import plotly.io as pio

# Unify Plotly chart fonts with the Streamlit UI font (Source Sans Pro) so everything matches,
# and standardize on one designed navy palette so the deck looks intentional, not default-Plotly.
_APP_FONT = "Source Sans Pro, -apple-system, Segoe UI, sans-serif"
NAVY_SEQ = ["#1e40af", "#3b82f6", "#0ea5e9", "#6366f1", "#93c5fd", "#1e3a8a", "#60a5fa"]
pio.templates.default = "plotly_white"
_tpl = pio.templates["plotly_white"].layout
_tpl.font.family = _APP_FONT
_tpl.colorway = NAVY_SEQ
_tpl.title.font.size = 15
_tpl.title.x = 0.0
_tpl.title.xanchor = "left"
_tpl.legend.title.text = ""
px.defaults.template = "plotly_white"
px.defaults.color_discrete_sequence = NAVY_SEQ

# Streamlit renders `$...$` in *markdown text* as LaTeX math, so money like "$813K … $279K"
# gets typeset as italics. The fix is to escape `$` -> `\$` in plain markdown text; inside raw
# HTML (unsafe_allow_html=True) there's no LaTeX pass, so we use the `$` HTML entity there (a `\$`
# would show a literal backslash). (Metrics/dataframes aren't markdown, so they're untouched.)
#
# CRITICAL: patch exactly once. Streamlit re-executes this script on every rerun, but the `st`
# module is cached in sys.modules, so without this guard each rerun would capture the
# already-patched functions as "originals" and re-wrap them — making the escaping compound into
# "\\\\\\$" over time.
# Idempotent: collapse any prior escaping first, then re-escape exactly once. This guarantees the
# output is always a single `\$` (or single `&#36;` entity) no matter how many times it runs, so
# stacked wrappers / reruns can never compound into "\\\\\\$".
def _esc_dollars(text):
    if not isinstance(text, str):
        return text
    return text.replace("\\$", "$").replace("$", "\\$")


def _html_dollars(text):
    if not isinstance(text, str):
        return text
    return text.replace("&#36;", "$").replace("$", "&#36;")


if not getattr(st, "_bizreview_dollar_patched", False):
    _st_markdown, _st_caption, _st_write = st.markdown, st.caption, st.write

    def _safe_markdown(body="", *a, **k):
        if isinstance(body, str):
            # HTML: use the `$` entity (renders as $, no LaTeX, no literal backslash).
            # Plain markdown: escape `$` -> `\$`.
            body = _html_dollars(body) if k.get("unsafe_allow_html") else _esc_dollars(body)
        return _st_markdown(body, *a, **k)

    def _safe_caption(body="", *a, **k):
        if not k.get("unsafe_allow_html"):
            body = _esc_dollars(body)
        return _st_caption(body, *a, **k)

    def _safe_write(*a, **k):
        return _st_write(*(_esc_dollars(x) for x in a), **k)

    st.markdown, st.caption, st.write = _safe_markdown, _safe_caption, _safe_write
    st._bizreview_dollar_patched = True

import data
import analytics
import goals as goalstore
import actions as actionstore
import snapshots
import notes
from definitions import MARKET_ORDER, GTM_ENGINES, fmt_triple, GLOSSARY, help_for

st.set_page_config(page_title="Paystand Business Review", layout="wide",
                   initial_sidebar_state="expanded")


def _require_login():
    """Simple shared-password gate. Active only when `app_password` is set in st.secrets
    (i.e. on Streamlit Cloud); skipped locally where no secrets file exists."""
    try:
        configured = st.secrets["app_password"]
    except Exception:
        return  # no password configured -> local/dev, no gate
    if st.session_state.get("authed"):
        return
    st.markdown("### Paystand Business Review")
    with st.form("login"):
        pw = st.text_input("Password", type="password")
        ok = st.form_submit_button("Enter")
    if ok and pw == configured:
        st.session_state["authed"] = True
        st.rerun()
    if ok:
        st.error("Incorrect password")
    st.stop()


_require_login()

PRIMARY = "#1e40af"
RAG = {"Red": "#dc2626", "Yellow": "#f59e0b", "Green": "#16a34a"}
NEUTRAL = "#6b7280"
# Consistent category colors so a market / forecast category is the same hue on every chart.
MARKET_COLORS = {"NetSuite": "#1e40af", "Sage": "#0ea5e9", "Dynamics": "#6366f1", "Other": "#94a3b8"}
FCAT_COLORS = {"Commit": "#1e40af", "Best Case": "#3b82f6", "Pipeline": "#93c5fd",
               "Not Forecasted": "#cbd5e1"}
STAGE_COLORS = {"SQL-Booked": "#1e40af", "SQL-Held": "#3b82f6", "SAL": "#93c5fd"}
METRIC_LABELS = {"sql_booked": "SQL-Booked", "sql_held": "SQL-Held", "sal": "SAL",
                 "pipeline_arr": "Pipeline ARR", "bookings_arr": "Bookings ARR"}
MONEY = {"pipeline_arr", "bookings_arr", "pipeline_acv", "bookings_acv",
         "pipeline_arr_sal", "pipeline_acv_sal"}
ALL_METRICS = analytics.FUNNEL_METRICS
SAL_METRICS = analytics.SAL_PIPELINE_METRICS
TOT_METRICS = ALL_METRICS + SAL_METRICS

# HubSpot deep links — progressive disclosure (V2): the deck answers "what", these dashboards
# answer "why". Portal + dashboard view ids are the company's own, taken from the live deck.
PORTAL_ID = "493201"
_HS_BASE = "https://app.hubspot.com/reports-dashboard"
HS_DASHBOARDS = {
    "funnel":    ("15502490", "Sales funnel by ERP · week vs last"),
    "pipeline":  ("16529206", "Pipeline performance"),
    "waterfall": ("10979308", "Pipeline waterfall · WoW change"),
    "marketing": ("15522896", "Marketing OKRs"),
    "sdr":       ("15513084", "SDR / AE OKRs"),
    "pods":      ("16035037", "Pod performance & win stats"),
}


def hs_deal_url(deal_id):
    return f"https://app.hubspot.com/contacts/{PORTAL_ID}/deal/{deal_id}"


def hs_link(key):
    """Render a subtle 'drill into HubSpot' link for a section (V2 progressive disclosure)."""
    if key not in HS_DASHBOARDS:
        return
    view, label = HS_DASHBOARDS[key]
    url = f"{_HS_BASE}/{PORTAL_ID}/view/{view}"
    st.markdown(
        f"<a href='{url}' target='_blank' style='font-size:0.86em;color:{PRIMARY};"
        f"text-decoration:none;font-weight:600'>↗ Why: open “{label}” in HubSpot</a>",
        unsafe_allow_html=True)


def money(v):
    v = v or 0
    return f"${v/1e6:.2f}M" if abs(v) >= 1e6 else f"${v/1e3:.0f}K"


@st.cache_data(ttl=3600)
def load_tof(today_iso: str):
    today = dt.date.fromisoformat(today_iso)
    p = data.pacing_dates(today)
    base = analytics.enrich_qtd(data.qtd_base(p["quarter_start"], today), p["quarter_start"], today)
    wow = data.wow_sql(today)
    return p, base, wow


@st.cache_data(ttl=3600)
def load_wow_funnel(today_iso: str):
    return data.wow_funnel(dt.date.fromisoformat(today_iso))


@st.cache_data(ttl=3600)
def load_pace_history(today_iso: str):
    return data.pace_history(dt.date.fromisoformat(today_iso))


@st.cache_data(ttl=3600)
def load_booking(today_iso: str):
    return analytics.open_pipeline(dt.date.fromisoformat(today_iso))


@st.cache_data(ttl=3600)
def load_won(today_iso: str):
    today = dt.date.fromisoformat(today_iso)
    p = data.pacing_dates(today)
    return analytics.closed_won(p["quarter_start"], today)


# ---------- Sidebar ----------
with st.sidebar:
    st.markdown("### Paystand Business Review")
    mode = st.radio("View", ["Present (deck)", "Drill-down (dashboard)"], index=0)
    meeting = st.radio("Meeting", ["TOF Review", "Booking Review"], index=0)
    snap = st.date_input("Snapshot date", value=dt.date.today())
    pacing = data.pacing_dates(snap)
    qkey = pacing["quarter_key"]
    st.caption(f"{pacing['quarter_label']} · day {pacing['days_into_quarter']}/"
               f"{pacing['days_in_quarter']} ({pacing['pct_elapsed']:.0f}% elapsed)")

    with st.expander("Goals editor (inputable)", expanded=False):
        st.caption(f"Targets for {qkey}. Saved instantly; attainment recomputes.")
        existing = goalstore.get_goals(qkey)
        for mk in MARKET_ORDER:
            st.markdown(f"**{mk}**")
            cols = st.columns(5)
            for i, metric in enumerate(goalstore.METRICS):
                cur = existing.get(mk, {}).get(metric, 0)
                val = cols[i].number_input(METRIC_LABELS[metric], min_value=0.0,
                                           value=float(cur), key=f"g_{mk}_{metric}",
                                           step=1000.0 if metric in MONEY else 1.0)
                if val != cur:
                    goalstore.set_goal(qkey, mk, metric, val)

today = snap
pacing, base, wow = load_tof(today.isoformat())
qkey = pacing["quarter_key"]
pace = pacing["pct_elapsed"]
market_funnel = analytics.dim_funnel(base, "market").rename(columns={"dim": "market"})

# Backfill reconstructed weekly snapshots so WoW / Trends / Forecast Movement are live now
# (instead of waiting weeks to accrue). Idempotent — only writes dates not already stored.
_hist = analytics.historical_snapshots(base, pacing["quarter_start"], today)
if not snapshots.has_dates(_hist.keys()):
    snapshots.backfill(_hist)


def goal_sum(metric):
    return sum((goalstore.goal_for(qkey, mk, metric) or 0) for mk in MARKET_ORDER)


def attainment(market, metric, actual):
    g = goalstore.goal_for(qkey, market, metric)
    return 100 * actual / g if g else None


def company_attainment(metric):
    """Company-level % of plan, apples-to-apples: sum actuals ONLY for markets that have a goal
    set, against the sum of those goals. Prevents the distortion where all-market actuals are
    compared to a goal set for just one market (which produced nonsense like '590% to plan')."""
    mf = market_funnel.set_index("market")
    gsum = act = 0.0
    have = 0
    for mk in MARKET_ORDER:
        g = goalstore.goal_for(qkey, mk, metric) or 0
        if g:
            gsum += g
            act += float(mf.loc[mk, metric]) if (mk in mf.index and metric in mf.columns) else 0.0
            have += 1
    if not gsum:
        return None, 0
    return 100 * act / gsum, have


def wow_pct(market=None):
    if market is None:
        tw, lw = wow["this_week"].sum(), wow["last_week"].sum()
        return 100 * (tw - lw) / lw if lw else None
    row = wow[wow.market == market]
    if row.empty or row.iloc[0]["last_week"] == 0:
        return None
    return 100 * (row.iloc[0]["this_week"] - row.iloc[0]["last_week"]) / row.iloc[0]["last_week"]


def company_totals():
    cols = [c for c in TOT_METRICS if c in market_funnel.columns]
    return market_funnel[cols].sum()


def erp_split(metric_col, money_fmt=False):
    """Format the strategic-ERP breakdown of a funnel metric (Will's #1 ask: never show a
    topline SQL number without the NetSuite/Sage/Dynamics split underneath it)."""
    mf = market_funnel.set_index("market")
    parts = []
    for mk in MARKET_ORDER:
        v = float(mf.loc[mk, metric_col]) if mk in mf.index and metric_col in mf.columns else 0.0
        parts.append(f"<b>{mk}</b> {money(v) if money_fmt else f'{v:,.0f}'}")
    return " · ".join(parts)


def rag_status(att, pace_pct):
    if att is None:
        return None
    return "Green" if att >= pace_pct else ("Yellow" if att >= pace_pct - 20 else "Red")


def _md_bold(s):
    """Convert markdown **bold** to <b>bold</b> for HTML callouts (where ** won't render)."""
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s) if isinstance(s, str) else s


def decision_callout(question, read, decision):
    """V2: every section answers a business question and leads to a decision — not activity.
    Renders question → what the data says → the recommended decision."""
    question, read, decision = _md_bold(question), _md_bold(read), _md_bold(decision)
    st.markdown(
        f"<div style='background:#eef2ff;border-left:5px solid {PRIMARY};padding:8px 14px;"
        f"border-radius:6px;margin:2px 0 10px'>"
        f"<span style='color:{PRIMARY};font-weight:700'>Question:</span> {question}<br>"
        f"<span style='color:#334155'><b>Read:</b> {read}</span><br>"
        f"<span style='color:#334155'><b>Decision:</b> {decision}</span></div>",
        unsafe_allow_html=True)


def rag_pill(label, status, sub=""):
    c = RAG.get(status, NEUTRAL)
    dot = "●" if status in RAG else "◌"
    label, sub = _md_bold(label), _md_bold(sub)
    st.markdown(
        f"<div style='background:{c}1a;border-left:6px solid {c};padding:10px 14px;"
        f"border-radius:6px;margin-bottom:8px'><b>{label}</b> "
        f"<span style='color:{c};font-weight:700'>{dot} {status or 'Monitor'}</span>"
        f"<br><span style='font-size:0.85em;color:#444'>{sub}</span></div>",
        unsafe_allow_html=True)


def snap_delta(metric, current):
    """WoW % vs the snapshot from ~a week ago (None if no usable history)."""
    d, prior = snapshots.prior_week(today)
    if not prior or not prior.get(metric):
        return None
    return 100 * (current - prior[metric]) / prior[metric]


def actions_section(meeting_key):
    st.caption("The point of the meeting: capture decisions, owners, and follow-ups. Persisted per quarter.")
    items = actionstore.list_items(qkey, meeting_key)
    seed = items or [{"text": "", "owner": "", "status": "Open", "added": today.isoformat()}]
    edited = st.data_editor(
        pd.DataFrame(seed), num_rows="dynamic", use_container_width=True, hide_index=True,
        key=f"act_{meeting_key}_{qkey}",
        column_config={
            "text": st.column_config.TextColumn("Action / Decision", width="large"),
            "owner": st.column_config.TextColumn("Owner"),
            "status": st.column_config.SelectboxColumn("Status", options=actionstore.STATUSES),
            "added": st.column_config.TextColumn("Added", disabled=True)})
    if st.button("Save actions", key=f"save_{meeting_key}"):
        recs = [r for r in edited.to_dict("records") if str(r.get("text", "")).strip()]
        actionstore.update_items(qkey, meeting_key, recs)
        st.success(f"Saved {len(recs)} item(s).")


def strategic_priorities(meeting_key):
    """Data-backed answers to the 6 weekly strategic questions (V2)."""
    mf = market_funnel.set_index("market")
    gtmf = analytics.dim_funnel(base, "gtm").rename(columns={"dim": "gtm"}).set_index("gtm")
    prodf = analytics.dim_funnel(base, "product").rename(columns={"dim": "product"}).set_index("product")

    def mkt(market):
        actual = float(mf.loc[market, "bookings_arr"]) if market in mf.index else 0.0
        pipe = float(mf.loc[market, "pipeline_arr"]) if market in mf.index else 0.0
        att = attainment(market, "bookings_arr", actual)
        s = rag_status(att, pace)
        detail = (f"Bookings {money(actual)}"
                  + (f" · {att:.0f}% to plan (pace {pace:.0f}%)" if att is not None else " · no goal set")
                  + f" · pipeline {money(pipe)}")
        return detail, s

    tof = meeting_key == "TOF"
    q1 = "Are we winning the NetSuite install base?" if tof else "Is the NetSuite install-base strategy producing bookings?"
    q4 = "Is Outbound recovering?" if tof else "Is Outbound contributing future pipeline?"
    q5 = "Is AP becoming a repeatable growth engine?" if tof else "Is AP conversion improving?"
    items = []
    items.append((q1, *mkt("NetSuite")))
    items.append(("Is Sage continuing to scale?" if tof else "Is Sage growth translating into revenue?", *mkt("Sage")))
    items.append(("Is Dynamics improving?", *mkt("Dynamics")))
    bdr_pipe = float(gtmf.loc["BDR", "pipeline_arr"]) if "BDR" in gtmf.index else 0.0
    bdr_sql = int(gtmf.loc["BDR", "sql_booked"]) if "BDR" in gtmf.index else 0
    items.append((q4, f"BDR: {bdr_sql} SQL-Booked · {money(bdr_pipe)} pipeline (QTD). Trend needs snapshot history.", None))
    ap_pipe = float(prodf.loc["AP", "pipeline_acv"]) if "AP" in prodf.index else 0.0
    ap_book = float(prodf.loc["AP", "bookings_acv"]) if "AP" in prodf.index else 0.0
    items.append((q5, f"AP: {money(ap_pipe)} pipeline · {money(ap_book)} bookings (QTD).", None))
    items.append(("Is Global FX proving the motion?", "No FX field in HubSpot yet — needs a definition (RevOps).", None))

    cc = st.columns(2)
    for i, (q, detail, s) in enumerate(items):
        with cc[i % 2]:
            rag_pill(q, s, detail)


# ============================================================================
# Deck styling + slide helpers
# ============================================================================
def inject_deck_css(present: bool):
    """Make Present mode feel like a slide deck (big titles, larger metrics,
    roomy spacing). Drill-down keeps the compact dashboard look.

    NOTE: lines must NOT be indented 4+ spaces — Streamlit's markdown renders
    indented blocks as literal code, which would print the raw <style> tag.
    """
    rules = [
        ".slide-kicker {color:#64748b;font-weight:700;letter-spacing:.09em;text-transform:uppercase;font-size:.78rem;margin:0 0 2px;}",
        ".slide-h1 {font-size:2.15rem;font-weight:800;color:#0f172a;margin:0 0 4px;line-height:1.12;}",
        ".slide-sub {color:#475569;font-size:1.04rem;margin:0 0 14px;}",
        ".hero-q {font-size:2.5rem;font-weight:800;color:#1e40af;margin:8px 0 22px;line-height:1.15;}",
        ".slide-rule {border:none;border-top:3px solid #1e40af;width:64px;margin:0 0 14px;opacity:.9;}",
    ]
    if present:
        rules += [
            '[data-testid="stMetricValue"] {font-size:2.0rem;}',
            '[data-testid="stMetricLabel"] {font-size:0.95rem;}',
            "section.main > div.block-container {padding-top:2.2rem;max-width:1250px;}",
        ]
    st.markdown("<style>" + " ".join(rules) + "</style>", unsafe_allow_html=True)


def slide_header(idx, total, title, subtitle=""):
    st.markdown(f"<div class='slide-kicker'>{meeting} · {pacing['quarter_label']} · "
                f"Slide {idx} of {total}</div>", unsafe_allow_html=True)
    st.markdown(f"<h1 class='slide-h1'>{title}</h1>", unsafe_allow_html=True)
    st.markdown("<hr class='slide-rule'>", unsafe_allow_html=True)
    if subtitle:
        st.markdown(f"<div class='slide-sub'>{subtitle}</div>", unsafe_allow_html=True)


def talking_points(slide_id):
    """Editable qualitative commentary that persists per quarter+meeting+slide."""
    with st.expander("Talking points / commentary", expanded=True):
        cur = notes.get(qkey, meeting, slide_id)
        txt = st.text_area(
            "notes", value=cur, key=f"tp_{meeting}_{slide_id}_{qkey}",
            label_visibility="collapsed", height=90,
            placeholder="Add qualitative context for this slide — what's behind the numbers and "
                        "what to say in the room.")
        if txt != cur:
            notes.set_note(qkey, meeting, slide_id, txt)


def how_to_read(keys):
    """A collapsible plain-language glossary so anyone can present the deck."""
    with st.expander("How to read this deck (definitions)", expanded=False):
        for k in keys:
            if k in GLOSSARY:
                st.markdown(f"- **{k}** — {GLOSSARY[k]}")


def pacing_line():
    """One-line explanation of where we are in the quarter, in human terms."""
    return (f"We're **{pace:.0f}% through {pacing['quarter_label']}** — "
            f"day {pacing['days_into_quarter']} of {pacing['days_in_quarter']} "
            f"({pacing['days_remaining']} days left). That's the *pace* line: a metric should be "
            f"~{pace:.0f}% of its quarterly plan to be on track.")


core_q = "Will we create enough future revenue?" if meeting == "TOF Review" else "Will we hit the number?"


# ============================================================================
# Build the slide list for the active meeting
# ============================================================================
SLIDES = []

# ===================== TOF REVIEW =====================
if meeting == "TOF Review":
    tot = company_totals()
    snapshots.capture(today, {f"tof_{m}": tot[m] for m in ALL_METRICS})

    # GTM funnel computed once (used by the GTM slide and the drill tables).
    gtm = analytics.dim_funnel(base, "gtm").rename(columns={"dim": "gtm"})
    gtm = gtm.set_index("gtm").reindex(GTM_ENGINES).reset_index()
    for col in ["sql_booked", "sql_held", "sal", "pipeline_arr", "pipeline_acv",
                "bookings_arr", "bookings_acv"]:
        gtm[col] = pd.to_numeric(gtm[col], errors="coerce").fillna(0.0)
    _sb, _sh = gtm["sql_booked"].where(gtm["sql_booked"] != 0), gtm["sql_held"].where(gtm["sql_held"] != 0)
    gtm["held_rate"] = (gtm["sql_held"] / _sb * 100).round(0)
    gtm["sal_rate"] = (gtm["sal"] / _sh * 100).round(0)

    def s_exec():
        # Slide 1 = the whole story in one frame (V2: "understand business health within the first
        # few minutes"; Will: "page one is quarter-to-date, high level — no week-over-week here").
        bk_att, _bk_have = company_attainment("bookings_arr")
        sqlb_att, _sqlb_have = company_attainment("sql_booked")
        sqlb = tot["sql_booked"]
        sqlb_proj = sqlb / (pace / 100) if pace else sqlb

        # One clean north-star QTD strip (no WoW — that's the next slide). '% of plan' shown as a
        # plain caption (not a delta) so there's no misleading up/down arrow.
        k = st.columns(5)
        kpis = [("sql_booked", "SQL-Booked", "SQL-Booked", False),
                ("sql_held", "SQL-Held", "SQL-Held", False),
                ("sal", "SAL", "SAL", False),
                ("pipeline_arr_sal", "Pipeline (SAL-qual.)", "SAL-qualified pipeline", True),
                ("bookings_arr", "Bookings ARR", "Bookings ARR", True)]
        for i, (m, lbl, gl, ismoney) in enumerate(kpis):
            att = company_attainment(m)[0] if m in ("sql_booked", "sql_held", "sal", "bookings_arr") else None
            val = money(tot[m]) if ismoney else f"{tot[m]:,.0f}"
            k[i].metric(lbl, val, help=help_for(gl))
            k[i].caption(f"{att:.0f}% of plan" if att is not None else "goal not set")
        st.markdown(f"<div style='background:#f1f5f9;border-radius:6px;padding:8px 14px;margin:6px 0 4px'>"
                    f"<span style='color:#64748b;font-weight:700;font-size:.8rem;text-transform:uppercase;"
                    f"letter-spacing:.06em'>SQL-Booked by strategic ERP</span><br>{erp_split('sql_booked')}"
                    f"</div>", unsafe_allow_html=True)
        st.caption(f"**% of plan** = QTD result ÷ this quarter's goal (set in the sidebar). Compare it to "
                   f"**quarter elapsed ({pace:.0f}%)**: above that = ahead of pace, below = behind. "
                   + pacing_line())

        status = rag_status(bk_att, pace) or "Yellow"
        rag_pill("Quarter health (Bookings ARR vs pace)", status,
                 f"{bk_att:.0f}% of plan at {pace:.0f}% of quarter elapsed"
                 if bk_att is not None else "Set Bookings ARR goals in the sidebar to activate RAG")
        decision_callout(
            "Will we create enough future revenue?",
            f"SQL-Booked {sqlb:,.0f}, projecting **{sqlb_proj:,.0f}** by quarter-end"
            + (f" ({sqlb_att:.0f}% of plan)" if sqlb_att is not None else "")
            + f"; Bookings {money(tot['bookings_arr'])}"
            + (f" = {bk_att:.0f}% of plan at {pace:.0f}% elapsed" if bk_att is not None else "")
            + ".",
            ("Pipeline creation is behind pace — increase BDR/Channel investment or intervene."
             if (sqlb_att is not None and sqlb_att < pace) else
             "Top-of-funnel is on/above pace — hold investment and protect conversion."))

        # Cumulative pace vs target + context (prior quarter, trailing-4-qtr avg, run-rate forecast).
        # Will: "$600K means nothing without a reference" — these lines say are-we-tracking at a glance.
        qs_ts = pd.Timestamp(pacing["quarter_start"])
        q = (today.month - 1) // 3
        q_end = dt.date(today.year + (q == 3), ((q + 1) % 4) * 3 + 1, 1) - dt.timedelta(days=1)
        days = pd.date_range(qs_ts, pd.Timestamp(today))
        day_in_q = pacing["days_in_quarter"]
        today_doq = max(pacing["days_into_quarter"], 1)
        full_dates = pd.date_range(qs_ts, periods=day_in_q)
        pace_metric = st.radio("Pace chart", ["Bookings ARR", "SQL-Booked"], horizontal=True, key="pacem")
        if pace_metric == "Bookings ARR":
            s = base[base["is_book"]].copy()
            s["d"] = pd.to_datetime(s["close_d"])
            daily = s.groupby(s["d"].dt.normalize())["arr"].sum()
            goal = goal_sum("bookings_arr")
            ylab, metric_key = "Cumulative Bookings ARR ($)", "bookings_arr"
        else:
            s = base[base["is_sql"]].copy()
            s["d"] = pd.to_datetime(s["create_d"])
            daily = s.groupby(s["d"].dt.normalize()).size()
            goal = goal_sum("sql_booked")
            ylab, metric_key = "Cumulative SQL-Booked", "sql_booked"
        cum = daily.reindex(days, fill_value=0).cumsum()
        fig = go.Figure()
        # Benchmark context first (so the bold actual line sits on top).
        ph = load_pace_history(today.isoformat())
        curves = analytics.quarter_pace_curves(ph, metric_key, today, day_in_q)
        if curves.get("avg4") is not None:
            fig.add_trace(go.Scatter(x=full_dates, y=curves["avg4"].values, mode="lines",
                                     name="Last-4-qtr avg pace", line={"color": "#c7d2fe", "width": 2}))
        if curves.get("prior") is not None:
            fig.add_trace(go.Scatter(x=full_dates, y=curves["prior"].values, mode="lines",
                                     name="Prior quarter", line={"color": "#94a3b8", "width": 2, "dash": "dot"}))
        fig.add_trace(go.Scatter(x=cum.index, y=cum.values, mode="lines", name="Actual (QTD)",
                                 line={"color": PRIMARY, "width": 3}, fill="tozeroy"))
        # Run-rate forecast: extend today's pace to quarter-end (dashed).
        cum_today = float(cum.iloc[-1]) if len(cum) else 0.0
        fc_dates = full_dates[today_doq - 1:]
        fc_y = [cum_today * (i + 1) / today_doq for i in range(today_doq - 1, day_in_q)]
        fig.add_trace(go.Scatter(x=fc_dates, y=fc_y, mode="lines", name="Run-rate forecast",
                                 line={"color": PRIMARY, "width": 2, "dash": "dash"}))
        if goal:
            total_days = (q_end - pacing["quarter_start"]).days or 1
            tgt_x = pd.date_range(qs_ts, pd.Timestamp(q_end))
            tgt_y = [goal * i / total_days for i in range(len(tgt_x))]
            fig.add_trace(go.Scatter(x=tgt_x, y=tgt_y, mode="lines", name="Plan (linear)",
                                     line={"color": "#16a34a", "dash": "dash"}))
        fig.add_vline(x=pd.Timestamp(today), line_dash="dot", line_color="#cbd5e1")
        fig.update_layout(title=f"{pace_metric}: QTD pace vs prior quarter, 4-qtr avg & forecast",
                          height=320, yaxis_title=ylab, margin=dict(t=40, b=10),
                          legend={"orientation": "h"})
        st.plotly_chart(fig, use_container_width=True)
        proj = cum_today / (pace / 100) if pace else cum_today
        proj_disp = money(proj) if metric_key in MONEY else f"{proj:,.0f}"
        ref = ""
        if curves.get("prior") is not None and len(curves["prior"]) >= today_doq:
            prior_at_day = float(curves["prior"].iloc[today_doq - 1])
            if prior_at_day:
                ref = (f" That's **{(cum_today/prior_at_day - 1)*100:+.0f}%** vs the prior quarter at "
                       f"the same day ({money(prior_at_day) if metric_key in MONEY else f'{prior_at_day:,.0f}'}).")
        st.caption(f"Run-rate forecast ≈ **{proj_disp}** by quarter-end if today's pace holds." + ref +
                   ("" if goal else " Add a goal in the sidebar to overlay the plan line."))

        with st.expander("Funnel, conversion & run-rate detail", expanded=False):
            won = int(base["is_book"].sum())
            cc = st.columns([3, 2])
            with cc[0]:
                fig = go.Figure(go.Funnel(y=["SQL-Booked", "SQL-Held", "SAL", "Won"],
                                x=[tot["sql_booked"], tot["sql_held"], tot["sal"], won],
                                textinfo="value+percent initial", marker={"color": PRIMARY}))
                fig.update_layout(title="Funnel (QTD)", height=300, margin=dict(t=40, b=10))
                st.plotly_chart(fig, use_container_width=True)
            with cc[1]:
                st.markdown("**Run-rate projection (linear)**")
                for m, lbl in [("sql_booked", "SQL-Booked"), ("bookings_arr", "Bookings ARR")]:
                    proj2 = tot[m] / (pace / 100) if pace else tot[m]
                    g = goal_sum(m)
                    disp = money(proj2) if m in MONEY else f"{proj2:,.0f}"
                    gd = money(g) if m in MONEY else f"{g:,.0f}"
                    st.write(f"- **{lbl}:** projecting **{disp}** by quarter-end" + (f" vs {gd} plan" if g else ""))
                st.markdown(f"- **Pipeline:** {money(tot['pipeline_arr'])} created · "
                            f"{money(tot['pipeline_arr_sal'])} SAL-qualified")
                st.caption("Linear pace = QTD ÷ % elapsed. SAL-qualified pipeline is the realistic "
                           "'generated' figure; created runs high.")
        how_to_read(["SQL-Booked", "SQL-Held", "SAL", "Created pipeline", "SAL-qualified pipeline",
                     "Bookings ARR", "Bookings ACV", "ARR vs ACV", "QTD", "Quarter elapsed",
                     "Pace vs prior quarter", "WoW"])

    def s_weekly():
        # Dedicated week-over-week view (Will): show the ACTUAL this-week vs last-week counts,
        # not just a %. Weeks are Mon–Sun; current week is Mon→today.
        wf = load_wow_funnel(today.isoformat())
        this_start = today - dt.timedelta(days=today.weekday())
        last_start = this_start - dt.timedelta(days=7)
        last_end = this_start - dt.timedelta(days=1)
        tw_lbl = f"This week ({this_start:%b %d}–{today:%b %d})"
        lw_lbl = f"Last week ({last_start:%b %d}–{last_end:%b %d})"
        rows_spec = [("SQL-Booked", "sql_booked", False), ("SQL-Held", "sql_held", False),
                     ("SAL", "sal", False), ("Bookings ARR", "bookings_arr", True)]
        tw_tot = {m: float(wf[f"{m}_tw"].sum()) for _, m, _ in rows_spec}
        lw_tot = {m: float(wf[f"{m}_lw"].sum()) for _, m, _ in rows_spec}
        _sb_chg = (tw_tot["sql_booked"] - lw_tot["sql_booked"])
        decision_callout(
            "Did this week speed up or slow down — and in which ERP?",
            f"**{tw_tot['sql_booked']:,.0f}** SQL-Booked this week vs **{lw_tot['sql_booked']:,.0f}** last week "
            f"({_sb_chg:+,.0f}). Volume alone hides the mix — the chart shows which ERP moved.",
            ("Pull forward BDR/Channel activity — weekly creation is dropping."
             if _sb_chg < 0 else "Weekly creation is holding/rising — protect what's working."))
        cards = st.columns(len(rows_spec))
        for i, (lbl, m, ismoney) in enumerate(rows_spec):
            tw, lw = tw_tot[m], lw_tot[m]
            d = tw - lw
            disp = money(tw) if ismoney else f"{tw:,.0f}"
            dd = money(d) if ismoney else f"{d:+,.0f}"
            cards[i].metric(lbl + " (this wk)", disp,
                            f"{dd} vs last wk" if not ismoney else f"{('+' if d>=0 else '')}{dd} vs last wk")
        tbl = []
        for lbl, m, ismoney in rows_spec:
            tw, lw = tw_tot[m], lw_tot[m]
            d = tw - lw
            pct = (100 * d / lw) if lw else None
            fmt = (lambda v: money(v)) if ismoney else (lambda v: f"{v:,.0f}")
            tbl.append({"Metric": lbl, lw_lbl: fmt(lw), tw_lbl: fmt(tw),
                        "Δ": (f"{'+' if d>=0 else ''}{fmt(d)}"),
                        "Δ%": "—" if pct is None else f"{pct:+.0f}%"})
        st.dataframe(pd.DataFrame(tbl), use_container_width=True, hide_index=True)
        # SQL-Booked by ERP, this week vs last — the "80 SQLs but only 8 NetSuite" view.
        wm = wf.set_index("market").reindex(MARKET_ORDER).fillna(0).reset_index()
        melt = pd.DataFrame({
            "Market": list(wm["market"]) * 2,
            "Week": [lw_lbl] * len(wm) + [tw_lbl] * len(wm),
            "SQL-Booked": list(wm["sql_booked_lw"]) + list(wm["sql_booked_tw"]),
        })
        fig = px.bar(melt, x="Market", y="SQL-Booked", color="Week", barmode="group",
                     title="SQL-Booked by ERP — this week vs last", category_orders={"Market": MARKET_ORDER},
                     color_discrete_sequence=["#cbd5e1", PRIMARY])
        fig.update_layout(height=340, xaxis_title="", legend={"orientation": "h"})
        st.plotly_chart(fig, use_container_width=True)
        st.caption("Weekly **flow** (events that happened in each week), not cumulative QTD. The split by ERP "
                   "is the key check: a strong topline week can still be weak in the strategic markets.")

    def s_market():
        _mfi = market_funnel.set_index("market")

        def _mkt_book(m):
            return float(_mfi.loc[m, "bookings_arr"]) if m in _mfi.index else 0.0
        _lead = max(MARKET_ORDER, key=_mkt_book)
        _lag = min(["NetSuite", "Sage", "Dynamics"], key=_mkt_book)
        decision_callout(
            "Where is growth coming from, and where is it slowing?",
            f"**{_lead}** leads bookings ({money(_mkt_book(_lead))}); **{_lag}** is the softest "
            f"strategic market ({money(_mkt_book(_lag))}).",
            f"Direct review time to {_lag}; confirm whether it's a pipeline-creation or conversion problem.")
        st.caption("Quarter-to-date by strategic market; format `value (% to plan)` — a `–` means no goal is "
                   "set yet (Goals editor in the sidebar). **Pipeline (created)** vs **Pipeline (SAL-qual.)** "
                   "are shown side by side: created runs high; SAL-qualified is the realistic 'generated' figure. "
                   "Week-over-week is on the Weekly Performance slide.")
        show = market_funnel.set_index("market").reindex(MARKET_ORDER).fillna(0).reset_index()
        rows = []
        for _, r in show.iterrows():
            mk = r["market"]
            rows.append({
                "Market": mk,
                "SQL-Booked": fmt_triple(r["sql_booked"], None, attainment(mk, "sql_booked", r["sql_booked"])),
                "SQL-Held": fmt_triple(r["sql_held"], None, attainment(mk, "sql_held", r["sql_held"])),
                "SAL": fmt_triple(r["sal"], None, attainment(mk, "sal", r["sal"])),
                "Pipeline ARR (created)": money(r["pipeline_arr"]),
                "Pipeline ARR (SAL-qual.)": money(r["pipeline_arr_sal"]),
                "Bookings ARR": fmt_triple(r["bookings_arr"], None, attainment(mk, "bookings_arr", r["bookings_arr"]), money=True),
                "Bookings ACV": money(r["bookings_acv"]),
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        c1, c2 = st.columns(2)
        with c1:
            melt = show.melt(id_vars="market", value_vars=["sql_booked", "sql_held", "sal"],
                             var_name="Stage", value_name="Count")
            melt["Stage"] = melt["Stage"].map(METRIC_LABELS)
            fig = px.bar(melt, x="market", y="Count", color="Stage", barmode="group",
                         title="Funnel by Market (QTD)", category_orders={"market": MARKET_ORDER},
                         color_discrete_map=STAGE_COLORS)
            fig.update_layout(height=340, xaxis_title="")
            st.plotly_chart(fig, use_container_width=True)
        with c2:
            if goal_sum("bookings_arr"):
                s2 = show.copy()
                s2["att"] = s2["market"].map(lambda mk: attainment(mk, "bookings_arr",
                            s2.loc[s2.market == mk, "bookings_arr"].iloc[0]) or 0)
                fig = px.bar(s2, x="att", y="market", orientation="h", text=s2["att"].map(lambda v: f"{v:.0f}%"),
                             title="Bookings ARR attainment by Market", category_orders={"market": MARKET_ORDER},
                             color="market", color_discrete_map=MARKET_COLORS)
                fig.add_vline(x=pace, line_dash="dash", annotation_text=f"pace {pace:.0f}%")
                fig.update_layout(height=340, xaxis_title="% to plan", yaxis_title="", showlegend=False)
                st.plotly_chart(fig, use_container_width=True)
            else:
                # Was reading as "broken" — it's just empty without goals. Show created vs SAL pipeline instead.
                pm = show.melt(id_vars="market", value_vars=["pipeline_arr", "pipeline_arr_sal"],
                               var_name="Type", value_name="ARR")
                pm["Type"] = pm["Type"].map({"pipeline_arr": "Created", "pipeline_arr_sal": "SAL-qualified"})
                pm["ARR $K"] = pm["ARR"] / 1e3
                fig = px.bar(pm, x="market", y="ARR $K", color="Type", barmode="group",
                             title="Pipeline ARR by Market — created vs SAL-qualified",
                             category_orders={"market": MARKET_ORDER},
                             color_discrete_sequence=["#cbd5e1", PRIMARY])
                fig.update_layout(height=340, xaxis_title="", legend={"orientation": "h"})
                st.plotly_chart(fig, use_container_width=True)
                st.caption("Set Bookings ARR goals in the sidebar to switch this to an attainment-vs-pace chart.")

    def s_gtm():
        _gi = gtm.set_index("gtm")
        _ch_sql = int(_gi.loc["Channels", "sql_booked"]) if "Channels" in _gi.index else 0
        _top_engine = gtm.loc[gtm["sql_booked"].idxmax(), "gtm"] if not gtm["sql_booked"].empty else "—"
        decision_callout(
            "Which acquisition engine is producing repeatable pipeline?",
            f"**{_top_engine}** creates the most SQL-Booked; Channel contributes **{_ch_sql}** "
            "(our highest-priority growth lever per V2).",
            "If Channel is under-indexing, prioritize partner enablement; otherwise double down on the leading engine.")
        # Channel gets dedicated visibility — V2 calls it our strongest acquisition source.
        if "Channels" in _gi.index:
            cr = _gi.loc["Channels"]
            st.markdown("**Channel spotlight** — dedicated visibility per V2")
            ch = st.columns(4)
            ch[0].metric("Channel SQL-Booked", f"{int(cr['sql_booked']):,}", help=help_for("SQL-Booked"))
            ch[1].metric("Channel SQL-Held", f"{int(cr['sql_held']):,}", help=help_for("SQL-Held"))
            ch[2].metric("Channel pipeline ARR", money(cr["pipeline_arr"]), help=help_for("Pipeline ARR"))
            ch[3].metric("Channel bookings ARR", money(cr["bookings_arr"]), help=help_for("Bookings ARR"))
            share = 100 * cr["sql_booked"] / gtm["sql_booked"].sum() if gtm["sql_booked"].sum() else 0
            st.caption(f"Channel = {share:.0f}% of all SQL-Booked this quarter. "
                       "It's our strongest acquisition source and a top growth lever (V2).")
        c1, c2 = st.columns(2)
        with c1:
            melt = gtm.melt(id_vars="gtm", value_vars=["sql_booked", "sql_held", "sal"],
                            var_name="Stage", value_name="Count")
            melt["Stage"] = melt["Stage"].map(METRIC_LABELS)
            fig = px.bar(melt, x="gtm", y="Count", color="Stage", barmode="group",
                         title="Funnel by GTM Engine (QTD)", color_discrete_map=STAGE_COLORS)
            fig.update_layout(height=340, xaxis_title="")
            st.plotly_chart(fig, use_container_width=True)
        with c2:
            g2 = gtm.copy(); g2["Bookings $K"] = g2["bookings_arr"] / 1e3
            fig = px.bar(g2, x="gtm", y="Bookings $K", title="Bookings ARR by GTM Engine ($K)",
                         color="gtm", text_auto=".0f")
            fig.update_layout(height=340, showlegend=False, xaxis_title="")
            st.plotly_chart(fig, use_container_width=True)
        gtm_disp = gtm[["gtm", "sql_booked", "sql_held", "sal", "held_rate", "sal_rate"]].copy()
        gtm_disp.columns = ["GTM Engine", "SQL-Booked", "SQL-Held", "SAL", "Held %", "SAL %"]
        st.dataframe(gtm_disp, use_container_width=True, hide_index=True)

        # GTM × ERP matrix (Will: stop conflating volume with strategic-market production —
        # show which engine actually creates NetSuite/Sage/Dynamics SQLs).
        st.markdown("**Engine × ERP — SQL-Booked (QTD)**")
        gxm = (base[base["is_sql"]].groupby(["gtm", "market"]).size()
               .unstack(fill_value=0).reindex(index=GTM_ENGINES, columns=MARKET_ORDER, fill_value=0))
        fig = px.imshow(gxm, text_auto=True, aspect="auto", color_continuous_scale="Blues",
                        title="Which engine produces which ERP's SQLs")
        fig.update_layout(height=300, xaxis_title="", yaxis_title="", coloraxis_showscale=False)
        st.plotly_chart(fig, use_container_width=True)
        st.caption("A high-volume engine that's light on NetSuite / Sage / Dynamics isn't producing "
                   "the leads that convert to revenue — that's the gap to call out in the room.")

    def s_product():
        prod = analytics.dim_funnel(base, "product").rename(columns={"dim": "product"})
        _pi = prod.set_index("product")
        _ap_book = float(_pi.loc["AP", "bookings_acv"]) if "AP" in _pi.index else 0.0
        _ap_pipe = float(_pi.loc["AP", "pipeline_acv"]) if "AP" in _pi.index else 0.0
        _lead_p = prod.loc[prod["bookings_acv"].idxmax(), "product"] if not prod["bookings_acv"].empty else "—"
        matrix = analytics.product_market_matrix(base, "bookings_acv")
        _cell, _cell_val = None, 0.0
        if not matrix.empty:
            matrix = matrix.reindex(columns=[m for m in MARKET_ORDER if m in matrix.columns])
            _flat = matrix.stack()
            if not _flat.empty:
                _cell, _cell_val = _flat.idxmax(), float(_flat.max())
        _cell_txt = (f" Strongest product × market cell: **{_cell[0]} × {_cell[1]}** "
                     f"({money(_cell_val)} bookings ACV)." if _cell else "")
        decision_callout(
            "Which products — and product × market combos — are actually producing bookings?",
            f"**{_lead_p}** leads bookings ACV; AP carries {money(_ap_pipe)} pipeline and "
            f"{money(_ap_book)} bookings (QTD)." + _cell_txt,
            "Double down where a product and a strategic market already convert together; if AP "
            "pipeline isn't converting, inspect AP deal quality before adding AP demand.")
        c1, c2 = st.columns(2)
        with c1:
            fig = px.pie(prod, names="product", values="pipeline_acv", hole=0.5,
                         title="Pipeline ACV mix by Product (QTD)")
            fig.update_layout(height=320)
            st.plotly_chart(fig, use_container_width=True)
        with c2:
            p2 = prod.copy(); p2["Bookings $K"] = p2["bookings_acv"] / 1e3
            fig = px.bar(p2.sort_values("Bookings $K"), x="Bookings $K", y="product", orientation="h",
                         title="Bookings ACV by Product ($K)", text_auto=".0f", color="product")
            fig.update_layout(height=320, showlegend=False)
            st.plotly_chart(fig, use_container_width=True)
        st.markdown("**Where does each product actually convert? (Product × Market, bookings ACV)**")
        if not matrix.empty:
            fig = px.imshow(matrix, text_auto=".2s", aspect="auto", color_continuous_scale="Blues",
                            title="Bookings ACV: Product (rows) × Market (cols)")
            fig.update_layout(height=300)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.caption("No closed-won bookings yet this quarter to populate the product × market matrix.")

    def s_strategic():
        strategic_priorities("TOF")

    def s_trends():
        hist = snapshots.history()
        tr = pd.DataFrame([{
            "date": d, "SQL-Booked": m.get("tof_sql_booked"), "SQL-Held": m.get("tof_sql_held"),
            "SAL": m.get("tof_sal"), "Bookings ARR": m.get("tof_bookings_arr")} for d, m in hist])
        tr = tr.dropna(subset=["SQL-Booked"])
        if len(tr) >= 2:
            decision_callout(
                "Is the funnel accelerating or stalling week over week?",
                f"{len(tr)} weeks of reconstructed history; latest SQL-Booked {tr['SQL-Booked'].iloc[-1]:,.0f} "
                f"(was {tr['SQL-Booked'].iloc[-2]:,.0f} prior week).",
                "If weekly creation is flattening below pace, intervene on top-of-funnel now — not at quarter-end.")
            c1, c2 = st.columns(2)
            with c1:
                fig = px.line(tr, x="date", y=["SQL-Booked", "SQL-Held", "SAL"], markers=True,
                              title="Funnel: cumulative QTD by week")
                fig.update_layout(height=300, legend={"orientation": "h"}, yaxis_title="count")
                st.plotly_chart(fig, use_container_width=True)
            with c2:
                tr["Bookings $K"] = tr["Bookings ARR"] / 1e3
                fig = px.line(tr, x="date", y="Bookings $K", markers=True, title="Bookings ARR: cumulative QTD by week")
                fig.update_traces(line={"color": PRIMARY, "width": 3})
                fig.update_layout(height=300)
                st.plotly_chart(fig, use_container_width=True)
            st.caption("Reconstructed from date-stamped HubSpot fields (createdate / discovery+happened / "
                       "sal_date / won closedate) — so trends are live today, not pending weeks of capture.")
        else:
            st.info(f"Trends populate once 2+ weekly snapshots exist. Captured so far: **{len(tr)}**.")

    def s_actions():
        actions_section("TOF")

    def drill_extras():
        st.markdown("### Drill-down: raw funnel tables")
        st.caption("Supporting detail behind the deck. In production these deep-link into HubSpot dashboards (V2).")
        st.markdown("**Funnel by Market (QTD)**")
        st.dataframe(market_funnel.set_index("market").reindex(MARKET_ORDER).reset_index(),
                     use_container_width=True, hide_index=True)
        st.markdown("**Funnel by GTM Engine (QTD)**")
        st.dataframe(gtm, use_container_width=True, hide_index=True)
        st.markdown("**Funnel by Product (QTD)**")
        st.dataframe(analytics.dim_funnel(base, "product").rename(columns={"dim": "product"}),
                     use_container_width=True, hide_index=True)

    SLIDES = [
        {"id": "exec", "title": "Executive Summary", "hs": "pipeline",
         "sub": "Will we create enough future revenue? Quarter-to-date health, pace and forecast.",
         "render": s_exec},
        {"id": "weekly", "title": "Weekly Performance", "hs": "funnel",
         "sub": "What happened this week vs last week — by volume and by ERP.", "render": s_weekly},
        {"id": "market", "title": "Market Performance", "hs": "funnel",
         "sub": "Where growth is coming from — and where it's slowing.", "render": s_market},
        {"id": "gtm", "title": "GTM Engine", "hs": "marketing",
         "sub": "Which acquisition engine produces repeatable pipeline.", "render": s_gtm},
        {"id": "product", "title": "Product & Product × Market", "hs": "pipeline",
         "sub": "AR / AP / Multi-product mix, and where each product converts by market.",
         "render": s_product},
        {"id": "strategic", "title": "Strategic Priorities", "hs": "funnel",
         "sub": "The six weekly questions, answered with data.", "render": s_strategic},
        {"id": "trends", "title": "Trends", "hs": "waterfall",
         "sub": "Week-over-week funnel and bookings trajectory.", "render": s_trends},
        {"id": "actions", "title": "Actions & Decisions",
         "sub": "Decisions, owners, and follow-ups from today.", "render": s_actions},
    ]

# ===================== BOOKING REVIEW =====================
else:
    df = load_booking(today.isoformat())
    roll = analytics.forecast_rollup(df)
    weighted = roll["weighted_acv"].sum()
    commit = roll.loc[roll.forecast_cat == "Commit", "acv"].sum()
    best = roll.loc[roll.forecast_cat == "Best Case", "acv"].sum()
    plan = goal_sum("bookings_arr")
    win = analytics.forecast_windows(df, today)
    snapshots.capture(today, {"bk_open_acv": df["acv"].sum(), "bk_commit": commit,
                              "bk_best": best, "bk_weighted": weighted,
                              "bk_q_total": win["Quarter"]["total"]})

    bookings_arr_qtd = float(base.loc[base["is_book"], "arr"].sum())
    open_arr = float(df["arr"].sum())
    gap = max((plan or 0) - bookings_arr_qtd, 0.0)
    coverage = (open_arr / gap) if gap else None
    categorized = int(df["is_forecasted"].sum())
    commit_best_ct = int(df["forecast_cat"].isin(["Commit", "Best Case"]).sum())
    uncat_acv = float(df.loc[~df["is_forecasted"], "acv"].sum())
    q_att = 100 * commit / plan if plan else None
    cov_ok = coverage is None or coverage >= 3
    cov_word = ("no goal set" if coverage is None else
                ("healthy (≥3×)" if coverage >= 3 else "thin (<3×)"))

    def s_exec():
        # Slide 1 = forecast health in one frame (V2: "understand forecast health within the first
        # few minutes"). North-star strip, the W/M/Q forecast, then the decision. Gauges → expander.
        n = st.columns(4)
        n[0].metric("Commit (Q)", money(commit), f"{q_att:.0f}% to plan" if q_att is not None else None,
                    delta_color="off", help=help_for("Commit"))
        n[1].metric("Commit + Best", money(commit + best),
                    f"{100*(commit+best)/plan:.0f}% to plan" if plan else None, delta_color="off",
                    help=help_for("Commit", "Best Case"))
        n[2].metric("Pipeline coverage", "—" if coverage is None else f"{coverage:.1f}×",
                    cov_word, delta_color="off", help=help_for("Pipeline coverage"))
        n[3].metric("Bookings QTD (ARR)", money(bookings_arr_qtd),
                    f"{100*bookings_arr_qtd/plan:.0f}% to plan" if plan else None, delta_color="off",
                    help=help_for("Bookings ARR", "QTD"))
        st.caption(pacing_line())
        decision_callout(
            "Will we hit the number?",
            f"Commit {money(commit)}"
            + (f" = {q_att:.0f}% of plan" if q_att is not None else "")
            + f"; Commit+Best {money(commit + best)}; coverage {('—' if coverage is None else f'{coverage:.1f}×')} of the "
            f"{money(gap)} gap; {categorized}/{len(df)} open deals carry a rep forecast category.",
            ("Pressure-test Commit deals flagged for downgrade and pull Best Case upside forward."
             if (q_att is None or q_att < pace) else "On pace — protect Commit and convert Best Case.")
            + ("" if cov_ok else " Coverage is thin — accelerate pipeline creation / channel."))
        st.caption("Forecast categories come straight from HubSpot `hs_manual_forecast_category` (rep's call = "
                   "source of truth). Open pipeline = deals beyond SQL stage still open. Each horizon = deals "
                   "expected to close by that date.")

        st.markdown("**Forecast by horizon**")
        cols = st.columns(3)
        for i, name in enumerate(["Week", "Month", "Quarter"]):
            w = win[name]
            att = 100 * w["commit"] / plan if (plan and name == "Quarter") else None
            with cols[i]:
                st.markdown(f"**{name}** · by {w['end']:%b %d}")
                st.metric("Commit", money(w["commit"]), f"{w['deals']} deals ≤ horizon",
                          delta_color="off", help=help_for("Commit"))
                st.caption(f"+ Best Case {money(w['best'])} · Total open {money(w['total'])}"
                           + (f" · Commit {att:.0f}% of plan" if att else ""))

        disc_pct = 100 * categorized / len(df) if len(df) else 0
        disc_status = "Green" if disc_pct >= 70 else ("Yellow" if disc_pct >= 40 else "Red")
        ahead_ct = int(df["cat_ahead_of_stage"].sum())
        ahead_acv = float(df.loc[df["cat_ahead_of_stage"], "acv"].sum())
        st.markdown(f"**Forecast discipline:** {categorized}/{len(df)} open deals categorized "
                    f"({commit_best_ct} Commit/Best) · {money(uncat_acv)} ACV uncategorized · "
                    f"**{disc_status}** hygiene. &nbsp; **Coverage:** open pipeline {money(open_arr)} ÷ "
                    f"{money(gap)} gap to plan (benchmark ≥ 3×).")
        if ahead_ct:
            st.markdown(f"<span style='color:#b45309'>⚠ <b>{ahead_ct}</b> deal(s) "
                        f"({money(ahead_acv)} ACV) have a forecast category <b>ahead of their stage</b> "
                        f"(e.g. Commit before Negotiation) — flagged on the Deal Watchlist.</span>",
                        unsafe_allow_html=True)

        with st.expander("Forecast detail — gauges vs plan & how it's computed", expanded=False):
            g = st.columns(4)
            for i, (lbl, val, ref) in enumerate([
                    ("Commit (Q)", commit, plan or commit * 1.5),
                    ("Commit + Best", commit + best, plan or (commit + best) * 1.3),
                    ("Weighted (illustrative)", weighted, plan or weighted * 1.3),
                    ("Total open (Q)", win["Quarter"]["total"], plan or win["Quarter"]["total"])]):
                fig = go.Figure(go.Indicator(
                    mode="gauge+number", value=val, number={"prefix": "$", "valueformat": ".2s"},
                    title={"text": lbl, "font": {"size": 12}},
                    gauge={"axis": {"range": [0, max(ref, val) * 1.1]}, "bar": {"color": PRIMARY},
                           "threshold": {"line": {"color": "red", "width": 3}, "value": ref}}))
                fig.update_layout(height=190, margin=dict(t=36, b=8, l=18, r=18))
                g[i].plotly_chart(fig, use_container_width=True)
            st.caption(f"Red line = Bookings ARR plan ({money(plan) if plan else 'set in sidebar'}). "
                       "Weighted = Σ (Deal ACV × stage probability) per the AE Forecast-Hygiene standard.")
            st.markdown("**Deal probability by stage (AE Forecast-Hygiene standard)** — system-assigned by "
                        "stage, not entered by reps:")
            st.dataframe(pd.DataFrame([
                {"Deal stage": "SQL", "Probability": "6%", "Allowed forecast category": "Not Forecasted"},
                {"Deal stage": "SAL – Discovery / Demo", "Probability": "15%",
                 "Allowed forecast category": "Not Forecasted or Pipeline"},
                {"Deal stage": "OPP – Proposal / ROI", "Probability": "45%",
                 "Allowed forecast category": "Pipeline or Best Case"},
                {"Deal stage": "OPP – Negotiation / Decision", "Probability": "70%",
                 "Allowed forecast category": "Best Case or Commit"},
                {"Deal stage": "Closed Won", "Probability": "100%", "Allowed forecast category": "Closed Won"},
            ]), use_container_width=True, hide_index=True)
            st.markdown(
                "- **Weighted** = Σ (Deal ACV × stage probability above) — the documented hygiene formula.\n"
                "- **Commit** = Σ ACV where the rep's HubSpot category = `COMMIT` (only valid at Negotiation).\n"
                "- **Best Case** = Σ ACV where category = `BEST_CASE` (valid at Proposal/Negotiation).\n"
                "- **Week / Month / Quarter** = the above, restricted to deals with `closedate` ≤ horizon end.\n"
                "- **Pipeline coverage** = open pipeline ARR ÷ remaining gap to plan.\n"
                "- The watchlist flags **'Category ahead of stage'** when a rep's category is more optimistic "
                "than the stage allows (e.g. Commit at Proposal), and recommends aligning it.\n\n"
                "*Confirm with Marcelo: Commit in ACV or ARR? Horizon on `closedate` or a forecast date?*")
        how_to_read(["Commit", "Best Case", "Pipeline coverage", "Weighted", "Forecast discipline",
                     "Bookings ARR", "ARR vs ACV", "RAG", "MEDDPICC", "Quarter elapsed"])

    def s_movement():
        pd_date, prior = snapshots.prior_week(today)
        mv = []
        book_old = prior.get("tof_bookings_arr") if prior else None
        if book_old is not None:
            d = bookings_arr_qtd - book_old
            mv.append({"Metric": "Bookings ARR (QTD)", "Last week": money(book_old), "Now": money(bookings_arr_qtd),
                       "Δ": f"{'+' if d >= 0 else ''}{money(d)}"})
        for key, lbl, cur in [("bk_commit", "Commit", commit), ("bk_best", "Best Case", best),
                              ("bk_weighted", "Weighted", weighted), ("bk_q_total", "Total open (Q)", win["Quarter"]["total"])]:
            old = prior.get(key) if prior else None
            delta = cur - old if old is not None else None
            mv.append({"Metric": lbl, "Last week": money(old) if old is not None else "— (accrues fwd)",
                       "Now": money(cur), "Δ": (f"{'+' if delta >= 0 else ''}{money(delta)}" if delta is not None else "—")})
        st.dataframe(pd.DataFrame(mv), use_container_width=True, hide_index=True)
        st.caption(f"Bookings ARR movement vs {pd_date} (reconstructed). Commit/Best/Weighted reflect the "
                   "rep's current HubSpot forecast category, which has no history — these start moving at the "
                   "next weekly run (captured each load).")

    def s_market():
        _cbm = df[df.forecast_cat == "Commit"].groupby("market")["acv"].sum()
        _rbm = df[df.rag == "Red"].groupby("market")["acv"].sum()
        _top_commit = _cbm.idxmax() if not _cbm.empty else "—"
        _top_red = _rbm.idxmax() if not _rbm.empty else "—"
        decision_callout(
            "Which markets carry the forecast — and the risk?",
            f"**{_top_commit}** holds the most Commit ACV"
            + (f" ({money(_cbm.max())})" if not _cbm.empty else "")
            + f"; **{_top_red}** carries the most at-risk (Red) open ACV"
            + (f" ({money(_rbm.max())})" if not _rbm.empty else "") + ".",
            f"Inspect Red-heavy {_top_red} deals this week; confirm {_top_commit} Commit isn't single-deal dependent.")
        c1, c2 = st.columns([2, 1])
        with c1:
            mk = df.groupby("market").apply(lambda x: pd.Series({
                "Commit": x.loc[x.forecast_cat == "Commit", "acv"].sum(),
                "Best Case": x.loc[x.forecast_cat == "Best Case", "acv"].sum(),
                "Pipeline": x.loc[x.forecast_cat == "Pipeline", "acv"].sum()})).reset_index()
            m2 = mk.melt(id_vars="market", var_name="Category", value_name="ACV")
            fig = px.bar(m2, x="market", y="ACV", color="Category", title="Open ACV by Market × Forecast category",
                         category_orders={"market": MARKET_ORDER}, color_discrete_map=FCAT_COLORS)
            fig.update_layout(height=340, xaxis_title="")
            st.plotly_chart(fig, use_container_width=True)
        with c2:
            t = df.groupby("market").agg(ACV=("acv", "sum"), Deals=("deal_id", "count")).reindex(MARKET_ORDER).dropna().reset_index()
            t["ACV"] = t["ACV"].map(money)
            st.dataframe(t, use_container_width=True, hide_index=True)

    def s_product():
        _pr = df.groupby("product")["acv"].sum()
        _topp = _pr.idxmax() if not _pr.empty else "—"
        _ap = float(_pr.get("AP", 0.0))
        pm_matrix = df.pivot_table(index="product", columns="market", values="acv", aggfunc="sum", fill_value=0)
        pm_matrix = pm_matrix.reindex(columns=[m for m in MARKET_ORDER if m in pm_matrix.columns])
        _flat = pm_matrix.stack()
        _cell = _flat.idxmax() if not _flat.empty else None
        _cell_txt = (f" Biggest product × market cell: **{_cell[0]} × {_cell[1]}** "
                     f"({money(float(_flat.max()))} open ACV)." if _cell else "")
        decision_callout(
            "Where does open forecast sit by product — and which product × market combo carries it?",
            f"**{_topp}** holds the most open ACV; AP carries {money(_ap)} open." + _cell_txt,
            "If AP is heavy on Pipeline but light on Commit, qualify AP deals harder before counting "
            "them; make sure the biggest cell isn't single-deal dependent.")
        c1, c2 = st.columns(2)
        with c1:
            pr = df.groupby("product").agg(acv=("acv", "sum")).reset_index()
            fig = px.pie(pr, names="product", values="acv", hole=0.5, title="Open ACV by Product")
            fig.update_layout(height=300)
            st.plotly_chart(fig, use_container_width=True)
        with c2:
            ps = df.groupby("product").apply(lambda x: pd.Series({
                "Commit": x.loc[x.forecast_cat == "Commit", "acv"].sum(),
                "Best Case": x.loc[x.forecast_cat == "Best Case", "acv"].sum(),
                "Pipeline": x.loc[x.forecast_cat == "Pipeline", "acv"].sum()})).reset_index()
            pm = ps.melt(id_vars="product", var_name="Category", value_name="ACV")
            fig = px.bar(pm, x="product", y="ACV", color="Category",
                         title="Open ACV by Product × Forecast category", color_discrete_map=FCAT_COLORS)
            fig.update_layout(height=300, xaxis_title="")
            st.plotly_chart(fig, use_container_width=True)
        st.markdown("**Where does open ACV concentrate across product and market?**")
        if not _flat.empty:
            fig = px.imshow(pm_matrix, text_auto=".2s", aspect="auto", color_continuous_scale="Blues",
                            title="Open pipeline ACV: Product (rows) × Market (cols)")
            fig.update_layout(height=300)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.caption("No open pipeline to map across product × market yet.")

    def s_gtm():
        gv = df.groupby("gtm").agg(ACV=("acv", "sum"), Deals=("deal_id", "count")).reset_index()
        _topg = gv.loc[gv["ACV"].idxmax(), "gtm"] if not gv["ACV"].empty else "—"
        _ch = float(gv.loc[gv["gtm"] == "Channels", "ACV"].sum())
        decision_callout(
            "Which engine's deals are carrying the open forecast?",
            f"**{_topg}** holds the most open ACV; Channel carries {money(_ch)} (our top growth lever per V2).",
            "Concentrate forecast inspection where the open ACV sits; if Channel is thin, escalate partner pipeline.")
        fig = px.bar(gv.sort_values("ACV"), x="ACV", y="gtm", orientation="h", text_auto=".2s",
                     color="gtm", title="Open pipeline ACV by GTM Engine")
        fig.update_layout(height=300, showlegend=False, yaxis_title="")
        st.plotly_chart(fig, use_container_width=True)

    def s_strategic():
        strategic_priorities("Booking")

    def s_pods():
        st.caption("Format per V2: Status · Biggest Win · Biggest Risk · What Changed · Exec Support Needed.")
        pods = analytics.pod_detail(df)
        pods = pods[pods.deals >= 3] if (pods.deals >= 3).any() else pods
        has_prior = snapshots.prior_week(today)[1] is not None
        cols = st.columns(2)
        for i, (_, r) in enumerate(pods.head(8).iterrows()):
            with cols[i % 2]:
                changed = "WoW from snapshots (next week)" if not has_prior else "see Movement"
                sub = (f"{int(r.deals)} deals · {money(r.acv)} &nbsp; 🔴{int(r.red)} 🟡{int(r.yellow)} 🟢{int(r.green)}<br>"
                       f"<b>Biggest win:</b> {r.biggest_win}<br>"
                       f"<b>Biggest risk:</b> {r.biggest_risk}<br>"
                       f"<b>What changed:</b> {changed}<br>"
                       f"<b>Exec support:</b> {r.exec_support}")
                rag_pill(r["pod"], r["status"], sub)

    def s_watchlist():
        n = st.slider("Top N", 5, 40, 12) if mode.startswith("Drill") else 12
        risky = df[df.rag.isin(["Red", "Yellow"])].nlargest(n, "acv").copy()
        risky["ACV"] = risky["acv"].map(money)
        risky["Close"] = pd.to_datetime(risky["close_d"]).dt.strftime("%b %d")
        risky["Risks"] = risky["risk_flags"].map(lambda fs: ", ".join(fs) if fs else "—")
        risky["MEDDPICC"] = risky["meddpicc"].map(lambda v: f"{v}/5")
        risky["Conf"] = risky["confidence"].map(lambda v: f"{v:.0f}")
        risky["Gong"] = risky["gong_sentiment"].map(lambda v: f"{v:+.2f}" if pd.notna(v) else "—")
        risky["HubSpot"] = risky["deal_id"].map(hs_deal_url)
        wl = risky[["dealname", "owner_name", "ACV", "stage", "Close", "forecast_cat", "rec_category",
                    "rec_reason", "Conf", "rag", "MEDDPICC", "Gong", "Risks", "partner_disp", "action", "HubSpot"]]
        wl.columns = ["Deal", "Owner", "ACV", "Stage", "Close", "Rep cat (HS)", "→ Recommended",
                      "Why", "Conf%", "RAG", "MEDDPICC", "Gong sent.", "Risk flags", "Partner", "Action", "HubSpot"]
        st.dataframe(wl, use_container_width=True, hide_index=True,
                     column_config={"HubSpot": st.column_config.LinkColumn("HubSpot", display_text="open ↗")})
        gong_cov = df["gong_sentiment"].notna().sum()
        st.caption(f"HubSpot links open the deal in portal `{PORTAL_ID}`. **Gong sentiment** is shown where "
                   f"the deal is Gong-linked ({gong_cov}/{len(df)} open deals; linkage is partial). "
                   "Recommended category is rule-based, from forecast-hygiene + MEDDPICC + activity risk.")

    def s_actions():
        actions_section("Booking")

    def drill_extras():
        st.markdown("### Wins")
        won = load_won(today.isoformat())
        if won.empty:
            st.caption("No closed-won deals yet this quarter.")
        else:
            k = st.columns(5)
            k[0].metric("Wins", f"{len(won)}")
            k[1].metric("Total ACV", money(won["acv"].sum()))
            k[2].metric("Total ARR", money(won["arr"].sum()))
            k[3].metric("Avg deal (ACV)", money(won["acv"].mean()))
            cyc = won["days_to_close"].replace(0, pd.NA).dropna()
            k[4].metric("Avg sales cycle", f"{cyc.mean():.0f}d" if len(cyc) else "—")
            c1, c2, c3 = st.columns(3)
            with c1:
                w = won.groupby("market").agg(acv=("acv", "sum")).reset_index()
                w["ACV $K"] = w["acv"] / 1e3
                fig = px.bar(w, x="market", y="ACV $K", color="market", text_auto=".0f", title="Wins ACV by Market")
                fig.update_layout(height=300, showlegend=False)
                st.plotly_chart(fig, use_container_width=True)
            with c2:
                wp = won.groupby("product").agg(acv=("acv", "sum")).reset_index()
                fig = px.pie(wp, names="product", values="acv", hole=0.5, title="Wins ACV by Product")
                fig.update_layout(height=300)
                st.plotly_chart(fig, use_container_width=True)
            with c3:
                wpa = won.groupby("partner_disp").agg(acv=("acv", "sum")).reset_index()
                wpa = wpa.sort_values("acv", ascending=False).head(8)
                wpa["ACV $K"] = wpa["acv"] / 1e3
                fig = px.bar(wpa, x="ACV $K", y="partner_disp", orientation="h", text_auto=".0f",
                             title="Wins ACV by Partner / Direct")
                fig.update_layout(height=300, yaxis_title="")
                st.plotly_chart(fig, use_container_width=True)
            st.caption("'Why we won' needs the Gong agent — CRM `closed_won_reason` is ~90% blank/unstructured.")
        st.markdown("### Full open pipeline")
        cc = ["dealname", "stage", "forecast_cat", "rec_category", "market", "product", "gtm",
              "acv", "owner_name", "pod", "meddpicc", "rag", "confidence", "days_since_contact", "action"]
        st.dataframe(df[cc].sort_values("acv", ascending=False), use_container_width=True, hide_index=True)

    SLIDES = [
        {"id": "exec", "title": "Executive Forecast", "hs": "pods",
         "sub": "Will we hit the number? Week / Month / Quarter forecast, coverage and discipline.",
         "render": s_exec},
        {"id": "movement", "title": "Forecast Movement", "hs": "waterfall",
         "sub": "What changed in the forecast week-over-week.", "render": s_movement},
        {"id": "market", "title": "Market Forecast View", "hs": "funnel",
         "sub": "Which markets carry the forecast — and the risk.", "render": s_market},
        {"id": "product", "title": "Product & Product × Market", "hs": "pipeline",
         "sub": "Open ACV by product and forecast category, and where it concentrates by market.",
         "render": s_product},
        {"id": "gtm", "title": "GTM Engine View", "hs": "sdr",
         "sub": "Open pipeline ACV by acquisition engine.", "render": s_gtm},
        {"id": "strategic", "title": "Strategic Priorities", "hs": "funnel",
         "sub": "The six weekly questions, answered with data.", "render": s_strategic},
        {"id": "pods", "title": "Pod Reviews", "hs": "pods",
         "sub": "Status, wins, risks, and exec support by pod.", "render": s_pods},
        {"id": "watchlist", "title": "Deal Watchlist", "hs": "pipeline",
         "sub": "Largest at-risk deals with recommended forecast actions.", "render": s_watchlist},
        {"id": "actions", "title": "Actions & Decisions",
         "sub": "Decisions, owners, and follow-ups from today.", "render": s_actions},
    ]


# ============================================================================
# Render
# ============================================================================
present = mode.startswith("Present")
inject_deck_css(present)
total = len(SLIDES)

if present:
    # Single source of truth for the current slide. The dropdown is bound to this same
    # key so the buttons and the dropdown can't disagree (which previously caused the
    # dropdown to reset the index right after a Prev/Next click).
    idx_key = f"slide_idx_{meeting}"
    if idx_key not in st.session_state or st.session_state[idx_key] not in range(total):
        st.session_state[idx_key] = 0

    def _go(delta):
        st.session_state[idx_key] = max(0, min(st.session_state[idx_key] + delta, total - 1))

    idx = st.session_state[idx_key]
    nav = st.columns([1.2, 5, 1.2])
    nav[0].button("◀  Prev", use_container_width=True, disabled=(idx == 0),
                  key="nav_prev", on_click=_go, args=(-1,))
    with nav[1]:
        st.selectbox("Go to slide", list(range(total)),
                     format_func=lambda i: f"{i + 1}/{total}  ·  {SLIDES[i]['title']}",
                     label_visibility="collapsed", key=idx_key)
    nav[2].button("Next  ▶", use_container_width=True, disabled=(idx == total - 1),
                  key="nav_next", on_click=_go, args=(1,))

    idx = st.session_state[idx_key]
    st.progress((idx + 1) / total)

    slide = SLIDES[idx]
    slide_header(idx + 1, total, slide["title"], slide.get("sub", ""))
    slide["render"]()
    if slide.get("hs"):
        hs_link(slide["hs"])
    if slide["id"] != "title":
        talking_points(slide["id"])

    # Bottom nav for convenience on long slides.
    st.write("")
    bn = st.columns([1.2, 5, 1.2])
    bn[0].button("◀  Prev", use_container_width=True, disabled=(idx == 0),
                 key="nav_prev_b", on_click=_go, args=(-1,))
    bn[1].markdown(f"<div style='text-align:center;color:#64748b'>Slide {idx + 1} of {total}</div>",
                   unsafe_allow_html=True)
    bn[2].button("Next  ▶", use_container_width=True, disabled=(idx == total - 1),
                 key="nav_next_b", on_click=_go, args=(1,))
    st.markdown(
        f"<div style='text-align:center;color:#94a3b8;font-size:0.78rem;margin-top:6px'>"
        f"Paystand · {meeting} · {pacing['quarter_label']} · Confidential · "
        f"data from HubSpot, updated {pacing['today']:%b %d %Y}</div>",
        unsafe_allow_html=True)
else:
    st.markdown(f"## {meeting} · {pacing['quarter_label']}")
    st.caption(f"*{core_q}*  ·  {pacing['days_remaining']} days remaining  ·  "
               f"updated {pacing['today']:%A, %b %d %Y}")
    for i, slide in enumerate([s for s in SLIDES if s["id"] != "title"], start=1):
        st.markdown(f"### {i}. {slide['title']}")
        slide["render"]()
        if slide.get("hs"):
            hs_link(slide["hs"])
        st.divider()
    drill_extras()

st.divider()
st.caption("Forecast category, deal-stage probability, MEDDPICC, RAG and pod logic run deterministically "
           "from HubSpot data and the documented forecast-hygiene standard.")
