"""Inputable goals store.

Goals change quarter-over-quarter and must be editable in the app (the old Google-Sheet
sync is broken and we don't want to depend on it). v0 persists to a local JSON file;
when we deploy, swap _load/_save for a BigQuery table (paystand.reporting_bizreview.goals)
without touching the rest of the app.

Goal grain: quarter x market x metric. Metrics: sql_booked, sql_held, sal, pipeline_arr, bookings_arr.
"""
import json, os

_STORE = os.path.join(os.path.dirname(__file__), "goals_store.json")
METRICS = ["sql_booked", "sql_held", "sal", "pipeline_arr", "bookings_arr", "bookings_acv"]


def _load() -> dict:
    if os.path.exists(_STORE):
        with open(_STORE) as f:
            return json.load(f)
    return {}


def _save(data: dict):
    with open(_STORE, "w") as f:
        json.dump(data, f, indent=2)


def get_goals(quarter: str) -> dict:
    """Return {market: {metric: value}} for a quarter key like '2026Q2'."""
    return _load().get(quarter, {})


def set_goal(quarter: str, market: str, metric: str, value: float):
    data = _load()
    data.setdefault(quarter, {}).setdefault(market, {})[metric] = value
    _save(data)


def set_quarter_goals(quarter: str, goals: dict):
    """goals = {market: {metric: value}}."""
    data = _load()
    data[quarter] = goals
    _save(data)


def goal_for(quarter: str, market: str, metric: str):
    return _load().get(quarter, {}).get(market, {}).get(metric)
