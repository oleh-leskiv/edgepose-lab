"""EdgePose Lab 2026 — R&D trainee candidate explorer."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

import storage
from map_view import render_labs_map
from data import (
    LABS,
    SKILL_COLUMNS,
    SKILL_LABELS,
    apply_filters,
    explode_counts,
    get_lab,
    interview_display_columns,
    lab_filter_fields,
    lab_multiselect_columns,
    load_merged,
    normalize_link,
)

st.set_page_config(page_title="IT-JIM R&D Labs — Candidates", layout="wide")

# Shown before a lab is chosen: the sign-in screen and the lab picker.
APP_TITLE = "IT-JIM R&D Labs"

PRIMARY = "#5B8DEF"
COMPARE = "#F2A354"

MS_LABELS = {
    "domain_interest": "Domain interest",
    "cv_tasks": "CV tasks worked on",
    "cv_libraries": "CV / 3D libraries used",
    "edge_tools": "Edge deployment tools",
    "infra_tools": "Infra / compute tools",
    "graphics_tools": "3D / graphics tools",
    "audio_tasks": "Audio tasks worked on",
}


def ms_label(col_name: str) -> str:
    """Chart title for a multi-select field, whichever lab defined it."""
    return MS_LABELS.get(col_name, col_name.replace("_", " ").capitalize())


# ---------------------------------------------------------------------------
# Lab selection. The app serves several recruitment campaigns, each with its own
# response sheet, interviewers and questions, so a lab is chosen first and every
# tab below is rendered for that lab. "Both labs" is a cross-lab view and shows
# the applicant map only -- the scoring tabs are per-lab by definition.
# ---------------------------------------------------------------------------
BOTH_LABS = "both"
_LAB_STATE_KEY = "selected_lab"


def selected_lab() -> str | None:
    return st.session_state.get(_LAB_STATE_KEY)


def render_lab_picker() -> None:
    st.title(APP_TITLE)
    st.caption("Choose a lab to review its candidates.")
    st.write("")

    columns = st.columns(len(LABS) + 1)
    for column, (key, cfg) in zip(columns, LABS.items()):
        with column:
            st.subheader(cfg["title"])
            st.caption(cfg["subtitle"])
            if st.button("Open", key=f"pick_{key}", width="stretch"):
                st.session_state[_LAB_STATE_KEY] = key
                st.rerun()

    with columns[-1]:
        st.subheader("Both labs")
        st.caption("Applicants who applied to both — map only")
        if st.button("Open", key="pick_both", width="stretch"):
            st.session_state[_LAB_STATE_KEY] = BOTH_LABS
            st.rerun()


# ---------------------------------------------------------------------------
# Google sign-in gate. Anyone with an @it-jim.com Google account can view the
# app; edit rights are checked separately (see INTERVIEWERS / ADMIN_EMAIL).
# Replaces the old shared password: a password can't tell WHO is signed in, and
# "view-only for the whole company, edit for three people" needs a verified
# identity. Auth is via OIDC (st.login/st.user) configured in [auth] secrets.
#
# If [auth] isn't configured (e.g. a bare local run), the gate is skipped so the
# app still opens — identity just falls back to empty (view-only).
# ---------------------------------------------------------------------------
ALLOWED_EMAIL_DOMAIN = "it-jim.com"


def _auth_configured() -> bool:
    try:
        return "auth" in st.secrets
    except Exception:
        return False


def require_login() -> None:
    if not _auth_configured():
        return  # local dev without [auth]: skip the gate
    if st.user.is_logged_in:
        email = (st.user.get("email") or "").strip().lower()
        if email.endswith("@" + ALLOWED_EMAIL_DOMAIN):
            return
        # Signed in, but not an it-jim.com account.
        st.title(APP_TITLE)
        st.error(
            f"This app is restricted to {ALLOWED_EMAIL_DOMAIN} accounts. "
            f"You are signed in as {email or 'an unknown account'}."
        )
        if st.button("Sign out"):
            st.logout()
        st.stop()

    st.title(APP_TITLE)
    st.caption("Sign in with your it-jim.com Google account to continue.")
    if st.button("Log in with Google"):
        st.login()
    st.stop()


require_login()


def current_user_email() -> str:
    """Verified email of the signed-in user, or '' when auth is off (local dev)."""
    if not _auth_configured():
        return ""
    if getattr(st.user, "is_logged_in", False):
        return (st.user.get("email") or "").strip().lower()
    return ""


def _filled(value) -> bool:
    """True when the candidate actually answered this field.

    A plain `if value` is not enough: unanswered cells arrive as pandas NA,
    whose truth value raises TypeError rather than being falsey.
    """
    if isinstance(value, (list, tuple, set)):
        return len(value) > 0
    try:
        if pd.isna(value):
            return False
    except (TypeError, ValueError):
        pass
    return bool(value)


def _show(value, default: str = "—") -> str:
    """Value for display, or a dash when the candidate left it blank."""
    return str(value) if _filled(value) else default


def _table_height(n_rows: int, max_rows: int = 60) -> int:
    """Pixel height that shows every row instead of a short scrolling box.

    st.dataframe defaults to a fixed-height window; sized to the row count the
    whole table is visible at once. Capped so a very long table still fits on
    screen.
    """
    # Slightly generous: if the height is even a pixel short, st.dataframe adds
    # its own vertical scrollbar, which is exactly what this avoids.
    return 45 + 35 * max(1, min(n_rows, max_rows))


# Score columns hold a number or two; comments hold sentences. Left to divide
# the width evenly, the numeric columns stretch and the comments get squeezed,
# and any manual resize is lost on the next rerun -- so widths are declared.
_SCORES_COLUMN_WIDTHS = {
    "Candidate": "medium",
    "Average": "small",
    "Advise CV path": "small",
    "Decision": "small",
}


def _interviewer_at(position: int) -> str | None:
    """Who owns the sheet's "Interviewer N" column in this lab.

    Read from score_columns, not from the voter list: EdgePose has three voters
    but only two of them ran interviews, and the labs order them differently.
    """
    keys = LAB_CFG.get("score_columns") or list(INTERVIEWER_NAMES)
    if position >= len(keys):
        return None
    return INTERVIEWER_NAMES.get(keys[position])


def _score_label(position: int) -> str:
    name = _interviewer_at(position)
    return f"{name} score" if name else f"Interviewer {position + 1} score"


def _comment_label(position: int) -> str:
    name = _interviewer_at(position)
    return f"{name} comment" if name else f"Interviewer {position + 1} comment"


def _scores_column_config(frame: pd.DataFrame) -> dict:
    config: dict = {
        "Recording": st.column_config.LinkColumn(
            "Recording", display_text="Open recording", width="small"
        )
    }
    for col in frame.columns:
        if col in config:
            continue
        label = str(col).replace("\n", " ").strip()
        if col in _SCORES_COLUMN_WIDTHS or label.endswith("score"):
            width = _SCORES_COLUMN_WIDTHS.get(col, "small")
        elif "comment" in label.lower():
            width = "large"
        else:
            width = "medium"
        config[col] = st.column_config.Column(label, width=width)
    return config


@st.cache_data
def get_data(lab: str) -> pd.DataFrame:
    return load_merged(lab)


# Nothing below renders until a lab is picked.
if selected_lab() is None:
    render_lab_picker()
    st.stop()

LAB = selected_lab()

if st.button("← All labs"):
    del st.session_state[_LAB_STATE_KEY]
    st.rerun()

# "Both labs" is map-only: it spans labs, so per-lab tabs don't apply.
if LAB == BOTH_LABS:
    st.title("IT-JIM R&D Labs — Applicants")
    st.caption("Candidates who applied to both labs, and where each one got to.")
    render_labs_map(BOTH_LABS)
    st.stop()

LAB_CFG = get_lab(LAB)
INTERVIEWERS = LAB_CFG["interviewers"]
INTERVIEWER_NAMES = LAB_CFG["interviewer_names"]
ADMIN_EMAIL = LAB_CFG["admin_email"]
FILTER_FIELDS = lab_filter_fields(LAB)
MULTISELECT_COLUMNS = lab_multiselect_columns(LAB)

df = get_data(LAB)

st.title(f"{LAB_CFG['title']} — Trainee Candidates")
st.caption(f"{len(df)} candidates in the response sheet")

# ---------------------------------------------------------------------------
# Sidebar: identity, used to gate interview-pool checkboxes/notes
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("### Your identity")
    current_email = current_user_email()
    current_interviewer_key = INTERVIEWERS.get(current_email)
    is_admin = current_email == ADMIN_EMAIL
    if current_email:
        st.caption(f"Signed in as **{current_email}**")
        if current_interviewer_key:
            st.caption(
                "You can edit pool decisions"
                + (" for anyone." if is_admin else " in your own column.")
            )
        else:
            st.caption("View only — your account isn't an interviewer.")
        if _auth_configured() and st.button("Sign out"):
            st.logout()
    else:
        # Local dev without [auth] configured.
        st.caption("Running without sign-in (local dev) — view only.")

tab_stats, tab_filters, tab_explorer, tab_pool, tab_scores = st.tabs(
    ["Overview & Stats", "Filter Pool", "Candidate Explorer", "Interview Pool", "Interview Scores"]
)


# ---------------------------------------------------------------------------
# Tab: Overview & Stats
# ---------------------------------------------------------------------------
with tab_stats:
    # No heading here: the map carries its own title.
    render_labs_map(LAB_CFG["map_lab"])

    st.divider()
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Candidates", len(df))
    c2.metric("Avg Python", f"{df['skill_python'].mean():.1f}")
    c3.metric("Avg C++", f"{df['skill_cpp'].mean():.1f}")
    c4.metric("Avg lin. algebra", f"{df['skill_linalg'].mean():.1f}")
    c5.metric("Avg stats", f"{df['skill_stats'].mean():.1f}")

    st.subheader("Skill distributions")
    skill_cols = st.columns(len(SKILL_COLUMNS))
    for col, skill_col in zip(skill_cols, SKILL_COLUMNS):
        with col:
            fig = px.histogram(df, x=skill_col, nbins=10, title=SKILL_LABELS[skill_col])
            fig.update_traces(marker_color=PRIMARY)
            fig.update_layout(height=280, margin=dict(t=40, b=10, l=10, r=10), xaxis_title=None, yaxis_title=None)
            st.plotly_chart(fig, width="stretch")

    st.subheader("Categorical breakdown")
    categorical_fields = [
        ("english_level", "English level"),
        ("hours_per_week", "Hours/week available"),
        ("dl_framework", "DL framework"),
        ("trained_dl_models", "Trained DL models"),
        ("mobile_dev", "Mobile dev experience"),
        ("source", "How they heard about us"),
    ]
    # Labs ask different questions, and a column the form never had is backfilled
    # empty -- charting it would raise, so only fields with answers are drawn.
    charts = []
    for col_name, label in categorical_fields:
        if col_name not in df.columns:
            continue
        counts = df[col_name].value_counts()
        if counts.empty:
            continue
        charts.append((label, counts))

    if not charts:
        st.info("No categorical answers in this lab's form.")
    else:
        cat_cols = st.columns(3)
        for i, (label, counts) in enumerate(charts):
            with cat_cols[i % 3]:
                fig = px.bar(
                    x=list(counts.values), y=list(counts.index), orientation="h", title=label
                )
                fig.update_traces(marker_color=PRIMARY)
                fig.update_layout(height=300, margin=dict(t=40, b=10, l=10, r=10), xaxis_title=None, yaxis_title=None)
                st.plotly_chart(fig, width="stretch")

    st.subheader("Multi-select fields")
    ms_charts = []
    for col_name in MULTISELECT_COLUMNS:
        if col_name + "_list" not in df.columns:
            continue
        counts = explode_counts(df, col_name + "_list").head(10)
        if counts.empty:
            continue
        ms_charts.append((col_name, counts))

    if not ms_charts:
        st.info("No multi-select answers in this lab's form.")
    else:
        ms_cols = st.columns(3)
        for i, (col_name, counts) in enumerate(ms_charts):
            with ms_cols[i % 3]:
                fig = px.bar(
                    x=list(counts.values),
                    y=list(counts.index),
                    orientation="h",
                    title=ms_label(col_name),
                )
                fig.update_traces(marker_color=PRIMARY)
                fig.update_layout(height=320, margin=dict(t=40, b=10, l=10, r=10), xaxis_title=None, yaxis_title=None, yaxis=dict(autorange="reversed"))
                st.plotly_chart(fig, width="stretch")

    st.subheader("Top universities")
    uni_counts = df["university"].value_counts().head(12)
    fig = px.bar(x=uni_counts.values, y=uni_counts.index, orientation="h")
    fig.update_traces(marker_color=PRIMARY)
    fig.update_layout(height=380, margin=dict(t=10, b=10, l=10, r=10), xaxis_title="Candidates", yaxis_title=None, yaxis=dict(autorange="reversed"))
    st.plotly_chart(fig, width="stretch")


# ---------------------------------------------------------------------------
# Tab: Filter Pool
# ---------------------------------------------------------------------------
with tab_filters:
    st.subheader("Reduce the candidate pool")
    st.caption(
        "Pick one or more acceptable values per field. Within a field it's OR (e.g. PyTorch "
        "or Both both match); across fields it's AND. Leave a field empty to not filter on it."
    )

    saved_filters = storage.load_state(LAB).get("filters", {})
    for column, _label, _kind in FILTER_FIELDS:
        key = f"filter_{column}"
        if key not in st.session_state:
            st.session_state[key] = saved_filters.get(column, [])

    def _filter_options(column: str, kind: str) -> list[str]:
        if kind == "single":
            return sorted(v for v in df[column].dropna().unique())
        return sorted(explode_counts(df, column + "_list").index.tolist())

    filter_cols = st.columns(3)
    for i, (column, label, kind) in enumerate(FILTER_FIELDS):
        with filter_cols[i % 3]:
            st.multiselect(label, _filter_options(column, kind), key=f"filter_{column}")

    current_filters = {column: st.session_state[f"filter_{column}"] for column, _, _ in FILTER_FIELDS}

    btn_save, btn_reset = st.columns([1, 1])
    if btn_save.button("Save filters", width="stretch"):
        storage.save_filters({k: v for k, v in current_filters.items() if v}, LAB)
        st.success("Filters saved — they'll be pre-loaded next time the app starts.")
    if btn_reset.button("Reset filters", width="stretch"):
        # Delete the widget-backed keys rather than assigning to them: Streamlit
        # forbids setting session_state for a key tied to an instantiated widget
        # (raises StreamlitAPIException). After deletion + rerun the multiselects
        # re-initialise empty from the now-cleared saved filters.
        storage.save_filters({}, LAB)
        for column, _, _ in FILTER_FIELDS:
            st.session_state.pop(f"filter_{column}", None)
        st.rerun()

    filtered_df = apply_filters(df, current_filters, LAB)
    st.metric("Candidates matching filters", f"{len(filtered_df)} / {len(df)}")
    st.dataframe(
        filtered_df[["full_name", "university", "english_level", "dl_framework"] + SKILL_COLUMNS],
        width="stretch",
        hide_index=True,
    )


# ---------------------------------------------------------------------------
# Shared render helpers used by Candidate Explorer
# ---------------------------------------------------------------------------
def render_candidate_card(row: pd.Series) -> None:
    st.markdown(f"### {row['full_name']}")
    st.caption(f"{_show(row.get('position'))} · {_show(row.get('affiliation'))} · {_show(row.get('city'))}")

    meta1, meta2, meta3 = st.columns(3)
    meta1.markdown(f"**Email:** {_show(row.get('email'))}")
    meta1.markdown(f"**Phone:** {_show(row.get('phone'))}")
    meta1.markdown(f"**Telegram:** {_show(row.get('telegram'))}")
    meta2.markdown(f"**University:** {_show(row.get('university'))}")
    meta2.markdown(f"**Education:** {_show(row.get('education_details'))}")

    li_url, li_text = normalize_link(row.get("linkedin"))
    meta3.markdown(f"[LinkedIn]({li_url})" if li_url else f"**LinkedIn:** {_show(li_text)}")
    rs_url, rs_text = normalize_link(row.get("resume_link"))
    meta3.markdown(f"[Resume]({rs_url})" if rs_url else f"**Resume:** {_show(rs_text)}")

    st.markdown("**Skill ratings (1–10)**")
    skills = {SKILL_LABELS[c]: row[c] for c in SKILL_COLUMNS}
    fig = go.Figure(go.Bar(x=list(skills.values()), y=list(skills.keys()), orientation="h", marker_color=PRIMARY))
    fig.update_layout(height=220, margin=dict(t=10, b=10, l=10, r=10), xaxis_range=[0, 10])
    st.plotly_chart(fig, width="stretch")

    tags1, tags2 = st.columns(2)
    left_tags = [
        ("English", row.get("english_level")),
        ("Hours/week", row.get("hours_per_week")),
        ("DL framework", row.get("dl_framework")),
        ("Trained DL models", row.get("trained_dl_models")),
        ("Read papers", row.get("read_papers")),
    ]
    tags1.markdown(
        "  \n".join(f"**{label}:** {value}" for label, value in left_tags if _filled(value)) or "—"
    )
    domains = ", ".join(row.get("domain_interest_list") or [])
    tags2.markdown(
        f"**Domain interest:** {_show(domains)}  \n"
        f"**Mobile dev:** {_show(row.get('mobile_dev'))}  \n"
        f"**Source:** {_show(row.get('source'))}"
    )

    # Domain questions differ per lab: EdgePose asked about CV/edge tooling,
    # Audio about audio tasks and instruments. Only answered fields are shown.
    tool_fields = [
        ("cv_tasks_list", "CV tasks"),
        ("cv_libraries_list", "CV/3D libraries"),
        ("edge_tools_list", "Edge tools"),
        ("infra_tools_list", "Infra tools"),
        ("graphics_tools_list", "Graphics/3D tools"),
        ("audio_tasks_list", "Audio tasks"),
    ]
    tool_lines = [
        f"**{label}:** {', '.join(row.get(field) or [])}"
        for field, label in tool_fields
        if _filled(row.get(field))
    ]
    if _filled(row.get("instruments")):
        tool_lines.append(f"**Musical instruments:** {row.get('instruments')}")
    if tool_lines:
        with st.expander("Tasks / libraries / tools"):
            for line in tool_lines:
                st.markdown(line)

    text_fields = [
        ("cv_experience_text", "CV/DL experience"),
        ("audio_experience_text", "NLP & Audio DL experience"),
        ("own_projects", "Own projects"),
        ("achievements", "Achievements"),
        ("motivation", "Motivation"),
        ("comments", "Comments"),
        ("thoughts", "Thoughts"),
    ]
    text_lines = [
        (label, row.get(field)) for field, label in text_fields if _filled(row.get(field))
    ]
    if text_lines:
        with st.expander("Free-text answers"):
            for label, value in text_lines:
                st.markdown(f"**{label}:**\n\n{value}")


def render_pool_controls(row: pd.Series, current_key: str | None, is_admin: bool) -> None:
    st.markdown("**Interview pool decision**")
    state = storage.load_state(LAB)
    entry = state["candidates"].get(row["candidate_key"], {"pool": {}, "notes": {}})
    pool = entry.get("pool", {})
    notes = entry.get("notes", {})

    keys = list(INTERVIEWER_NAMES)
    cols = st.columns(len(keys))
    for i, key in enumerate(keys):
        name = INTERVIEWER_NAMES[key]
        editable = is_admin or current_key == key
        with cols[i]:
            checked = bool(pool.get(key, False))
            new_val = st.checkbox(name, value=checked, key=f"pool_{row['candidate_key']}_{key}", disabled=not editable)
            if new_val != checked:
                storage.set_pool_flag(row["candidate_key"], key, new_val, LAB)

            note_val = notes.get(key, "")
            new_note = st.text_area(
                f"{name}'s reason (if against)",
                value=note_val,
                key=f"note_{row['candidate_key']}_{key}",
                disabled=not editable,
                height=90,
            )
            if new_note != note_val:
                storage.set_note(row["candidate_key"], key, new_note, LAB)

    if not current_key and not is_admin:
        st.caption("Sign in as an interviewer to record a pool decision.")

    # Scheduled-call marker (Oleh tracks these) and a manually-pasted recording
    # link. Both editable by any interviewer / admin; stored in Firestore.
    st.divider()
    sc_col, rec_col = st.columns([1, 2])
    with sc_col:
        scheduled = bool(entry.get("scheduled", False))
        new_scheduled = st.checkbox(
            "📅 Call scheduled",
            value=scheduled,
            key=f"sched_{row['candidate_key']}",
            disabled=not (is_admin or current_key),
        )
        if new_scheduled != scheduled:
            storage.set_scheduled_call(row["candidate_key"], new_scheduled, LAB)
    with rec_col:
        rec_val = entry.get("recording", "")
        new_rec = st.text_input(
            "Recording link",
            value=rec_val,
            key=f"rec_{row['candidate_key']}",
            disabled=not (is_admin or current_key),
            placeholder="Paste the call recording URL",
        )
        if new_rec != rec_val:
            storage.set_recording_url(row["candidate_key"], new_rec, LAB)


def render_compare(pool_df: pd.DataFrame, default_name: str | None) -> None:
    names = pool_df["full_name"].tolist()
    compare_names = st.multiselect(
        "Pick 2+ candidates to compare skill ratings",
        names,
        default=[default_name] if default_name in names else [],
        key="explorer_compare",
    )
    if len(compare_names) >= 2:
        compare_df = pool_df[pool_df["full_name"].isin(compare_names)]
        fig = go.Figure()
        for _, r in compare_df.iterrows():
            fig.add_trace(
                go.Scatterpolar(
                    r=[r[c] for c in SKILL_COLUMNS] + [r[SKILL_COLUMNS[0]]],
                    theta=[SKILL_LABELS[c] for c in SKILL_COLUMNS] + [SKILL_LABELS[SKILL_COLUMNS[0]]],
                    fill="toself",
                    name=r["full_name"],
                )
            )
        fig.update_layout(polar=dict(radialaxis=dict(visible=True, range=[0, 10])), height=450)
        st.plotly_chart(fig, width="stretch")

        table_cols = ["full_name", "university", "english_level", "hours_per_week", "dl_framework"] + SKILL_COLUMNS
        st.dataframe(compare_df[table_cols], width="stretch", hide_index=True)
    else:
        st.info("Select at least two candidates to compare.")


def render_vs_average(df_all: pd.DataFrame, row2: pd.Series) -> None:
    avg_row = df_all[SKILL_COLUMNS].mean()
    st.markdown(
        "The **average candidate** is computed from *all* responses (regardless of active "
        "filters): mean skill ratings, and the most common answer for categorical fields."
    )

    col_radar, col_deltas = st.columns([2, 1])
    with col_radar:
        fig = go.Figure()
        fig.add_trace(
            go.Scatterpolar(
                r=[row2[c] for c in SKILL_COLUMNS] + [row2[SKILL_COLUMNS[0]]],
                theta=[SKILL_LABELS[c] for c in SKILL_COLUMNS] + [SKILL_LABELS[SKILL_COLUMNS[0]]],
                fill="toself",
                name=row2["full_name"],
                line_color=PRIMARY,
            )
        )
        fig.add_trace(
            go.Scatterpolar(
                r=[avg_row[c] for c in SKILL_COLUMNS] + [avg_row[SKILL_COLUMNS[0]]],
                theta=[SKILL_LABELS[c] for c in SKILL_COLUMNS] + [SKILL_LABELS[SKILL_COLUMNS[0]]],
                fill="toself",
                name="Average candidate",
                line_color=COMPARE,
            )
        )
        fig.update_layout(polar=dict(radialaxis=dict(visible=True, range=[0, 10])), height=450, title=f"{row2['full_name']} vs. average")
        st.plotly_chart(fig, width="stretch")

    with col_deltas:
        st.markdown("**Skill deltas vs. average**")
        for c in SKILL_COLUMNS:
            delta = row2[c] - avg_row[c]
            st.metric(SKILL_LABELS[c], f"{row2[c]:.0f}", f"{delta:+.1f} vs avg")

    st.subheader("Categorical fields: candidate vs. most common answer")
    categorical_fields = [
        ("english_level", "English level"),
        ("hours_per_week", "Hours/week available"),
        ("dl_framework", "DL framework"),
        ("trained_dl_models", "Trained DL models"),
        ("read_papers", "Read/implemented papers"),
        ("mobile_dev", "Mobile dev experience"),
    ]
    rows = []
    for col_name, label in categorical_fields:
        if col_name not in df_all.columns:
            continue
        mode_val = df_all[col_name].mode(dropna=True)
        if mode_val.empty:
            continue  # question this lab's form never asked
        rows.append(
            {
                "Field": label,
                "Candidate": _show(row2.get(col_name)),
                "Most common": mode_val.iloc[0],
            }
        )
    if rows:
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

    st.subheader("Multi-select fields: candidate vs. top picks across all candidates")
    comparisons = []
    for col_name in MULTISELECT_COLUMNS:
        if col_name + "_list" not in df_all.columns:
            continue
        top_overall = explode_counts(df_all, col_name + "_list").head(6)
        if top_overall.empty:
            continue
        comparisons.append((col_name, top_overall))

    if not comparisons:
        st.info("No multi-select answers in this lab's form.")
    else:
        ms_cols = st.columns(3)
        for i, (col_name, top_overall) in enumerate(comparisons):
            with ms_cols[i % 3]:
                candidate_items = set(row2.get(col_name + "_list") or [])
                labels = top_overall.index.tolist()
                colors = [PRIMARY if label in candidate_items else "#D8DEE9" for label in labels]
                fig = px.bar(
                    x=list(top_overall.values),
                    y=labels,
                    orientation="h",
                    title=ms_label(col_name),
                )
                fig.update_traces(marker_color=colors)
                fig.update_layout(height=280, margin=dict(t=40, b=10, l=10, r=10), xaxis_title=None, yaxis_title=None, yaxis=dict(autorange="reversed"))
                st.plotly_chart(fig, width="stretch")
                picked = ", ".join(candidate_items)
                st.caption(f"Highlighted = candidate's selections. Candidate picked: {_show(picked)}")


# ---------------------------------------------------------------------------
# Tab: Candidate Explorer (profile, pool decision, compare, vs. average)
# ---------------------------------------------------------------------------
with tab_explorer:
    if filtered_df.empty:
        st.warning("No candidates match the current filters — showing the full pool instead. Adjust filters in the Filter Pool tab.")
        pool_df = df
    else:
        st.caption(f"Browsing {len(filtered_df)} of {len(df)} candidates (see Filter Pool tab to adjust).")
        pool_df = filtered_df

    pool_names = pool_df["full_name"].tolist()
    selected_name = st.selectbox("Select a candidate", pool_names, key="explorer_select")
    selected_row = pool_df[pool_df["full_name"] == selected_name].iloc[0]

    render_candidate_card(selected_row)
    st.divider()
    render_pool_controls(selected_row, current_interviewer_key, is_admin)

    st.divider()
    sub_compare, sub_vs_avg = st.tabs(["Compare", "Vs. Average"])
    with sub_compare:
        render_compare(pool_df, selected_name)
    with sub_vs_avg:
        render_vs_average(df, selected_row)


# ---------------------------------------------------------------------------
# Tab: Interview Pool
# ---------------------------------------------------------------------------
with tab_pool:
    state = storage.load_state(LAB)
    rows = []
    for email, entry in state["candidates"].items():
        pool = entry.get("pool", {})
        notes = entry.get("notes", {})
        has_note = any((notes.get(k) or "").strip() for k in INTERVIEWER_NAMES)
        scheduled = bool(entry.get("scheduled", False))
        recording = entry.get("recording", "")
        # Show a candidate that has any vote, a note, OR a scheduled call.
        if not any(pool.values()) and not has_note and not scheduled:
            continue
        match = df[df["candidate_key"] == email]
        name = match.iloc[0]["full_name"] if not match.empty else email
        votes = sum(1 for v in pool.values() if v)
        entry_row = {"Candidate": name}
        for key, label in INTERVIEWER_NAMES.items():
            entry_row[label] = "✅" if pool.get(key) else "–"
        entry_row["Scheduled call"] = "🟣" if scheduled else "–"
        for key, label in INTERVIEWER_NAMES.items():
            entry_row[f"{label}'s note"] = notes.get(key, "")
        entry_row["_votes"] = votes
        rows.append(entry_row)

    if rows:
        total_interviewers = len(INTERVIEWER_NAMES)
        thresholds = [t for t in range(1, total_interviewers + 1)]
        metric_cols = st.columns(len(thresholds))
        for col, t in zip(metric_cols, thresholds):
            if t == total_interviewers and total_interviewers > 1:
                label = f"All {t} in favor"
            else:
                label = f"≥{t} interviewer{'s' if t > 1 else ''} in favor"
            col.metric(label, sum(1 for r in rows if r["_votes"] >= t))
        table = pd.DataFrame(rows).sort_values("_votes", ascending=False).drop(columns="_votes")
        # st.table, not st.dataframe: a dataframe always renders its own
        # scrolling viewport, however tall it is set. st.table writes a plain
        # HTML table, so the whole pool is visible and only the page scrolls.
        # It also wraps long notes instead of clipping them to one line.
        st.table(table.set_index("Candidate"))
    else:
        st.info("No interview-pool decisions recorded yet — set them from the Candidate Explorer tab.")


# ---------------------------------------------------------------------------
# Tab: Interview Scores
# ---------------------------------------------------------------------------
with tab_scores:
    interviewed = df.dropna(subset=["average_score"])
    if not interviewed.empty:
        ic1, ic2 = st.columns(2)
        ic1.metric("Interviewed", len(interviewed))
        # Audio's interviews were never scored, so the mean is NaN there; show a
        # dash rather than the literal "nan", which reads like a failure.
        mean_score = interviewed["average_score"].mean()
        ic2.metric(
            "Avg interview score",
            "—" if pd.isna(mean_score) else f"{mean_score:.1f}",
        )
        # Curated columns (see interview_display_columns); junk/duplicate sheet
        # columns are filtered out. Tidy up the messy multiline headers for
        # display only.
        cols = interview_display_columns(interviewed, LAB)
        scores = interviewed[cols].copy()

        # Recording links live in Firestore (the sheet buries them in a cell
        # hyperlink the API can't read). They are entered from the Candidate
        # Explorer; shown here, immediately before Decision.
        rec_state = storage.load_state(LAB)["candidates"]
        scores["Recording"] = [
            (rec_state.get(key) or {}).get("recording", "")
            for key in interviewed["candidate_key"]
        ]
        ordered = [c for c in scores.columns if c != "Recording"]
        if "Decision" in ordered:
            ordered.insert(ordered.index("Decision"), "Recording")
        else:
            ordered.append("Recording")
        scores = scores[ordered]

        display = scores.rename(
            columns={
                "full_name": "Candidate",
                "interviewer_1_score": _score_label(0),
                "interviewer_2_score": _score_label(1),
                "average_score": "Average",
                "Advise CV Learing Path": "Advise CV path",
                "Comment \nInterviewer 1\n(Sofiia)": _comment_label(0),
                "Comment \nInterviewer 2\n(Yura)": _comment_label(1),
                # The sheet's plain (unsuffixed) variants of the same headings.
                "Comment \nInterviewer 1": _comment_label(0),
                "Comment \nInterviewer 2": _comment_label(1),
            }
        )
        st.dataframe(
            display,
            width="stretch",
            hide_index=True,
            height=_table_height(len(display)),
            column_config=_scores_column_config(display),
        )
    else:
        st.info("No interviews recorded yet.")
