# Task Log

## 2026-06-27 — Initial Strava analysis app

- Created a Python 3.11+ FastAPI project.
- Added SQLite activity and OAuth-token storage.
- Implemented Strava OAuth login, scope validation, short-lived token refresh,
  rotated refresh-token persistence, and CSRF state handling.
- Added Strava API pagination from 2026-01-01, run-only filtering, API sync, and
  duplicate-safe upserts.
- Added detailed run, split, lap, stream, raw JSON, and aggregate exports.
- Added beginner-friendly derived metrics and documented conservative
  classification rules.
- Added automated synthetic tests.

## 2026-06-27 — Free offline archive import

- Made Strava API use optional.
- Added Strava bulk export ZIP import through CLI and API.
- Added duplicate-aware `activities.csv` parsing.
- Added FIT parsing with `fitdecode`.
- Added GPX and TCX parsing with Python XML tools.
- Added gzip support, per-member size limits, corrupt-file isolation, and
  duplicate-safe reimports.
- Added ZIP import instructions for users without API access/subscription.

## 2026-06-28 — Gemini/none analysis

- Added provider boundary with only `gemini` and `none`.
- Added deterministic fallback and strict Pydantic output validation.
- Added anonymized training payload generation.
- Ensured `ai_safe_payload.json` is written before external requests.
- Excluded routes, GPS, locations, IDs, names, notes, devices, gear, and raw
  streams from Gemini.
- Added deterministic summary, AI analysis, and beginner-plan JSON outputs.
- Added CLI `analyze` and API `POST /analysis`.

## 2026-06-28 — Model selection and mobile-first dashboard

- Verified current official Gemini models and free-tier pricing.
- Changed the default to stable `gemini-3.5-flash`.
- Retained `gemini-2.5-flash` as the automatic unavailable-model fallback.
- Added CLI `list-models`.
- Added Jinja server-rendered dashboard templates and responsive CSS.
- Added dashboard, import, activities, activity detail, analysis, plan, and
  settings pages.
- Changed `/` to redirect to `/dashboard`; API status moved to `/api/status`.
- Added mobile route and structured-output regression tests.
- Test suite status at this point: 23 passing tests.

## 2026-06-28 — AI coaching, premium UI, and PWA overhaul

- Added walk-break trend, personal-best, current-capability, and consistency
  analysis.
- Replaced the flat coaching response with validated structured sessions,
  weekly plans, walk strategies, progressive milestones, and safety watch
  points.
- Expanded the privacy-safe Gemini payload with trend and capability data while
  preserving the GPS/route/location exclusions.
- Rebuilt the deterministic fallback into a data-driven one-, two-, or
  three-session plan instead of repeating identical sessions.
- Added local training-frequency and beginner-goal preferences.
- Rebuilt all dashboard pages with a premium dark-first responsive design,
  light-mode toggle, glass cards, charts, counters, animations, and loading
  states.
- Added PWA manifest, service worker, offline page, Apple metadata, and original
  192/512/1024-pixel application icons.
- Added PWA routes and package-data declarations.
- Expanded regression coverage for the schema, analysis metrics, preferences,
  manifest, service worker, and icons.
- Test suite status: 29 passing tests.

## 2026-06-28 — Responsive spacing repair

- Added one shared page-flow grid so top-level cards and sections keep a
  consistent gap on every dashboard page.
- Added narrow-phone wrapping rules for action buttons, activity headings,
  settings rows, split rows, long statistics, and bottom navigation.
- Verified dashboard, activities, a real activity detail, insights, plan,
  import, and settings at a 390 × 844 iPhone-style viewport.
- Confirmed there is no horizontal page overflow and no visible top-level card
  overlap.

## Commands

### Open the project

```powershell
cd "C:\Users\aliga\Documents\Codex\2026-06-27\hi"
```

### First-time installation

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

Do not overwrite an existing `.env`; edit it instead.

### Run the local web app

```powershell
.\.venv\Scripts\Activate.ps1
python -m uvicorn app.main:app --reload
```

Open <http://localhost:8000/dashboard>.

### Import Strava ZIP

Browser: open <http://localhost:8000/import>.

CLI:

```powershell
python -m app.cli import-zip "$HOME\Downloads\your-strava-export.zip"
```

### Run analysis

```powershell
python -m app.cli analyze
```

### List Gemini models available to the configured key

```powershell
python -m app.cli list-models
```

### Rebuild exports without contacting Strava

```powershell
python -m app.cli export
```

### Run tests

```powershell
python -m pytest -q
```
