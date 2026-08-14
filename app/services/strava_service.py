"""
Strava service — wraps the existing scripts/strava_client.py logic
but uses config.py for env vars and supports Railway deployment.
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import requests
from dotenv import set_key

from app.config import (
    STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET, STRAVA_REFRESH_TOKEN,
    OVERVIEW_DIR
)

STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"
STRAVA_BASE_URL  = "https://www.strava.com/api/v3"
ENV_PATH = Path(".env")


def _get_access_token() -> Optional[str]:
    """Exchange refresh token for access token."""
    client_id     = os.getenv("STRAVA_CLIENT_ID", STRAVA_CLIENT_ID)
    client_secret = os.getenv("STRAVA_CLIENT_SECRET", STRAVA_CLIENT_SECRET)
    refresh_token = os.getenv("STRAVA_REFRESH_TOKEN", STRAVA_REFRESH_TOKEN)

    if not all([client_id, client_secret, refresh_token]):
        return None

    resp = requests.post(STRAVA_TOKEN_URL, data={
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    new_refresh = data.get("refresh_token")
    if new_refresh and new_refresh != refresh_token:
        # Persist rotated token
        set_key(str(ENV_PATH), "STRAVA_REFRESH_TOKEN", new_refresh)
        os.environ["STRAVA_REFRESH_TOKEN"] = new_refresh

    return data.get("access_token")


def test_strava_connection() -> dict:
    """Return status dict for the setup wizard."""
    client_id     = os.getenv("STRAVA_CLIENT_ID", STRAVA_CLIENT_ID)
    client_secret = os.getenv("STRAVA_CLIENT_SECRET", STRAVA_CLIENT_SECRET)
    refresh_token = os.getenv("STRAVA_REFRESH_TOKEN", STRAVA_REFRESH_TOKEN)

    missing = []
    if not client_id:     missing.append("STRAVA_CLIENT_ID")
    if not client_secret: missing.append("STRAVA_CLIENT_SECRET")
    if not refresh_token: missing.append("STRAVA_REFRESH_TOKEN")

    if missing:
        return {"ok": False, "error": f"Missing: {', '.join(missing)}"}

    try:
        token = _get_access_token()
        if not token:
            return {"ok": False, "error": "Could not get access token"}

        resp = requests.get(
            f"{STRAVA_BASE_URL}/athlete",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        resp.raise_for_status()
        athlete = resp.json()
        name = f"{athlete.get('firstname','')} {athlete.get('lastname','')}".strip()
        return {"ok": True, "athlete_name": name}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def fetch_activities(after_date: Optional[str] = None) -> list[dict]:
    """
    Fetch activities from Strava. after_date in YYYY-MM-DD format.
    Falls back to 30 days ago if not provided.
    """
    if not after_date:
        after_dt = datetime.now(timezone.utc) - timedelta(days=30)
        after_ts = int(after_dt.timestamp())
    else:
        dt = datetime.strptime(after_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        after_ts = int(dt.timestamp())

    token = _get_access_token()
    if not token:
        raise RuntimeError("Strava not connected. Configure credentials in setup.")

    activities = []
    page = 1
    while True:
        resp = requests.get(
            f"{STRAVA_BASE_URL}/athlete/activities",
            headers={"Authorization": f"Bearer {token}"},
            params={"after": after_ts, "per_page": 100, "page": page},
            timeout=20,
        )
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        activities.extend(batch)
        page += 1

    return [_normalize(a) for a in activities]


def _normalize(a: dict) -> dict:
    return {
        "id": a.get("id"),
        "name": a.get("name"),
        "sport_type": a.get("sport_type") or a.get("type"),
        "start_date_local": a.get("start_date_local"),
        "moving_time_seconds": a.get("moving_time"),
        "distance_meters": a.get("distance"),
        "average_heartrate": a.get("average_heartrate"),
        "average_watts": a.get("average_watts"),
        "weighted_average_watts": a.get("weighted_average_watts"),
        "average_speed_mps": a.get("average_speed"),
        "kilojoules": a.get("kilojoules"),
        "device_watts": a.get("device_watts"),
        "total_elevation_gain": a.get("total_elevation_gain"),
    }


def build_strava_auth_url(client_id: str, redirect_uri: str) -> str:
    return (
        f"https://www.strava.com/oauth/authorize"
        f"?client_id={client_id}"
        f"&redirect_uri={redirect_uri}"
        f"&response_type=code"
        f"&scope=activity:read_all"
        f"&approval_prompt=auto"
    )


def exchange_strava_code(client_id: str, client_secret: str, code: str) -> dict:
    """Exchange OAuth code for tokens. Returns athlete info + saves tokens."""
    resp = requests.post(STRAVA_TOKEN_URL, data={
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
    }, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    # Persist to .env
    set_key(str(ENV_PATH), "STRAVA_CLIENT_ID", client_id)
    set_key(str(ENV_PATH), "STRAVA_CLIENT_SECRET", client_secret)
    set_key(str(ENV_PATH), "STRAVA_REFRESH_TOKEN", data["refresh_token"])

    # Also set in process env so it works immediately
    os.environ["STRAVA_CLIENT_ID"]     = client_id
    os.environ["STRAVA_CLIENT_SECRET"] = client_secret
    os.environ["STRAVA_REFRESH_TOKEN"] = data["refresh_token"]

    athlete = data.get("athlete", {})
    return {
        "ok": True,
        "athlete_name": f"{athlete.get('firstname','')} {athlete.get('lastname','')}".strip(),
    }