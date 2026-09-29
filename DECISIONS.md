# Decisions

## Session completion and replacement

The active plan is a persisted ordered sequence. Imported runs do not silently
complete sessions. A confirmed imported-run link is the completion record; both
run and session are unique in that relationship. Undo removes the link.
Generating a plan saves a proposal. Accepting a current proposal archives the old
sequence and retains its completed history. Stale revisions, changed base plans,
or proposals older than seven days cannot be accepted.

Content revisions hash normalized activity, health, check-in, preference, and
completion contents. Import timestamps and bookkeeping timestamps are excluded,
so identical ZIPs do not invalidate a proposal merely because they were uploaded
again. Corrections at an unchanged record count do invalidate it.

## Coaching

Keep Gemini 3.5 Flash as the configured default. Remove alternative-model cascades.
One provider attempt per generation, total deadline 30 seconds. Local planning
is always available. Both chat and ordinary proposals use the same acceptance
rules; provider code cannot mutate the active sequence or completion records.
Local validation checks session counts, stage order, matching first-stage content,
distance totals, bounded increases, recovery stage, duration ceilings, and unsafe
effort. Unsafe schedules are replaced with the local conservative sequence and
the adjustment is shown.

## Privacy, storage and ownership

Only the owner uses this app. Existing authentication is enabled in Fly config.
Automatic Gemini context excludes routes, GPS, names, notes, raw files and secrets;
typed chat is explicitly disclosed as sent to Google. New form mutations require
CSRF protection. Private content is escaped and never service-worker cached.
Use existing SQLite and its Fly volume; conversation content is capped at 2 MiB.
No new cloud service, volume, Google project, or billing change.

## Scope

Retain FastAPI/Jinja and the existing mobile visual system. The initial goal is
consistency at one session per week, flexible dates, adjustable between one and
three. Preserve existing preferences rather than resetting them on every startup.
Do not infer inactivity from an old import. Deployment is a separate step after
a downloaded, verified SQLite snapshot.
