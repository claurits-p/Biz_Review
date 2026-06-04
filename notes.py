"""Per-slide qualitative notes ("Talking points / commentary") store.

This is the qualitative layer of the deck — typed context that shows on each slide and
persists per quarter + meeting + slide. v0 = local JSON; move to BigQuery on deploy.
"""
import json
import os

_STORE = os.path.join(os.path.dirname(__file__), "notes_store.json")


def _load() -> dict:
    if os.path.exists(_STORE):
        with open(_STORE) as f:
            return json.load(f)
    return {}


def _save(data: dict):
    with open(_STORE, "w") as f:
        json.dump(data, f, indent=2)


def _key(qkey: str, meeting: str, slide_id: str) -> str:
    return f"{qkey}|{meeting}|{slide_id}"


def get(qkey: str, meeting: str, slide_id: str) -> str:
    return _load().get(_key(qkey, meeting, slide_id), "")


def set_note(qkey: str, meeting: str, slide_id: str, text: str):
    data = _load()
    data[_key(qkey, meeting, slide_id)] = text
    _save(data)
