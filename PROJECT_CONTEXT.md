# Project Context

## Goal

Runstead is a local, privacy-conscious running analysis PWA for a beginner
runner. It imports 2026 activity history, identifies run/walk patterns, produces
analysis-ready CSV/JSON files, shows a mobile-first web dashboard, and creates a
conservative beginner training interpretation and plan.

The priority is safe, understandable progression—not speed, advanced training,
or replacing medical advice.

## User running history and beginner context

- Initial continuous-running limit was roughly 2.0–2.25 km.
- The user discovered that walking 100–200 m after reaching that limit allowed
  another running segment.
- Early extended sessions were around 3.2–3.5 km: roughly 2–2.5 km running,
  then a short walk, then another 1–1.5 km running.
- A usual route was about 3.5 km outward from home. The unrecorded return was
  often walked, so recorded Strava distance does not always represent the
  entire outing or workload.
- The first 7 km activity was a beginner long run/walk with about five walking
  pauses, not a continuous advanced long run.
- The latest intentional 5 km had about two 200 m walking pauses.
- The previous intentional 5 km had about three walking pauses: approximately
  300 m, 150 m, and 100 m.
- Current practical level:
  - about 3 km continuous running;
  - 5 km with two or three walking breaks;
  - 7 km with several walking breaks.
- Walking is a valid training tool and must never be treated as failure.
- Normal workdays end around 19:00. The default schedule is two runs per week,
  preferably Wednesday and Sunday.
- Three sessions can be considered during a fully free week. One weekly session
  is acceptable when busy, with the understanding that progress may be slower.

## Current data sources

1. **Primary free mode:** Strava bulk account-export ZIP.
   - Parses `activities.csv`.
   - Parses `.fit`, `.fit.gz`, `.gpx`, `.gpx.gz`, `.tcx`, and `.tcx.gz`.
   - Stores normalized activities and optional streams/laps in SQLite.
2. **Existing but not currently used:** Strava OAuth/API integration.
3. **Possible later source:** Mi Fitness account export, if a usable official
   export format becomes available.
4. **Planned later source:** manual CSV/JSON import.

The app analyzes only `Run`, `TrailRun`, and `VirtualRun` activities from 2026.

## Privacy rules

- Never hardcode or commit secrets.
- `.env`, SQLite files, OAuth tokens, and generated exports are Git-ignored.
- Before any Gemini call, write the exact external payload to
  `exports/ai_safe_payload.json`.
- Gemini must not receive:
  - raw GPS coordinates or GPS streams;
  - start/end latitude and longitude;
  - map summary polylines;
  - raw/full routes;
  - exact city, country, or home location;
  - activity names or private notes;
  - activity IDs;
  - exact activity timestamps;
  - device or gear identifiers;
  - raw FIT/GPX/TCX data.
- Gemini may receive only anonymized totals, weekly/monthly statistics, latest
  activity date/distance/duration/pace, run/walk estimates, available HR
  summaries, safety flags, progression and walk-break trends, current
  capabilities, personal bests, consistency, preferences, and safe user
  context.
- There is intentionally no browser switch that disables this privacy filter.

## AI rules

- Supported providers are only `gemini` and `none`.
- Default is `AI_PROVIDER=none`; no data leaves the computer.
- Recommended model is stable `gemini-3.5-flash`.
- Automatic availability fallback is `gemini-2.5-flash`.
- `AI_MODEL` remains configurable in `.env`.
- No Groq or Ollama implementation yet.
- Gemini output must pass strict structured JSON validation with:
  - `current_level`
  - `progress_highlights`
  - `walk_break_analysis`
  - `main_findings`
  - `risks`
  - `next_week`
  - `four_week_plan`
  - `milestone_targets`
  - `what_to_watch_next`
  - `questions_for_user`
- Missing API key, network failure, rate limit, unavailable model, or invalid
  Gemini JSON must fall back to deterministic analysis without crashing.
- AI guidance is not medical advice and must not diagnose.
- The system prompt must be conservative, beginner-friendly, supportive of
  walking breaks, and focused on consistency and injury prevention.

## Current watch/device setup

- Redmi Watch 3 Active.
- Connected to Mi Fitness on iPhone.
- Outdoor Running is recorded from the watch while carrying the iPhone.
- Mi Fitness syncs to Strava.
- Future Strava exports may include heart-rate data.
- GPS may depend on the iPhone and must not be assumed perfectly accurate.
- No direct Mi Fitness API integration unless a clean official API appears.

## Important training assumptions

- Most sessions should be treated as run/walk unless streams clearly indicate
  continuous running.
- Do not infer real intervals unless the activity name or repeated fast-stream
  structure clearly supports it.
- Do not infer tempo training.
- A long run is one of the longest easy sessions of that week; it is not hard.
- A fast run requires a clear improvement over recent comparable normal pace.
- A progress test requires a named test/race/benchmark and a comparable prior
  activity; it is not a weekly requirement.
- No speed work when sudden-volume, injury-risk, or excessive-hard-session
  flags are active.
- Do not increase distance aggressively.
- Heart rate is secondary and conservative. Do not guess medical zones. Zone
  output remains null unless max HR or custom boundaries are configured.
- Pain, illness, unusual fatigue, or professional medical advice always
  overrides app suggestions.
- Default preferred frequency is two sessions per week, configurable from one
  to three in `/settings`.
- The default goal is building toward a comfortable continuous 5 km. Other
  local goal choices are fewer walking breaks, comfortable 7 km, or
  consistency.
