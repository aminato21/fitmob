from __future__ import annotations

import asyncio

from app.config import Settings
from app.db import Database
from app.sync import sync_activities


class FakeStravaClient:
    async def list_activities(self, after, before):
        return [
            {
                "id": 10,
                "sport_type": "Run",
                "start_date_local": "2026-03-01T08:00:00Z",
            },
            {
                "id": 20,
                "sport_type": "Ride",
                "start_date_local": "2026-03-01T09:00:00Z",
            },
        ]

    async def activity_detail(self, activity_id):
        return {
            "id": activity_id,
            "name": "New run",
            "sport_type": "Run",
            "start_date": "2026-03-01T08:00:00Z",
            "start_date_local": "2026-03-01T08:00:00Z",
            "distance": 2000,
            "moving_time": 1200,
            "elapsed_time": 1300,
            "average_speed": 1.667,
        }

    async def activity_streams(self, activity_id):
        return None

    async def activity_laps(self, activity_id):
        return []


def test_sync_filters_runs_and_skips_existing(tmp_path):
    settings = Settings(
        database_path=tmp_path / "sync.db",
        export_dir=tmp_path / "exports",
    )
    database = Database(settings)
    client = FakeStravaClient()
    first = asyncio.run(sync_activities(settings, database, client=client))
    second = asyncio.run(sync_activities(settings, database, client=client))
    assert first["activities_fetched"] == 1
    assert first["runs"] == 1
    assert second["activities_fetched"] == 0
    assert second["existing_activities_skipped"] == 1
    assert len(database.all_activities()) == 1

