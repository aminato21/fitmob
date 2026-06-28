from __future__ import annotations

import json
import statistics
from abc import ABC, abstractmethod
from datetime import datetime, timezone
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
        response = await self._request(self.settings.ai_model, safe_payload)
        if (
            response.status_code in {400, 404}
            and self.settings.ai_fallback_model
            and self.settings.ai_fallback_model != self.settings.ai_model
        ):
            response = await self._request(
                self.settings.ai_fallback_model, safe_payload
            )
            self.model_used = self.settings.ai_fallback_model
        if response.status_code == 429:
            raise AIProviderError("Gemini rate limit reached")
        if response.is_error:
            raise AIProviderError(f"Gemini returned HTTP {response.status_code}")
        try:
            body = response.json()
            text = body["candidates"][0]["content"]["parts"][0]["text"]
            return CoachAnalysis.model_validate_json(text)
        except (
            KeyError,
            IndexError,
            TypeError,
            ValueError,
            ValidationError,
        ) as exc:
            raise AIProviderError("Gemini returned invalid structured JSON") from exc

    async def _request(
        self, model_name: str, safe_payload: dict[str, Any]
    ) -> httpx.Response:
        model = quote(model_name, safe="")
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent"
        )
        request_body = {
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": (
                                "Analyze this anonymized training summary:\n"
                                + json.dumps(
                                    safe_payload,
                                    ensure_ascii=False,
                                    separators=(",", ":"),
                                )
                            )
                        }
                    ],
                }
            ],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseJsonSchema": CoachAnalysis.model_json_schema(),
                "temperature": 0.2,
            },
        }
        try:
            async with httpx.AsyncClient(
                timeout=45, transport=self.transport
            ) as client:
                response = await client.post(
                    url,
                    headers={
                        "x-goog-api-key": self.settings.gemini_api_key,
                        "Content-Type": "application/json",
                    },
                    json=request_body,
                )
        except httpx.HTTPError as exc:
            raise AIProviderError("Gemini could not be reached") from exc
        return response


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
            }
        )
    return safe


def build_safe_payload(
    records: list[dict[str, Any]],
    settings: Settings,
    preferences: dict[str, Any] | None = None,
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
        "schema_version": 2,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "user_context": settings.ai_user_context,
        "training_preferences": {
            "sessions_per_week": sessions_per_week,
            "goal": training_goal,
            "preferred_normal_days": ["Wednesday", "Sunday"],
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
        "comfortable_7k": "make 7 km feel calmer with planned walking",
        "consistency": "establish a sustainable weekly routine",
    }.get(goal, "build consistent beginner run/walk fitness")


def _walk_strategy(capabilities: dict[str, Any], distance_km: float) -> str:
    continuous = capabilities.get("estimated_continuous_km")
    if continuous and continuous >= distance_km * 0.9:
        return "Run comfortably; an optional 60–90 sec walk is allowed before fatigue."
    segment = min(distance_km * 0.55, (continuous or 2.0) * 0.8)
    segment = max(1.0, round(segment, 1))
    remaining = max(0.5, round(distance_km - segment - 0.2, 1))
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
        strategy = _walk_strategy(capabilities, distance_km)
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
    sessions_per_week: int = 2,
    training_goal: str = "continuous_5k",
) -> CoachAnalysis:
    sessions_per_week = max(1, min(3, int(sessions_per_week)))
    if not rows:
        starter_days = {
            1: ["Sunday"],
            2: ["Wednesday", "Sunday"],
            3: ["Tuesday", "Thursday", "Sunday"],
        }[sessions_per_week]
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
            )
            for index, (day, session_type) in enumerate(
                zip(starter_days, starter_types)
            )
        ]
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
            main_findings=["Import at least one 2026 run to establish a baseline."],
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
    consistency = compute_consistency_score(rows, sessions_per_week)
    active_risk = bool(
        latest.get("injury_risk_flag")
        or latest.get("sudden_volume_increase_flag")
        or latest.get("too_many_hard_sessions_flag")
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
    if active_risk:
        risks.append(
            "A recent workload or hard-session flag is active; avoid speed work."
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
    recent_paces = [
        row["pace_sec_per_km"] for row in rows[-5:] if row.get("pace_sec_per_km")
    ]
    pace = statistics.median(recent_paces) if recent_paces else 540
    days = {
        1: ["Sunday"],
        2: ["Wednesday", "Sunday"],
        3: ["Tuesday", "Thursday", "Sunday"],
    }[sessions_per_week]
    week_multipliers = [1.0, 1.05, 0.85, 1.05]
    themes = ["Settle into rhythm", "Small controlled build", "Recovery week", "Consolidate"]
    week_plans: list[WeekPlan] = []
    for week_number, multiplier in enumerate(week_multipliers, 1):
        if sessions_per_week == 1:
            types = ["easy_run_walk"]
            factors = [1.0]
        elif sessions_per_week == 2:
            types = ["easy_run_walk", "recovery_walk" if active_risk else "long_run_walk"]
            factors = [0.82, 1.05]
        else:
            types = [
                "recovery_walk" if active_risk else "easy_run_walk",
                "easy_run_walk" if active_risk else "beginner_intervals",
                "long_run_walk",
            ]
            factors = [0.62, 0.78, 1.05]
        week_sessions = [
            _make_session(
                day,
                session_type,
                baseline * factor * multiplier * (0.9 if active_risk else 1),
                pace,
                capabilities,
                active_risk,
            )
            for day, session_type, factor in zip(days, types, factors)
        ]
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
            "Pain, unusual fatigue, and recovery between Wednesday and Sunday.",
            "Walking-break count and longest comfortable running segment.",
            "Heart-rate trend only when reliable sensor data is available.",
        ],
        questions_for_user=[
            "How hard did the latest session feel in your own words?",
            "Did you have pain during the run or the following day?",
        ],
    )


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
    )


async def run_ai_analysis(
    settings: Settings,
    database: Database,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    database.initialize()
    output = Path(settings.export_dir)
    output.mkdir(parents=True, exist_ok=True)
    preferences = database.get_preferences()
    sessions_per_week = int(
        preferences.get(
            "preferred_sessions_per_week", settings.preferred_sessions_per_week
        )
    )
    training_goal = str(preferences.get("training_goal", settings.training_goal))
    safe_payload, rows = build_safe_payload(
        database.all_activities(), settings, preferences
    )
    fallback = deterministic_analysis(rows, sessions_per_week, training_goal)

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

    if provider_requested == "gemini" and not settings.gemini_api_key:
        fallback_reason = "GEMINI_API_KEY is not configured"
        status = "deterministic_fallback"
    elif provider_requested == "gemini":
        try:
            provider = GeminiProvider(settings, transport)
            analysis = await provider.analyze(safe_payload)
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
    return {
        "status": status,
        "provider_requested": provider_requested,
        "provider_used": provider_used,
        "model": model_used,
        "fallback_reason": fallback_reason,
        "analysis": analysis.model_dump(mode="json"),
        "safe_payload_file": str((output / "ai_safe_payload.json").resolve()),
        "medical_disclaimer": (
            "This is training information, not medical advice or a diagnosis."
        ),
    }
