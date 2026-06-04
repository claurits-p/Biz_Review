"""BigQuery data layer for the Business Review app.

Built on the LIVE bronze table (paystand.bronze_paystand_hubspot.deal) because the
existing reporting_sales_forecasting / goals views are broken (they still point at the
dropped hubspot_v2.deals). All dates are dynamic off the snapshot date.
"""
from __future__ import annotations
import os, functools, datetime as dt
import pandas as pd
from google.cloud import bigquery

os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS",
                      "/Users/claurits/Cursor/paystand-ba3fd4a26cf4.json")
from definitions import ERP_CASE_SQL, market_bucket  # noqa: E402

DEAL = "`paystand.bronze_paystand_hubspot.deal`"
SALES_PIPELINE_ID = "default"  # the HubSpot "Sales Pipeline"

# Hygiene (defaults; confirm with RevOps). Mirrors the company's own forecasting view
# (test-deal exclusions) + the Paystand-core (non-Teampay) scope from the idle analysis.
TEST_DEALS = ("Cloudera - New Deal", "TRX -  New Deal (Test)",
              "Analytix - New Deal", "NetDocuments - New Deal (Test)")
BASE_FILTER = f"""
  d.deal_pipeline_id = '{SALES_PIPELINE_ID}'
  AND d.property_dealname NOT IN {TEST_DEALS}
  AND LOWER(IFNULL(d.property_hs_object_source_detail_1,'')) NOT LIKE '%teampay%'
  AND LOWER(IFNULL(d.property_hs_object_source_detail_1,'')) NOT LIKE '%tp data%'
  -- Machine-generated sources (INTEGRATION = sync dump, IMPORT = bulk migrations/renewals,
  -- ~2.4k phantom 'wins' / $16M phantom ACV) are excluded — EXCEPT records that have a real
  -- discovery call booked. That single carve-out keeps legitimately-worked import deals like the
  -- "Sage Future" event batch (which have discovery calls) while still dropping the renewal/
  -- migration junk (only 1 of 2,392 renewals has a discovery call). Verified 2026-06.
  AND (
        IFNULL(d.property_hs_object_source_label,'') NOT IN ('INTEGRATION','IMPORT')
        OR d.property_discovery_call_date IS NOT NULL
      )
"""


@functools.lru_cache(maxsize=1)
def _client():
    """BigQuery client. On Streamlit Cloud, credentials come from st.secrets
    (`[gcp_service_account]`); locally they fall back to GOOGLE_APPLICATION_CREDENTIALS / ADC."""
    try:
        import streamlit as st
        from google.oauth2 import service_account
        if "gcp_service_account" in st.secrets:
            info = dict(st.secrets["gcp_service_account"])
            creds = service_account.Credentials.from_service_account_info(info)
            return bigquery.Client(project=info.get("project_id", "paystand"), credentials=creds)
    except Exception:
        pass
    return bigquery.Client(project="paystand")


def _q(sql: str) -> pd.DataFrame:
    return _client().query(sql).result().to_dataframe()


def pacing_dates(today: dt.date | None = None) -> dict:
    """Dynamic quarter pacing — mirrors the company's report_goals_pacing logic."""
    today = today or dt.date.today()
    q = (today.month - 1) // 3
    q_start = dt.date(today.year, q * 3 + 1, 1)
    q_end_next = dt.date(today.year + (q == 3), ((q + 1) % 4) * 3 + 1, 1)
    days_in = (q_end_next - q_start).days
    into = (today - q_start).days
    return {
        "quarter_key": f"{today.year}Q{q+1}",
        "quarter_start": q_start,
        "quarter_label": f"Q{q+1} {today.year}",
        "today": today,
        "days_in_quarter": days_in,
        "days_into_quarter": into,
        "days_remaining": days_in - into,
        "pct_elapsed": round(100 * into / days_in, 1),
    }


def tof_funnel(quarter_start: dt.date, today: dt.date) -> pd.DataFrame:
    """QTD funnel by strategic market: SQL-Booked, SQL-Held, SAL, Pipeline $, Bookings $."""
    qs, td = quarter_start.isoformat(), today.isoformat()
    sql = f"""
    WITH d AS (
      SELECT
        {ERP_CASE_SQL} AS erp,
        SAFE_CAST(d.property_arr AS FLOAT64) AS arr,
        SAFE_CAST(d.property_total_ar_ap_acv AS FLOAT64) AS acv,   -- aggregated AR+AP ACV
        DATE(d.property_createdate)        AS create_d,            -- SQL-Booked
        DATE(d.property_discovery_call_date) AS disco_d,           -- SQL-Held (date)
        d.property_meeting_happened_       AS meeting_happened,    -- SQL-Held (happened?)
        DATE(d.property_sal_date)          AS sal_d,               -- SAL
        DATE(d.property_closedate)         AS close_d,
        d.property_hs_is_closed_won AS won
      FROM {DEAL} d
      WHERE {BASE_FILTER}
    )
    SELECT erp,
      COUNTIF(create_d BETWEEN '{qs}' AND '{td}')                          AS sql_booked,
      COUNTIF(disco_d  BETWEEN '{qs}' AND '{td}' AND LOWER(meeting_happened)='yes') AS sql_held,
      COUNTIF(sal_d    BETWEEN '{qs}' AND '{td}')                          AS sal,
      ROUND(SUM(IF(create_d BETWEEN '{qs}' AND '{td}', arr, 0)))           AS pipeline_arr,
      ROUND(SUM(IF(create_d BETWEEN '{qs}' AND '{td}', acv, 0)))           AS pipeline_acv,
      ROUND(SUM(IF(won AND close_d BETWEEN '{qs}' AND '{td}', arr, 0)))    AS bookings_arr,
      ROUND(SUM(IF(won AND close_d BETWEEN '{qs}' AND '{td}', acv, 0)))    AS bookings_acv
    FROM d GROUP BY erp
    """
    df = _q(sql)
    df["market"] = df["erp"].map(market_bucket)
    metrics = ["sql_booked", "sql_held", "sal", "pipeline_arr", "pipeline_acv",
               "bookings_arr", "bookings_acv"]
    return df.groupby("market", as_index=False)[metrics].sum()


def qtd_base(quarter_start: dt.date, today: dt.date) -> pd.DataFrame:
    """Deal-level rows touching the quarter — powers GTM/Product/Market funnel breakdowns
    in pandas from a single query (one source of truth, fast)."""
    qs, td = quarter_start.isoformat(), today.isoformat()
    sql = f"""
    SELECT
      {ERP_CASE_SQL} AS erp,
      d.property_sql_generated_by AS gtm_raw,
      d.property_use_case          AS use_case,
      SAFE_CAST(d.property_arr AS FLOAT64) AS arr,
      SAFE_CAST(d.property_total_ar_ap_acv AS FLOAT64) AS acv,
      DATE(d.property_createdate)          AS create_d,
      DATE(d.property_discovery_call_date) AS disco_d,
      d.property_meeting_happened_         AS meeting_happened,
      DATE(d.property_sal_date)            AS sal_d,
      DATE(d.property_closedate)           AS close_d,
      d.property_hs_is_closed_won AS won
    FROM {DEAL} d
    WHERE {BASE_FILTER}
      AND (DATE(d.property_createdate)          BETWEEN '{qs}' AND '{td}'
        OR DATE(d.property_discovery_call_date) BETWEEN '{qs}' AND '{td}'
        OR DATE(d.property_sal_date)            BETWEEN '{qs}' AND '{td}'
        OR (d.property_hs_is_closed_won AND DATE(d.property_closedate) BETWEEN '{qs}' AND '{td}'))
    """
    return _q(sql)


def wow_sql(today: dt.date) -> pd.DataFrame:
    """This-week vs last-week SQL-Booked by market (for the WoW component)."""
    this_start = today - dt.timedelta(days=today.weekday())
    last_start = this_start - dt.timedelta(days=7)
    sql = f"""
    WITH d AS (
      SELECT {ERP_CASE_SQL} AS erp, DATE(d.property_createdate) AS cd
      FROM {DEAL} d WHERE {BASE_FILTER}
    )
    SELECT erp,
      COUNTIF(cd BETWEEN '{this_start.isoformat()}' AND '{today.isoformat()}') AS this_week,
      COUNTIF(cd BETWEEN '{last_start.isoformat()}' AND '{(this_start - dt.timedelta(days=1)).isoformat()}') AS last_week
    FROM d GROUP BY erp
    """
    df = _q(sql)
    df["market"] = df["erp"].map(market_bucket)
    return df.groupby("market", as_index=False)[["this_week", "last_week"]].sum()


def wow_funnel(today: dt.date) -> pd.DataFrame:
    """This-week vs last-week FLOW counts (not cumulative) for every funnel event, by market.

    This is the dedicated weekly view Will asked for: 'how many SQLs happened THIS week vs
    LAST week' — actual period counts, so the room sees the underlying numbers instead of a
    bare WoW %. Weeks are Mon–Sun; the current week runs Mon→today.
    """
    this_start = today - dt.timedelta(days=today.weekday())
    last_start = this_start - dt.timedelta(days=7)
    last_end = this_start - dt.timedelta(days=1)
    tw0, tw1 = this_start.isoformat(), today.isoformat()
    lw0, lw1 = last_start.isoformat(), last_end.isoformat()
    sql = f"""
    WITH d AS (
      SELECT
        {ERP_CASE_SQL} AS erp,
        DATE(d.property_createdate)          AS create_d,
        DATE(d.property_discovery_call_date) AS disco_d,
        d.property_meeting_happened_         AS meeting_happened,
        DATE(d.property_sal_date)            AS sal_d,
        DATE(d.property_closedate)           AS close_d,
        d.property_hs_is_closed_won          AS won,
        SAFE_CAST(d.property_arr AS FLOAT64) AS arr
      FROM {DEAL} d WHERE {BASE_FILTER}
    )
    SELECT erp,
      COUNTIF(create_d BETWEEN '{tw0}' AND '{tw1}')                                  AS sql_booked_tw,
      COUNTIF(create_d BETWEEN '{lw0}' AND '{lw1}')                                  AS sql_booked_lw,
      COUNTIF(disco_d  BETWEEN '{tw0}' AND '{tw1}' AND LOWER(meeting_happened)='yes') AS sql_held_tw,
      COUNTIF(disco_d  BETWEEN '{lw0}' AND '{lw1}' AND LOWER(meeting_happened)='yes') AS sql_held_lw,
      COUNTIF(sal_d    BETWEEN '{tw0}' AND '{tw1}')                                  AS sal_tw,
      COUNTIF(sal_d    BETWEEN '{lw0}' AND '{lw1}')                                  AS sal_lw,
      ROUND(SUM(IF(won AND close_d BETWEEN '{tw0}' AND '{tw1}', arr, 0)))            AS bookings_arr_tw,
      ROUND(SUM(IF(won AND close_d BETWEEN '{lw0}' AND '{lw1}', arr, 0)))            AS bookings_arr_lw
    FROM d GROUP BY erp
    """
    df = _q(sql)
    df["market"] = df["erp"].map(market_bucket)
    cols = [c for c in df.columns if c not in ("erp", "market")]
    out = df.groupby("market", as_index=False)[cols].sum()
    out.attrs["this_label"] = f"{this_start:%b %d}–{today:%b %d}"
    out.attrs["last_label"] = f"{last_start:%b %d}–{last_end:%b %d}"
    return out


def pace_history(today: dt.date, lookback_quarters: int = 5) -> pd.DataFrame:
    """Daily SQL-Booked + Bookings-ARR back over several quarters, so the deck can plot
    'pace vs prior quarter' and 'pace vs trailing-4-quarter average' aligned to day-of-quarter.

    Will's note: a number like '$600K bookings' is meaningless without a reference. These
    series let us overlay how we paced last quarter (and on average) at the same day index.
    Returns one row per calendar date with sql_booked + bookings_arr flow for that day.
    """
    q = (today.month - 1) // 3
    q_start = dt.date(today.year, q * 3 + 1, 1)
    # Walk back `lookback_quarters` quarter-starts to bound the pull.
    y, qi = today.year, q
    for _ in range(lookback_quarters):
        qi -= 1
        if qi < 0:
            qi = 3
            y -= 1
    lb_start = dt.date(y, qi * 3 + 1, 1)
    lb, td = lb_start.isoformat(), today.isoformat()
    sql = f"""
    WITH d AS (
      SELECT DATE(d.property_createdate) AS create_d,
             DATE(d.property_closedate)  AS close_d,
             d.property_hs_is_closed_won AS won,
             SAFE_CAST(d.property_arr AS FLOAT64) AS arr
      FROM {DEAL} d WHERE {BASE_FILTER}
    ),
    ev AS (
      SELECT create_d AS d_date, 1 AS sql_booked, 0.0 AS bookings_arr
      FROM d WHERE create_d BETWEEN '{lb}' AND '{td}'
      UNION ALL
      SELECT close_d AS d_date, 0 AS sql_booked, IFNULL(arr, 0) AS bookings_arr
      FROM d WHERE won AND close_d BETWEEN '{lb}' AND '{td}'
    )
    SELECT d_date, SUM(sql_booked) AS sql_booked, SUM(bookings_arr) AS bookings_arr
    FROM ev GROUP BY d_date ORDER BY d_date
    """
    df = _q(sql)
    df["d_date"] = pd.to_datetime(df["d_date"])
    df.attrs["quarter_start"] = q_start
    return df


if __name__ == "__main__":
    p = pacing_dates()
    print("Pacing:", p)
    print("\nTOF funnel:\n", tof_funnel(p["quarter_start"], p["today"]).to_string(index=False))
    print("\nWoW SQL:\n", wow_sql(p["today"]).to_string(index=False))
