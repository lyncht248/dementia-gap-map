"""API-derived MeSH -> disease_group classifier for Track B.

This module REPLACES the hand-curated ``map/mesh_disease.csv`` crosswalk. Disease
classification of a MeSH descriptor now requires ZERO hand definition: the whole
Dementia subtree is read live from the authoritative NLM MeSH SPARQL endpoint,
and each descriptor is bucketed into a ``disease_group`` purely from its position
in the MeSH tree (its tree number's sub-branch).

Design
------
The ONLY hand input is five anchor sub-branch prefixes under the neurological
Dementia branch ``C10.228.140.380``:

    alzheimer                = C10.228.140.380.100
    vascular_dementia        = C10.228.140.380.230
    frontotemporal_dementia  = C10.228.140.380.266  OR  C10.228.140.380.132
    lewy_body_dementia       = C10.228.140.380.422
    mixed_dementia           = C10.228.140.380.711

Everything else under ``C10.228.140.380`` (e.g. AIDS Dementia Complex,
Creutzfeldt-Jakob, Huntington, Kluver-Bucy, the bare "Dementia" root) and
everything under the mental-disorders mirror ``F03.615.400`` falls back to
``dementia_unspecified``. The set of descriptors in each bucket is NOT hand
listed -- it is whatever the API returns under those tree positions today, so a
re-run automatically picks up new/retired MeSH descriptors.

A single descriptor UI can sit at several tree numbers (e.g. D000544 Alzheimer
Disease is under both C10.228.140.380.100 and F03.615.400.100). We classify each
tree number independently and then, per UI, keep the MOST SPECIFIC group: a
concrete subtype (alzheimer / vascular_dementia / frontotemporal_dementia /
lewy_body_dementia / mixed_dementia) always wins over dementia_unspecified. The
retained ``tree_number`` is the one that decided the bucket, so the provenance
shows exactly WHY the UI got its group.

Data path
---------
Primary:  MeSH SPARQL endpoint, one query per branch, cached under
          RAW_DIR/mesh/dementia_subtree_{stamp}.json.
Fallback: if SPARQL fails, per-UI descriptor JSON from
          https://id.nlm.nih.gov/mesh/{UI}.json for the corpus's distinct MeSH
          UIs, classified by treeNumber prefix (cached per UI).

The path actually used is logged.

Public API
----------
    fetch_dementia_subtree()          -> list[{ui, tree_number, label}]
    classify_mesh_ui(ui)              -> {disease_group, tree_number, label} | None
    DERIVED_MESH_DISEASE              -> {ui: {disease_group, tree_number, label}}

None from ``classify_mesh_ui`` means the UI is not under a Dementia branch.

STDLIB-only Python 3.9. Run directly to print the classified table and write a
gitignored debug CSV to INTERIM_DIR/mesh_disease_derived.csv:

    python3 translational-evidence/map/mesh_tree.py
"""

import csv
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import common  # noqa: E402  (import after sys.path bootstrap)


# ---------------------------------------------------------------------------
# Configuration: endpoints, branches, and the ONLY hand mapping (5 anchors)
# ---------------------------------------------------------------------------

MESH_SPARQL_URL = "https://id.nlm.nih.gov/mesh/sparql"
MESH_DESCRIPTOR_URL = "https://id.nlm.nih.gov/mesh/%s.json"
MESH_URI_PREFIX = "http://id.nlm.nih.gov/mesh/"

# The two MeSH tree branches that ARE "Dementia": the neurological branch and
# the mental-disorders mirror. Everything read comes from under these.
DEMENTIA_BRANCHES = (
    "C10.228.140.380",   # Nervous System Diseases -> ... -> Dementia
    "F03.615.400",       # Mental Disorders -> ... -> Dementia (mirror)
)

# The disease_group buckets, in "specificity" order: a concrete subtype outranks
# dementia_unspecified when a UI sits in several branches.
DISEASE_GROUP_UNSPECIFIED = "dementia_unspecified"

# The ONLY hand mapping: anchor sub-branch prefixes -> disease_group. A tree
# number is bucketed to the group whose anchor prefix it starts with (longest
# match wins); anything else under a Dementia branch is dementia_unspecified.
# These live only under C10.228.140.380; the F03 mirror has different numeric
# sub-branches, so F03 tree numbers never match an anchor and stay unspecified.
ANCHOR_PREFIXES = (
    ("C10.228.140.380.100", "alzheimer"),
    ("C10.228.140.380.230", "vascular_dementia"),
    ("C10.228.140.380.266", "frontotemporal_dementia"),
    ("C10.228.140.380.132", "frontotemporal_dementia"),  # PPA / FTD spectrum
    ("C10.228.140.380.422", "lewy_body_dementia"),
    ("C10.228.140.380.711", "mixed_dementia"),
)

# Specificity rank for choosing a UI's group when it has several tree numbers.
# Higher wins. dementia_unspecified is the floor.
_GROUP_RANK = {
    "alzheimer": 5,
    "vascular_dementia": 5,
    "frontotemporal_dementia": 5,
    "lewy_body_dementia": 5,
    "mixed_dementia": 5,
    DISEASE_GROUP_UNSPECIFIED: 1,
}


# ---------------------------------------------------------------------------
# Tree-number -> bucket rule
# ---------------------------------------------------------------------------

def tree_number_group(tree_number):
    """Return the disease_group for one tree number, or None if not Dementia.

    A tree number is Dementia iff it starts with one of DEMENTIA_BRANCHES. It is
    bucketed to the anchor sub-branch it starts with (longest anchor wins);
    otherwise (root, other C10 sub-branches, or the whole F03 mirror) it is
    ``dementia_unspecified``.
    """
    if tree_number is None:
        return None
    tn = str(tree_number).strip()
    under_dementia = any(
        tn == b or tn.startswith(b + ".") or tn == b
        for b in DEMENTIA_BRANCHES
    )
    if not under_dementia:
        return None
    # Longest anchor prefix match wins (so .266.299 -> frontotemporal via .266,
    # and .100 exact -> alzheimer). Sort anchors by length descending.
    best_group = None
    best_len = -1
    for prefix, group in ANCHOR_PREFIXES:
        if (tn == prefix or tn.startswith(prefix + ".")) and len(prefix) > best_len:
            best_group = group
            best_len = len(prefix)
    return best_group if best_group is not None else DISEASE_GROUP_UNSPECIFIED


def _better(existing, candidate):
    """True if candidate (group, tree_number) should replace existing.

    More-specific group wins; on a tie, the shorter/lexicographically-smaller
    tree number wins so the choice is deterministic.
    """
    if existing is None:
        return True
    eg, etn = existing
    cg, ctn = candidate
    er = _GROUP_RANK.get(eg, 0)
    cr = _GROUP_RANK.get(cg, 0)
    if cr != er:
        return cr > er
    # Same specificity: prefer the anchor (C10) tree over the F03 mirror, then
    # the shorter, then lexicographically smaller, for determinism.
    e_is_c10 = etn.startswith("C10")
    c_is_c10 = ctn.startswith("C10")
    if c_is_c10 != e_is_c10:
        return c_is_c10
    if len(ctn) != len(etn):
        return len(ctn) < len(etn)
    return ctn < etn


# ---------------------------------------------------------------------------
# Primary path: SPARQL
# ---------------------------------------------------------------------------

def _sparql_query(branch_prefix):
    """Build the tree-subtree SPARQL query for one branch prefix."""
    return (
        "PREFIX meshv: <http://id.nlm.nih.gov/mesh/vocab#>\n"
        "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>\n"
        "SELECT ?d ?label ?tn WHERE {\n"
        "  ?d meshv:treeNumber ?t . ?d rdfs:label ?label .\n"
        "  BIND(STRAFTER(STR(?t),\"http://id.nlm.nih.gov/mesh/\") AS ?tn)\n"
        "  FILTER(STRSTARTS(?tn,\"%s\"))\n"
        "} ORDER BY ?tn" % branch_prefix
    )


def _ui_from_uri(uri):
    """Extract the descriptor UI from a MeSH resource URI."""
    if not uri:
        return None
    return uri.rsplit("/", 1)[-1]


def _fetch_subtree_sparql():
    """Fetch both Dementia branches via SPARQL.

    Returns a list of raw {ui, tree_number, label} rows (one per tree number),
    or raises on failure so the caller can trigger the fallback.
    """
    stamp = common.today_stamp()
    rows = []
    for branch in DEMENTIA_BRANCHES:
        cache = (common.RAW_DIR / "mesh"
                 / ("dementia_subtree_%s_%s.json"
                    % (branch.replace(".", "_"), stamp)))
        data = common.get_json(
            MESH_SPARQL_URL,
            params={"query": _sparql_query(branch), "format": "JSON"},
            cache_path=cache,
        )
        bindings = (data.get("results") or {}).get("bindings") or []
        for b in bindings:
            ui = _ui_from_uri((b.get("d") or {}).get("value"))
            tn = (b.get("tn") or {}).get("value")
            label = (b.get("label") or {}).get("value")
            if ui and tn:
                rows.append({"ui": ui, "tree_number": tn, "label": label})
        common.log("MeSH SPARQL branch %s -> %d tree rows" % (branch, len(bindings)))
    if not rows:
        raise RuntimeError("MeSH SPARQL returned no rows for any Dementia branch")
    return rows


# ---------------------------------------------------------------------------
# Fallback path: per-UI descriptor JSON
# ---------------------------------------------------------------------------

def _corpus_mesh_uis():
    """Distinct MeSH descriptor UIs present in the Track A snapshot papers.

    Used only by the fallback path. Returns [] if the snapshot is absent.
    """
    papers_path = (common.INTERIM_DIR / "track_a_snapshot" / "papers.jsonl")
    if not papers_path.exists():
        common.log("fallback: snapshot papers.jsonl not found at %s" % papers_path)
        return []
    uis = set()
    for p in common.read_jsonl(papers_path):
        for m in (p.get("mesh") or []):
            ui = m.get("ui")
            if ui:
                uis.add(ui)
    return sorted(uis)


def _tree_numbers_from_descriptor(descriptor):
    """Extract the list of tree-number strings from a descriptor JSON doc."""
    raw = descriptor.get("treeNumber") or []
    if isinstance(raw, str):
        raw = [raw]
    out = []
    for item in raw:
        if isinstance(item, str):
            tn = item
        elif isinstance(item, dict):
            tn = item.get("@id") or item.get("value")
        else:
            tn = None
        if tn and tn.startswith(MESH_URI_PREFIX):
            tn = tn[len(MESH_URI_PREFIX):]
        if tn:
            out.append(tn)
    return out


def _label_from_descriptor(descriptor):
    """Extract the English label string from a descriptor JSON doc."""
    lab = descriptor.get("label")
    if isinstance(lab, str):
        return lab
    if isinstance(lab, dict):
        return lab.get("@value") or lab.get("value")
    return None


def _fetch_subtree_fallback():
    """Classify the corpus's MeSH UIs via per-UI descriptor JSON.

    Returns raw {ui, tree_number, label} rows for every corpus UI that has at
    least one tree number under a Dementia branch. Cached per UI.
    """
    rows = []
    uis = _corpus_mesh_uis()
    common.log("fallback: probing %d distinct corpus MeSH UIs" % len(uis))
    for ui in uis:
        cache = common.RAW_DIR / "mesh" / ("descriptor_%s.json" % ui)
        try:
            descriptor = common.get_json(
                MESH_DESCRIPTOR_URL % ui, cache_path=cache
            )
        except RuntimeError as err:
            common.log("fallback: descriptor fetch failed for %s: %s" % (ui, err))
            continue
        label = _label_from_descriptor(descriptor)
        for tn in _tree_numbers_from_descriptor(descriptor):
            if any(tn == b or tn.startswith(b + ".") for b in DEMENTIA_BRANCHES):
                rows.append({"ui": ui, "tree_number": tn, "label": label})
    return rows


# ---------------------------------------------------------------------------
# Build the derived map
# ---------------------------------------------------------------------------

def fetch_dementia_subtree():
    """Return raw {ui, tree_number, label} rows for the Dementia subtree.

    Tries SPARQL first (both branches); on any failure falls back to per-UI
    descriptor JSON over the corpus's MeSH UIs. Logs which path was used and
    records it on the module-level ``FETCH_PATH``.
    """
    global FETCH_PATH
    try:
        rows = _fetch_subtree_sparql()
        FETCH_PATH = "sparql"
        common.log("MeSH classifier path: SPARQL (%d tree rows total)" % len(rows))
        return rows
    except (RuntimeError, Exception) as err:  # noqa: BLE001 broad on purpose
        common.log("MeSH SPARQL failed (%s); using per-UI descriptor fallback"
                   % err)
    rows = _fetch_subtree_fallback()
    FETCH_PATH = "descriptor_fallback"
    common.log("MeSH classifier path: descriptor fallback (%d tree rows)"
               % len(rows))
    return rows


def build_derived_map(rows):
    """Collapse raw tree rows into {ui: {disease_group, tree_number, label}}.

    Each tree number is bucketed via ``tree_number_group``; per UI the most
    specific group is kept (see module docstring). Tree numbers not under a
    Dementia branch are ignored.
    """
    derived = {}
    for row in rows:
        ui = row["ui"]
        tn = row["tree_number"]
        group = tree_number_group(tn)
        if group is None:
            continue
        candidate = (group, tn)
        existing = derived.get(ui)
        existing_pair = (
            (existing["disease_group"], existing["tree_number"])
            if existing else None
        )
        if _better(existing_pair, candidate):
            derived[ui] = {
                "disease_group": group,
                "tree_number": tn,
                "label": row.get("label"),
            }
        elif existing and not existing.get("label") and row.get("label"):
            existing["label"] = row.get("label")
    return derived


# ---------------------------------------------------------------------------
# Module-level derived map (lazy, built once on first use)
# ---------------------------------------------------------------------------

FETCH_PATH = None            # "sparql" | "descriptor_fallback" | None
DERIVED_MESH_DISEASE = {}    # populated by _ensure_derived()

_BUILT = False


def _ensure_derived():
    """Build DERIVED_MESH_DISEASE once (idempotent)."""
    global DERIVED_MESH_DISEASE, _BUILT
    if _BUILT:
        return DERIVED_MESH_DISEASE
    rows = fetch_dementia_subtree()
    DERIVED_MESH_DISEASE = build_derived_map(rows)
    _BUILT = True
    common.log("derived MeSH disease map: %d UIs classified (path=%s)"
               % (len(DERIVED_MESH_DISEASE), FETCH_PATH))
    return DERIVED_MESH_DISEASE


def classify_mesh_ui(ui):
    """Classify one MeSH descriptor UI.

    Returns {disease_group, tree_number, label} if the UI is under a Dementia
    branch, else None.
    """
    if not ui:
        return None
    return _ensure_derived().get(ui)


# Build eagerly on import so ``DERIVED_MESH_DISEASE`` is a populated dict for
# callers that read the constant directly (per the task's public API contract).
_ensure_derived()


# ---------------------------------------------------------------------------
# Debug entry point
# ---------------------------------------------------------------------------

DEBUG_CSV = common.INTERIM_DIR / "mesh_disease_derived.csv"


def _write_debug_csv(derived):
    """Write ui,tree_number,label,disease_group to the gitignored debug CSV."""
    DEBUG_CSV.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(
        derived.items(),
        key=lambda kv: (kv[1]["disease_group"], kv[1]["tree_number"]),
    )
    with DEBUG_CSV.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["ui", "tree_number", "label", "disease_group"])
        for ui, rec in ordered:
            writer.writerow([ui, rec["tree_number"], rec.get("label") or "",
                             rec["disease_group"]])
    return len(ordered)


def _print_table(derived):
    """Print the classified subtree grouped by disease_group."""
    from collections import Counter
    counts = Counter(rec["disease_group"] for rec in derived.values())
    print("\n=== API-derived MeSH disease classification (path=%s) ==="
          % FETCH_PATH)
    print("%-11s  %-28s  %-24s  %s"
          % ("ui", "tree_number", "disease_group", "label"))
    print("-" * 100)
    for ui, rec in sorted(
        derived.items(),
        key=lambda kv: (kv[1]["disease_group"], kv[1]["tree_number"]),
    ):
        print("%-11s  %-28s  %-24s  %s"
              % (ui, rec["tree_number"], rec["disease_group"],
                 rec.get("label") or ""))
    print("\n=== bucket counts (%d UIs total) ===" % len(derived))
    for group in sorted(counts, key=lambda g: (-counts[g], g)):
        print("  %-24s %d" % (group, counts[group]))


def main():
    derived = _ensure_derived()
    _print_table(derived)
    n = _write_debug_csv(derived)
    common.log("wrote %d rows -> %s" % (n, DEBUG_CSV))
    return 0


if __name__ == "__main__":
    sys.exit(main())
