from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_ignore_empty=True, extra="ignore"
    )

    strava_client_id: str = ""
    strava_client_secret: str = ""
    strava_redirect_uri: str = "http://localhost:8000/auth/callback"
    database_path: Path = Path("./strava.db")
    export_dir: Path = Path("./exports")
    strava_export_distance_unit: str = "km"

    ai_provider: str = "none"
    gemini_api_key: str = ""
    ai_model: str = "gemini-3.5-flash"
    ai_fallback_model: str = "gemini-2.5-flash"
    ai_user_context: str = (
        "Beginner run/walk runner; prioritize consistency and injury prevention."
    )
    preferred_sessions_per_week: int = 2
    training_goal: str = "continuous_5k"

    max_hr: int | None = None
    hr_zone_bounds: list[int] | None = None

    walk_speed_threshold_mps: float = 1.8
    min_walk_break_sec: int = 15
    long_run_weekly_percentile: float = 0.8
    fast_pace_improvement_percent: float = 10.0
    sudden_volume_ratio: float = 1.3
    sudden_volume_min_increase_km: float = 5.0
    max_hard_sessions_7d: int = 2

    @field_validator("hr_zone_bounds", mode="before")
    @classmethod
    def parse_hr_zones(cls, value: object) -> object:
        if value in (None, ""):
            return None
        if isinstance(value, str):
            return [int(part.strip()) for part in value.split(",") if part.strip()]
        return value

    @field_validator("strava_export_distance_unit")
    @classmethod
    def validate_export_distance_unit(cls, value: str) -> str:
        normalized = value.lower().strip()
        if normalized not in {"km", "mi"}:
            raise ValueError("STRAVA_EXPORT_DISTANCE_UNIT must be km or mi")
        return normalized

    @field_validator("ai_provider")
    @classmethod
    def validate_ai_provider(cls, value: str) -> str:
        normalized = value.lower().strip()
        if normalized not in {"none", "gemini"}:
            raise ValueError("AI_PROVIDER must be gemini or none")
        return normalized

    @field_validator("preferred_sessions_per_week")
    @classmethod
    def validate_sessions_per_week(cls, value: int) -> int:
        if value not in {1, 2, 3}:
            raise ValueError("PREFERRED_SESSIONS_PER_WEEK must be 1, 2, or 3")
        return value

    @field_validator("training_goal")
    @classmethod
    def validate_training_goal(cls, value: str) -> str:
        normalized = value.lower().strip()
        allowed = {"continuous_5k", "fewer_walk_breaks", "comfortable_7k", "consistency"}
        if normalized not in allowed:
            raise ValueError(f"TRAINING_GOAL must be one of {sorted(allowed)}")
        return normalized

    def require_strava_credentials(self) -> None:
        if not self.strava_client_id or not self.strava_client_secret:
            raise RuntimeError(
                "STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET must be set in .env"
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()
