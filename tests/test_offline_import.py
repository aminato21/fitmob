from __future__ import annotations

import csv
import io
import zipfile

from fastapi.testclient import TestClient

from app import main
from app.config import Settings
from app.db import Database
from app.offline_import import import_strava_zip


GPX = b"""<?xml version="1.0" encoding="UTF-8"?>
<gpx xmlns="http://www.topografix.com/GPX/1/1"
     xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1">
  <trk><type>running</type><trkseg>
    <trkpt lat="33.5000" lon="-7.6000"><ele>10</ele>
      <time>2026-04-01T08:00:00Z</time>
      <extensions><gpxtpx:TrackPointExtension><gpxtpx:hr>130</gpxtpx:hr>
      <gpxtpx:cad>75</gpxtpx:cad></gpxtpx:TrackPointExtension></extensions>
    </trkpt>
    <trkpt lat="33.5010" lon="-7.6000"><ele>12</ele>
      <time>2026-04-01T08:01:00Z</time>
      <extensions><gpxtpx:TrackPointExtension><gpxtpx:hr>140</gpxtpx:hr>
      <gpxtpx:cad>78</gpxtpx:cad></gpxtpx:TrackPointExtension></extensions>
    </trkpt>
  </trkseg></trk>
</gpx>
"""


def make_archive(path):
    csv_buffer = io.StringIO()
    writer = csv.writer(csv_buffer)
    writer.writerow(
        [
            "Activity ID",
            "Activity Date",
            "Activity Name",
            "Activity Type",
            "Distance",
            "Filename",
        ]
    )
    writer.writerow(
        [
            "987654321",
            "Apr 01, 2026, 08:00:00 AM",
            "Morning run walk",
            "Run",
            "1.0",
            "activities/987654321.gpx",
        ]
    )
    writer.writerow(
        [
            "111111111",
            "Apr 01, 2026, 09:00:00 AM",
            "Bike",
            "Ride",
            "10.0",
            "activities/111111111.gpx",
        ]
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("activities.csv", csv_buffer.getvalue())
        archive.writestr("activities/987654321.gpx", GPX)


def test_import_zip_combines_csv_and_gpx(tmp_path):
    archive_path = tmp_path / "strava.zip"
    make_archive(archive_path)
    settings = Settings(
        database_path=tmp_path / "offline.db",
        export_dir=tmp_path / "exports",
    )
    database = Database(settings)
    result = import_strava_zip(archive_path, settings, database)
    assert result["activities_csv_found"] is True
    assert result["activities_imported"] == 1
    assert result["runs"] == 1
    assert result["parse_errors"] == []
    record = database.all_activities()[0]
    assert record["detail"]["id"] == 987654321
    assert record["detail"]["average_heartrate"] == 135
    assert record["streams"]["latlng"]["data"][0] == [33.5, -7.6]
    assert (tmp_path / "exports" / "strava_2026_runs.csv").exists()
    assert (tmp_path / "exports" / "strava_2026_summary.json").exists()
    assert (
        tmp_path / "exports" / "strava_2026_streams_summary.csv"
    ).exists()


def test_reimport_updates_instead_of_duplicating(tmp_path):
    archive_path = tmp_path / "strava.zip"
    make_archive(archive_path)
    settings = Settings(
        database_path=tmp_path / "offline.db",
        export_dir=tmp_path / "exports",
    )
    database = Database(settings)
    import_strava_zip(archive_path, settings, database)
    result = import_strava_zip(archive_path, settings, database)
    assert result["activities_imported"] == 0
    assert result["activities_updated"] == 1
    assert len(database.all_activities()) == 1


def test_track_file_import_works_without_activities_csv(tmp_path):
    archive_path = tmp_path / "tracks-only.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("activities/222222222.gpx", GPX)
    settings = Settings(
        database_path=tmp_path / "offline.db",
        export_dir=tmp_path / "exports",
    )
    database = Database(settings)
    result = import_strava_zip(archive_path, settings, database)
    assert result["activities_csv_found"] is False
    assert result["activities_imported"] == 1
    assert database.all_activities()[0]["detail"]["sport_type"] == "Run"


def test_csv_only_archive_still_generates_exports(tmp_path):
    archive_path = tmp_path / "csv-only.zip"
    csv_text = (
        "Activity ID,Activity Date,Activity Name,Activity Type,Distance,"
        "Moving Time,Elapsed Time\n"
        '333333333,"Apr 02, 2026, 08:00:00 AM",CSV run,Run,2.5,1500,1600\n'
    )
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("activities.csv", csv_text)
    settings = Settings(
        database_path=tmp_path / "offline.db",
        export_dir=tmp_path / "exports",
    )
    result = import_strava_zip(archive_path, settings, Database(settings))
    assert result["activities_imported"] == 1
    assert result["runs"] == 1
    assert (tmp_path / "exports" / "strava_2026_streams_summary.csv").exists()


def test_duplicate_strava_distance_columns_use_display_distance(tmp_path):
    archive_path = tmp_path / "duplicate-columns.zip"
    # Real Strava archives include duplicate summary/detail headings.
    csv_text = (
        "Activity ID,Activity Date,Activity Name,Activity Type,"
        "Activity Description,Elapsed Time,Distance,Max Heart Rate,"
        "Relative Effort,Commute,Activity Private Note,Activity Gear,Filename,"
        "Athlete Weight,Bike Weight,Elapsed Time,Moving Time,Distance\n"
        '444444444,"Apr 03, 2026, 08:00:00 AM",Run,Run,,1600,2.5,,,false,'
        ",Shoes,,70,,1600,1500,9999\n"
    )
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("activities.csv", csv_text)
    settings = Settings(
        database_path=tmp_path / "offline.db",
        export_dir=tmp_path / "exports",
    )
    database = Database(settings)
    import_strava_zip(archive_path, settings, database)
    assert database.all_activities()[0]["detail"]["distance"] == 2500


def test_zip_can_be_uploaded_through_api(tmp_path, monkeypatch):
    archive_path = tmp_path / "strava.zip"
    make_archive(archive_path)
    settings = Settings(
        database_path=tmp_path / "api.db",
        export_dir=tmp_path / "api-exports",
    )
    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(main, "database", Database(settings))
    with TestClient(main.app) as client:
        response = client.post(
            "/import/zip",
            files={
                "archive": (
                    "strava.zip",
                    archive_path.read_bytes(),
                    "application/zip",
                )
            },
        )
    assert response.status_code == 200
    assert response.json()["activities_imported"] == 1
