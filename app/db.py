from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from app.config import Settings


SCHEMA = """
CREATE TABLE IF NOT EXISTS oauth_tokens (
    athlete_id INTEGER PRIMARY KEY,
    access_token TEXT NOT NULL,
    refresh_token TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    scope TEXT,
    athlete_json TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS oauth_states (
    state TEXT PRIMARY KEY,
    expires_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS app_preferences (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS activities (
    id INTEGER PRIMARY KEY,
    name TEXT,
    start_date_local TEXT NOT NULL,
    sport_type TEXT NOT NULL,
    distance_m REAL,
    moving_time INTEGER,
    elapsed_time INTEGER,
    total_elevation_gain REAL,
    average_speed REAL,
    max_speed REAL,
    average_heartrate REAL,
    max_heartrate REAL,
    average_cadence REAL,
    detail_json TEXT NOT NULL,
    streams_json TEXT,
    laps_json TEXT,
    synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_activities_start
ON activities(start_date_local);
"""


class Database:
    def __init__(self, settings: Settings):
        self.path = Path(settings.database_path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            yield conn
            conn.commit()
        finally:
            conn.close()

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            # Lightweight migration support for databases made by older versions.
            existing = {
                row["name"] for row in conn.execute("PRAGMA table_info(activities)")
            }
            columns = {
                "name": "TEXT",
                "distance_m": "REAL",
                "moving_time": "INTEGER",
                "elapsed_time": "INTEGER",
                "total_elevation_gain": "REAL",
                "average_speed": "REAL",
                "max_speed": "REAL",
                "average_heartrate": "REAL",
                "max_heartrate": "REAL",
                "average_cadence": "REAL",
            }
            for name, sql_type in columns.items():
                if name not in existing:
                    conn.execute(
                        f"ALTER TABLE activities ADD COLUMN {name} {sql_type}"
                    )

    def save_state(self, state: str, expires_at: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM oauth_states WHERE expires_at < unixepoch()")
            conn.execute(
                "INSERT OR REPLACE INTO oauth_states(state, expires_at) VALUES (?, ?)",
                (state, expires_at),
            )

    def consume_state(self, state: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT state FROM oauth_states WHERE state = ? AND expires_at >= unixepoch()",
                (state,),
            ).fetchone()
            if row:
                conn.execute("DELETE FROM oauth_states WHERE state = ?", (state,))
            return row is not None

    def save_token(self, payload: dict[str, Any], scope: str | None = None) -> None:
        athlete = payload.get("athlete") or {}
        athlete_id = athlete.get("id")
        if athlete_id is None:
            existing = self.get_token()
            athlete_id = existing["athlete_id"] if existing else 1
            athlete_json = existing["athlete_json"] if existing else None
        else:
            athlete_json = json.dumps(athlete, ensure_ascii=False)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO oauth_tokens(
                    athlete_id, access_token, refresh_token, expires_at,
                    scope, athlete_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(athlete_id) DO UPDATE SET
                    access_token=excluded.access_token,
                    refresh_token=excluded.refresh_token,
                    expires_at=excluded.expires_at,
                    scope=COALESCE(excluded.scope, oauth_tokens.scope),
                    athlete_json=COALESCE(excluded.athlete_json, oauth_tokens.athlete_json),
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    athlete_id,
                    payload["access_token"],
                    payload["refresh_token"],
                    int(payload["expires_at"]),
                    scope,
                    athlete_json,
                ),
            )

    def get_token(self) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM oauth_tokens ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()

    def upsert_activity(
        self,
        detail: dict[str, Any],
        streams: dict[str, Any] | None,
        laps: list[dict[str, Any]] | None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO activities(
                    id, name, start_date_local, sport_type, distance_m,
                    moving_time, elapsed_time, total_elevation_gain,
                    average_speed, max_speed, average_heartrate,
                    max_heartrate, average_cadence, detail_json,
                    streams_json, laps_json, synced_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          CURRENT_TIMESTAMP)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    start_date_local=excluded.start_date_local,
                    sport_type=excluded.sport_type,
                    distance_m=excluded.distance_m,
                    moving_time=excluded.moving_time,
                    elapsed_time=excluded.elapsed_time,
                    total_elevation_gain=excluded.total_elevation_gain,
                    average_speed=excluded.average_speed,
                    max_speed=excluded.max_speed,
                    average_heartrate=excluded.average_heartrate,
                    max_heartrate=excluded.max_heartrate,
                    average_cadence=excluded.average_cadence,
                    detail_json=excluded.detail_json,
                    streams_json=COALESCE(excluded.streams_json, activities.streams_json),
                    laps_json=COALESCE(excluded.laps_json, activities.laps_json),
                    synced_at=CURRENT_TIMESTAMP
                """,
                (
                    int(detail["id"]),
                    detail.get("name"),
                    detail["start_date_local"],
                    detail.get("sport_type") or detail.get("type") or "Run",
                    detail.get("distance"),
                    detail.get("moving_time"),
                    detail.get("elapsed_time"),
                    detail.get("total_elevation_gain"),
                    detail.get("average_speed"),
                    detail.get("max_speed"),
                    detail.get("average_heartrate"),
                    detail.get("max_heartrate"),
                    detail.get("average_cadence"),
                    json.dumps(detail, ensure_ascii=False),
                    json.dumps(streams, ensure_ascii=False) if streams is not None else None,
                    json.dumps(laps, ensure_ascii=False) if laps is not None else None,
                ),
            )

    def activity_ids(self) -> set[int]:
        with self.connect() as conn:
            return {int(row[0]) for row in conn.execute("SELECT id FROM activities")}

    def get_preferences(self) -> dict[str, Any]:
        with self.connect() as conn:
            rows = conn.execute("SELECT key, value FROM app_preferences").fetchall()
        result: dict[str, Any] = {}
        for row in rows:
            try:
                result[row["key"]] = json.loads(row["value"])
            except (TypeError, json.JSONDecodeError):
                result[row["key"]] = row["value"]
        return result

    def save_preferences(self, values: dict[str, Any]) -> None:
        with self.connect() as conn:
            for key, value in values.items():
                conn.execute(
                    """
                    INSERT INTO app_preferences(key, value, updated_at)
                    VALUES (?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(key) DO UPDATE SET
                        value=excluded.value,
                        updated_at=CURRENT_TIMESTAMP
                    """,
                    (key, json.dumps(value, ensure_ascii=False)),
                )

    def all_activities(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT detail_json, streams_json, laps_json FROM activities "
                "ORDER BY start_date_local, id"
            ).fetchall()
        return [
            {
                "detail": json.loads(row["detail_json"]),
                "streams": json.loads(row["streams_json"]) if row["streams_json"] else None,
                "laps": json.loads(row["laps_json"]) if row["laps_json"] else None,
            }
            for row in rows
        ]
