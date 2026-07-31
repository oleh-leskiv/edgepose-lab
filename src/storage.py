"""Durable shared state in Firestore.

Holds saved Filter Pool selections plus per-candidate interview-pool flags,
notes, a scheduled-call marker, and a manually-entered recording URL. Keyed by
candidate email (stable across spreadsheet re-exports).

Per-candidate entry shape:
    {
      "pool":      {"sofiia": bool, "oleh": bool, "yurii": bool},
      "notes":     {"sofiia": str,  "oleh": str,  "yurii": str},
      "scheduled": bool,   # Oleh marks candidates with a scheduled call
      "recording": str,    # manually pasted link to the call recording
    }

Firestore layout:
    pool_state/filters      -> {"filters": {field: [values]}}
    pool_state/candidates   -> {"<email>": <entry>}
"""

from __future__ import annotations

import json

import streamlit as st

EMPTY_STATE: dict = {"filters": {}, "candidates": {}}

_COLLECTION = "pool_state"
_FILTERS_DOC = "filters"
_CANDIDATES_DOC = "candidates"


@st.cache_resource(show_spinner=False)
def _client():
    import firebase_admin
    from firebase_admin import credentials, firestore

    if not firebase_admin._apps:
        service_account = dict(st.secrets["firebase"])
        if "private_key" in service_account:
            service_account["private_key"] = service_account["private_key"].replace("\\n", "\n")
        firebase_admin.initialize_app(credentials.Certificate(service_account))
    return firestore.client()


def load_state() -> dict:
    try:
        db = _client()
        filters_snap = db.collection(_COLLECTION).document(_FILTERS_DOC).get()
        candidates_snap = db.collection(_COLLECTION).document(_CANDIDATES_DOC).get()
        filters = (filters_snap.to_dict() or {}).get("filters", {}) if filters_snap.exists else {}
        candidates = (candidates_snap.to_dict() or {}) if candidates_snap.exists else {}
        return {"filters": filters, "candidates": candidates}
    except Exception as exc:  # noqa: BLE001
        st.error(f"Could not read shared state from Firestore: {exc}")
        return {"filters": {}, "candidates": {}}


def save_filters(filters: dict[str, list[str]]) -> dict:
    db = _client()
    db.collection(_COLLECTION).document(_FILTERS_DOC).set({"filters": filters})
    return load_state()


def _update_candidate(email: str, mutate) -> dict:
    """Read one candidate's entry, apply mutate(entry), write it back (merge).

    Per-candidate merge writes keep two people editing different candidates from
    clobbering each other.
    """
    db = _client()
    doc_ref = db.collection(_COLLECTION).document(_CANDIDATES_DOC)
    snapshot = doc_ref.get()
    current = (snapshot.to_dict() or {}) if snapshot.exists else {}
    entry = current.get(email) or {"pool": {}, "notes": {}}
    entry.setdefault("pool", {})
    entry.setdefault("notes", {})
    mutate(entry)
    doc_ref.set({email: entry}, merge=True)
    return load_state()


def set_pool_flag(email: str, interviewer_key: str, value: bool) -> dict:
    return _update_candidate(email, lambda e: e["pool"].__setitem__(interviewer_key, value))


def set_note(email: str, interviewer_key: str, text: str) -> dict:
    return _update_candidate(email, lambda e: e["notes"].__setitem__(interviewer_key, text))


def set_scheduled_call(email: str, value: bool) -> dict:
    """Mark (or unmark) that a call has been scheduled with this candidate."""
    return _update_candidate(email, lambda e: e.__setitem__("scheduled", value))


def set_recording_url(email: str, url: str) -> dict:
    """Store a manually-entered link to the interview recording."""
    return _update_candidate(email, lambda e: e.__setitem__("recording", url.strip()))


def import_from_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        state = json.load(handle)
    db = _client()
    db.collection(_COLLECTION).document(_FILTERS_DOC).set({"filters": state.get("filters", {})})
    db.collection(_COLLECTION).document(_CANDIDATES_DOC).set(state.get("candidates", {}))
    return state
