"""EdgePose Lab 2026 — R&D trainee candidate explorer."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

import storage
from data import (
    ADMIN_EMAIL,
    FILTER_FIELDS,
    INTERVIEWER_NAMES,
    INTERVIEWERS,
    MULTISELECT_COLUMNS,
    SKILL_COLUMNS,
    SKILL_LABELS,
    apply_filters,
    explode_counts,
    interview_display_columns,
    load_merged,
    normalize_link,
)

st.set_page_config(page_title="EdgePose Lab 2026 — Candidates", layout="wide")

PRIMARY = "#5B8DEF"
COMPARE = "#F2A354"

MS_LABELS = {
    "domain_interest": "Domain interest",
    "cv_tasks": "CV tasks worked on",
    "cv_libraries": "CV / 3D libraries used",
    "edge_tools": "Edge deployment tools",
    "infra_tools": "Infra / compute tools",
    "graphics_tools": "3D / graphics tools",
}


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
        st.title("EdgePose Lab 2026 — Trainee Candidates")
        st.error(
            f"This app is restricted to {ALLOWED_EMAIL_DOMAIN} accounts. "
            f"You are signed in as {email or 'an unknown account'}."
        )
        if st.button("Sign out"):
            st.logout()
        st.stop()

    st.title("EdgePose Lab 2026 — Trainee Candidates")
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


@st.cache_data
def get_data() -> pd.DataFrame:
    return load_merged()


df = get_data()

st.title("EdgePose Lab 2026 — Trainee Candidates")
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
            st.plotly_chart(fig, use_container_width=True)

    st.subheader("Categorical breakdown")
    cat_cols = st.columns(3)
    categorical_fields = [
        ("english_level", "English level"),
        ("hours_per_week", "Hours/week available"),
        ("dl_framework", "DL framework"),
        ("trained_dl_models", "Trained DL models"),
        ("mobile_dev", "Mobile dev experience"),
        ("source", "How they heard about us"),
    ]
    for i, (col_name, label) in enumerate(categorical_fields):
        with cat_cols[i % 3]:
            counts = df[col_name].value_counts()
            fig = px.bar(x=counts.values, y=counts.index, orientation="h", title=label)
            fig.update_traces(marker_color=PRIMARY)
            fig.update_layout(height=300, margin=dict(t=40, b=10, l=10, r=10), xaxis_title=None, yaxis_title=None)
            st.plotly_chart(fig, use_container_width=True)

    st.subheader("Multi-select fields")
    ms_cols = st.columns(3)
    for i, col_name in enumerate(MULTISELECT_COLUMNS):
        with ms_cols[i % 3]:
            counts = explode_counts(df, col_name + "_list").head(10)
            fig = px.bar(x=counts.values, y=counts.index, orientation="h", title=MS_LABELS[col_name])
            fig.update_traces(marker_color=PRIMARY)
            fig.update_layout(height=320, margin=dict(t=40, b=10, l=10, r=10), xaxis_title=None, yaxis_title=None, yaxis=dict(autorange="reversed"))
            st.plotly_chart(fig, use_container_width=True)

    st.subheader("Top universities")
    uni_counts = df["university"].value_counts().head(12)
    fig = px.bar(x=uni_counts.values, y=uni_counts.index, orientation="h")
    fig.update_traces(marker_color=PRIMARY)
    fig.update_layout(height=380, margin=dict(t=10, b=10, l=10, r=10), xaxis_title="Candidates", yaxis_title=None, yaxis=dict(autorange="reversed"))
    st.plotly_chart(fig, use_container_width=True)


# ---------------------------------------------------------------------------
# Tab: Filter Pool
# ---------------------------------------------------------------------------
with tab_filters:
    st.subheader("Reduce the candidate pool")
    st.caption(
        "Pick one or more acceptable values per field. Within a field it's OR (e.g. PyTorch "
        "or Both both match); across fields it's AND. Leave a field empty to not filter on it."
    )

    saved_filters = storage.load_state().get("filters", {})
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
    if btn_save.button("Save filters", use_container_width=True):
        storage.save_filters({k: v for k, v in current_filters.items() if v})
        st.success("Filters saved — they'll be pre-loaded next time the app starts.")
    if btn_reset.button("Reset filters", use_container_width=True):
        # Delete the widget-backed keys rather than assigning to them: Streamlit
        # forbids setting session_state for a key tied to an instantiated widget
        # (raises StreamlitAPIException). After deletion + rerun the multiselects
        # re-initialise empty from the now-cleared saved filters.
        storage.save_filters({})
        for column, _, _ in FILTER_FIELDS:
            st.session_state.pop(f"filter_{column}", None)
        st.rerun()

    filtered_df = apply_filters(df, current_filters)
    st.metric("Candidates matching filters", f"{len(filtered_df)} / {len(df)}")
    st.dataframe(
        filtered_df[["full_name", "university", "english_level", "dl_framework"] + SKILL_COLUMNS],
        use_container_width=True,
        hide_index=True,
    )


# ---------------------------------------------------------------------------
# Shared render helpers used by Candidate Explorer
# ---------------------------------------------------------------------------
def render_candidate_card(row: pd.Series) -> None:
    st.markdown(f"### {row['full_name']}")
    st.caption(f"{row.get('position') or '—'} · {row.get('affiliation') or '—'} · {row.get('city') or '—'}")

    meta1, meta2, meta3 = st.columns(3)
    meta1.markdown(f"**Email:** {row.get('email') or '—'}")
    meta1.markdown(f"**Phone:** {row.get('phone') or '—'}")
    meta1.markdown(f"**Telegram:** {row.get('telegram') or '—'}")
    meta2.markdown(f"**University:** {row.get('university') or '—'}")
    meta2.markdown(f"**Education:** {row.get('education_details') or '—'}")

    li_url, li_text = normalize_link(row.get("linkedin"))
    meta3.markdown(f"[LinkedIn]({li_url})" if li_url else f"**LinkedIn:** {li_text or '—'}")
    rs_url, rs_text = normalize_link(row.get("resume_link"))
    meta3.markdown(f"[Resume]({rs_url})" if rs_url else f"**Resume:** {rs_text or '—'}")

    st.markdown("**Skill ratings (1–10)**")
    skills = {SKILL_LABELS[c]: row[c] for c in SKILL_COLUMNS}
    fig = go.Figure(go.Bar(x=list(skills.values()), y=list(skills.keys()), orientation="h", marker_color=PRIMARY))
    fig.update_layout(height=220, margin=dict(t=10, b=10, l=10, r=10), xaxis_range=[0, 10])
    st.plotly_chart(fig, use_container_width=True)

    tags1, tags2 = st.columns(2)
    tags1.markdown(f"**English:** {row.get('english_level') or '—'}  \n**Hours/week:** {row.get('hours_per_week') or '—'}  \n**DL framework:** {row.get('dl_framework') or '—'}  \n**Trained DL models:** {row.get('trained_dl_models') or '—'}  \n**Read papers:** {row.get('read_papers') or '—'}")
    tags2.markdown(f"**Domain interest:** {', '.join(row.get('domain_interest_list') or []) or '—'}  \n**Mobile dev:** {row.get('mobile_dev') or '—'}  \n**Source:** {row.get('source') or '—'}")

    with st.expander("CV tasks / libraries / tools"):
        st.markdown(f"**CV tasks:** {', '.join(row.get('cv_tasks_list') or []) or '—'}")
        st.markdown(f"**CV/3D libraries:** {', '.join(row.get('cv_libraries_list') or []) or '—'}")
        st.markdown(f"**Edge tools:** {', '.join(row.get('edge_tools_list') or []) or '—'}")
        st.markdown(f"**Infra tools:** {', '.join(row.get('infra_tools_list') or []) or '—'}")
        st.markdown(f"**Graphics/3D tools:** {', '.join(row.get('graphics_tools_list') or []) or '—'}")

    with st.expander("Free-text answers"):
        st.markdown(f"**CV/DL experience:**\n\n{row.get('cv_experience_text') or '—'}")
        st.markdown(f"**Own projects:**\n\n{row.get('own_projects') or '—'}")
        st.markdown(f"**Achievements:**\n\n{row.get('achievements') or '—'}")
        st.markdown(f"**Motivation:**\n\n{row.get('motivation') or '—'}")
        if row.get("comments"):
            st.markdown(f"**Comments:**\n\n{row.get('comments')}")


def render_pool_controls(row: pd.Series, current_key: str | None, is_admin: bool) -> None:
    st.markdown("**Interview pool decision**")
    state = storage.load_state()
    entry = state["candidates"].get(row["candidate_key"], {"pool": {}, "notes": {}})
    pool = entry.get("pool", {})
    notes = entry.get("notes", {})

    cols = st.columns(3)
    for i, key in enumerate(["sofiia", "oleh", "yurii"]):
        name = INTERVIEWER_NAMES[key]
        editable = is_admin or current_key == key
        with cols[i]:
            checked = bool(pool.get(key, False))
            new_val = st.checkbox(name, value=checked, key=f"pool_{row['candidate_key']}_{key}", disabled=not editable)
            if new_val != checked:
                storage.set_pool_flag(row["candidate_key"], key, new_val)

            note_val = notes.get(key, "")
            new_note = st.text_area(
                f"{name}'s reason (if against)",
                value=note_val,
                key=f"note_{row['candidate_key']}_{key}",
                disabled=not editable,
                height=90,
            )
            if new_note != note_val:
                storage.set_note(row["candidate_key"], key, new_note)

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
            storage.set_scheduled_call(row["candidate_key"], new_scheduled)
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
            storage.set_recording_url(row["candidate_key"], new_rec)


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
        st.plotly_chart(fig, use_container_width=True)

        table_cols = ["full_name", "university", "english_level", "hours_per_week", "dl_framework"] + SKILL_COLUMNS
        st.dataframe(compare_df[table_cols], use_container_width=True, hide_index=True)
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
        st.plotly_chart(fig, use_container_width=True)

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
        mode_val = df_all[col_name].mode(dropna=True)
        mode_val = mode_val.iloc[0] if not mode_val.empty else "—"
        rows.append({"Field": label, "Candidate": row2.get(col_name) or "—", "Most common": mode_val})
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    st.subheader("Multi-select fields: candidate vs. top picks across all candidates")
    ms_cols = st.columns(3)
    for i, col_name in enumerate(MULTISELECT_COLUMNS):
        with ms_cols[i % 3]:
            candidate_items = set(row2.get(col_name + "_list") or [])
            top_overall = explode_counts(df_all, col_name + "_list").head(6)
            labels = top_overall.index.tolist()
            colors = [PRIMARY if label in candidate_items else "#D8DEE9" for label in labels]
            fig = px.bar(x=top_overall.values, y=labels, orientation="h", title=MS_LABELS[col_name])
            fig.update_traces(marker_color=colors)
            fig.update_layout(height=280, margin=dict(t=40, b=10, l=10, r=10), xaxis_title=None, yaxis_title=None, yaxis=dict(autorange="reversed"))
            st.plotly_chart(fig, use_container_width=True)
            st.caption(f"Highlighted = candidate's selections. Candidate picked: {', '.join(candidate_items) or '—'}")


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
    state = storage.load_state()
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
        rows.append(
            {
                "Candidate": name,
                "Sofiia": "✅" if pool.get("sofiia") else "–",
                "Oleh": "✅" if pool.get("oleh") else "–",
                "Yurii": "✅" if pool.get("yurii") else "–",
                "Scheduled call": "🟣" if scheduled else "–",
                "Sofiia's note": notes.get("sofiia", ""),
                "Oleh's note": notes.get("oleh", ""),
                "Yurii's note": notes.get("yurii", ""),
                "Recording": recording,
                "_votes": votes,
            }
        )

    if rows:
        v1, v2, v3 = st.columns(3)
        v1.metric("≥1 interviewer in favor", sum(1 for r in rows if r["_votes"] >= 1))
        v2.metric("≥2 interviewers in favor", sum(1 for r in rows if r["_votes"] >= 2))
        v3.metric("All 3 in favor", sum(1 for r in rows if r["_votes"] == 3))
        table = pd.DataFrame(rows).sort_values("_votes", ascending=False).drop(columns="_votes")
        st.dataframe(table, use_container_width=True, hide_index=True)
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
        ic2.metric("Avg interview score", f"{interviewed['average_score'].mean():.1f}")
        # Curated columns (see interview_display_columns); junk/duplicate sheet
        # columns are filtered out. Tidy up the messy multiline headers for
        # display only.
        cols = interview_display_columns(interviewed)
        display = interviewed[cols].rename(
            columns={
                "full_name": "Candidate",
                "interviewer_1_score": "Sofiia score",
                "interviewer_2_score": "Yurii score",
                "average_score": "Average",
                "Advise CV Learing Path": "Advise CV path",
                "Comment \nInterviewer 1\n(Sofiia)": "Sofiia comment",
                "Comment \nInterviewer 2\n(Yura)": "Yurii comment",
            }
        )
        st.dataframe(display, use_container_width=True, hide_index=True)
    else:
        st.info("No interviews recorded yet.")
