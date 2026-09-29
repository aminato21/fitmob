from __future__ import annotations

import csv
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

from app.analysis import build_run_rows, pace_text, public_run_row, safe_div
from app.config import Settings


RUN_FIELDS = [
    "id", "name", "sport_type", "start_date", "start_date_local", "timezone",
    "utc_offset", "distance_m", "distance_km", "moving_time_sec",
    "elapsed_time_sec", "stopped_time_sec", "moving_ratio", "pace_sec_per_km",
    "pace_min_per_km", "avg_speed_mps", "avg_speed_kmh", "max_speed_mps",
    "max_speed_kmh", "total_elevation_gain_m", "elev_high_m", "elev_low_m",
    "elevation_per_km", "has_heartrate", "average_heartrate", "max_heartrate",
    "hr_efficiency_index", "average_cadence", "cadence_available", "calories",
    "device_name", "gear_id", "gear_name", "shoe_distance_before_run_km",
    "manual", "private", "trainer", "commute", "flagged", "start_latlng",
    "end_latlng", "map_summary_polyline", "location_country", "location_city",
    "achievement_count", "pr_count", "day_of_week", "week_number",
    "week_start_date", "month", "year", "days_since_previous_run",
    "rolling_7d_distance_km", "rolling_30d_distance_km", "rolling_7d_runs",
    "rolling_30d_runs", "weekly_total_km", "weekly_run_count",
    "monthly_total_km", "monthly_run_count", "is_long_run",
    "is_easy_run_guess", "is_fast_run_guess", "is_progress_test_guess",
    "run_walk_detected", "estimated_walk_break_count",
    "estimated_total_walk_time_sec", "estimated_total_run_time_sec",
    "average_run_segment_duration_sec", "longest_continuous_run_estimate_sec",
    "session_type_guess", "effort_guess", "beginner_notes",
    "suggested_next_session_type", "injury_risk_flag",
    "sudden_volume_increase_flag", "too_many_hard_sessions_flag",
    "recovery_flag", "heart_rate_source", "heart_rate_sample_count",
    "sleep_duration_sec", "resting_heartrate", "daily_steps",
    "perceived_effort", "soreness", "pain_reported", "sleep_quality",
    "energy_level", "followed_walk_strategy", "unrecorded_walk_minutes",
    "checkin_notes", "notes",
]

SPLIT_FIELDS = [
    "activity_id", "split_number", "distance_m", "moving_time_sec",
    "elapsed_time_sec", "elevation_difference_m", "average_speed_mps",
    "pace_sec_per_km", "pace_min_per_km",
]

LAP_FIELDS = [
    "activity_id", "lap_id", "lap_index", "name", "distance_m",
    "moving_time_sec", "elapsed_time_sec", "total_elevation_gain_m",
    "average_speed_mps", "max_speed_mps", "average_cadence",
    "average_heartrate", "max_heartrate",
]

STREAM_FIELDS = [
    "activity_id", "stream_points_count", "avg_stream_heartrate",
    "max_stream_heartrate", "min_stream_heartrate", "avg_stream_cadence",
    "avg_stream_pace_sec_per_km", "fastest_1km_estimate_sec",
    "elevation_gain_from_stream_m", "time_in_hr_zones_if_possible",
    "has_gps_stream", "has_hr_stream", "has_cadence_stream",
]


def _csv_value(value: Any) -> Any:
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return value


def write_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(
            {key: _csv_value(row.get(key)) for key in fields} for row in rows
        )


def export_all(
    records: list[dict[str, Any]], settings: Settings
) -> dict[str, Any]:
    output = Path(settings.export_dir)
    output.mkdir(parents=True, exist_ok=True)
    run_rows, stream_analyses = build_run_rows(records, settings)

    split_rows: list[dict[str, Any]] = []
    lap_rows: list[dict[str, Any]] = []
    stream_rows: list[dict[str, Any]] = []
    raw: list[dict[str, Any]] = []
    for record in records:
        detail = record["detail"]
        activity_id = int(detail["id"])
        raw.append(detail)
        for index, split in enumerate(detail.get("splits_metric") or [], 1):
            distance_m = split.get("distance")
            moving = split.get("moving_time")
            pace = safe_div(
                float(moving) if moving is not None else None,
                float(distance_m) / 1000 if distance_m else None,
            )
            split_rows.append(
                {
                    "activity_id": activity_id,
                    "split_number": split.get("split") or index,
                    "distance_m": distance_m,
                    "moving_time_sec": moving,
                    "elapsed_time_sec": split.get("elapsed_time"),
                    "elevation_difference_m": split.get("elevation_difference"),
                    "average_speed_mps": split.get("average_speed"),
                    "pace_sec_per_km": pace,
                    "pace_min_per_km": pace_text(pace),
                }
            )
        laps = record.get("laps")
        if laps is None:
            laps = detail.get("laps") or []
        for index, lap in enumerate(laps, 1):
            lap_rows.append(
                {
                    "activity_id": activity_id,
                    "lap_id": lap.get("id"),
                    "lap_index": lap.get("lap_index") or index,
                    "name": lap.get("name"),
                    "distance_m": lap.get("distance"),
                    "moving_time_sec": lap.get("moving_time"),
                    "elapsed_time_sec": lap.get("elapsed_time"),
                    "total_elevation_gain_m": lap.get("total_elevation_gain"),
                    "average_speed_mps": lap.get("average_speed"),
                    "max_speed_mps": lap.get("max_speed"),
                    "average_cadence": lap.get("average_cadence"),
                    "average_heartrate": lap.get("average_heartrate"),
                    "max_heartrate": lap.get("max_heartrate"),
                }
            )
        stream_rows.append(
            {"activity_id": activity_id, **stream_analyses[activity_id]}
        )

    write_csv(output / "strava_runs.csv", RUN_FIELDS, run_rows)
    write_csv(output / "strava_splits.csv", SPLIT_FIELDS, split_rows)
    write_csv(output / "strava_laps.csv", LAP_FIELDS, lap_rows)
    write_csv(
        output / "strava_streams_summary.csv", STREAM_FIELDS, stream_rows
    )
    (output / "strava_raw.json").write_text(
        json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    summary = make_summary(run_rows)
    (output / "strava_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"runs": len(run_rows), "files": 6, "export_dir": str(output.resolve())}


def make_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total_km = sum(row["distance_km"] or 0 for row in rows)
    total_time = sum(row["moving_time_sec"] or 0 for row in rows)
    valid_paces = [row for row in rows if row["pace_sec_per_km"]]
    longest = max(rows, key=lambda row: row["distance_km"] or 0, default=None)
    fastest = min(valid_paces, key=lambda row: row["pace_sec_per_km"], default=None)
    weekly: defaultdict[str, dict[str, Any]] = defaultdict(
        lambda: {"runs": 0, "distance_km": 0.0, "moving_time_sec": 0.0}
    )
    monthly: defaultdict[str, dict[str, Any]] = defaultdict(
        lambda: {"runs": 0, "distance_km": 0.0, "moving_time_sec": 0.0}
    )
    for row in rows:
        week = weekly[row["week_start_date"]]
        month = monthly[f"{row['year']:04d}-{row['month']:02d}"]
        for bucket in (week, month):
            bucket["runs"] += 1
            bucket["distance_km"] += row["distance_km"] or 0
            bucket["moving_time_sec"] += row["moving_time_sec"] or 0
    for group in (weekly, monthly):
        for bucket in group.values():
            bucket["distance_km"] = round(bucket["distance_km"], 3)

    weeks = sorted(weekly)
    recent = weeks[-4:]
    previous = weeks[-8:-4]
    recent_km = sum(weekly[key]["distance_km"] for key in recent)
    previous_km = sum(weekly[key]["distance_km"] for key in previous)
    progression = {
        "method": "recent 4 completed/data weeks compared with the preceding 4",
        "recent_4_week_distance_km": round(recent_km, 3),
        "previous_4_week_distance_km": round(previous_km, 3),
        "distance_change_percent": (
            round((recent_km / previous_km - 1) * 100, 1) if previous_km else None
        ),
        "interpretation": (
            "not_enough_data"
            if len(weeks) < 5
            else (
                "increasing"
                if previous_km and recent_km > previous_km * 1.1
                else "decreasing"
                if previous_km and recent_km < previous_km * 0.9
                else "stable"
            )
        ),
    }
    return {
        "total_runs": len(rows),
        "total_distance_km": round(total_km, 3),
        "total_time_sec": total_time,
        "average_pace_sec_per_km": safe_div(total_time, total_km),
        "average_pace_min_per_km": pace_text(safe_div(total_time, total_km)),
        "longest_run": (
            {"id": longest["id"], "distance_km": longest["distance_km"]}
            if longest
            else None
        ),
        "best_pace": (
            {
                "id": fastest["id"],
                "pace_sec_per_km": fastest["pace_sec_per_km"],
                "pace_min_per_km": fastest["pace_min_per_km"],
            }
            if fastest
            else None
        ),
        "weekly_totals": dict(weekly),
        "monthly_totals": dict(monthly),
        "progression_trend": progression,
    }
