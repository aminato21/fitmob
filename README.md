# Runstead

A private, mobile web app for a beginner run/walk routine. Import your Strava
ZIP, confirm which imported runs completed planned sessions, see the next
unfinished session, and ask the coach questions. The default goal is consistency
with one session per week; Settings offers one to three. The sequence is flexible
and does not assign weekdays.

## Use it locally

Requires Python 3.11 or newer. From this project directory in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.lock
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000/dashboard>. The local example uses local planning
and localhost access. Before serving the app beyond localhost, enable the
existing owner login with `AUTH_ENABLED=true` and retain a stable secret in
`AUTH_SECRET_KEY`. Neither credentials nor a database are included in this package.

The development environment prepared for this delivery is outside the project,
at `../../work/runstead-venv`. Its preview uses port 8765 and synthetic runs.

## Your phone workflow

1. Import a Strava account-download ZIP at **More → Import**.
2. Select **Review completed sessions**, choose an imported run for a session,
   and confirm. Importing alone completes nothing. A run can complete one session.
3. Dashboard and Plan show the earliest unfinished session, its distance ceiling,
   duration, walk strategy, and explanation. **Review or undo links** reverses a match.
4. Open **Coach**, send a question, or start a new conversation. Conversations
   are saved on the existing SQLite database. Delete controls remove their messages.
5. Ask for a change or generate a proposal from Plan. Review it, then choose
   **Accept this plan**. Your old completed sessions remain in history; its
   remaining sessions are archived. Saved proposals can be reopened from Plan.

New imports, corrected records, check-ins, preferences, and completion links can
make a plan stale. It remains active until you accept a valid replacement. A
proposal whose data or base plan changed is rejected; proposals expire after
seven days. Reimporting identical data updates upload time without changing the
content revision. Missing uploads are unknown training, not proof of inactivity.

The PWA can be added to the iPhone Home Screen from Safari on the HTTPS app URL.
Only public assets and the offline screen are cached. Private pages, replies,
and API responses use network requests and `Cache-Control: no-store, private`.
Offline mode does not display cached health or conversation data.

## Gemini and privacy

AI is optional. `AI_PROVIDER=none` sends no coach requests. To use the existing
Google configuration, set `AI_PROVIDER=gemini` and retain the existing
`GEMINI_API_KEY`; the configured default is `AI_MODEL=gemini-3.5-flash`.
This work does not change Google billing or provision another service.
Google documents [structured outputs for this model](https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash)
and the [JSON Schema API](https://ai.google.dev/gemini-api/docs/structured-output).

Each generation makes one provider attempt with a 30-second total deadline.
There is no preview-model cascade or automatic alternative-model request.
Availability errors, rate limits, invalid JSON, or a timeout preserve the active
plan and conversation. Chat explains the failure and offers retry or local plan
review. Ordinary generation returns a local proposal when Gemini fails.
Duplicate and overlapping generation submissions are rejected before a new call.

Automatic context includes imported aggregate metrics, dates, current plan,
confirmed completion history, and available structured check-ins. It excludes
GPS, routes, activity names, private notes, raw files, account tokens, and secrets.
The automatic payload is saved locally before a request as `ai_safe_payload.json`
or `chat_safe_payload.json` in the configured export directory. These are private.
Chat also sends the messages you type and relevant recent conversation to Google;
avoid typing information you do not want to send. Unuploaded-run statements are
unverified context, never measurements or completion records.

Messages are limited to 2,000 characters. At most eight recent exchanges,
bounded to 12,000 content characters, accompany the structured summary. Stored
conversation text is capped at 2 MiB of UTF-8 content; oldest inactive conversations
are pruned first. An exceptionally long active conversation loses its oldest
messages if required. There are at most 100 conversation headers. All storage
uses the existing SQLite file and Fly volume. Deleting a conversation removes
its messages; separately saved plan proposals remain reviewable.

Replies are plain text and escaped by Jinja. Structured proposals are validated
with Pydantic and local workload checks before display; Gemini cannot apply a
plan or complete a session. Missing health samples stay missing.

## Development and interfaces

```powershell
.\.venv\Scripts\python.exe -m pytest -q
python -m app.cli import-zip "C:\path\to\strava.zip"
python -m app.cli import-health "C:\path\to\export.zip"
python -m app.cli export
python -m app.cli analyze
```

CLI analysis produces analysis files; it does not replace an existing active
plan. ZIP import supports CSV, FIT, GPX, TCX, and compressed activity files.
The recovered importer accepts running activity types dated 2026 onward.
Apple Health enrichment and optional Strava OAuth/API code are preserved.

New authenticated interfaces:

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/plan`, `/dashboard` | Saved sequence and next session |
| GET | `/plan/matches` | Review imported runs and existing links |
| POST | `/plan/sessions/{id}/match`, `/unmatch` | Confirm or undo a link |
| POST | `/plan/regenerate`, `/plan/local`, `/analysis/run` | Create a proposal |
| POST | `/analysis` | Create a proposal; return ID and review URL as JSON |
| GET | `/plan/proposals/{id}` | Review a saved proposal |
| POST | `/plan/proposals/{id}/accept` | Accept if revision and base still match |
| GET | `/coach?conversation_id={id}` | Conversation/history screen |
| POST | `/coach/message`, `/coach/{id}/retry` | Saved chat generation |
| POST | `/coach/new`, `/coach/{id}/delete` | Conversation controls |
| GET | `/api/backup` | Consistent SQLite snapshot plus private exports |

New mutations require the `csrf_token` supplied by a rendered form, or
`X-CSRF-Token`, matching the same-site cookie. Generation also requires a unique
16–64-character `request_id`; the HTML forms supply it. The `/analysis` form
API now uses this proposal workflow rather than returning and applying a plan.
Account authentication protects these endpoints when enabled.

See [MIGRATIONS.md](MIGRATIONS.md) before any production change and
[VERIFICATION.md](VERIFICATION.md) for test evidence and limits. Hosting identity:
`runstead-aliga`, London (`lhr`), existing `/data` volume. Following the owner's
deployment request, release v28 was deployed on September 29, 2026 after a
verified production backup and migration rehearsal. See [DEPLOYMENT.md](DEPLOYMENT.md).
The recovered project and light-theme fix are also published to `aminato21/fitmob`
(main). `render.yaml` is a retained legacy alternative.
