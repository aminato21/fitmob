from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.analysis import RUN_TYPES
from app.config import Settings
from app.db import Database
from app.exporter import export_all
from app.strava import StravaClient


async def sync_activities(
    settings: Settings,
    database: Database,
    client: StravaClient | None = None,
    refresh_existing: bool = False,
) -> dict[str, Any]:
    database.initialize()
    owns_client = client is None
    client = client or StravaClient(settings, database)
    try:
        summaries = await client.list_activities(
            # Start early, then filter by local date. This includes Jan 1 runs
            # whose UTC timestamp falls on Dec 31 in positive time zones.
            datetime(2025, 12, 30),
            datetime.now(timezone.utc),
        )
        existing = database.activity_ids()
        candidates = [
            activity
            for activity in summaries
            if (activity.get("sport_type") or activity.get("type")) in RUN_TYPES
            and str(activity.get("start_date_local", ""))[:4].isdigit()
            and int(str(activity.get("start_date_local", ""))[:4]) >= 2026
        ]
        fetched = 0
        skipped = 0
        for summary in candidates:
            activity_id = int(summary["id"])
            if activity_id in existing and not refresh_existing:
                skipped += 1
                continue
            detail = await client.activity_detail(activity_id)
            streams = await client.activity_streams(activity_id)
            laps = await client.activity_laps(activity_id)
            database.upsert_activity(detail, streams, laps)
            fetched += 1
        exports = export_all(
            database.all_activities(),
            settings.with_preferences(database.get_preferences()),
        )
        return {
            "status": "ok",
            "strava_running_activities_seen": len(candidates),
            "activities_fetched": fetched,
            "existing_activities_skipped": skipped,
            **exports,
        }
    finally:
        if owns_client:
            await client.close()


def export_from_database(settings: Settings, database: Database) -> dict[str, Any]:
    database.initialize()
    return export_all(
        database.all_activities(),
        settings.with_preferences(database.get_preferences()),
    )
