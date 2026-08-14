"""
AI coaching service — sends requests to Claude via Anthropic API.
Wraps all slash-command logic as API calls.
"""
import json
import os
from pathlib import Path
from typing import Optional

import anthropic

from app.config import ANTHROPIC_API_KEY, ANTHROPIC_BASE_URL, ANTHROPIC_MODEL
from app.services import file_service as fs

# Load CLAUDE.md system prompt once
_CLAUDE_MD_PATH = Path("CLAUDE.md")
_SYSTEM_PROMPT: Optional[str] = None


_WEB_PREAMBLE = """
# IMPORTANT: Web App Mode

You are running as a web application, NOT as Claude Code. You do NOT have file system tools
(Read, Write, Edit, Bash). All athlete data files are provided to you below in the
"Current Athlete Context" section — treat them as the authoritative, up-to-date state.

When commands say "read file X" or "glob files", interpret this as: the contents of that
file are already included in the context below. Do not say you "can't read" files —
the data is already provided.

## CRITICAL: How to create workout files in Web App Mode

When commands say "write file X" or "create file Y" for workout sessions, you MUST output
the COMPLETE raw markdown content of each file — including the YAML frontmatter block —
directly in your response. The web app will automatically detect and save these files.

Each workout file MUST start with exactly this format (no code fences, raw markdown):

---
date: YYYY-MM-DD
type: cycling|running|weights|swimming
discipline: Ride|Run|WeightTraining|Swim
status: pending
planned_duration_min: 60
week_folder: YYYY-WXX
key_focus: "Description here"
strava_activity_id: null
---

# Rest of workout content here...

Output each workout file as a separate block starting with ---. Do NOT wrap in code fences.
Do NOT say "I would create a file called X" — output the actual file content directly.
The web app parses these blocks automatically and saves them to disk, updates the calendar,
and regenerates the pending sessions list.

Stay in character as the AI coach defined in CLAUDE.md. Use the context provided.
"""

def _get_system_prompt() -> str:
    global _SYSTEM_PROMPT
    if _SYSTEM_PROMPT is None:
        text = _CLAUDE_MD_PATH.read_text(encoding="utf-8") if _CLAUDE_MD_PATH.exists() else ""
        _SYSTEM_PROMPT = _WEB_PREAMBLE + "\n\n" + text
    return _SYSTEM_PROMPT


def _client() -> anthropic.Anthropic:
    key      = os.getenv("ANTHROPIC_API_KEY", ANTHROPIC_API_KEY)
    base_url = os.getenv("ANTHROPIC_BASE_URL", ANTHROPIC_BASE_URL)
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY not set. Add it in Settings.")
    kwargs = {"api_key": key}
    if base_url:
        # Ensure base_url ends without trailing slash
        kwargs["base_url"] = base_url.rstrip("/")
    import logging
    logging.info(f"[ai_service] model={_model()} base_url={kwargs.get('base_url','default')}")
    return anthropic.Anthropic(**kwargs)


def _model() -> str:
    return os.getenv("ANTHROPIC_MODEL", ANTHROPIC_MODEL)


def _build_context() -> str:
    """Build a context block from current athlete files."""
    parts = []

    profile = fs.get_profile()
    if profile:
        parts.append(f"## athlete/profile.md\n{profile}")

    # Pending workouts — raw markdown table
    pending_md = fs.read_file(fs.OVERVIEW_DIR / "pending.md")
    if pending_md:
        parts.append(f"## overview/pending.md\n{pending_md}")
    else:
        pending = fs.list_pending_workouts()
        if pending:
            parts.append(f"## Pending workouts (count: {len(pending)})\n" +
                         json.dumps(pending[:10], indent=2))
        else:
            parts.append("## overview/pending.md\nNo pending sessions.")

    # Workout library
    library = fs.read_file(fs.ATHLETE_DIR / "workout-library.md")
    if library:
        parts.append(f"## athlete/workout-library.md\n{library[:3000]}")

    # Strava sync state
    sync = fs.get_strava_sync()
    parts.append(f"## overview/strava-sync.json\n{json.dumps(sync, indent=2)}")

    # Latest reflection
    reflection = fs.get_latest_reflection()
    if reflection:
        parts.append(f"## Latest reflection\n{reflection[:2000]}")

    # Recent journals
    journals = fs.list_journal_entries()
    if journals:
        parts.append(f"## Recent journal entries\n" + json.dumps(journals[:3], indent=2))

    # Consistency log
    consistency = fs.get_consistency_log()
    if consistency:
        parts.append(f"## athlete/consistency-log.md\n{consistency[:1500]}")

    return "\n\n---\n\n".join(parts)


def chat(messages: list[dict], stream: bool = False) -> str:
    """
    Send a conversation to Claude and return the response text.
    messages: list of {"role": "user"|"assistant", "content": str}
    """
    client = _client()
    system = _get_system_prompt() + "\n\n# Current Athlete Context\n\n" + _build_context()

    response = client.messages.create(
        model=_model(),
        max_tokens=4096,
        system=system,
        messages=messages,
    )
    return response.content[0].text


def run_command(command: str, args: str = "") -> str:
    """
    Run a slash command (e.g. /plan-workouts, /review, /journal).
    Loads the command definition from .claude/commands/ and sends to Claude.
    """
    cmd_name = command.lstrip("/")
    cmd_path = Path(".claude") / "commands" / f"{cmd_name}.md"

    if not cmd_path.exists():
        return f"Command /{cmd_name} not found."

    cmd_def = cmd_path.read_text(encoding="utf-8")
    system = _get_system_prompt() + "\n\n# Current Athlete Context\n\n" + _build_context()
    system += f"\n\n# Command Definition\n\n{cmd_def}"

    user_msg = f"Run /{cmd_name}"
    if args:
        user_msg += f" {args}"

    client = _client()
    response = client.messages.create(
        model=_model(),
        max_tokens=8096,
        system=system,
        messages=[{"role": "user", "content": user_msg}],
    )
    return response.content[0].text


MAX_HISTORY_MESSAGES = 20  # keep last 20 messages to avoid context overflow


def _strip_images_from_message(msg: dict) -> dict:
    """Remove image blocks from a message, keeping only text content."""
    content = msg.get("content", "")
    if isinstance(content, list):
        text_only = [b for b in content if isinstance(b, dict) and b.get("type") != "image"]
        if not text_only:
            text_only = [{"type": "text", "text": "[image removed from history]"}]
        return {**msg, "content": text_only}
    return msg


def _trim_messages(messages: list[dict]) -> list[dict]:
    """Keep only the last MAX_HISTORY_MESSAGES, always starting with a user message.
    Strip images from all but the last 2 messages to avoid context overflow."""
    if len(messages) <= MAX_HISTORY_MESSAGES:
        trimmed = messages
    else:
        trimmed = messages[-MAX_HISTORY_MESSAGES:]

    # Ensure first message is from user (API requirement)
    while trimmed and trimmed[0].get("role") != "user":
        trimmed = trimmed[1:]

    # Strip images from all but the last 2 messages
    result = []
    for i, msg in enumerate(trimmed):
        if i < len(trimmed) - 2:
            result.append(_strip_images_from_message(msg))
        else:
            result.append(msg)
    return result


def stream_chat(messages: list[dict]):
    """Generator that yields text chunks for SSE streaming."""
    client = _client()
    system = _get_system_prompt() + "\n\n# Current Athlete Context\n\n" + _build_context()
    trimmed = _trim_messages(messages)

    try:
        with client.messages.stream(
            model=_model(),
            max_tokens=8096,
            system=system,
            messages=trimmed,
        ) as stream:
            for text in stream.text_stream:
                yield text
    except Exception as e:
        import traceback
        err = traceback.format_exc()
        yield f"\n\n⚠️ **AI Error:** {str(e)}\n\nDetails: {err[:500]}"