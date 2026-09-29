from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from pydantic import ValidationError

from app.ai import deterministic_analysis, list_gemini_models, run_ai_analysis
from app.config import Settings
from app.db import Database


def add_private_activity(database: Database) -> None:
    database.initialize()
    database.upsert_activity(
        {
            "id": 777777777,
            "name": "Home route - do not send",
            "description": "Started outside my exact home",
            "sport_type": "Run",
            "start_date": "2026-06-20T18:30:00Z",
            "start_date_local": "2026-06-20T19:30:00Z",
            "distance": 5000,
            "moving_time": 2100,
            "elapsed_time": 2300,
            "average_speed": 2.38,
            "max_speed": 3.5,
            "average_heartrate": 150,
            "max_heartrate": 172,
            "start_latlng": [33.123456, -7.654321],
            "end_latlng": [33.123499, -7.654399],
            "location_city": "Private City",
            "map": {"summary_polyline": "secret-polyline"},
            "device_name": "Private Watch",
            "gear_id": "private-shoes",
        },
        {
            "time": {"data": [0, 60, 120]},
            "distance": {"data": [0, 120, 240]},
            "velocity_smooth": {"data": [2.0, 2.0, 2.0]},
            "moving": {"data": [True, True, True]},
            "latlng": {
                "data": [
                    [33.123456, -7.654321],
                    [33.12347, -7.65435],
                    [33.123499, -7.654399],
                ]
            },
        },
        [],
    )


def make_settings(tmp_path, **overrides) -> Settings:
    values = {
        "database_path": tmp_path / "ai.db",
        "export_dir": tmp_path / "exports",
        "ai_provider": "none",
        "gemini_api_key": "",
        "ai_model": "gemini-3.5-flash",
        "_env_file": None,
    }
    values.update(overrides)
    return Settings(**values)


def valid_ai_result() -> dict:
    session = {
        "day": "Wednesday",
        "session_type": "easy_run_walk",
        "target_distance_km": 3.5,
        "duration_minutes": 30,
        "walk_strategy": "Run easy, walk 200 m, finish easy.",
        "warmup": "5 min brisk walk.",
        "focus": "Conversational effort.",
        "guidance": "Keep it easy.",
    }
    weeks = [
        {
            "week": week,
            "theme": "Consistency",
            "sessions": [session],
            "weekly_target_km": 3.5,
            "key_goal": "Finish comfortable.",
        }
        for week in range(1, 5)
    ]
    return {
        "current_level": "Beginner run/walk runner.",
        "progress_highlights": ["Walking breaks are reducing."],
        "walk_break_analysis": "Comparable 5 km runs improved.",
        "main_findings": ["Walking breaks are useful."],
        "risks": ["Increase volume conservatively."],
        "next_week": weeks[0],
        "four_week_plan": weeks,
        "milestone_targets": ["Comfortable 5 km."],
        "what_to_watch_next": ["Recovery."],
        "questions_for_user": ["How did it feel?"],
    }


def test_none_provider_writes_safe_payload_without_private_route_data(tmp_path):
    settings = make_settings(tmp_path)
    database = Database(settings)
    add_private_activity(database)
    result = asyncio.run(run_ai_analysis(settings, database))
    assert result["status"] == "deterministic"
    assert result["provider_used"] == "none"
    safe_path = tmp_path / "exports" / "ai_safe_payload.json"
    safe_text = safe_path.read_text("utf-8")
    for forbidden in (
        "start_latlng",
        "end_latlng",
        "map_summary_polyline",
        "secret-polyline",
        "Private City",
        "Home route",
        "exact home",
        "33.123456",
        "-7.654321",
        "Private Watch",
        "private-shoes",
    ):
        assert forbidden not in safe_text
    assert (tmp_path / "exports" / "ai_analysis.json").exists()


def test_gemini_request_uses_safe_payload_and_strict_json(tmp_path):
    settings = make_settings(
        tmp_path, ai_provider="gemini", gemini_api_key="test-key"
    )
    database = Database(settings)
    add_private_activity(database)

    def handler(request: httpx.Request) -> httpx.Response:
        # The privacy payload must exist before network I/O begins.
        assert (tmp_path / "exports" / "ai_safe_payload.json").exists()
        assert request.headers["x-goog-api-key"] == "test-key"
        assert "gemini-3.5-flash:generateContent" in str(request.url)
        request_json = json.loads(request.content)
        assert (
            request_json["generationConfig"]["responseMimeType"]
            == "application/json"
        )
        sent_text = request_json["contents"][0]["parts"][0]["text"]
        assert "33.123456" not in sent_text
        assert "secret-polyline" not in sent_text
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [{"text": json.dumps(valid_ai_result())}]
                        }
                    }
                ]
            },
        )

    result = asyncio.run(
        run_ai_analysis(
            settings,
            database,
            transport=httpx.MockTransport(handler),
        )
    )
    assert result["status"] == "ai"
    assert result["provider_used"] == "gemini"
    saved = json.loads(
        (tmp_path / "exports" / "ai_analysis.json").read_text("utf-8")
    )
    assert set(saved) == set(valid_ai_result())


def test_gemini_rate_limit_falls_back_without_crashing(tmp_path):
    settings = make_settings(
        tmp_path, ai_provider="gemini", gemini_api_key="test-key"
    )
    database = Database(settings)
    add_private_activity(database)
    transport = httpx.MockTransport(
        lambda request: httpx.Response(429, json={"error": "RESOURCE_EXHAUSTED"})
    )
    result = asyncio.run(run_ai_analysis(settings, database, transport=transport))
    assert result["status"] == "deterministic_fallback"
    assert result["provider_used"] == "none"
    assert result["fallback_reason"] == "Gemini rate limit reached"
    assert result["analysis"]["current_level"].startswith("Beginner")


def test_missing_gemini_key_uses_none_without_network(tmp_path):
    settings = make_settings(tmp_path, ai_provider="gemini", gemini_api_key="")
    result = asyncio.run(run_ai_analysis(settings, Database(settings)))
    assert result["status"] == "deterministic_fallback"
    assert result["provider_used"] == "none"
    assert "not configured" in result["fallback_reason"]


def test_only_none_and_gemini_are_valid(tmp_path):
    with pytest.raises(ValidationError):
        make_settings(tmp_path, ai_provider="groq")
    with pytest.raises(ValidationError):
        make_settings(tmp_path, ai_provider="ollama")


def test_default_model_is_configured_flash_without_provider_cascade():
    settings = Settings(_env_file=None)
    assert settings.ai_model == "gemini-3.5-flash"
    assert settings.preferred_sessions_per_week == 1
    assert settings.training_goal == "consistency"


def test_model_catalog_lists_generate_content_models(tmp_path):
    settings = make_settings(
        tmp_path, ai_provider="gemini", gemini_api_key="test-key"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-goog-api-key"] == "test-key"
        return httpx.Response(
            200,
            json={
                "models": [
                    {
                        "name": "models/gemini-3.5-flash",
                        "displayName": "Gemini 3.5 Flash",
                        "supportedGenerationMethods": ["generateContent"],
                        "inputTokenLimit": 1_048_576,
                        "outputTokenLimit": 65_536,
                    },
                    {
                        "name": "models/text-embedding",
                        "supportedGenerationMethods": ["embedContent"],
                    },
                ]
            },
        )

    result = asyncio.run(
        list_gemini_models(settings, httpx.MockTransport(handler))
    )
    assert result["status"] == "ok"
    assert [item["id"] for item in result["models"]] == ["gemini-3.5-flash"]
    assert result["models"][0]["configured"] is True


def test_unavailable_default_uses_local_fallback_without_extra_calls(tmp_path):
    settings = make_settings(
        tmp_path, ai_provider="gemini", gemini_api_key="test-key"
    )
    database = Database(settings)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if "gemini-3.5-flash" in str(request.url):
            return httpx.Response(404, json={"error": "not available"})
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [{"text": json.dumps(valid_ai_result())}]
                        }
                    }
                ]
            },
        )

    result = asyncio.run(
        run_ai_analysis(
            settings, database, transport=httpx.MockTransport(handler)
        )
    )
    assert len(calls) == 1
    assert result["provider_used"] == "none"
    assert result["model"] is None


@pytest.mark.parametrize("sessions", [1, 2, 3])
def test_deterministic_plan_honors_session_preference(sessions):
    analysis = deterministic_analysis([], sessions_per_week=sessions)
    assert len(analysis.next_week.sessions) == sessions
    assert all(len(week.sessions) == sessions for week in analysis.four_week_plan)
    for week in analysis.four_week_plan:
        assert week.weekly_target_km == sum(
            item.target_distance_km for item in week.sessions
        )
        assert all(item.walk_strategy for item in week.sessions)
