"""NHL API Fetcher with rate limiting, retry logic, and file-based fallback."""

import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx

from .config import settings

logger = logging.getLogger(__name__)

NHL_API_BASE = "https://api-web.nhle.com/v1"


class NHLAPIFetcher:
    """Fetch data from NHL API endpoints with rate limiting and file fallback."""

    def __init__(self, rate_limit_seconds: float = 1.0, fallback_dir: str | None = None):
        self.rate_limit = rate_limit_seconds
        self.fallback_dir = Path(fallback_dir) if fallback_dir else None
        self.client = httpx.Client(
            timeout=httpx.Timeout(30.0),
            headers={"User-Agent": "HockeyTalentID/1.0"},
        )
        self._last_request: float = 0.0

    def get(self, endpoint: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """GET request to NHL API with rate limiting, retry, and file fallback."""
        # Rate limiting
        elapsed = time.time() - self._last_request
        if elapsed < self.rate_limit:
            time.sleep(self.rate_limit - elapsed)

        # Try live API first
        max_retries = 3
        api_failed = False
        for attempt in range(max_retries):
            try:
                url = f"{NHL_API_BASE}/{endpoint}"
                logger.debug(f"Fetching: {url}")
                response = self.client.get(url, params=params)
                response.raise_for_status()
                self._last_request = time.time()
                return response.json()
            except (httpx.HTTPError, httpx.ConnectError) as e:
                logger.warning(f"Attempt {attempt + 1}/{max_retries} failed for {endpoint}: {e}")
                if attempt == max_retries - 1:
                    api_failed = True
                else:
                    time.sleep(2 ** attempt)

        # Fall back to local file if API failed
        if api_failed and self.fallback_dir:
            fallback_file = self.fallback_dir / f"{endpoint}.json"
            if fallback_file.exists():
                logger.info(f"API failed. Loading fallback from {fallback_file}")
                with open(fallback_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            else:
                logger.warning(f"No fallback file found for {endpoint}")

        if api_failed:
            raise RuntimeError(f"API failed for {endpoint} and no fallback available")

    def close(self):
        self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
