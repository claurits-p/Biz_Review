"""Deal-level analytics for the Booking Review — implements the boss's
pipeline_forecast_analysis_agent spec deterministically (forecast category, MEDDPICC
coverage, RAG risk scoring, watchlist, partner influence). LLM narrative layers on later.

We pull the (small) open Sales-Pipeline population once and compute everything in pandas.
"""
from __future__ import annotations
import datetime as dt
import functools
import pandas as pd
from data import _q, BASE_FILTER, DEAL
from definitions import market_bucket, product_bucket, gtm_bucket, FORECAST_CATEGORY_MAP

FUNNEL_METRICS = ["sql_booked", "sql_held", "sal", "pipeline_arr", "pipeline_acv",
                  "bookings_arr", "bookings_acv"]
# SAL-qualified pipeline = $ of deals that reached SAL (became accepted opportunities) in the
# period. Per Will: "everything from SAL and onward is pipeline." This is the realistic
# 'generated pipeline' figure; the create-date version (pipeline_arr/acv) overcounts because it
# includes everything ever created, pre-qualification.
SAL_PIPELINE_METRICS = ["pipeline_arr_sal", "pipeline_acv_sal"]


def _eco_label(e: str) -> str:
    e = str(e or "")
    if e == "Netsuite":
        return "NetSuite"
    if e == "Sage Intacct":
        return "Sage"
    if "Microsoft Dynamics" in e:
        return "Dynamics"
    if e == "Acumatica":
        return "Acumatica"
    return "Broad Market"


def enrich_qtd(base: pd.DataFrame, quarter_start, today) -> pd.DataFrame:
    """Add market/gtm/product dims + funnel-event booleans to the qtd_base rows."""
    df = base.copy()
    qs, td = pd.Timestamp(quarter_start), pd.Timestamp(today)
    df["market"] = df["erp"].map(market_bucket)
    df["gtm"] = df["gtm_raw"].map(gtm_bucket)
    df["product"] = df["use_case"].map(product_bucket)
    for col in ["create_d", "disco_d", "sal_d", "close_d"]:
        df[col] = pd.to_datetime(df[col], errors="coerce")
    df["arr"] = pd.to_numeric(df["arr"], errors="coerce").fillna(0)
    df["acv"] = pd.to_numeric(df["acv"], errors="coerce").fillna(0)
    df["is_sql"] = df["create_d"].between(qs, td)
    # SQL-Held = the discovery call actually HAPPENED in-period (meeting_happened_ = 'Yes'),
    # not merely scheduled — otherwise pending/no-show meetings overcount held above booked.
    happened = df["meeting_happened"].astype(str).str.strip().str.lower().eq("yes")
    df["is_held"] = df["disco_d"].between(qs, td) & happened
    df["is_sal"] = df["sal_d"].between(qs, td)
    df["is_book"] = df["won"].fillna(False) & df["close_d"].between(qs, td)
    return df


def dim_funnel(df: pd.DataFrame, dim: str) -> pd.DataFrame:
    """5-metric QTD funnel for any dimension column from the enriched qtd frame."""
    g = df.groupby(dim).apply(lambda x: pd.Series({
        "sql_booked": int(x["is_sql"].sum()),
        "sql_held": int(x["is_held"].sum()),
        "sal": int(x["is_sal"].sum()),
        # Created pipeline (by createdate) — every deal created in the period.
        "pipeline_arr": float(x.loc[x["is_sql"], "arr"].sum()),
        "pipeline_acv": float(x.loc[x["is_sql"], "acv"].sum()),
        # SAL-qualified pipeline (by sal_date) — only deals that became accepted opportunities.
        "pipeline_arr_sal": float(x.loc[x["is_sal"], "arr"].sum()),
        "pipeline_acv_sal": float(x.loc[x["is_sal"], "acv"].sum()),
        "bookings_arr": float(x.loc[x["is_book"], "arr"].sum()),
        "bookings_acv": float(x.loc[x["is_book"], "acv"].sum()),
    })).reset_index().rename(columns={dim: "dim"})
    return g


def product_market_matrix(df: pd.DataFrame, value: str = "bookings_acv") -> pd.DataFrame:
    """Pivot of product (rows) x market (cols) for a chosen funnel metric."""
    val_col = {"bookings_acv": ("is_book", "acv"), "bookings_arr": ("is_book", "arr"),
               "pipeline_acv": ("is_sql", "acv"), "pipeline_arr": ("is_sql", "arr")}[value]
    flag, money = val_col
    sub = df[df[flag]].copy()
    return sub.pivot_table(index="product", columns="market", values=money,
                           aggfunc="sum", fill_value=0)

STAGE_LABELS = {
    "9088aef5-ce3d-4409-917b-64e3c6ab6a91": "SQL",
    "9e0025bf-6ac8-4ea3-8be0-72670975ba17": "SAL – Discovery/Demo",
    "cef195c9-8378-451e-bad0-2ed3826dbf30": "OPP – Proposal/ROI",
    "826cdb91-de03-4bee-a009-1f9aeb058d10": "OPP – Negotiation",
}
STAGE_ORDER = ["SQL", "SAL – Discovery/Demo", "OPP – Proposal/ROI", "OPP – Negotiation"]
# Open pipeline per MD spec = beyond SQL stage (SQL-stage deals are NOT open pipeline).
OPEN_STAGES = ["SAL – Discovery/Demo", "OPP – Proposal/ROI", "OPP – Negotiation"]
OPEN_STAGE_IDS = ["9e0025bf-6ac8-4ea3-8be0-72670975ba17",
                  "cef195c9-8378-451e-bad0-2ed3826dbf30",
                  "826cdb91-de03-4bee-a009-1f9aeb058d10"]
STALE_DAYS = 14

# AE Forecast-Hygiene standard (source of truth). Deal probability is STAGE-DRIVEN — system-assigned
# by stage, not entered by reps — and each stage permits only certain forecast categories.
#   SQL 6% (Not Forecasted) · SAL/Discovery 15% (Not Forecasted/Pipeline) ·
#   Proposal/ROI 45% (Pipeline/Best Case) · Negotiation 70% (Best Case/Commit) · Closed Won 100%.
# Weighted Forecast Amount = Deal Amount × stage probability.
STAGE_PROBABILITY = {
    "SQL": 0.06,
    "SAL – Discovery/Demo": 0.15,
    "OPP – Proposal/ROI": 0.45,
    "OPP – Negotiation": 0.70,
}
# Most optimistic forecast category each stage may legitimately carry (anything beyond = "ahead of stage").
_CAT_RANK = {"Not Forecasted": 0, "Pipeline": 1, "Best Case": 2, "Commit": 3}
STAGE_MAX_CATEGORY = {
    "SQL": "Not Forecasted",
    "SAL – Discovery/Demo": "Pipeline",
    "OPP – Proposal/ROI": "Best Case",
    "OPP – Negotiation": "Commit",
}
STAGE_MAX_RANK = {s: _CAT_RANK[c] for s, c in STAGE_MAX_CATEGORY.items()}


def _yes(v) -> bool:
    return str(v).strip().lower() in ("true", "yes", "1", "won", "identified")


def _has(v) -> bool:
    return str(v).strip() not in ("", "None", "nan", "No", "false", "FALSE")


@functools.lru_cache(maxsize=1)
def gong_sentiment_map() -> dict:
    """deal_id -> (avg_sentiment, trend, call_count) from Gong's deal sentiment trajectory.
    Coverage is partial (Gong↔deal linkage is imperfect) — callers must handle None."""
    sql = """
    SELECT CAST(deal_id AS STRING) did,
           AVG(stage_avg_sentiment) AS sent,
           AVG(stage_avg_trend)     AS trend,
           MAX(call_count)          AS calls
    FROM `paystand.gold_paystand_gong.ml_sentiment_deal_trajectory`
    GROUP BY 1
    """
    try:
        df = _q(sql)
        return {r.did: (r.sent, r.trend, int(r.calls or 0)) for r in df.itertuples()}
    except Exception:
        return {}


@functools.lru_cache(maxsize=1)
def owner_map() -> dict:
    """owner_id -> (name, pod/team). Pod falls back to rep name when team is unset."""
    sql = """
    SELECT CAST(owner_id AS STRING) id, full_name, primary_team_name
    FROM `paystand.silver_paystand_hubspot.dim_hubspot_owner`
    """
    try:
        df = _q(sql)
        out = {}
        for r in df.itertuples():
            name = (r.full_name or "Unknown").strip()
            out[r.id] = (name, r.primary_team_name or name)
        return out
    except Exception:
        return {}


def open_pipeline(today: dt.date) -> pd.DataFrame:
    """Enriched open Sales-Pipeline deals with forecast category, MEDDPICC, RAG, partner."""
    sql = f"""
    SELECT
      d.deal_id, d.property_dealname AS dealname,
      CAST(d.owner_id AS STRING) AS owner_id,
      d.property_sql_generated_by AS gtm_raw, d.property_use_case AS use_case,
      d.property_accounting_erp_software AS erp_raw,
      SAFE_CAST(d.property_arr AS FLOAT64) AS arr,
      SAFE_CAST(d.property_total_ar_ap_acv AS FLOAT64) AS acv,
      d.deal_pipeline_stage_id AS stage_id,
      DATE(d.property_createdate) AS create_d,
      DATE(d.property_closedate)  AS close_d,
      d.property_hs_manual_forecast_category AS fcat,
      SAFE_CAST(d.property_hs_deal_stage_probability AS FLOAT64) AS prob,
      SAFE_CAST(d.property_num_associated_contacts AS INT64) AS n_contacts,
      d.property_hs_next_step AS next_step,
      DATE(d.property_notes_last_contacted) AS last_contact_d,
      d.property_associated_parner_var AS partner,
      d.property_metrics AS m_metrics,
      d.property_economic_buyer_identified_ AS e_buyer,
      d.property_d_what_s_the_decision_process_ AS d_process,
      d.property_main_pain_point AS i_pain,
      d.property_c_competitor_summary AS c_compet,
      d.property_champion_identified_ AS champion
    FROM {DEAL} d
    WHERE {BASE_FILTER} AND d.property_hs_is_closed = FALSE
      AND d.deal_pipeline_stage_id IN ({','.join(repr(s) for s in OPEN_STAGE_IDS)})
    """
    df = _q(sql)
    if df.empty:
        return df
    om = owner_map()
    df["stage"] = df["stage_id"].map(STAGE_LABELS)

    def _eco(e):
        e = str(e or "")
        if e == "Netsuite":
            return "NetSuite"
        if e == "Sage Intacct":
            return "Sage"
        if "Microsoft Dynamics" in e:
            return "Dynamics"
        if e == "Acumatica":
            return "Acumatica"
        return "Broad Market"
    df["market"] = df["erp_raw"].map(lambda e: market_bucket(_eco(e)))
    df["product"] = df["use_case"].map(product_bucket)
    df["gtm"] = df["gtm_raw"].map(gtm_bucket)
    df["forecast_cat"] = df["fcat"].map(lambda x: FORECAST_CATEGORY_MAP.get(x, "Not Forecasted"))
    # Forecast hygiene: did the rep actively put this deal in a forecast category?
    # COMMIT / BEST_CASE / PIPELINE = forecasted; OMIT / blank / LOST = not (V2 + MD source-of-truth).
    df["is_forecasted"] = df["fcat"].isin(["COMMIT", "BEST_CASE", "PIPELINE"])
    df["fcat_disp"] = df["fcat"].map(lambda x: "Uncategorized" if (x is None or str(x).strip() == "")
                                     else FORECAST_CATEGORY_MAP.get(x, str(x).title()))
    # Weighted forecast uses the STAGE-driven probability from the AE Forecast-Hygiene standard
    # (system-assigned by stage): SQL 6% · SAL 15% · Proposal 45% · Negotiation 70%. HubSpot's raw
    # stage-probability field is kept as `hs_prob` for reconciliation.
    df["stage_prob"] = df["stage"].map(STAGE_PROBABILITY).fillna(0.0)
    hs_prob = pd.to_numeric(df["prob"], errors="coerce").fillna(0.0)
    df["hs_prob"] = hs_prob.where(hs_prob <= 1.0, hs_prob / 100.0)
    df["prob"] = df["stage_prob"]
    df["weighted_acv"] = df["acv"] * df["stage_prob"]
    df["weighted_arr"] = df["arr"] * df["stage_prob"]
    # Hygiene: is the rep's forecast category more optimistic than the stage allows?
    df["cat_ahead_of_stage"] = df.apply(
        lambda r: _CAT_RANK.get(r.forecast_cat, 0) > STAGE_MAX_RANK.get(r.stage, 3), axis=1)
    df["stage_max_cat"] = df["stage"].map(STAGE_MAX_CATEGORY).fillna("Commit")
    df["owner_name"] = df["owner_id"].map(lambda x: om.get(x, ("Unassigned", "Unassigned"))[0])
    df["pod"] = df["owner_id"].map(lambda x: om.get(x, ("Unassigned", "Unassigned"))[1])
    df["days_since_contact"] = df["last_contact_d"].map(
        lambda d: (today - d).days if pd.notnull(d) else None)

    # MEDDPICC coverage (M/E/D/I/C/Champion) per-element + total 0-6
    df["has_M"] = df["m_metrics"].map(_has)
    df["has_E"] = df["e_buyer"].map(_yes)
    df["has_D"] = df["d_process"].map(_has)
    df["has_I"] = df["i_pain"].map(_has)
    df["has_C"] = df["c_compet"].map(_has)
    df["has_Champ"] = df["champion"].map(_yes)  # NOTE: champion_identified_ is 100% null in CRM
    # Score out of the 5 trackable elements (Champion not captured in HubSpot today).
    df["meddpicc"] = (df[["has_M", "has_E", "has_D", "has_I", "has_C"]]
                      .sum(axis=1).astype(int))

    # Risk flags per MD risk rules
    def risks(r):
        out = []
        if r.days_since_contact is None or r.days_since_contact > STALE_DAYS:
            out.append("No activity 14d+")
        if not r.n_contacts or r.n_contacts <= 1:
            out.append("Single-threaded")
        if not _has(r.next_step):
            out.append("No next step")
        if not _yes(r.e_buyer):
            out.append("No economic buyer")
        if r.stage in ("OPP – Proposal/ROI", "OPP – Negotiation") and r.meddpicc < 3:
            out.append("Late stage, weak MEDDPICC")
        if r.cat_ahead_of_stage:
            out.append("Category ahead of stage")
        return out
    df["risk_flags"] = df.apply(risks, axis=1)
    df["risk_count"] = df["risk_flags"].map(len)

    def rag(r):
        if r.risk_count >= 3 or "No activity 14d+" in r.risk_flags:
            return "Red"
        if r.risk_count == 2 or r.meddpicc < 3:
            return "Yellow"
        return "Green"
    df["rag"] = df.apply(rag, axis=1)

    # Confidence 1-100 (CRM hygiene + MEDDPICC + recency + threading)
    def conf(r):
        s = 100
        s -= r.risk_count * 15
        s -= max(0, (5 - r.meddpicc)) * 5
        if r.days_since_contact and r.days_since_contact > 30:
            s -= 10
        return max(5, min(100, s))
    df["confidence"] = df.apply(conf, axis=1)
    # Gong evidence (where deal-linked): sentiment + trend + #calls. Partial coverage.
    gs = gong_sentiment_map()
    df["gong_sentiment"] = df["deal_id"].map(lambda x: gs.get(str(x), (None, None, 0))[0])
    df["gong_trend"] = df["deal_id"].map(lambda x: gs.get(str(x), (None, None, 0))[1])
    df["gong_calls"] = df["deal_id"].map(lambda x: gs.get(str(x), (None, None, 0))[2])
    df["partner_flag"] = df["partner"].map(_has)
    df["action"] = df["risk_flags"].map(recommended_action)
    df["rec_category"] = df.apply(recommended_category, axis=1)
    df["rec_reason"] = df.apply(recommended_reason, axis=1)
    df["partner_disp"] = df["partner"].map(lambda p: p if _has(p) else "—")
    return df


def recommended_category(r) -> str:
    """Deterministic forecast-category recommendation per the MD's exact verbs:
    Keep / Upgrade / Downgrade / Remove from forecast / Escalate for manager review.
    The rep's HubSpot category is the source of truth; this is the evidence-based challenge.
    (Gong + LLM will deepen the rationale later.)"""
    cur = r.forecast_cat
    # Category ahead of stage is the clearest, most-documented hygiene violation — align it first.
    if getattr(r, "cat_ahead_of_stage", False):
        return f"Downgrade → {r.stage_max_cat} (stage cap)"
    if cur == "Commit" and (r.rag == "Red" or r.meddpicc < 3 or "No activity 14d+" in r.risk_flags):
        return "Downgrade → Best Case"
    if cur == "Best Case" and r.rag == "Red":
        return "Downgrade → Pipeline"
    if cur in ("Pipeline", "Not Forecasted") and r.rag == "Green" and r.meddpicc >= 4:
        return "Upgrade"
    if cur in ("Pipeline", "Best Case", "Commit") and r.rag == "Red" and r.meddpicc <= 1:
        return "Remove from forecast"
    if r.risk_count >= 3:
        return "Escalate for manager review"
    return "Keep current category"


def recommended_reason(r) -> str:
    """One-line evidence rationale for the recommendation (HubSpot + MEDDPICC facts)."""
    bits = []
    if getattr(r, "cat_ahead_of_stage", False):
        bits.append(f"{r.forecast_cat} at {r.stage} (cap {r.stage_max_cat})")
    if r.meddpicc < 3:
        bits.append(f"MEDDPICC {r.meddpicc}/5")
    if "No activity 14d+" in r.risk_flags:
        bits.append("stale 14d+")
    if "No economic buyer" in r.risk_flags:
        bits.append("no EB")
    if "Single-threaded" in r.risk_flags:
        bits.append("single-threaded")
    if "No next step" in r.risk_flags:
        bits.append("no next step")
    if r.rag == "Green" and not bits:
        bits.append("clean evidence")
    return ", ".join(bits) if bits else "—"


_ACTIONS = {
    "Category ahead of stage": "Align forecast category to deal stage",
    "No activity 14d+": "Re-engage — book next touch this week",
    "Single-threaded": "Multi-thread — add a 2nd+ stakeholder",
    "No next step": "Set a concrete next step + date",
    "No economic buyer": "Confirm + meet the economic buyer",
    "Late stage, weak MEDDPICC": "Close MEDDPICC gaps before forecasting",
}


def recommended_action(flags) -> str:
    for f in ["Category ahead of stage", "No activity 14d+", "No economic buyer",
              "Late stage, weak MEDDPICC", "Single-threaded", "No next step"]:
        if f in flags:
            return _ACTIONS[f]
    return "On track — maintain cadence"


def closed_won(quarter_start, today) -> pd.DataFrame:
    """Closed-won cohort for the quarter (the Wins / 'why we won' base, per the SKILL spec)."""
    qs, td = quarter_start.isoformat(), today.isoformat()
    sql = f"""
    SELECT
      d.property_dealname AS dealname,
      CAST(d.owner_id AS STRING) AS owner_id,
      d.property_accounting_erp_software AS erp_raw,
      d.property_use_case AS use_case,
      -- Use property_arr (the canonical ARR used everywhere else in the app) so the Wins slide
      -- reconciles with Bookings ARR on the exec slides. (Was property_total_arr, ~5% higher.)
      SAFE_CAST(d.property_arr AS FLOAT64)              AS arr,
      SAFE_CAST(d.property_total_ar_ap_acv AS FLOAT64)  AS acv,
      SAFE_CAST(d.property_days_to_close AS FLOAT64)    AS days_to_close,
      d.property_associated_parner_var AS partner,
      d.property_company_annual_revenue_range AS rev_range,
      d.property_closed_won_reason AS win_reason,
      DATE(d.property_closedate) AS close_d
    FROM {DEAL} d
    WHERE {BASE_FILTER} AND d.property_hs_is_closed_won = TRUE
      AND DATE(d.property_closedate) BETWEEN '{qs}' AND '{td}'
    """
    df = _q(sql)
    if df.empty:
        return df
    om = owner_map()
    df["market"] = df["erp_raw"].map(lambda e: market_bucket(_eco_label(e)))
    df["product"] = df["use_case"].map(product_bucket)
    df["owner_name"] = df["owner_id"].map(lambda x: om.get(x, ("Unassigned", ""))[0])
    df["partner_disp"] = df["partner"].map(lambda p: p if _has(p) else "Direct")
    df["arr"] = pd.to_numeric(df["arr"], errors="coerce").fillna(0)
    df["acv"] = pd.to_numeric(df["acv"], errors="coerce").fillna(0)
    return df


def forecast_rollup(df: pd.DataFrame) -> pd.DataFrame:
    """$ by HubSpot forecast category (rep's call = source of truth), in BOTH ARR and ACV so the
    deck can show each against its own plan. Weighted uses the AE Forecast-Hygiene stage
    probability, not invented category weights."""
    g = df.groupby("forecast_cat").agg(deals=("deal_id", "count"),
                                       arr=("arr", "sum"),
                                       acv=("acv", "sum"),
                                       weighted_arr=("weighted_arr", "sum"),
                                       weighted_acv=("weighted_acv", "sum")).reset_index()
    order = ["Commit", "Best Case", "Pipeline", "Not Forecasted"]
    g["__o"] = g["forecast_cat"].map({k: i for i, k in enumerate(order)})
    return g.sort_values("__o").drop(columns="__o")


def pod_summary(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby("pod").agg(
        deals=("deal_id", "count"), acv=("acv", "sum"),
        red=("rag", lambda s: (s == "Red").sum()),
        yellow=("rag", lambda s: (s == "Yellow").sum()),
        green=("rag", lambda s: (s == "Green").sum()),
        avg_conf=("confidence", "mean")).reset_index().sort_values("acv", ascending=False)
    g["status"] = g.apply(lambda r: "Red" if r.red > r.green else
                          ("Yellow" if r.yellow >= r.green else "Green"), axis=1)
    return g


def forecast_windows(df: pd.DataFrame, today) -> dict:
    """Forecast at Week / Month / Quarter levels (Booking V2 #1), in BOTH ARR and ACV.

    Window = open deals expected to close by the end of each horizon (nested by `closedate`).
    `overdue` counts deals whose close date is already in the past (still open) — they fall into
    every horizon, so we surface the count instead of hiding it.
    """
    t = pd.Timestamp(today)
    week_end = t + pd.Timedelta(days=6 - t.weekday())
    month_end = t + pd.offsets.MonthEnd(0)
    q = (today.month - 1) // 3
    q_end = pd.Timestamp(dt.date(today.year + (q == 3), ((q + 1) % 4) * 3 + 1, 1)) - pd.Timedelta(days=1)
    cd = pd.to_datetime(df["close_d"], errors="coerce")
    out = {}
    for name, end in [("Week", week_end), ("Month", month_end), ("Quarter", q_end)]:
        sub = df[cd <= end]
        c = sub.forecast_cat
        out[name] = {
            "end": end.date(),
            "deals": len(sub),
            "overdue": int((cd < t).sum()) if name == "Week" else int(((cd <= end) & (cd < t)).sum()),
            "total": float(sub["acv"].sum()),
            "commit": float(sub.loc[c == "Commit", "acv"].sum()),
            "best": float(sub.loc[c == "Best Case", "acv"].sum()),
            "weighted": float(sub["weighted_acv"].sum()) if len(sub) else 0.0,
            "total_arr": float(sub["arr"].sum()),
            "commit_arr": float(sub.loc[c == "Commit", "arr"].sum()),
            "best_arr": float(sub.loc[c == "Best Case", "arr"].sum()),
            "weighted_arr": float(sub["weighted_arr"].sum()) if len(sub) else 0.0,
        }
    return out


def pod_detail(df: pd.DataFrame) -> pd.DataFrame:
    """Per-pod rows in the V2 Pod-Review format: Status, Biggest Win, Biggest Risk,
    Exec Support Needed. (What Changed needs week-over-week snapshots.)"""
    support = {
        "No economic buyer": "Help access the economic buyer",
        "No activity 14d+": "Unblock stalled engagement",
        "Late stage, weak MEDDPICC": "Deal inspection / qualification help",
        "Single-threaded": "Exec intro to multi-thread",
        "No next step": "Hold rep accountable for next step",
    }
    rows = []
    for pod, g in df.groupby("pod"):
        red, green, yellow = (g.rag == "Red").sum(), (g.rag == "Green").sum(), (g.rag == "Yellow").sum()
        status = "Red" if red > green else ("Yellow" if yellow >= green else "Green")
        winners = g[(g.forecast_cat == "Commit") | (g.rag == "Green")]
        win = (winners if not winners.empty else g).nlargest(1, "acv")
        risk = g[g.rag == "Red"].nlargest(1, "acv")
        ask = "None this week"
        if not risk.empty and risk.iloc[0]["risk_flags"]:
            ask = next((support[f] for f in risk.iloc[0]["risk_flags"] if f in support),
                       "Deal inspection")
        rows.append({
            "pod": pod, "deals": len(g), "acv": float(g["acv"].sum()), "status": status,
            "red": int(red), "yellow": int(yellow), "green": int(green),
            "biggest_win": f"{win.iloc[0]['dealname'][:34]} ({money_short(win.iloc[0]['acv'])})" if not win.empty else "—",
            "biggest_risk": f"{risk.iloc[0]['dealname'][:34]} ({money_short(risk.iloc[0]['acv'])})" if not risk.empty else "—",
            "exec_support": ask,
        })
    return pd.DataFrame(rows).sort_values("acv", ascending=False)


def historical_snapshots(base: pd.DataFrame, quarter_start, today) -> dict:
    """Reconstruct weekly QTD funnel/bookings snapshots from date-stamped fields, so WoW /
    Trends / Movement are live immediately instead of waiting weeks to accrue.

    Each metric is date-driven (createdate, discovery+happened, sal_date, won closedate),
    so the value 'as of' any prior Friday = the same cumulative QTD logic capped at that date.
    Returns {friday_iso: {tof_*: value}} for every Friday from quarter start through today."""
    qs, td = pd.Timestamp(quarter_start), pd.Timestamp(today)
    happened = base["meeting_happened"].astype(str).str.strip().str.lower().eq("yes")
    won = base["won"].fillna(False)
    out = {}
    first_fri = qs + pd.Timedelta(days=(4 - qs.weekday()) % 7)
    fri = first_fri
    while fri.date() <= today:
        sb = base["create_d"].between(qs, fri)
        held = base["disco_d"].between(qs, fri) & happened
        sal = base["sal_d"].between(qs, fri)
        book = won & base["close_d"].between(qs, fri)
        out[fri.date().isoformat()] = {
            "tof_sql_booked": float(sb.sum()),
            "tof_sql_held": float(held.sum()),
            "tof_sal": float(sal.sum()),
            "tof_pipeline_arr": float(base.loc[sb, "arr"].sum()),
            "tof_pipeline_acv": float(base.loc[sb, "acv"].sum()),
            "tof_bookings_arr": float(base.loc[book, "arr"].sum()),
            "tof_bookings_acv": float(base.loc[book, "acv"].sum()),
        }
        fri += pd.Timedelta(days=7)
    return out


def _q_start_of(dte: dt.date) -> dt.date:
    qq = (dte.month - 1) // 3
    return dt.date(dte.year, qq * 3 + 1, 1)


def quarter_pace_curves(hist: pd.DataFrame, metric: str, today: dt.date,
                        days_in_quarter: int) -> dict:
    """Turn daily flow history into cumulative-by-day-of-quarter pace curves so the deck can
    answer 'are we tracking?' — prior quarter and trailing-4-quarter average, aligned to the
    same day index and extended across the full quarter length.

    metric ∈ {'sql_booked', 'bookings_arr'}. Returns {'prior': Series, 'avg4': Series} indexed
    0..days_in_quarter-1 (cumulative, forward-filled). Missing inputs -> empty dict entries.
    """
    if hist is None or hist.empty or metric not in hist.columns:
        return {"prior": None, "avg4": None}
    df = hist.copy()
    df["q_start"] = df["d_date"].apply(lambda x: _q_start_of(x.date()))
    df["doq"] = df.apply(lambda r: (r["d_date"].date() - r["q_start"]).days, axis=1)
    cur_qs = _q_start_of(today)
    full_idx = range(days_in_quarter)

    def _cum(g):
        s = g.groupby("doq")[metric].sum().sort_index().cumsum()
        return s.reindex(full_idx).ffill().fillna(0.0)

    completed = {qs: _cum(g) for qs, g in df.groupby("q_start") if qs < cur_qs}
    if not completed:
        return {"prior": None, "avg4": None}
    ordered = sorted(completed)
    prior = completed[ordered[-1]]
    last4 = [completed[qs] for qs in ordered[-4:]]
    avg4 = pd.concat(last4, axis=1).mean(axis=1)
    return {"prior": prior, "avg4": avg4}


def money_short(v) -> str:
    v = v or 0
    return f"${v/1e6:.2f}M" if abs(v) >= 1e6 else f"${v/1e3:.0f}K"


if __name__ == "__main__":
    import data
    df = open_pipeline(dt.date.today())
    print(f"open pipeline deals: {len(df)}")
    print("\nby forecast category:\n", forecast_rollup(df).to_string(index=False))
    print("\nby RAG:", df["rag"].value_counts().to_dict())
    print("avg MEDDPICC:", round(df["meddpicc"].mean(), 1), "/6")
    print("\npods:\n", pod_summary(df).to_string(index=False))
    print("\ntop watchlist (Red, by ACV):")
    red = df[df.rag == "Red"].nlargest(5, "acv")[["dealname", "stage", "acv", "owner_name", "risk_count", "meddpicc"]]
    print(red.to_string(index=False))
