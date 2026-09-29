from __future__ import annotations

import math
import statistics
import zipfile
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from app.db import Database


HEART_RATE = "HKQuantityTypeIdentifierHeartRate"
RESTING_HR = "HKQuantityTypeIdentifierRestingHeartRate"
STEPS = "HKQuantityTypeIdentifierStepCount"
ACTIVE_ENERGY = "HKQuantityTypeIdentifierActiveEnergyBurned"
OXYGEN = "HKQuantityTypeIdentifierOxygenSaturation"
SLEEP = "HKCategoryTypeIdentifierSleepAnalysis"
ASLEEP_VALUES = {
    "HKCategoryValueSleepAnalysisAsleep",
    "HKCategoryValueSleepAnalysisAsleepCore",
    "HKCategoryValueSleepAnalysisAsleepDeep",
    "HKCategoryValueSleepAnalysisAsleepREM",
    "HKCategoryValueSleepAnalysisAsleepUnspecified",
}


def _health_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    for pattern in ("%Y-%m-%d %H:%M:%S %z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(value, pattern)
        except ValueError:
            continue
    return None


def _number(value: str | None) -> float | None:
    try:
        parsed = float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
    return parsed if parsed is not None and math.isfinite(parsed) else None


def _merge_seconds(intervals: list[tuple[datetime, datetime]]) -> float | None:
    if not intervals:
        return None
    ordered = sorted(intervals)
    merged: list[tuple[datetime, datetime]] = []
    for start, end in ordered:
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return round(sum((end - start).total_seconds() for start, end in merged), 1)


def _activity_intervals(database: Database) -> list[dict[str, Any]]:
    intervals = []
    for record in database.all_activities():
        detail = record["detail"]
        start = _health_datetime(
            str(detail.get("start_date") or "").replace("Z", "+0000")
        )
        if start is None:
            try:
                start = datetime.fromisoformat(
                    str(detail.get("start_date")).replace("Z", "+00:00")
                )
            except (TypeError, ValueError):
                continue
        elapsed = float(detail.get("elapsed_time") or detail.get("moving_time") or 0)
        intervals.append(
            {
                "activity_id": int(detail["id"]),
                "start": start - timedelta(minutes=5),
                "end": start + timedelta(seconds=elapsed, minutes=10),
            }
        )
    return intervals


def import_apple_health_zip(
    zip_path: str | Path, database: Database, year: int | None = None
) -> dict[str, Any]:
    """Stream Apple's export.xml and store privacy-preserving aggregates."""
    database.initialize()
    path = Path(zip_path)
    if not zipfile.is_zipfile(path):
        raise ValueError("This is not a valid Apple Health ZIP archive.")

    daily_values: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    daily_source_sums: dict[str, dict[str, dict[str, float]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(float))
    )
    sleep_intervals: dict[str, list[tuple[datetime, datetime]]] = defaultdict(list)
    daily_sources: dict[str, set[str]] = defaultdict(set)
    activity_hr: dict[int, list[float]] = defaultdict(list)
    activity_sources: dict[int, set[str]] = defaultdict(set)
    intervals = _activity_intervals(database)
    records_seen = workouts_seen = 0

    with zipfile.ZipFile(path) as archive:
        candidates = [
            name
            for name in archive.namelist()
            if name.lower().endswith("export.xml")
        ]
        if not candidates:
            raise ValueError("The ZIP does not contain Apple Health export.xml.")
        member = min(candidates, key=len)
        with archive.open(member) as source:
            for _, element in ET.iterparse(source, events=("end",)):
                tag = element.tag.rsplit("}", 1)[-1]
                if tag == "Workout":
                    start = _health_datetime(element.attrib.get("startDate"))
                    if start and (start.year == year if year is not None else start.year >= 2026):
                        workouts_seen += 1
                    element.clear()
                    continue
                if tag != "Record":
                    element.clear()
                    continue
                start = _health_datetime(element.attrib.get("startDate"))
                end = _health_datetime(element.attrib.get("endDate")) or start
                if start is None or not (start.year == year if year is not None else start.year >= 2026):
                    element.clear()
                    continue
                records_seen += 1
                record_type = element.attrib.get("type", "")
                value = _number(element.attrib.get("value"))
                day = (
                    end.date().isoformat()
                    if record_type == SLEEP and end is not None
                    else start.date().isoformat()
                )
                source_name = element.attrib.get("sourceName") or "Apple Health"
                daily_sources[day].add(source_name)

                if record_type == HEART_RATE and value is not None:
                    for interval in intervals:
                        if interval["start"] <= start <= interval["end"]:
                            activity_id = interval["activity_id"]
                            activity_hr[activity_id].append(value)
                            activity_sources[activity_id].add(source_name)
                            break
                elif record_type == RESTING_HR and value is not None:
                    daily_values[day]["resting_heartrate"].append(value)
                elif record_type == STEPS and value is not None:
                    daily_source_sums[day]["steps"][source_name] += value
                elif record_type == ACTIVE_ENERGY and value is not None:
                    daily_source_sums[day]["active_energy_kcal"][
                        source_name
                    ] += value
                elif record_type == OXYGEN and value is not None:
                    daily_values[day]["oxygen_saturation_percent"].append(
                        value * 100 if value <= 1.5 else value
                    )
                elif (
                    record_type == SLEEP
                    and element.attrib.get("value") in ASLEEP_VALUES
                    and end is not None
                ):
                    sleep_intervals[day].append((start, end))
                element.clear()

    all_days = sorted(set(daily_values) | set(sleep_intervals) | set(daily_source_sums))
    daily_rows = []
    for day in all_days:
        values = daily_values[day]
        source_sums = daily_source_sums[day]
        daily_rows.append(
            {
                "date": day,
                "sleep_duration_sec": _merge_seconds(sleep_intervals[day]),
                "resting_heartrate": (
                    round(statistics.mean(values["resting_heartrate"]), 1)
                    if values["resting_heartrate"]
                    else None
                ),
                "steps": (
                    round(max(source_sums["steps"].values()))
                    if source_sums["steps"]
                    else None
                ),
                "active_energy_kcal": (
                    round(max(source_sums["active_energy_kcal"].values()), 1)
                    if source_sums["active_energy_kcal"]
                    else None
                ),
                "oxygen_saturation_percent": (
                    round(statistics.mean(values["oxygen_saturation_percent"]), 1)
                    if values["oxygen_saturation_percent"]
                    else None
                ),
                "sources": sorted(daily_sources[day]),
            }
        )
    activity_rows = [
        {
            "activity_id": activity_id,
            "average_heartrate": round(statistics.mean(values), 1),
            "max_heartrate": round(max(values), 1),
            "min_heartrate": round(min(values), 1),
            "sample_count": len(values),
            "sources": sorted(activity_sources[activity_id]),
        }
        for activity_id, values in activity_hr.items()
        if values
    ]
    database.upsert_health_daily(daily_rows)
    database.upsert_activity_health(activity_rows)
    return {
        "source": "apple_health",
        "year": year,
        "records_processed": records_seen,
        "workouts_seen": workouts_seen,
        "daily_records_saved": len(daily_rows),
        "activities_enriched": len(activity_rows),
    }
