#!/usr/bin/env python3
"""Build the interactive "theme atlas" web view from the Qwen3-Embedding-8B run.

Reads the bake-off artifacts produced by ``scripts/embed_map.py`` for the
``qwen3-8b`` model and turns them into a self-contained, pan/zoom canvas atlas
(``web/atlas/index.html``) styled after a Nomic-Atlas-style theme map:

  * one hex-packed dot per paper (4,780 dots), no overlaps;
  * a two-tier topic hierarchy — a handful of **major** topics shown when zoomed
    out, and the fine-grained **minor** topics revealed as you zoom in.

Key design decision — *how we choose topics*
--------------------------------------------
The 45 clusters Qwen/HDBSCAN found mix several *classes* of thing (diseases,
methodologies, individual genes, biological themes). For the zoom-out view we
want the big labels to all be the **same class**, so we pick a single class that
the embedding geometry actually supports as coherent, contiguous regions:
**disease / neurological condition**.

Empirically the non-Alzheimer conditions (Parkinson's, ALS/FTD, Huntington's,
Lewy body, prion, MS, ophthalmic, vascular, psychiatric) fall out as tight,
spatially-separated islands, while Alzheimer's disease forms one large central
"continent". Each fine cluster is assigned to exactly one disease major; the
fine clusters themselves (Mendelian randomization, polygenic scores, microglia &
TREM2, fluid biomarkers, …) become the minor topics you see on zoom-in.

Run: ``python3 scripts/build_atlas.py``
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data/exports/visual/embeddings/qwen3-8b"
# Inside the web app's public/ dir so it is both openable directly (the page is
# fully self-contained) and served live at /atlas/ when the site deploys.
OUT_DIR = ROOT / "web/public/atlas"

# Hex-lattice spacing for the packed display coords (world units). Sized to the
# corpus's dense-core nearest-neighbour distance so the footprint is preserved.
PACK_SPACING = 0.032

# ---------------------------------------------------------------------------
# Minor topics: human-readable names for each of the 45 Qwen/HDBSCAN clusters.
# (Derived from each cluster's top TF-IDF terms in clusters.jsonl.)
# ---------------------------------------------------------------------------
MINOR_LABELS: dict[int, str] = {
    7: "Mendelian Randomization",
    19: "Parkinson's — SNCA / LRRK2",
    12: "ALS & Frontotemporal Dementia",
    14: "Alzheimer's Reviews & Genomics",
    21: "AD Pathology — Tau / Amyloid",
    37: "Neuroimaging Genetics",
    10: "Fluid Biomarkers (CSF / plasma)",
    42: "Polygenic Risk Scores",
    29: "Microglia & Neuroinflammation",
    31: "Multi-omics & Drug Repurposing",
    9: "DNA Methylation & Epigenetics",
    2: "Alzheimer's GWAS Meta-analyses",
    44: "Cognitive Function & Schizophrenia",
    33: "Epistasis & Gene Interactions",
    41: "East Asian AD Genetics",
    1: "Huntington's Disease",
    34: "AD Rare Variants (ABCA7)",
    25: "Cerebral Small-vessel Disease",
    32: "Ancestry-diverse AD Genetics",
    20: "Aging, Longevity & Telomeres",
    24: "Cardiometabolic Shared Risk",
    23: "GWAS Methods & Imputation",
    43: "Mild Cognitive Impairment",
    4: "Glaucoma & Macular Degeneration",
    27: "TREM2 & Microglial Receptors",
    3: "Multiple Sclerosis",
    15: "Neurodegeneration (overview)",
    26: "Cross-disorder eQTL & Pleiotropy",
    40: "Late-onset AD Linkage",
    5: "Sleep & Circadian Traits",
    6: "Gut Microbiome",
    8: "Copy-number & Structural Variation",
    13: "PSP & Atypical Parkinsonism",
    16: "Dementia Meta-analyses (ABCC9)",
    28: "Regulatory & Functional Variants",
    38: "Depression & Bipolar Genetics",
    18: "Lewy Body Dementia",
    11: "Hereditary Ataxia & Canine Models",
    17: "Parkinson's Cognitive Decline",
    0: "Prion Disease (CJD)",
    30: "Transcriptome-wide Association",
    39: "Clusterin (CLU) Association",
    35: "Complement Receptor (CR1)",
    22: "Splicing & Isoforms",
    36: "Machine-learning Classification",
}

# ---------------------------------------------------------------------------
# Major topics: single class = disease / neurological condition.
# Each entry: key -> (label, color). Fine clusters map into these below.
# Colors: one warm hue for the Alzheimer's continent, distinct saturated hues
# for the disease islands.
# ---------------------------------------------------------------------------
MAJORS: dict[str, tuple[str, str]] = {
    "alzheimer": ("Alzheimer's Disease & Dementia", "#E4794B"),
    "parkinson": ("Parkinson's Disease", "#E0A93B"),
    "als_ftd": ("ALS & Frontotemporal Dementia", "#C24C8E"),
    "lewy_atypical": ("Lewy Body & Atypical Parkinsonism", "#9070D0"),
    "huntington": ("Huntington's & Hereditary Ataxias", "#5FA8DE"),
    "vascular": ("Vascular & Small-vessel Dementia", "#3FB0A2"),
    "psychiatric": ("Psychiatric & Cognitive Traits", "#B98A34"),
    "ms": ("Multiple Sclerosis", "#63BE55"),
    "prion": ("Prion Disease", "#D9534F"),
    "ophthalmic": ("Ophthalmic Neurodegeneration", "#7E9A3C"),
}

# fine cluster id -> major key
CLUSTER_TO_MAJOR: dict[int, str] = {
    # Parkinson's
    19: "parkinson", 17: "parkinson",
    # ALS / FTD
    12: "als_ftd",
    # Lewy body & atypical parkinsonism (PSP/MSA/corticobasal)
    18: "lewy_atypical", 13: "lewy_atypical",
    # Huntington's & hereditary ataxias
    1: "huntington", 11: "huntington",
    # Vascular / small-vessel / cardiometabolic
    25: "vascular", 24: "vascular",
    # Psychiatric & cognitive / cross-disorder
    44: "psychiatric", 38: "psychiatric", 26: "psychiatric",
    # Multiple sclerosis
    3: "ms",
    # Prion
    0: "prion",
    # Ophthalmic
    4: "ophthalmic",
}
# everything else -> the Alzheimer's / dementia continent
for _c in MINOR_LABELS:
    CLUSTER_TO_MAJOR.setdefault(_c, "alzheimer")


def pack_force(xy, groups, spacing, iters=220):
    """Compress the cloud into dense, no-overlap disease blobs.

    Combines three forces, settled iteratively from the UMAP layout:
      * **collision** — push any two points closer than one dot apart away from
        each other (guarantees the no-overlap tiling look);
      * **group gravity** — pull each point gently toward its disease-major
        centroid, squeezing out the diffuse whitespace so each region reads as a
        solid colour mass while staying a separate island;
      * **anchor** — a faint pull to the point's own UMAP position, so the
        internal ordering of sub-topics survives.
    Deterministic (fixed initial positions, no RNG).
    """
    import numpy as np
    from scipy.spatial import cKDTree

    pos = np.asarray(xy, dtype=float).copy()
    anchor = pos.copy()
    groups = np.asarray(groups)
    r = spacing / 2.0

    # per-group centroids (recomputed each iter as blobs move)
    gids = np.unique(groups)
    for it in range(iters):
        # cool the packing forces over time for a clean settle
        t = it / iters
        g_grav = 0.05 * (1 - 0.5 * t)   # compact each region
        g_glob = 0.045 * (1 - 0.4 * t)  # draw regions together (close whitespace)
        g_anch = 0.006                  # retain internal sub-topic ordering

        gcen = pos.mean(0)
        cen = {g: pos[groups == g].mean(0) for g in gids}
        for g in gids:
            m = groups == g
            pos[m] += g_grav * (cen[g] - pos[m])
        pos += g_glob * (gcen - pos)
        pos += g_anch * (anchor - pos)

        # collision resolution
        tree = cKDTree(pos)
        pairs = tree.query_pairs(2 * r, output_type="ndarray")
        if len(pairs):
            a, b = pairs[:, 0], pairs[:, 1]
            d = pos[a] - pos[b]
            dist = np.sqrt((d ** 2).sum(1)) + 1e-9
            push = (2 * r - dist).clip(min=0) / 2.0
            unit = d / dist[:, None]
            disp = np.zeros_like(pos)
            np.add.at(disp, a, unit * push[:, None])
            np.add.at(disp, b, -unit * push[:, None])
            pos += disp
    return pos


def hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def rgb_to_hex(r: float, g: float, b: float) -> str:
    return "#%02x%02x%02x" % (round(r), round(g), round(b))


def lighten(hex_color: str, amt: float) -> str:
    """amt in [-1,1]; positive toward white, negative toward black."""
    r, g, b = hex_to_rgb(hex_color)
    if amt >= 0:
        r += (255 - r) * amt
        g += (255 - g) * amt
        b += (255 - b) * amt
    else:
        f = 1 + amt
        r *= f
        g *= f
        b *= f
    return rgb_to_hex(r, g, b)


def main() -> None:
    points = [json.loads(l) for l in (SRC / "points.jsonl").open()]
    clusters = {json.loads(l)["cluster"]: json.loads(l)
                for l in (SRC / "clusters.jsonl").open()}
    manifest = json.loads((SRC / "manifest.json").read_text())

    # Re-pack for a dense, no-overlap "theme atlas" look: squeeze whitespace out
    # of each disease region while keeping regions as separate islands. Write the
    # packed coords back onto each point as px/py (used for dots + label
    # centroids). Noise points ride along with their nearest disease major.
    grp_key = {**CLUSTER_TO_MAJOR}
    fallback_major = "alzheimer"
    grp_idx = {g: i for i, g in enumerate(MAJORS)}
    groups = []
    for p in points:
        c = p["cluster"]
        if c == -1:
            groups.append(-1)  # provisional; reassigned after nearest-fine below
        else:
            groups.append(grp_idx[grp_key.get(c, fallback_major)])
    # provisionally give noise the global-centroid group so they don't distort;
    # assign each noise point to the major of its nearest non-noise point.
    import numpy as _np
    from scipy.spatial import cKDTree as _KD
    XY = _np.array([(p["x"], p["y"]) for p in points])
    real = [i for i, p in enumerate(points) if p["cluster"] != -1]
    rtree = _KD(XY[real])
    for i, p in enumerate(points):
        if p["cluster"] == -1:
            j = real[int(rtree.query(XY[i])[1])]
            groups[i] = grp_idx[grp_key.get(points[j]["cluster"], fallback_major)]

    packed = pack_force([(p["x"], p["y"]) for p in points], groups, spacing=PACK_SPACING)
    for p, (qx, qy) in zip(points, packed):
        p["px"], p["py"] = float(qx), float(qy)

    by_cluster: dict[int, list] = defaultdict(list)
    for p in points:
        by_cluster[p["cluster"]].append(p)

    fine_ids = sorted(c for c in by_cluster if c != -1)

    # Per-fine-cluster colour: shade the major's base colour so that sub-regions
    # inside a major (esp. the big Alzheimer's continent) are gently
    # distinguishable while still reading as one colour family.
    major_members: dict[str, list[int]] = defaultdict(list)
    for c in fine_ids:
        major_members[CLUSTER_TO_MAJOR[c]].append(c)
    # order each major's clusters by centroid x so the shading forms a smooth ramp
    for mk, members in major_members.items():
        members.sort(key=lambda c: sum(p["px"] for p in by_cluster[c]) / len(by_cluster[c]))

    fine_color: dict[int, str] = {}
    for mk, members in major_members.items():
        base = MAJORS[mk][1]
        n = len(members)
        for i, c in enumerate(members):
            # spread lightness across ~[-0.12, +0.24] of the base hue
            t = 0 if n == 1 else i / (n - 1)
            fine_color[c] = lighten(base, -0.12 + 0.36 * t)

    # centroids (packed display coords) for label placement
    def centroid(pts):
        return (sum(p["px"] for p in pts) / len(pts),
                sum(p["py"] for p in pts) / len(pts))

    fine_records = []
    for c in fine_ids:
        cx, cy = centroid(by_cluster[c])
        fine_records.append({
            "id": c,
            "major": CLUSTER_TO_MAJOR[c],
            "label": MINOR_LABELS.get(c, clusters[c]["label"]),
            "color": fine_color[c],
            "x": round(cx, 3),
            "y": round(cy, 3),
            "count": len(by_cluster[c]),
        })

    major_records = []
    for mk, (label, color) in MAJORS.items():
        member_pts = [p for c in major_members[mk] for p in by_cluster[c]]
        if not member_pts:
            continue
        cx, cy = centroid(member_pts)
        major_records.append({
            "id": mk, "label": label, "color": color,
            "x": round(cx, 3), "y": round(cy, 3), "count": len(member_pts),
        })
    major_records.sort(key=lambda m: -m["count"])

    # Assign noise points (-1) to the nearest fine cluster centroid (2D) so the
    # map is fully coloured; they render at lower opacity and carry no label.
    fine_cx = {r["id"]: (r["x"], r["y"]) for r in fine_records}

    def nearest_fine(px, py):
        best, bd = None, 1e18
        for cid, (fx, fy) in fine_cx.items():
            d = (px - fx) ** 2 + (py - fy) ** 2
            if d < bd:
                bd, best = d, cid
        return best

    # points array: [px, py, fine_id, year, is_noise]
    pt_rows = []
    titles = []
    for p in points:
        c = p["cluster"]
        is_noise = 1 if c == -1 else 0
        if is_noise:
            c = nearest_fine(p["px"], p["py"])
        pt_rows.append([round(p["px"], 3), round(p["py"], 3), c, p["year"], is_noise])
        titles.append(p["title"])

    years = [p["year"] for p in points]
    data = {
        "meta": {
            "model": manifest.get("model_id", "Qwen/Qwen3-Embedding-8B"),
            "spacing": PACK_SPACING,
            "n_papers": len(points),
            "n_major": len(major_records),
            "n_minor": len(fine_records),
            "year_min": min(years),
            "year_max": max(years),
        },
        "majors": major_records,
        "minors": fine_records,
        "points": pt_rows,
        "titles": titles,
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "atlas.json").write_text(json.dumps(data, separators=(",", ":")))

    html = HTML_TEMPLATE.replace("__DATA__", json.dumps(data, separators=(",", ":")))
    (OUT_DIR / "index.html").write_text(html)

    print(f"wrote {OUT_DIR/'index.html'}")
    print(f"  {len(points)} papers | {len(major_records)} major topics | "
          f"{len(fine_records)} minor topics")
    for m in major_records:
        print(f"    {m['count']:>4}  {m['label']}")


# ---------------------------------------------------------------------------
# Self-contained web view (vanilla canvas; data inlined as __DATA__).
# ---------------------------------------------------------------------------
HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Dementia Gap Map · Theme Atlas</title>
<style>
  :root { --bg:#ffffff; --fg:#1b1b1f; --muted:#6b6b73; }
  * { box-sizing:border-box; }
  html,body { margin:0; height:100%; background:var(--bg); color:var(--fg);
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; }
  #wrap { position:fixed; inset:0; overflow:hidden; }
  canvas { display:block; width:100%; height:100%; cursor:grab; }
  canvas.drag { cursor:grabbing; }
  .panel { position:absolute; top:16px; left:18px; max-width:340px;
    background:rgba(255,255,255,.82); backdrop-filter:blur(6px);
    border:1px solid rgba(0,0,0,.06); border-radius:12px; padding:14px 16px;
    box-shadow:0 6px 24px rgba(0,0,0,.06); }
  .panel h1 { font-size:16px; margin:0 0 4px; letter-spacing:-.01em; }
  .panel p { font-size:12px; line-height:1.45; color:var(--muted); margin:0 0 10px; }
  .legend { display:flex; flex-direction:column; gap:5px; max-height:42vh; overflow:auto; }
  .legend .row { display:flex; align-items:center; gap:8px; font-size:12px; cursor:pointer;
    padding:2px 4px; border-radius:6px; }
  .legend .row:hover { background:rgba(0,0,0,.04); }
  .legend .row.off { opacity:.35; }
  .legend .sw { width:11px; height:11px; border-radius:50%; flex:none; }
  .legend .ct { margin-left:auto; color:var(--muted); font-variant-numeric:tabular-nums; }
  .hint { font-size:11px; color:var(--muted); margin-top:10px; }
  #tip { position:absolute; pointer-events:none; z-index:10; max-width:300px;
    background:rgba(20,20,24,.94); color:#fff; padding:8px 10px; border-radius:8px;
    font-size:12px; line-height:1.4; opacity:0; transition:opacity .08s; }
  #tip .t { font-weight:600; }
  #tip .m { color:#c9c9d2; margin-top:3px; font-size:11px; }
  #zoom { position:absolute; right:16px; bottom:16px; display:flex; gap:6px; }
  #zoom button { width:32px; height:32px; border-radius:8px; border:1px solid rgba(0,0,0,.1);
    background:rgba(255,255,255,.9); font-size:17px; cursor:pointer; color:var(--fg); }
  #zoom button:hover { background:#fff; }
</style>
</head>
<body>
<div id="wrap">
  <canvas id="c"></canvas>
  <div class="panel">
    <h1>Dementia Gap Map — Theme Atlas</h1>
    <p id="sub"></p>
    <div class="legend" id="legend"></div>
    <div class="hint">Scroll to zoom · drag to pan · zoom in for finer topics</div>
  </div>
  <div id="tip"></div>
  <div id="zoom"><button id="zout">–</button><button id="zin">+</button></div>
</div>
<script>
const DATA = __DATA__;
const cv = document.getElementById('c');
const ctx = cv.getContext('2d');
const tip = document.getElementById('tip');
let DPR = Math.min(window.devicePixelRatio || 1, 2);

// ---- world bounds -----------------------------------------------------------
const P = DATA.points;
let minx=1e9,maxx=-1e9,miny=1e9,maxy=-1e9;
for (const p of P){ if(p[0]<minx)minx=p[0]; if(p[0]>maxx)maxx=p[0];
  if(p[1]<miny)miny=p[1]; if(p[1]>maxy)maxy=p[1]; }
const worldW = maxx-minx, worldH = maxy-miny;
const cx0 = (minx+maxx)/2, cy0 = (miny+maxy)/2;

// hex-lattice spacing -> dot radius that tiles the lattice with a tiny gap
const spacing = DATA.meta.spacing || 0.032;
const dotR = spacing * 0.5;

const minorColor = {}, minorMajor = {}, minorLabel = {};
for (const m of DATA.minors){ minorColor[m.id]=m.color; minorMajor[m.id]=m.major; minorLabel[m.id]=m.label; }
const majorLabel = {}, majorColor = {};
for (const M of DATA.majors){ majorLabel[M.id]=M.label; majorColor[M.id]=M.color; }
const hidden = new Set();  // hidden major ids

// ---- view state -------------------------------------------------------------
let view = { s:1, tx:0, ty:0 };   // screen = world*s + t
function fit(){
  const w = cv.clientWidth, h = cv.clientHeight;
  const pad = 60;
  const s = Math.min((w-2*pad)/worldW, (h-2*pad)/worldH);
  view.s = s;
  view.tx = w/2 - cx0*s;
  view.ty = h/2 - cy0*s;   // note: y flipped below
}
function wx(x){ return x*view.s + view.tx; }
function wy(y){ return -y*view.s + (cv.clientHeight - view.ty); }  // flip y so up is +

function resize(){
  DPR = Math.min(window.devicePixelRatio || 1, 2);
  cv.width = cv.clientWidth*DPR; cv.height = cv.clientHeight*DPR;
  draw();
}

// baseline scale (fit) to gauge zoom level for label tiers
let baseS = 1;

function draw(){
  const w = cv.clientWidth, h = cv.clientHeight;
  ctx.setTransform(DPR,0,0,DPR,0,0);
  ctx.clearRect(0,0,w,h);

  const zoom = view.s / baseS;
  // dot grows with zoom but is capped so deep zoom reads as a clean lattice
  // (with gaps + labels) rather than giant overlapping blobs.
  const r = Math.min(Math.max(1.1, dotR*view.s), 7);

  // dots
  for (let i=0;i<P.length;i++){
    const p=P[i]; const fid=p[2];
    if (hidden.has(minorMajor[fid])) continue;
    const X=wx(p[0]), Y=wy(p[1]);
    if (X<-4||X>w+4||Y<-4||Y>h+4) continue;
    ctx.beginPath();
    ctx.arc(X,Y,r,0,6.2832);
    ctx.fillStyle = minorColor[fid];
    ctx.globalAlpha = p[4] ? 0.42 : 0.92;
    ctx.fill();
  }
  ctx.globalAlpha = 1;

  // labels — minor appear as you zoom in, major fade out
  const showMinor = zoom > 1.7;
  const majorAlpha = showMinor ? Math.max(0.12, 1-(zoom-1.7)/1.4) : 1;

  if (majorAlpha > 0.02){
    for (const M of DATA.majors){
      if (hidden.has(M.id)) continue;
      drawLabel(M.label, wx(M.x), wy(M.y), 15+Math.min(7,M.count/500),
                M.color, majorAlpha, true);
    }
  }
  if (showMinor){
    const a = Math.min(1, (zoom-1.7)/0.6);
    for (const m of DATA.minors){
      if (hidden.has(m.major)) continue;
      if (m.count < 40 && zoom < 3) continue;   // reveal small ones deeper in
      drawLabel(m.label, wx(m.x), wy(m.y), 12, '#23232a', a, false);
    }
  }
}

function drawLabel(text,x,y,size,color,alpha,bold){
  ctx.font = `${bold?'700':'600'} ${size}px -apple-system,Segoe UI,Roboto,sans-serif`;
  ctx.textAlign='center'; ctx.textBaseline='middle';
  ctx.globalAlpha = alpha;
  ctx.lineWidth = bold?4:3; ctx.strokeStyle='rgba(255,255,255,.92)';
  ctx.lineJoin='round';
  ctx.strokeText(text,x,y);
  ctx.fillStyle=color; ctx.fillText(text,x,y);
  ctx.globalAlpha=1;
}

// ---- interaction ------------------------------------------------------------
let drag=null;
cv.addEventListener('mousedown',e=>{ drag={x:e.clientX,y:e.clientY,tx:view.tx,ty:view.ty}; cv.classList.add('drag'); });
window.addEventListener('mouseup',()=>{ drag=null; cv.classList.remove('drag'); });
window.addEventListener('mousemove',e=>{
  if(drag){ view.tx = drag.tx + (e.clientX-drag.x); view.ty = drag.ty - (e.clientY-drag.y); draw(); hideTip(); return; }
  hover(e);
});
cv.addEventListener('wheel',e=>{
  e.preventDefault();
  const f = Math.exp(-e.deltaY*0.0015);
  zoomAt(e.clientX, e.clientY, f);
},{passive:false});

function zoomAt(sx,sy,f){
  // keep world point under cursor fixed
  const wxp=(sx-view.tx)/view.s, wyp=(cv.clientHeight-sy-view.ty)/view.s;
  view.s*=f;
  view.tx = sx - wxp*view.s;
  view.ty = (cv.clientHeight-sy) - wyp*view.s;
  draw();
}
document.getElementById('zin').onclick=()=>zoomAt(cv.clientWidth/2,cv.clientHeight/2,1.4);
document.getElementById('zout').onclick=()=>zoomAt(cv.clientWidth/2,cv.clientHeight/2,1/1.4);

function hover(e){
  const r = Math.max(2.5, dotR*view.s)+2;
  let best=-1,bd=r*r;
  for(let i=0;i<P.length;i++){
    const p=P[i]; if(hidden.has(minorMajor[p[2]])) continue;
    const dx=wx(p[0])-e.clientX, dy=wy(p[1])-e.clientY;
    const d=dx*dx+dy*dy; if(d<bd){bd=d;best=i;}
  }
  if(best<0){ hideTip(); return; }
  const p=P[best], fid=p[2];
  tip.innerHTML = `<div class="t">${esc(DATA.titles[best])}</div>`+
    `<div class="m">${p[3]} · ${minorLabel[fid]} · ${majorLabel[minorMajor[fid]]}</div>`;
  tip.style.opacity=1;
  const tx=Math.min(e.clientX+14, window.innerWidth-320);
  const ty=Math.min(e.clientY+14, window.innerHeight-90);
  tip.style.left=tx+'px'; tip.style.top=ty+'px';
}
function hideTip(){ tip.style.opacity=0; }
function esc(s){ return s.replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }

// ---- legend -----------------------------------------------------------------
const legend=document.getElementById('legend');
for(const M of DATA.majors){
  const row=document.createElement('div'); row.className='row';
  row.innerHTML=`<span class="sw" style="background:${M.color}"></span>`+
    `<span>${M.label}</span><span class="ct">${M.count}</span>`;
  row.onclick=()=>{ if(hidden.has(M.id))hidden.delete(M.id); else hidden.add(M.id);
    row.classList.toggle('off'); draw(); };
  legend.appendChild(row);
}
document.getElementById('sub').textContent =
  `${DATA.meta.n_papers.toLocaleString()} papers · ${DATA.meta.n_major} disease areas · `+
  `${DATA.meta.n_minor} sub-topics · ${DATA.meta.year_min}–${DATA.meta.year_max} · Qwen3-Embedding-8B`;

window.addEventListener('resize',()=>{ resize(); });
function boot(){ fit(); baseS=view.s; resize(); }
boot();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
