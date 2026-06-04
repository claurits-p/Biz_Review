"""Data/compute layer for the boss's TOF proposal deck (Business Review Deck_Proposal 06-04-2026).

Isolated module so the proposal deck can evolve without touching the core app's data.py /
analytics.py. Everything reads the live HubSpot bronze table through data._q and respects the
canonical BASE_FILTER (Sales Pipeline + SQL source team known + hygiene carve-outs).

Mappings used by this deck (confirm / iterate with RevOps):
- ERP (5 columns): NetSuite · Sage · Microsoft (Dynamics) · Acumatica · Other (Paystand X / broad)
- Segment (company annual revenue): SMB $1-10M · Mid-Market $10-100M · Enterprise $100M-$1B+
- GTM engine: Marketing · Channels · Outbound (BDR/SDR) · AE
- Product membership (OVERLAPPING, per the latest definition): AR = use case contains "AR";
  AP = contains "AP"; Multi-Product = AR+AP, AR+Expense, AR+Global Payroll, or AP+Global Payroll.
"""
from __future__ import annotations
import datetime as dt
import pandas as pd
from data import _q, BASE_FILTER, DEAL

# This deck is "AR Performance": every slide up to the Product slide is scoped to deals whose
# use case CONTAINS "AR" (so Multi-Product like AR+AP counts; AP-only is excluded). The Product
# slide (slide 11) is the only place AP / Multi-Product are broken out, so it uses BASE_FILTER.
AR_SCOPE = "\n  AND UPPER(IFNULL(d.property_use_case,'')) LIKE '%AR%'"
SCOPED = BASE_FILTER + AR_SCOPE

# ---- dimension SQL ------------------------------------------------------------------------
ERP5_ORDER = ["NetSuite", "Sage", "Microsoft", "Acumatica", "Other"]
ERP5_CASE = """
  CASE
    WHEN d.property_accounting_erp_software = 'Netsuite'                THEN 'NetSuite'
    WHEN d.property_accounting_erp_software = 'Sage Intacct'            THEN 'Sage'
    WHEN d.property_accounting_erp_software LIKE '%Microsoft Dynamics%' THEN 'Microsoft'
    WHEN d.property_accounting_erp_software = 'Acumatica'               THEN 'Acumatica'
    ELSE 'Other'
  END"""

SEGMENT_ORDER = ["SMB", "Mid-Market", "Enterprise"]
SEGMENT_CASE = """
  CASE
    WHEN d.property_company_annual_revenue_range IN ('<$1M','$1M - $10M','$5M - $10M') THEN 'SMB'
    WHEN d.property_company_annual_revenue_range IN ('$10M - $50M','$50M - $100M')      THEN 'Mid-Market'
    WHEN d.property_company_annual_revenue_range IN
         ('$100M - $250M','$250M - $500M','$500M - $1B','$1B+')                         THEN 'Enterprise'
    ELSE 'Unknown'
  END"""

GTM4_ORDER = ["Marketing", "Channels", "Outbound", "AE"]
GTM4_CASE = """
  CASE
    WHEN d.property_sql_generated_by = 'Marketing' THEN 'Marketing'
    WHEN d.property_sql_generated_by = 'Channels'  THEN 'Channels'
    WHEN d.property_sql_generated_by = 'BDR'       THEN 'Outbound'
    WHEN d.property_sql_generated_by = 'AE'        THEN 'AE'
    ELSE 'Other'
  END"""

# Stage time-in-stage columns (HubSpot bronze, milliseconds). Verified against the 4 Sales-Pipeline
# stage GUIDs. We report avg days in each stage as the "time per stage" velocity.
TIME_IN_STAGE = {
    "SQL → SAL":   "property_hs_v_2_latest_time_in_9088_aef_5_ce_3_d_4409_917_b_64_e_3_c_6_ab_6_a_91_1221680679",
    "SAL → ROI":   "property_hs_v_2_latest_time_in_9_e_0025_bf_6_ac_8_4_ea_3_8_be_0_72670975_ba_17_1558515864",
    "ROI → NEG":   "property_hs_v_2_latest_time_in_cef_195_c_9_8378_451_e_bad_0_2_ed_3826_dbf_30_1529380635",
    "NEG → WIN":   "property_hs_v_2_latest_time_in_826_cdb_91_de_03_4_bee_a_009_1_f_9_aeb_058_d_10_1801570979",
}
# Benchmarks (target days / conversion %) — configurable; defaults mirror the proposal deck.
VELOCITY_BENCHMARK = {"SQL → SAL": 10, "SAL → ROI": 10, "ROI → NEG": 15, "NEG → WIN": 12}
CONVERSION_BENCHMARK = {"SQL-H → SAL": 70, "SAL → ROI": 42, "ROI → NEG": 42, "NEG → WIN": 42}
MS_PER_DAY = 86_400_000.0

OPEN_STAGE_IDS = ("9e0025bf-6ac8-4ea3-8be0-72670975ba17",
                  "cef195c9-8378-451e-bad0-2ed3826dbf30",
                  "826cdb91-de03-4bee-a009-1f9aeb058d10")


def quarter_start_of(d: dt.date) -> dt.date:
    q = (d.month - 1) // 3
    return dt.date(d.year, q * 3 + 1, 1)


def shift_quarters(q_start: dt.date, back: int) -> dt.date:
    """Return the quarter-start `back` quarters before q_start."""
    y, q = q_start.year, (q_start.month - 1) // 3
    for _ in range(back):
        q -= 1
        if q < 0:
            q, y = 3, y - 1
    return dt.date(y, q * 3 + 1, 1)


# ---- exec KPIs (slide 3) ------------------------------------------------------------------
def _funnel_between(start: dt.date, end: dt.date) -> dict:
    """The 6 headline funnel metrics for deals whose driving date falls in [start, end]."""
    s, e = start.isoformat(), end.isoformat()
    sql = f"""
    WITH d AS (
      SELECT SAFE_CAST(d.property_arr AS FLOAT64) AS arr,
             SAFE_CAST(d.property_total_ar_ap_acv AS FLOAT64) AS acv,
             DATE(d.property_createdate) create_d, DATE(d.property_sal_date) sal_d,
             DATE(d.property_discovery_call_date) disco_d,
             d.property_meeting_happened_ meeting, DATE(d.property_closedate) close_d,
             d.property_hs_is_closed_won won
      FROM {DEAL} d WHERE {SCOPED})
    SELECT
      COUNTIF(create_d BETWEEN '{s}' AND '{e}')                                   sql_booked,
      COUNTIF(disco_d BETWEEN '{s}' AND '{e}' AND LOWER(meeting)='yes')           sql_held,
      COUNTIF(sal_d BETWEEN '{s}' AND '{e}')                                      sal,
      ROUND(SUM(IF(sal_d BETWEEN '{s}' AND '{e}', arr, 0)))                       pipeline_arr,
      ROUND(SUM(IF(won AND close_d BETWEEN '{s}' AND '{e}', arr, 0)))             bookings_arr,
      ROUND(SUM(IF(won AND close_d BETWEEN '{s}' AND '{e}', acv, 0)))             bookings_acv
    FROM d"""
    return _q(sql).iloc[0].to_dict()


def exec_kpis(today: dt.date) -> dict:
    """Current QTD + same-day-of-quarter prior-quarter and year-ago comparisons + WoW flow."""
    qs = quarter_start_of(today)
    doq = (today - qs).days
    prior_qs = shift_quarters(qs, 1)
    yr_qs = shift_quarters(qs, 4)
    cur = _funnel_between(qs, today)
    prior = _funnel_between(prior_qs, prior_qs + dt.timedelta(days=doq))
    yr = _funnel_between(yr_qs, yr_qs + dt.timedelta(days=doq))
    wow = wow_all(today)

    def pct(a, b):
        return None if not b else 100 * (a - b) / b

    out = {}
    for m in ["sql_booked", "sql_held", "sal", "pipeline_arr", "bookings_arr", "bookings_acv"]:
        out[m] = {"value": cur[m], "vs_prior_q": pct(cur[m], prior[m]),
                  "vs_year_q": pct(cur[m], yr[m]), "wow": wow.get(m)}
    out["_labels"] = {"prior_q": _qlabel(prior_qs), "year_q": _qlabel(yr_qs)}
    return out


def _qlabel(qs: dt.date) -> str:
    return f"Q{(qs.month - 1)//3 + 1} {str(qs.year)[2:]}"


def wow_all(today: dt.date) -> dict:
    """This-week vs last-week FLOW for all 6 metrics (company-wide), returned as WoW %."""
    this_start = today - dt.timedelta(days=today.weekday())
    last_start = this_start - dt.timedelta(days=7)
    last_end = this_start - dt.timedelta(days=1)
    tw = _funnel_between(this_start, today)
    lw = _funnel_between(last_start, last_end)

    def pct(a, b):
        return None if not b else 100 * (a - b) / b
    return {m: pct(tw[m], lw[m]) for m in tw}


# ---- bookings + avg deal size over time (slides 3 & 4) ------------------------------------
def bookings_weekly(today: dt.date, weeks: int = 14) -> pd.DataFrame:
    """Weekly closed-won bookings (ARR & ACV) + deal count + avg ACV, total and by ERP."""
    start = today - dt.timedelta(weeks=weeks)
    sql = f"""
    SELECT DATE_TRUNC(DATE(d.property_closedate), WEEK(MONDAY)) wk,
           {ERP5_CASE} erp,
           SUM(SAFE_CAST(d.property_arr AS FLOAT64)) arr,
           SUM(SAFE_CAST(d.property_total_ar_ap_acv AS FLOAT64)) acv,
           COUNT(*) deals
    FROM {DEAL} d
    WHERE {SCOPED} AND d.property_hs_is_closed_won = TRUE
      AND DATE(d.property_closedate) BETWEEN '{start.isoformat()}' AND '{today.isoformat()}'
    GROUP BY wk, erp ORDER BY wk"""
    df = _q(sql)
    if not df.empty:
        df["wk"] = pd.to_datetime(df["wk"])
    return df


def bookings_quarterly(today: dt.date, n_quarters: int = 6) -> pd.DataFrame:
    """Closed-won bookings (ARR & ACV) + deal count BY QUARTER, total and by ERP, over the last
    `n_quarters` (current quarter is QTD). Powers the quarter-over-quarter trend charts."""
    start = shift_quarters(quarter_start_of(today), n_quarters - 1)
    sql = f"""
    SELECT DATE_TRUNC(DATE(d.property_closedate), QUARTER) q,
           {ERP5_CASE} erp,
           SUM(SAFE_CAST(d.property_arr AS FLOAT64)) arr,
           SUM(SAFE_CAST(d.property_total_ar_ap_acv AS FLOAT64)) acv,
           COUNT(*) deals
    FROM {DEAL} d
    WHERE {SCOPED} AND d.property_hs_is_closed_won = TRUE
      AND DATE(d.property_closedate) BETWEEN '{start.isoformat()}' AND '{today.isoformat()}'
    GROUP BY q, erp ORDER BY q"""
    df = _q(sql)
    if not df.empty:
        df["q"] = pd.to_datetime(df["q"])
        df["qlabel"] = df["q"].apply(lambda d: f"Q{(d.month - 1)//3 + 1} {str(d.year)[2:]}")
    return df


# ---- key stats by ERP (slide 5) -----------------------------------------------------------
def key_stats_by_erp(today: dt.date) -> pd.DataFrame:
    """QTD per-ERP: closed-won ARR, avg ACV, new logos (won count), sales cycle, logo %,
    bookings %, open pipeline ARR. Plus prior-quarter (same day-of-quarter) deltas."""
    qs = quarter_start_of(today)
    doq = (today - qs).days
    prior_qs = shift_quarters(qs, 1)

    def _stats(start, end):
        s, e = start.isoformat(), end.isoformat()
        sql = f"""
        SELECT {ERP5_CASE} erp,
          ROUND(SUM(SAFE_CAST(d.property_arr AS FLOAT64))) won_arr,
          ROUND(AVG(SAFE_CAST(d.property_total_ar_ap_acv AS FLOAT64))) avg_acv,
          COUNT(*) logos,
          ROUND(AVG(SAFE_CAST(d.property_days_to_close AS FLOAT64))) cycle
        FROM {DEAL} d
        WHERE {SCOPED} AND d.property_hs_is_closed_won = TRUE
          AND DATE(d.property_closedate) BETWEEN '{s}' AND '{e}'
        GROUP BY erp"""
        return _q(sql).set_index("erp")

    cur = _stats(qs, today)
    prior = _stats(prior_qs, prior_qs + dt.timedelta(days=doq))
    # Open pipeline ARR by ERP (current snapshot).
    op = _q(f"""
        SELECT {ERP5_CASE} erp, ROUND(SUM(SAFE_CAST(d.property_arr AS FLOAT64))) open_arr
        FROM {DEAL} d WHERE {SCOPED} AND d.property_hs_is_closed = FALSE
          AND d.deal_pipeline_stage_id IN {OPEN_STAGE_IDS}
        GROUP BY erp""").set_index("erp")

    rows = cur.reindex(ERP5_ORDER).fillna(0)
    prior = prior.reindex(ERP5_ORDER).fillna(0)
    op = op.reindex(ERP5_ORDER).fillna(0)
    total_logos = rows["logos"].sum() or 1
    total_arr = rows["won_arr"].sum() or 1
    out = pd.DataFrame(index=ERP5_ORDER)
    out["won_arr"] = rows["won_arr"]
    out["avg_acv"] = rows["avg_acv"]
    out["logos"] = rows["logos"]
    out["cycle"] = rows["cycle"]
    out["logo_pct"] = (rows["logos"] / total_logos * 100).round(0)
    out["bookings_pct"] = (rows["won_arr"] / total_arr * 100).round(0)
    out["open_arr"] = op["open_arr"]
    out["won_arr_prior"] = prior["won_arr"]
    out["logos_prior"] = prior["logos"]
    return out


# ---- win rates (slides 6 & 7) -------------------------------------------------------------
def _grain_window(today: dt.date, grain: str):
    if grain == "Weekly":
        return today - dt.timedelta(days=today.weekday()), today
    if grain == "Monthly":
        return today.replace(day=1), today
    return quarter_start_of(today), today  # Quarterly = QTD


def win_rate_matrix(today: dt.date, dim: str, grain: str = "Quarterly") -> pd.DataFrame:
    """Win rate % = won / (won + lost) by Segment (rows) × dim (cols), over the grain window.
    dim ∈ {'erp','gtm'}. Closed deals only (won or lost) within the window by closedate."""
    start, end = _grain_window(today, grain)
    dim_case = ERP5_CASE if dim == "erp" else GTM4_CASE
    cols = ERP5_ORDER if dim == "erp" else GTM4_ORDER
    sql = f"""
    SELECT {SEGMENT_CASE} segment, {dim_case} dimv,
      COUNTIF(d.property_hs_is_closed_won = TRUE)  won,
      COUNT(*)                                     closed
    FROM {DEAL} d
    WHERE {SCOPED} AND d.property_hs_is_closed = TRUE
      AND DATE(d.property_closedate) BETWEEN '{start.isoformat()}' AND '{end.isoformat()}'
    GROUP BY segment, dimv"""
    df = _q(sql)
    rate = pd.DataFrame(index=SEGMENT_ORDER, columns=cols, dtype=float)
    counts = pd.DataFrame(index=SEGMENT_ORDER, columns=cols, dtype=float)
    for r in df.itertuples():
        if r.segment in rate.index and r.dimv in rate.columns:
            rate.loc[r.segment, r.dimv] = (100 * r.won / r.closed) if r.closed else None
            counts.loc[r.segment, r.dimv] = r.closed
    return rate, counts, f"{start:%b %d}–{end:%b %d}"


# ---- GTM engine performance (slide 8) -----------------------------------------------------
def gtm_engine_performance(today: dt.date) -> pd.DataFrame:
    """SAL-qualified pipeline ARR by GTM engine (rows) × ERP (cols) + engine totals + % of total."""
    qs = quarter_start_of(today)
    sql = f"""
    SELECT {GTM4_CASE} gtm, {ERP5_CASE} erp,
      ROUND(SUM(SAFE_CAST(d.property_arr AS FLOAT64))) pipe_arr
    FROM {DEAL} d
    WHERE {SCOPED} AND DATE(d.property_sal_date) BETWEEN '{qs.isoformat()}' AND '{today.isoformat()}'
    GROUP BY gtm, erp"""
    df = _q(sql)
    mat = df.pivot_table(index="gtm", columns="erp", values="pipe_arr", aggfunc="sum", fill_value=0)
    mat = mat.reindex(index=GTM4_ORDER, columns=ERP5_ORDER).fillna(0)
    mat["Total"] = mat.sum(axis=1)
    grand = mat["Total"].sum() or 1
    mat["pct_total"] = (mat["Total"] / grand * 100).round(0)
    return mat


# ---- pipeline velocity (slide 9) ----------------------------------------------------------
def stage_velocity(today: dt.date, lookback_days: int = 180) -> pd.DataFrame:
    """Avg days in each pipeline stage vs benchmark, over deals closed-won in the lookback."""
    start = (today - dt.timedelta(days=lookback_days)).isoformat()
    sel = ",\n".join(
        f"ROUND(AVG(SAFE_CAST({col} AS FLOAT64))/{MS_PER_DAY},1) AS s{i}"
        for i, col in enumerate(TIME_IN_STAGE.values()))
    sql = f"""
    SELECT {sel}
    FROM {DEAL} d WHERE {SCOPED} AND d.property_hs_is_closed_won = TRUE
      AND DATE(d.property_closedate) >= '{start}'"""
    row = _q(sql).iloc[0]
    rows = []
    for i, (stage, _) in enumerate(TIME_IN_STAGE.items()):
        days = float(row[f"s{i}"] or 0)
        bench = VELOCITY_BENCHMARK[stage]
        rows.append({"stage": stage, "days": round(days), "benchmark": bench,
                     "delta": round(days - bench),
                     "status": "On pace" if days <= bench else "Slow"})
    df = pd.DataFrame(rows)
    actual_close = int(_q(
        f"""SELECT ROUND(AVG(SAFE_CAST(d.property_days_to_close AS FLOAT64))) v FROM {DEAL} d
            WHERE {SCOPED} AND d.property_hs_is_closed_won=TRUE
              AND DATE(d.property_closedate) >= '{start}'""").iloc[0]["v"] or 0)
    return df, actual_close, sum(VELOCITY_BENCHMARK.values())


# ---- stage-to-stage conversion (slide 10) -------------------------------------------------
def stage_conversion(today: dt.date, grain: str = "Quarterly") -> pd.DataFrame:
    """Conversion % vs benchmark for the key transitions.

    SQL-H → SAL is computed directly from funnel counts in the window (held → sal). Deeper
    transitions use the current open-pipeline stage distribution + in-window wins as a proxy
    (full stage-cohort history needs date_entered backfill — flagged for iteration).
    """
    start, end = _grain_window(today, grain)
    s, e = start.isoformat(), end.isoformat()
    f = _q(f"""
      WITH d AS (SELECT DATE(d.property_discovery_call_date) disco_d, d.property_meeting_happened_ meeting,
                        DATE(d.property_sal_date) sal_d FROM {DEAL} d WHERE {SCOPED})
      SELECT COUNTIF(disco_d BETWEEN '{s}' AND '{e}' AND LOWER(meeting)='yes') held,
             COUNTIF(sal_d BETWEEN '{s}' AND '{e}') sal FROM d""").iloc[0]
    held_to_sal = (100 * f["sal"] / f["held"]) if f["held"] else None
    # Current open-pipeline stage counts (proxy for downstream conversion shape).
    stg = _q(f"""SELECT d.deal_pipeline_stage_id sid, COUNT(*) n FROM {DEAL} d
                 WHERE {SCOPED} AND d.property_hs_is_closed=FALSE
                   AND d.deal_pipeline_stage_id IN {OPEN_STAGE_IDS} GROUP BY sid""")
    cnt = {r.sid: r.n for r in stg.itertuples()}
    sal_n = cnt.get("9e0025bf-6ac8-4ea3-8be0-72670975ba17", 0)
    roi_n = cnt.get("cef195c9-8378-451e-bad0-2ed3826dbf30", 0)
    neg_n = cnt.get("826cdb91-de03-4bee-a009-1f9aeb058d10", 0)
    won = _q(f"""SELECT COUNT(*) n FROM {DEAL} d WHERE {SCOPED} AND d.property_hs_is_closed_won=TRUE
                 AND DATE(d.property_closedate) BETWEEN '{s}' AND '{e}'""").iloc[0]["n"]

    def rate(a, b):
        return (100 * a / b) if b else None
    rows = [
        ("SQL-H → SAL", held_to_sal, True),
        ("SAL → ROI", rate(roi_n + neg_n, sal_n + roi_n + neg_n), False),
        ("ROI → NEG", rate(neg_n, roi_n + neg_n), False),
        ("NEG → WIN", rate(won, neg_n + won), False),
    ]
    out = []
    for name, val, real in rows:
        bench = CONVERSION_BENCHMARK[name]
        out.append({"transition": name, "rate": None if val is None else round(val),
                    "benchmark": bench, "real": real,
                    "status": "—" if val is None else ("On pace" if val >= bench else "Below")})
    return pd.DataFrame(out), f"{start:%b %d}–{end:%b %d}"


# ---- performance by product (slide 11) ----------------------------------------------------
PRODUCT_COLS = ["AR", "AP", "Multi-Product"]


def product_performance(today: dt.date) -> pd.DataFrame:
    """SQL-Held, SAL, SAL-pipeline ARR, Bookings ACV by OVERLAPPING product membership.

    AR = use case contains 'AR'; AP = contains 'AP'; Multi-Product = AR+AP, AR+Expense,
    AR+Global Payroll, or AP+Global Payroll. A deal can count in more than one column.
    """
    qs = quarter_start_of(today)
    s, e = qs.isoformat(), today.isoformat()
    uc = "UPPER(IFNULL(d.property_use_case,''))"
    has_ar = f"({uc} LIKE '%AR%')"
    has_ap = f"({uc} LIKE '%AP%')"
    has_exp = f"({uc} LIKE '%EXPENSE%')"
    has_gp = f"({uc} LIKE '%GLOBAL PAYROLL%')"
    multi = (f"(({has_ar} AND {has_ap}) OR ({has_ar} AND {has_exp}) OR "
             f"({has_ar} AND {has_gp}) OR ({has_ap} AND {has_gp}))")
    sql = f"""
    WITH d AS (
      SELECT {has_ar} is_ar, {has_ap} is_ap, {multi} is_multi,
             SAFE_CAST(d.property_arr AS FLOAT64) arr,
             SAFE_CAST(d.property_total_ar_ap_acv AS FLOAT64) acv,
             DATE(d.property_discovery_call_date) disco_d, d.property_meeting_happened_ meeting,
             DATE(d.property_sal_date) sal_d, DATE(d.property_closedate) close_d,
             d.property_hs_is_closed_won won
      FROM {DEAL} d WHERE {BASE_FILTER})
    SELECT label,
      COUNTIF(disco_d BETWEEN '{s}' AND '{e}' AND LOWER(meeting)='yes') sql_held,
      COUNTIF(sal_d BETWEEN '{s}' AND '{e}')                           sal,
      ROUND(SUM(IF(sal_d BETWEEN '{s}' AND '{e}', arr, 0)))            pipeline_arr,
      ROUND(SUM(IF(won AND close_d BETWEEN '{s}' AND '{e}', acv, 0)))  bookings_acv,
      COUNTIF(won AND close_d BETWEEN '{s}' AND '{e}')                 wins
    FROM d, UNNEST(['AR','AP','Multi-Product']) label
    WHERE (label='AR' AND is_ar) OR (label='AP' AND is_ap) OR (label='Multi-Product' AND is_multi)
    GROUP BY label"""
    df = _q(sql).set_index("label").reindex(PRODUCT_COLS).fillna(0)
    df["avg_acv"] = (df["bookings_acv"] / df["wins"].replace(0, pd.NA)).fillna(0)
    return df
