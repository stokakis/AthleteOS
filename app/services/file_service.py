"""
File service: read/write athlete data files (profile, workouts, journals, etc.)
All paths resolved relative to DATA_DIR from config.
"""
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

from app.config import (
    ATHLETE_DIR, WORKOUTS_DIR, JOURNALS_DIR, OVERVIEW_DIR, DATA_FILES, DATA_DIR
)

# .env lives inside DATA_DIR so it survives Railway redeployments
ENV_PATH = DATA_DIR / ".env"


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
    env_text = read_file(ENV_PATH) or ""

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
    """Write or update a single key in persistent .env inside DATA_DIR."""
    text = read_file(ENV_PATH) or ""
    pattern = rf"^{re.escape(key)}=.*$"
    new_line = f"{key}={value}"
    if re.search(pattern, text, re.MULTILINE):
        text = re.sub(pattern, new_line, text, flags=re.MULTILINE)
    else:
        text = text.rstrip("\n") + f"\n{new_line}\n"
    write_file(ENV_PATH, text)
    # Also update os.environ immediately so the running process picks it up
    import os
    os.environ[key] = value


# ---------------------------------------------------------------------------
# Chat sessions
# ---------------------------------------------------------------------------

CHAT_DIR = DATA_DIR / "chats"


def _chat_path(session_id: str) -> Path:
    return CHAT_DIR / f"{session_id}.json"


def save_chat_session(session_id: str, messages: list) -> None:
    """Persist a chat session (messages list) to disk."""
    CHAT_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "session_id": session_id,
        "updated_at": datetime.utcnow().isoformat(),
        "title": _derive_title(messages),
        "messages": messages,
    }
    write_file(_chat_path(session_id), json.dumps(data, ensure_ascii=False, indent=2))


def _derive_title(messages: list) -> str:
    """Use first user message as session title (truncated)."""
    for m in messages:
        if m.get("role") == "user":
            content = m.get("content", "")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        content = block.get("text", "")
                        break
            if content:
                return str(content)[:60]
    return "New conversation"


def get_chat_session(session_id: str) -> Optional[dict]:
    text = read_file(_chat_path(session_id))
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def list_chat_sessions() -> list[dict]:
    """Return all sessions sorted by updated_at descending (newest first)."""
    if not CHAT_DIR.exists():
        return []
    sessions = []
    for f in CHAT_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            sessions.append({
                "session_id": data.get("session_id", f.stem),
                "title": data.get("title", "Conversation"),
                "updated_at": data.get("updated_at", ""),
                "message_count": len(data.get("messages", [])),
            })
        except Exception:
            pass
    sessions.sort(key=lambda x: x["updated_at"], reverse=True)
    return sessions[:50]  # max 50 recent sessions


def delete_chat_session(session_id: str) -> None:
    p = _chat_path(session_id)
    if p.exists():
        p.unlink()


# ---------------------------------------------------------------------------
# Workouts
# ---------------------------------------------------------------------------

def save_workout_file(content: str, relative_path: str) -> Path:
    """Save a workout markdown file to the correct location under DATA_DIR."""
    # relative_path e.g. "workouts/plans/2026-W36/2026-09-01-weights-full-body-a.md"
    full = DATA_DIR / relative_path
    write_file(full, content)
    return full


def derive_workout_path(content: str) -> Optional[str]:
    """Parse frontmatter and derive the canonical file path."""
    fm = parse_frontmatter(content)
    if not fm.get("date") or not fm.get("type"):
        return None
    date_str = str(fm["date"])
    wtype = str(fm.get("type", "workout"))
    focus = str(fm.get("key_focus", "session")).lower()
    week = str(fm.get("week_folder", ""))

    # derive slug from key_focus
    import re as _re
    slug = _re.sub(r"[^a-z0-9]+", "-", focus).strip("-")[:30]

    if not week:
        # compute ISO week
        from datetime import date as _date
        try:
            d = _date.fromisoformat(date_str)
            iso = d.isocalendar()
            week = f"{iso[0]}-W{iso[1]:02d}"
        except Exception:
            week = "unknown"

    filename = f"{date_str}-{wtype}-{slug}.md"
    return f"workouts/plans/{week}/{filename}"


def regenerate_pending_md() -> None:
    """Rewrite overview/pending.md from current pending workout files."""
    pending = list_pending_workouts()
    lines = ["# Pending Workouts\n", f"_Last updated: {date.today()}_\n\n"]
    if not pending:
        lines.append("No pending sessions.\n")
    else:
        lines.append("| Date | Day | Type | Duration | Focus | Status | File |\n")
        lines.append("|------|-----|------|----------|-------|--------|------|\n")
        for w in pending:
            try:
                from datetime import date as _date
                d = _date.fromisoformat(w["date"])
                day = d.strftime("%A")
            except Exception:
                day = ""
            dur = w.get("planned_duration_min") or "–"
            lines.append(
                f"| {w['date']} | {day} | {w['type']} | {dur} min | {w['key_focus']} | pending | {w['file']} |\n"
            )
    overview_file = OVERVIEW_DIR / "pending.md"
    write_file(overview_file, "".join(lines))


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