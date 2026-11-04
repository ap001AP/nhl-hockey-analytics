"""Project settings. Change seasons / game types here and nowhere else."""
from pathlib import Path

# Seasons to load, written the way the NHL API writes them.
# Add older seasons (e.g. "20232024", "20242025") to fill the Career Stats table.
# Each extra season adds ~30-40 min to the FIRST fetch; reruns use the on-disk cache.
SEASONS = ["20252026", "20262027"]

# 2 = regular season, 3 = playoffs
GAME_TYPES = {2}

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw" / "nhl"
CLEAN_DIR = ROOT / "data" / "clean"
ASSETS_DIR = ROOT / "tableau" / "assets"

API_WEB = "https://api-web.nhle.com/v1"
API_STATS = "https://api.nhle.com/stats/rest/en"

REQUEST_DELAY_SEC = 0.25          # free, undocumented API: be polite
FINAL_STATES = {"FINAL", "OFF"}   # games that are finished
