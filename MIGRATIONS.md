# Migration and deployment notes

Release v28 was deployed on September 29, 2026 after a consistent production
snapshot was downloaded and verified, and additive migrations were rehearsed on
a separate copy. See DEPLOYMENT.md. For every future deployment, complete and
verify a fresh downloaded snapshot before startup with new code. The new schema is additive and startup initializes it
idempotently; existing activity/account/health/check-in tables are preserved.

## Download a consistent snapshot before deployment

The previously deployed backup endpoint copied SQLite directly. Until the new
code is deployed, use SQLite's backup API on the current machine rather than
copying a possibly live database file. `tools/sqlite_backup.py` works without
starting the app or applying migrations.

First confirm the current Fly machine and volume from your authenticated CLI:

```powershell
fly status -a runstead-aliga
fly volumes list -a runstead-aliga
```

Observed recovery identities: machine `83e561f79d1948`, volume
`vol_rnz5mp265pkxk50r`, region `lhr`, database `/data/strava.db`. If they changed,
use the current verified machine attached to the existing data volume. Do not
create a new volume. A suspended machine may need starting for SFTP/SSH.

After confirming the machine, choose a fresh snapshot filename:

```powershell
fly sftp put tools/sqlite_backup.py /tmp/runstead-sqlite-backup.py -a runstead-aliga --machine 83e561f79d1948
fly ssh console -a runstead-aliga --machine 83e561f79d1948 -C "python /tmp/runstead-sqlite-backup.py /data/strava.db /tmp/runstead-pre-migration.sqlite"
fly sftp get /tmp/runstead-pre-migration.sqlite ./runstead-pre-migration.sqlite -a runstead-aliga --machine 83e561f79d1948
python tools/sqlite_backup.py ./runstead-pre-migration.sqlite --verify
```

The helper opens the source read-only, calls `Connection.backup`, checks integrity
and foreign keys, and refuses to overwrite an existing snapshot. A successful
local verification is required; keep the downloaded backup in a private location.
It contains accounts, health information, conversation data if present, and OAuth
tokens. Also preserve `/data/exports` if you need generated files; preserve the
original recovery archive and identify the deployed image/release for rollback.
This backup procedure was completed before the September 29 deployment; the
private downloaded snapshot remains outside the source project and delivery ZIP.

To rehearse migrations on a local copy (never the only backup):

```powershell
python tools/sqlite_backup.py ./runstead-pre-migration.sqlite ./migration-rehearsal.sqlite
$env:DATABASE_PATH = (Resolve-Path ./migration-rehearsal.sqlite).Path
$env:EXPORT_DIR = "./rehearsal-exports"
$env:AI_PROVIDER = "none"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open the dashboard, compare source-table counts/content and test matching/undo.
Avoid sending real health/chat data to Google during rehearsal unless intended.

## New schema and existing state

New tables: `plan_versions`, `plan_sessions`, `completion_links`, `import_events`,
`conversations`, `coach_messages`, `generation_requests`. Existing source data is
not rewritten by migration. A unique partial index enforces one active plan.
Sessions/completions remain separate from generated analysis. Acceptance and
matching use SQLite transactions and uniqueness constraints.

On the first plan view, seed the initial sequence from a valid locally checked
legacy `latest_coach_result.json` when available, otherwise use a deterministic
baseline. Previously completed plan sessions were not recorded by the recovered
schema and cannot be inferred: confirm historical runs explicitly. The first new
upload records upload time; earlier upload time remains unknown.

Existing stored preferences are retained. New installations default to consistency
and one weekly session. Set Settings to the chosen goal/frequency if old values
remain, then review a proposal. Content revisions detect corrections, check-ins,
preferences, health changes and completion links. Generated-file counters are no
longer the authority for active-plan replacement.

## Future production deployment

After backup/rehearsal and separate deployment authorization, use the existing
`runstead-aliga` app, London `lhr`, existing `runstead_data` volume at `/data`.
Keep `DATABASE_PATH=/data/strava.db`, `EXPORT_DIR=/data/exports`, owner account,
`GEMINI_API_KEY` and `AUTH_SECRET_KEY`. Verify `AUTH_ENABLED=true` and the configured
`AI_MODEL=gemini-3.5-flash`. Old `AI_FALLBACK_MODEL` values are ignored. Do not change
Google billing or provision storage/services. Review `fly.toml` before deployment;
do not use the retained legacy Render configuration for this phase.

Run tests before deploying and check the full phone flow afterward. The deployed
v28 model passed a synthetic live structured-output check. A physical iPhone and
the owner's authenticated import/chat flow still need checking; mocked responses
alone do not establish provider availability.

## Rollback

Prefer redeploying the verified prior Fly image against the existing volume.
The migration adds tables, so the recovered app can ignore them. Its old behavior
cannot display the new plan/completion/chat state. If the database itself needs
restoring, stop writes first and restore the verified snapshot carefully with
separate approval; restoring discards changes made after the backup. Preserve the
current database separately before any restoration. Do not delete the volume.

The deployed `/api/backup` now uses SQLite's backup API and
integrity validation, and bundles the snapshot with private exports.
