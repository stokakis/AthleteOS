"""
File service: read/write athlete data files (profile, workouts, journals, etc.)
All paths resolved relative to DATA_DIR from config.
"""
import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import yaml

from app.config import (
    ATHLETE_DIR, WORKOUTS_DIR, JOURNALS_DIR, OVERVIEW_DIR, DATA_FILES
)


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------

def read_file(path: Path) -> Optional[str]:
    """Return file text or None if missing."""
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def parse_frontmatter(text: str) -> dict:
    """Extract YAML frontmatter from a markdown file."""
    match = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
    if not match:
        return {}
    try:
        return yaml.safe_load(match.group(1)) or {}
    except Exception:
        return {}


def update_frontmatter_field(text: str, key: str, value) -> str:
    """Replace a single frontmatter field value."""
    pattern = rf"^({re.escape(key)}:\s*).*$"
    replacement = rf"\g<1>{value}"
    return re.sub(pattern, replacement, text, flags=re.MULTILINE)


# ---------------------------------------------------------------------------
# Athlete profile
# ---------------------------------------------------------------------------

def get_profile() -> Optional[str]:
    return read_file(ATHLETE_DIR / "profile.md")


def save_profile(content: str) -> None:
    write_file(ATHLETE_DIR / "profile.md", content)


def profile_exists() -> bool:
    return (ATHLETE_DIR / "profile.md").exists()


# ---------------------------------------------------------------------------
# Setup state
# ---------------------------------------------------------------------------

def get_setup_state() -> dict:
    """Return a dict summarising what's configured."""
    profile = get_profile()
    env_path = Path(".env")
    env_text = read_file(env_path) or ""

    def env_has(key):
        return bool(re.search(rf"^{key}=.+", env_text, re.MULTILINE))

    # Read Intervals athlete ID for pre-fill
    intervals_id = ""
    m = re.search(r"^INTERVALS_ATHLETE_ID=(.+)", env_text, re.MULTILINE)
    if m:
        intervals_id = m.group(1).strip()

    return {
        "profile_exists": profile is not None,
        "profile_has_placeholders": bool(re.search(r"\[.+\]", profile or "")),
        "strava_connected": all(
            env_has(k) for k in
            ["STRAVA_CLIENT_ID", "STRAVA_CLIENT_SECRET", "STRAVA_REFRESH_TOKEN"]
        ),
        "intervals_connected": env_has("INTERVALS_ATHLETE_ID") and env_has("INTERVALS_API_KEY"),
        "intervals_athlete_id": intervals_id,
        "hevy_connected": env_has("HEVY_API_KEY"),
        "anthropic_connected": env_has("ANTHROPIC_API_KEY"),
    }


def save_env_var(key: str, value: str) -> None:
    """Write or update a single key in .env."""
    env_path = Path(".env")
    text = read_file(env_path) or ""
    pattern = rf"^{re.escape(key)}=.*$"
    new_line = f"{key}={value}"
    if re.search(pattern, text, re.MULTILINE):
        text = re.sub(pattern, new_line, text, flags=re.MULTILINE)
    else:
        text = text.rstrip("\n") + f"\n{new_line}\n"
    write_file(env_path, text)


# ---------------------------------------------------------------------------
# Workouts
# ---------------------------------------------------------------------------

def list_pending_workouts() -> list[dict]:
    """Return list of pending workout metadata dicts sorted by date."""
    results = []
    plans_dir = WORKOUTS_DIR / "plans"
    if not plans_dir.exists():
        return results
    for md_file in sorted(plans_dir.rglob("*.md")):
        text = read_file(md_file)
        if not text:
            continue
        fm = parse_frontmatter(text)
        if fm.get("status") == "pending":
            results.append({
                "file": str(md_file.relative_to(WORKOUTS_DIR.parent)),
                "date": str(fm.get("date", "")),
                "type": fm.get("type", ""),
                "key_focus": fm.get("key_focus", ""),
                "planned_duration_min": fm.get("planned_duration_min"),
                "status": "pending",
            })
    return sorted(results, key=lambda x: x["date"])


def get_workout_file(relative_path: str) -> Optional[str]:
    base = WORKOUTS_DIR.parent
    full = base / relative_path
    return read_file(full)


def list_completed_workouts() -> list[dict]:
    results = []
    completed_dir = WORKOUTS_DIR / "completed"
    if not completed_dir.exists():
        return results
    for md_file in sorted(completed_dir.rglob("*.md"), reverse=True):
        text = read_file(md_file)
        if not text:
            continue
        fm = parse_frontmatter(text)
        if fm.get("status") == "completed":
            results.append({
                "file": str(md_file.relative_to(WORKOUTS_DIR.parent)),
                "date": str(fm.get("date", "")),
                "type": fm.get("type", ""),
                "key_focus": fm.get("key_focus", ""),
                "planned_duration_min": fm.get("planned_duration_min"),
                "status": "completed",
            })
    return results[:20]


# ---------------------------------------------------------------------------
# Reflections
# ---------------------------------------------------------------------------

def list_reflections() -> list[dict]:
    results = []
    ref_dir = WORKOUTS_DIR / "reflections"
    if not ref_dir.exists():
        return results
    for f in sorted(ref_dir.glob("*.md"), reverse=True):
        text = read_file(f)
        results.append({"week": f.stem, "content": text or "", "file": f.name})
    return results[:8]


def get_latest_reflection() -> Optional[str]:
    ref_dir = WORKOUTS_DIR / "reflections"
    if not ref_dir.exists():
        return None
    files = sorted(ref_dir.glob("*.md"), reverse=True)
    return read_file(files[0]) if files else None


# ---------------------------------------------------------------------------
# Journals
# ---------------------------------------------------------------------------

def list_journal_entries() -> list[dict]:
    results = []
    if not JOURNALS_DIR.exists():
        return results
    for f in sorted(JOURNALS_DIR.rglob("*.md"), reverse=True):
        text = read_file(f)
        if not text:
            continue
        fm = parse_frontmatter(text)
        results.append({
            "file": f.name,
            "date": str(fm.get("date", "")),
            "energy": fm.get("energy"),
            "fatigue": fm.get("fatigue"),
            "mood": fm.get("mood"),
            "stress": fm.get("stress"),
            "sleep_hours": fm.get("sleep_hours"),
            "soreness": fm.get("soreness"),
            "context": fm.get("context", "general"),
        })
    return results[:20]


def save_journal_entry(entry: dict) -> str:
    """Save a journal entry and return the file path."""
    entry_date = entry.get("date", str(date.today()))
    dt = datetime.strptime(entry_date, "%Y-%m-%d")
    iso_week = dt.isocalendar()
    week_folder = f"{iso_week[0]}-W{iso_week[1]:02d}"

    folder = JOURNALS_DIR / week_folder
    folder.mkdir(parents=True, exist_ok=True)
    filename = f"{entry_date}-journal.md"
    path = folder / filename

    content = f"""---
date: {entry_date}
context: {entry.get('context', 'general')}
energy: {entry.get('energy', 3)}
fatigue: {entry.get('fatigue', 3)}
mood: {entry.get('mood', 3)}
stress: {entry.get('stress', 2)}
sleep_hours: {entry.get('sleep_hours', 7.0)}
soreness: {entry.get('soreness') or 'null'}
adjustment_triggered: false
---

# Journal — {entry_date}

{entry.get('notes', '')}
"""
    write_file(path, content)
    return str(path)


# ---------------------------------------------------------------------------
# Consistency log
# ---------------------------------------------------------------------------

def get_consistency_log() -> Optional[str]:
    return read_file(ATHLETE_DIR / "consistency-log.md")


# ---------------------------------------------------------------------------
# Strava sync state
# ---------------------------------------------------------------------------

def get_strava_sync() -> dict:
    text = read_file(OVERVIEW_DIR / "strava-sync.json")
    if not text:
        return {"last_sync": None, "seen_ids": []}
    try:
        return json.loads(text)
    except Exception:
        return {"last_sync": None, "seen_ids": []}


def save_strava_sync(data: dict) -> None:
    write_file(OVERVIEW_DIR / "strava-sync.json", json.dumps(data, indent=2))


# ---------------------------------------------------------------------------
# Pending overview
# ---------------------------------------------------------------------------

def regenerate_pending_md() -> None:
    pending = list_pending_workouts()
    lines = [
        "# Pending Workouts",
        "",
        f"_Last updated: {date.today()}_",
        "",
        "| Date | Day | Type | Duration | Focus | Status |",
        "|------|-----|------|----------|-------|--------|",
    ]
    for w in pending:
        try:
            d = datetime.strptime(w["date"], "%Y-%m-%d")
            day = d.strftime("%a")
        except Exception:
            day = ""
        dur = f"{w['planned_duration_min']} min" if w.get("planned_duration_min") else "—"
        lines.append(f"| {w['date']} | {day} | {w['type']} | {dur} | {w['key_focus']} | pending |")

    write_file(OVERVIEW_DIR / "pending.md", "\n".join(lines))