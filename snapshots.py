"""Weekly snapshot store — freezes the week's key metrics so we can show Forecast
Movement (Booking #2), Trends (TOF #7), and true Week-over-Week.

Time-sensitive: movement/trends only populate once 2+ weekly snapshots exist, so we start
capturing now. Grain: snapshot_date (ISO) -> {metric: value}. v0 = local JSON; swap for a
BigQuery table (paystand.reporting_bizreview.weekly_snapshot) on deploy.
"""
import json
import os
import datetime as dt

_STORE = os.path.join(os.path.dirname(__file__), "snapshots_store.json")


def _load() -> dict:
    if os.path.exists(_STORE):
        with open(_STORE) as f:
            return json.load(f)
    return {}


def _save(data: dict):
    with open(_STORE, "w") as f:
        json.dump(data, f, indent=2)


def capture(snapshot_date: dt.date, metrics: dict):
    """Merge this week's metric values into the snapshot for the date (idempotent)."""
    data = _load()
    entry = data.get(snapshot_date.isoformat(), {})
    entry.update({k: float(v) for k, v in metrics.items()})
    data[snapshot_date.isoformat()] = entry
    _save(data)


def backfill(snapshots_by_date: dict):
    """Merge a batch of reconstructed historical snapshots ({date_iso: metrics})."""
    data = _load()
    for d_iso, metrics in snapshots_by_date.items():
        entry = data.get(d_iso, {})
        entry.update({k: float(v) for k, v in metrics.items()})
        data[d_iso] = entry
    _save(data)


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


def history() -> list:
    """All snapshots as a sorted list of (date_iso, metrics)."""
    data = _load()
    return [(d, data[d]) for d in sorted(data)]
