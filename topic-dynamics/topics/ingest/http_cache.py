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
    text = _cached_get(url, params, min_interval, timeout, label, ext="json")
    return json.loads(text)


def get_text(
    url: str,
    params: dict[str, Any] | None = None,
    *,
    min_interval: float = 0.0,
    timeout: int = 60,
    label: str = "",
    ext: str = "xml",
) -> str:
    """GET ``url`` and return the raw response body, caching on disk.

    Used for endpoints that return XML (e.g. PubMed efetch) rather than JSON.
    """
    return _cached_get(url, params, min_interval, timeout, label, ext=ext)


def _cached_get(
    url: str,
    params: dict[str, Any] | None,
    min_interval: float,
    timeout: int,
    label: str,
    ext: str,
) -> str:
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = _cache_key(url, params)
    prefix = f"{label}_" if label else ""
    cache_path = config.CACHE_DIR / f"{prefix}{key}.{ext}"

    if cache_path.exists():
        return cache_path.read_text()

    text = _get_with_retry(url, params, timeout, min_interval)
    cache_path.write_text(text)
    return text


def _get_with_retry(
    url: str,
    params: dict[str, Any] | None,
    timeout: int,
    min_interval: float,
    attempts: int = 5,
) -> str:
    """GET with exponential backoff over transient network/SSL/5xx errors.

    Long field runs make thousands of calls, so an occasional dropped
    connection is expected and must not abort the pipeline. Returns the raw
    response text; callers parse JSON or XML as needed.
    """
    last_exc: Exception | None = None
    for attempt in range(attempts):
        _throttle(min_interval)
        try:
            resp = requests.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
            return resp.text
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            if attempt < attempts - 1:
                time.sleep(2**attempt)  # 1, 2, 4, 8, 16s
    raise RuntimeError(f"GET failed after {attempts} attempts: {url}") from last_exc
