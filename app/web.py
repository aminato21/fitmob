from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.ai import deterministic_analysis, run_ai_analysis
from app.analysis import (
    analyze_streams,
    build_run_rows,
    compute_consistency_score,
    compute_current_capabilities,
    compute_personal_bests,
    compute_walk_break_trend,
    pace_text,
    safe_div,
)
from app.offline_import import import_strava_zip


router = APIRouter(default_response_class=HTMLResponse)
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


def _duration(seconds: Any) -> str:
    if seconds in (None, ""):
        return "—"
    value = max(0, round(float(seconds)))
    hours, remainder = divmod(value, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def _number(value: Any, digits: int = 1) -> str:
    if value is None:
        return "—"
    return f"{float(value):.{digits}f}"


templates.env.filters["duration"] = _duration
templates.env.filters["number"] = _number


def _state(request: Request):
    return request.app.state.settings, request.app.state.database


def _data(request: Request):
    settings, database = _state(request)
    records = database.all_activities()
    rows, stream_analyses = build_run_rows(records, settings)
    from app.exporter import make_summary

    return settings, database, records, rows, stream_analyses, make_summary(rows)


def _base(request: Request, page: str, **values: Any) -> dict[str, Any]:
    settings, _ = _state(request)
    return {
        "request": request,
        "page": page,
        "ai_provider": settings.ai_provider,
        "ai_model": settings.ai_model,
        **values,
    }


def _split_rows(detail: dict[str, Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for index, split in enumerate(detail.get("splits_metric") or [], 1):
        distance = split.get("distance")
        moving = split.get("moving_time")
        pace = safe_div(
            float(moving) if moving is not None else None,
            float(distance) / 1000 if distance else None,
        )
        output.append(
            {
                "number": split.get("split") or index,
                "distance_m": distance,
                "moving_time": moving,
                "pace": pace_text(pace),
                "pace_sec": pace,
                "elevation": split.get("elevation_difference"),
            }
        )
    valid = [item["pace_sec"] for item in output if item["pace_sec"]]
    fastest = min(valid, default=None)
    slowest = max(valid, default=None)
    for item in output:
        if not item["pace_sec"] or fastest is None or slowest is None:
            item["visual_width"] = 55
        elif fastest == slowest:
            item["visual_width"] = 82
        else:
            item["visual_width"] = round(
                55
                + 40
                * (slowest - item["pace_sec"])
                / (slowest - fastest)
            )
    return output


@router.get("/dashboard")
def dashboard(request: Request):
    _, database, _, rows, _, summary = _data(request)
    preferences = database.get_preferences()
    latest = list(reversed(rows[-5:]))
    flagged = [
        row
        for row in rows
        if row.get("injury_risk_flag")
        or row.get("sudden_volume_increase_flag")
        or row.get("too_many_hard_sessions_flag")
    ]
    suggestion = (
        rows[-1].get("suggested_next_session_type") if rows else "easy_run_walk"
    )
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context=_base(
            request,
            "dashboard",
            summary=summary,
            latest=latest,
            flagged=flagged[-3:],
            suggestion=suggestion,
            weekly_chart=[
                {"label": week, "value": totals["distance_km"]}
                for week, totals in summary["weekly_totals"].items()
            ][-10:],
            consistency=compute_consistency_score(
                rows, int(preferences.get("preferred_sessions_per_week", 2))
            ),
        ),
    )


@router.get("/import")
def import_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="import.html",
        context=_base(request, "import", result=None, error=None),
    )


@router.post("/import")
def import_upload(
    request: Request,
    archive: UploadFile = File(...),
):
    settings, database = _state(request)
    result = None
    error = None
    temp_path: Path | None = None
    try:
        if not (archive.filename or "").lower().endswith(".zip"):
            raise ValueError("Please choose the ZIP downloaded from Strava.")
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as temp:
            shutil.copyfileobj(archive.file, temp)
            temp_path = Path(temp.name)
        result = import_strava_zip(temp_path, settings, database)
    except ValueError as exc:
        error = str(exc)
    finally:
        archive.file.close()
        if temp_path:
            temp_path.unlink(missing_ok=True)
    return templates.TemplateResponse(
        request=request,
        name="import.html",
        context=_base(request, "import", result=result, error=error),
    )


@router.get("/activities")
def activities(request: Request):
    _, _, _, rows, _, _ = _data(request)
    return templates.TemplateResponse(
        request=request,
        name="activities.html",
        context=_base(
            request,
            "activities",
            activities=list(reversed(rows)),
        ),
    )


@router.get("/activity/{activity_id}")
def activity_detail(request: Request, activity_id: int):
    settings, _, records, rows, stream_analyses, _ = _data(request)
    row = next((item for item in rows if item["id"] == activity_id), None)
    record = next(
        (item for item in records if int(item["detail"]["id"]) == activity_id),
        None,
    )
    if not row or not record:
        return templates.TemplateResponse(
            request=request,
            name="not_found.html",
            context=_base(request, "activities"),
            status_code=404,
        )
    laps = record.get("laps")
    if laps is None:
        laps = record["detail"].get("laps") or []
    return templates.TemplateResponse(
        request=request,
        name="activity.html",
        context=_base(
            request,
            "activities",
            activity=row,
            splits=_split_rows(record["detail"]),
            laps=laps,
            stream=stream_analyses.get(activity_id)
            or analyze_streams(record.get("streams"), settings),
            effort_percent={
                "easy": 32,
                "moderate": 62,
                "hard": 90,
                "unknown": 48,
            }.get(row.get("effort_guess"), 48),
        ),
    )


def _analysis_context(request: Request, result: dict[str, Any] | None = None):
    settings, database, _, rows, _, _ = _data(request)
    preferences = database.get_preferences()
    sessions_per_week = int(
        preferences.get(
            "preferred_sessions_per_week", settings.preferred_sessions_per_week
        )
    )
    training_goal = str(
        preferences.get("training_goal", settings.training_goal)
    )
    deterministic = deterministic_analysis(
        rows, sessions_per_week, training_goal
    ).model_dump(mode="json")
    walk_trend = compute_walk_break_trend(rows)
    safe_path = Path(settings.export_dir) / "ai_safe_payload.json"
    safe_payload = None
    if safe_path.exists():
        try:
            safe_payload = json.loads(safe_path.read_text("utf-8"))
        except (OSError, ValueError):
            safe_payload = None
    return _base(
        request,
        "analysis",
        deterministic=deterministic,
        result=result,
        safe_payload=safe_payload,
        key_configured=bool(settings.gemini_api_key),
        run_count=len(rows),
        walk_trend=walk_trend,
        walk_chart=[
            {"label": item["date"], "value": item["break_count"]}
            for item in walk_trend["series"]
        ],
        pace_trend=[
            {
                "label": row["_date"].isoformat(),
                "value": round(row["pace_sec_per_km"] / 60, 2),
            }
            for row in rows
            if row.get("pace_sec_per_km")
        ][-12:],
        capabilities=compute_current_capabilities(rows),
        personal_bests=compute_personal_bests(rows),
        consistency=compute_consistency_score(rows, sessions_per_week),
        sessions_per_week=sessions_per_week,
        training_goal=training_goal,
    )


@router.get("/analysis")
def analysis_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="analysis.html",
        context=_analysis_context(request),
    )


@router.post("/analysis/run")
async def run_analysis_page(request: Request):
    settings, database = _state(request)
    result = await run_ai_analysis(settings, database)
    return templates.TemplateResponse(
        request=request,
        name="analysis.html",
        context=_analysis_context(request, result),
    )


@router.get("/plan")
def plan(request: Request):
    settings, database, _, rows, _, _ = _data(request)
    preferences = database.get_preferences()
    sessions_per_week = int(
        preferences.get(
            "preferred_sessions_per_week", settings.preferred_sessions_per_week
        )
    )
    training_goal = str(
        preferences.get("training_goal", settings.training_goal)
    )
    analysis = deterministic_analysis(
        rows, sessions_per_week, training_goal
    ).model_dump(mode="json")
    return templates.TemplateResponse(
        request=request,
        name="plan.html",
        context=_base(
            request,
            "plan",
            analysis=analysis,
            sessions_per_week=sessions_per_week,
            training_goal=training_goal,
        ),
    )


@router.get("/settings")
def settings_page(request: Request):
    settings, database = _state(request)
    preferences = database.get_preferences()
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context=_base(
            request,
            "settings",
            key_configured=bool(settings.gemini_api_key),
            fallback_model=settings.ai_fallback_model,
            database_path=str(database.path.resolve()),
            export_dir=str(Path(settings.export_dir).resolve()),
            max_hr=settings.max_hr,
            hr_zones=settings.hr_zone_bounds,
            sessions_per_week=int(
                preferences.get(
                    "preferred_sessions_per_week",
                    settings.preferred_sessions_per_week,
                )
            ),
            training_goal=str(
                preferences.get("training_goal", settings.training_goal)
            ),
            saved=request.query_params.get("saved") == "1",
        ),
    )


@router.post("/settings")
def save_settings_page(
    request: Request,
    preferred_sessions_per_week: int = Form(...),
    training_goal: str = Form(...),
):
    _, database = _state(request)
    sessions = max(1, min(3, int(preferred_sessions_per_week)))
    allowed_goals = {
        "continuous_5k",
        "fewer_walk_breaks",
        "comfortable_7k",
        "consistency",
    }
    goal = training_goal if training_goal in allowed_goals else "continuous_5k"
    database.save_preferences(
        {
            "preferred_sessions_per_week": sessions,
            "training_goal": goal,
        }
    )
    return RedirectResponse("/settings?saved=1", status_code=303)
