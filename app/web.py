from __future__ import annotations

import json
import secrets
import shutil
import tempfile
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.ai import deterministic_analysis
from app.auth import (
    COOKIE_NAME,
    SESSION_MAX_AGE_SEC,
    create_session_cookie,
    hash_password,
    password_matches,
)
from app.analysis import (
    analyze_streams,
    build_run_rows,
    compute_consistency_score,
    compute_current_capabilities,
    compute_personal_bests,
    compute_recovery_summary,
    compute_walk_break_trend,
    pace_text,
    safe_div,
)
from app.offline_import import import_strava_zip
from app.health_import import import_apple_health_zip
from app.sync import export_from_database


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
    settings = settings.with_preferences(database.get_preferences())
    records = database.all_activities()
    rows, stream_analyses = build_run_rows(records, settings)
    from app.exporter import make_summary

    return settings, database, records, rows, stream_analyses, make_summary(rows)


def _base(request: Request, page: str, **values: Any) -> dict[str, Any]:
    settings, _ = _state(request)
    return {
        "request": request,
        "csrf_token": getattr(request.state, "csrf_token", ""),
        "request_id": secrets.token_urlsafe(24),
        "page": page,
        "ai_provider": settings.ai_provider,
        "ai_model": settings.ai_model,
        "current_year": date.today().year,
        **values,
    }


def _safe_next_path(value: str) -> str:
    return value if value.startswith("/") and not value.startswith("//") else "/dashboard"


@router.get("/login")
def login_page(request: Request, next: str = Query("/dashboard")):
    settings, database = _state(request)
    if not settings.auth_enabled:
        return RedirectResponse("/dashboard", status_code=303)
    if database.user_count() == 0:
        return RedirectResponse("/register", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "request": request,
            "next_path": _safe_next_path(next),
            "error": None,
            "can_register": False,
        },
    )


@router.post("/login")
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next_path: str = Form("/dashboard"),
):
    settings, database = _state(request)
    if not settings.auth_enabled:
        return RedirectResponse("/dashboard", status_code=303)
    destination = _safe_next_path(next_path)
    user = database.get_user(username)
    if not user or not password_matches(
        password,
        user["password_salt"],
        user["password_hash"],
        user["password_iterations"],
    ):
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "request": request,
                "next_path": destination,
                "error": "Username or password is incorrect.",
                "can_register": database.user_count() == 0,
            },
            status_code=401,
        )
    response = RedirectResponse(destination, status_code=303)
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    response.set_cookie(
        COOKIE_NAME,
        create_session_cookie(settings, user["username"]),
        max_age=SESSION_MAX_AGE_SEC,
        httponly=True,
        secure=request.url.scheme == "https" or forwarded_proto == "https",
        samesite="lax",
        path="/",
    )
    return response


@router.get("/register")
def register_page(request: Request):
    settings, database = _state(request)
    if not settings.auth_enabled:
        return RedirectResponse("/dashboard", status_code=303)
    if database.user_count() > 0:
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="register.html",
        context={"request": request, "error": None},
    )


@router.post("/register")
def register(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
):
    settings, database = _state(request)
    if not settings.auth_enabled:
        return RedirectResponse("/dashboard", status_code=303)
    error = None
    username = username.strip()
    if database.user_count() > 0:
        error = "The Runstead owner account is already registered."
    elif len(username) < 3 or len(username) > 64:
        error = "Username must contain between 3 and 64 characters."
    elif len(password) < 5:
        error = "Password must contain at least 5 characters."
    elif password != confirm_password:
        error = "The passwords do not match."
    if error:
        return templates.TemplateResponse(
            request=request,
            name="register.html",
            context={"request": request, "error": error},
            status_code=400,
        )
    salt, password_hash, iterations = hash_password(password)
    if not database.create_user(
        username, salt, password_hash, iterations
    ):
        return templates.TemplateResponse(
            request=request,
            name="register.html",
            context={
                "request": request,
                "error": "The owner account could not be created.",
            },
            status_code=409,
        )
    response = RedirectResponse("/dashboard", status_code=303)
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    response.set_cookie(
        COOKIE_NAME,
        create_session_cookie(settings, username),
        max_age=SESSION_MAX_AGE_SEC,
        httponly=True,
        secure=request.url.scheme == "https" or forwarded_proto == "https",
        samesite="lax",
        path="/",
    )
    return response


@router.post("/logout")
def logout() -> RedirectResponse:
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


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
    settings, database, _, rows, _, summary = _data(request)
    preferences = database.get_preferences()
    latest = list(reversed(rows[-3:]))
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
    training_goal = str(
        preferences.get("training_goal", settings.training_goal)
    )
    goal_labels = {
        "continuous_5k": "Run 5 km continuously",
        "fewer_walk_breaks": "Use fewer walking breaks",
        "longer_continuous_segments": "Run longer before each walk",
        "comfortable_7k": "Complete 7 km comfortably",
        "comfortable_10k": "Complete 10 km comfortably",
        "build_endurance": "Build easy endurance",
        "consistency": "Build weekly consistency",
    }
    goal_targets = {
        "continuous_5k": 5.0,
        "comfortable_7k": 7.0,
        "comfortable_10k": 10.0,
    }
    goal_target_km = goal_targets.get(training_goal)
    longest_km = float((summary.get("longest_run") or {}).get("distance_km") or 0)
    goal_progress = (
        {
            "target_km": goal_target_km,
            "longest_km": longest_km,
            "remaining_km": round(max(0.0, goal_target_km - longest_km), 1),
            "percent": round(min(100.0, longest_km / goal_target_km * 100)),
        }
        if goal_target_km
        else None
    )
    current_plan = _plan_context(request)
    dashboard_session = current_plan["next_session"]
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)
    sessions_target = int(
        preferences.get(
            "preferred_sessions_per_week", settings.preferred_sessions_per_week
        )
    )
    current_week_rows = [row for row in rows if week_start <= row["_date"] <= week_end]
    linked_ids = {item['activity_id'] for item in current_plan['completed_history']}
    confirmed_this_week = sum(row['id'] in linked_ids for row in current_week_rows)
    current_week_km = round(
        sum(float(row.get("distance_km") or 0) for row in current_week_rows), 1
    )
    days_since_last_run = (today - rows[-1]["_date"]).days if rows else None
    recent_week_distances = []
    for offset in range(1, 5):
        start = week_start - timedelta(days=7 * offset)
        end = start + timedelta(days=6)
        recent_week_distances.append(
            sum(
                float(row.get("distance_km") or 0)
                for row in rows
                if start <= row["_date"] <= end
            )
        )
    recent_week_average = round(sum(recent_week_distances) / 4, 1)
    latest_risk = bool(
        rows
        and (
            rows[-1].get("injury_risk_flag")
            or rows[-1].get("recovery_flag")
        )
    )
    if days_since_last_run is not None and days_since_last_run > 14:
        week_status = "Upload may be old"
        week_status_class = "return"
        week_guidance = (
            "Your latest imported run is over two weeks old. Upload recent runs "
            "before judging your current routine; missing uploads do not prove inactivity."
        )
    elif latest_risk:
        week_status = "Recovery first"
        week_status_class = "recovery"
        week_guidance = (
            "A recent workload or recovery signal is present. Pain or unusual "
            "fatigue overrides the plan."
        )
    elif confirmed_this_week >= sessions_target:
        week_status = "Target complete"
        week_status_class = "complete"
        week_guidance = (
            "Your weekly target has confirmed imported runs. Extra running is optional; "
            "recovery still counts."
        )
    else:
        week_status = "Keep it repeatable"
        week_status_class = "steady"
        week_guidance = (
            "One comfortable session you can repeat is more useful than a hard "
            "session that disrupts next week."
        )
    week_snapshot = {
        "date_range": f"{week_start.strftime('%b')} {week_start.day}–{week_end.day}",
        "sessions": confirmed_this_week,
        "sessions_target": sessions_target,
        "sessions_remaining": max(0, sessions_target - confirmed_this_week),
        "distance_km": current_week_km,
        "recent_week_average_km": recent_week_average,
        "days_since_last_run": days_since_last_run,
        "last_run_label": (
            "No runs yet"
            if days_since_last_run is None
            else "Today"
            if days_since_last_run == 0
            else "Yesterday"
            if days_since_last_run == 1
            else f"{days_since_last_run} days ago"
        ),
        "status": week_status,
        "status_class": week_status_class,
        "guidance": week_guidance,
        "progress_percent": min(
            100, round(len(current_week_rows) / sessions_target * 100)
        ),
    }
    load_weeks = []
    for offset in range(5, -1, -1):
        start = week_start - timedelta(days=7 * offset)
        end = start + timedelta(days=6)
        distance_km = round(
            sum(
                float(row.get("distance_km") or 0)
                for row in rows
                if start <= row["_date"] <= end
            ),
            1,
        )
        load_weeks.append(
            {
                "label": f"{start.strftime('%b')} {start.day}",
                "distance_km": distance_km,
                "is_current": start == week_start,
            }
        )
    max_load_km = max((item["distance_km"] for item in load_weeks), default=0)
    for item in load_weeks:
        item["height_percent"] = (
            max(5, round(item["distance_km"] / max_load_km * 100))
            if max_load_km and item["distance_km"]
            else 0
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
            training_goal_label=goal_labels.get(
                training_goal, "Build comfortable run/walk fitness"
            ),
            goal_progress=goal_progress,
            dashboard_session=dashboard_session,
            next_session=dashboard_session,
            plan_stale=current_plan["plan_stale"],
            latest_run_date=current_plan["latest_run_date"],
            latest_upload=current_plan["latest_upload"],
            dashboard_plan_source=current_plan.get("plan_source", "deterministic"),
            week_snapshot=week_snapshot,
            load_weeks=load_weeks,
            consistency=compute_consistency_score(
                rows, sessions_target
            ),
        ),
    )


@router.get("/import")
def import_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="import.html",
        context=_base(
            request,
            "import",
            result=None,
            health_result=None,
            error=None,
        ),
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
        context=_base(
            request,
            "import",
            result=result,
            health_result=None,
            error=error,
        ),
    )


@router.post("/import/apple-health")
def import_apple_health_upload(
    request: Request,
    archive: UploadFile = File(...),
):
    settings, database = _state(request)
    result = None
    error = None
    temp_path: Path | None = None
    try:
        if not (archive.filename or "").lower().endswith(".zip"):
            raise ValueError("Please choose the ZIP exported by Apple Health.")
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as temp:
            shutil.copyfileobj(archive.file, temp)
            temp_path = Path(temp.name)
        result = import_apple_health_zip(temp_path, database)
        export_from_database(settings, database)
    except ValueError as exc:
        error = str(exc)
    finally:
        archive.file.close()
        if temp_path:
            temp_path.unlink(missing_ok=True)
    return templates.TemplateResponse(
        request=request,
        name="import.html",
        context=_base(
            request,
            "import",
            result=None,
            health_result=result,
            error=error,
        ),
    )


@router.get("/activities")
def activities(
    request: Request,
    q: str = Query("", max_length=80),
    page_number: int = Query(1, alias="page", ge=1),
):
    _, _, _, rows, _, _ = _data(request)
    query = q.strip().lower()
    ordered = list(reversed(rows))
    if query:
        ordered = [
            row
            for row in ordered
            if query in str(row.get("name") or "").lower()
            or query in str(row.get("start_date_local") or "").lower()
            or query in str(row.get("sport_type") or "").lower()
            or query in str(row.get("session_type_guess") or "").lower()
        ]
    page_size = 10
    total = len(ordered)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page_number = min(page_number, total_pages)
    start = (page_number - 1) * page_size
    return templates.TemplateResponse(
        request=request,
        name="activities.html",
        context=_base(
            request,
            "activities",
            activities=ordered[start : start + page_size],
            query=q.strip(),
            page_number=page_number,
            total_pages=total_pages,
            total_results=total,
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
            checkin=record.get("checkin"),
        ),
    )


@router.post("/activity/{activity_id}/checkin")
def save_activity_checkin(
    request: Request,
    activity_id: int,
    perceived_effort: int = Form(...),
    soreness: int = Form(...),
    pain: str | None = Form(None),
    sleep_quality: int = Form(...),
    energy_level: int = Form(...),
    followed_walk_strategy: str = Form(...),
    unrecorded_walk_minutes: int = Form(0),
    notes: str = Form(""),
):
    settings, database = _state(request)
    if activity_id not in database.activity_ids():
        return RedirectResponse("/activities", status_code=303)
    database.save_checkin(
        activity_id,
        {
            "perceived_effort": max(1, min(10, perceived_effort)),
            "soreness": max(0, min(10, soreness)),
            "pain": pain == "yes",
            "sleep_quality": max(1, min(5, sleep_quality)),
            "energy_level": max(1, min(5, energy_level)),
            "followed_walk_strategy": followed_walk_strategy == "yes",
            "unrecorded_walk_minutes": max(
                0, min(300, unrecorded_walk_minutes)
            ),
            "notes": notes.strip()[:1000] or None,
        },
    )
    export_from_database(settings, database)
    return RedirectResponse(
        f"/activity/{activity_id}?checkin_saved=1", status_code=303
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
        recovery=compute_recovery_summary(
            rows, database.health_daily(limit=14)
        ),
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
    from app.workflow_routes import create_proposal
    return await create_proposal(request)


def _plan_context(request: Request, generated_result=None) -> dict[str, Any]:
    from app.plans import plan_state
    settings, database = _state(request)
    return _base(request, "plan", **plan_state(settings, database))


@router.get("/plan")
def plan(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="plan.html",
        context=_plan_context(request),
    )


@router.post("/plan/regenerate")
async def regenerate_plan(request: Request):
    from app.workflow_routes import create_proposal
    return await create_proposal(request)


@router.get("/settings")
def settings_page(request: Request):
    settings, database = _state(request)
    preferences = database.get_preferences()
    effective_settings = settings.with_preferences(preferences)
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context=_base(
            request,
            "settings",
            key_configured=bool(settings.gemini_api_key),
            database_path=str(database.path.resolve()),
            export_dir=str(Path(settings.export_dir).resolve()),
            max_hr=effective_settings.max_hr,
            hr_zones=effective_settings.hr_zone_bounds,
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
    max_hr: str = Form(""),
    hr_zone_bounds: str = Form(""),
):
    _, database = _state(request)
    sessions = max(1, min(3, int(preferred_sessions_per_week)))
    allowed_goals = {
        "continuous_5k",
        "fewer_walk_breaks",
        "longer_continuous_segments",
        "comfortable_7k",
        "comfortable_10k",
        "build_endurance",
        "consistency",
    }
    goal = training_goal if training_goal in allowed_goals else "continuous_5k"
    parsed_max_hr: int | None = None
    if max_hr.strip():
        try:
            parsed_max_hr = max(100, min(240, int(max_hr)))
        except ValueError:
            parsed_max_hr = None
    parsed_zones: list[int] | None = None
    if hr_zone_bounds.strip():
        try:
            values = [
                int(item.strip())
                for item in hr_zone_bounds.split(",")
                if item.strip()
            ]
            if (
                len(values) == 5
                and values == sorted(values)
                and all(60 <= value <= 240 for value in values)
                and (parsed_max_hr is None or values[-1] <= parsed_max_hr)
            ):
                parsed_zones = values
        except ValueError:
            parsed_zones = None
    database.save_preferences(
        {
            "preferred_sessions_per_week": sessions,
            "training_goal": goal,
            "max_hr": parsed_max_hr,
            "hr_zone_bounds": parsed_zones,
        }
    )
    return RedirectResponse("/settings?saved=1", status_code=303)
