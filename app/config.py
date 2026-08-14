import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Base data directory — /data on Railway, ./local_data in dev
DATA_DIR = Path(os.getenv("DATA_DIR", "./local_data"))

ATHLETE_DIR   = DATA_DIR / "athlete"
WORKOUTS_DIR  = DATA_DIR / "workouts"
JOURNALS_DIR  = DATA_DIR / "journals"
OVERVIEW_DIR  = DATA_DIR / "overview"
DATA_FILES    = DATA_DIR / "data"

# Strava
STRAVA_CLIENT_ID     = os.getenv("STRAVA_CLIENT_ID", "")
STRAVA_CLIENT_SECRET = os.getenv("STRAVA_CLIENT_SECRET", "")
STRAVA_REFRESH_TOKEN = os.getenv("STRAVA_REFRESH_TOKEN", "")

# Anthropic
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# Hevy (optional)
HEVY_API_KEY = os.getenv("HEVY_API_KEY", "")

# App
APP_SECRET = os.getenv("APP_SECRET", "change_me")


def ensure_dirs():
    """Create all required data directories if they don't exist."""
    for d in [
        ATHLETE_DIR,
        WORKOUTS_DIR / "plans",
        WORKOUTS_DIR / "completed",
        WORKOUTS_DIR / "reflections",
        JOURNALS_DIR,
        OVERVIEW_DIR,
        DATA_FILES,
    ]:
        d.mkdir(parents=True, exist_ok=True)