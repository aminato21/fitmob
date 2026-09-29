# Verification

## Local evidence

Isolated Windows environment: Python 3.12.14, dependencies captured in
`requirements-dev.lock`. Restored baseline: 24 passed, five expectation-drift
failures. Current implementation: **62 tests passed**. One upstream Starlette
warning recommends a future test-client dependency change; it does not affect
these passing checks. The package supports Python 3.11 or newer.

Coverage includes duplicate/corrected/batch ZIPs; content changes at unchanged
counts; unique run/session links and undo; complete-sequence state; archived-plan
completion retention; expired/stale/base-plan rejection; repeatable additive
migrations; preserved accounts, OAuth tokens, health and check-ins; auth and CSRF;
escaped chat; deletion and UTF-8 storage limits; bounded recent context;
oversized-message rejection; duplicate/concurrent generation prevention;
single provider request; actual timeout cancellation; rate limits, 404/500,
invalid JSON; privacy exclusions; unsafe proposals; old/empty data; and consistent
snapshot integrity including committed SQLite WAL data.

Mobile browser walkthrough used a local synthetic two-run ZIP:
import → review → confirm a match → next session advances → saved coach question
while Gemini is absent → generate local proposal → review → accept → original
completion remains in history. Additional screenshots and narrow-width checks
verify the mobile pages without a full redesign. Plan and Coach were checked at
390 × 844 and 320 × 740; neither produced horizontal overflow. Screenshots are
saved alongside the project in the delivery outputs.
The Python wheel was built and inspected: all new modules, templates, and static
assets are included. Python bytecode compilation and Git whitespace checks passed.

## Practical limits

Gemini failure paths were tested using mock transports and a timeout simulation.
After deployment, one live Gemini 3.5 Flash request passed with the exact Coach
structured-output schema and synthetic input; no personal data was sent. Existing
Google credentials and billing configuration were preserved.

Fly release v28 was deployed after downloading and verifying a consistent SQLite
snapshot and rehearsing migrations on a separate copy. Production checks confirmed
all seven source-table contents unchanged, SQLite integrity, additive tables,
matching hashes for four deployed application modules, owner authentication,
and the unchanged machine/volume/256 MiB resource allocation. Login returned 200;
protected Dashboard, Plan and Coach returned 401 without authentication. Public
assets and the service worker were checked. See DEPLOYMENT.md.

Local browser checks are viewport tests, not physical iPhone Safari certification.
The owner's authenticated production import/chat flow remains to be exercised on
their phone. No test runs, completion links, or chat messages were inserted into
production. The local preview contains demo data rather than the owner's records.


## Light-theme follow-up

All 62 tests passed again after the theme changes. Reviewed Dashboard, Plan,
Coach, Insights, run list/detail, Import and Settings at 390-pixel width, and
Dashboard at desktop width; no horizontal overflow was observed. Confirmed dark
Plan rendering still uses the existing colors. Mobile Plan/Coach screenshots are
saved alongside the source delivery. Sign-in and offline templates now apply
the persisted theme before rendering. The shell cache was advanced to v15.

Fly v29's eleven application/template/static hashes matched the local project.
All production table contents matched the fresh pre-deployment backup; integrity
and foreign-key checks passed. Exact public asset hashes and authenticated page
boundaries were checked over HTTPS. Source publication includes the restored
project on GitHub main; it excludes backups, secrets and local preview records.
