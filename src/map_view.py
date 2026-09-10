"""Embed the R&D Labs applicant map inside Streamlit.

The map is a self-contained HTML/JS app (SVG map of Ukraine, Firebase-backed
edits). It is rendered verbatim in an iframe so its UI/UX is preserved exactly.

`lab` picks which dataset the map opens on:
    "edge"  -> R&D EdgePose Lab 2026   (the file's own default)
    "music" -> R&D Music Lab 2026
    "both"  -> cross-lab view

When `hide_switcher` is True the map's own lab buttons are hidden, because the
lab is chosen outside the map, in the app itself.

Height: a fixed iframe height is always wrong somewhere -- the map's layout
reflows with viewport width, so the same number leaves dead space on one screen
and a scrollbar on another. Instead the page measures itself and resizes its own
frame, so the map sits flush in the page with no inner scrollbar.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

_MAP_FILE = Path(__file__).parent / "assets" / "labs_map.html"

# Starting height only; the script below replaces it with the real one.
_FALLBACK_HEIGHT = 1100

_EMBED_CSS = """
<style>
  /* Chosen outside the map now, so the in-map switcher is redundant. */
  .lab-switcher { display: none !important; }
  /* Sit flush against the surrounding page instead of floating in a box. */
  html, body { background: transparent !important; overflow: hidden !important; }
  body { margin: 0 !important; padding: 0 !important; }
  .container { padding-top: 0 !important; margin-top: 0 !important; }

  /* Inside an iframe, 100vh is the frame's own height. The map's own
     "min-height: 100vh" therefore stretches the content to whatever the frame
     currently is, so a measured height can only ever grow -- which left a gap
     under short lists. Standing the rule down lets the content report its real
     size, and the frame follows it in both directions. */
  body { min-height: 0 !important; height: auto !important; }

  /* Same reason, and it also removes the panel's inner scrollbar: the list of
     students grows the page instead of scrolling inside a fixed box. */
  .panel-content { max-height: none !important; overflow-y: visible !important; }
</style>
"""

_HIDE_STATS_CSS = "<style>.stats-bar{display:none !important;}</style>"

_EMBED_JS = """
<script>
(function () {
  var lab = "%(lab)s";

  function applyLab() {
    if (typeof switchLab !== "function") { setTimeout(applyLab, 50); return; }
    if (lab !== "edge") switchLab(lab);   // "edge" is the file's own default
  }
  applyLab();

  // Resize our own iframe to fit the content exactly. The frame is same-origin
  // (Streamlit renders this via srcdoc), so frameElement is reachable.
  var last = 0;
  function fit() {
    try {
      var frame = window.frameElement;
      if (!frame) return;
      // Measure where the content actually ends, not the page's scrollHeight:
      // once the frame has grown, scrollHeight can never report less than the
      // frame itself, so the height would only ever ratchet upwards and leave
      // a gap under shorter lists.
      var bottom = 0;
      var kids = document.body.children;
      for (var i = 0; i < kids.length; i++) {
        var box = kids[i].getBoundingClientRect();
        if (box.height) bottom = Math.max(bottom, box.bottom);
      }
      var h = Math.ceil(bottom + window.scrollY) + 8;
      if (!h || Math.abs(h - last) < 4) return;
      last = h;
      frame.style.height = h + "px";
      frame.setAttribute("height", h);
      frame.setAttribute("scrolling", "no");
      // Streamlit wraps the frame in fixed-height containers. Growing only the
      // frame makes it spill out of that wrapper and cover the charts below,
      // so the wrappers have to grow with it.
      var box = frame.parentElement;
      for (var i = 0; i < 2 && box; i++) {
        box.style.height = h + "px";
        box = box.parentElement;
      }
    } catch (err) { /* cross-origin: keep the fallback height */ }
  }

  fit();
  window.addEventListener("load", fit);
  window.addEventListener("resize", fit);
  if (window.ResizeObserver) new ResizeObserver(fit).observe(document.body);
  // The SVG map and the panels render asynchronously; keep checking briefly.
  var ticks = 0;
  var timer = setInterval(function () {
    fit();
    if (++ticks > 40) clearInterval(timer);
  }, 250);
  // Expanding a university row changes height well after load.
  document.addEventListener("click", function () { setTimeout(fit, 60); });
})();
</script>
"""


@st.cache_data(show_spinner=False)
def _read_map_html() -> str:
    return _MAP_FILE.read_text(encoding="utf-8")


def render_labs_map(lab: str = "edge", hide_switcher: bool = True) -> None:
    """Render the applicant map for one lab, sized to its content."""
    if lab not in {"edge", "music", "both"}:
        raise ValueError(f"unknown lab: {lab!r}")

    html = _read_map_html()
    extra = (_EMBED_CSS if hide_switcher else "") + (_EMBED_JS % {"lab": lab})
    if lab == "both":
        # The headline counters are one lab's totals, so they are wrong for the
        # cross-lab view, which is about the overlap rather than either intake.
        extra += _HIDE_STATS_CSS

    # Appended before </body> so every function it calls is already defined.
    if "</body>" in html:
        html = html.replace("</body>", extra + "</body>", 1)
    else:
        html += extra

    components.html(html, height=_FALLBACK_HEIGHT, scrolling=False)
