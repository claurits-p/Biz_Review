"""Canonical metric definitions for the Paystand Business Review app.

These are derived from the company's own reporting logic (report_goals_pacing view,
reporting_sales_forecasting.deals view) and the two boss agent specs. They are kept in
one place so the deck, dashboard, and agent all speak the same language.

NOTE (assumptions to confirm with RevOps):
- SQL-Booked  = deal entered the Sales Pipeline (createdate) in the period
- SQL-Held    = SQL where the discovery/first meeting happened (meeting_happened_)
- SAL         = deal marked SAL (sal_ = 'Yes' / sal_date in period)
- Pipeline $  = ARR of deals created in period (Sales Pipeline)
- Bookings $  = ARR of Closed-Won deals (closedate in period)
"""

PIPELINE_LABEL = "Sales Pipeline"

# ERP ecosystem mapping (off company/deal accounting_erp_software). Mirrors report_goals_pacing.
ERP_CASE_SQL = """
  CASE
    WHEN d.property_accounting_erp_software = 'Netsuite'                THEN 'NetSuite'
    WHEN d.property_accounting_erp_software = 'Sage Intacct'            THEN 'Sage'
    WHEN d.property_accounting_erp_software LIKE '%Microsoft Dynamics%' THEN 'Dynamics'
    WHEN d.property_accounting_erp_software = 'Acumatica'               THEN 'Acumatica'
    ELSE 'Broad Market'
  END
"""

# V2 framework: NetSuite / Sage / Dynamics are the strategic markets; everything else is "Other".
PRIMARY_MARKETS = ["NetSuite", "Sage", "Dynamics"]
OTHER_MARKETS = ["Acumatica", "Broad Market"]
MARKET_ORDER = PRIMARY_MARKETS + ["Other"]

def market_bucket(erp) -> str:
    s = "" if erp is None else str(erp)
    return s if s in PRIMARY_MARKETS else "Other"

# GTM engines (off sql_generated_by). V2 cares about Channel, Marketing, BDR, AE.
GTM_ENGINES = ["Channels", "Marketing", "BDR", "AE", "Other"]
GTM_EXCLUDE = ["CS"]  # customer success is not a new-business acquisition engine

def gtm_bucket(src) -> str:
    s = ("" if src is None else str(src)).lower()
    if s in ("nan", "none", "<na>", "nat"):
        return "Other"
    if any(k in s for k in ("channel", "partner", "var", "referral")):
        return "Channels"
    if any(k in s for k in ("marketing", "inbound", "demand", "web", "event", "content")):
        return "Marketing"
    if any(k in s for k in ("bdr", "sdr", "outbound", "prospect")):
        return "BDR"
    if any(k in s for k in ("ae", "account exec", "sales", "self", "direct")):
        return "AE"
    return "Other"

# Product (off use_case, order-independent). AR / AP / Multi-Product.
def product_bucket(use_case) -> str:
    # Robust to None / float NaN / pandas NA / Arrow-backed strings (Streamlit Cloud).
    s = "" if use_case is None else str(use_case)
    if not s or s.strip().lower() in ("nan", "none", "<na>", "nat"):
        return "Unknown"
    parts = {p.strip().upper() for p in s.replace(";", ",").split(",") if p.strip()}
    has_ar = "AR" in parts
    has_ap = "AP" in parts or "EXPENSE MANAGEMENT" in parts or "CORPORATE CARDS" in parts
    if has_ar and has_ap:
        return "Multi-Product"
    if has_ar:
        return "AR"
    if has_ap:
        return "AP"
    return "Other"

# Forecast categories — exactly as the boss's pipeline_forecast_analysis_agent spec.
FORECAST_CATEGORIES = ["Commit", "Best Case", "Pipeline", "Not Forecasted"]
FORECAST_CATEGORY_FIELD = "property_hs_manual_forecast_category"
FORECAST_CATEGORY_MAP = {
    "COMMIT": "Commit", "BEST_CASE": "Best Case",
    "PIPELINE": "Pipeline", "OMIT": "Not Forecasted", None: "Not Forecasted",
}

# Strategic priorities reviewed every week (from both V2 frameworks).
STRATEGIC_PRIORITIES = [
    "NetSuite install base",
    "Sage scaling",
    "Dynamics improving",
    "Outbound recovering",
    "AP repeatable engine",
    "Global FX motion",
]

# Plain-language definitions surfaced as ⓘ tooltips and a "How to read this" panel,
# so anyone can present the deck without memorizing the metric logic.
GLOSSARY = {
    "QTD": "Quarter-to-Date: counts/dollars accumulated from the first day of the quarter "
           "through the snapshot date.",
    "Quarter elapsed": "How far through the quarter we are by calendar days "
                       "(days into quarter ÷ total days). It's the 'pace' line: a metric at "
                       "X% of plan is exactly on pace when X% of the quarter has elapsed.",
    "Pace": "The % of the quarter that has elapsed by calendar days. We compare attainment "
            "against pace — above pace = ahead of schedule, below = behind.",
    "SQL-Booked": "Sales-Qualified Leads booked: deals that entered the Sales Pipeline "
                  "(HubSpot createdate) during the period. Measures pipeline creation.",
    "SQL-Held": "SQL-Booked deals where the discovery/first meeting actually happened "
                "(discovery_call_date in period AND meeting_happened = Yes).",
    "SAL": "Sales-Accepted Leads: deals AEs accepted as real opportunities (sal_date in period).",
    "Pipeline ARR": "Annual Recurring Revenue of deals created in the period (new pipeline $).",
    "Pipeline ACV": "Annual Contract Value (AR + AP) of deals created in the period.",
    "Bookings ARR": "ARR of deals Closed-Won in the period — revenue actually booked.",
    "Bookings ACV": "Annual Contract Value (AR + AP) of deals Closed-Won in the period.",
    "ARR vs ACV": "ARR = recurring software revenue. ACV = total annual contract value "
                  "including AR and AP. ACV ≥ ARR for the same deal.",
    "WoW": "Week-over-Week change vs the prior weekly snapshot. Shows momentum, not just level.",
    "Commit": "Deals the rep is confident will close this quarter (HubSpot forecast category = Commit). "
              "Treated as the high-confidence number.",
    "Best Case": "Upside deals that could close with effort (HubSpot forecast category = Best Case).",
    "Pipeline coverage": "Open pipeline ARR ÷ remaining gap to plan. Benchmark ≥ 3× — i.e. you "
                         "typically need 3x the gap in open pipeline to cover normal loss rates.",
    "Weighted": "Σ (deal ACV × HubSpot deal-stage probability). A probability-adjusted forecast "
                "using HubSpot's own stage odds — not invented weights.",
    "Forecast discipline": "Share of open deals that carry a rep forecast category. Low = reps "
                           "aren't calling their deals, so the forecast is less trustworthy.",
    "RAG": "Red / Amber / Green health rating from risk flags + MEDDPICC completeness + pace.",
    "MEDDPICC": "Deal-qualification score (Metrics, Economic buyer, Decision criteria/process, "
                "Paper process, Identified pain, Champion, Competition). Shown out of 5 trackable "
                "elements (Champion field is empty in CRM).",
}


def help_for(*keys) -> str:
    """Compose a tooltip string from one or more glossary keys."""
    return "  •  ".join(GLOSSARY[k] for k in keys if k in GLOSSARY)


def fmt_triple(current, wow_pct, attainment_pct, money=False):
    """Consistent metric language: '78 (+12%, 80%)' or '$450K (+8%, 72%)'.
    Components that aren't available yet (no prior snapshot / no goal set) are shown as '–',
    and if NEITHER is available we just show the value (no empty parentheses)."""
    if money:
        cur = f"${current/1e6:.2f}M" if abs(current) >= 1e6 else f"${current/1e3:.0f}K"
    else:
        cur = f"{current:,.0f}"
    if wow_pct is None and attainment_pct is None:
        return cur
    wow = f"{wow_pct:+.0f}%" if wow_pct is not None else "–"
    att = f"{attainment_pct:.0f}%" if attainment_pct is not None else "–"
    return f"{cur} ({wow}, {att})"
