#!/usr/bin/env python3
"""Build a zero-install, filterable WebGL explorer over the FULL Track B graph.

This is the *viz* layer for the standalone evidence-graph explorer. It reads the
already-built, un-capped graph export produced by
``translational-evidence/exports/build_evidence_graph.py``:

  data/exports/graph/nodes.jsonl        (evidence_node records + x/y layout)
  data/exports/graph/edges.jsonl        (evidence_edge records)
  data/exports/graph/graph_manifest.json

...and writes TWO gitignored build products next to them:

  data/exports/graph/graph_data.js      -> assigns window.GRAPH = {nodes, edges,
                                           meta}. Loaded by the HTML via a plain
                                           <script src> tag so it works under
                                           file:// (no fetch(), no CORS).
  data/exports/graph/evidence_graph.html -> self-contained page that pulls
                                           sigma.js + graphology from a CDN
                                           (needs internet for the libs) and
                                           renders the full ~15k-node graph with
                                           WebGL, using the baked-in x/y layout.

Design intent (from the user): show EVERYTHING and let FILTERS do the legibility
work. So the export is not capped; the HTML ships a rich filter panel:
  * per-node-type checkboxes (7 types) with a colour legend,
  * a disease_group multi-select,
  * a pathway / mechanism group select,
  * a trial-phase filter,
  * a min-score slider (hide nodes below a threshold),
  * a text search box that highlights + zooms matching nodes,
  * a "reset / show all" button,
and the default view shows variant/gene/pathway/drug/disease/topic with TRIALS
toggled OFF (6.8k) so it opens legible but everything remains reachable.

Nothing is fabricated: every node/edge carries its original score + provenance,
which the side panel renders in human-readable form.

Run:
  python3 translational-evidence/viz/build_graph_viz.py
"""

import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import common  # noqa: E402  (import after sys.path bootstrap)


# "Now" for recency filter defaults. Documented + overridable via
# TE_CURRENT_YEAR so it matches how entity_metrics.jsonl was computed.
CURRENT_YEAR = int(os.environ.get("TE_CURRENT_YEAR", "2026"))

# Flat, filterable metric props hoisted onto graph nodes by
# build_evidence_graph.py (union across gene/variant/pathway). Carried through to
# the browser payload for the side panel + metric filter controls.
FLAT_METRIC_PROPS = [
    "stopped_ratio",
    "direction_agreement",
    "n_conflicting",
    "n_trials",
    "n_drugs",
    "has_approval",
    "translation_gap",   # gene/pathway note: also a per-node flat metric
    "first_gwas_year",
    "latest_gwas_year",
    "n_recent_gwas",
    "first_trial_year",
    "latest_trial_year",
    "n_recent_trials",
    "first_year",
    "latest_year",
    "n_recent",
    "n_associations",
    "n_studies",
]


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

GRAPH_DIR = common.REPO_ROOT / "data" / "exports" / "graph"
NODES_PATH = GRAPH_DIR / "nodes.jsonl"
EDGES_PATH = GRAPH_DIR / "edges.jsonl"
MANIFEST_PATH = GRAPH_DIR / "graph_manifest.json"

DATA_JS_PATH = GRAPH_DIR / "graph_data.js"
HTML_PATH = GRAPH_DIR / "evidence_graph.html"


# ---------------------------------------------------------------------------
# Visual vocabulary
# ---------------------------------------------------------------------------

# Node colour per node_type (kept in one place; mirrored into the HTML legend).
NODE_COLORS = {
    "variant": "#8c8c8c",   # grey     - GWAS variants / loci
    "gene": "#1f77b4",      # blue     - genes / targets
    "pathway": "#2ca02c",   # green    - mechanism / pathway groups
    "drug": "#d62728",      # red      - real drug interventions
    "trial": "#ff7f0e",     # orange   - clinical trials
    "disease": "#9467bd",   # purple   - disease groups
    "topic": "#e377c2",     # pink     - Track A literature topics
}

# Human-readable node_type labels for the legend / side panel.
NODE_TYPE_LABELS = {
    "variant": "Variant / locus",
    "gene": "Gene / target",
    "pathway": "Pathway / mechanism",
    "drug": "Drug",
    "trial": "Trial",
    "disease": "Disease group",
    "topic": "Literature topic",
}

# Faint edge colour per edge_type (RGBA so overlapping edges stay legible).
EDGE_COLORS = {
    "variant_gene": "rgba(140,140,140,0.25)",
    "gene_disease": "rgba(148,103,189,0.30)",
    "gene_pathway": "rgba(44,160,44,0.35)",
    "drug_pathway": "rgba(214,39,40,0.22)",
    "drug_gene": "rgba(214,39,40,0.30)",
    "topic_gene": "rgba(227,119,194,0.35)",
    "topic_pathway": "rgba(227,119,194,0.30)",
    "topic_disease": "rgba(227,119,194,0.40)",
    "trial_drug": "rgba(255,127,14,0.14)",
    "trial_pathway": "rgba(255,127,14,0.12)",
}

# Node types shown by default; trials (~6.8k) start OFF so the page opens legible.
DEFAULT_ON_TYPES = ["variant", "gene", "pathway", "drug", "disease", "topic"]

# Threshold above which a pathway is flagged as "under-translated".
TRANSLATION_GAP_FLAG = 0.30


# ---------------------------------------------------------------------------
# Node / edge slimming for the browser payload
# ---------------------------------------------------------------------------

def _round_coord(v):
    """Round a layout coordinate; None -> 0.0 so sigma never chokes."""
    if v is None:
        return 0.0
    return round(float(v), 3)


def slim_node(rec):
    """Project a full evidence_node into the compact record the viz needs.

    We keep everything the filters + side panel use: id, label, type, group,
    disease_groups, score, the full scores dict, x/y, a phases list (trials),
    an overall_status (trials), a translation_gap flag (pathways) and the raw
    provenance dict (rendered read-only in the side panel).
    """
    node_type = rec.get("node_type")
    prov = rec.get("provenance") or {}
    scores = rec.get("scores") or {}

    phases = []
    status = None
    if node_type == "trial":
        phases = list(prov.get("phases") or [])
        status = prov.get("overall_status")

    gap = None
    under_translated = False
    if node_type == "pathway":
        gap = scores.get("translation_gap")
        if gap is not None and float(gap) >= TRANSLATION_GAP_FLAG:
            under_translated = True

    slim = {
        "id": rec.get("node_id"),
        "label": rec.get("label") or rec.get("node_id"),
        "type": node_type,
        "group": rec.get("group"),
        "disease_groups": list(rec.get("disease_groups") or []),
        "score": rec.get("score"),
        "scores": scores,
        "provenance": prov,
        "x": _round_coord(rec.get("x")),
        "y": _round_coord(rec.get("y")),
        "phases": phases,
        "status": status,
        "translation_gap": gap,
        "under_translated": under_translated,
        # Full nested per-entity metrics (each {value, source}); rendered in the
        # side panel and used by the metric filters below. Empty {} if absent.
        "metrics": rec.get("metrics") or {},
    }

    # Hoist the flat, filterable metric props that build_evidence_graph.py wrote
    # onto the node top-level so the side panel + metric filters can use them
    # without walking the nested metrics object.
    for prop in FLAT_METRIC_PROPS:
        if prop in rec:
            slim[prop] = rec.get(prop)

    return slim


def slim_edge(rec):
    """Project a full evidence_edge into the compact record the viz needs.

    Track A<->B bridge edges (topic_gene/topic_pathway/topic_disease) carry the
    structured-join ``method`` + ``confidence`` and a ``provenance`` object (the
    exact join key + counts). We keep these so the side panel can render HOW and
    WHY the link was made, and so the bridge edges are inspectable/queryable in
    the browser. Track-B-internal edges simply omit method/confidence.
    """
    slim = {
        "id": rec.get("edge_id"),
        "source": rec.get("source_id"),
        "target": rec.get("target_id"),
        "type": rec.get("edge_type"),
        "evidence": rec.get("evidence"),
        "score": rec.get("score"),
    }
    if rec.get("method") is not None:
        slim["method"] = rec.get("method")
    if rec.get("confidence") is not None:
        slim["confidence"] = rec.get("confidence")
    prov = rec.get("provenance")
    if prov:
        slim["provenance"] = prov
    return slim


# ---------------------------------------------------------------------------
# Filter-option discovery (so the HTML controls match the actual data)
# ---------------------------------------------------------------------------

def build_meta(nodes, manifest):
    """Derive the distinct filter values + colour maps the UI needs."""
    disease_groups = set()
    groups = set()
    phases = set()
    type_counts = {}
    score_min = None
    score_max = None

    for n in nodes:
        type_counts[n["type"]] = type_counts.get(n["type"], 0) + 1
        for dg in n["disease_groups"]:
            if dg:
                disease_groups.add(dg)
        if n["group"]:
            groups.add(n["group"])
        for ph in n["phases"]:
            if ph:
                phases.add(ph)
        s = n["score"]
        if s is not None:
            s = float(s)
            score_min = s if score_min is None else min(score_min, s)
            score_max = s if score_max is None else max(score_max, s)

    # Present trial phases earliest-first, with any un-phased trials grouped.
    phase_order = ["EARLY_PHASE1", "PHASE1", "PHASE2", "PHASE3", "PHASE4", "NA"]
    ordered_phases = [p for p in phase_order if p in phases]
    ordered_phases += sorted(p for p in phases if p not in phase_order)

    # Does the payload actually carry per-entity metrics? (Drives whether the
    # metric filter controls render at all.)
    has_metrics = any(n.get("metrics") for n in nodes)

    return {
        "node_colors": NODE_COLORS,
        "node_type_labels": NODE_TYPE_LABELS,
        "edge_colors": EDGE_COLORS,
        "default_on_types": DEFAULT_ON_TYPES,
        "translation_gap_flag": TRANSLATION_GAP_FLAG,
        "node_types": [t for t in NODE_COLORS if t in type_counts],
        "type_counts": type_counts,
        "disease_groups": sorted(disease_groups),
        "groups": sorted(groups),
        "phases": ordered_phases,
        "score_min": 0.0 if score_min is None else round(score_min, 4),
        "score_max": 1.0 if score_max is None else round(score_max, 4),
        "current_year": CURRENT_YEAR,
        "recent_year_from": CURRENT_YEAR - 2,
        "has_metrics": has_metrics,
        "manifest": manifest,
    }


# ---------------------------------------------------------------------------
# Emit graph_data.js
# ---------------------------------------------------------------------------

def write_graph_data_js(nodes, edges, meta):
    """Write window.GRAPH = {nodes, edges, meta} as a plain JS assignment."""
    payload = {"nodes": nodes, "edges": edges, "meta": meta}
    # Compact separators keep the file small; it is still valid, parseable JS.
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    DATA_JS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = DATA_JS_PATH.with_name(DATA_JS_PATH.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write("// Auto-generated by translational-evidence/viz/build_graph_viz.py\n")
        fh.write("// Do not edit by hand. Assigns the full evidence graph to window.GRAPH.\n")
        fh.write("window.GRAPH = ")
        fh.write(body)
        fh.write(";\n")
    import os
    os.replace(str(tmp), str(DATA_JS_PATH))


# ---------------------------------------------------------------------------
# Emit evidence_graph.html
# ---------------------------------------------------------------------------

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Track B - Translational Evidence Graph Explorer</title>
<style>
  :root {
    --bg: #0f1115; --panel: #171a21; --panel2: #1f232c; --line: #2a2f3a;
    --fg: #e6e8ec; --muted: #9aa3b2; --accent: #4da3ff;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; height: 100%; background: var(--bg); color: var(--fg);
    font: 13px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
  #app { display: flex; height: 100vh; width: 100vw; overflow: hidden; }
  #sidebar { width: 320px; min-width: 320px; height: 100%; overflow-y: auto;
    background: var(--panel); border-right: 1px solid var(--line); padding: 14px; }
  #stage { position: relative; flex: 1 1 auto; height: 100%; }
  #sigma { position: absolute; inset: 0; }
  #panel { position: absolute; top: 12px; right: 12px; width: 320px; max-height: calc(100% - 24px);
    overflow-y: auto; background: var(--panel); border: 1px solid var(--line); border-radius: 8px;
    padding: 12px; display: none; box-shadow: 0 6px 24px rgba(0,0,0,0.4); }
  #counts { position: absolute; left: 12px; bottom: 12px; background: rgba(23,26,33,0.9);
    border: 1px solid var(--line); border-radius: 6px; padding: 6px 10px; color: var(--muted);
    font-variant-numeric: tabular-nums; }
  #loading { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
    color: var(--muted); font-size: 15px; }
  h1 { font-size: 15px; margin: 0 0 2px; }
  h2 { font-size: 12px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted);
    margin: 16px 0 6px; border-top: 1px solid var(--line); padding-top: 10px; }
  .sub { color: var(--muted); font-size: 11px; margin: 0 0 8px; }
  .note { color: var(--muted); font-size: 11px; margin: 4px 0 0; }
  label.row { display: flex; align-items: center; gap: 8px; padding: 2px 0; cursor: pointer; }
  label.row input { accent-color: var(--accent); }
  .swatch { width: 11px; height: 11px; border-radius: 50%; flex: 0 0 auto; }
  .count { margin-left: auto; color: var(--muted); font-variant-numeric: tabular-nums; }
  select, input[type=text] { width: 100%; background: var(--panel2); color: var(--fg);
    border: 1px solid var(--line); border-radius: 6px; padding: 6px 8px; font: inherit; }
  select[multiple] { height: 120px; }
  input[type=range] { width: 100%; accent-color: var(--accent); }
  .flexrow { display: flex; gap: 8px; align-items: center; }
  .btn { width: 100%; background: var(--accent); color: #06121f; border: 0; border-radius: 6px;
    padding: 8px; font: inherit; font-weight: 600; cursor: pointer; margin-top: 10px; }
  .btn.secondary { background: var(--panel2); color: var(--fg); border: 1px solid var(--line); }
  .kv { display: grid; grid-template-columns: auto 1fr; gap: 2px 10px; font-size: 12px; }
  .kv dt { color: var(--muted); white-space: nowrap; }
  .kv dd { margin: 0; word-break: break-word; }
  .flag { display: inline-block; background: #3a1d1d; color: #ffb4b4; border: 1px solid #7a2c2c;
    border-radius: 4px; padding: 1px 6px; font-size: 11px; margin-left: 6px; }
  pre { background: var(--panel2); border: 1px solid var(--line); border-radius: 6px; padding: 8px;
    overflow-x: auto; font-size: 11px; white-space: pre-wrap; word-break: break-word; }
  .close { float: right; cursor: pointer; color: var(--muted); font-size: 16px; }
  .warn { color: #ffb4b4; }
  code { background: var(--panel2); padding: 1px 4px; border-radius: 4px; }
</style>
</head>
<body>
<div id="app">
  <div id="sidebar">
    <h1>Translational Evidence Graph</h1>
    <p class="sub">Track B - the FULL graph. Use the filters to make it legible;
      nothing is capped.</p>
    <p class="note">Needs internet: sigma.js + graphology load from a CDN.</p>
    <p class="note">Click a node for its scores/provenance; click a Track A&harr;B
      <em>bridge edge</em> (topic&rarr;gene/pathway/disease) to see its
      <code>method</code> + <code>confidence</code> + join provenance.</p>

    <h2>Node types</h2>
    <div id="typeFilters"></div>

    <h2>Disease group</h2>
    <select id="diseaseFilter" multiple></select>
    <p class="note">Nothing selected = all disease groups.</p>

    <h2>Pathway / mechanism group</h2>
    <select id="groupFilter">
      <option value="">All groups</option>
    </select>

    <h2>Trial phase</h2>
    <select id="phaseFilter">
      <option value="">All phases</option>
    </select>
    <p class="note">Only constrains trial nodes (enable "Trial" above).</p>

    <h2>Min score</h2>
    <div class="flexrow">
      <input type="range" id="scoreFilter" min="0" max="1" step="0.01" value="0" />
      <span id="scoreVal" style="min-width:34px;text-align:right;">0.00</span>
    </div>
    <p class="note">Hides scored nodes below the threshold. Nodes without a score
      are always kept.</p>

    <div id="metricControls" style="display:none;">
      <h2>Metrics (gene / variant / pathway)</h2>
      <p class="note">Filters over the per-entity metrics layer. They only
        constrain node types that carry the metric; all other nodes pass through.</p>

      <label class="row" style="margin-top:6px;">Min stopped-trial ratio</label>
      <div class="flexrow">
        <input type="range" id="stoppedFilter" min="0" max="1" step="0.01" value="0" />
        <span id="stoppedVal" style="min-width:34px;text-align:right;">0.00</span>
      </div>
      <p class="note">Keeps gene/pathway nodes whose <code>stopped_ratio</code>
        &ge; the threshold (nodes without the metric are kept).</p>

      <label class="row"><input type="checkbox" id="recentOnly" />
        <span>Recent activity only</span></label>
      <p class="note" id="recentNote">Keeps gene/variant/pathway nodes with a
        recent GWAS/trial (year &ge; <span id="recentYear"></span>).</p>
    </div>

    <h2>Search</h2>
    <input type="text" id="searchBox" placeholder="Label contains... (e.g. APOE)" />
    <p class="note">Highlights + zooms matches. Clears highlight when empty.</p>

    <button class="btn" id="resetBtn">Reset / show all</button>

    <h2>Legend</h2>
    <div id="legend"></div>
    <p class="note"><span class="flag">under-translated</span> pathways
      (translation_gap &ge; <span id="gapThresh"></span>) get a red ring.</p>
  </div>

  <div id="stage">
    <div id="sigma"></div>
    <div id="loading">Loading libraries + graph...</div>
    <div id="counts">nodes 0 / 0 &middot; edges 0 / 0</div>
    <div id="panel">
      <span class="close" id="panelClose">&times;</span>
      <div id="panelBody"></div>
    </div>
  </div>
</div>

<!-- Data first (plain script tag -> works under file://; sets window.GRAPH) -->
<script src="graph_data.js"></script>
<!-- Libraries from CDN (needs internet) -->
<script src="https://cdn.jsdelivr.net/npm/graphology@0.25.4/dist/graphology.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/sigma@2.4.0/build/sigma.min.js"></script>
<script>
(function () {
  "use strict";
  var loading = document.getElementById("loading");
  function fail(msg) { loading.innerHTML = '<span class="warn">' + msg + '</span>'; }

  if (!window.GRAPH) { fail("graph_data.js did not load (window.GRAPH missing)."); return; }
  if (!window.graphology || !window.Sigma) {
    fail("CDN libraries failed to load. This page needs internet for sigma.js + graphology.");
    return;
  }

  var DATA = window.GRAPH;
  var META = DATA.meta || {};
  var NODES = DATA.nodes || [];
  var EDGES = DATA.edges || [];
  var COLORS = META.node_colors || {};
  var EDGE_COLORS = META.edge_colors || {};
  var TYPE_LABELS = META.node_type_labels || {};
  var GAP_FLAG = META.translation_gap_flag != null ? META.translation_gap_flag : 0.3;
  document.getElementById("gapThresh").textContent = GAP_FLAG.toFixed(2);

  // --- Node size by score -------------------------------------------------
  function nodeSize(n) {
    var s = (n.score == null) ? 0.15 : n.score;   // unscored -> small but visible
    return 2 + Math.sqrt(Math.max(0, s)) * 8;      // 2..10 px
  }

  // --- Build the graphology graph ----------------------------------------
  var g = new graphology.Graph({ type: "directed", multi: true });
  var nodeById = Object.create(null);

  NODES.forEach(function (n) {
    nodeById[n.id] = n;
    var color = COLORS[n.type] || "#888";
    g.addNode(n.id, {
      x: n.x, y: -n.y,                 // flip y so higher score reads "up"
      size: nodeSize(n),
      label: n.label,
      color: color,
      baseColor: color,
      nodeType: n.type,
      dataRef: n,
      // Under-translated pathways get a visible red ring via a border-ish halo.
      type: n.under_translated ? "circle" : "circle"
    });
  });

  var edgeSkipped = 0;
  EDGES.forEach(function (e) {
    if (!(e.source in nodeById) || !(e.target in nodeById)) { edgeSkipped++; return; }
    try {
      g.addEdgeWithKey(e.id, e.source, e.target, {
        size: 0.6,
        color: EDGE_COLORS[e.type] || "rgba(150,150,150,0.15)",
        edgeType: e.type,
        dataRef: e
      });
    } catch (err) { edgeSkipped++; }
  });

  // --- Renderer -----------------------------------------------------------
  loading.style.display = "none";
  var renderer = new Sigma(g, document.getElementById("sigma"), {
    renderEdges: true,
    enableEdgeEvents: true,   // needed for clickEdge (bridge-edge side panel)
    enableEdgeClickEvents: true,
    labelRenderedSizeThreshold: 8,
    labelDensity: 0.6,
    labelGridCellSize: 120,
    defaultEdgeColor: "rgba(150,150,150,0.15)",
    zIndex: true,
    minCameraRatio: 0.02,
    maxCameraRatio: 20
  });

  // Draw a red ring around under-translated pathway nodes using the node
  // reducer + an after-render hook on the WebGL canvas overlay.
  var underTranslated = NODES.filter(function (n) { return n.under_translated; })
                             .map(function (n) { return n.id; });

  // --- Filter state -------------------------------------------------------
  var RECENT_FROM = META.recent_year_from != null ? META.recent_year_from : 2024;
  var state = {
    types: {},                 // type -> bool
    diseases: new Set(),       // empty = all
    group: "",                 // "" = all
    phase: "",                 // "" = all
    minScore: 0,
    minStopped: 0,             // 0 = off; gene/pathway stopped_ratio threshold
    recentOnly: false,         // gene/variant/pathway recent-activity filter
    search: ""
  };
  (META.node_types || Object.keys(COLORS)).forEach(function (t) {
    state.types[t] = (META.default_on_types || []).indexOf(t) !== -1;
  });

  function nodeVisible(n) {
    if (!state.types[n.type]) return false;
    if (state.diseases.size > 0) {
      var ok = false;
      for (var i = 0; i < n.disease_groups.length; i++) {
        if (state.diseases.has(n.disease_groups[i])) { ok = true; break; }
      }
      if (!ok) return false;
    }
    if (state.group) {
      if (n.group !== state.group) return false;
    }
    if (state.phase && n.type === "trial") {
      if (!n.phases || n.phases.indexOf(state.phase) === -1) return false;
    }
    if (state.minScore > 0 && n.score != null && n.score < state.minScore) return false;
    // Metric: min stopped-trial ratio (gene/pathway carry stopped_ratio).
    if (state.minStopped > 0 && n.stopped_ratio != null &&
        n.stopped_ratio < state.minStopped) return false;
    // Metric: recent-activity only. A node passes if it has NO recency metric
    // (so drugs/trials/etc are unaffected) OR any recent count is >= 1.
    if (state.recentOnly) {
      var hasRecency = (n.n_recent_gwas != null) || (n.n_recent_trials != null) ||
                       (n.n_recent != null);
      if (hasRecency) {
        var recent = (n.n_recent_gwas || 0) + (n.n_recent_trials || 0) + (n.n_recent || 0);
        if (recent < 1) return false;
      }
    }
    return true;
  }

  var visibleCount = 0, visibleEdges = 0;
  function applyFilters() {
    visibleCount = 0;
    var hiddenSet = Object.create(null);
    g.forEachNode(function (id, attr) {
      var vis = nodeVisible(attr.dataRef);
      attr.hidden = !vis;
      if (vis) visibleCount++;
      hiddenSet[id] = !vis;
    });
    visibleEdges = 0;
    g.forEachEdge(function (id, attr, s, t) {
      var hide = hiddenSet[s] || hiddenSet[t];
      attr.hidden = hide;
      if (!hide) visibleEdges++;
    });
    updateCounts();
    renderer.refresh();
  }

  function updateCounts() {
    document.getElementById("counts").innerHTML =
      "nodes " + visibleCount.toLocaleString() + " / " + NODES.length.toLocaleString() +
      " &middot; edges " + visibleEdges.toLocaleString() + " / " + (EDGES.length - edgeSkipped).toLocaleString();
  }

  // --- Search highlight ---------------------------------------------------
  var searchMatches = new Set();
  function applySearch() {
    searchMatches = new Set();
    var q = state.search.trim().toLowerCase();
    if (q) {
      var firstMatch = null, best = null;
      g.forEachNode(function (id, attr) {
        if (attr.hidden) return;
        if ((attr.label || "").toLowerCase().indexOf(q) !== -1) {
          searchMatches.add(id);
          if (!firstMatch) firstMatch = { id: id, x: attr.x, y: attr.y };
          var d = attr.dataRef;
          if (!best || (d.score || 0) > (best.score || -1)) best = { id: id, x: attr.x, y: attr.y, score: d.score };
        }
      });
      var target = best || firstMatch;
      if (target) {
        renderer.getCamera().animate({ x: 0.5, y: 0.5, ratio: 0.15 }, { duration: 400 });
        // Center on the matched node's graph coords via viewport conversion.
        var vp = renderer.graphToViewport({ x: target.x, y: target.y });
        var cam = renderer.getCamera();
        var state2 = renderer.viewportToFramedGraph(vp);
        cam.animate({ x: state2.x, y: state2.y, ratio: 0.12 }, { duration: 500 });
      }
    }
    renderer.refresh();
  }

  // Node reducer: dim non-matches when searching; keep colours otherwise.
  renderer.setSetting("nodeReducer", function (id, data) {
    var res = Object.assign({}, data);
    if (searchMatches.size > 0) {
      if (searchMatches.has(id)) {
        res.color = "#ffffff";
        res.size = Math.max(data.size, 8);
        res.zIndex = 2;
        res.forceLabel = true;
      } else {
        res.color = "rgba(120,120,130,0.25)";
        res.zIndex = 0;
      }
    }
    return res;
  });

  // --- Under-translated pathway red ring (custom overlay draw) ------------
  renderer.on("afterRender", function () {
    if (!underTranslated.length) return;
    var ctx = renderer.getCanvases && renderer.getCanvases().mouse
      ? renderer.getCanvases().mouse.getContext("2d") : null;
    // Fallback: use the "hovers" canvas 2d context for the ring overlay.
    var canvases = renderer.getCanvases ? renderer.getCanvases() : null;
    var c = canvases && (canvases.hovers || canvases.mouse);
    if (!c) return;
    var g2 = c.getContext("2d");
    // Only clear+redraw if nothing is being hovered (avoid clobbering hover).
    // We draw rings each frame; sigma clears hovers canvas itself between frames.
    underTranslated.forEach(function (id) {
      if (g.getNodeAttribute(id, "hidden")) return;
      var attr = g.getNodeAttributes(id);
      var vp = renderer.graphToViewport({ x: attr.x, y: attr.y });
      var size = renderer.scaleSize ? renderer.scaleSize(attr.size) : attr.size;
      g2.beginPath();
      g2.arc(vp.x, vp.y, (size || 4) + 3, 0, 2 * Math.PI);
      g2.strokeStyle = "#ff4d4d";
      g2.lineWidth = 2;
      g2.stroke();
    });
  });

  // --- Side panel ---------------------------------------------------------
  var panel = document.getElementById("panel");
  var panelBody = document.getElementById("panelBody");
  document.getElementById("panelClose").onclick = function () { panel.style.display = "none"; };

  function fmtScore(v) {
    if (v == null) return "-";
    return (typeof v === "number") ? v.toFixed(4).replace(/0+$/, "").replace(/\.$/, "") : String(v);
  }
  function escapeHtml(s) {
    return String(s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  function showPanel(n) {
    var rows = "";
    rows += '<dt>Type</dt><dd>' + escapeHtml(TYPE_LABELS[n.type] || n.type) + '</dd>';
    rows += '<dt>ID</dt><dd><code>' + escapeHtml(n.id) + '</code></dd>';
    if (n.group) rows += '<dt>Group</dt><dd>' + escapeHtml(n.group) + '</dd>';
    if (n.disease_groups && n.disease_groups.length)
      rows += '<dt>Disease</dt><dd>' + escapeHtml(n.disease_groups.join(", ")) + '</dd>';
    if (n.score != null) rows += '<dt>Score</dt><dd>' + fmtScore(n.score) + '</dd>';
    if (n.status) rows += '<dt>Status</dt><dd>' + escapeHtml(n.status) + '</dd>';
    if (n.phases && n.phases.length) rows += '<dt>Phases</dt><dd>' + escapeHtml(n.phases.join(", ")) + '</dd>';

    var scoreRows = "";
    if (n.scores) {
      Object.keys(n.scores).forEach(function (k) {
        scoreRows += '<dt>' + escapeHtml(k) + '</dt><dd>' + fmtScore(n.scores[k]) + '</dd>';
      });
    }

    // Per-entity metrics: each dotted key maps to {value, source}. Render the
    // value inline; expose the 'source' provenance note via a tooltip (title).
    function fmtMetricValue(v) {
      if (v == null) return "-";
      if (Array.isArray(v)) return v.length ? escapeHtml(v.join(", ")) : "[]";
      if (typeof v === "boolean") return v ? "true" : "false";
      if (typeof v === "number") return fmtScore(v);
      return escapeHtml(String(v));
    }
    var metricRows = "";
    if (n.metrics && Object.keys(n.metrics).length) {
      Object.keys(n.metrics).sort().forEach(function (k) {
        var m = n.metrics[k] || {};
        var src = m.source ? String(m.source) : "";
        metricRows += '<dt title="' + escapeHtml(src) + '">' + escapeHtml(k) + '</dt>' +
          '<dd title="' + escapeHtml(src) + '">' + fmtMetricValue(m.value) + '</dd>';
      });
    }

    var flag = n.under_translated
      ? ' <span class="flag">under-translated (gap ' + fmtScore(n.translation_gap) + ')</span>' : '';

    var html = '';
    html += '<span class="close" id="panelClose2">&times;</span>';
    html += '<h1 style="padding-right:18px;">' + escapeHtml(n.label) + flag + '</h1>';
    html += '<dl class="kv">' + rows + '</dl>';
    if (scoreRows) { html += '<h2>Scores</h2><dl class="kv">' + scoreRows + '</dl>'; }
    if (metricRows) {
      html += '<h2>Metrics</h2>';
      html += '<p class="note">Transparent per-entity signals; hover a row for its source. ' +
        'No verdicts baked in.</p>';
      html += '<dl class="kv">' + metricRows + '</dl>';
    }
    html += '<h2>Provenance</h2><pre>' + escapeHtml(JSON.stringify(n.provenance || {}, null, 2)) + '</pre>';
    panelBody.innerHTML = html;
    document.getElementById("panelClose2").onclick = function () { panel.style.display = "none"; };
    panel.style.display = "block";
  }

  // Edge side panel: surface the Track A<->B bridge join metadata (method,
  // confidence, provenance join key) so HOW + WHY a link was made is visible.
  var EDGE_TYPE_LABELS = {
    variant_gene: "Variant -> Gene",
    gene_pathway: "Gene -> Pathway",
    gene_disease: "Gene -> Disease",
    drug_pathway: "Drug -> Pathway",
    drug_gene: "Drug -> Gene (target)",
    trial_drug: "Trial -> Drug",
    trial_pathway: "Trial -> Pathway",
    topic_gene: "Topic -> Gene (bridge)",
    topic_pathway: "Topic -> Pathway (bridge)",
    topic_disease: "Topic -> Disease (bridge)"
  };

  function showEdgePanel(edata, srcNode, tgtNode) {
    var e = edata.dataRef || {};
    var rows = "";
    rows += '<dt>Relationship</dt><dd>' +
      escapeHtml(EDGE_TYPE_LABELS[e.type] || e.type || "edge") + '</dd>';
    if (srcNode) rows += '<dt>From</dt><dd>' + escapeHtml(srcNode.label) + '</dd>';
    if (tgtNode) rows += '<dt>To</dt><dd>' + escapeHtml(tgtNode.label) + '</dd>';
    if (e.evidence) rows += '<dt>Evidence</dt><dd>' + escapeHtml(e.evidence) + '</dd>';
    if (e.score != null) rows += '<dt>Score</dt><dd>' + fmtScore(e.score) + '</dd>';
    // Structured-join metadata (present on topic_* bridge edges).
    if (e.method) rows += '<dt>Method</dt><dd><code>' + escapeHtml(e.method) + '</code></dd>';
    if (e.confidence) rows += '<dt>Confidence</dt><dd>' + escapeHtml(e.confidence) + '</dd>';

    var html = '';
    html += '<span class="close" id="panelClose2">&times;</span>';
    var title = e.method ? "Bridge link" : "Edge";
    html += '<h1 style="padding-right:18px;">' + escapeHtml(title) + '</h1>';
    html += '<dl class="kv">' + rows + '</dl>';
    if (e.method) {
      html += '<p class="note">HOW + WHY this link was made. Structured ID joins ' +
        'are preferred over text/regex; <code>method</code> + <code>confidence</code> ' +
        'record the join, and provenance carries the exact join key. See ' +
        'LINK_METHODS.md.</p>';
    }
    if (e.provenance) {
      html += '<h2>Provenance</h2><pre>' +
        escapeHtml(JSON.stringify(e.provenance, null, 2)) + '</pre>';
    }
    panelBody.innerHTML = html;
    document.getElementById("panelClose2").onclick = function () { panel.style.display = "none"; };
    panel.style.display = "block";
  }

  renderer.on("clickNode", function (e) {
    var n = g.getNodeAttribute(e.node, "dataRef");
    if (n) showPanel(n);
  });
  renderer.on("clickEdge", function (e) {
    var attr = g.getEdgeAttributes(e.edge);
    var srcNode = nodeById[g.source(e.edge)];
    var tgtNode = nodeById[g.target(e.edge)];
    showEdgePanel(attr, srcNode, tgtNode);
  });
  renderer.on("clickStage", function () { /* keep panel open on stage click */ });

  // --- Build the control panel from META ---------------------------------
  // Node-type checkboxes.
  var typeWrap = document.getElementById("typeFilters");
  (META.node_types || Object.keys(COLORS)).forEach(function (t) {
    var cnt = (META.type_counts || {})[t] || 0;
    var lab = document.createElement("label");
    lab.className = "row";
    var cb = document.createElement("input");
    cb.type = "checkbox"; cb.checked = state.types[t]; cb.dataset.type = t;
    cb.onchange = function () { state.types[t] = cb.checked; applyFilters(); applySearch(); };
    var sw = document.createElement("span");
    sw.className = "swatch"; sw.style.background = COLORS[t] || "#888";
    var txt = document.createElement("span");
    txt.textContent = TYPE_LABELS[t] || t;
    var c = document.createElement("span");
    c.className = "count"; c.textContent = cnt.toLocaleString();
    lab.appendChild(cb); lab.appendChild(sw); lab.appendChild(txt); lab.appendChild(c);
    typeWrap.appendChild(lab);
  });

  // Disease multi-select.
  var dsel = document.getElementById("diseaseFilter");
  (META.disease_groups || []).forEach(function (d) {
    var o = document.createElement("option"); o.value = d; o.textContent = d;
    dsel.appendChild(o);
  });
  dsel.onchange = function () {
    state.diseases = new Set(Array.prototype.filter.call(dsel.options, function (o) { return o.selected; })
      .map(function (o) { return o.value; }));
    applyFilters(); applySearch();
  };

  // Group select.
  var gsel = document.getElementById("groupFilter");
  (META.groups || []).forEach(function (grp) {
    var o = document.createElement("option"); o.value = grp; o.textContent = grp;
    gsel.appendChild(o);
  });
  gsel.onchange = function () { state.group = gsel.value; applyFilters(); applySearch(); };

  // Phase select.
  var psel = document.getElementById("phaseFilter");
  (META.phases || []).forEach(function (ph) {
    var o = document.createElement("option"); o.value = ph; o.textContent = ph;
    psel.appendChild(o);
  });
  psel.onchange = function () { state.phase = psel.value; applyFilters(); applySearch(); };

  // Min-score slider.
  var ssel = document.getElementById("scoreFilter");
  var sval = document.getElementById("scoreVal");
  ssel.oninput = function () {
    state.minScore = parseFloat(ssel.value);
    sval.textContent = state.minScore.toFixed(2);
    applyFilters(); applySearch();
  };

  // Metric controls (only shown if the payload carries per-entity metrics).
  var stoppedSel = document.getElementById("stoppedFilter");
  var stoppedVal = document.getElementById("stoppedVal");
  var recentChk = document.getElementById("recentOnly");
  if (META.has_metrics) {
    document.getElementById("metricControls").style.display = "block";
    document.getElementById("recentYear").textContent = String(RECENT_FROM);
  }
  stoppedSel.oninput = function () {
    state.minStopped = parseFloat(stoppedSel.value);
    stoppedVal.textContent = state.minStopped.toFixed(2);
    applyFilters(); applySearch();
  };
  recentChk.onchange = function () {
    state.recentOnly = recentChk.checked;
    applyFilters(); applySearch();
  };

  // Search box (debounced).
  var searchBox = document.getElementById("searchBox");
  var searchTimer = null;
  searchBox.oninput = function () {
    state.search = searchBox.value;
    if (searchTimer) clearTimeout(searchTimer);
    searchTimer = setTimeout(applySearch, 180);
  };

  // Reset button.
  document.getElementById("resetBtn").onclick = function () {
    (META.node_types || Object.keys(COLORS)).forEach(function (t) {
      state.types[t] = (META.default_on_types || []).indexOf(t) !== -1;
    });
    Array.prototype.forEach.call(typeWrap.querySelectorAll("input[type=checkbox]"), function (cb) {
      cb.checked = state.types[cb.dataset.type];
    });
    state.diseases = new Set();
    Array.prototype.forEach.call(dsel.options, function (o) { o.selected = false; });
    state.group = ""; gsel.value = "";
    state.phase = ""; psel.value = "";
    state.minScore = 0; ssel.value = "0"; sval.textContent = "0.00";
    state.minStopped = 0; stoppedSel.value = "0"; stoppedVal.textContent = "0.00";
    state.recentOnly = false; recentChk.checked = false;
    state.search = ""; searchBox.value = "";
    applyFilters(); applySearch();
    renderer.getCamera().animatedReset();
  };

  // Legend.
  var legend = document.getElementById("legend");
  (META.node_types || Object.keys(COLORS)).forEach(function (t) {
    var row = document.createElement("label"); row.className = "row";
    var sw = document.createElement("span"); sw.className = "swatch"; sw.style.background = COLORS[t] || "#888";
    var txt = document.createElement("span"); txt.textContent = TYPE_LABELS[t] || t;
    row.appendChild(sw); row.appendChild(txt);
    legend.appendChild(row);
  });

  // --- Go -----------------------------------------------------------------
  applyFilters();
  applySearch();
})();
</script>
</body>
</html>
"""


def write_html():
    """Write the self-contained explorer HTML (loads graph_data.js + CDN libs)."""
    HTML_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = HTML_PATH.with_name(HTML_PATH.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write(HTML_TEMPLATE)
    import os
    os.replace(str(tmp), str(HTML_PATH))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    for p in (NODES_PATH, EDGES_PATH):
        if not p.exists():
            raise SystemExit(
                "missing input %s -- run "
                "translational-evidence/exports/build_evidence_graph.py first" % p
            )

    common.log("reading %s" % NODES_PATH)
    raw_nodes = common.read_jsonl(NODES_PATH)
    common.log("reading %s" % EDGES_PATH)
    raw_edges = common.read_jsonl(EDGES_PATH)

    manifest = {}
    if MANIFEST_PATH.exists():
        with MANIFEST_PATH.open("r", encoding="utf-8") as fh:
            manifest = json.load(fh)

    nodes = [slim_node(n) for n in raw_nodes]
    edges = [slim_edge(e) for e in raw_edges]

    # Drop any edge whose endpoint is not a node (defensive; builder already
    # dropped dangling edges, but never trust the payload the browser gets).
    node_ids = {n["id"] for n in nodes}
    kept_edges = [e for e in edges if e["source"] in node_ids and e["target"] in node_ids]
    dropped = len(edges) - len(kept_edges)
    if dropped:
        common.log("dropped %d dangling edge(s) during slimming" % dropped)

    meta = build_meta(nodes, manifest)

    write_graph_data_js(nodes, kept_edges, meta)
    write_html()

    common.log("wrote %s (%d bytes)" % (DATA_JS_PATH, DATA_JS_PATH.stat().st_size))
    common.log("wrote %s (%d bytes)" % (HTML_PATH, HTML_PATH.stat().st_size))
    common.log("nodes=%d edges=%d" % (len(nodes), len(kept_edges)))
    common.log("open with: open %s" % HTML_PATH)


if __name__ == "__main__":
    main()
