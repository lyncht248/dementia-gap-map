"""Shared standard-library-only helpers for the translational-evidence track.

This module is the Foundation-phase shared library for Track B of the
dementia-gap-map prototype. It uses ONLY the Python 3.9 standard library
(no third-party packages) so the pipeline is reproducible on a clean machine.

Import it from scripts one level deep (ingest/ normalize/ map/ score/) via the
bootstrap:

    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
    import common

Run directly to print the resolved paths:

    python3 translational-evidence/common.py
"""

import datetime
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

TE_DIR = Path(__file__).resolve().parent
REPO_ROOT = TE_DIR.parent

RAW_DIR = REPO_ROOT / "data" / "raw" / "translational-evidence"
INTERIM_DIR = REPO_ROOT / "data" / "interim" / "translational-evidence"
PROCESSED_DIR = REPO_ROOT / "data" / "processed" / "translational-evidence"
SHARED_PROCESSED_DIR = REPO_ROOT / "data" / "processed" / "shared"
SCHEMA_DIR = REPO_ROOT / "shared" / "schemas"

# Polite identification for public research APIs.
USER_AGENT = "dementia-gap-map/0.1 (mailto:research@example.org)"


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------

def today_stamp():
    """Return today's date as an ISO string, e.g. '2026-07-01'."""
    return datetime.date.today().isoformat()


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def log(msg):
    """Print a progress message to stderr, prefixed with '[te] '."""
    print("[te] " + str(msg), file=sys.stderr)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _refresh_requested():
    """True if the TE_REFRESH env var is set to a non-empty value."""
    return bool(os.environ.get("TE_REFRESH"))


def _load_cache(cache_path):
    """Load and parse a cached JSON file, or return None if unavailable."""
    if cache_path is None:
        return None
    cache_path = Path(cache_path)
    if not cache_path.exists():
        return None
    if _refresh_requested():
        return None
    with cache_path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _write_cache(cache_path, data):
    """Pretty-write parsed JSON to the cache path (atomic), creating parents."""
    if cache_path is None:
        return
    cache_path = Path(cache_path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache_path.with_name(cache_path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False, sort_keys=True)
        fh.write("\n")
    os.replace(str(tmp), str(cache_path))


def _http_read(req, timeout):
    """Open a urllib request and return (status, decoded_body_text)."""
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        status = getattr(resp, "status", None)
        if status is None:
            # Python 3.9 HTTPResponse always has .status, but be defensive.
            status = resp.getcode()
        raw = resp.read()
    charset = "utf-8"
    return status, raw.decode(charset, errors="replace")


def _should_retry_httperror(err):
    """Retry HTTP errors only on server-side (>=500) failures."""
    code = getattr(err, "code", None)
    return code is not None and code >= 500


def _backoff_sleep(attempt):
    """Exponential backoff starting at 1s: 1, 2, 4, 8, ..."""
    time.sleep(2 ** attempt)


# ---------------------------------------------------------------------------
# HTTP: GET JSON
# ---------------------------------------------------------------------------

def get_json(url, params=None, headers=None, cache_path=None,
             timeout=60, retries=4, pause=0.34):
    """GET a URL and return parsed JSON, with caching and retries.

    - If ``cache_path`` exists and TE_REFRESH is not set, the cached JSON is
      returned without any network call.
    - Otherwise the request is issued with a real User-Agent and Accept header,
      retrying up to ``retries`` times on URLError / HTTP 5xx / timeout with
      exponential backoff starting at 1 second.
    - On success the raw JSON is written (pretty, indent=2) to ``cache_path``
      when provided, and the process sleeps ``pause`` seconds for politeness.
    - Raises RuntimeError including the url and last status/error on final
      failure.
    """
    cached = _load_cache(cache_path)
    if cached is not None:
        return cached

    full_url = url
    if params:
        query = urllib.parse.urlencode(params, doseq=True)
        sep = "&" if ("?" in url) else "?"
        full_url = url + sep + query

    req_headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }
    if headers:
        req_headers.update(headers)

    last_err = None
    last_status = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(full_url, headers=req_headers, method="GET")
            status, body = _http_read(req, timeout)
            last_status = status
            data = json.loads(body)
            _write_cache(cache_path, data)
            if pause:
                time.sleep(pause)
            return data
        except urllib.error.HTTPError as err:
            last_err = err
            last_status = getattr(err, "code", None)
            if _should_retry_httperror(err) and attempt < retries - 1:
                log("GET %s -> HTTP %s, retrying (%d/%d)"
                    % (full_url, last_status, attempt + 1, retries))
                _backoff_sleep(attempt)
                continue
            break
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as err:
            last_err = err
            if attempt < retries - 1:
                log("GET %s -> %s, retrying (%d/%d)"
                    % (full_url, err, attempt + 1, retries))
                _backoff_sleep(attempt)
                continue
            break

    raise RuntimeError(
        "GET failed for %s (status=%s) after %d attempts: %s"
        % (full_url, last_status, retries, last_err)
    )


# ---------------------------------------------------------------------------
# HTTP: POST JSON
# ---------------------------------------------------------------------------

def post_json(url, payload, cache_path=None, timeout=90, retries=4):
    """POST a JSON payload and return parsed JSON, with caching and retries.

    Same caching + retry semantics as ``get_json``. The body is
    ``json.dumps(payload)`` with Content-Type application/json.

    For GraphQL, an HTTP 200 response that carries a top-level ``errors`` key is
    treated as a failure: it is retried once and then raised.
    """
    cached = _load_cache(cache_path)
    if cached is not None:
        return cached

    body = json.dumps(payload).encode("utf-8")
    req_headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    last_err = None
    last_status = None
    graphql_error_retries_left = 1
    attempt = 0
    while attempt < retries:
        try:
            req = urllib.request.Request(
                url, data=body, headers=req_headers, method="POST"
            )
            status, resp_body = _http_read(req, timeout)
            last_status = status
            data = json.loads(resp_body)

            # GraphQL-style errors: HTTP 200 but a top-level "errors" key.
            if isinstance(data, dict) and data.get("errors"):
                if graphql_error_retries_left > 0:
                    graphql_error_retries_left -= 1
                    last_err = RuntimeError(
                        "GraphQL errors: %r" % (data.get("errors"),)
                    )
                    log("POST %s -> GraphQL errors, retrying once" % url)
                    _backoff_sleep(attempt)
                    attempt += 1
                    continue
                raise RuntimeError(
                    "POST %s returned GraphQL errors: %r"
                    % (url, data.get("errors"))
                )

            _write_cache(cache_path, data)
            return data
        except urllib.error.HTTPError as err:
            last_err = err
            last_status = getattr(err, "code", None)
            if _should_retry_httperror(err) and attempt < retries - 1:
                log("POST %s -> HTTP %s, retrying (%d/%d)"
                    % (url, last_status, attempt + 1, retries))
                _backoff_sleep(attempt)
                attempt += 1
                continue
            break
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as err:
            last_err = err
            if attempt < retries - 1:
                log("POST %s -> %s, retrying (%d/%d)"
                    % (url, err, attempt + 1, retries))
                _backoff_sleep(attempt)
                attempt += 1
                continue
            break

    raise RuntimeError(
        "POST failed for %s (status=%s) after %d attempts: %s"
        % (url, last_status, retries, last_err)
    )


# ---------------------------------------------------------------------------
# JSONL I/O
# ---------------------------------------------------------------------------

def read_jsonl(path):
    """Read a JSONL file into a list of dicts, tolerating blank lines."""
    path = Path(path)
    records = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def write_jsonl(path, records):
    """Atomically write records to a JSONL file; return the count written.

    Ensures the parent directory exists, writes to a temp file and then
    os.replace()s it into place. None records are skipped.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    count = 0
    with tmp.open("w", encoding="utf-8") as fh:
        for rec in records:
            if rec is None:
                continue
            fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True))
            fh.write("\n")
            count += 1
    os.replace(str(tmp), str(path))
    return count


# ---------------------------------------------------------------------------
# Text / numeric helpers
# ---------------------------------------------------------------------------

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def slug(text):
    """Lowercase a string, map non-alphanumeric runs to '_', and strip."""
    if text is None:
        return ""
    s = str(text).lower()
    s = _NON_ALNUM.sub("_", s)
    s = s.strip("_")
    return s


def clamp01(x):
    """Clamp a value to [0, 1] as float; None passes through as None."""
    if x is None:
        return None
    return max(0.0, min(1.0, float(x)))


def neglog10(p):
    """Return -log10(p); None for None or non-positive p."""
    if p is None:
        return None
    p = float(p)
    if p <= 0:
        return None
    return -math.log10(p)


def pick_gene_id(ensembl_ids, entrez_ids, symbol):
    """Choose a stable gene id: Ensembl > 'ENTREZ:'+Entrez > symbol > None."""
    if ensembl_ids:
        for eid in ensembl_ids:
            if eid:
                return str(eid)
    if entrez_ids:
        for eid in entrez_ids:
            if eid:
                return "ENTREZ:" + str(eid)
    if symbol:
        return str(symbol)
    return None


# ---------------------------------------------------------------------------
# Disease-group classification (Alzheimer + related dementias / ADRD)
# ---------------------------------------------------------------------------
#
# Every Track B record is tagged with a `disease_group` drawn from a small
# controlled vocabulary so downstream views can split Alzheimer disease from the
# related dementias. Classification is ONTOLOGY-DERIVED: it delegates to
# ``map/mesh_tree.classify_disease_text``, which matches the input free text (a
# GWAS trait, a trial condition, an Open Targets disease label, etc.) against the
# preferred labels AND entry-term synonyms of every MeSH descriptor in the
# Dementia subtree (branches C10.228.140.380 and F03.615.400), read live from the
# NLM MeSH SPARQL endpoint. The old hand-curated keyword lists have been retired:
# the vocabulary is now whatever MeSH says today.
#
# Controlled vocabulary (exact string values returned):
#   alzheimer                -> Alzheimer Disease (incl. late/early/focal onset)
#   vascular_dementia        -> Dementia, Vascular (Binswanger, multi-infarct, ...)
#   frontotemporal_dementia  -> FTLD / FTD / primary progressive aphasia / Pick
#   lewy_body_dementia       -> Lewy Body Disease
#   mixed_dementia           -> Mixed Dementias, or AD + a specific subtype
#   dementia_unspecified     -> bare "Dementia" and other Dementia-branch nodes
#                               (AIDS dementia, Creutzfeldt-Jakob, Huntington, ...)
#   None                     -> no Dementia label/synonym matched (e.g. mild
#                               cognitive impairment / cognitive dysfunction,
#                               which are NOT under the MeSH Dementia tree)
#
# NOTE: this replaces the old behaviour of returning "other" for unmatched text.
# Non-dementia text now returns None (schema allows null), which is consistent
# with the topic bridge's MeSH-tree classifier. "other" remains a listed
# vocabulary value but is no longer produced by the classifier.
#
# Precedence when the text matches several groups (unchanged):
#   mixed_dementia
#     > vascular_dementia / frontotemporal_dementia / lewy_body_dementia
#     > alzheimer
#     > dementia_unspecified
#
# Examples:
#   "Alzheimer's disease and vascular dementia" -> mixed_dementia
#   "Alzheimer's disease"                        -> alzheimer
#   "Alzheimer's disease or related dementias"   -> alzheimer
#   "Dementia"                                   -> dementia_unspecified
#   "Mild cognitive impairment"                  -> None

# Controlled-vocabulary constant so callers can reference values symbolically.
# "other" is retained for backward compatibility but is no longer emitted; the
# MeSH-driven classifier returns None instead of "other" for unmatched text.
DISEASE_GROUPS = (
    "alzheimer",
    "vascular_dementia",
    "frontotemporal_dementia",
    "lewy_body_dementia",
    "mixed_dementia",
    "dementia_unspecified",
    "other",
)

# Lazily-imported handle on the MeSH-tree module. Imported on first use (not at
# module load) to avoid a circular import: map/mesh_tree.py imports this module.
_MESH_TREE = None


def _mesh_tree():
    """Import and cache map/mesh_tree, resolving it from this file's location.

    The import is deferred so that ``import common`` never triggers a circular
    import (mesh_tree imports common). We add TE_DIR/map to sys.path and import
    by module name so it works regardless of how ``common`` was first imported.
    """
    global _MESH_TREE
    if _MESH_TREE is None:
        map_dir = str(TE_DIR / "map")
        if map_dir not in sys.path:
            sys.path.insert(0, map_dir)
        import mesh_tree  # noqa: E402  (deferred, path set just above)
        _MESH_TREE = mesh_tree
    return _MESH_TREE


def classify_disease_group(text):
    """Classify free text into ONE disease_group value, or None.

    Delegates to the MeSH-label-driven ``mesh_tree.classify_disease_text``: the
    input is matched, as whole phrases, against the preferred labels and entry
    terms of the MeSH Dementia subtree, resolved by the precedence:

        mixed_dementia
          > vascular_dementia / frontotemporal_dementia / lewy_body_dementia
          > alzheimer
          > dementia_unspecified

    Returns None when no Dementia label matches (including None / empty /
    whitespace-only input). This is ontology-derived: there are no hand keyword
    lists.
    """
    if not text or not str(text).strip():
        return None
    return _mesh_tree().classify_disease_text(text)


def classify_disease_groups(texts):
    """Classify an iterable of strings into a dedup+sorted list of groups.

    Useful for records that span several traits / conditions (e.g. a gene
    aggregated across many GWAS traits, or a trial with several conditions).
    Each string is classified independently with ``classify_disease_group`` and
    the distinct NON-NULL results are returned sorted. None / non-string members
    and texts that match no Dementia label contribute nothing. An empty / falsy
    iterable returns [].
    """
    if not texts:
        return []
    groups = set()
    for text in texts:
        group = classify_disease_group(text)
        if group is not None:
            groups.add(group)
    return sorted(groups)


# ---------------------------------------------------------------------------
# Debug entry point
# ---------------------------------------------------------------------------

def _print_paths():
    print("TE_DIR               = %s" % TE_DIR)
    print("REPO_ROOT            = %s" % REPO_ROOT)
    print("RAW_DIR              = %s" % RAW_DIR)
    print("INTERIM_DIR          = %s" % INTERIM_DIR)
    print("PROCESSED_DIR        = %s" % PROCESSED_DIR)
    print("SHARED_PROCESSED_DIR = %s" % SHARED_PROCESSED_DIR)
    print("SCHEMA_DIR           = %s" % SCHEMA_DIR)
    print("today_stamp()        = %s" % today_stamp())


if __name__ == "__main__":
    _print_paths()
