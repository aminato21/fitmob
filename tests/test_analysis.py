from __future__ import annotations

import csv
import json

from app.analysis import (
    analyze_streams,
    build_run_rows,
    compute_consistency_score,
    compute_current_capabilities,
    compute_personal_bests,
    compute_walk_break_trend,
)
from app.config import Settings
from app.exporter import export_all


def sample_detail(activity_id: int = 1) -> dict:
    return {
        "id": activity_id,
        "name": "Gentle run walk",
        "sport_type": "Run",
        "start_date": "2026-01-03T09:00:00Z",
        "start_date_local": "2026-01-03T10:00:00Z",
        "distance": 3000.0,
        "moving_time": 1800,
        "elapsed_time": 1900,
        "average_speed": 1.6667,
        "max_speed": 3.0,
        "total_elevation_gain": 20.0,
        "has_heartrate": False,
        "splits_metric": [
            {
                "split": 1,
                "distance": 1000,
                "moving_time": 600,
                "elapsed_time": 620,
                "elevation_difference": 4,
                "average_speed": 1.667,
            }
        ],
    }


def sample_streams() -> dict:
    return {
        "time": {"data": [0, 30, 60, 90, 120, 150, 180]},
        "distance": {"data": [0, 75, 150, 190, 230, 305, 380]},
        "velocity_smooth": {"data": [2.5, 2.5, 2.5, 1.3, 1.3, 2.5, 2.5]},
        "moving": {"data": [True] * 7},
        "latlng": {"data": [[1, 1]] * 7},
    }


def test_run_walk_stream_detection():
    result = analyze_streams(sample_streams(), Settings())
    assert result["run_walk_detected"] is True
    assert result["estimated_walk_break_count"] == 1
    assert result["has_gps_stream"] is True
    assert result["has_hr_stream"] is False
    assert result["time_in_hr_zones_if_possible"] is None


def test_derived_fields_and_nulls():
    rows, _ = build_run_rows(
        [{"detail": sample_detail(), "streams": sample_streams(), "laps": None}],
        Settings(),
    )
    row = rows[0]
    assert row["distance_km"] == 3.0
    assert row["pace_sec_per_km"] == 600
    assert row["pace_min_per_km"] == "10:00"
    assert row["average_heartrate"] is None
    assert row["session_type_guess"] in {"easy_run_walk", "long_run_walk"}


def test_all_exports_are_created(tmp_path):
    settings = Settings(export_dir=tmp_path)
    result = export_all(
        [{"detail": sample_detail(), "streams": sample_streams(), "laps": []}],
        settings,
    )
    assert result["runs"] == 1
    expected = {
        "strava_runs.csv",
        "strava_splits.csv",
        "strava_laps.csv",
        "strava_streams_summary.csv",
        "strava_raw.json",
        "strava_summary.json",
    }
    assert {path.name for path in tmp_path.iterdir()} == expected
    raw = json.loads((tmp_path / "strava_raw.json").read_text("utf-8"))
    assert raw[0]["id"] == 1
    with (tmp_path / "strava_runs.csv").open(
        encoding="utf-8-sig", newline=""
    ) as handle:
        row = next(csv.DictReader(handle))
    assert row["run_walk_detected"] == "True"


def test_progress_metrics_track_comparable_walk_breaks():
    first = sample_detail(1)
    second = sample_detail(2)
    second["start_date"] = "2026-01-10T09:00:00Z"
    second["start_date_local"] = "2026-01-10T10:00:00Z"
    continuous_stream = sample_streams()
    continuous_stream["velocity_smooth"]["data"] = [2.5] * 7
    rows, _ = build_run_rows(
        [
            {"detail": first, "streams": sample_streams(), "laps": []},
            {"detail": second, "streams": continuous_stream, "laps": []},
        ],
        Settings(_env_file=None),
    )
    walk = compute_walk_break_trend(rows)
    assert walk["overall_direction"] == "improving"
    assert walk["comparable_distance_groups"][0]["break_count_change"] == -1

    bests = compute_personal_bests(rows)
    assert bests["longest_run"]["distance_km"] == 3.0
    assert bests["longest_continuous_segment"] is not None

    capabilities = compute_current_capabilities(rows)
    assert capabilities["estimated_continuous_km"] == 3.0
    assert capabilities["five_k_with_breaks"] is False

    consistency = compute_consistency_score(rows, 2)
    assert 0 <= consistency["score"] <= 100
    assert consistency["target_sessions_per_week"] == 2
