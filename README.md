# Runstead: Strava Beginner Running Analysis

A local FastAPI app that imports a Strava account-download ZIP or connects to
one Strava account with OAuth 2.0. It stores 2026 running activities in SQLite
and provides a mobile-first dashboard plus AI-friendly CSV/JSON exports. The
offline ZIP method requires no API credentials or Strava subscription. It is
designed for a new runner using run/walk breaks.

## What kind of app is this?

This started as CLI commands, a FastAPI backend, an API documentation page, and
CSV/JSON exports. **This is not yet a native mobile app.**

It now also has a mobile-first web dashboard and Progressive Web App (PWA)
shell that works in a phone or desktop browser. It can be added to an iPhone
Home Screen when served securely, but it is still a local Python web app—not a
native App Store or Android application.

- **CLI:** PowerShell commands such as `python -m app.cli import-zip` for direct
  local operation.
- **API:** machine-readable FastAPI endpoints for future integrations.
- **API docs:** an interactive developer/testing page at
  <http://localhost:8000/docs>.
- **Dashboard:** the phone-friendly user interface at
  <http://localhost:8000/dashboard>.
- **PWA:** installable browser experience with an app icon, standalone display,
  and cached interface shell when served over HTTPS.
- **Not included:** a native App Store/Play Store application, background phone
  syncing, or direct Mi Fitness integration.

### Run the backend and dashboard

```powershell
cd "C:\Users\aliga\Documents\Codex\2026-06-27\hi"
.\.venv\Scripts\Activate.ps1
python -m uvicorn app.main:app --reload
```

Then open <http://localhost:8000/dashboard>. The available pages are:

- `/dashboard` — totals, trends, recent runs, and safety flags
- `/import` — upload a Strava export ZIP
- `/activities` — all imported runs
- `/activity/{id}` — splits, laps, streams, HR, and beginner interpretation
- `/analysis` — deterministic and optional Gemini analysis
- `/plan` — conservative one-, two-, or three-session beginner plan
- `/settings` — training frequency/goal preferences and safe configuration
  status without exposing secrets

The interface defaults to a dark premium theme. Use the moon/sun button to
switch themes; the browser remembers the choice. Charts, counters, plan tabs,
import feedback, large touch targets, and bottom navigation use plain
JavaScript—there is no React build step.

To open it from a phone on the same trusted home Wi-Fi, run:

```powershell
python -m uvicorn app.main:app --reload --host 0.0.0.0
```

Then open `http://YOUR-PC-IP:8000/dashboard` on the phone. Use `ipconfig` in
PowerShell to find the PC's IPv4 address. This local app has no login screen, so
do not expose that server to public Wi-Fi or the internet.

### Add it to an iPhone Home Screen

The manifest, 192/512-pixel icons, Apple touch icon, standalone metadata,
service worker, and offline shell are included. On iPhone, open the secure app
URL in Safari, tap **Share**, then **Add to Home Screen**.

There is one web-platform limitation: service workers require a secure context.
`http://localhost:8000` is accepted for development on the same computer, but
`http://YOUR-PC-IP:8000` opened from an iPhone is not localhost and is not
secure. The dashboard itself can still load over trusted home Wi-Fi, but full
PWA installation/offline caching requires an HTTPS URL. Do not expose the app
publicly just to solve this; add local HTTPS or a private authenticated tunnel
as a separate deployment step.

Reference: [MDN Service Worker API](https://developer.mozilla.org/docs/Web/API/Service_Worker_API).

## Optional Gemini analysis

AI is optional. The default is local deterministic analysis, which sends
nothing over the internet:

```dotenv
AI_PROVIDER=none
GEMINI_API_KEY=
AI_MODEL=gemini-3.5-flash
AI_FALLBACK_MODEL=gemini-2.5-flash
```

To enable Gemini, create an API key in
[Google AI Studio](https://aistudio.google.com/app/apikey) and set:

```dotenv
AI_PROVIDER=gemini
GEMINI_API_KEY=your_key_here
AI_MODEL=gemini-3.5-flash
AI_FALLBACK_MODEL=gemini-2.5-flash
```

Never commit `.env`. If `AI_PROVIDER=gemini` but the key is blank, the app
automatically uses deterministic analysis. Only `gemini` and `none` are valid
providers. Groq and Ollama are intentionally not implemented yet, although the
provider boundary makes a later addition straightforward.

### Recommended free model

As checked against Google's official model and pricing pages on 2026-06-28,
the recommended default is `gemini-3.5-flash`:

- Google describes it as its most intelligent and capable Flash model.
- It is stable and generally available, rather than preview/experimental.
- Its standard Gemini Developer API tier lists input and output as free of
  charge, subject to the free account's rate limits and availability.
- It supports the strict structured JSON output used by this app.

The alternatives are less suitable as the default:

- `gemini-3-flash-preview` is free but is a preview model; Google lists
  `gemini-3.5-flash` as its stable replacement.
- `gemini-3.1-flash-lite` has a free tier and structured outputs, but is
  optimized for cost-efficient, high-volume, simpler processing rather than
  maximum Flash quality.
- `gemini-2.5-flash` has a free tier and structured outputs, but is older and
  has an announced 2026-10-16 shutdown date. The app retains it as an automatic
  fallback if the configured newer model returns “not found/unavailable.”
- Gemini Pro is not used by default. Google's Gemini 3 guide explicitly says
  the Pro preview has no free Gemini API tier.

Free-tier availability and limits can change and can vary by account or region.
On the free tier, Google states submitted content may be used to improve its
products; the app therefore sends only `ai_safe_payload.json`.

Official references:

- [Gemini 3.5 Flash model](https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash)
- [Gemini API pricing](https://ai.google.dev/gemini-api/docs/pricing)
- [Gemini model deprecations](https://ai.google.dev/gemini-api/docs/deprecations)

To query the models currently available to your own API key:

```powershell
python -m app.cli list-models
```

This command lists models supporting `generateContent`. It cannot determine
pricing from the catalog response, so verify free-tier terms on the official
pricing page before choosing a different model.

After importing activities, run:

```powershell
python -m app.cli analyze
```

Or start the server and call `POST /analysis` from
<http://localhost:8000/docs>. The command creates:

- `deterministic_summary.json`
- `beginner_training_plan.json`
- `ai_safe_payload.json`
- `ai_analysis.json`

`ai_safe_payload.json` is written before any Gemini request and contains the
exact data Gemini receives. It includes totals, weekly/monthly distance,
training trends, latest-session distance/duration/pace, walking-break estimates
and trends, personal bests, current capabilities, consistency, available
heart-rate summaries, safety flags, preferences, and the configured
plain-language user context.

It excludes activity names and notes, activity IDs, exact timestamps, GPS
coordinates, start/end points, city/country, map polylines, routes, device and
gear identifiers, and raw streams. There is no setting to bypass this privacy
filter. If Gemini is unavailable, returns invalid JSON, or reaches a rate
limit, the app writes and returns the deterministic result instead of failing.
All analysis is training information—not medical advice.

The validated coaching result is deliberately structured rather than
free-form. It includes progress highlights, walk-break analysis, risks, a
detailed next week with per-session distance/duration/walk strategy, a
progressive four-week plan, milestones, watch points, and questions. The
deterministic fallback uses the same structure and builds different sessions
from the runner's actual recent distance and capabilities.

Gemini calls use its structured-output JSON mode following the official
[Gemini structured output documentation](https://ai.google.dev/gemini-api/docs/structured-output).

## What it does

- Imports `activities.csv` and original `.fit`, `.fit.gz`, `.gpx`, `.gpx.gz`,
  `.tcx`, and `.tcx.gz` files directly from a Strava account export ZIP.
- Requests only `activity:read_all` and validates that it was granted.
- Stores OAuth access and refresh tokens in SQLite; refresh tokens are replaced
  whenever Strava rotates them.
- Fetches activities from 2026-01-01 through the current time and keeps only
  `Run`, `TrailRun`, and `VirtualRun`.
- Uses the Strava activity ID as the SQLite primary key, so syncs cannot create
  duplicates.
- Downloads DetailedActivity, metric splits, laps, and the `time`, `distance`,
  `velocity_smooth`, `heartrate`, `cadence`, `altitude`, `moving`,
  `grade_smooth`, and `latlng` streams when available.
- Continues when optional heart rate, cadence, calories, gear, splits, laps, or
  streams are absent.
- Regenerates all exports after every sync. Existing activities are skipped by
  default to conserve Strava API requests; use `--refresh-existing` after
  editing an old Strava activity.

## Simplest setup: no Strava API or subscription

### 1. Request your Strava account archive

1. Sign in at <https://www.strava.com> in a web browser.
2. Open **Settings**, then **My Account**.
3. Under **Download or Delete Your Account**, choose **Get Started**.
4. Select **Request your archive** (the wording may appear as **Request
   download**).
5. Strava emails you a link when the ZIP is ready. Download it but do not
   extract it.

Strava documents this process in its
[Bulk Export guide](https://support.strava.com/en-us/articles/15401919-exporting-your-data-and-bulk-export).

### 2. Install the app

Open PowerShell:

```powershell
cd "C:\Users\aliga\Documents\Codex\2026-06-27\hi"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

No Strava Client ID or Client Secret is required for ZIP imports.

### 3. Import the ZIP

Use the real path to the downloaded ZIP:

```powershell
python -m app.cli import-zip "$HOME\Downloads\your-strava-export.zip"
```

The command imports only 2026 `Run`, `TrailRun`, and `VirtualRun` activities,
updates duplicate activity IDs, and creates the output files in `.\exports`.
You can run the command again when you request a newer archive.

The browser dashboard upload is also available:

```powershell
python -m uvicorn app.main:app --reload
```

Open <http://localhost:8000/import>, choose the Strava ZIP, and select **Import
and rebuild exports**. Developers can still use `POST /import/zip` from
<http://localhost:8000/docs>.

### How offline parsing works

- `activities.csv` supplies the activity ID, name, type, date, and fallback
  summary fields.
- FIT is preferred when present and can supply timestamps, GPS, speed, heart
  rate, cadence, altitude, session totals, and laps.
- TCX can supply GPS, heart rate, cadence, speed, altitude, and laps.
- GPX can supply timestamps, GPS, elevation, and accessory data included by the
  exporter.
- If a sensor value is absent, the output stays null. The importer never
  invents heart rate, cadence, or zones.
- Corrupt individual activity files are reported in `parse_errors`; other
  activities continue importing.
- If the archive has only `activities.csv`, set
  `STRAVA_EXPORT_DISTANCE_UNIT=km` (default) or `mi` in `.env` to match that
  CSV. Original activity files take precedence when available.

## Optional API mode

The original OAuth/API mode remains available. Skip this entire section when
using ZIP imports.

### 1. Create the Strava developer app

1. Sign in to Strava and open <https://www.strava.com/settings/api>.
2. Create an application. For local use, set **Authorization Callback Domain**
   to `localhost`.
3. Copy the Client ID and Client Secret. Never commit or share the secret.
4. The exact callback URL used by this app is
   `http://localhost:8000/auth/callback`.

Strava's official references are the
[OAuth documentation](https://developers.strava.com/docs/authentication/) and
[API reference](https://developers.strava.com/docs/reference/).

### 2. Install and configure

Python 3.11 or newer is recommended.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

Edit `.env`:

```dotenv
STRAVA_CLIENT_ID=your_client_id
STRAVA_CLIENT_SECRET=your_client_secret
STRAVA_REDIRECT_URI=http://localhost:8000/auth/callback
DATABASE_PATH=./strava.db
EXPORT_DIR=./exports
```

`.env`, SQLite files, and exports are ignored by Git. OAuth tokens are sensitive
even though they are in SQLite; do not publish the database.

### 3. Connect and sync

Start the server:

```powershell
python -m uvicorn app.main:app --reload
```

Open <http://localhost:8000/auth/login>, approve all requested activity access,
and let Strava redirect back to the app. Then sync in either way:

```powershell
# CLI: normal daily/manual sync
python -m app.cli sync

# API endpoint
Invoke-RestMethod -Method Post http://localhost:8000/sync
```

Useful alternatives:

```powershell
# Refresh old activities too (more Strava requests)
python -m app.cli sync --refresh-existing

# Rebuild files without contacting Strava
python -m app.cli export
```

For unattended daily operation, schedule the normal CLI sync with Windows Task
Scheduler after completing OAuth once. The CLI automatically refreshes an
expiring access token. Offline ZIP imports are manual because Strava must first
prepare and provide a new archive.

## Output files

Files are written to `EXPORT_DIR`:

- `strava_2026_runs.csv`: one row per run, including source fields, rolling
  totals, beginner classifications, and safety flags.
- `strava_2026_splits.csv`: one row per metric split.
- `strava_2026_laps.csv`: one row per lap.
- `strava_2026_streams_summary.csv`: per-activity stream statistics.
- `strava_2026_raw.json`: untouched DetailedActivity objects for future use.
- `strava_2026_summary.json`: totals, best/longest runs, weekly/monthly totals,
  and a progression trend.

CSV list/dictionary values are JSON encoded. Missing Strava values stay empty
(null in JSON). `pace_min_per_km` is a `mm:ss` display value; use
`pace_sec_per_km` for calculations. The fastest kilometre is estimated from
stream samples and is not an official Strava best effort.
`shoe_distance_before_run_km` is cumulative distance observed for that gear
within this 2026 dataset; the API does not provide historical shoe totals at
each activity date.

For offline imports, `strava_2026_raw.json` contains the normalized activity
representation assembled from the archive rather than an API DetailedActivity.
All original source data remains in your Strava ZIP.

## Beginner classification rules

These fields are deliberately cautious and can be changed later through `.env`.
They are analysis hints—not workout prescriptions.

- **Run/walk:** A walk break is a stream segment lasting at least
  `MIN_WALK_BREAK_SEC` (15 seconds by default) where Strava marks the athlete as
  not moving or smoothed speed is at most `WALK_SPEED_THRESHOLD_MPS` (1.8 m/s).
  Run/walk duration fields are null when usable time and speed streams are
  absent. GPS noise and hills can make these estimates imperfect.
- **Easy:** With `MAX_HR`, average HR at or below 75% of max is considered easy.
  Without it, a session must not be clearly fast and is compared with the
  runner's recent pace; early run/walk sessions can be provisionally easy.
- **Fast:** Requires at least three prior runs in 30 days and a pace at least
  `FAST_PACE_IMPROVEMENT_PERCENT` (10% by default) faster than the recent median.
- **Long:** One of the longest easy sessions that week—by default at least 80%
  of that week's longest distance. It never means “run hard.”
- **Possible intervals:** Requires `interval`, `repeats`, or `fartlek` in the
  activity name, or at least three clearly separated stream segments lasting
  30+ seconds at 25% above the session's median moving speed. Tempo sessions are
  not inferred.
- **Progress test:** Requires `test`, `benchmark`, `time trial`, or `race` in
  the name, plus a prior run within 10% of its distance, the same sport/trainer
  setting, and at least a 3% pace improvement.
- **Progression trend:** Compares distance in the newest four data-bearing ISO
  weeks with the previous four. Under five weeks returns `not_enough_data`.
- **Sudden volume:** Flags a rolling seven-day distance at least 30% and 5 km
  above the preceding seven days. Both thresholds are configurable.
- **Too many hard sessions:** Flags more than `MAX_HARD_SESSIONS_7D` (default 2)
  hard sessions in seven days. Without max HR, “hard” relies on a clearly faster
  recent-relative pace and is intentionally uncommon.
- **Suggestions:** After a hard/long/flagged session, the only suggested next
  type is a gentle recovery walk. Otherwise it suggests easy run/walk. Pain,
  unusual fatigue, illness, or medical guidance always overrides the app.

`session_type_guess` is one of `easy_run_walk`, `long_run_walk`,
`fast_short_run`, `possible_intervals`, `recovery_walk`, or `unknown`.
`effort_guess` is `easy`, `moderate`, `hard`, or `unknown`.

## Optional heart-rate zones

Zones remain null unless you explicitly configure five upper bounds:

```dotenv
MAX_HR=190
HR_ZONE_BOUNDS=120,140,155,170,190
```

The app does not guess maximum HR or zones. Stream zone totals are seconds in
each configured zone. They are descriptive and are not the main basis for
training suggestions.

## Tests

```powershell
pytest
```

The tests use synthetic data and require neither Strava secrets nor network
access.

## Continuing in a new Codex chat

If Codex chat context becomes too long, start a new Codex chat in the same
workspace folder and ask Codex to read `PROJECT_CONTEXT.md`, `ROADMAP.md`,
`TASK_LOG.md`, `DECISIONS.md`, and `README.md` before continuing.
