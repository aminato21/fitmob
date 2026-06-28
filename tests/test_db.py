from app.config import Settings
from app.db import Database


def test_activity_upsert_avoids_duplicates(tmp_path):
    database = Database(Settings(database_path=tmp_path / "test.db"))
    database.initialize()
    detail = {
        "id": 123,
        "name": "First",
        "sport_type": "Run",
        "start_date_local": "2026-02-01T10:00:00Z",
    }
    database.upsert_activity(detail, None, None)
    detail["name"] = "Edited"
    database.upsert_activity(detail, {}, [])
    records = database.all_activities()
    assert len(records) == 1
    assert records[0]["detail"]["name"] == "Edited"


def test_oauth_state_is_single_use(tmp_path):
    database = Database(Settings(database_path=tmp_path / "test.db"))
    database.initialize()
    database.save_state("safe-state", 4_102_444_800)
    assert database.consume_state("safe-state") is True
    assert database.consume_state("safe-state") is False

