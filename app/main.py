from __future__ import annotations

import secrets
import shutil
import tempfile
import time
from contextlib import asynccontextmanager, closing
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.auth import session_cookie_username
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
    from app.workflow import Workflow
    Workflow(database).initialize()
    yield


app = FastAPI(
    title="Strava 2026 Beginner Running Analysis",
    version="0.1.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def private_cache_and_csrf(request: Request, call_next):
    request.state.csrf_token = request.cookies.get("runstead_csrf") or secrets.token_urlsafe(32)
    response = await call_next(request)
    if not request.cookies.get("runstead_csrf"):
        response.set_cookie("runstead_csrf", request.state.csrf_token, httponly=True,
                            samesite="strict", secure=request.url.scheme == "https" or
                            request.headers.get("x-forwarded-proto") == "https")
    if not request.url.path.startswith("/static/") and request.url.path not in {"/manifest.json", "/offline", "/sw.js"}:
        response.headers["Cache-Control"] = "no-store, private"
    return response


@app.middleware("http")
async def require_login(request: Request, call_next):
    active_settings = getattr(request.app.state, "settings", settings)
    if not active_settings.auth_enabled:
        return await call_next(request)
    active_database = getattr(request.app.state, "database", database)
    path = request.url.path
    is_public = (
        path == "/login"
        or path == "/register"
        or path == "/offline"
        or path == "/manifest.json"
        or path == "/sw.js"
        or path.startswith("/static/")
    )
    username = session_cookie_username(
        active_settings, request.cookies.get("runstead_session")
    )
    if is_public or (username and active_database.user_exists(username)):
        return await call_next(request)
    if active_database.user_count() == 0:
        if request.method == "GET":
            return RedirectResponse("/register", status_code=303)
        return JSONResponse(
            {"detail": "Owner account registration is required"},
            status_code=503,
        )
    if request.method == "GET" and "text/html" in request.headers.get(
        "accept", ""
    ):
        next_path = path if path.startswith("/") and not path.startswith("//") else "/dashboard"
        return RedirectResponse(
            f"/login?next={next_path}", status_code=303
        )
    return JSONResponse({"detail": "Authentication required"}, status_code=401)
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


@app.get("/api/backup")
def download_backup(
    request: Request, background_tasks: BackgroundTasks
) -> FileResponse:
    import zipfile
    active_settings = getattr(request.app.state, "settings", settings)
    active_database = getattr(request.app.state, "database", database)

    temp_zip = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
    temp_zip_path = Path(temp_zip.name)
    temp_zip.close()

    try:
        with zipfile.ZipFile(temp_zip_path, "w", zipfile.ZIP_DEFLATED) as zip_file:
            db_path = Path(active_database.path)
            if db_path.exists():
                import sqlite3
                snapshot = temp_zip_path.with_suffix(".sqlite")
                try:
                    with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)) as source:
                        with closing(sqlite3.connect(snapshot)) as target:
                            source.backup(target)
                            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                                raise RuntimeError("Database backup did not pass integrity verification")
                    zip_file.write(snapshot, arcname=db_path.name)
                finally:
                    snapshot.unlink(missing_ok=True)

            exp_dir = Path(active_settings.export_dir)
            if exp_dir.exists() and exp_dir.is_dir():
                for file_path in exp_dir.rglob("*"):
                    if file_path.is_file():
                        arcname = Path("exports") / file_path.relative_to(exp_dir)
                        zip_file.write(file_path, arcname=arcname)
    except Exception as exc:
        temp_zip_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to create backup: {str(exc)}"
        )

    background_tasks.add_task(lambda: temp_zip_path.unlink(missing_ok=True))

    return FileResponse(
        path=temp_zip_path,
        media_type="application/zip",
        filename="runstead_backup.zip",
    )



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
async def analysis(request: Request) -> dict[str, object]:
    # Provider errors are converted to deterministic results inside the service.
    from app.workflow_routes import generate_proposal
    proposal_id = await generate_proposal(request)
    return {'proposal_id': proposal_id, 'review_url': f'/plan/proposals/{proposal_id}',
            'active_plan_changed': False}


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


from pydantic import BaseModel, Field

class DailyHealthRecord(BaseModel):
    date: str  # YYYY-MM-DD
    sleep_duration_sec: float | None = None
    resting_heartrate: float | None = None
    steps: int | None = None
    active_energy_kcal: float | None = None
    oxygen_saturation_percent: float | None = None
    sources: list[str] = Field(default_factory=lambda: ["iOS Shortcut"])


@app.post("/api/health/daily")
def import_daily_health_records(records: list[DailyHealthRecord]) -> dict[str, object]:
    daily_rows = [record.model_dump() for record in records]
    database.upsert_health_daily(daily_rows)
    export_from_database(settings, database)
    return {
        "status": "success",
        "records_imported": len(daily_rows)
    }


app.include_router(web_router)
from app.workflow_routes import router as workflow_router
app.include_router(workflow_router)
