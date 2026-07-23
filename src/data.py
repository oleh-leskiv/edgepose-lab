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
SPREADSHEET_ID = "16Hv_tvcrzMi-Qbm8kTBfIQPzd4-gg7MCz394a7LfJQ8"
CANDIDATES_SHEET = "Form responses 1"
INTERVIEWS_SHEET = "Interviews"

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


def apply_filters(df: pd.DataFrame, filter_state: dict[str, list[str]]) -> pd.DataFrame:
    mask = pd.Series(True, index=df.index)
    for column, _label, kind in FILTER_FIELDS:
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
def _read_sheet(sheet_name: str) -> pd.DataFrame:
    """Read one tab into a DataFrame, mimicking ``pd.read_excel``'s first-row-is-header behaviour.

    The API returns ragged rows -- trailing empty cells are omitted -- so short rows are padded
    to the header width before building the frame.
    """
    service = _sheets_service()
    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=SPREADSHEET_ID, range=sheet_name, valueRenderOption="UNFORMATTED_VALUE")
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


def load_candidates() -> pd.DataFrame:
    df = _read_sheet(CANDIDATES_SHEET)
    df = df.rename(columns=RENAME)
    df = df.dropna(how="all")
    df["full_name"] = (df["first_name"].fillna("") + " " + df["last_name"].fillna("")).str.strip()

    for col in SKILL_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    for col, mapping in ORDINAL_MAPS.items():
        df[col + "_score"] = df[col].map(mapping)

    for col in MULTISELECT_COLUMNS:
        df[col + "_list"] = df[col].apply(split_multiselect)

    df["candidate_key"] = df["email"].fillna("").str.strip().str.lower()
    return df.reset_index(drop=True)


def load_interviews() -> pd.DataFrame:
    df = _read_sheet(INTERVIEWS_SHEET)
    df.columns = [
        "first_name",
        "last_name",
        "linkedin",
        "resume_link",
        "city",
        "call_datetime",
        "interviewer_1",
        "interviewer_1_score",
        "interviewer_2",
        "interviewer_2_score",
        "average_score",
        "comment_1",
        "comment_2",
        "decision",
    ]
    df = df.dropna(subset=["first_name", "last_name"], how="all")
    df["full_name"] = (df["first_name"].fillna("") + " " + df["last_name"].fillna("")).str.strip()
    return df.reset_index(drop=True)


def load_merged() -> pd.DataFrame:
    candidates = load_candidates()
    interviews = load_interviews()
    interview_cols = [
        "full_name",
        "interviewer_1_score",
        "interviewer_2_score",
        "average_score",
        "comment_1",
        "comment_2",
        "decision",
        "call_datetime",
    ]
    merged = candidates.merge(interviews[interview_cols], on="full_name", how="left")
    return merged


def explode_counts(df: pd.DataFrame, list_col: str) -> pd.Series:
    return df[list_col].explode().dropna().value_counts()
