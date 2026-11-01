import json
import os
import re
import time

from dedupe import url_id

CACHE_DIR = os.environ.get("CACHE_DIR", "/app/cache")
MAX_AGE_SECONDS = 30 * 24 * 3600
COOLDOWN_SECONDS = 6 * 3600
# Only a real rate limit starts the cooldown; an expired job page must not pause every other job.
RATE_BLOCK = re.compile(r"HTTP (429|403|999)|login|anti-bot", re.IGNORECASE)


class JobCache:
    """Remembers each job page we already read, so a page is opened once and LinkedIn is not hit again."""

    def __init__(self, path: str | None = None, now=time.time):
        self.path = path or os.path.join(CACHE_DIR, "job_details.json")
        self.now = now
        self.data = self._load()

    def _load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        if not isinstance(data.get("jobs"), dict):
            data["jobs"] = {}
        data.setdefault("blocked_until", 0)
        return data

    def get(self, url: str) -> dict | None:
        entry = self.data["jobs"].get(url_id(url))
        if entry and self.now() - entry.get("cached_at", 0) < MAX_AGE_SECONDS:
            return entry["details"]
        return None

    def put(self, url: str, details: dict) -> None:
        self.data["jobs"][url_id(url)] = {"cached_at": self.now(), "details": details}

    def block(self, reason: str) -> None:
        if RATE_BLOCK.search(reason or ""):
            self.data["blocked_until"] = self.now() + COOLDOWN_SECONDS

    def cooldown_minutes(self) -> int:
        return max(0, int((self.data["blocked_until"] - self.now()) / 60))

    def save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = f"{self.path}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f)
            os.replace(tmp, self.path)
        except OSError as e:
            # A cache that cannot be written only means pages are read again next time.
            print(f"job cache not saved: {type(e).__name__}", flush=True)
