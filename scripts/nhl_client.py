"""Tiny HTTP client: GET JSON with retries, and cache every response on disk.

Caching means a crash at game 900 of 1312 costs nothing: rerun and it skips
everything already downloaded.
"""
import json
import time
from pathlib import Path

import requests

from config import REQUEST_DELAY_SEC

_session = requests.Session()
_session.headers["User-Agent"] = "nhl-hockey-analytics (personal portfolio project)"


class NotFound(Exception):
    """The API answered 404: retrying will not help."""


def get_json(url: str, cache_path: Path, refresh: bool = False, retries: int = 4):
    if cache_path.exists() and not refresh:
        return json.loads(cache_path.read_text(encoding="utf-8"))

    for attempt in range(retries):
        try:
            resp = _session.get(url, timeout=30)
            if resp.status_code == 404:
                raise NotFound(url)
            if resp.status_code == 429 or resp.status_code >= 500:
                raise requests.HTTPError(f"HTTP {resp.status_code}")
            resp.raise_for_status()
            data = resp.json()
            break
        except (requests.RequestException, ValueError) as err:
            if attempt == retries - 1:
                raise
            wait = 2 ** attempt   # 1s, 2s, 4s: exponential backoff
            print(f"    retry {attempt + 1} in {wait}s ({err}) - {url}")
            time.sleep(wait)

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(data), encoding="utf-8")
    time.sleep(REQUEST_DELAY_SEC)
    return data
