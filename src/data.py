"""Load and clean the EdgePose Lab 2026 candidate data from the response spreadsheet.

Source of truth is the live Google Sheet the response form writes into, not a checked-in
.xlsx export. Two reasons: applications arrive daily, so any committed file is stale within
a day; and the sheet holds names, phone numbers, Telegram handles and resume links for ~100
real applicants, which should not live in a git repository at all.

Access is via a read-only service account (see README). Everything below the two loader
functions is unchanged from the .xlsx version -- the cleaning logic works on a DataFrame and
does not care where it came from.
"""

import re

import pandas as pd
import streamlit as st

# Spreadsheet the EdgePose Lab 2026 response form writes into.
# Kept as module-level names so existing call sites keep working; the per-lab
# values now live in LABS below and are what the loaders actually read.
SPREADSHEET_ID = "16Hv_tvcrzMi-Qbm8kTBfIQPzd4-gg7MCz394a7LfJQ8"
CANDIDATES_SHEET = "Form responses 1"
INTERVIEWS_SHEET = "Interviews"

AUDIO_SPREADSHEET_ID = "1w1zXovxFd648nq9zqzUZbwe4FAJSNdq7-xTEV_r61zc"

DEFAULT_LAB = "edge"

# Applications trickle in during the day; re-read at most this often per server process.
_CACHE_TTL_SECONDS = 300

INTERVIEWERS = {
    "sofia.kuzmenko@it-jim.com": "sofiia",
    "oleh.leskiv@it-jim.com": "oleh",
    "yurii.chyrka@it-jim.com": "yurii",
}
INTERVIEWER_NAMES = {"sofiia": "Sofiia", "oleh": "Oleh", "yurii": "Yurii"}
ADMIN_EMAIL = "sofia.kuzmenko@it-jim.com"

RENAME = {
    "Timestamp": "timestamp",
    "Email address": "email",
    "First name:": "first_name",
    "Last name:": "last_name",
    "Provide your phone number:": "phone",
    "Provide your Telegram username:": "telegram",
    "Provide a link to your LinkedIn profile:": "linkedin",
    "Provide a link to your resume (Google Drive, Dropbox):": "resume_link",
    "Current location (city):": "city",
    "Please specify the university you studied or continue to study at": "university",
    "Education details (university if not found in the dropdown menu above, college, etc.), specialty, degree, graduation year.": "education_details",
    "Current affiliation (company, university, etc.):": "affiliation",
    "Current position:": "position",
    "Which domain are you most interested in?": "domain_interest",
    "How much time can you devote to the trainee program per week?": "hours_per_week",
    "Rate your English skills:": "english_level",
    "Rate your Python skills:": "skill_python",
    "Rate your C++ skills:": "skill_cpp",
    "Rate your linear algebra knowledge:": "skill_linalg",
    "Rate your statistics and calculus knowledge:": "skill_stats",
    "Which DL framework do you use?": "dl_framework",
    "Have you trained deep learning models yourself?": "trained_dl_models",
    "Which computer vision tasks have you worked on?  (Select all that apply)": "cv_tasks",
    "Describe your experience with computer vision and deep learning.": "cv_experience_text",
    "Have you had any experience working on your own projects? If yes, share links to GitHub, Bitbucket, etc.": "own_projects",
    "Which CV / pose estimation / 3D libraries have you used?": "cv_libraries",
    "Do you have any other professional achievements, projects, etc.? Don`t be shy, let us know :)": "achievements",
    "What is your motivation to become a trainee at our company?": "motivation",
    "Do you have an active Private Entrepreneur status?": "private_entrepreneur",
    "How did you learn about the internship?": "source",
    "I agree to the collection, processing and storage of my personal data": "consent",
    "Comments/suggestions:": "comments",
    "Have you read or implemented research papers?": "read_papers",
    "Do you have experience with mobile development?": "mobile_dev",
    "Which edge deployment / inference tools have you used?": "edge_tools",
    "Which infrastructure / compute tools have you used?": "infra_tools",
    "Which 3D / graphics / synthetic data tools have you used?": "graphics_tools",
}

SKILL_COLUMNS = ["skill_python", "skill_cpp", "skill_linalg", "skill_stats"]
SKILL_LABELS = {
    "skill_python": "Python",
    "skill_cpp": "C++",
    "skill_linalg": "Linear algebra",
    "skill_stats": "Statistics & calculus",
}

MULTISELECT_COLUMNS = [
    "domain_interest",
    "cv_tasks",
    "cv_libraries",
    "edge_tools",
    "infra_tools",
    "graphics_tools",
]

ORDINAL_MAPS = {
    "english_level": {"Beginner": 1, "Pre-intermediate": 2, "Intermediate": 3, "Upper-intermediate": 4, "Advanced": 5, "Fluent": 6},
    "trained_dl_models": {"Never": 1, "Rarely": 2, "Occasionally": 3, "Regularly": 4},
    "read_papers": {"Never": 1, "Rarely": 2, "Occasionally": 3, "Regularly": 4},
    "hours_per_week": {"0-10 hours": 1, "11-20 hours": 2, "21-30 hours": 3, "31-40 hours": 4, "40+ hours": 5},
}

# (column, label, kind) — "single" filters keep rows whose value is in the selected set,
# "multi" filters (backed by the exploded "<column>_list" column) keep rows whose list
# intersects the selected set. Within a field this is OR; across fields it's AND.
FILTER_FIELDS = [
    ("english_level", "English level", "single"),
    ("hours_per_week", "Hours/week available", "single"),
    ("dl_framework", "DL framework", "single"),
    ("trained_dl_models", "Trained DL models", "single"),
    ("read_papers", "Read/implemented papers", "single"),
    ("mobile_dev", "Mobile dev experience", "single"),
    ("university", "University", "single"),
    ("domain_interest", "Domain interest", "multi"),
    ("cv_tasks", "CV tasks worked on", "multi"),
    ("cv_libraries", "CV / 3D libraries used", "multi"),
    ("edge_tools", "Edge deployment tools", "multi"),
    ("infra_tools", "Infra / compute tools", "multi"),
    ("graphics_tools", "3D / graphics tools", "multi"),
]

# ---------------------------------------------------------------------------
# Lab registry
# ---------------------------------------------------------------------------
# Each lab is a separate response spreadsheet with its own interviewers and its
# own domain questions. The application core (skill ratings, names, links) is
# identical across labs, so only the differences are listed here.
#
# "rename_extra" is merged over RENAME: it covers headings that differ between
# labs (Audio's form writes "Email Address", EdgePose's writes "Email address")
# plus each lab's domain-specific questions.

AUDIO_RENAME_EXTRA = {
    "Email Address": "email",
    "Which audio tasks have you worked on previously?": "audio_tasks",
    "Describe your experience with NLP & Audio DL tasks": "audio_experience_text",
    "Do you play any musical instruments? If so, please list them. ": "instruments",
    "Thougths": "thoughts",
}

AUDIO_MULTISELECT_COLUMNS = ["domain_interest", "audio_tasks"]

# Audio's form never asked about CV tasks, edge/infra/3D tooling, papers or
# DL-model training, so those fields are absent from its filter set.
AUDIO_FILTER_FIELDS = [
    ("english_level", "English level", "single"),
    ("hours_per_week", "Hours/week available", "single"),
    ("dl_framework", "DL framework", "single"),
    ("mobile_dev", "Mobile dev experience", "single"),
    ("university", "University", "single"),
    ("domain_interest", "Domain interest", "multi"),
    ("audio_tasks", "Audio tasks worked on", "multi"),
]

LABS = {
    "edge": {
        "key": "edge",
        "title": "EdgePose Lab 2026",
        "subtitle": "Computer Vision & Edge Deployment",
        "spreadsheet_id": SPREADSHEET_ID,
        "candidates_sheet": CANDIDATES_SHEET,
        "interviews_sheet": INTERVIEWS_SHEET,
        # Which dataset the embedded applicant map should open on.
        "map_lab": "edge",
        "interviewers": {
            "sofia.kuzmenko@it-jim.com": "sofiia",
            "oleh.leskiv@it-jim.com": "oleh",
            "yurii.chyrka@it-jim.com": "yurii",
        },
        "interviewer_names": {"sofiia": "Sofiia", "oleh": "Oleh", "yurii": "Yurii"},
        # Who owns the sheet's "Interviewer 1 / 2" columns, in that order. This
        # is NOT the same list as interviewer_names: all three vote in the pool,
        # but only Sofiia and Yurii ran interviews.
        "score_columns": ["sofiia", "yurii"],
        "admin_email": "sofia.kuzmenko@it-jim.com",
        "rename_extra": {},
        "multiselect_columns": MULTISELECT_COLUMNS,
        "filter_fields": FILTER_FIELDS,
        # Existing Firestore documents -- left untouched so current votes and
        # notes stay exactly where they are.
        "filters_doc": "filters",
        "candidates_doc": "candidates",
    },
    "audio": {
        "key": "audio",
        "title": "Audio Lab 2026",
        "subtitle": "Audio, Music & Speech AI",
        "spreadsheet_id": AUDIO_SPREADSHEET_ID,
        # Note the capital R -- Audio's tab really is named differently.
        "candidates_sheet": "Form Responses 1",
        "interviews_sheet": "Interviews",
        "map_lab": "music",
        # Audio was interviewed by Yurii and Oleh, in that order: the sheet's
        # "Interviewer 1" column is Yurii's, "Interviewer 2" is Oleh's. Order
        # matters -- score and comment headings are named from this mapping.
        "interviewers": {
            "yurii.chyrka@it-jim.com": "yurii",
            "oleh.leskiv@it-jim.com": "oleh",
        },
        "interviewer_names": {"yurii": "Yurii", "oleh": "Oleh"},
        "score_columns": ["yurii", "oleh"],
        "admin_email": "sofia.kuzmenko@it-jim.com",
        "rename_extra": AUDIO_RENAME_EXTRA,
        "multiselect_columns": AUDIO_MULTISELECT_COLUMNS,
        "filter_fields": AUDIO_FILTER_FIELDS,
        "candidates_doc": "candidates_audio",
        "filters_doc": "filters_audio",
    },
}


def get_lab(lab: str | None = None) -> dict:
    """Config for one lab; unknown keys fail loudly rather than silently."""
    key = lab or DEFAULT_LAB
    if key not in LABS:
        raise ValueError(f"unknown lab: {key!r} (known: {', '.join(LABS)})")
    return LABS[key]


def lab_rename(lab: str | None = None) -> dict:
    return {**RENAME, **get_lab(lab)["rename_extra"]}


def lab_filter_fields(lab: str | None = None) -> list:
    return get_lab(lab)["filter_fields"]


def lab_multiselect_columns(lab: str | None = None) -> list:
    return get_lab(lab)["multiselect_columns"]


_PLACEHOLDER_LINKS = {"", "n/a", "-", ".", "none"}
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_BARE_DOMAIN_RE = re.compile(r"^[a-z0-9.-]+\.[a-z]{2,}(/\S*)?$", re.IGNORECASE)


def normalize_link(raw: object) -> tuple[str | None, str | None]:
    """Return (url, display_text) for a free-text link field.

    Handles missing schemes ("www.linkedin.com/..."), values embedded in a sentence, quoted
    placeholders like '"N/A"', and genuinely non-URL free text (returned as display_text only).
    """
    if not isinstance(raw, str):
        return None, None
    text = raw.strip().strip("\"'").strip()
    if text.lower() in _PLACEHOLDER_LINKS:
        return None, None

    match = _URL_RE.search(text)
    if match:
        url = match.group(0).rstrip(").,;\"'")
        return url, text

    if _BARE_DOMAIN_RE.match(text):
        return f"https://{text}", text

    return None, text


def split_multiselect(value: object) -> list[str]:
    if not isinstance(value, str) or not value.strip():
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


def apply_filters(
    df: pd.DataFrame, filter_state: dict[str, list[str]], lab: str | None = None
) -> pd.DataFrame:
    mask = pd.Series(True, index=df.index)
    for column, _label, kind in lab_filter_fields(lab):
        selected = filter_state.get(column)
        if not selected:
            continue
        selected_set = set(selected)
        if kind == "single":
            mask &= df[column].isin(selected_set)
        else:
            mask &= df[column + "_list"].apply(lambda vals: bool(selected_set.intersection(vals)))
    return df[mask]


@st.cache_resource(show_spinner=False)
def _sheets_service():
    """Google Sheets API client, built once per server process.

    Credentials come from ``st.secrets["firebase"]`` -- the same service account used for
    Firestore, which just also needs Viewer access on the spreadsheet.
    """
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build

    info = dict(st.secrets["firebase"])
    if "private_key" in info:
        info["private_key"] = info["private_key"].replace("\\n", "\n")
    creds = Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"]
    )
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


@st.cache_data(ttl=_CACHE_TTL_SECONDS, show_spinner=False)
def _read_sheet(sheet_name: str, spreadsheet_id: str | None = None) -> pd.DataFrame:
    """Read one tab into a DataFrame, mimicking ``pd.read_excel``'s first-row-is-header behaviour.

    The API returns ragged rows -- trailing empty cells are omitted -- so short rows are padded
    to the header width before building the frame.
    """
    service = _sheets_service()
    result = (
        service.spreadsheets()
        .values()
        .get(
            spreadsheetId=spreadsheet_id or SPREADSHEET_ID,
            range=sheet_name,
            valueRenderOption="UNFORMATTED_VALUE",
        )
        .execute()
    )
    values = result.get("values", [])
    if not values:
        return pd.DataFrame()

    header, *rows = values
    width = len(header)
    padded = [row + [None] * (width - len(row)) for row in (r[:width] for r in rows)]
    df = pd.DataFrame(padded, columns=header)
    # Empty cells arrive as "" from the API; pandas logic downstream expects NaN.
    return df.replace("", pd.NA)


def load_candidates(lab: str | None = None) -> pd.DataFrame:
    cfg = get_lab(lab)
    rename = lab_rename(lab)
    multiselect = cfg["multiselect_columns"]

    df = _read_sheet(cfg["candidates_sheet"], cfg["spreadsheet_id"])
    df = df.rename(columns=rename)
    df = df.dropna(how="all")

    # If a form question gets reworded, its RENAME entry stops matching and the
    # column silently disappears. Backfill anything expected but absent so the
    # app degrades to blank cells instead of crashing on first access.
    expected = set(rename.values()) | set(SKILL_COLUMNS) | set(ORDINAL_MAPS) | set(multiselect)
    for col in expected:
        if col not in df.columns:
            df[col] = pd.NA

    df["full_name"] = (df["first_name"].fillna("") + " " + df["last_name"].fillna("")).str.strip()

    for col in SKILL_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    for col, mapping in ORDINAL_MAPS.items():
        df[col + "_score"] = df[col].map(mapping)

    for col in multiselect:
        df[col + "_list"] = df[col].apply(split_multiselect)

    df["candidate_key"] = df["email"].fillna("").str.strip().str.lower()

    # A few people submitted the form twice. Keep the most recent submission so
    # counts here match the applicant map (which is de-duplicated too) and one
    # person is not reviewed as two candidates.
    before = len(df)
    df = df.drop_duplicates(subset=["full_name"], keep="last")
    if len(df) != before:
        df = df.reset_index(drop=True)

    return df.reset_index(drop=True)


# Columns the app itself relies on from the Interviews tab. Everything else is
# passed through under whatever heading the sheet uses.
INTERVIEW_REQUIRED = {
    "first_name": ["first name"],
    "last_name": ["last name"],
    "average_score": ["average"],
    "interviewer_1_score": ["interviewer 1 score"],
    "interviewer_2_score": ["interviewer 2 score"],
}

# Interview columns the app does arithmetic on; always coerced to numbers.
INTERVIEW_NUMERIC_COLUMNS = ("average_score", "interviewer_1_score", "interviewer_2_score")

# Headings duplicated from the candidates sheet; dropped on merge so pandas
# doesn't suffix them with _x/_y.
INTERVIEW_OVERLAP = {"first_name", "last_name", "linkedin", "resume_link", "city"}

# Junk / duplicated columns from the Interviews tab that must NOT appear on the
# Scores table. In the sheet, applicants pasted resume links into the
# "Current location (city)" column, so city/resume/linkedin there hold shifted
# or meaningless values; the real versions already live on the candidates side.
# "Call day and video recording" is a date with the recording link buried in a
# cell hyperlink the API can't read, so it's dropped in favour of an in-app
# recording field (see storage / app.py).
# Internal names to hide outright.
INTERVIEW_SCORES_HIDE = {
    "city",
    "resume_link",
    "linkedin",
    "call_datetime",
    "interviewer_1",
    "interviewer_2",
}
# Substrings: any interview column whose (lowercased) heading contains one of
# these is also hidden. Catches the raw sheet headers that duplicate candidate
# data or hold shifted junk — LinkedIn, resume, location, and the call-day date
# with its un-extractable embedded recording link.
INTERVIEW_SCORES_HIDE_SUBSTRINGS = (
    "linkedin",
    "resume",
    "current location",
    "call day",
    "email",
)


def _is_hidden_interview_col(col: str) -> bool:
    if col in INTERVIEW_SCORES_HIDE:
        return True
    text = str(col).strip().lower()
    # Keep score and comment columns (they contain "interviewer" too).
    if col in ("interviewer_1_score", "interviewer_2_score"):
        return False
    if "comment" in text or "score" in text:
        return False
    # Hide the bare interviewer-NAME columns ("Interviewer 1 ", "Interviewer 2").
    if text in ("interviewer 1", "interviewer 2"):
        return True
    return any(s in text for s in INTERVIEW_SCORES_HIDE_SUBSTRINGS)


def _match_interview_columns(headers: list[str]) -> dict[str, str]:
    """Map sheet headings to the internal names the app needs.

    Matched on heading text rather than position. The Interviews tab is edited by
    hand and columns get inserted mid-sheet; positional naming silently shifts
    every field after the insertion point, so a comment ends up displayed as the
    hiring decision. Matching on the heading survives that.
    """
    resolved: dict[str, str] = {}
    for internal, patterns in INTERVIEW_REQUIRED.items():
        for header in headers:
            text = str(header).strip().lower().replace("\n", " ")
            if any(p in text for p in patterns):
                resolved[header] = internal
                break
    return resolved


def load_interviews(lab: str | None = None) -> pd.DataFrame:
    """Read the Interviews tab, keeping every column the sheet has.

    Only the handful of fields the app computes on are renamed; the rest keep
    their original headings so new columns appear in the UI without a code
    change and none of them can be mistaken for another.
    """
    cfg = get_lab(lab)
    df = _read_sheet(cfg["interviews_sheet"], cfg["spreadsheet_id"])
    if df.empty:
        return pd.DataFrame(columns=["first_name", "last_name", "full_name"])

    df = df.rename(columns=_match_interview_columns(list(df.columns)))

    for col in ("first_name", "last_name"):
        if col not in df.columns:
            df[col] = pd.NA

    # Scores are typed by hand and the Average cell is a formula, so this column
    # can hold blanks or spreadsheet errors ("#DIV/0!") alongside real numbers.
    # Left as text, an object-dtype column makes .mean() raise TypeError and
    # takes the whole tab down; coercing turns the bad cells into blanks.
    for col in INTERVIEW_NUMERIC_COLUMNS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["first_name", "last_name"], how="all")
    df["full_name"] = (df["first_name"].fillna("") + " " + df["last_name"].fillna("")).str.strip()
    return df.reset_index(drop=True)


# Set by load_merged(); read by interview_display_columns() so the scores tab
# knows which columns came from the Interviews sheet.
_interview_data_columns: dict[str, set[str]] = {}


def load_merged(lab: str | None = None) -> pd.DataFrame:
    """Candidates joined with whatever the Interviews tab currently holds.

    Every interview column is carried through rather than a fixed subset, so
    columns added to the sheet show up in the app without a code change. Columns
    that duplicate candidate fields (name, links, city) are dropped to avoid
    pandas appending _x/_y suffixes on the join.
    """
    lab_key = get_lab(lab)["key"]
    candidates = load_candidates(lab_key)
    interviews = load_interviews(lab_key)

    # Audio's Interviews tab mirrors the entire application form, so anything
    # that is a known form question is dropped here -- those values already come
    # from the candidates side, and carrying them through would fill the Scores
    # table with duplicated columns.
    form_headings = set(lab_rename(lab_key))

    interview_cols = ["full_name"] + [
        c
        for c in interviews.columns
        if c not in INTERVIEW_OVERLAP
        and c not in form_headings
        and not _is_hidden_interview_col(c)
        and c != "full_name"
    ]

    # Remember which columns are interview-side so the scores tab can list them
    # without re-reading the sheet.
    _interview_data_columns[lab_key] = set(interview_cols)

    merged = candidates.merge(interviews[interview_cols], on="full_name", how="left")
    return merged





def interview_display_columns(df: pd.DataFrame, lab: str | None = None) -> list[str]:
    """Curated, ordered columns for the Interview Scores table.

    An explicit allow-list rather than "everything from the sheet": several sheet
    columns hold shifted or duplicated data (see INTERVIEW_SCORES_HIDE) that only
    confuses the scores view. New meaningful columns can be added here when the
    team introduces them.
    """
    preferred = [
        "full_name",
        "interviewer_1_score",
        "interviewer_2_score",
        "average_score",
        "Advise CV Learing Path",
        "comment_1",
        "comment_2",
        "Comment \nInterviewer 1\n(Sofiia)",
        "Comment \nInterviewer 2\n(Yura)",
        "decision",
        "Decision",
    ]
    # Keep only those that exist, in this order, without duplicates.
    known = _interview_data_columns.get(get_lab(lab)["key"], set())
    ordered = [c for c in preferred if c in df.columns]
    # Append any other interview-side columns that aren't explicitly hidden,
    # so a newly added column still surfaces instead of silently vanishing.
    for c in df.columns:
        if (
            c in known
            and c not in ordered
            and not _is_hidden_interview_col(c)
            and c != "full_name"
        ):
            ordered.append(c)
    return ordered


def explode_counts(df: pd.DataFrame, list_col: str) -> pd.Series:
    return df[list_col].explode().dropna().value_counts()
