"""Actions & Decisions log — the closing section of both reviews per the V2 spec
('spend less time reporting, more time deciding'). Inputable + persisted.

Grain: quarter x meeting ('TOF' | 'Booking'). Each item: text, owner, status, date.
v0 persists to local JSON; swap _load/_save for BigQuery on deploy.
"""
import json
import os
import datetime as dt

_STORE = os.path.join(os.path.dirname(__file__), "actions_store.json")
STATUSES = ["Open", "In progress", "Done", "Blocked"]


def _load() -> dict:
    if os.path.exists(_STORE):
        with open(_STORE) as f:
            return json.load(f)
    return {}


def _save(data: dict):
    with open(_STORE, "w") as f:
        json.dump(data, f, indent=2)


def _key(quarter: str, meeting: str) -> str:
    return f"{quarter}|{meeting}"


def list_items(quarter: str, meeting: str) -> list:
    return _load().get(_key(quarter, meeting), [])


def add_item(quarter: str, meeting: str, text: str, owner: str = "", status: str = "Open"):
    data = _load()
    items = data.setdefault(_key(quarter, meeting), [])
    items.append({"text": text, "owner": owner, "status": status,
                  "added": dt.date.today().isoformat()})
    _save(data)


def update_items(quarter: str, meeting: str, items: list):
    data = _load()
    data[_key(quarter, meeting)] = items
    _save(data)
