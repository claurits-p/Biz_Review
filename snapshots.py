"""Weekly snapshot store — freezes the week's key metrics so we can show Forecast
Movement (Booking deck), Trends (TOF deck), and true Week-over-Week.

Persistence: BigQuery (`paystand.reporting_bizreview.weekly_snapshot`) is the source of truth so
snapshots SURVIVE Streamlit Cloud redeploys/reboots and are shared across everyone who opens the
app. If BigQuery is unavailable (e.g. local dev with no creds) it transparently falls back to a
local JSON file. Same public API either way: capture / backfill / has_dates / prior_week /
latest_before / history.

Grain: snapshot_date (DATE) -> {metric: value}. Point-in-time metrics (forecast categories) can't
be reconstructed from HubSpot, so they accrue forward from the first capture; date-driven metrics
(funnel/bookings) are reconstructed and backfilled on load.
"""
import json
import os
import datetime as dt

_STORE = os.path.join(os.path.dirname(__file__), "snapshots_store.json")
_DATASET = "paystand.reporting_bizreview"
_TABLE = "paystand.reporting_bizreview.weekly_snapshot"

# In-process cache of {date_iso: {metric: value}} so reads don't re-hit BigQuery on every rerun.
_cache = None
_table_ready = False


# ----------------------------------------------------------------------------- local JSON fallback
def _load_json() -> dict:
    if os.path.exists(_STORE):
        with open(_STORE) as f:
            return json.load(f)
    return {}


def _save_json(data: dict):
    with open(_STORE, "w") as f:
        json.dump(data, f, indent=2)


# ----------------------------------------------------------------------------------- BigQuery layer
def _bq():
    """Return a BigQuery client, or None if unavailable (→ JSON fallback)."""
    try:
        import data
        return data._client()
    except Exception:
        return None


def _ensure_table(c) -> bool:
    global _table_ready
    if _table_ready:
        return True
    try:
        from google.cloud import bigquery
        ds = bigquery.Dataset(_DATASET)
        ds.location = "US"
        c.create_dataset(ds, exists_ok=True)
        schema = [
            bigquery.SchemaField("snapshot_date", "DATE"),
            bigquery.SchemaField("metric", "STRING"),
            bigquery.SchemaField("value", "FLOAT64"),
            bigquery.SchemaField("updated_at", "TIMESTAMP"),
        ]
        c.create_table(bigquery.Table(_TABLE, schema=schema), exists_ok=True)
        _table_ready = True
        return True
    except Exception:
        return False


def _load() -> dict:
    """All snapshots as {date_iso: {metric: value}}, cached in-process."""
    global _cache
    if _cache is not None:
        return _cache
    c = _bq()
    if c is None or not _ensure_table(c):
        _cache = _load_json()
        return _cache
    try:
        rows = c.query(
            f"SELECT FORMAT_DATE('%Y-%m-%d', snapshot_date) d, metric, value FROM `{_TABLE}`"
        ).result()
        data: dict = {}
        for r in rows:
            data.setdefault(r.d, {})[r.metric] = r.value
        _cache = data
        return data
    except Exception:
        _cache = _load_json()
        return _cache


def _merge_bq(c, snapshot_date: dt.date, metrics: dict) -> bool:
    try:
        from google.cloud import bigquery
        sql = f"""
        MERGE `{_TABLE}` T
        USING (
          SELECT @date AS snapshot_date, m AS metric, v AS value
          FROM UNNEST(@metrics) m WITH OFFSET o
          JOIN UNNEST(@values) v WITH OFFSET p ON o = p
        ) S
        ON T.snapshot_date = S.snapshot_date AND T.metric = S.metric
        WHEN MATCHED THEN UPDATE SET value = S.value, updated_at = CURRENT_TIMESTAMP()
        WHEN NOT MATCHED THEN INSERT (snapshot_date, metric, value, updated_at)
        VALUES (S.snapshot_date, S.metric, S.value, CURRENT_TIMESTAMP())"""
        keys = list(metrics.keys())
        vals = [float(metrics[k]) for k in keys]
        cfg = bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("date", "DATE", snapshot_date.isoformat()),
            bigquery.ArrayQueryParameter("metrics", "STRING", keys),
            bigquery.ArrayQueryParameter("values", "FLOAT64", vals),
        ])
        c.query(sql, job_config=cfg).result()
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------------------- public API
def capture(snapshot_date: dt.date, metrics: dict):
    """Merge this week's metric values into the snapshot for the date (idempotent).

    Skips the write when the stored values already match (so repeat loads on the same day are
    cheap). Persists to BigQuery; falls back to local JSON if BigQuery is unavailable.
    """
    global _cache
    metrics = {k: float(v) for k, v in metrics.items()}
    cur = _load().get(snapshot_date.isoformat(), {})
    if all(cur.get(k) == v for k, v in metrics.items()):
        return  # nothing changed — avoid a redundant write
    c = _bq()
    if c is not None and _ensure_table(c) and _merge_bq(c, snapshot_date, metrics):
        # keep the in-process cache in sync instead of re-querying
        if _cache is not None:
            _cache.setdefault(snapshot_date.isoformat(), {}).update(metrics)
        return
    # JSON fallback
    data = _load_json()
    entry = data.get(snapshot_date.isoformat(), {})
    entry.update(metrics)
    data[snapshot_date.isoformat()] = entry
    _save_json(data)
    _cache = None


def backfill(snapshots_by_date: dict):
    """Merge a batch of reconstructed historical snapshots ({date_iso: metrics})."""
    for d_iso, metrics in snapshots_by_date.items():
        capture(dt.date.fromisoformat(d_iso), metrics)


def has_dates(date_isos) -> bool:
    """True if every given ISO date already exists in the store (skip redundant backfill)."""
    data = _load()
    return all(d in data for d in date_isos)


def latest_before(snapshot_date: dt.date):
    """Return (date, metrics) of the most recent snapshot strictly before snapshot_date."""
    data = _load()
    prior = sorted(d for d in data if d < snapshot_date.isoformat())
    if not prior:
        return None, None
    return prior[-1], data[prior[-1]]


def prior_week(snapshot_date: dt.date, target_gap_days: int = 7):
    """Return (date, metrics) of the snapshot closest to ~target_gap_days before snapshot_date.

    This is the correct basis for Week-over-Week: it deliberately skips snapshots captured
    a day or two ago (the app writes one on every load) and instead picks the one nearest to
    'a week ago', so WoW reflects a real week of movement — not day-over-day noise (≈0%)."""
    data = _load()
    cands = [d for d in data if d < snapshot_date.isoformat()]
    if not cands:
        return None, None
    target = snapshot_date - dt.timedelta(days=target_gap_days)
    best = min(cands, key=lambda d: abs((dt.date.fromisoformat(d) - target).days))
    return best, data[best]


def history() -> list:
    """All snapshots as a sorted list of (date_iso, metrics)."""
    data = _load()
    return [(d, data[d]) for d in sorted(data)]
