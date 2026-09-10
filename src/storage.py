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
      "scores":    {"sofiia": float, "yurii": float},   # interview marks
      "comments":  {"sofiia": str,   "yurii": str},     # interview write-ups
      "advise_cv": bool,   # "+" in the Advise CV path column
      "decision":  str,    # final call, made by Oleh
    }

Interview marks entered here take precedence over the same candidate's row in
the Interviews sheet, so past labs keep the marks already typed into the sheet
while new labs never need the sheet touched at all.

Firestore layout (one pair of documents per lab, so labs never mix):
    pool_state/filters            -> EdgePose filters
    pool_state/candidates         -> EdgePose candidates
    pool_state/filters_audio      -> Audio filters
    pool_state/candidates_audio   -> Audio candidates

EdgePose keeps the original document names, so the votes and notes recorded
before labs existed stay exactly where they are.
"""

from __future__ import annotations

import json

import streamlit as st

EMPTY_STATE: dict = {"filters": {}, "columns": [], "candidates": {}}

_COLLECTION = "pool_state"
_FILTERS_DOC = "filters"
_CANDIDATES_DOC = "candidates"


def _docs(lab: str | None) -> tuple[str, str]:
    """(filters_doc, candidates_doc) for a lab; EdgePose keeps the old names."""
    import data

    cfg = data.get_lab(lab)
    return cfg.get("filters_doc", _FILTERS_DOC), cfg.get("candidates_doc", _CANDIDATES_DOC)


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


def load_state(lab: str | None = None) -> dict:
    filters_doc, candidates_doc = _docs(lab)
    try:
        db = _client()
        filters_snap = db.collection(_COLLECTION).document(filters_doc).get()
        candidates_snap = db.collection(_COLLECTION).document(candidates_doc).get()
        settings = (filters_snap.to_dict() or {}) if filters_snap.exists else {}
        candidates = (candidates_snap.to_dict() or {}) if candidates_snap.exists else {}
        return {
            "filters": settings.get("filters", {}),
            "columns": settings.get("columns", []),
            "candidates": candidates,
        }
    except Exception as exc:  # noqa: BLE001
        st.error(f"Could not read shared state from Firestore: {exc}")
        return {"filters": {}, "columns": [], "candidates": {}}


def save_filters(filters: dict[str, list[str]], lab: str | None = None) -> dict:
    filters_doc, _ = _docs(lab)
    db = _client()
    # merge: the same document also holds the shared column selection.
    db.collection(_COLLECTION).document(filters_doc).set({"filters": filters}, merge=True)
    return load_state(lab)


def save_columns(columns: list[str], lab: str | None = None) -> dict:
    """Which form questions the table shows, shared by everyone on this lab."""
    filters_doc, _ = _docs(lab)
    db = _client()
    db.collection(_COLLECTION).document(filters_doc).set({"columns": columns}, merge=True)
    return load_state(lab)


def _update_candidate(email: str, mutate, lab: str | None = None) -> dict:
    """Read one candidate's entry, apply mutate(entry), write it back (merge).

    Per-candidate merge writes keep two people editing different candidates from
    clobbering each other.
    """
    _, candidates_doc = _docs(lab)
    db = _client()
    doc_ref = db.collection(_COLLECTION).document(candidates_doc)
    snapshot = doc_ref.get()
    current = (snapshot.to_dict() or {}) if snapshot.exists else {}
    entry = current.get(email) or {"pool": {}, "notes": {}}
    entry.setdefault("pool", {})
    entry.setdefault("notes", {})
    mutate(entry)
    doc_ref.set({email: entry}, merge=True)
    return load_state(lab)


def set_pool_flag(email: str, interviewer_key: str, value: bool, lab: str | None = None) -> dict:
    return _update_candidate(email, lambda e: e["pool"].__setitem__(interviewer_key, value), lab)


def set_note(email: str, interviewer_key: str, text: str, lab: str | None = None) -> dict:
    return _update_candidate(email, lambda e: e["notes"].__setitem__(interviewer_key, text), lab)


def set_scheduled_call(email: str, value: bool, lab: str | None = None) -> dict:
    """Mark (or unmark) that a call has been scheduled with this candidate."""
    return _update_candidate(email, lambda e: e.__setitem__("scheduled", value), lab)


def set_recording_url(email: str, url: str, lab: str | None = None) -> dict:
    """Store a manually-entered link to the interview recording."""
    return _update_candidate(email, lambda e: e.__setitem__("recording", url.strip()), lab)


def _set_in(entry: dict, bucket: str, key: str, value) -> None:
    entry.setdefault(bucket, {})[key] = value


def set_score(email: str, interviewer_key: str, value, lab: str | None = None) -> dict:
    """Record one interviewer's mark; None clears it."""
    return _update_candidate(email, lambda e: _set_in(e, "scores", interviewer_key, value), lab)


def set_interview_comment(email: str, interviewer_key: str, text: str, lab: str | None = None) -> dict:
    return _update_candidate(email, lambda e: _set_in(e, "comments", interviewer_key, text), lab)


def set_advise_cv(email: str, value: bool, lab: str | None = None) -> dict:
    """Mark the candidate as worth a CV learning path."""
    return _update_candidate(email, lambda e: e.__setitem__("advise_cv", value), lab)


def set_decision(email: str, text: str, lab: str | None = None) -> dict:
    """Final decision on the candidate."""
    return _update_candidate(email, lambda e: e.__setitem__("decision", text.strip()), lab)


def import_from_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        state = json.load(handle)
    db = _client()
    db.collection(_COLLECTION).document(_FILTERS_DOC).set({"filters": state.get("filters", {})})
    db.collection(_COLLECTION).document(_CANDIDATES_DOC).set(state.get("candidates", {}))
    return state
