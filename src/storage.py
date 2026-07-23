"""Durable shared state in Firestore, replacing the previous local ``pool_state.json``.

Holds saved Filter Pool selections plus per-candidate interview-pool flags/notes, keyed by
candidate email (stable across spreadsheet re-exports). Moving this off the local filesystem is
what makes the app deployable to a cloud host: Streamlit Community Cloud has an ephemeral disk,
so a JSON file there would be wiped on every redeploy, taking the team's hiring notes with it.

Per-user identity is still deliberately NOT persisted here or anywhere else on the server: it's
read fresh from each browser session (see app.py's sidebar). On a shared server one user's
"remembered" email would otherwise leak into everyone else's session.

Firestore layout
----------------
``pool_state/filters``      -> {"filters": {field: [values]}}
``pool_state/candidates``   -> {"<email>": {"pool": {...}, "notes": {...}}}

Both are single documents rather than a collection of per-candidate documents. With ~100
candidates the whole state is a few KB, so one read per page load is cheaper and simpler than
querying a collection, and it keeps the ``load_state()`` contract identical to the old JSON
version -- which is why app.py needs no changes.
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
    """Firestore client, built once per server process.

    Credentials come from ``st.secrets["firebase"]`` (a TOML table holding the service-account
    JSON fields). Kept out of the repo -- see README for how to set it locally and on Streamlit
    Community Cloud.
    """
    import firebase_admin
    from firebase_admin import credentials, firestore

    if not firebase_admin._apps:
        service_account = dict(st.secrets["firebase"])
        # TOML mangles the newlines in the PEM private key; restore them.
        if "private_key" in service_account:
            service_account["private_key"] = service_account["private_key"].replace("\\n", "\n")
        firebase_admin.initialize_app(credentials.Certificate(service_account))
    return firestore.client()


def load_state() -> dict:
    """Read the full shared state. Returns the same shape as the old JSON file."""
    try:
        db = _client()
        filters_snap = db.collection(_COLLECTION).document(_FILTERS_DOC).get()
        candidates_snap = db.collection(_COLLECTION).document(_CANDIDATES_DOC).get()
        filters = (filters_snap.to_dict() or {}).get("filters", {}) if filters_snap.exists else {}
        candidates = (candidates_snap.to_dict() or {}) if candidates_snap.exists else {}
        return {"filters": filters, "candidates": candidates}
    except Exception as exc:  # noqa: BLE001 - surface backend problems in the UI, don't crash
        st.error(f"Could not read shared state from Firestore: {exc}")
        return {"filters": {}, "candidates": {}}


def save_filters(filters: dict[str, list[str]]) -> dict:
    db = _client()
    db.collection(_COLLECTION).document(_FILTERS_DOC).set({"filters": filters})
    return load_state()


def set_pool_flag(email: str, interviewer_key: str, value: bool) -> dict:
    """Set one interviewer's pool vote for one candidate.

    Writes only that candidate's sub-object so two people voting on different candidates at the
    same time don't overwrite each other.
    """
    db = _client()
    doc_ref = db.collection(_COLLECTION).document(_CANDIDATES_DOC)
    snapshot = doc_ref.get()
    current = (snapshot.to_dict() or {}) if snapshot.exists else {}
    entry = current.get(email) or {"pool": {}, "notes": {}}
    entry.setdefault("pool", {})[interviewer_key] = value
    entry.setdefault("notes", {})
    doc_ref.set({email: entry}, merge=True)
    return load_state()


def set_note(email: str, interviewer_key: str, text: str) -> dict:
    """Set one interviewer's note for one candidate (same merge semantics as set_pool_flag)."""
    db = _client()
    doc_ref = db.collection(_COLLECTION).document(_CANDIDATES_DOC)
    snapshot = doc_ref.get()
    current = (snapshot.to_dict() or {}) if snapshot.exists else {}
    entry = current.get(email) or {"pool": {}, "notes": {}}
    entry.setdefault("notes", {})[interviewer_key] = text
    entry.setdefault("pool", {})
    doc_ref.set({email: entry}, merge=True)
    return load_state()


def import_from_json(path: str) -> dict:
    """One-off migration: push an old ``pool_state.json`` into Firestore.

    Run once from ``scripts/migrate_state.py``; not used by the app itself.
    """
    with open(path, "r", encoding="utf-8") as handle:
        state = json.load(handle)
    db = _client()
    db.collection(_COLLECTION).document(_FILTERS_DOC).set({"filters": state.get("filters", {})})
    db.collection(_COLLECTION).document(_CANDIDATES_DOC).set(state.get("candidates", {}))
    return state
