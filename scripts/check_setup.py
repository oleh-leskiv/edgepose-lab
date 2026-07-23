"""Pre-flight check: can we reach the spreadsheet and Firestore, and do the columns match?

Run this before deploying:

    uv run streamlit run scripts/check_setup.py

Reports what it finds instead of failing silently, so a column rename in the form or a missing
share on the sheet shows up here rather than as a stack trace in the deployed app.
"""

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

st.title("Setup check")

# --- Secrets present? ---
st.subheader("1. Secrets")
if "firebase" not in st.secrets:
    st.error("No [firebase] section in secrets. Copy .streamlit/secrets.toml.example and fill it in.")
    st.stop()
sa = dict(st.secrets["firebase"])
st.success(f"Service account: `{sa.get('client_email', '(missing client_email)')}`")
if "private_key" in sa and "BEGIN PRIVATE KEY" not in sa["private_key"]:
    st.error("private_key looks malformed — it should contain -----BEGIN PRIVATE KEY-----")
    st.stop()

# --- Google Sheets reachable? ---
st.subheader("2. Google Sheet")
try:
    import data

    candidates = data.load_candidates()
    st.success(f"Read **{len(candidates)}** candidates from '{data.CANDIDATES_SHEET}'")
except Exception as exc:  # noqa: BLE001
    st.error(f"Could not read the candidates tab: {exc}")
    st.caption(
        "If this says PERMISSION_DENIED, share the spreadsheet with the service account email "
        "above (Viewer is enough)."
    )
    st.stop()

# --- Do the expected columns exist? ---
st.subheader("3. Column names")
expected = set(data.RENAME.values())
actual = set(candidates.columns)
missing = sorted(expected - actual)
if missing:
    st.warning(f"{len(missing)} expected column(s) not found — the form may have been edited:")
    st.code("\n".join(missing))
    st.caption("Raw headers currently in the sheet:")
    st.code("\n".join(str(c) for c in candidates.columns))
else:
    st.success("All expected columns present")

# --- Interviews tab ---
st.subheader("4. Interviews tab")
try:
    interviews = data.load_interviews()
    st.success(f"Read **{len(interviews)}** interview row(s)")
except Exception as exc:  # noqa: BLE001
    st.error(f"Could not read the Interviews tab: {exc}")

# --- Firestore reachable? ---
st.subheader("5. Firestore")
try:
    import storage

    state = storage.load_state()
    st.success(
        f"Connected. {len(state.get('candidates', {}))} candidate entries, "
        f"{len(state.get('filters', {}))} saved filter fields."
    )
except Exception as exc:  # noqa: BLE001
    st.error(f"Could not reach Firestore: {exc}")

st.divider()
st.caption("All green? You're ready to migrate old state and deploy.")
