from __future__ import annotations

import math
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from app.config import Settings

RUN_TYPES = {"Run", "TrailRun", "VirtualRun"}


def number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def safe_div(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator


def pace_text(seconds: float | None) -> str | None:
    if seconds is None or not math.isfinite(seconds):
        return None
    rounded = max(0, round(seconds))
    return f"{rounded // 60:02d}:{rounded % 60:02d}"


def parse_local(value: str) -> datetime:
    # Strava's start_date_local often ends in Z even though it represents wall time.
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)


def stream_data(streams: dict[str, Any] | None, key: str) -> list[Any] | None:
    if not streams or key not in streams:
        return None
    value = streams[key]
    if isinstance(value, dict):
        value = value.get("data")
    return value if isinstance(value, list) else None


def _weighted_average(values: list[float], times: list[float] | None) -> float | None:
    if not values:
        return None
    if not times or len(times) != len(values):
        return statistics.fmean(values)
    total = 0.0
    duration = 0.0
    for index in range(1, len(values)):
        dt = max(0.0, times[index] - times[index - 1])
        total += values[index] * dt
        duration += dt
    return safe_div(total, duration) if duration else statistics.fmean(values)


def _fastest_distance_window(
    times: list[float] | None, distances: list[float] | None, target_m: float = 1000
) -> float | None:
    if not times or not distances or len(times) != len(distances):
        return None
    best: float | None = None
    left = 0
    for right in range(len(distances)):
        while left < right and distances[right] - distances[left] >= target_m:
            span_distance = distances[right] - distances[left]
            span_time = times[right] - times[left]
            estimate = span_time * target_m / span_distance if span_distance else None
            if estimate and estimate > 0:
                best = estimate if best is None else min(best, estimate)
            left += 1
    return best


def _segments(mask: list[bool], times: list[float]) -> list[tuple[float, float]]:
    result: list[tuple[float, float]] = []
    start: int | None = None
    for index, active in enumerate(mask):
        if active and start is None:
            start = index
        if start is not None and (not active or index == len(mask) - 1):
            end = index if active and index == len(mask) - 1 else max(start, index - 1)
            result.append((times[start], times[end]))
            start = None
    return result


def analyze_streams(
    streams: dict[str, Any] | None, settings: Settings
) -> dict[str, Any]:
    times_raw = stream_data(streams, "time")
    velocity_raw = stream_data(streams, "velocity_smooth")
    distance_raw = stream_data(streams, "distance")
    hr_raw = stream_data(streams, "heartrate")
    cadence_raw = stream_data(streams, "cadence")
    altitude_raw = stream_data(streams, "altitude")
    moving_raw = stream_data(streams, "moving")
    gps_raw = stream_data(streams, "latlng")

    times = [float(x) for x in times_raw] if times_raw else []
    velocity = [float(x) for x in velocity_raw] if velocity_raw else []
    distances = [float(x) for x in distance_raw] if distance_raw else []
    hr = [float(x) for x in hr_raw if isinstance(x, (int, float))] if hr_raw else []
    cadence = (
        [float(x) for x in cadence_raw if isinstance(x, (int, float))]
        if cadence_raw
        else []
    )
    altitude = [float(x) for x in altitude_raw] if altitude_raw else []

    point_counts = [
        len(values)
        for values in (
            times_raw,
            velocity_raw,
            distance_raw,
            hr_raw,
            cadence_raw,
            altitude_raw,
            gps_raw,
        )
        if values
    ]

    zone_times: dict[str, float] | None = None
    if settings.hr_zone_bounds and len(settings.hr_zone_bounds) == 5 and hr_raw and times:
        zone_times = {f"z{index}": 0.0 for index in range(1, 6)}
        for index in range(1, min(len(hr_raw), len(times))):
            value = hr_raw[index]
            if not isinstance(value, (int, float)):
                continue
            zone = next(
                (
                    zone_index
                    for zone_index, upper in enumerate(settings.hr_zone_bounds, 1)
                    if value <= upper
                ),
                5,
            )
            zone_times[f"z{zone}"] += max(0.0, times[index] - times[index - 1])

    elevation_gain = None
    if altitude:
        elevation_gain = sum(
            max(0.0, altitude[index] - altitude[index - 1])
            for index in range(1, len(altitude))
        )

    moving_velocity = [
        value
        for index, value in enumerate(velocity)
        if not moving_raw or index >= len(moving_raw) or moving_raw[index]
    ]
    mean_velocity = statistics.fmean(moving_velocity) if moving_velocity else None
    avg_pace = safe_div(1000.0, mean_velocity)
    fastest_km = (
        _fastest_distance_window(times, distances)
        if times and distances
        else None
    )

    walk_metrics = {
        "run_walk_detected": None,
        "estimated_walk_break_count": None,
        "estimated_total_walk_time_sec": None,
        "estimated_total_run_time_sec": None,
        "average_run_segment_duration_sec": None,
        "longest_continuous_run_estimate_sec": None,
        "_fast_repetition_count": 0,
    }
    if times and velocity and len(times) == len(velocity):
        walk_mask = [
            (not bool(moving_raw[i]) if moving_raw and i < len(moving_raw) else False)
            or speed <= settings.walk_speed_threshold_mps
            for i, speed in enumerate(velocity)
        ]
        walk_segments = [
            item
            for item in _segments(walk_mask, times)
            if item[1] - item[0] >= settings.min_walk_break_sec
        ]
        run_mask = [not item for item in walk_mask]
        run_segments = [
            item
            for item in _segments(run_mask, times)
            if item[1] - item[0] >= settings.min_walk_break_sec
        ]
        walk_time = sum(end - start for start, end in walk_segments)
        run_time = sum(end - start for start, end in run_segments)
        run_durations = [end - start for start, end in run_segments]

        fast_repetitions = 0
        if moving_velocity:
            median_velocity = statistics.median(moving_velocity)
            fast_mask = [speed >= median_velocity * 1.25 for speed in velocity]
            fast_repetitions = sum(
                1
                for start, end in _segments(fast_mask, times)
                if end - start >= 30
            )
        walk_metrics = {
            "run_walk_detected": bool(walk_segments),
            "estimated_walk_break_count": len(walk_segments),
            "estimated_total_walk_time_sec": round(walk_time),
            "estimated_total_run_time_sec": round(run_time),
            "average_run_segment_duration_sec": (
                round(statistics.fmean(run_durations), 1) if run_durations else None
            ),
            "longest_continuous_run_estimate_sec": (
                round(max(run_durations)) if run_durations else None
            ),
            "_fast_repetition_count": fast_repetitions,
        }

    return {
        "stream_points_count": max(point_counts, default=0),
        "avg_stream_heartrate": round(statistics.fmean(hr), 2) if hr else None,
        "max_stream_heartrate": max(hr, default=None),
        "min_stream_heartrate": min(hr, default=None),
        "avg_stream_cadence": (
            round(_weighted_average(cadence, times), 2) if cadence else None
        ),
        "avg_stream_pace_sec_per_km": round(avg_pace, 2) if avg_pace else None,
        "fastest_1km_estimate_sec": (
            round(fastest_km, 2) if fastest_km is not None else None
        ),
        "elevation_gain_from_stream_m": (
            round(elevation_gain, 2) if elevation_gain is not None else None
        ),
        "time_in_hr_zones_if_possible": zone_times,
        "has_gps_stream": bool(gps_raw),
        "has_hr_stream": bool(hr_raw),
        "has_cadence_stream": bool(cadence_raw),
        **walk_metrics,
    }


def build_run_rows(
    records: list[dict[str, Any]], settings: Settings
) -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    stream_analyses: dict[int, dict[str, Any]] = {}
    gear_distance: defaultdict[str, float] = defaultdict(float)

    for record in sorted(records, key=lambda item: item["detail"]["start_date_local"]):
        detail = record["detail"]
        health = record.get("health") or {}
        daily_health = record.get("daily_health") or {}
        checkin = record.get("checkin") or {}
        activity_id = int(detail["id"])
        local_dt = parse_local(detail["start_date_local"])
        iso = local_dt.isocalendar()
        week_start = local_dt.date() - timedelta(days=local_dt.weekday())
        distance_m = number(detail.get("distance"))
        distance_km = safe_div(distance_m, 1000)
        moving = number(detail.get("moving_time"))
        elapsed = number(detail.get("elapsed_time"))
        avg_speed = number(detail.get("average_speed"))
        max_speed = number(detail.get("max_speed"))
        elevation = number(detail.get("total_elevation_gain"))
        avg_hr = number(detail.get("average_heartrate"))
        if avg_hr is None:
            avg_hr = number(health.get("average_heartrate"))
        max_hr_value = number(detail.get("max_heartrate"))
        if max_hr_value is None:
            max_hr_value = number(health.get("max_heartrate"))
        gear = detail.get("gear") if isinstance(detail.get("gear"), dict) else {}
        gear_id = detail.get("gear_id")
        streams = analyze_streams(record.get("streams"), settings)
        stream_analyses[activity_id] = streams

        row = {
            "id": activity_id,
            "name": detail.get("name"),
            "sport_type": detail.get("sport_type") or detail.get("type"),
            "start_date": detail.get("start_date"),
            "start_date_local": detail.get("start_date_local"),
            "timezone": detail.get("timezone"),
            "utc_offset": detail.get("utc_offset"),
            "distance_m": distance_m,
            "distance_km": round(distance_km, 3) if distance_km is not None else None,
            "moving_time_sec": moving,
            "elapsed_time_sec": elapsed,
            "stopped_time_sec": (
                max(0.0, elapsed - moving)
                if elapsed is not None and moving is not None
                else None
            ),
            "moving_ratio": safe_div(moving, elapsed),
            "pace_sec_per_km": safe_div(moving, distance_km),
            "pace_min_per_km": pace_text(safe_div(moving, distance_km)),
            "avg_speed_mps": avg_speed,
            "avg_speed_kmh": avg_speed * 3.6 if avg_speed is not None else None,
            "max_speed_mps": max_speed,
            "max_speed_kmh": max_speed * 3.6 if max_speed is not None else None,
            "total_elevation_gain_m": elevation,
            "elev_high_m": number(detail.get("elev_high")),
            "elev_low_m": number(detail.get("elev_low")),
            "elevation_per_km": safe_div(elevation, distance_km),
            "has_heartrate": bool(
                detail.get("has_heartrate") or avg_hr is not None
            ),
            "average_heartrate": avg_hr,
            "max_heartrate": max_hr_value,
            "heart_rate_source": (
                "strava_activity"
                if detail.get("average_heartrate") is not None
                else "apple_health"
                if avg_hr is not None
                else None
            ),
            "heart_rate_sample_count": health.get("sample_count"),
            "hr_efficiency_index": safe_div(
                avg_hr, avg_speed * 3.6 if avg_speed is not None else None
            ),
            "average_cadence": number(detail.get("average_cadence")),
            "cadence_available": detail.get("average_cadence") is not None,
            "calories": number(detail.get("calories")),
            "device_name": detail.get("device_name"),
            "gear_id": gear_id,
            "gear_name": gear.get("name"),
            "shoe_distance_before_run_km": (
                round(gear_distance[str(gear_id)], 3) if gear_id else None
            ),
            "manual": detail.get("manual"),
            "private": detail.get("private"),
            "trainer": detail.get("trainer"),
            "commute": detail.get("commute"),
            "flagged": detail.get("flagged"),
            "start_latlng": detail.get("start_latlng"),
            "end_latlng": detail.get("end_latlng"),
            "map_summary_polyline": (
                detail.get("map", {}).get("summary_polyline")
                if isinstance(detail.get("map"), dict)
                else None
            ),
            "location_country": detail.get("location_country"),
            "location_city": detail.get("location_city"),
            "achievement_count": detail.get("achievement_count"),
            "pr_count": detail.get("pr_count"),
            "day_of_week": local_dt.strftime("%A"),
            "week_number": iso.week,
            "week_start_date": week_start.isoformat(),
            "month": local_dt.month,
            "year": local_dt.year,
            "_date": local_dt.date(),
            "_week_key": (iso.year, iso.week),
            "_month_key": (local_dt.year, local_dt.month),
            "_fast_repetition_count": streams["_fast_repetition_count"],
            "run_walk_detected": streams["run_walk_detected"],
            "estimated_walk_break_count": streams["estimated_walk_break_count"],
            "estimated_total_walk_time_sec": streams["estimated_total_walk_time_sec"],
            "estimated_total_run_time_sec": streams["estimated_total_run_time_sec"],
            "average_run_segment_duration_sec": streams[
                "average_run_segment_duration_sec"
            ],
            "longest_continuous_run_estimate_sec": streams[
                "longest_continuous_run_estimate_sec"
            ],
            "sleep_duration_sec": number(daily_health.get("sleep_duration_sec")),
            "resting_heartrate": number(
                daily_health.get("resting_heartrate")
            ),
            "daily_steps": number(daily_health.get("steps")),
            "perceived_effort": checkin.get("perceived_effort"),
            "soreness": checkin.get("soreness"),
            "pain_reported": (
                bool(checkin.get("pain")) if checkin else None
            ),
            "sleep_quality": checkin.get("sleep_quality"),
            "energy_level": checkin.get("energy_level"),
            "followed_walk_strategy": (
                bool(checkin.get("followed_walk_strategy"))
                if checkin.get("followed_walk_strategy") is not None
                else None
            ),
            "unrecorded_walk_minutes": checkin.get(
                "unrecorded_walk_minutes"
            ),
            "checkin_notes": checkin.get("notes"),
            "notes": detail.get("description"),
        }
        rows.append(row)
        if gear_id and distance_km:
            gear_distance[str(gear_id)] += distance_km

    _add_contextual_metrics(rows, settings)
    return rows, stream_analyses


def _window(rows: list[dict[str, Any]], current: dict[str, Any], days: int) -> list:
    start = current["_date"] - timedelta(days=days - 1)
    return [row for row in rows if start <= row["_date"] <= current["_date"]]


def _add_contextual_metrics(rows: list[dict[str, Any]], settings: Settings) -> None:
    weekly: defaultdict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    monthly: defaultdict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        weekly[row["_week_key"]].append(row)
        monthly[row["_month_key"]].append(row)

    previous_date: date | None = None
    for index, row in enumerate(rows):
        row["days_since_previous_run"] = (
            (row["_date"] - previous_date).days if previous_date else None
        )
        previous_date = row["_date"]
        seven = _window(rows, row, 7)
        thirty = _window(rows, row, 30)
        row["rolling_7d_distance_km"] = round(
            sum(item["distance_km"] or 0 for item in seven), 3
        )
        row["rolling_30d_distance_km"] = round(
            sum(item["distance_km"] or 0 for item in thirty), 3
        )
        row["rolling_7d_runs"] = len(seven)
        row["rolling_30d_runs"] = len(thirty)
        week = weekly[row["_week_key"]]
        month = monthly[row["_month_key"]]
        row["weekly_total_km"] = round(
            sum(item["distance_km"] or 0 for item in week), 3
        )
        row["weekly_run_count"] = len(week)
        row["monthly_total_km"] = round(
            sum(item["distance_km"] or 0 for item in month), 3
        )
        row["monthly_run_count"] = len(month)

        prior = [
            item
            for item in rows[:index]
            if item["_date"] >= row["_date"] - timedelta(days=30)
            and item["pace_sec_per_km"]
        ]
        recent_paces = [item["pace_sec_per_km"] for item in prior]
        normal_pace = statistics.median(recent_paces) if len(recent_paces) >= 3 else None
        faster_by = settings.fast_pace_improvement_percent / 100
        row["is_fast_run_guess"] = bool(
            normal_pace
            and row["pace_sec_per_km"]
            and row["pace_sec_per_km"] < normal_pace * (1 - faster_by)
        )

        if settings.max_hr and row["average_heartrate"]:
            easy = row["average_heartrate"] <= settings.max_hr * 0.75
            hard = row["average_heartrate"] >= settings.max_hr * 0.88
        elif normal_pace and row["pace_sec_per_km"]:
            easy = row["pace_sec_per_km"] >= normal_pace * 0.95
            hard = row["pace_sec_per_km"] < normal_pace * (1 - faster_by)
        else:
            easy = not row["is_fast_run_guess"] and (
                row["run_walk_detected"] is True or row["moving_ratio"] in (None, 0)
                or row["moving_ratio"] < 0.98
            )
            hard = False
        row["is_easy_run_guess"] = bool(easy and not row["is_fast_run_guess"])
        row["effort_guess"] = "hard" if hard else ("easy" if easy else "unknown")

        name = (row["name"] or "").lower()
        explicit_test = any(
            term in name for term in ("test", "benchmark", "time trial", "race")
        )
        comparable = [
            item
            for item in prior
            if item["distance_km"]
            and row["distance_km"]
            and abs(item["distance_km"] - row["distance_km"]) / row["distance_km"]
            <= 0.1
            and item["sport_type"] == row["sport_type"]
            and item.get("trainer") == row.get("trainer")
        ]
        comparable_pace = (
            statistics.median(
                item["pace_sec_per_km"]
                for item in comparable
                if item["pace_sec_per_km"]
            )
            if comparable
            else None
        )
        row["is_progress_test_guess"] = bool(
            explicit_test
            and comparable_pace
            and row["pace_sec_per_km"]
            and row["pace_sec_per_km"] < comparable_pace * 0.97
        )

    # A long run is one of the week's longest sessions only when it also looks easy.
    for week_rows in weekly.values():
        longest = max((row["distance_km"] or 0 for row in week_rows), default=0)
        for row in week_rows:
            row["is_long_run"] = bool(
                longest
                and row["is_easy_run_guess"]
                and (row["distance_km"] or 0)
                >= longest * settings.long_run_weekly_percentile
            )

    for index, row in enumerate(rows):
        name = (row["name"] or "").lower()
        named_intervals = any(
            term in name for term in ("interval", "repeats", "fartlek")
        )
        possible_intervals = named_intervals or row["_fast_repetition_count"] >= 3
        walk_time = row["estimated_total_walk_time_sec"]
        elapsed = row["elapsed_time_sec"]
        mostly_walking = bool(walk_time is not None and elapsed and walk_time / elapsed >= 0.6)
        if mostly_walking:
            session = "recovery_walk"
        elif possible_intervals:
            session = "possible_intervals"
        elif row["is_long_run"]:
            session = "long_run_walk"
        elif row["is_fast_run_guess"] and (row["distance_km"] or 0) <= 5:
            session = "fast_short_run"
        elif row["is_easy_run_guess"]:
            session = "easy_run_walk"
        else:
            session = "unknown"
        row["session_type_guess"] = session
        if session == "possible_intervals" and row["effort_guess"] == "unknown":
            row["effort_guess"] = "moderate"

        current_7d = row["rolling_7d_distance_km"]
        prior_period = [
            item
            for item in rows
            if row["_date"] - timedelta(days=13)
            <= item["_date"]
            <= row["_date"] - timedelta(days=7)
        ]
        prior_km = sum(item["distance_km"] or 0 for item in prior_period)
        row["sudden_volume_increase_flag"] = bool(
            prior_km > 0
            and current_7d >= prior_km * settings.sudden_volume_ratio
            and current_7d - prior_km >= settings.sudden_volume_min_increase_km
        )
        recent_rows = _window(rows[: index + 1], row, 7)
        hard_count = sum(item["effort_guess"] == "hard" for item in recent_rows)
        row["too_many_hard_sessions_flag"] = (
            hard_count > settings.max_hard_sessions_7d
        )
        row["recovery_flag"] = bool(
            row.get("pain_reported")
            or (row.get("soreness") or 0) >= 7
            or (
                row.get("energy_level") is not None
                and row["energy_level"] <= 2
            )
            or (
                row.get("sleep_quality") is not None
                and row["sleep_quality"] <= 2
            )
        )
        row["injury_risk_flag"] = bool(
            row["sudden_volume_increase_flag"]
            or row["too_many_hard_sessions_flag"]
            or row["recovery_flag"]
        )
        if row["injury_risk_flag"] or row["effort_guess"] == "hard":
            row["suggested_next_session_type"] = "recovery_walk"
            row["beginner_notes"] = (
                "Keep the next session gentle; consider extra rest if sore or unusually tired."
            )
        elif row["is_long_run"]:
            row["suggested_next_session_type"] = "recovery_walk"
            row["beginner_notes"] = "A longer easy session; follow it with gentle recovery."
        else:
            row["suggested_next_session_type"] = "easy_run_walk"
            row["beginner_notes"] = (
                "A conservative classification; use comfort and symptoms over the label."
            )


def compute_recovery_summary(
    rows: list[dict[str, Any]], daily_health: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Summarize recovery inputs without diagnosing or inventing readiness."""
    daily_health = daily_health or []
    recent_daily = daily_health[-14:]
    sleep_values = [
        float(item["sleep_duration_sec"]) / 3600
        for item in recent_daily
        if item.get("sleep_duration_sec")
    ]
    resting_values = [
        float(item["resting_heartrate"])
        for item in recent_daily
        if item.get("resting_heartrate")
    ]
    checkins = [row for row in rows[-10:] if row.get("perceived_effort") is not None]
    latest = checkins[-1] if checkins else None
    flags = [
        row
        for row in rows[-5:]
        if row.get("pain_reported")
        or (row.get("soreness") or 0) >= 7
        or row.get("recovery_flag")
    ]
    return {
        "days_with_health_data": len(recent_daily),
        "average_sleep_hours_14d": (
            round(statistics.mean(sleep_values), 2) if sleep_values else None
        ),
        "average_resting_heartrate_14d": (
            round(statistics.mean(resting_values), 1)
            if resting_values
            else None
        ),
        "checkins_available": len(checkins),
        "latest_checkin": (
            {
                "date": latest["_date"].isoformat(),
                "perceived_effort": latest.get("perceived_effort"),
                "soreness": latest.get("soreness"),
                "pain_reported": latest.get("pain_reported"),
                "sleep_quality": latest.get("sleep_quality"),
                "energy_level": latest.get("energy_level"),
                "followed_walk_strategy": latest.get(
                    "followed_walk_strategy"
                ),
                "unrecorded_walk_minutes": latest.get(
                    "unrecorded_walk_minutes"
                ),
            }
            if latest
            else None
        ),
        "recent_recovery_flags": len(flags),
        "interpretation": (
            "recovery_attention"
            if flags
            else "no_subjective_warning"
            if checkins
            else "not_enough_subjective_data"
        ),
    }


def public_run_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if not key.startswith("_")}


def compute_walk_break_trend(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare walk breaks across similar-distance sessions."""
    series = []
    groups: defaultdict[float, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        breaks = row.get("estimated_walk_break_count")
        distance = row.get("distance_km")
        if breaks is None or not distance:
            continue
        walk_time = row.get("estimated_total_walk_time_sec")
        elapsed = row.get("elapsed_time_sec")
        item = {
            "date": row["_date"].isoformat(),
            "distance_km": round(distance, 2),
            "break_count": int(breaks),
            "walk_time_sec": walk_time,
            "walk_ratio": (
                round(walk_time / elapsed, 3)
                if walk_time is not None and elapsed
                else None
            ),
        }
        series.append(item)
        groups[round(distance * 2) / 2].append(item)

    comparable = []
    for distance_band, items in sorted(groups.items()):
        if len(items) < 2:
            continue
        first, latest = items[0], items[-1]
        change = latest["break_count"] - first["break_count"]
        direction = "improving" if change < 0 else "increasing" if change > 0 else "stable"
        comparable.append(
            {
                "distance_band_km": distance_band,
                "sessions": len(items),
                "first_break_count": first["break_count"],
                "latest_break_count": latest["break_count"],
                "break_count_change": change,
                "direction": direction,
            }
        )
    improving = sum(item["direction"] == "improving" for item in comparable)
    increasing = sum(item["direction"] == "increasing" for item in comparable)
    overall = (
        "improving"
        if improving > increasing
        else "increasing"
        if increasing > improving
        else "stable_or_insufficient_data"
    )
    if comparable:
        best = min(comparable, key=lambda item: item["break_count_change"])
        summary = (
            f"Comparable {best['distance_band_km']:.1f} km sessions changed from "
            f"{best['first_break_count']} to {best['latest_break_count']} estimated "
            "walking breaks."
        )
    else:
        summary = "Not enough comparable sessions with usable streams yet."
    return {
        "overall_direction": overall,
        "summary": summary,
        "series": series,
        "comparable_distance_groups": comparable,
    }


def compute_personal_bests(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "longest_run": None,
            "best_5k_pace": None,
            "best_overall_pace": None,
            "longest_continuous_segment": None,
        }

    longest = max(rows, key=lambda row: row.get("distance_km") or 0)
    valid_pace = [
        row
        for row in rows
        if row.get("pace_sec_per_km") and (row.get("distance_km") or 0) >= 2
    ]
    best_pace = min(valid_pace, key=lambda row: row["pace_sec_per_km"], default=None)
    five_k = [
        row
        for row in rows
        if 4.5 <= (row.get("distance_km") or 0) <= 5.5
        and row.get("pace_sec_per_km")
    ]
    best_5k = min(five_k, key=lambda row: row["pace_sec_per_km"], default=None)
    continuous = [
        row
        for row in rows
        if row.get("longest_continuous_run_estimate_sec") is not None
    ]
    longest_segment = max(
        continuous,
        key=lambda row: row["longest_continuous_run_estimate_sec"],
        default=None,
    )

    def pace_result(row: dict[str, Any] | None) -> dict[str, Any] | None:
        if not row:
            return None
        return {
            "date": row["_date"].isoformat(),
            "distance_km": row.get("distance_km"),
            "pace_sec_per_km": row.get("pace_sec_per_km"),
            "pace_min_per_km": row.get("pace_min_per_km"),
            "estimated_walk_break_count": row.get("estimated_walk_break_count"),
        }

    return {
        "longest_run": {
            "date": longest["_date"].isoformat(),
            "distance_km": longest.get("distance_km"),
            "estimated_walk_break_count": longest.get(
                "estimated_walk_break_count"
            ),
        },
        "best_5k_pace": pace_result(best_5k),
        "best_overall_pace": pace_result(best_pace),
        "longest_continuous_segment": (
            {
                "date": longest_segment["_date"].isoformat(),
                "duration_sec": longest_segment.get(
                    "longest_continuous_run_estimate_sec"
                ),
                "estimated_distance_km": round(
                    (
                        longest_segment["longest_continuous_run_estimate_sec"]
                        * (longest_segment.get("avg_speed_mps") or 0)
                    )
                    / 1000,
                    2,
                ),
            }
            if longest_segment
            else None
        ),
    }


def compute_current_capabilities(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "estimated_continuous_km": None,
            "five_k_with_breaks": False,
            "seven_k_with_breaks": False,
            "recent_typical_distance_km": None,
            "longest_completed_km": None,
        }
    recent_cutoff = rows[-1]["_date"] - timedelta(days=60)
    recent = [row for row in rows if row["_date"] >= recent_cutoff] or rows
    continuous_estimates = []
    for row in recent:
        distance = row.get("distance_km") or 0
        if row.get("run_walk_detected") is False:
            continuous_estimates.append(distance)
        elif (
            row.get("longest_continuous_run_estimate_sec")
            and row.get("avg_speed_mps")
        ):
            continuous_estimates.append(
                row["longest_continuous_run_estimate_sec"]
                * row["avg_speed_mps"]
                / 1000
            )
    distances = [row.get("distance_km") or 0 for row in recent]
    longest = max(distances, default=0)
    return {
        "estimated_continuous_km": (
            round(max(continuous_estimates), 2) if continuous_estimates else None
        ),
        "five_k_with_breaks": any(distance >= 4.8 for distance in distances),
        "seven_k_with_breaks": any(distance >= 6.8 for distance in distances),
        "recent_typical_distance_km": (
            round(statistics.median(distances), 2) if distances else None
        ),
        "longest_completed_km": round(longest, 2),
        "evidence_sessions": len(recent),
    }


def compute_consistency_score(
    rows: list[dict[str, Any]],
    target_sessions_per_week: int = 2,
    as_of: date | None = None,
) -> dict[str, Any]:
    if not rows:
        return {
            "score": 0,
            "active_weeks": 0,
            "observed_weeks": 0,
            "average_runs_per_week": 0,
            "average_gap_days": None,
            "label": "no_data",
        }
    end = as_of or date.today()
    end = max(end, rows[-1]["_date"])
    start = max(rows[0]["_date"], end - timedelta(days=55))
    observed_weeks = max(1, math.ceil(((end - start).days + 1) / 7))
    recent = [row for row in rows if row["_date"] >= start]
    active_week_keys = {row["_week_key"] for row in recent}
    average_runs = len(recent) / observed_weeks
    gaps = [
        (recent[index]["_date"] - recent[index - 1]["_date"]).days
        for index in range(1, len(recent))
    ]
    active_component = len(active_week_keys) / observed_weeks
    frequency_component = min(1.0, average_runs / max(1, target_sessions_per_week))
    long_gap_penalty = min(0.25, sum(gap > 10 for gap in gaps) * 0.05)
    score = round(max(0, min(100, (active_component * 55 + frequency_component * 45) * (1 - long_gap_penalty))))
    label = "consistent" if score >= 75 else "building" if score >= 50 else "irregular"
    return {
        "score": score,
        "active_weeks": len(active_week_keys),
        "observed_weeks": observed_weeks,
        "average_runs_per_week": round(average_runs, 2),
        "average_gap_days": round(statistics.fmean(gaps), 1) if gaps else None,
        "target_sessions_per_week": target_sessions_per_week,
        "label": label,
    }
