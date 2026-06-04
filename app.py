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
MONEY = {"pipeline_arr", "bookings_arr", "pipeline_acv", "bookings_acv"}
ALL_METRICS = analytics.FUNNEL_METRICS

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


def wow_pct(market=None):
    if market is None:
        tw, lw = wow["this_week"].sum(), wow["last_week"].sum()
        return 100 * (tw - lw) / lw if lw else None
    row = wow[wow.market == market]
    if row.empty or row.iloc[0]["last_week"] == 0:
        return None
    return 100 * (row.iloc[0]["this_week"] - row.iloc[0]["last_week"]) / row.iloc[0]["last_week"]


def company_totals():
    return market_funnel[ALL_METRICS].sum()


def rag_status(att, pace_pct):
    if att is None:
        return None
    return "Green" if att >= pace_pct else ("Yellow" if att >= pace_pct - 20 else "Red")


def decision_callout(question, read, decision):
    """V2: every section answers a business question and leads to a decision — not activity.
    Renders question → what the data says → the recommended decision."""
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
    """Editable qualitative commentary that persists per quarter+meeting+slide.
    This is the layer Looker can't do — and where the Gong+HubSpot agent will
    later auto-draft the narrative."""
    with st.expander("🗒  Talking points / commentary", expanded=True):
        cur = notes.get(qkey, meeting, slide_id)
        txt = st.text_area(
            "notes", value=cur, key=f"tp_{meeting}_{slide_id}_{qkey}",
            label_visibility="collapsed", height=90,
            placeholder="Add qualitative context for this slide — what's behind the numbers, "
                        "what to say in the room. (The AI will draft these from Gong + HubSpot later.)")
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

    def s_title():
        st.markdown(f"<div class='hero-q'>{core_q}</div>", unsafe_allow_html=True)
        c = st.columns(4)
        c[0].metric("SQL-Booked (QTD)", f"{tot['sql_booked']:,.0f}", help=help_for("SQL-Booked", "QTD"))
        c[1].metric("SQL-Held (QTD)", f"{tot['sql_held']:,.0f}", help=help_for("SQL-Held"))
        c[2].metric("Bookings ARR (QTD)", money(tot["bookings_arr"]), help=help_for("Bookings ARR"))
        c[3].metric("Quarter elapsed", f"{pace:.0f}%", help=help_for("Quarter elapsed"))
        st.markdown(pacing_line())
        st.caption(f"Updated {pacing['today']:%A, %b %d %Y} · hover any ⓘ for a definition · "
                   "use ◀ / ▶ above to move through the deck.")
        how_to_read(["SQL-Booked", "SQL-Held", "SAL", "Pipeline ARR", "Pipeline ACV",
                     "Bookings ARR", "Bookings ACV", "ARR vs ACV", "QTD", "Quarter elapsed", "WoW"])

    def s_exec():
        bk_att = 100 * tot["bookings_arr"] / goal_sum("bookings_arr") if goal_sum("bookings_arr") else None
        sqlb = tot["sql_booked"]
        sqlb_proj = sqlb / (pace / 100) if pace else sqlb
        sqlb_goal = goal_sum("sql_booked")
        decision_callout(
            "Will we create enough future revenue?",
            f"SQL-Booked {sqlb:,.0f}, projecting **{sqlb_proj:,.0f}** by quarter-end"
            + (f" vs {sqlb_goal:,.0f} plan" if sqlb_goal else "")
            + f"; Bookings {money(tot['bookings_arr'])}"
            + (f" = {bk_att:.0f}% of plan at {pace:.0f}% elapsed" if bk_att is not None else "")
            + ".",
            ("Pipeline creation is behind pace — increase BDR/Channel investment or intervene."
             if (sqlb_goal and sqlb_proj < sqlb_goal) else
             "Top-of-funnel is on/above pace — hold investment and protect conversion."))
        status = rag_status(bk_att, pace) or "Yellow"
        rag_pill("Quarter health (Bookings ARR vs pace)", status,
                 f"{bk_att:.0f}% of plan at {pace:.0f}% of quarter elapsed"
                 if bk_att is not None else "Set Bookings ARR goals in the sidebar to activate RAG")

        r1 = st.columns(3)
        for i, metric in enumerate(["sql_booked", "sql_held", "sal"]):
            actual = tot[metric]
            g = goal_sum(metric)
            att = 100 * actual / g if g else None
            w = wow_pct() if metric == "sql_booked" else snap_delta(f"tof_{metric}", actual)
            parts = []
            if w is not None:
                parts.append(f"{w:+.0f}% WoW")
            if att is not None:
                parts.append(f"{att:.0f}% plan")
            r1[i].metric(METRIC_LABELS[metric], f"{actual:,.0f}", " · ".join(parts) or None,
                         help=help_for(METRIC_LABELS[metric]))
        r2 = st.columns(4)
        for i, (m, lbl) in enumerate([("pipeline_arr", "Pipeline ARR"), ("pipeline_acv", "Pipeline ACV"),
                                      ("bookings_arr", "Bookings ARR"), ("bookings_acv", "Bookings ACV")]):
            g = goal_sum(m)
            att = 100 * tot[m] / g if g else None
            w = snap_delta(f"tof_{m}", tot[m])
            parts = []
            if w is not None:
                parts.append(f"{w:+.0f}% WoW")
            if att is not None:
                parts.append(f"{att:.0f}% plan")
            r2[i].metric(lbl, money(tot[m]), " · ".join(parts) or None, help=help_for(lbl))
        st.caption("Metric language: **value (WoW Δ, % to plan)** per the V2 spec. WoW compares to the "
                   "snapshot from ~a week ago (reconstructed back to quarter start, so it's live now — "
                   "not day-over-day).")

        c1, c2 = st.columns([3, 2])
        with c1:
            won = int(base["is_book"].sum())
            fig = go.Figure(go.Funnel(y=["SQL-Booked", "SQL-Held", "SAL", "Won"],
                            x=[tot["sql_booked"], tot["sql_held"], tot["sal"], won],
                            textinfo="value+percent initial", marker={"color": PRIMARY}))
            fig.update_layout(title="Funnel (QTD)", height=320, margin=dict(t=40, b=10))
            st.plotly_chart(fig, use_container_width=True)
        with c2:
            st.markdown("**Run-rate projection (linear)**")
            for m, lbl in [("sql_booked", "SQL-Booked"), ("bookings_arr", "Bookings ARR")]:
                proj = tot[m] / (pace / 100) if pace else tot[m]
                g = goal_sum(m)
                disp = money(proj) if m in MONEY else f"{proj:,.0f}"
                gd = money(g) if m in MONEY else f"{g:,.0f}"
                st.write(f"- **{lbl}:** projecting **{disp}** by quarter-end" + (f" vs {gd} plan" if g else ""))
            st.caption("Linear pace = QTD ÷ % elapsed.")

        # Cumulative pace vs target (real QTD data — no snapshot needed)
        qs_ts = pd.Timestamp(pacing["quarter_start"])
        q = (today.month - 1) // 3
        q_end = dt.date(today.year + (q == 3), ((q + 1) % 4) * 3 + 1, 1) - dt.timedelta(days=1)
        days = pd.date_range(qs_ts, pd.Timestamp(today))
        pace_metric = st.radio("Pace chart", ["Bookings ARR", "SQL-Booked"], horizontal=True, key="pacem")
        if pace_metric == "Bookings ARR":
            s = base[base["is_book"]].copy()
            s["d"] = pd.to_datetime(s["close_d"])
            daily = s.groupby(s["d"].dt.normalize())["arr"].sum()
            goal = goal_sum("bookings_arr")
            ylab = "Cumulative Bookings ARR ($)"
        else:
            s = base[base["is_sql"]].copy()
            s["d"] = pd.to_datetime(s["create_d"])
            daily = s.groupby(s["d"].dt.normalize()).size()
            goal = goal_sum("sql_booked")
            ylab = "Cumulative SQL-Booked"
        cum = daily.reindex(days, fill_value=0).cumsum()
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=cum.index, y=cum.values, mode="lines", name="Actual (QTD)",
                                 line={"color": PRIMARY, "width": 3}, fill="tozeroy"))
        if goal:
            total_days = (q_end - pacing["quarter_start"]).days or 1
            tgt_x = pd.date_range(qs_ts, pd.Timestamp(q_end))
            tgt_y = [goal * i / total_days for i in range(len(tgt_x))]
            fig.add_trace(go.Scatter(x=tgt_x, y=tgt_y, mode="lines", name="Linear target",
                                     line={"color": "#9ca3af", "dash": "dash"}))
        fig.update_layout(title=f"{pace_metric}: cumulative QTD vs target", height=300,
                          yaxis_title=ylab, margin=dict(t=40, b=10), legend={"orientation": "h"})
        st.plotly_chart(fig, use_container_width=True)
        if not goal:
            st.caption("Add a goal in the sidebar to overlay the linear target line.")

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
        st.caption("Format `value (WoW Δ, % to plan)`. A `–` means not-yet-available: **% to plan** needs a "
                   "goal in the sidebar Goals editor; **WoW** needs a prior weekly snapshot (accruing from today — "
                   "SQL-Booked already has live WoW). Strategic markets get the depth; the rest roll into Other (V2).")
        show = market_funnel.set_index("market").reindex(MARKET_ORDER).fillna(0).reset_index()
        rows = []
        for _, r in show.iterrows():
            mk = r["market"]
            rows.append({
                "Market": mk,
                "SQL-Booked": fmt_triple(r["sql_booked"], wow_pct(mk), attainment(mk, "sql_booked", r["sql_booked"])),
                "SQL-Held": fmt_triple(r["sql_held"], None, attainment(mk, "sql_held", r["sql_held"])),
                "SAL": fmt_triple(r["sal"], None, attainment(mk, "sal", r["sal"])),
                "Pipeline ARR": fmt_triple(r["pipeline_arr"], None, attainment(mk, "pipeline_arr", r["pipeline_arr"]), money=True),
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
            s2 = show.copy()
            s2["att"] = s2["market"].map(lambda mk: attainment(mk, "bookings_arr",
                        s2.loc[s2.market == mk, "bookings_arr"].iloc[0]) or 0)
            fig = px.bar(s2, x="att", y="market", orientation="h", text=s2["att"].map(lambda v: f"{v:.0f}%"),
                         title="Bookings ARR attainment by Market", category_orders={"market": MARKET_ORDER},
                         color="market", color_discrete_map=MARKET_COLORS)
            fig.add_vline(x=pace, line_dash="dash", annotation_text=f"pace {pace:.0f}%")
            fig.update_layout(height=340, xaxis_title="% to plan", yaxis_title="", showlegend=False)
            st.plotly_chart(fig, use_container_width=True)

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

    def s_product():
        prod = analytics.dim_funnel(base, "product").rename(columns={"dim": "product"})
        c1, c2 = st.columns(2)
        with c1:
            fig = px.pie(prod, names="product", values="pipeline_acv", hole=0.5,
                         title="Pipeline ACV mix by Product (QTD)")
            fig.update_layout(height=340)
            st.plotly_chart(fig, use_container_width=True)
        with c2:
            p2 = prod.copy(); p2["Bookings $K"] = p2["bookings_acv"] / 1e3
            fig = px.bar(p2.sort_values("Bookings $K"), x="Bookings $K", y="product", orientation="h",
                         title="Bookings ACV by Product ($K)", text_auto=".0f", color="product")
            fig.update_layout(height=340, showlegend=False)
            st.plotly_chart(fig, use_container_width=True)

    def s_pxm():
        matrix = analytics.product_market_matrix(base, "bookings_acv")
        if not matrix.empty:
            matrix = matrix.reindex(columns=[m for m in MARKET_ORDER if m in matrix.columns])
            fig = px.imshow(matrix, text_auto=".2s", aspect="auto", color_continuous_scale="Blues",
                            title="Bookings ACV: Product (rows) × Market (cols)")
            fig.update_layout(height=320)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.caption("No closed-won bookings yet this quarter to populate the matrix.")

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
        {"id": "title", "title": "Top of Funnel Review", "sub": "", "render": s_title},
        {"id": "exec", "title": "Executive Summary", "hs": "pipeline",
         "sub": "Are we creating enough future revenue, and are we on pace?", "render": s_exec},
        {"id": "market", "title": "Market Performance", "hs": "funnel",
         "sub": "Where growth is coming from — and where it's slowing.", "render": s_market},
        {"id": "gtm", "title": "GTM Engine", "hs": "marketing",
         "sub": "Which acquisition engine produces repeatable pipeline.", "render": s_gtm},
        {"id": "product", "title": "Product", "hs": "pipeline",
         "sub": "AR / AP / Multi-product mix across pipeline and bookings.", "render": s_product},
        {"id": "pxm", "title": "Product × Market", "hs": "pipeline",
         "sub": "Where product and market intersect on bookings.", "render": s_pxm},
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

    def s_title():
        st.markdown(f"<div class='hero-q'>{core_q}</div>", unsafe_allow_html=True)
        c = st.columns(4)
        c[0].metric("Commit (Q)", money(commit), help=help_for("Commit"))
        c[1].metric("Commit + Best", money(commit + best), help=help_for("Commit", "Best Case"))
        c[2].metric("Pipeline coverage", "—" if coverage is None else f"{coverage:.1f}×",
                    help=help_for("Pipeline coverage"))
        c[3].metric("Bookings QTD (ARR)", money(bookings_arr_qtd), help=help_for("Bookings ARR", "QTD"))
        st.markdown(pacing_line())
        st.caption(f"Updated {pacing['today']:%A, %b %d %Y} · hover any ⓘ for a definition · "
                   "use ◀ / ▶ above to move through the deck.")
        how_to_read(["Commit", "Best Case", "Pipeline coverage", "Weighted", "Forecast discipline",
                     "Bookings ARR", "ARR vs ACV", "RAG", "MEDDPICC", "Quarter elapsed"])

    def s_exec():
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
        cols = st.columns(3)
        for i, name in enumerate(["Week", "Month", "Quarter"]):
            w = win[name]
            att = 100 * w["commit"] / plan if (plan and name == "Quarter") else None
            with cols[i]:
                st.markdown(f"**{name}** · by {w['end']:%b %d}")
                st.metric("Commit", money(w["commit"]), f"{w['deals']} deals ≤ horizon",
                          help=help_for("Commit"))
                st.caption(f"+ Best Case {money(w['best'])} · Total open {money(w['total'])}"
                           + (f" · Commit {att:.0f}% of plan" if att else ""))
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
                   "Weighted = Σ (ACV × HubSpot deal-stage probability) — HubSpot's own numbers, not invented weights.")

        k = st.columns(3)
        k[0].metric("Pipeline coverage", "—" if coverage is None else f"{coverage:.1f}×",
                    cov_word, delta_color="off", help=help_for("Pipeline coverage"))
        k[0].caption(f"Open pipeline {money(open_arr)} ÷ {money(gap)} gap to plan. Benchmark ≥ 3×.")
        disc_pct = 100 * categorized / len(df) if len(df) else 0
        disc_status = "Green" if disc_pct >= 70 else ("Yellow" if disc_pct >= 40 else "Red")
        k[1].metric("Forecast discipline", f"{categorized}/{len(df)} categorized",
                    f"{commit_best_ct} Commit/Best", delta_color="off",
                    help=help_for("Forecast discipline"))
        k[1].caption(f"{money(uncat_acv)} ACV sits in OMIT/uncategorized — not a forecast call. "
                     f"({disc_status} hygiene)")
        k[2].metric("Bookings QTD (ARR)", money(bookings_arr_qtd),
                    f"{100*bookings_arr_qtd/plan:.0f}% of plan" if plan else None, delta_color="off",
                    help=help_for("Bookings ARR", "QTD"))
        k[2].caption("Closed-won ARR booked so far this quarter.")

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
        c1, c2 = st.columns(2)
        with c1:
            pr = df.groupby("product").agg(acv=("acv", "sum")).reset_index()
            fig = px.pie(pr, names="product", values="acv", hole=0.5, title="Open ACV by Product")
            fig.update_layout(height=320)
            st.plotly_chart(fig, use_container_width=True)
        with c2:
            ps = df.groupby("product").apply(lambda x: pd.Series({
                "Commit": x.loc[x.forecast_cat == "Commit", "acv"].sum(),
                "Best Case": x.loc[x.forecast_cat == "Best Case", "acv"].sum(),
                "Pipeline": x.loc[x.forecast_cat == "Pipeline", "acv"].sum()})).reset_index()
            pm = ps.melt(id_vars="product", var_name="Category", value_name="ACV")
            fig = px.bar(pm, x="product", y="ACV", color="Category",
                         title="Open ACV by Product × Forecast category", color_discrete_map=FCAT_COLORS)
            fig.update_layout(height=320, xaxis_title="")
            st.plotly_chart(fig, use_container_width=True)

    def s_gtm():
        gv = df.groupby("gtm").agg(ACV=("acv", "sum"), Deals=("deal_id", "count")).reset_index()
        fig = px.bar(gv.sort_values("ACV"), x="ACV", y="gtm", orientation="h", text_auto=".2s",
                     color="gtm", title="Open pipeline ACV by GTM Engine")
        fig.update_layout(height=300, showlegend=False, yaxis_title="")
        st.plotly_chart(fig, use_container_width=True)

    def s_pxm():
        pm_matrix = df.pivot_table(index="product", columns="market", values="acv", aggfunc="sum", fill_value=0)
        pm_matrix = pm_matrix.reindex(columns=[m for m in MARKET_ORDER if m in pm_matrix.columns])
        fig = px.imshow(pm_matrix, text_auto=".2s", aspect="auto", color_continuous_scale="Blues",
                        title="Open pipeline ACV: Product × Market")
        fig.update_layout(height=300)
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
        st.caption(f"HubSpot links use portal id `{PORTAL_ID}` (placeholder — set PORTAL_ID to activate). "
                   f"**Gong sentiment** shown where deal-linked ({gong_cov}/{len(df)} open deals; "
                   "linkage is partial — see audit). Recommended category is rule-based; the Gong-grounded "
                   "agent will add buyer-evidence rationale.")

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
        {"id": "title", "title": "Booking / Deals Review", "sub": "", "render": s_title},
        {"id": "exec", "title": "Executive Forecast", "hs": "pods",
         "sub": "Will we hit the number? Week / Month / Quarter view.", "render": s_exec},
        {"id": "movement", "title": "Forecast Movement", "hs": "waterfall",
         "sub": "What changed in the forecast week-over-week.", "render": s_movement},
        {"id": "market", "title": "Market Forecast View", "hs": "funnel",
         "sub": "Which markets carry the forecast — and the risk.", "render": s_market},
        {"id": "product", "title": "Product Forecast View", "hs": "pipeline",
         "sub": "Open ACV by product and forecast category.", "render": s_product},
        {"id": "gtm", "title": "GTM Engine View", "hs": "sdr",
         "sub": "Open pipeline ACV by acquisition engine.", "render": s_gtm},
        {"id": "pxm", "title": "Product × Market View", "hs": "pipeline",
         "sub": "Where open ACV concentrates across product and market.", "render": s_pxm},
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
st.caption("AI brain (ask-anything, grounded in BigQuery + Gong) docks here once the LLM runtime is set. "
           "RAG / MEDDPICC / forecast-category / pod logic already runs deterministically from the boss's specs.")
