"""Embed the R&D Labs applicant map inside Streamlit.

The map is a self-contained HTML/JS app (SVG map of Ukraine, Firebase-backed
edits). We render it verbatim in an iframe so its UI/UX is preserved exactly.

`lab` picks which dataset the map opens on:
    "edge"  -> R&D EdgePose Lab 2026   (the file's own default)
    "music" -> R&D Music Lab 2026
    "both"  -> cross-lab view (no map, table only)

When `hide_switcher` is True the map's own lab buttons are hidden, because the
lab is chosen outside the map, in the app itself.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

_MAP_FILE = Path(__file__).parent / "assets" / "labs_map.html"

_HIDE_SWITCHER_CSS = "<style>.lab-switcher{display:none !important;}</style>"

_FORCE_LAB_JS = """
<script>
(function () {
  var lab = "%s";
  function force() {
    if (typeof switchLab !== "function") { setTimeout(force, 50); return; }
    if (lab !== "edge") switchLab(lab);   // "edge" is already the file default
  }
  force();
})();
</script>
"""


@st.cache_data(show_spinner=False)
def _read_map_html() -> str:
    return _MAP_FILE.read_text(encoding="utf-8")


def render_labs_map(lab: str = "edge", height: int = 1500, hide_switcher: bool = True) -> None:
    """Render the applicant map for one lab."""
    if lab not in {"edge", "music", "both"}:
        raise ValueError(f"unknown lab: {lab!r}")

    html = _read_map_html()
    extra = (_HIDE_SWITCHER_CSS if hide_switcher else "") + (_FORCE_LAB_JS % lab)

    # Append just before </body> so all functions are already defined.
    if "</body>" in html:
        html = html.replace("</body>", extra + "</body>", 1)
    else:
        html += extra

    components.html(html, height=height, scrolling=True)
