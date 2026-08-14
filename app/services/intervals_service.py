"""
Intervals.icu API client for AthleteOS.
Replaces the Strava client — uses Basic Auth (API_KEY / api_key).
Docs: https://intervals.icu/api/v1/docs
"""
import os
from datetime import datetime, timezone, timedelta, date
from typing import Optional

import requests
from dotenv import set_key
from pathlib import Path

from app.config import OVERVIEW_DIR
from app.services import file_service as fs

INTERVALS_BASE = "https://intervals.icu/api/v1"
ENV_PATH = Path(".env")


def _creds() -> tuple[str, str]:
    """Return (athlete_id, api_key) from env."""
    athlete_id = os.getenv("INTERVALS_ATHLETE_ID", "")
    api_key    = os.getenv("INTERVALS_API_KEY", "")
    return athlete_id, api_key


def _auth():
    """HTTP Basic auth tuple for requests."""
    _, api_key = _creds()
    return ("API_KEY", api_key)


def test_connection() -> dict:
    athlete_id, api_key = _creds()
    if not athlete_id or not api_key:
        return {"ok": False, "error": "Missing INTERVALS_ATHLETE_ID or INTERVALS_API_KEY"}
    try:
        resp = requests.get(
            f"{INTERVALS_BASE}/athlete/{athlete_id}",
            auth=("API_KEY", api_key),
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        name = f"{data.get('firstname','')} {data.get('lastname','')}".strip() or data.get('name', 'Athlete')
        return {"ok": True, "athlete_name": name}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def save_credentials(athlete_id: str, api_key: str) -> None:
    set_key(str(ENV_PATH), "INTERVALS_ATHLETE_ID", athlete_id)
    set_key(str(ENV_PATH), "INTERVALS_API_KEY", api_key)
    os.environ["INTERVALS_ATHLETE_ID"] = athlete_id
    os.environ["INTERVALS_API_KEY"]    = api_key


def fetch_activities(after_date: Optional[str] = None, before_date: Optional[str] = None) -> list[dict]:
    """
    Fetch activities from Intervals.icu.
    after_date / before_date: YYYY-MM-DD strings.
    """
    athlete_id, api_key = _creds()
    if not athlete_id or not api_key:
        raise RuntimeError("Intervals.icu not connected. Configure in Setup.")

    if not after_date:
        after_date = (date.today() - timedelta(days=30)).strftime("%Y-%m-%d")
    if not before_date:
        before_date = (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")

    params = {
        "oldest": after_date,
        "newest": before_date,
        "fields": (
            "id,name,type,start_date_local,moving_time,elapsed_time,"
            "distance,total_elevation_gain,average_heartrate,max_heartrate,"
            "average_watts,weighted_average_watts,average_speed,kilojoules,"
            "device_watts,description,icu_training_load,icu_atl,icu_ctl"
        ),
    }

    resp = requests.get(
        f"{INTERVALS_BASE}/athlete/{athlete_id}/activities",
        auth=("API_KEY", api_key),
        params=params,
        timeout=20,
    )
    resp.raise_for_status()
    raw = resp.json()
    return [_normalize(a) for a in raw]


def _normalize(a: dict) -> dict:
    """Normalize Intervals.icu activity to AthleteOS standard format."""
    # Intervals.icu uses moving_time in seconds directly
    return {
        "id": a.get("id"),
        "name": a.get("name"),
        "sport_type": _map_type(a.get("type", "")),
        "start_date_local": a.get("start_date_local"),
        "moving_time_seconds": a.get("moving_time"),
        "elapsed_time_seconds": a.get("elapsed_time"),
        "distance_meters": a.get("distance"),
        "total_elevation_gain": a.get("total_elevation_gain"),
        "average_heartrate": a.get("average_heartrate"),
        "max_heartrate": a.get("max_heartrate"),
        "average_watts": a.get("average_watts"),
        "weighted_average_watts": a.get("weighted_average_watts"),
        "average_speed_mps": a.get("average_speed"),
        "kilojoules": a.get("kilojoules"),
        "device_watts": a.get("device_watts"),
        "description": a.get("description"),
        "training_load": a.get("icu_training_load"),
        "atl": a.get("icu_atl"),   # acute training load (fatigue)
        "ctl": a.get("icu_ctl"),   # chronic training load (fitness)
        "efficiency_factor": (
            round(a["weighted_average_watts"] / a["average_heartrate"], 3)
            if a.get("device_watts") and a.get("weighted_average_watts") and a.get("average_heartrate")
            else None
        ),
        "source": "intervals.icu",
    }


def _map_type(itype: str) -> str:
    """Map Intervals.icu activity types to AthleteOS sport_type."""
    mapping = {
        "Ride": "Ride",
        "VirtualRide": "VirtualRide",
        "Run": "Run",
        "VirtualRun": "Run",
        "Swim": "Swim",
        "WeightTraining": "WeightTraining",
        "Workout": "WeightTraining",
        "Walk": "Walk",
        "Hike": "Hike",
    }
    return mapping.get(itype, itype)


def get_athlete_profile() -> dict:
    """Fetch athlete profile from Intervals.icu (FTP, weight, HR zones)."""
    athlete_id, api_key = _creds()
    if not athlete_id or not api_key:
        return {}
    try:
        resp = requests.get(
            f"{INTERVALS_BASE}/athlete/{athlete_id}",
            auth=("API_KEY", api_key),
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return {}


def get_wellness(date_str: Optional[str] = None) -> dict:
    """Fetch wellness data (HRV, sleep, weight) for a given date."""
    athlete_id, api_key = _creds()
    if not athlete_id or not api_key:
        return {}
    if not date_str:
        date_str = date.today().strftime("%Y-%m-%d")
    try:
        resp = requests.get(
            f"{INTERVALS_BASE}/athlete/{athlete_id}/wellness/{date_str}",
            auth=("API_KEY", api_key),
            timeout=10,
        )
        if resp.status_code == 404:
            return {}
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return {}