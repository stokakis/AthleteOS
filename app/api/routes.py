"""
API routes for AthleteOS web app.
"""
import json
import os
from datetime import date, datetime, timedelta
from typing import Optional
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Query
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel

from app.services import file_service as fs
from app.services import strava_service as strava
from app.services import intervals_service as intervals
from app.services import ai_service as ai

router = APIRouter(prefix="/api")


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@router.get("/health")
def health():
    return {"status": "ok", "date": str(date.today())}


# ---------------------------------------------------------------------------
# Setup wizard state
# ---------------------------------------------------------------------------

@router.get("/setup/state")
def setup_state():
    return fs.get_setup_state()


@router.post("/setup/save-env")
async def save_env(request: Request):
    body = await request.json()
    allowed = {"ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "ANTHROPIC_MODEL",
               "HEVY_API_KEY", "INTERVALS_ATHLETE_ID", "INTERVALS_API_KEY",
               "STRAVA_CLIENT_ID", "STRAVA_CLIENT_SECRET", "STRAVA_REFRESH_TOKEN"}
    for key, value in body.items():
        if key in allowed or key.startswith("STRAVA_"):
            fs.save_env_var(key, value)
            os.environ[key] = value
    return {"ok": True}


@router.get("/setup/strava-auth-url")
def strava_auth_url(request: Request, client_id: str = Query(...)):
    base_url = str(request.base_url).rstrip("/")
    redirect_uri = f"{base_url}/api/setup/strava-callback"
    url = strava.build_strava_auth_url(client_id, redirect_uri)
    return {"url": url, "redirect_uri": redirect_uri}


@router.get("/setup/strava-callback")
async def strava_callback(request: Request, code: str = Query(None), error: str = Query(None)):
    """Strava redirects here after OAuth. Exchanges code and closes popup."""
    if error:
        html = f"""<html><body style="background:#0f0f0f;color:#ef4444;font-family:sans-serif;padding:2rem">
        <h2>Authorization failed: {error}</h2>
        <script>window.opener && window.opener.postMessage({{type:'strava_error',error:'{error}'}},'*');window.close();</script>
        </body></html>"""
        return HTMLResponse(html)

    if not code:
        raise HTTPException(400, "Missing code parameter")

    # We need client_id and client_secret — read from env (already saved by /setup/save-env)
    client_id     = os.getenv("STRAVA_CLIENT_ID", "")
    client_secret = os.getenv("STRAVA_CLIENT_SECRET", "")

    if not client_id or not client_secret:
        raise HTTPException(400, "STRAVA_CLIENT_ID / STRAVA_CLIENT_SECRET not configured")

    try:
        result = strava.exchange_strava_code(client_id, client_secret, code)
    except Exception as e:
        html = f"""<html><body style="background:#0f0f0f;color:#ef4444;font-family:sans-serif;padding:2rem">
        <h2>Error: {e}</h2>
        <script>window.opener && window.opener.postMessage({{type:'strava_error',error:'{e}'}},'*');window.close();</script>
        </body></html>"""
        from fastapi.responses import HTMLResponse
        return HTMLResponse(html)

    athlete = result.get("athlete_name", "")
    html = f"""<html><body style="background:#0f0f0f;color:#22c55e;font-family:sans-serif;padding:2rem;text-align:center">
    <h2>✓ Connected as {athlete}</h2>
    <p>You can close this window.</p>
    <script>window.opener && window.opener.postMessage({{type:'strava_ok',athlete:'{athlete}'}},'*');setTimeout(()=>window.close(),2000);</script>
    </body></html>"""
    from fastapi.responses import HTMLResponse
    return HTMLResponse(html)


@router.post("/setup/test-strava")
def test_strava():
    return strava.test_strava_connection()


# ---------------------------------------------------------------------------
# Intervals.icu
# ---------------------------------------------------------------------------

@router.post("/setup/save-intervals")
async def save_intervals(request: Request):
    body = await request.json()
    athlete_id = body.get("athlete_id", "").strip()
    api_key    = body.get("api_key", "").strip()
    if not athlete_id or not api_key:
        raise HTTPException(400, "athlete_id and api_key are required")
    intervals.save_credentials(athlete_id, api_key)
    return {"ok": True}


@router.post("/setup/test-intervals")
def test_intervals():
    return intervals.test_connection()


@router.post("/intervals/sync")
async def sync_intervals(request: Request):
    body = await request.json()
    after  = body.get("after")
    before = body.get("before")
    if not after:
        from app.services import file_service as fs2
        sync = fs2.get_strava_sync()
        last = sync.get("last_sync")
        if last:
            from datetime import datetime, timedelta
            dt = datetime.fromisoformat(last) - timedelta(days=1)
            after = dt.strftime("%Y-%m-%d")
        else:
            from datetime import date, timedelta
            after = (date.today() - timedelta(days=30)).strftime("%Y-%m-%d")
    try:
        activities = intervals.fetch_activities(after, before)
        return {"ok": True, "count": len(activities), "activities": activities}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/intervals/wellness")
def get_wellness(date: str = Query(None)):
    return intervals.get_wellness(date)


@router.post("/setup/save-profile")
async def save_profile(request: Request):
    body = await request.json()
    content = body.get("content", "")
    if not content:
        raise HTTPException(400, "Profile content is empty")
    fs.save_profile(content)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------

@router.get("/profile")
def get_profile():
    content = fs.get_profile()
    if not content:
        return {"exists": False, "content": None}
    return {"exists": True, "content": content}


# ---------------------------------------------------------------------------
# Dashboard / stats
# ---------------------------------------------------------------------------

@router.get("/dashboard")
def dashboard():
    state = fs.get_setup_state()
    pending = fs.list_pending_workouts()
    recent_completed = fs.list_completed_workouts()[:5]
    recent_journals = fs.list_journal_entries()[:5]
    latest_reflection = fs.get_latest_reflection()
    sync_state = fs.get_strava_sync()

    return {
        "setup": state,
        "pending_count": len(pending),
        "pending_next": pending[0] if pending else None,
        "recent_completed": recent_completed,
        "recent_journals": recent_journals,
        "latest_reflection": latest_reflection,
        "last_strava_sync": sync_state.get("last_sync"),
    }


# ---------------------------------------------------------------------------
# Workouts
# ---------------------------------------------------------------------------

@router.get("/workouts/pending")
def get_pending():
    return fs.list_pending_workouts()


@router.get("/workouts/completed")
def get_completed():
    return fs.list_completed_workouts()


@router.get("/workouts/file")
def get_workout_file(path: str = Query(...)):
    content = fs.get_workout_file(path)
    if content is None:
        raise HTTPException(404, "File not found")
    return {"content": content}


# ---------------------------------------------------------------------------
# Reflections
# ---------------------------------------------------------------------------

@router.get("/reflections")
def get_reflections():
    return fs.list_reflections()


# ---------------------------------------------------------------------------
# Journal
# ---------------------------------------------------------------------------

@router.get("/journal")
def get_journal():
    return fs.list_journal_entries()


class JournalEntry(BaseModel):
    date: Optional[str] = None
    context: str = "general"
    energy: int = 3
    fatigue: int = 3
    mood: int = 3
    stress: int = 2
    sleep_hours: float = 7.0
    soreness: Optional[str] = None
    notes: Optional[str] = None


@router.post("/journal")
def save_journal(entry: JournalEntry):
    if not entry.date:
        entry.date = str(date.today())
    path = fs.save_journal_entry(entry.dict())
    return {"ok": True, "path": path}


# ---------------------------------------------------------------------------
# Strava sync
# ---------------------------------------------------------------------------

@router.post("/strava/sync")
async def sync_strava(request: Request):
    body = await request.json()
    after = body.get("after")
    if not after:
        sync = fs.get_strava_sync()
        last = sync.get("last_sync")
        if last:
            # 1 day before last sync to avoid gaps
            dt = datetime.fromisoformat(last) - timedelta(days=1)
            after = dt.strftime("%Y-%m-%d")
        else:
            after = (date.today() - timedelta(days=30)).strftime("%Y-%m-%d")
    try:
        activities = strava.fetch_activities(after)
        return {"ok": True, "count": len(activities), "activities": activities}
    except Exception as e:
        raise HTTPException(500, str(e))


# ---------------------------------------------------------------------------
# AI Coach chat + commands
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    messages: list[dict]


class CommandRequest(BaseModel):
    command: str
    args: str = ""


@router.post("/ai/chat")
def ai_chat(req: ChatRequest):
    try:
        response = ai.chat(req.messages)
        return {"response": response}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/ai/chat/stream")
async def ai_chat_stream(req: ChatRequest):
    """SSE streaming endpoint."""
    def generate():
        try:
            for chunk in ai.stream_chat(req.messages):
                yield f"data: {json.dumps({'text': chunk})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.post("/ai/command")
def ai_command(req: CommandRequest):
    try:
        result = ai.run_command(req.command, req.args)
        return {"response": result}
    except Exception as e:
        raise HTTPException(500, str(e))