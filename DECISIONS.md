# Decisions

## Data access

- We are not currently using the Strava API because it requires
  subscription/API access that the user does not want to pay for.
- The existing OAuth/API code remains optional and maintained, but it is not
  the primary workflow.
- The main free mode is Strava bulk export ZIP import.
- The ZIP is parsed locally and can be reimported safely when a newer archive is
  requested.
- Direct Mi Fitness API integration is deferred unless a clean official API is
  available.

## AI

- The only supported AI provider choices are `gemini` and `none`.
- `none` is the safe default and deterministic analysis always remains
  available.
- The default Gemini model is stable `gemini-3.5-flash`.
- `gemini-2.5-flash` is the automatic availability fallback.
- Groq and Ollama are not implemented now.
- Strict JSON schema validation is required.
- Any Gemini failure must return deterministic analysis instead of crashing.

## Privacy

- No raw GPS coordinates, map polylines, exact home location, city/country,
  activity IDs, activity names, notes, raw routes, or raw streams are sent to
  Gemini.
- The exact allowed payload is saved locally as `ai_safe_payload.json` before
  Gemini is called.
- Secrets belong only in `.env`; they must never be committed or displayed in
  the dashboard.

## Product direction

- The app is local and is not a native mobile app.
- The current product is a mobile-friendly web dashboard/PWA first.
- Server-rendered FastAPI/Jinja HTML and responsive CSS are preferred over
  React while the UI remains small.
- Dark is the default theme, with a locally remembered light-mode option.
- Session frequency is user-selectable from one to three; two is the default.
- The goal picker is limited to conservative beginner goals.
- Full service-worker behavior requires HTTPS when the app is opened from an
  iPhone; a PC LAN address over plain HTTP is not a secure context.
- A native app may be considered later only after the PWA workflow is useful
  and stable.

## Training philosophy

- The user is a beginner run/walk runner, not an advanced continuous-distance
  runner.
- Walking breaks are valid training.
- Consistency and injury prevention take priority over pace.
- Default plan frequency is Wednesday and Sunday.
- No hard or speed work on consecutive days.
- No speed work while workload or injury-risk flags are active.
- Heart-rate data is used conservatively and never to invent medical zones.
