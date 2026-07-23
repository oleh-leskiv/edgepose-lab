# EdgePose Lab 2026 — Candidate Explorer

A Streamlit app for reviewing R&D trainee candidates from the EdgePose Lab 2026 Google Form
responses, used by the hiring team (Sofiia, Oleh, Yurii). Deployed on Streamlit Community Cloud;
candidate data is read live from Google Sheets, and the team's votes and notes live in
Firestore.

## For a future Claude/Anthropic session picking this up

Context you won't get just from skimming the code:

- **Data source**: `data/It-Jim EdgePose Lab 2026 (Responses).xlsx`, two sheets — `Form
  responses 1` (92 applicants, one row per form submission) and `Interviews` (sparse — only 2
  candidates interviewed so far, filled in manually by the team as interviews happen).
- **`src/data.py`**: reads both tabs from the live Google Sheet (service account, read-only)
  and cleans them. `RENAME` maps the long Google Form question
  text to snake_case columns. `SKILL_COLUMNS` are the four 1–10 self-ratings (Python, C++,
  linear algebra, stats). `MULTISELECT_COLUMNS` are comma-separated checkbox answers, exploded
  into parallel `<col>_list` columns. `FILTER_FIELDS` drives the Filter Pool tab.
  `normalize_link()` exists because the raw linkedin/resume columns are messy free text — missing
  `https://`, quoted `"N/A"` literals, URLs embedded mid-sentence, or genuinely no URL at all; it
  was added after a real bug where a bare `www.linkedin.com/...` rendered as a broken relative
  link. `apply_filters()` is OR within a field, AND across fields. `candidate_key` (lowercased,
  stripped email) is the **stable identity used everywhere** instead of row position — this
  matters because the source `.xlsx` gets replaced periodically and rows can reorder/insert.
- **`src/storage.py`**: durable state in Firestore, keyed by `candidate_key`. Holds saved Filter Pool selections and each
  interviewer's pool flag/note per candidate. Per-candidate writes use `merge=True` so two
  people voting on different candidates at the same time don't clobber each other.
  **Deliberately does not persist "who is using the app" anywhere on the server** — an earlier version remembered the last-typed email in a file under
  the user's home directory, but that breaks once the app is one shared process serving multiple
  people (whoever typed last becomes everyone else's default). Don't reintroduce that without
  rethinking it for the shared-server case.
- **`src/app.py`**: single-file Streamlit UI. Tab order: `Overview & Stats | Filter Pool |
  Candidate Explorer | Interview Pool | Interview Scores`. "Vs. Average" is *not* a top-level tab
  — it's a nested sub-tab inside Candidate Explorer, and it always averages over the **full**
  unfiltered candidate set (a stable baseline regardless of active filters), by design.
  `require_password()` runs first and gates the whole app on `st.secrets["app_password"]`; if
  that secret isn't set, the app runs with no gate (keeps local/dev usage friction-free). The
  sidebar's "your email" field resolves per-browser-session (via `st.session_state`, re-entered
  each session on purpose) into `current_interviewer_key` / `is_admin`, which
  `render_pool_controls()` uses to enable/disable each interviewer's own checkbox+note (Sofiia is
  admin and can edit all three).
- **`scripts/run_public.sh`**: runs `streamlit run` plus `cloudflared tunnel --url
  http://localhost:8501` (a Cloudflare *quick* tunnel — anonymous, no account, but the
  `https://*.trycloudflare.com` URL is random and changes every run).

### Why the deployment looks the way it does

Originally this ran on one person's laptop behind a Cloudflare quick tunnel, with state in a
local JSON file and a shared password. That was a deliberate tradeoff at the time, but it meant
the app only worked while that laptop was on, and the tunnel URL changed on every restart. It
now runs on Streamlit Community Cloud at a fixed URL instead. Three things had to change:

- **Candidate data** moved from a committed `.xlsx` to reading the live Google Sheet through a
  read-only service account. Applications arrive daily, so a committed export went stale within a
  day; and the sheet holds names, phone numbers, Telegram handles and resume links for ~100 real
  applicants, which has no business sitting in a git repository.
- **Team votes and notes** moved from `data/pool_state.json` to Firestore. Community Cloud's
  filesystem is ephemeral, so a JSON file there would be wiped on every redeploy — this was the
  exact reason Community Cloud was rejected in the first version.
- **Credentials** live in `st.secrets`, never in the repo. `.gitignore` blocks `*.json` outright
  so a downloaded service-account key can't be committed by accident.

Still open, worth revisiting: auth is a single shared password. Now that the URL is stable, real
Google sign-in restricted to `@it-jim.com` is finally possible (it was blocked before because
OAuth needs a fixed redirect URL). That would be a strict upgrade.

## How to use

### First-time setup

```bash
uv sync
```

Then create `.streamlit/secrets.toml` from the example and fill in the service-account fields
from the Firebase JSON key (Firebase Console -> Project settings -> Service accounts ->
Generate new private key). That same service account also needs Viewer access on the candidate
spreadsheet — share the sheet with its `client_email`.

### Migrating old votes and notes (one-off)

If you have a `pool_state.json` from the laptop-hosted version:

```bash
uv run streamlit run scripts/migrate_state.py
```

Point it at the file, check the counts it reports, press the button. Safe to re-run.

### Run it locally

```bash
uv run streamlit run src/app.py
```

Opens at `http://localhost:8501`.

### Deployed version

Lives on Streamlit Community Cloud, deployed from the `main` branch of this repo. Pushing to
`main` redeploys automatically. Secrets are set in the app's settings on Community Cloud, not in
a file — paste the same content as `.streamlit/secrets.toml`.

### Using the app itself

- **Sidebar**: enter your `it-jim.com` email once per browser session — this is how the app
  knows who you are for the Interview Pool feature. Recognized: `sofia.kuzmenko@it-jim.com`
  (Sofiia, can edit anyone's pool decision), `oleh.leskiv@it-jim.com` (Oleh),
  `yurii.chyrka@it-jim.com` (Yurii).
- **Overview & Stats**: aggregate charts across all candidates.
- **Filter Pool**: narrow the pool by any combination of fields (English level, DL framework,
  domain interest, CV tasks, etc.) — multiple values within one field are OR'd, different fields
  are AND'd together. "Save filters" persists your selection for next time; "Reset filters"
  clears it.
- **Candidate Explorer**: browse the (filtered) pool one person at a time — full profile, then
  your interview-pool vote (checkbox + a reason if you're against), then "Compare" and "Vs.
  Average" sub-tabs.
- **Interview Pool**: everyone's votes and notes in one table, with vote-count tallies.
- **Interview Scores**: results from the `Interviews` sheet (interviewer scores, decision) —
  populated as interviews actually happen.

### New applications

Nothing to do — the app reads the live Google Sheet, cached for 5 minutes per server process.
New form submissions show up on the next load after that. Filters, votes and notes are keyed by
candidate email, so they stay attached to the right person even as rows shift around. A
candidate whose email drops out of the sheet simply stops appearing; their entry stays
harmlessly unused in Firestore.

## Project layout

```
src/
  app.py       Streamlit UI — all tabs
  data.py      read/clean the Google Sheet, filtering, link normalization
  storage.py   Firestore persistence for filters + interview-pool decisions
  main.py      `uv run python src/main.py` — thin launcher, same as streamlit run
scripts/
  migrate_state.py   one-off import of an old pool_state.json into Firestore
.streamlit/
  secrets.toml.example   template — copy to secrets.toml (gitignored)
```

No candidate data lives in this repo. The spreadsheet is read at runtime from Google Sheets,
and team votes/notes live in Firestore.
