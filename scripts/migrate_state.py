"""One-off: copy an existing local ``pool_state.json`` into Firestore.

Run once, after secrets are configured and before the team starts using the deployed app:

    uv run streamlit run scripts/migrate_state.py

It runs as a tiny Streamlit page purely so it can reuse ``st.secrets`` for credentials. Shows
what it is about to write, waits for a button press, then writes. Safe to re-run: it overwrites
both documents wholesale, so running it twice leaves the same result as running it once.
"""

import json
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import storage  # noqa: E402

st.title("Migrate pool_state.json to Firestore")

default_path = Path(__file__).resolve().parent.parent / "data" / "pool_state.json"
path_str = st.text_input("Path to pool_state.json", value=str(default_path))
path = Path(path_str)

if not path.exists():
    st.warning(f"No file at {path}. Point this at the pool_state.json from the old setup.")
    st.stop()

state = json.loads(path.read_text(encoding="utf-8"))
candidates = state.get("candidates", {})
filters = state.get("filters", {})

votes = sum(1 for entry in candidates.values() if entry.get("pool"))
notes = sum(1 for entry in candidates.values() if any(entry.get("notes", {}).values()))

st.write(f"**{len(candidates)}** candidates — {votes} with votes, {notes} with notes")
st.write(f"**{len(filters)}** saved filter fields: {', '.join(filters) or '(none)'}")

st.divider()
st.caption("This overwrites the two Firestore documents with the contents above.")

if st.button("Write to Firestore", type="primary"):
    storage.import_from_json(str(path))
    st.success("Done. Open the app and confirm the votes and notes are there.")
