from fastapi.testclient import TestClient

from app import main
from app.config import Settings
from app.db import Database


def configured_client(tmp_path, monkeypatch):
    settings = Settings(
        database_path=tmp_path / "web.db",
        export_dir=tmp_path / "exports",
        ai_provider="none",
        _env_file=None,
    )
    database = Database(settings)
    database.initialize()
    database.upsert_activity(
        {
            "id": 123456789,
            "name": "Easy evening run",
            "sport_type": "Run",
            "start_date": "2026-06-20T18:00:00Z",
            "start_date_local": "2026-06-20T19:00:00Z",
            "distance": 3200,
            "moving_time": 1500,
            "elapsed_time": 1650,
            "average_speed": 2.13,
            "max_speed": 3.1,
            "total_elevation_gain": 20,
        },
        None,
        [],
    )
    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(main, "database", database)
    return TestClient(main.app)


def test_mobile_dashboard_and_all_requested_pages(tmp_path, monkeypatch):
    with configured_client(tmp_path, monkeypatch) as client:
        root = client.get("/", follow_redirects=False)
        assert root.status_code in {302, 307}
        assert root.headers["location"] == "/dashboard"

        for path in (
            "/dashboard",
            "/import",
            "/activities",
            "/activity/123456789",
            "/analysis",
            "/plan",
            "/settings",
        ):
            response = client.get(path)
            assert response.status_code == 200, path
            assert "viewport-fit=cover" in response.text
            assert "/static/app.css" in response.text
            assert 'class="page-content"' in response.text

        dashboard = client.get("/dashboard")
        assert "Easy evening run" in dashboard.text
        assert "Steady beats heroic" in dashboard.text
        assert "/manifest.json" in dashboard.text
        assert "/static/app.js" in dashboard.text


def test_dashboard_analysis_form_uses_deterministic_fallback(
    tmp_path, monkeypatch
):
    with configured_client(tmp_path, monkeypatch) as client:
        response = client.post("/analysis/run")
        assert response.status_code == 200
        assert "Deterministic analysis used" in response.text
        assert (tmp_path / "exports" / "ai_safe_payload.json").exists()


def test_api_status_remains_available(tmp_path, monkeypatch):
    with configured_client(tmp_path, monkeypatch) as client:
        response = client.get("/api/status")
        assert response.status_code == 200
        assert response.json()["docs"] == "/docs"


def test_pwa_assets_are_served_with_correct_scope(tmp_path, monkeypatch):
    with configured_client(tmp_path, monkeypatch) as client:
        manifest = client.get("/manifest.json")
        assert manifest.status_code == 200
        assert manifest.json()["display"] == "standalone"
        assert manifest.json()["start_url"] == "/dashboard"
        assert len(manifest.json()["icons"]) == 2

        worker = client.get("/sw.js")
        assert worker.status_code == 200
        assert worker.headers["service-worker-allowed"] == "/"
        assert "runstead-shell" in worker.text

        assert client.get("/static/icon-192.png").status_code == 200
        assert client.get("/static/icon-512.png").status_code == 200
        assert client.get("/offline").status_code == 200


def test_training_preferences_save_locally(tmp_path, monkeypatch):
    with configured_client(tmp_path, monkeypatch) as client:
        response = client.post(
            "/settings",
            data={
                "preferred_sessions_per_week": "3",
                "training_goal": "fewer_walk_breaks",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        saved = main.database.get_preferences()
        assert saved["preferred_sessions_per_week"] == 3
        assert saved["training_goal"] == "fewer_walk_breaks"
