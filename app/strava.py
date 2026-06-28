from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

import httpx

from app.config import Settings
from app.db import Database


API_BASE = "https://www.strava.com/api/v3"
AUTHORIZE_URL = "https://www.strava.com/oauth/authorize"
TOKEN_URL = "https://www.strava.com/oauth/token"
STREAM_KEYS = (
    "time,distance,velocity_smooth,heartrate,cadence,"
    "altitude,moving,grade_smooth,latlng"
)


class StravaError(RuntimeError):
    pass


class StravaClient:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.settings = settings
        self.database = database
        self.client = httpx.AsyncClient(timeout=30, transport=transport)

    async def close(self) -> None:
        await self.client.aclose()

    def authorization_url(self, state: str) -> str:
        self.settings.require_strava_credentials()
        return f"{AUTHORIZE_URL}?{urlencode({
            'client_id': self.settings.strava_client_id,
            'redirect_uri': self.settings.strava_redirect_uri,
            'response_type': 'code',
            'approval_prompt': 'auto',
            'scope': 'activity:read_all',
            'state': state,
        })}"

    async def exchange_code(self, code: str, granted_scope: str) -> dict[str, Any]:
        if "activity:read_all" not in {
            item.strip() for item in granted_scope.split(",")
        }:
            raise StravaError("Required activity:read_all permission was not granted")
        payload = await self._token_request(
            {
                "client_id": self.settings.strava_client_id,
                "client_secret": self.settings.strava_client_secret,
                "code": code,
                "grant_type": "authorization_code",
            }
        )
        self.database.save_token(payload, granted_scope)
        return payload

    async def access_token(self) -> str:
        token = self.database.get_token()
        if not token:
            raise StravaError("Not connected to Strava. Visit /auth/login first.")
        if int(token["expires_at"]) > int(time.time()) + 3600:
            return str(token["access_token"])
        payload = await self._token_request(
            {
                "client_id": self.settings.strava_client_id,
                "client_secret": self.settings.strava_client_secret,
                "grant_type": "refresh_token",
                "refresh_token": token["refresh_token"],
            }
        )
        self.database.save_token(payload, token["scope"])
        return str(payload["access_token"])

    async def _token_request(self, data: dict[str, Any]) -> dict[str, Any]:
        self.settings.require_strava_credentials()
        response = await self.client.post(TOKEN_URL, data=data)
        if response.is_error:
            raise StravaError(f"Strava token request failed: {response.status_code}")
        return response.json()

    async def _get(
        self, path: str, params: dict[str, Any] | None = None
    ) -> Any:
        token = await self.access_token()
        response = await self.client.get(
            f"{API_BASE}{path}",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
        )
        if response.is_error:
            detail = response.text[:300]
            raise StravaError(
                f"Strava API request {path} failed ({response.status_code}): {detail}"
            )
        return response.json()

    async def list_activities(
        self, after: datetime, before: datetime
    ) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        page = 1
        while True:
            batch = await self._get(
                "/athlete/activities",
                {
                    "after": int(after.replace(tzinfo=timezone.utc).timestamp()),
                    "before": int(before.astimezone(timezone.utc).timestamp()),
                    "page": page,
                    "per_page": 100,
                },
            )
            output.extend(batch)
            if len(batch) < 100:
                return output
            page += 1

    async def activity_detail(self, activity_id: int) -> dict[str, Any]:
        return await self._get(f"/activities/{activity_id}")

    async def activity_streams(self, activity_id: int) -> dict[str, Any] | None:
        try:
            return await self._get(
                f"/activities/{activity_id}/streams",
                {"keys": STREAM_KEYS, "key_by_type": "true"},
            )
        except StravaError:
            return None

    async def activity_laps(
        self, activity_id: int
    ) -> list[dict[str, Any]] | None:
        try:
            return await self._get(f"/activities/{activity_id}/laps")
        except StravaError:
            return None

