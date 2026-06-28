# Roadmap

## Already done

- FastAPI application with SQLite persistence.
- Strava OAuth 2.0, token refresh, `activity:read_all`, API sync endpoint, and
  CLI sync command.
- Main free mode importing Strava account-export ZIP files.
- Duplicate-safe activity storage by Strava activity ID.
- FIT, GPX, TCX, gzip, and duplicate-column `activities.csv` parsing.
- 2026 run filtering for `Run`, `TrailRun`, and `VirtualRun`.
- Run, split, lap, stream-summary, raw JSON, and summary exports.
- Beginner run/walk estimates and conservative classifications.
- Weekly/monthly/rolling metrics and safety flags.
- Walk-break trend, personal bests, capability estimates, and consistency score.
- Structured deterministic analysis and configurable one-to-three-session plan
  with per-session distance, duration, walk strategy, warm-up, and focus.
- Optional Gemini integration with strict structured JSON.
- Privacy-filtered `ai_safe_payload.json` written before Gemini calls.
- Gemini `none` fallback for missing key, errors, rate limits, and invalid JSON.
- Stable `gemini-3.5-flash` default with `gemini-2.5-flash` availability
  fallback.
- `python -m app.cli list-models`.
- Premium responsive, server-rendered mobile-first dashboard with dark/light
  themes, canvas charts, animated counters, loading states, and large touch
  targets:
  - `/dashboard`
  - `/import`
  - `/activities`
  - `/activity/{id}`
  - `/analysis`
  - `/plan`
  - `/settings`
- API documentation remains available at `/docs`.
- Locally stored session-frequency and beginner-goal preferences.
- Installable PWA shell with manifest, app icons, standalone iPhone metadata,
  service worker, and offline page.
- Automated tests for import, database, analysis, privacy, Gemini mocks,
  fallbacks, model catalog, API upload, and all dashboard routes.

## Next

- Put the Gemini key in the local `.env` only if optional AI analysis is wanted.
- Import the user's real Strava ZIP through `/import`.
- Review real outputs and tune walk-speed/duration thresholds against actual
  FIT/GPX/TCX streams.
- Confirm how Redmi/Mi Fitness heart rate and cadence appear in the real
  exported files.
- Review the dashboard at an actual iPhone viewport and adjust spacing/text
  where useful.
- Configure a secure local/private HTTPS URL for full iPhone PWA installation;
  plain LAN HTTP can display the site but cannot run its service worker.
- Add an editable, locally stored user-context/profile form that never exposes
  secrets.
- Tune walk-break estimates against the user's real activity streams.

## Later

- Manual CSV and JSON import formats.
- Mi Fitness export import if a stable official export becomes available.
- Native iPhone/Android packaging only if the web dashboard proves useful.
- Additional AI providers only after Gemini mode is stable and there is a real
  need.
- Optional local authentication before allowing LAN access.
- More detailed charts and comparisons across years.
- Optional Strava webhooks if API mode becomes practical again.

## Out of scope for now

- Direct unofficial Mi Fitness scraping or reverse-engineered APIs.
- Groq or Ollama providers.
- Native App Store or Play Store application; the PWA is a web app.
- Public internet deployment.
- Medical diagnosis, medical HR zones, or injury treatment recommendations.
- Aggressive plans, advanced tempo work, or sprint training.
- Sending raw GPS, routes, exact locations, or private activity details to AI.
- Assuming the 7 km run/walk was a continuous advanced long run.
