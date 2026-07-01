"""Rate-limited, file-cached HTTP JSON client.

Every response is written to ``data/raw/topic-dynamics/cache`` keyed by a hash
of the URL + params, so re-running the pipeline never re-hits an API and
debugging is deterministic. NCBI requests are throttled to respect its rate
limits.
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.parse
from typing import Any

import requests

from .. import config

_last_request_ts = 0.0


def _throttle(min_interval: float) -> None:
    global _last_request_ts
    if min_interval <= 0:
        return
    wait = min_interval - (time.monotonic() - _last_request_ts)
    if wait > 0:
        time.sleep(wait)
    _last_request_ts = time.monotonic()


def _cache_key(url: str, params: dict[str, Any] | None) -> str:
    canonical = url + "?" + urllib.parse.urlencode(sorted((params or {}).items()))
    return hashlib.sha1(canonical.encode()).hexdigest()


def get_json(
    url: str,
    params: dict[str, Any] | None = None,
    *,
    min_interval: float = 0.0,
    timeout: int = 60,
    label: str = "",
) -> Any:
    """GET ``url`` and return parsed JSON, caching on disk.

    ``label`` is only used to make cache filenames human-readable.
    """
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = _cache_key(url, params)
    prefix = f"{label}_" if label else ""
    cache_path = config.CACHE_DIR / f"{prefix}{key}.json"

    if cache_path.exists():
        with cache_path.open() as fh:
            return json.load(fh)

    _throttle(min_interval)
    resp = requests.get(url, params=params, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()

    with cache_path.open("w") as fh:
        json.dump(data, fh)
    return data
