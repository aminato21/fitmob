from __future__ import annotations

import secrets
import shutil
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.ai import run_ai_analysis
from app.config import get_settings
from app.db import Database
from app.offline_import import import_strava_zip
from app.strava import StravaClient, StravaError
from app.sync import export_from_database, sync_activities
from app.web import router as web_router


settings = get_settings()
database = Database(settings)


@asynccontextmanager
async def lifespan(_: FastAPI):
    app.state.settings = settings
    app.state.database = database
    database.initialize()
    yield


app = FastAPI(
    title="Strava 2026 Beginner Running Analysis",
    version="0.1.0",
    lifespan=lifespan,
)
app.mount(
    "/static",
    StaticFiles(directory=Path(__file__).parent / "static"),
    name="static",
)
static_dir = Path(__file__).parent / "static"


@app.get("/")
def home() -> RedirectResponse:
    return RedirectResponse("/dashboard")


@app.get("/manifest.json", include_in_schema=False)
def manifest() -> FileResponse:
    return FileResponse(
        static_dir / "manifest.json",
        media_type="application/manifest+json",
    )


@app.get("/sw.js", include_in_schema=False)
def service_worker() -> FileResponse:
    return FileResponse(
        static_dir / "sw.js",
        media_type="application/javascript",
        headers={
            "Service-Worker-Allowed": "/",
            "Cache-Control": "no-cache",
        },
    )


@app.get("/offline", include_in_schema=False)
def offline_page() -> FileResponse:
    return FileResponse(static_dir / "offline.html", media_type="text/html")


@app.get("/api/status")
def status() -> dict[str, object]:
    token = database.get_token()
    return {
        "app": "Strava 2026 Beginner Running Analysis",
        "connected_to_strava": token is not None,
        "next_step": (
            "POST /sync"
            if token
            else "Import a Strava ZIP at POST /import/zip, or configure API OAuth"
        ),
        "docs": "/docs",
    }


@app.get("/auth/login")
async def auth_login() -> RedirectResponse:
    state = secrets.token_urlsafe(32)
    database.save_state(state, int(time.time()) + 600)
    client = StravaClient(settings, database)
    try:
        return RedirectResponse(client.authorization_url(state))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    finally:
        await client.close()


@app.get("/auth/callback")
async def auth_callback(
    code: str | None = None,
    scope: str = "",
    state: str = "",
    error: str | None = None,
) -> RedirectResponse:
    if error:
        raise HTTPException(status_code=400, detail=f"Strava authorization: {error}")
    if not code or not state or not database.consume_state(state):
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth callback")
    client = StravaClient(settings, database)
    try:
        await client.exchange_code(code, scope)
    except (StravaError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        await client.close()
    return RedirectResponse("/?connected=1")


@app.post("/sync")
async def sync(
    refresh_existing: bool = Query(
        False,
        description="Re-download activities already stored (uses more API requests)",
    )
) -> dict[str, object]:
    try:
        return await sync_activities(
            settings, database, refresh_existing=refresh_existing
        )
    except (StravaError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/export")
def export() -> dict[str, object]:
    return export_from_database(settings, database)


@app.post("/analysis")
async def analysis() -> dict[str, object]:
    # Provider errors are converted to deterministic results inside the service.
    return await run_ai_analysis(settings, database)


@app.post("/import/zip")
def import_zip(
    archive: UploadFile = File(
        ..., description="ZIP downloaded from Strava's account export"
    ),
) -> dict[str, object]:
    if not (archive.filename or "").lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Please upload a .zip file")
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as temp:
            shutil.copyfileobj(archive.file, temp)
            temp_path = Path(temp.name)
        return import_strava_zip(temp_path, settings, database)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        archive.file.close()
        if temp_path:
            temp_path.unlink(missing_ok=True)


app.include_router(web_router)
