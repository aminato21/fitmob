from __future__ import annotations

import csv
import gzip
import hashlib
import io
import math
import re
import statistics
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO
from xml.etree import ElementTree as ET

import fitdecode

from app.analysis import RUN_TYPES
from app.config import Settings
from app.db import Database
from app.sync import export_from_database

SUPPORTED_SUFFIXES = (".fit", ".gpx", ".tcx", ".fit.gz", ".gpx.gz", ".tcx.gz")
MAX_ARCHIVE_MEMBER_BYTES = 512 * 1024 * 1024


@dataclass
class ParsedActivity:
    detail: dict[str, Any]
    streams: dict[str, Any] | None = None
    laps: list[dict[str, Any]] = field(default_factory=list)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        number = _as_float(value)
        return int(number) if number is not None else None


def _as_bool(value: Any) -> bool | None:
    if value in (None, ""):
        return None
    return str(value).strip().lower() in {"true", "1", "yes"}


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        pass
    for fmt in (
        "%b %d, %Y, %I:%M:%S %p",
        "%b %d, %Y, %H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%m/%d/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M:%S",
    ):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value else None


def _normalized_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower().lstrip("\ufeff"))


def _get(row: dict[str, Any], *names: str) -> Any:
    normalized = {_normalized_key(key): value for key, value in row.items()}
    for name in names:
        value = normalized.get(_normalized_key(name))
        if value not in (None, ""):
            return value
    return None


def _csv_rows(text: str) -> list[dict[str, Any]]:
    reader = csv.reader(io.StringIO(text))
    try:
        headers = next(reader)
    except StopIteration:
        return []
    counts: dict[str, int] = {}
    unique_headers: list[str] = []
    for header in headers:
        count = counts.get(header, 0)
        unique_headers.append(header if count == 0 else f"{header}.{count}")
        counts[header] = count + 1
    rows: list[dict[str, Any]] = []
    for values in reader:
        row = {
            header: values[index] if index < len(values) else ""
            for index, header in enumerate(unique_headers)
        }
        # Position fallbacks also allow the standard archive layout to work
        # when Strava localizes its CSV headings.
        fallback_positions = {
            "Fallback Column Activity ID": 0,
            "Fallback Column Activity Date": 1,
            "Fallback Column Activity Name": 2,
            "Fallback Column Activity Type": 3,
            "Fallback Column Activity Description": 4,
            "Fallback Column Elapsed Time": 5,
            "Fallback Column Distance": 6,
            "Fallback Column Commute": 9,
            "Fallback Column Activity Gear": 11,
            "Fallback Column Filename": 12,
            "Fallback Column Moving Time": 16,
        }
        for name, index in fallback_positions.items():
            row[name] = values[index] if index < len(values) else ""
        rows.append(row)
    return rows


def _sport_type(value: Any) -> str | None:
    normalized = str(value or "").lower().replace("_", " ").replace("-", " ").strip()
    mapping = {
        "run": "Run",
        "running": "Run",
        "trail run": "TrailRun",
        "trailrun": "TrailRun",
        "virtual run": "VirtualRun",
        "virtualrun": "VirtualRun",
    }
    return mapping.get(normalized)


def _stable_id(name: str, content: bytes) -> int:
    match = re.search(r"(?<!\d)(\d{6,})(?!\d)", PurePosixPath(name).name)
    if match:
        value = int(match.group(1))
        if value <= 9_223_372_036_854_775_807:
            return value
    digest = hashlib.sha256(name.encode("utf-8") + content[:4096]).hexdigest()
    return -int(digest[:15], 16)


def _haversine_m(left: list[float], right: list[float]) -> float:
    lat1, lon1, lat2, lon2 = map(
        math.radians, (left[0], left[1], right[0], right[1])
    )
    a = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 6_371_000 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _stream(values: list[Any]) -> dict[str, Any]:
    return {"data": values}


def _from_points(
    points: list[dict[str, Any]], detail: dict[str, Any]
) -> ParsedActivity:
    points = [point for point in points if point.get("timestamp")]
    points.sort(key=lambda point: point["timestamp"])
    if not points:
        return ParsedActivity(detail=detail)
    start = points[0]["timestamp"]
    times = [
        max(0.0, (point["timestamp"] - start).total_seconds()) for point in points
    ]
    distances: list[float] = []
    cumulative = 0.0
    previous_position: list[float] | None = None
    for point in points:
        supplied = _as_float(point.get("distance"))
        position = point.get("latlng")
        if supplied is not None:
            cumulative = max(cumulative, supplied)
        elif position and previous_position:
            cumulative += _haversine_m(previous_position, position)
        distances.append(cumulative)
        if position:
            previous_position = position

    velocities: list[float] = []
    moving: list[bool] = []
    for index, point in enumerate(points):
        speed = _as_float(point.get("speed"))
        if speed is None and index:
            dt = times[index] - times[index - 1]
            speed = (distances[index] - distances[index - 1]) / dt if dt > 0 else 0
        speed = max(0.0, speed or 0.0)
        velocities.append(speed)
        moving.append(speed >= 0.5)

    moving_time = sum(
        max(0.0, times[index] - times[index - 1])
        for index in range(1, len(times))
        if moving[index]
    )
    altitude = [point.get("altitude") for point in points]
    elevations = [float(value) for value in altitude if value is not None]
    elevation_gain = sum(
        max(0.0, float(altitude[index]) - float(altitude[index - 1]))
        for index in range(1, len(altitude))
        if altitude[index] is not None and altitude[index - 1] is not None
    )
    hrs = [float(point["heartrate"]) for point in points if point.get("heartrate") is not None]
    cadences = [
        float(point["cadence"]) for point in points if point.get("cadence") is not None
    ]
    positions = [point.get("latlng") for point in points]
    valid_positions = [position for position in positions if position]
    distance = distances[-1] if distances else None
    elapsed = times[-1] if times else None
    moving_speeds = [speed for speed, active in zip(velocities, moving) if active]

    generated = {
        "start_date": detail.get("start_date") or _iso(start),
        "start_date_local": detail.get("start_date_local") or _iso(start),
        "distance": distance,
        "moving_time": round(moving_time),
        "elapsed_time": round(elapsed) if elapsed is not None else None,
        "total_elevation_gain": round(elevation_gain, 2) if elevations else None,
        "elev_high": max(elevations, default=None),
        "elev_low": min(elevations, default=None),
        "average_speed": (
            distance / moving_time if distance is not None and moving_time else None
        ),
        "max_speed": max(velocities, default=None),
        "has_heartrate": bool(hrs),
        "average_heartrate": statistics.fmean(hrs) if hrs else None,
        "max_heartrate": max(hrs, default=None),
        "average_cadence": statistics.fmean(cadences) if cadences else None,
        "start_latlng": valid_positions[0] if valid_positions else None,
        "end_latlng": valid_positions[-1] if valid_positions else None,
    }
    # Track files are more precise than activities.csv for these fields.
    detail.update({key: value for key, value in generated.items() if value is not None})
    streams: dict[str, Any] = {
        "time": _stream(times),
        "distance": _stream(distances),
        "velocity_smooth": _stream(velocities),
        "moving": _stream(moving),
    }
    optional = {
        "heartrate": [point.get("heartrate") for point in points],
        "cadence": [point.get("cadence") for point in points],
        "altitude": altitude,
        "latlng": positions,
    }
    for key, values in optional.items():
        if any(value is not None for value in values):
            streams[key] = _stream(values)
    return ParsedActivity(detail=detail, streams=streams)


def _parse_gpx(content: bytes, detail: dict[str, Any]) -> ParsedActivity:
    root = ET.fromstring(content)
    if not detail.get("sport_type"):
        activity_type = next(
            (
                element.text
                for element in root.iter()
                if _local_name(element.tag) == "type" and element.text
            ),
            None,
        )
        detail["sport_type"] = _sport_type(activity_type)
        detail["type"] = "Run" if detail["sport_type"] in RUN_TYPES else activity_type
    points: list[dict[str, Any]] = []
    for element in root.iter():
        if _local_name(element.tag) != "trkpt":
            continue
        point: dict[str, Any] = {
            "latlng": [_as_float(element.get("lat")), _as_float(element.get("lon"))]
        }
        if None in point["latlng"]:
            point["latlng"] = None
        for child in element.iter():
            name = _local_name(child.tag)
            if name == "time":
                point["timestamp"] = _parse_datetime(child.text)
            elif name in {"ele", "altitude"}:
                point["altitude"] = _as_float(child.text)
            elif name in {"hr", "heartratebpm"}:
                point["heartrate"] = _as_float(child.text)
            elif name in {"cad", "cadence"}:
                point["cadence"] = _as_float(child.text)
            elif name in {"speed", "velocity"}:
                point["speed"] = _as_float(child.text)
        points.append(point)
    return _from_points(points, detail)


def _child_value(element: ET.Element, name: str) -> str | None:
    wanted = name.lower()
    for child in element.iter():
        if _local_name(child.tag) == wanted and child.text:
            return child.text
    return None


def _nested_value(element: ET.Element, parent_name: str) -> str | None:
    for child in element.iter():
        if _local_name(child.tag) == parent_name.lower():
            return _child_value(child, "Value")
    return None


def _parse_tcx(content: bytes, detail: dict[str, Any]) -> ParsedActivity:
    root = ET.fromstring(content)
    if not detail.get("sport_type"):
        activity = next(
            (
                element
                for element in root.iter()
                if _local_name(element.tag) == "activity"
            ),
            None,
        )
        activity_sport = activity.get("Sport") if activity is not None else None
        detail["sport_type"] = _sport_type(activity_sport)
        detail["type"] = "Run" if detail["sport_type"] in RUN_TYPES else activity_sport
    points: list[dict[str, Any]] = []
    laps: list[dict[str, Any]] = []
    for element in root.iter():
        name = _local_name(element.tag)
        if name == "trackpoint":
            lat = lon = None
            for child in element.iter():
                child_name = _local_name(child.tag)
                if child_name == "latitudedegrees":
                    lat = _as_float(child.text)
                elif child_name == "longitudedegrees":
                    lon = _as_float(child.text)
            points.append(
                {
                    "timestamp": _parse_datetime(_child_value(element, "Time")),
                    "distance": _as_float(_child_value(element, "DistanceMeters")),
                    "altitude": _as_float(_child_value(element, "AltitudeMeters")),
                    "heartrate": _as_float(_child_value(element, "Value")),
                    "cadence": _as_float(_child_value(element, "Cadence")),
                    "speed": _as_float(_child_value(element, "Speed")),
                    "latlng": [lat, lon] if lat is not None and lon is not None else None,
                }
            )
        elif name == "lap":
            lap = {
                "id": None,
                "name": None,
                "distance": _as_float(_child_value(element, "DistanceMeters")),
                "moving_time": _as_float(_child_value(element, "TotalTimeSeconds")),
                "elapsed_time": _as_float(_child_value(element, "TotalTimeSeconds")),
                "average_speed": _as_float(_child_value(element, "AvgSpeed")),
                "max_speed": _as_float(_child_value(element, "MaximumSpeed")),
                "average_cadence": _as_float(_child_value(element, "Cadence")),
                "average_heartrate": _as_float(
                    _nested_value(element, "AverageHeartRateBpm")
                ),
                "max_heartrate": _as_float(
                    _nested_value(element, "MaximumHeartRateBpm")
                ),
            }
            laps.append(lap)
    parsed = _from_points(points, detail)
    for index, lap in enumerate(laps, 1):
        lap["lap_index"] = index
        lap["name"] = f"Lap {index}"
    parsed.laps = laps
    return parsed


def _fit_value(frame: Any, name: str) -> Any:
    try:
        return frame.get_value(name)
    except (KeyError, TypeError):
        return None


def _semicircles(value: Any) -> float | None:
    number = _as_float(value)
    return number * (180.0 / 2**31) if number is not None else None


def _parse_fit(content: bytes, detail: dict[str, Any]) -> ParsedActivity:
    points: list[dict[str, Any]] = []
    laps: list[dict[str, Any]] = []
    session: dict[str, Any] = {}
    with fitdecode.FitReader(io.BytesIO(content)) as reader:
        for frame in reader:
            if frame.frame_type != fitdecode.FIT_FRAME_DATA:
                continue
            if frame.name == "record":
                lat = _semicircles(_fit_value(frame, "position_lat"))
                lon = _semicircles(_fit_value(frame, "position_long"))
                points.append(
                    {
                        "timestamp": _fit_value(frame, "timestamp"),
                        "distance": _fit_value(frame, "distance"),
                        "altitude": _fit_value(frame, "enhanced_altitude")
                        or _fit_value(frame, "altitude"),
                        "heartrate": _fit_value(frame, "heart_rate"),
                        "cadence": _fit_value(frame, "cadence"),
                        "speed": _fit_value(frame, "enhanced_speed")
                        or _fit_value(frame, "speed"),
                        "latlng": [lat, lon] if lat is not None and lon is not None else None,
                    }
                )
            elif frame.name == "session":
                session = frame.get_values()
            elif frame.name == "lap":
                values = frame.get_values()
                laps.append(
                    {
                        "id": None,
                        "lap_index": len(laps) + 1,
                        "name": f"Lap {len(laps) + 1}",
                        "distance": values.get("total_distance"),
                        "moving_time": values.get("total_timer_time"),
                        "elapsed_time": values.get("total_elapsed_time"),
                        "total_elevation_gain": values.get("total_ascent"),
                        "average_speed": values.get("enhanced_avg_speed")
                        or values.get("avg_speed"),
                        "max_speed": values.get("enhanced_max_speed")
                        or values.get("max_speed"),
                        "average_cadence": values.get("avg_running_cadence")
                        or values.get("avg_cadence"),
                        "average_heartrate": values.get("avg_heart_rate"),
                        "max_heartrate": values.get("max_heart_rate"),
                    }
                )
    parsed = _from_points(points, detail)
    if not parsed.detail.get("sport_type"):
        session_sport = session.get("sport")
        parsed.detail["sport_type"] = _sport_type(session_sport)
        parsed.detail["type"] = (
            "Run" if parsed.detail["sport_type"] in RUN_TYPES else str(session_sport or "")
        )
    session_fields = {
        "start_date": _iso(session.get("start_time")),
        "start_date_local": _iso(session.get("start_time")),
        "distance": session.get("total_distance"),
        "moving_time": session.get("total_timer_time"),
        "elapsed_time": session.get("total_elapsed_time"),
        "total_elevation_gain": session.get("total_ascent"),
        "average_speed": session.get("enhanced_avg_speed") or session.get("avg_speed"),
        "max_speed": session.get("enhanced_max_speed") or session.get("max_speed"),
        "average_heartrate": session.get("avg_heart_rate"),
        "max_heartrate": session.get("max_heart_rate"),
        "average_cadence": session.get("avg_running_cadence")
        or session.get("avg_cadence"),
        "calories": session.get("total_calories"),
    }
    parsed.detail.update(
        {key: value for key, value in session_fields.items() if value is not None}
    )
    parsed.detail["has_heartrate"] = parsed.detail.get("average_heartrate") is not None
    parsed.laps = laps
    return parsed


def _csv_detail(row: dict[str, Any], settings: Settings) -> dict[str, Any] | None:
    sport = _sport_type(
        _get(
            row,
            "Activity Type",
            "Sport Type",
            "Type",
            "Fallback Column Activity Type",
        )
    )
    start = _parse_datetime(
        _get(
            row,
            "Activity Date",
            "Start Date",
            "Start Time",
            "Fallback Column Activity Date",
        )
    )
    if sport not in RUN_TYPES or not start or start.year < 2026:
        return None
    activity_id = _as_int(
        _get(row, "Activity ID", "ID", "Fallback Column Activity ID")
    )
    distance = _as_float(_get(row, "Distance", "Fallback Column Distance"))
    if distance is not None:
        distance *= 1609.344 if settings.strava_export_distance_unit == "mi" else 1000
    moving_time = _as_float(
        _get(row, "Moving Time", "Fallback Column Moving Time")
    )
    elapsed_time = _as_float(
        _get(row, "Elapsed Time", "Fallback Column Elapsed Time")
    )
    avg_speed = _as_float(_get(row, "Average Speed"))
    max_speed = _as_float(_get(row, "Max Speed", "Maximum Speed"))
    detail = {
        "id": activity_id,
        "name": _get(
            row, "Activity Name", "Name", "Fallback Column Activity Name"
        )
        or "Imported run",
        "description": _get(
            row,
            "Activity Description",
            "Description",
            "Fallback Column Activity Description",
        ),
        "sport_type": sport,
        "type": "Run",
        "start_date": _iso(start),
        "start_date_local": _iso(start),
        "distance": distance,
        "moving_time": moving_time,
        "elapsed_time": elapsed_time,
        "total_elevation_gain": _as_float(_get(row, "Elevation Gain")),
        "elev_low": _as_float(_get(row, "Elevation Low")),
        "elev_high": _as_float(_get(row, "Elevation High")),
        "average_speed": avg_speed,
        "max_speed": max_speed,
        "average_heartrate": _as_float(_get(row, "Average Heart Rate")),
        "max_heartrate": _as_float(_get(row, "Max Heart Rate")),
        "average_cadence": _as_float(_get(row, "Average Cadence")),
        "has_heartrate": _get(row, "Average Heart Rate") not in (None, ""),
        "calories": _as_float(_get(row, "Calories")),
        "commute": _as_bool(
            _get(row, "Commute", "Fallback Column Commute")
        ),
        "flagged": _as_bool(_get(row, "Flagged")),
        "manual": _as_bool(_get(row, "Manual")),
        "gear": (
            {
                "name": _get(
                    row,
                    "Activity Gear",
                    "Gear",
                    "Fallback Column Activity Gear",
                )
            }
            if _get(
                row,
                "Activity Gear",
                "Gear",
                "Fallback Column Activity Gear",
            )
            else None
        ),
        "_archive_filename": _get(
            row, "Filename", "File Name", "Fallback Column Filename"
        ),
        "_import_source": "strava_bulk_export",
    }
    return detail


def _read_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    if info.file_size > MAX_ARCHIVE_MEMBER_BYTES:
        raise ValueError(f"Archive member is too large: {info.filename}")
    content = archive.read(info)
    if info.filename.lower().endswith(".gz"):
        with gzip.GzipFile(fileobj=io.BytesIO(content)) as compressed:
            content = compressed.read(MAX_ARCHIVE_MEMBER_BYTES + 1)
        if len(content) > MAX_ARCHIVE_MEMBER_BYTES:
            raise ValueError(f"Decompressed activity is too large: {info.filename}")
    return content


def _parse_activity_file(
    filename: str, content: bytes, detail: dict[str, Any]
) -> ParsedActivity:
    lower = filename.lower()
    if lower.endswith((".fit", ".fit.gz")):
        return _parse_fit(content, detail)
    if lower.endswith((".gpx", ".gpx.gz")):
        return _parse_gpx(content, detail)
    if lower.endswith((".tcx", ".tcx.gz")):
        return _parse_tcx(content, detail)
    raise ValueError(f"Unsupported activity file: {filename}")


def _find_info(
    infos: list[zipfile.ZipInfo], filename: str | None
) -> zipfile.ZipInfo | None:
    if not filename:
        return None
    wanted = filename.replace("\\", "/").lstrip("./").lower()
    for info in infos:
        candidate = info.filename.replace("\\", "/").lstrip("./").lower()
        if candidate == wanted or candidate.endswith("/" + wanted):
            return info
    wanted_name = PurePosixPath(wanted).name
    matches = [
        info for info in infos if PurePosixPath(info.filename.lower()).name == wanted_name
    ]
    return matches[0] if len(matches) == 1 else None


def import_strava_zip(
    zip_path: str | Path, settings: Settings, database: Database
) -> dict[str, Any]:
    database.initialize()
    imported = updated = skipped = 0
    errors: list[str] = []
    seen_files: set[str] = set()
    existing_ids = database.activity_ids()

    try:
        archive = zipfile.ZipFile(zip_path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError("The selected file is not a readable ZIP archive") from exc
    with archive:
        infos = [
            info
            for info in archive.infolist()
            if not info.is_dir() and not PurePosixPath(info.filename).is_absolute()
        ]
        csv_info = next(
            (
                info
                for info in infos
                if PurePosixPath(info.filename).name.lower() == "activities.csv"
            ),
            None,
        )
        csv_details: list[dict[str, Any]] = []
        ignored_csv_filenames: list[str] = []
        if csv_info:
            text = _read_member(archive, csv_info).decode("utf-8-sig", errors="replace")
            for row in _csv_rows(text):
                detail = _csv_detail(row, settings)
                if detail:
                    csv_details.append(detail)
                else:
                    filename = _get(
                        row,
                        "Filename",
                        "File Name",
                        "Fallback Column Filename",
                    )
                    if filename:
                        ignored_csv_filenames.append(str(filename))
        for filename in ignored_csv_filenames:
            ignored_info = _find_info(infos, filename)
            if ignored_info:
                seen_files.add(ignored_info.filename)

        for detail in csv_details:
            info = _find_info(infos, detail.pop("_archive_filename", None))
            parsed = ParsedActivity(detail=detail)
            if info:
                seen_files.add(info.filename)
                try:
                    content = _read_member(archive, info)
                    if parsed.detail.get("id") is None:
                        parsed.detail["id"] = _stable_id(info.filename, content)
                    parsed = _parse_activity_file(info.filename, content, parsed.detail)
                except Exception as exc:  # one corrupt activity must not abort the archive
                    errors.append(f"{info.filename}: {type(exc).__name__}: {exc}")
            if parsed.detail.get("id") is None:
                source = repr(sorted(parsed.detail.items())).encode("utf-8")
                parsed.detail["id"] = _stable_id(parsed.detail["start_date"], source)
            database.upsert_activity(parsed.detail, parsed.streams, parsed.laps)
            if int(parsed.detail["id"]) in existing_ids:
                updated += 1
            else:
                imported += 1
                existing_ids.add(int(parsed.detail["id"]))

        # If activities.csv is absent or omits a file, parse supported track files.
        for info in infos:
            if info.filename in seen_files or not info.filename.lower().endswith(
                SUPPORTED_SUFFIXES
            ):
                continue
            try:
                content = _read_member(archive, info)
                detail = {
                    "id": _stable_id(info.filename, content),
                    "name": PurePosixPath(info.filename).stem,
                    "sport_type": None,
                    "type": None,
                    "_import_source": "strava_bulk_export",
                }
                parsed = _parse_activity_file(info.filename, content, detail)
                date_value = _parse_datetime(parsed.detail.get("start_date_local"))
                if (
                    not date_value
                    or date_value.year < 2026
                    or parsed.detail.get("sport_type") not in RUN_TYPES
                ):
                    skipped += 1
                    continue
                database.upsert_activity(parsed.detail, parsed.streams, parsed.laps)
                if int(parsed.detail["id"]) in existing_ids:
                    updated += 1
                else:
                    imported += 1
                    existing_ids.add(int(parsed.detail["id"]))
            except Exception as exc:
                errors.append(f"{info.filename}: {type(exc).__name__}: {exc}")

    exports = export_from_database(settings, database)
    from app.workflow import Workflow
    workflow = Workflow(database)
    workflow.initialize()
    workflow.imported("strava")
    return {
        "status": "ok",
        "mode": "offline_zip",
        "activities_imported": imported,
        "activities_updated": updated,
        "activities_skipped": skipped,
        "activities_csv_found": csv_info is not None,
        "parse_errors": errors,
        **exports,
    }
