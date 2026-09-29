# Fly deployment — September 29, 2026

Deployed on the owner's explicit request. Live URL: https://runstead-aliga.fly.dev

## Release and infrastructure

- Release: **v28**, completed September 29, 2026 at approximately 21:54 UTC.
- Image: `registry.fly.io/runstead-aliga@sha256:3b856a137dd2650945669703f32216baa96e033a6b3fc23b681ac9bd795a23e4`.
- Existing machine: `83e561f79d1948`, London (`lhr`).
- Existing volume: `vol_rnz5mp265pkxk50r`, `runstead_data`, 1 GB at `/data`.
- Resources retained: one shared CPU, 256 MiB memory; idle suspension retained.
- Existing public ingress IPs retained. No additional app machine or volume.
- `AI_PROVIDER=gemini`, `AI_MODEL=gemini-3.5-flash`, owner authentication enabled.
- Existing Google API key, auth secret, account and billing configuration retained.

The Fly CLI build transport stalled before starting a build. The same source was
built directly with Docker on the existing Fly builder, pushed to the existing
app registry, then deployed by immutable image digest. Temporary registry login
files were removed from the builder and local Docker configuration afterward.

## Backup and migration evidence

Before deployment, SQLite's backup API produced a consistent snapshot. The
1,613,824-byte snapshot was downloaded and passed integrity and foreign-key checks.
It is stored privately in `work/production-backups/runstead-pre-deploy-20260929.sqlite`
relative to the Codex workspace; it is excluded from source archives and Git.
A separate copy was used for migration rehearsal.

After deployment, database integrity was `ok`. All seven existing source tables
matched the snapshot by complete-content SHA-256 hashes. The additive workflow
and chat tables were present. Existing source records were preserved. The stored two-session weekly
preference remains adjustable in Settings; installation defaults do not reset it.

## Runtime checks

- Deployed hashes matched `app/main.py`, `app/ai.py`, `app/coach.py`, and
  `app/workflow.py` in the tested local project.
- HTTPS login returned 200 and rendered the private sign-in page.
- Dashboard, Plan and Coach rejected unauthenticated requests with 401.
- Public static assets and service worker were checked against local content.
- One live Gemini 3.5 Flash request returned a valid response for the exact Coach
  JSON schema, using synthetic input and no personal data.
- Local suite: 62 passing tests; wheel and mobile browser flow previously verified.

No test activities, completion matches or chat messages were inserted into the
production database. Physical iPhone Safari and the owner's authenticated flow
remain a user check. GitHub publication was not part of v28; the subsequent v29 delivery includes it.

## Rollback reference

Prior release: v27, July 11, 2026, image:
`registry.fly.io/runstead-aliga:deployment-01KX9SNSPM3KYZJD6GFHVMWB01`.
Prefer an image rollback while retaining the current volume. Database restoration
requires preserving newer data first and separate authorization; see MIGRATIONS.md.


## Light-theme delivery — v29

On the owner's request, deployed v29 on September 29, 2026 at 22:12 UTC.
Image: `registry.fly.io/runstead-aliga@sha256:fc054bfbb6d8ba9e8c23a0f758f7a6d1c01b0e19e12a542e9f647bdbf2bf8bf3`.
The existing London machine, volume, CPU/memory allocation, Google configuration
and owner authentication remain in use.

Fixed remaining dark session/hero/progress panels in light mode. Accent text and
chart strokes now use darker theme-aware ink, while brand fills stay bright.
Navigation, form fields, code blocks, loading overlays, badges and chart tracks
were checked. Sign-in, registration and offline pages honor the saved theme.
The service-worker shell cache advances to v15 to refresh cached assets.

A fresh SQLite backup was downloaded and verified before deployment, stored
privately as `work/production-backups/runstead-pre-light-20260929.sqlite` relative
to the Codex workspace. After deployment, every table's complete-content hash
matched this backup. Integrity and foreign-key checks passed; eleven deployed
application/template/static file hashes matched local source. Public HTTP checks
verified sign-in, protected pages and exact asset hashes. All 62 tests passed.
Light-mode main pages were checked at 390-pixel mobile width; Dashboard was also
checked at desktop width. The Plan dark-theme appearance was checked after toggling.

The source project, recovery changes, workflow implementation and light-theme fix
are published to `aminato21/fitmob` main with the June GitHub head retained as the
commit parent. Private backups, recovery ZIPs, secrets and demo data are excluded.
Rollback image for this release: the v28 digest recorded above.
