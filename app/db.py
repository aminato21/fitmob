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

CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY COLLATE NOCASE,
    password_salt TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    password_iterations INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS health_daily (
    date TEXT PRIMARY KEY,
    sleep_duration_sec REAL,
    resting_heartrate REAL,
    steps REAL,
    active_energy_kcal REAL,
    oxygen_saturation_percent REAL,
    sources_json TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS activity_health (
    activity_id INTEGER PRIMARY KEY,
    average_heartrate REAL,
    max_heartrate REAL,
    min_heartrate REAL,
    sample_count INTEGER,
    sources_json TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(activity_id) REFERENCES activities(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS activity_checkins (
    activity_id INTEGER PRIMARY KEY,
    perceived_effort INTEGER,
    soreness INTEGER,
    pain INTEGER,
    sleep_quality INTEGER,
    energy_level INTEGER,
    followed_walk_strategy INTEGER,
    unrecorded_walk_minutes INTEGER,
    notes TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(activity_id) REFERENCES activities(id) ON DELETE CASCADE
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

    def user_count(self) -> int:
        with self.connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS count FROM users").fetchone()
        return int(row["count"])

    def activity_count(self) -> int:
        with self.connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS count FROM activities").fetchone()
        return int(row["count"])

    def checkin_count(self) -> int:
        with self.connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS count FROM activity_checkins").fetchone()
        return int(row["count"])

    def health_count(self) -> int:
        with self.connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS count FROM health_daily").fetchone()
        return int(row["count"])

    def latest_activity_id(self) -> int | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT id FROM activities ORDER BY start_date_local DESC, id DESC LIMIT 1"
            ).fetchone()
        return int(row["id"]) if row else None


    def create_user(
        self,
        username: str,
        password_salt: str,
        password_hash: str,
        password_iterations: int,
    ) -> bool:
        normalized = username.strip()
        if not normalized:
            return False
        try:
            with self.connect() as conn:
                conn.execute(
                    """
                    INSERT INTO users(
                        username, password_salt, password_hash,
                        password_iterations
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        normalized,
                        password_salt,
                        password_hash,
                        int(password_iterations),
                    ),
                )
        except sqlite3.IntegrityError:
            return False
        return True

    def get_user(self, username: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE username = ? COLLATE NOCASE",
                (username.strip(),),
            ).fetchone()
        return dict(row) if row else None

    def user_exists(self, username: str) -> bool:
        return self.get_user(username) is not None

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

    def upsert_health_daily(self, rows: list[dict[str, Any]]) -> None:
        with self.connect() as conn:
            for row in rows:
                conn.execute(
                    """
                    INSERT INTO health_daily(
                        date, sleep_duration_sec, resting_heartrate, steps,
                        active_energy_kcal, oxygen_saturation_percent,
                        sources_json, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(date) DO UPDATE SET
                        sleep_duration_sec=COALESCE(excluded.sleep_duration_sec, health_daily.sleep_duration_sec),
                        resting_heartrate=COALESCE(excluded.resting_heartrate, health_daily.resting_heartrate),
                        steps=COALESCE(excluded.steps, health_daily.steps),
                        active_energy_kcal=COALESCE(excluded.active_energy_kcal, health_daily.active_energy_kcal),
                        oxygen_saturation_percent=COALESCE(excluded.oxygen_saturation_percent, health_daily.oxygen_saturation_percent),
                        sources_json=excluded.sources_json,
                        updated_at=CURRENT_TIMESTAMP
                    """,
                    (
                        row["date"],
                        row.get("sleep_duration_sec"),
                        row.get("resting_heartrate"),
                        row.get("steps"),
                        row.get("active_energy_kcal"),
                        row.get("oxygen_saturation_percent"),
                        json.dumps(row.get("sources") or [], ensure_ascii=False),
                    ),
                )

    def upsert_activity_health(self, rows: list[dict[str, Any]]) -> None:
        with self.connect() as conn:
            for row in rows:
                conn.execute(
                    """
                    INSERT INTO activity_health(
                        activity_id, average_heartrate, max_heartrate,
                        min_heartrate, sample_count, sources_json, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(activity_id) DO UPDATE SET
                        average_heartrate=excluded.average_heartrate,
                        max_heartrate=excluded.max_heartrate,
                        min_heartrate=excluded.min_heartrate,
                        sample_count=excluded.sample_count,
                        sources_json=excluded.sources_json,
                        updated_at=CURRENT_TIMESTAMP
                    """,
                    (
                        int(row["activity_id"]),
                        row.get("average_heartrate"),
                        row.get("max_heartrate"),
                        row.get("min_heartrate"),
                        row.get("sample_count"),
                        json.dumps(row.get("sources") or [], ensure_ascii=False),
                    ),
                )

    def health_daily(self, limit: int | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM health_daily ORDER BY date"
        params: tuple[Any, ...] = ()
        if limit:
            query = (
                "SELECT * FROM (SELECT * FROM health_daily ORDER BY date DESC "
                "LIMIT ?) ORDER BY date"
            )
            params = (int(limit),)
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [
            {
                **dict(row),
                "sources": json.loads(row["sources_json"] or "[]"),
            }
            for row in rows
        ]

    def save_checkin(self, activity_id: int, values: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO activity_checkins(
                    activity_id, perceived_effort, soreness, pain,
                    sleep_quality, energy_level, followed_walk_strategy,
                    unrecorded_walk_minutes, notes, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(activity_id) DO UPDATE SET
                    perceived_effort=excluded.perceived_effort,
                    soreness=excluded.soreness,
                    pain=excluded.pain,
                    sleep_quality=excluded.sleep_quality,
                    energy_level=excluded.energy_level,
                    followed_walk_strategy=excluded.followed_walk_strategy,
                    unrecorded_walk_minutes=excluded.unrecorded_walk_minutes,
                    notes=excluded.notes,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    int(activity_id),
                    values.get("perceived_effort"),
                    values.get("soreness"),
                    int(bool(values.get("pain"))),
                    values.get("sleep_quality"),
                    values.get("energy_level"),
                    (
                        None
                        if values.get("followed_walk_strategy") is None
                        else int(bool(values.get("followed_walk_strategy")))
                    ),
                    values.get("unrecorded_walk_minutes"),
                    values.get("notes"),
                ),
            )

    def get_checkin(self, activity_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM activity_checkins WHERE activity_id = ?",
                (int(activity_id),),
            ).fetchone()
        return dict(row) if row else None

    def all_activities(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT detail_json, streams_json, laps_json FROM activities "
                "ORDER BY start_date_local, id"
            ).fetchall()
            health_rows = conn.execute("SELECT * FROM activity_health").fetchall()
            checkin_rows = conn.execute("SELECT * FROM activity_checkins").fetchall()
            daily_rows = conn.execute("SELECT * FROM health_daily").fetchall()
        health_by_id = {int(row["activity_id"]): dict(row) for row in health_rows}
        checkin_by_id = {int(row["activity_id"]): dict(row) for row in checkin_rows}
        daily_by_date = {row["date"]: dict(row) for row in daily_rows}
        output = []
        for row in rows:
            detail = json.loads(row["detail_json"])
            activity_id = int(detail["id"])
            local_date = str(detail["start_date_local"])[:10]
            output.append({
                "detail": json.loads(row["detail_json"]),
                "streams": json.loads(row["streams_json"]) if row["streams_json"] else None,
                "laps": json.loads(row["laps_json"]) if row["laps_json"] else None,
                "health": health_by_id.get(activity_id),
                "daily_health": daily_by_date.get(local_date),
                "checkin": checkin_by_id.get(activity_id),
            })
        return output
