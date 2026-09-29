# Roadmap

## Implemented and locally verified

ZIP → explicit match review → stable next session → saved Coach question →
proposal review → accepted replacement, including completion history and undo.
Content revision checks, additive schema, owner authentication, CSRF, safe text
rendering, private cache rules, bounded conversations, single-attempt Gemini
generation, and local failure handling are included. See VERIFICATION.md.

## Next production session

Production backup, migration rehearsal and v28 deployment are complete; see
DEPLOYMENT.md. Remaining checks and delivery:

1. Run the flow with the owner's real archive on iPhone Safari, including a live
   Gemini answer using the existing Google configuration.
2. Before future deployments, repeat the backup and verification in MIGRATIONS.md.

The recovered source and light-theme fix have been published to GitHub main
as part of the authorized v29 delivery.

## Deferred

Native app, automatic/background syncing, public accounts, new hosting services,
full visual redesign, new health integrations, and additional model providers.
