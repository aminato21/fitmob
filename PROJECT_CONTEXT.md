# Project context

## Verified baseline

The application baseline is the source recovered from Fly's July 11, 2026
deployment (v27). GitHub repository `aminato21/fitmob` supplies the surrounding
packaging, tests, and documentation; its observed last commit was
`d3f25ba2e2b623d98162b90a1e7a1df17ea05d5b` on June 28, 2026.
The recovery archive is preserved separately as `../runstead-recovered-source.zip`.
The development project's initial Git commit records the restored baseline.

Hosting: `https://runstead-aliga.fly.dev`, app `runstead-aliga`, region `lhr`,
existing volume `vol_rnz5mp265pkxk50r`, database `/data/strava.db`.
The observed machine was `83e561f79d1948`; recheck its identity before operating
on production. A Fly deployment can contain locally edited code that was never
pushed to GitHub. Do not assume the two sources are synchronized.

## Implemented and deployed in this phase

- Durable plan versions and ordered sessions, explicit unique run/session
  completion links, undo, retained historical completion, archived replacements.
- Matching review after imports and stable next-session cards on Dashboard/Plan.
- Proposed-plan review/accept, content revisions, stale rejection, seven-day
  expiry, refresh offers after new data, preferences, and check-ins.
- Latest imported date and ZIP upload time, with unuploaded data treated as unknown.
- Mobile saved Coach conversations, delete/new/retry, filtered context and
  locally checked structured proposals; one Gemini attempt, 30-second deadline,
  configured 3.5 Flash model, useful local failure path.
- Bounded history, CSRF on new mutations, escaped replies, private cache exclusion,
  owner authentication, consistent snapshot helper and backup endpoint.
- Local tests and mobile browser verification. See VERIFICATION.md.

## Product choices

Private owner app; consistency first; initially one weekly session, adjustable
one to three. Use a flexible sequence, with recovery between sessions. Walking
is useful training. Measured running ability comes from imported data; historical
notes about the owner's fitness are background rather than current measurements.
Existing activities, accounts, health data, and check-ins are preserved.

## Separate or future work

Production backup, migration rehearsal, and release v28 deployment were completed
on September 29, 2026. Source-table contents and deployed code hashes were checked;
a synthetic live Gemini structured response passed. See DEPLOYMENT.md.
Release v29 fixes light-mode colors and publishes the restored project to GitHub
main. Physical iPhone/Safari and the owner's
authenticated import/chat flow still need checking in that environment. Native applications, automatic syncing,
public accounts, a full redesign, and new hosting/storage services are outside
this phase. Keep the existing Google project and billing configuration.
