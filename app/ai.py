from __future__ import annotations

import json
import statistics
from abc import ABC, abstractmethod
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.analysis import (
    build_run_rows,
    compute_consistency_score,
    compute_current_capabilities,
    compute_personal_bests,
    compute_recovery_summary,
    compute_walk_break_trend,
)
from app.config import Settings
from app.db import Database
from app.exporter import make_summary


class PlannedSession(BaseModel):
    model_config = ConfigDict(extra="forbid")

    day: str
    session_type: str
    target_distance_km: float = Field(ge=0.5, le=20)
    duration_minutes: int = Field(ge=10, le=180)
    walk_strategy: str
    warmup: str
    focus: str
    guidance: str


class WeekPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    week: int = Field(ge=1, le=4)
    theme: str
    sessions: list[PlannedSession] = Field(min_length=1, max_length=3)
    weekly_target_km: float = Field(ge=0.5, le=40)
    key_goal: str


class CoachAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_level: str
    progress_highlights: list[str]
    walk_break_analysis: str
    main_findings: list[str]
    risks: list[str]
    next_week: WeekPlan
    four_week_plan: list[WeekPlan] = Field(min_length=4, max_length=4)
    milestone_targets: list[str]
    what_to_watch_next: list[str]
    questions_for_user: list[str]


SYSTEM_PROMPT = """You are a conservative beginner run/walk coach and analyst.
Explain recommendations briefly using supplied evidence; acknowledge missing or old uploads.
Use a flexible session sequence rather than fixed weekdays.
Use only the supplied anonymized training summary and its configured goal and
session preference. Walking breaks are a valid training tool; never shame them
or treat total distance as continuous-running ability. A 7 km run/walk is not
an advanced continuous long run.

Build a practical 1-4 week progression with 1-3 sessions per week exactly as
the preference allows. Give every session a conservative distance target,
warm-up, specific walk strategy, focus, and plain-language guidance. Vary easy,
long run/walk, recovery, and only carefully controlled beginner intervals when
the data supports them. Do not prescribe sprinting, tempo work, aggressive
distance increases, or consecutive hard days. Week 3 should normally reduce
load for recovery. If any injury-risk, sudden-volume, or too-many-hard-session
flag is active, remove speed work and reduce volume.

Subjective pain, high soreness, low energy, or poor sleep always outweighs a
good pace or watch score. Heart-rate and sleep data are context, not diagnoses.
Explain why each session is appropriate and never use missing recovery data as
evidence that the runner is ready for harder work.

Use comparable-distance walk-break trends, capabilities, personal bests, and
consistency without inventing missing data. Do not infer routes or locations.
Do not pretend to be a doctor and do not diagnose. Pain, illness, or unusual
fatigue overrides the plan. Explain decisions simply and return only strict JSON
matching the required schema."""


class AIProviderError(RuntimeError):
    pass


class AnalysisProvider(ABC):
    """Small provider boundary so another provider can be added later."""

    name: str

    @abstractmethod
    async def analyze(self, safe_payload: dict[str, Any]) -> CoachAnalysis:
        raise NotImplementedError


class GeminiProvider(AnalysisProvider):
    name = "gemini"

    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.settings = settings
        self.transport = transport
        self.model_used = settings.ai_model

    async def analyze(self, safe_payload: dict[str, Any]) -> CoachAnalysis:
        import asyncio
        try:
            async with asyncio.timeout(30):
                response = await self._request(self.settings.ai_model, safe_payload)
                if response.status_code == 429:
                    raise AIProviderError("Gemini rate limit reached")
                if response.is_error:
                    raise AIProviderError(f"Gemini returned HTTP {response.status_code}")
                text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
                return CoachAnalysis.model_validate_json(text)
        except AIProviderError:
            raise
        except Exception as exc:
            raise AIProviderError("Gemini analysis failed safely") from exc

    async def _request(self, model_name: str, safe_payload: dict[str, Any], *,
                       system_prompt=SYSTEM_PROMPT, response_schema=None) -> httpx.Response:
        model = quote(model_name, safe="")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        body = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": json.dumps(safe_payload, ensure_ascii=False, separators=(",", ":"))}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseJsonSchema": response_schema or _gemini_response_schema(),
                "temperature": 0.2,
                "maxOutputTokens": 8192,
            },
        }
        async with httpx.AsyncClient(timeout=30, transport=self.transport) as client:
            return await client.post(url, headers={"x-goog-api-key": self.settings.gemini_api_key,
                                                   "Content-Type": "application/json"}, json=body)


def _gemini_response_schema() -> dict[str, Any]:
    """Keep Gemini's serving schema small; Pydantic still validates strictly."""
    schema = CoachAnalysis.model_json_schema()
    unsupported_or_expensive = {
        "title",
        "additionalProperties",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "minItems",
        "maxItems",
        "minLength",
        "maxLength",
    }

    def simplify(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: simplify(item)
                for key, item in value.items()
                if key not in unsupported_or_expensive
            }
        if isinstance(value, list):
            return [simplify(item) for item in value]
        return value

    return simplify(schema)


async def list_gemini_models(
    settings: Settings,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    if not settings.gemini_api_key:
        return {
            "status": "no_api_key",
            "recommended_default": "gemini-3.5-flash",
            "configured_model": settings.ai_model,
            "models": [],
            "message": "Set GEMINI_API_KEY to query models available to your key.",
        }
    models: list[dict[str, Any]] = []
    page_token: str | None = None
    try:
        async with httpx.AsyncClient(timeout=30, transport=transport) as client:
            while True:
                params: dict[str, Any] = {"pageSize": 1000}
                if page_token:
                    params["pageToken"] = page_token
                response = await client.get(
                    "https://generativelanguage.googleapis.com/v1beta/models",
                    params=params,
                    headers={"x-goog-api-key": settings.gemini_api_key},
                )
                if response.is_error:
                    return {
                        "status": "error",
                        "recommended_default": "gemini-3.5-flash",
                        "configured_model": settings.ai_model,
                        "models": [],
                        "message": f"Gemini returned HTTP {response.status_code}",
                    }
                body = response.json()
                for item in body.get("models", []):
                    methods = item.get("supportedGenerationMethods") or []
                    if "generateContent" not in methods:
                        continue
                    model_id = str(item.get("name", "")).removeprefix("models/")
                    models.append(
                        {
                            "id": model_id,
                            "display_name": item.get("displayName"),
                            "description": item.get("description"),
                            "input_token_limit": item.get("inputTokenLimit"),
                            "output_token_limit": item.get("outputTokenLimit"),
                            "configured": model_id == settings.ai_model,
                        }
                    )
                page_token = body.get("nextPageToken")
                if not page_token:
                    break
    except (httpx.HTTPError, ValueError):
        return {
            "status": "error",
            "recommended_default": "gemini-3.5-flash",
            "configured_model": settings.ai_model,
            "models": [],
            "message": "Could not query the Gemini model catalog.",
        }
    return {
        "status": "ok",
        "recommended_default": "gemini-3.5-flash",
        "configured_model": settings.ai_model,
        "models": sorted(models, key=lambda item: item["id"]),
        "message": (
            "Availability is key-specific; check the official pricing page "
            "separately for current free-tier terms."
        ),
    }


def _latest_safe_activities(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    safe: list[dict[str, Any]] = []
    for row in rows[-10:]:
        safe.append(
            {
                "date": row["_date"].isoformat(),
                "sport_type": row.get("sport_type"),
                "distance_km": row.get("distance_km"),
                "moving_time_sec": row.get("moving_time_sec"),
                "pace_sec_per_km": row.get("pace_sec_per_km"),
                "pace_min_per_km": row.get("pace_min_per_km"),
                "run_walk_detected": row.get("run_walk_detected"),
                "estimated_walk_break_count": row.get(
                    "estimated_walk_break_count"
                ),
                "estimated_total_walk_time_sec": row.get(
                    "estimated_total_walk_time_sec"
                ),
                "longest_continuous_run_estimate_sec": row.get(
                    "longest_continuous_run_estimate_sec"
                ),
                "average_heartrate": row.get("average_heartrate"),
                "max_heartrate": row.get("max_heartrate"),
                "session_type_guess": row.get("session_type_guess"),
                "effort_guess": row.get("effort_guess"),
                "injury_risk_flag": row.get("injury_risk_flag"),
                "sudden_volume_increase_flag": row.get(
                    "sudden_volume_increase_flag"
                ),
                "too_many_hard_sessions_flag": row.get(
                    "too_many_hard_sessions_flag"
                ),
                "recovery_flag": row.get("recovery_flag"),
                "heart_rate_source": row.get("heart_rate_source"),
                "perceived_effort": row.get("perceived_effort"),
                "soreness": row.get("soreness"),
                "pain_reported": row.get("pain_reported"),
                "sleep_quality": row.get("sleep_quality"),
                "energy_level": row.get("energy_level"),
                "followed_walk_strategy": row.get(
                    "followed_walk_strategy"
                ),
                "unrecorded_walk_minutes": row.get(
                    "unrecorded_walk_minutes"
                ),
            }
        )
    return safe


def build_safe_payload(
    records: list[dict[str, Any]],
    settings: Settings,
    preferences: dict[str, Any] | None = None,
    health_daily: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows, _ = build_run_rows(records, settings)
    summary = make_summary(rows)
    preferences = preferences or {}
    sessions_per_week = int(
        preferences.get(
            "preferred_sessions_per_week", settings.preferred_sessions_per_week
        )
    )
    training_goal = str(
        preferences.get("training_goal", settings.training_goal)
    )
    payload = {
        "schema_version": 3,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "user_context": settings.ai_user_context,
        "training_preferences": {
            "sessions_per_week": sessions_per_week,
            "goal": training_goal,
            "schedule_style": "flexible_sequence",
        },
        "totals": {
            "total_runs": summary["total_runs"],
            "total_distance_km": summary["total_distance_km"],
            "total_time_sec": summary["total_time_sec"],
            "average_pace_sec_per_km": summary["average_pace_sec_per_km"],
            "average_pace_min_per_km": summary["average_pace_min_per_km"],
            "longest_run_distance_km": (
                summary["longest_run"]["distance_km"]
                if summary["longest_run"]
                else None
            ),
            "best_pace_sec_per_km": (
                summary["best_pace"]["pace_sec_per_km"]
                if summary["best_pace"]
                else None
            ),
        },
        "weekly_totals": summary["weekly_totals"],
        "monthly_totals": summary["monthly_totals"],
        "progression_trend": summary["progression_trend"],
        "walk_break_trend": compute_walk_break_trend(rows),
        "current_capabilities": compute_current_capabilities(rows),
        "personal_bests": compute_personal_bests(rows),
        "consistency": compute_consistency_score(rows, sessions_per_week),
        "recovery_context": compute_recovery_summary(rows, health_daily),
        "latest_activities": _latest_safe_activities(rows),
        "safety_flags": {
            "injury_risk_sessions": sum(
                bool(row.get("injury_risk_flag")) for row in rows
            ),
            "sudden_volume_increase_sessions": sum(
                bool(row.get("sudden_volume_increase_flag")) for row in rows
            ),
            "too_many_hard_sessions": sum(
                bool(row.get("too_many_hard_sessions_flag")) for row in rows
            ),
            "subjective_recovery_flags": sum(
                bool(row.get("recovery_flag")) for row in rows
            ),
        },
        "privacy": {
            "raw_gps_included": False,
            "route_geometry_included": False,
            "exact_location_included": False,
        },
    }
    return payload, rows


def _goal_text(goal: str) -> str:
    return {
        "continuous_5k": "build toward a comfortable continuous 5 km",
        "fewer_walk_breaks": "reduce walking breaks without forcing the pace",
        "longer_continuous_segments": (
            "extend comfortable running segments before each planned walk"
        ),
        "comfortable_7k": "make 7 km feel calmer with planned walking",
        "comfortable_10k": (
            "build gradually toward a comfortable 10 km run/walk"
        ),
        "build_endurance": "build general easy endurance without chasing pace",
        "consistency": "establish a sustainable weekly routine",
    }.get(goal, "build consistent beginner run/walk fitness")


def _walk_strategy(
    capabilities: dict[str, Any], distance_km: float, training_goal: str
) -> str:
    continuous = capabilities.get("estimated_continuous_km")
    if training_goal == "comfortable_10k":
        return (
            "Use planned walks from the start: about 8 minutes easy running, "
            "then 90 seconds walking. Repeat calmly; do not wait until exhausted."
        )
    if training_goal in {"comfortable_7k", "build_endurance"}:
        return (
            "Use a relaxed 10-minute run / 90-second walk rhythm from the "
            "start, adding an earlier walk whenever form or breathing fades."
        )
    if training_goal == "fewer_walk_breaks":
        return (
            "Repeat the recent comfortable structure and aim for at most one "
            "fewer walk only if breathing and form stay relaxed."
        )
    if training_goal in {"continuous_5k", "longer_continuous_segments"}:
        return (
            "Run comfortably only to the point before form fades, take a "
            "60–90 second walk, then restart easily. Extend the first segment "
            "gradually rather than forcing the total distance continuously."
        )
    if continuous and continuous >= distance_km * 0.9:
        return "Run comfortably; an optional 60–90 sec walk is allowed before fatigue."
    segment = min(distance_km * 0.55, (continuous or 2.0) * 0.8)
    segment = round(min(distance_km - 0.7, max(0.5, segment)), 1)
    remaining = round(distance_km - segment - 0.2, 1)
    return (
        f"Run about {segment:.1f} km, walk 150–200 m, then run the remaining "
        f"about {remaining:.1f} km easily; add another short walk if form fades."
    )


def _make_session(
    day: str,
    session_type: str,
    distance_km: float,
    pace_sec_per_km: float,
    capabilities: dict[str, Any],
    risk_active: bool,
    training_goal: str,
) -> PlannedSession:
    distance_km = round(max(1.5, distance_km), 1)
    duration = round((distance_km * pace_sec_per_km / 60 + 5) / 5) * 5
    if session_type == "recovery_walk":
        strategy = "Walk briskly but comfortably; no running is required."
        focus = "Finish fresher than you started."
    elif session_type == "beginner_intervals":
        strategy = (
            "After the warm-up, repeat 4 × 1 min gently quicker with 2 min "
            "easy walk/jog recovery; never sprint."
        )
        focus = "Controlled rhythm and relaxed form, not maximum speed."
    else:
        strategy = _walk_strategy(capabilities, distance_km, training_goal)
        focus = (
            "Conversational effort; keep volume stable."
            if risk_active
            else "Comfortable breathing and smooth, repeatable running."
        )
    return PlannedSession(
        day=day,
        session_type=session_type,
        target_distance_km=distance_km,
        duration_minutes=max(15, min(180, int(duration))),
        walk_strategy=strategy,
        warmup="5 minutes of brisk walking plus gentle ankle and leg movement.",
        focus=focus,
        guidance=(
            "Stop or switch to walking for pain, dizziness, or unusual fatigue. "
            "The distance is a ceiling, not a requirement."
        ),
    )


def deterministic_analysis(
    rows: list[dict[str, Any]],
    sessions_per_week: int = 1,
    training_goal: str = "consistency",
    as_of: date | None = None,
) -> CoachAnalysis:
    sessions_per_week = max(1, min(3, int(sessions_per_week)))
    if not rows:
        starter_days = [f"Session {index}" for index in range(1, sessions_per_week + 1)]
        starter_types = {
            1: ["easy_run_walk"],
            2: ["easy_run_walk", "long_run_walk"],
            3: ["easy_run_walk", "recovery_walk", "long_run_walk"],
        }[sessions_per_week]
        starter_sessions = [
            _make_session(
                day,
                session_type,
                2.0 + index * 0.5,
                540,
                {},
                False,
                training_goal,
            )
            for index, (day, session_type) in enumerate(
                zip(starter_days, starter_types)
            )
        ]
        for session in starter_sessions:
            session.guidance = (
                "No imported baseline is available, so this is a gentle starting point; "
                "start shorter if needed. " + session.guidance
            )
        week_one = WeekPlan(
            week=1,
            theme="Gentle baseline",
            sessions=starter_sessions,
            weekly_target_km=round(
                sum(item.target_distance_km for item in starter_sessions), 1
            ),
            key_goal="Learn what conversational effort feels like.",
        )
        return CoachAnalysis(
            current_level="Not enough running history yet.",
            progress_highlights=["The first useful milestone is simply starting consistently."],
            walk_break_analysis="No stream-based walk-break history is available yet.",
            main_findings=["Import at least one run to establish a baseline."],
            risks=["No activity history is available for workload comparison."],
            next_week=week_one,
            four_week_plan=[
                WeekPlan(
                    week=week,
                    theme="Learn and repeat",
                    sessions=[
                        item.model_copy() for item in starter_sessions
                    ],
                    weekly_target_km=week_one.weekly_target_km,
                    key_goal="Build a comfortable routine; walking breaks are welcome.",
                )
                for week in range(1, 5)
            ],
            milestone_targets=["Complete four comfortable sessions without chasing pace."],
            what_to_watch_next=["Comfort, soreness, and recovery after each session."],
            questions_for_user=[
                "Did the latest session feel easy, moderate, or hard?"
            ],
        )

    latest = rows[-1]
    longest = max(rows, key=lambda row: row.get("distance_km") or 0)
    walk_sessions = sum(row.get("run_walk_detected") is True for row in rows)
    capabilities = compute_current_capabilities(rows)
    personal_bests = compute_personal_bests(rows)
    walk_trend = compute_walk_break_trend(rows)
    today_date = as_of or date.today()
    latest_run_date = latest["_date"]
    gap_days = (today_date - latest_run_date).days

    inactivity_multiplier = 1.0
    inactivity_recovery_flag = False

    if gap_days > 14:
        inactivity_recovery_flag = True
        weeks_missed = (gap_days - 14) // 7 + 1
        inactivity_multiplier = max(0.60, 1.0 - (weeks_missed * 0.10))

    consistency = compute_consistency_score(rows, sessions_per_week, as_of=today_date)
    active_risk = bool(
        latest.get("injury_risk_flag")
        or latest.get("sudden_volume_increase_flag")
        or latest.get("too_many_hard_sessions_flag")
        or latest.get("recovery_flag")
        or inactivity_recovery_flag
    )
    longest_text = f"{longest.get('distance_km') or 0:.1f} km"
    level = (
        f"Beginner run/walk runner. Longest recorded session is {longest_text}; "
        "distance does not imply continuous running."
    )
    findings = [
        f"{len(rows)} runs are available for analysis.",
        f"Run/walk structure was detected in {walk_sessions} sessions.",
        f"Consistency score is {consistency['score']}/100 ({consistency['label']}).",
        "Walking breaks count as useful training and are not a failure.",
    ]
    if inactivity_recovery_flag:
        findings.append(
            f"The latest imported run is {gap_days} days old. Unuploaded runs are unknown; "
            "the proposal uses a conservative workload until newer data is available."
        )
    if latest.get("perceived_effort") is not None:
        findings.append(
            "Latest self-reported effort was "
            f"{latest['perceived_effort']}/10; subjective feedback takes "
            "priority over pace."
        )
    highlights = [
        f"Longest completed session: {longest_text}.",
        walk_trend["summary"],
    ]
    if capabilities.get("estimated_continuous_km"):
        highlights.append(
            "Longest stream-estimated continuous segment is about "
            f"{capabilities['estimated_continuous_km']:.1f} km."
        )
    risks = []
    if inactivity_recovery_flag:
        risks.append(
            "Old uploads cannot establish current readiness. Import recent runs and "
            "use easy effort while current workload is uncertain."
        )
    if active_risk and not inactivity_recovery_flag:
        risks.append(
            "A recent workload or hard-session flag is active; avoid speed work."
        )
    if latest.get("pain_reported"):
        risks.append(
            "Pain was reported after the latest session; choose rest or gentle "
            "walking and seek professional advice if it persists or worsens."
        )
    if not risks:
        risks.append(
            "No current rule-based workload flag, but pain or unusual fatigue overrides the data."
        )
    recent_distances = [
        row.get("distance_km") or 0 for row in rows[-5:] if row.get("distance_km")
    ]
    baseline = (
        statistics.median(recent_distances)
        if recent_distances
        else capabilities.get("recent_typical_distance_km") or 3.0
    )
    baseline = baseline * inactivity_multiplier
    recent_paces = [
        row["pace_sec_per_km"] for row in rows[-5:] if row.get("pace_sec_per_km")
    ]
    pace = statistics.median(recent_paces) if recent_paces else 540
    days = [f"Session {index}" for index in range(1, sessions_per_week + 1)]
    week_multipliers = [1.0, 1.04, 0.94, 1.03]
    themes = ["Settle into rhythm", "Small controlled build", "Recovery week", "Consolidate"]
    long_factor = {
        "continuous_5k": 1.02,
        "fewer_walk_breaks": 1.02,
        "longer_continuous_segments": 1.04,
        "comfortable_7k": 1.07,
        "comfortable_10k": 1.12,
        "build_endurance": 1.08,
        "consistency": 1.0,
    }.get(training_goal, 1.02)
    week_plans: list[WeekPlan] = []
    for week_number, multiplier in enumerate(week_multipliers, 1):
        if sessions_per_week == 1:
            types = ["easy_run_walk"]
            factors = [1.0]
        elif sessions_per_week == 2:
            types = ["easy_run_walk", "recovery_walk" if active_risk else "long_run_walk"]
            factors = [0.82, long_factor]
        else:
            types = [
                "recovery_walk" if active_risk else "easy_run_walk",
                "easy_run_walk",
                "long_run_walk",
            ]
            factors = [0.62, 0.78, long_factor]
        week_sessions = [
            _make_session(
                day,
                session_type,
                baseline * factor * multiplier * (0.9 if active_risk else 1),
                pace,
                capabilities,
                active_risk,
                training_goal,
            )
            for day, session_type, factor in zip(days, types, factors)
        ]
        for session in week_sessions:
            explanation = (
                f"This ceiling uses the median distance of your last {len(recent_distances)} "
                "imported runs, with a lighter third stage. "
            ) if recent_distances else "Distance data is missing, so the workload stays conservative. "
            if active_risk:
                explanation += "Uncertain or flagged recovery keeps this session easy. "
            session.guidance = explanation + session.guidance
        weekly_target = round(
            sum(session.target_distance_km for session in week_sessions), 1
        )
        week_plans.append(
            WeekPlan(
                week=week_number,
                theme=themes[week_number - 1],
                sessions=week_sessions,
                weekly_target_km=weekly_target,
                key_goal=(
                    "Reduce load and finish every session fresh."
                    if week_number == 3 or active_risk
                    else _goal_text(training_goal).capitalize() + "."
                ),
            )
        )
    milestones = [
        f"Maintain about {sessions_per_week} session(s) per week for four weeks.",
        "Make comparable runs feel easier before adding distance.",
    ]
    continuous = capabilities.get("estimated_continuous_km")
    if continuous:
        milestones.append(
            f"Move the comfortable continuous estimate from {continuous:.1f} km "
            f"toward {min(5.0, continuous + 0.5):.1f} km without forcing it."
        )
    if capabilities.get("five_k_with_breaks"):
        milestones.append("Complete a comfortable 5 km with the same or fewer planned walks.")
    if training_goal == "comfortable_10k":
        milestones.extend(
            [
                "First make 7 km run/walk feel repeatable and well recovered.",
                "Build the longest easy session by no more than about 0.5 km "
                "after a comfortable week; 10 km is a multi-block goal, not a "
                "four-week test.",
            ]
        )
    elif training_goal == "comfortable_7k":
        milestones.append(
            "Make 7 km comfortable with planned walks before trying to reduce them."
        )
    elif training_goal == "fewer_walk_breaks":
        milestones.append(
            "Reduce at most one planned walk on comparable sessions when effort stays easy."
        )
    return CoachAnalysis(
        current_level=level,
        progress_highlights=highlights,
        walk_break_analysis=walk_trend["summary"],
        main_findings=findings,
        risks=risks,
        next_week=week_plans[0],
        four_week_plan=week_plans,
        milestone_targets=milestones,
        what_to_watch_next=[
            "Pain, unusual fatigue, and recovery between sessions; choose days flexibly.",
            "Walking-break count and longest comfortable running segment.",
            "Heart-rate trend only when reliable sensor data is available.",
        ],
        questions_for_user=[
            "How hard did the latest session feel in your own words?",
            "Did you have pain during the run or the following day?",
        ],
    )


def enforce_plan_safety(
    candidate: CoachAnalysis,
    fallback: CoachAnalysis,
    rows: list[dict[str, Any]],
    sessions_per_week: int,
) -> tuple[CoachAnalysis, list[str]]:
    """Replace unsafe AI plans while preserving safe narrative findings."""
    reasons: list[str] = []
    risk_active = bool(
        rows
        and (
            rows[-1].get("injury_risk_flag")
            or rows[-1].get("recovery_flag")
            or rows[-1].get("pain_reported")
            or rows[-1].get("sudden_volume_increase_flag")
            or rows[-1].get("too_many_hard_sessions_flag")
        )
    )
    risk_active = risk_active or not rows or (date.today() - rows[-1]['_date']).days > 14
    forbidden = ("sprint", "tempo", "max effort", "all-out", "all out")
    weeks = candidate.four_week_plan
    if [week.week for week in weeks] != [1, 2, 3, 4]:
        reasons.append("stages were not ordered from 1 to 4")
    if candidate.next_week.model_dump() != weeks[0].model_dump():
        reasons.append("next-week content differed from the first stage")
    if len(candidate.next_week.sessions) != sessions_per_week:
        reasons.append("next-week session count did not match preferences")
    if any(len(week.sessions) != sessions_per_week for week in weeks):
        reasons.append("four-week session count did not match preferences")
    for index, week in enumerate([candidate.next_week, *weeks]):
        baseline_index = max(0, index - 1)
        calculated = round(sum(item.target_distance_km for item in week.sessions), 1)
        if abs(calculated - week.weekly_target_km) > 0.2:
            reasons.append(f"week {index + 1} total did not match its sessions")
        safe_ceiling = fallback.four_week_plan[baseline_index].weekly_target_km * 1.15
        if week.weekly_target_km > safe_ceiling + 0.1:
            reasons.append(f"week {index + 1} exceeded the safe volume ceiling")
        if index > 1 and week.weekly_target_km > weeks[baseline_index - 1].weekly_target_km * 1.1:
            reasons.append(f"week {index + 1} increased by more than 10%")
        if index == 3 and week.weekly_target_km > weeks[baseline_index - 1].weekly_target_km:
            reasons.append("week 3 was not a recovery week")
        duration_ceiling = max(session.duration_minutes for session in fallback.four_week_plan[baseline_index].sessions) * 1.2 + 5
        for session in week.sessions:
            if session.duration_minutes > duration_ceiling:
                reasons.append("session duration exceeded the local conservative ceiling")
            text = " ".join(
                [
                    session.session_type,
                    session.warmup,
                    session.walk_strategy,
                    session.focus,
                    session.guidance,
                ]
            ).lower()
            if any(term in text for term in forbidden):
                reasons.append("plan contained advanced or maximum-effort work")
            if risk_active and any(
                term in session.session_type.lower()
                for term in ("interval", "fast", "tempo")
            ):
                reasons.append("speed work was prescribed despite a recovery flag")
    if not reasons:
        normalized = candidate.model_copy(deep=True)
        for week in [normalized.next_week, *normalized.four_week_plan]:
            for position, session in enumerate(week.sessions, 1):
                session.day = f"Session {position}"
        return normalized, []
    adjusted = candidate.model_copy(
        update={
            "next_week": fallback.next_week.model_copy(deep=True),
            "four_week_plan": [
                week.model_copy(deep=True) for week in fallback.four_week_plan
            ],
            "risks": list(candidate.risks)
            + [
                "The generated schedule was replaced by local conservative "
                "guardrails."
            ],
        }
    )
    return adjusted, sorted(set(reasons))


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
    )


async def run_ai_analysis(
    settings: Settings,
    database: Database,
    transport: httpx.AsyncBaseTransport | None = None,
    as_of: date | None = None,
) -> dict[str, Any]:
    database.initialize()
    output = Path(settings.export_dir)
    output.mkdir(parents=True, exist_ok=True)
    preferences = database.get_preferences()
    effective_settings = settings.with_preferences(preferences)
    sessions_per_week = int(
        preferences.get(
            "preferred_sessions_per_week", settings.preferred_sessions_per_week
        )
    )
    training_goal = str(preferences.get("training_goal", settings.training_goal))
    safe_payload, rows = build_safe_payload(
        database.all_activities(),
        effective_settings,
        preferences,
        database.health_daily(limit=30),
    )
    fallback = deterministic_analysis(rows, sessions_per_week, training_goal, as_of=as_of)

    # This write intentionally occurs before any possible external request.
    _write_json(output / "ai_safe_payload.json", safe_payload)
    _write_json(
        output / "deterministic_summary.json",
        fallback.model_dump(mode="json"),
    )
    _write_json(
        output / "beginner_training_plan.json",
        {
            "next_week": fallback.next_week.model_dump(mode="json"),
            "four_week_plan": [
                item.model_dump(mode="json") for item in fallback.four_week_plan
            ],
            "milestone_targets": fallback.milestone_targets,
        },
    )

    provider_requested: Literal["none", "gemini"] = settings.ai_provider  # type: ignore[assignment]
    provider_used: Literal["none", "gemini"] = "none"
    status = "deterministic"
    fallback_reason: str | None = None
    model_used: str | None = None
    analysis = fallback
    safety_adjustments: list[str] = []

    if provider_requested == "gemini" and not settings.gemini_api_key:
        fallback_reason = "GEMINI_API_KEY is not configured"
        status = "deterministic_fallback"
    elif provider_requested == "gemini":
        try:
            provider = GeminiProvider(settings, transport)
            analysis = await provider.analyze(safe_payload)
            analysis, safety_adjustments = enforce_plan_safety(
                analysis, fallback, rows, sessions_per_week
            )
            provider_used = "gemini"
            model_used = provider.model_used
            status = "ai"
        except AIProviderError as exc:
            fallback_reason = str(exc)
            status = "deterministic_fallback"
        except Exception:
            # An unexpected provider-side response must never take down the app
            # or prevent the local deterministic result from being available.
            fallback_reason = "Gemini analysis failed safely"
            status = "deterministic_fallback"

    # The AI output file always has the exact strict analysis shape.
    _write_json(output / "ai_analysis.json", analysis.model_dump(mode="json"))
    result = {
        "status": status,
        "provider_requested": provider_requested,
        "provider_used": provider_used,
        "model": model_used,
        "fallback_reason": fallback_reason,
        "safety_adjustments": safety_adjustments,
        "analysis": analysis.model_dump(mode="json"),
        "safe_payload_file": str((output / "ai_safe_payload.json").resolve()),
        "medical_disclaimer": (
            "This is training information, not medical advice or a diagnosis."
        ),
        "training_preferences": {
            "sessions_per_week": sessions_per_week,
            "goal": training_goal,
        },
        "fingerprint": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "activity_count": database.activity_count(),
            "latest_activity_id": database.latest_activity_id(),
            "health_count": database.health_count(),
            "checkin_count": database.checkin_count(),
        },
    }
    _write_json(output / "latest_coach_result.json", result)
    return result
