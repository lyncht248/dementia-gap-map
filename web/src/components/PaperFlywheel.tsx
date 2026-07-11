import { useEffect, useMemo, useRef, useState } from "react";

// The "paper flywheel": individual high-influence papers mapped onto the
// drug-discovery loop (Human anchor -> Cell state & mechanism -> Perturbation
// -> Clinical trial -> Results & feedback -> back to the anchor). Each dot is
// one paper, placed on the step it plays, coloured by the mechanism it argues
// for, and sized by influence (RCR). Click a paper to open its "role card":
// role / inputs / outputs / method (and whether it is FRO-able) / the
// assumption it makes about how dementia gets cured / how that assumption is
// challenged. Dashed arcs connect papers that question each other's assumptions.
//
// Loads its own curated dataset (paperflow.json), built by
// scripts/build_paperflow.py — the numbers come live from the corpus.

interface Step {
  id: string;
  label: string;
  question: string;
  inputs: string;
  outputs: string;
}
interface Theory {
  label: string;
  color: string;
}
interface PaperNode {
  id: string;
  pmid: string;
  step: string;
  theory: string;
  star: boolean;
  short: string;
  title: string;
  journal: string;
  year: number;
  rcr: number | null;
  url: string;
  genes: string[];
  n_trials: number;
  drugs: string[];
  role: string;
  inputs: string;
  outputs: string;
  method: string;
  fro: boolean;
  fro_note: string;
  assumption: string;
  challenge: string;
}
interface Edge {
  from: string;
  to: string;
  note: string;
}
interface PaperflowData {
  note: string;
  steps: Step[];
  theories: Record<string, Theory>;
  papers: PaperNode[];
  edges: Edge[];
}

// --- geometry --------------------------------------------------------------
const CX = 500;
const CY = 500;
const R_OUT = 388; // wheel rim
const R_IN = 150; // hub radius
const R_MID = 300; // node band centre
const R_BADGE = 408; // step number badge, just outside the rim
const R_TEXT = 452; // step label, radially beyond the badge
const SECTOR = 72; // 5 steps
// padded viewBox so the outer step labels never clip (wheel stays centred on 500,500)
const VBX = -150;
const VBY = -20;
const VBW = 1300;
const VBH = 1040;

// angle in degrees, measured clockwise from the top (12 o'clock)
function polar(r: number, deg: number) {
  const a = (deg * Math.PI) / 180;
  return { x: CX + r * Math.sin(a), y: CY - r * Math.cos(a) };
}
// annular-sector wedge path between two radii and two angles
function wedge(r0: number, r1: number, a0: number, a1: number) {
  const p0 = polar(r1, a0);
  const p1 = polar(r1, a1);
  const p2 = polar(r0, a1);
  const p3 = polar(r0, a0);
  const large = a1 - a0 > 180 ? 1 : 0;
  return [
    `M ${p0.x} ${p0.y}`,
    `A ${r1} ${r1} 0 ${large} 1 ${p1.x} ${p1.y}`,
    `L ${p2.x} ${p2.y}`,
    `A ${r0} ${r0} 0 ${large} 0 ${p3.x} ${p3.y}`,
    "Z",
  ].join(" ");
}
function nodeRadius(rcr: number | null) {
  const v = rcr ?? 2;
  return Math.max(9, Math.min(26, 8 + Math.sqrt(v) * 1.15));
}

// place each paper within its step's wedge: spread across the arc, staggered
// between an inner and outer radial band so neighbours never overlap.
function layout(papers: PaperNode[], steps: Step[]) {
  const stepIndex: Record<string, number> = {};
  steps.forEach((s, i) => (stepIndex[s.id] = i));
  const byStep: Record<string, PaperNode[]> = {};
  for (const p of papers) (byStep[p.step] ??= []).push(p);
  // biggest first so the anchor's heavyweight sits on the outer band
  for (const k of Object.keys(byStep)) byStep[k].sort((a, b) => (b.rcr ?? 0) - (a.rcr ?? 0));

  const pos: Record<string, { x: number; y: number; r: number; angle: number }> = {};
  for (const [step, list] of Object.entries(byStep)) {
    const centre = stepIndex[step] * SECTOR; // sector centre angle
    const n = list.length;
    const spread = n > 1 ? Math.min(28, 12 + n * 5) : 0;
    list.forEach((p, j) => {
      const t = n > 1 ? j / (n - 1) - 0.5 : 0; // -0.5..0.5
      const angle = centre + t * 2 * spread;
      const band = n > 2 ? (j % 2 === 0 ? -32 : 30) : n === 2 ? (j === 0 ? -20 : 22) : 0;
      const { x, y } = polar(R_MID + band, angle);
      pos[p.id] = { x, y, r: nodeRadius(p.rcr), angle };
    });
  }
  return pos;
}

export default function PaperFlywheel() {
  const [data, setData] = useState<PaperflowData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [hover, setHover] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [hoverStep, setHoverStep] = useState<string | null>(null);
  const [reduced, setReduced] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);
  const [tip, setTip] = useState<{ x: number; y: number; node: PaperNode } | null>(null);

  useEffect(() => {
    setReduced(window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false);
    fetch(`${import.meta.env.BASE_URL}atlas/paperflow.json`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`paperflow.json (${r.status})`))))
      .then((d: PaperflowData) => setData(d))
      .catch((e) => setError(String(e)));
  }, []);

  const pos = useMemo(() => (data ? layout(data.papers, data.steps) : {}), [data]);
  const byId = useMemo(() => new Map((data?.papers ?? []).map((p) => [p.id, p])), [data]);
  const sel = selected ? byId.get(selected) ?? null : null;

  // which challenge edges to light up: those touching the active (hover/select) node
  const active = hover ?? selected;
  const activeChallengers = useMemo(() => {
    if (!data || !active) return new Set<string>();
    const s = new Set<string>();
    for (const e of data.edges) {
      if (e.from === active) s.add(e.to);
      if (e.to === active) s.add(e.from);
    }
    return s;
  }, [data, active]);

  if (error)
    return (
      <div className="atlas-loading">
        <p>Could not load the paper flywheel.</p>
        <pre>{error}</pre>
      </div>
    );
  if (!data) return <div className="atlas-loading">Loading paper flywheel…</div>;

  const ticks = Array.from({ length: 72 }, (_, i) => i * 5);
  const boundaries = data.steps.map((_, i) => i * SECTOR - SECTOR / 2);

  const showTip = (node: PaperNode, e: React.MouseEvent) => {
    const rect = wrapRef.current?.getBoundingClientRect();
    setTip({ x: e.clientX - (rect?.left ?? 0), y: e.clientY - (rect?.top ?? 0), node });
  };

  return (
    <div className="pf-root" ref={wrapRef}>
      <svg viewBox={`${VBX} ${VBY} ${VBW} ${VBH}`} className="pf-svg" role="img"
        aria-label="Papers mapped onto the drug-discovery flywheel"
        onClick={() => setSelected(null)}>
        <defs>
          <radialGradient id="pf-hub" cx="50%" cy="42%" r="65%">
            <stop offset="0%" stopColor="#fdf4d8" />
            <stop offset="55%" stopColor="#f3e3ad" />
            <stop offset="100%" stopColor="#e6cf86" />
          </radialGradient>
          <radialGradient id="pf-rim" cx="50%" cy="50%" r="50%">
            <stop offset="88%" stopColor="#efe7cf" stopOpacity="0" />
            <stop offset="97%" stopColor="#e4d7ad" stopOpacity="0.55" />
            <stop offset="100%" stopColor="#cbb87f" stopOpacity="0.15" />
          </radialGradient>
          <marker id="pf-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7"
            markerHeight="7" orient="auto-start-reverse">
            <path d="M0,0 L10,5 L0,10 z" fill="#d24b4b" />
          </marker>
        </defs>

        {/* faint rim glow */}
        <circle cx={CX} cy={CY} r={R_OUT + 8} fill="url(#pf-rim)" />

        {/* rotating mechanical rim (decorative only) */}
        <g className="pf-rimspin">
          {!reduced && (
            <animateTransform attributeName="transform" attributeType="XML" type="rotate"
              from={`0 ${CX} ${CY}`} to={`360 ${CX} ${CY}`} dur="160s" repeatCount="indefinite" />
          )}
          <circle cx={CX} cy={CY} r={R_OUT} className="pf-rim-ring" />
          <circle cx={CX} cy={CY} r={R_OUT + 11} className="pf-rim-ring2" />
          {ticks.map((a) => {
            const p0 = polar(R_OUT, a);
            const p1 = polar(R_OUT + (a % 30 === 0 ? 11 : 6), a);
            return <line key={a} x1={p0.x} y1={p0.y} x2={p1.x} y2={p1.y} className="pf-tick" />;
          })}
        </g>

        {/* step wedges + headers (badge + label centred on the radial line) */}
        {data.steps.map((s, i) => {
          const a0 = i * SECTOR - SECTOR / 2;
          const a1 = i * SECTOR + SECTOR / 2;
          const on = hoverStep === s.id || (sel && sel.step === s.id);
          const badge = polar(R_BADGE, i * SECTOR);
          const txt = polar(R_TEXT, i * SECTOR);
          return (
            <g key={s.id} className="pf-steplab"
              onMouseEnter={() => setHoverStep(s.id)}
              onMouseLeave={() => setHoverStep((v) => (v === s.id ? null : v))}>
              <path d={wedge(R_IN + 4, R_OUT - 4, a0, a1)} className={`pf-wedge ${on ? "on" : ""}`} />
              <circle className="pf-stepnum" r="13" cx={badge.x} cy={badge.y} />
              <text className="pf-stepnum-t" x={badge.x} y={badge.y + 1}>{i + 1}</text>
              <text className={`pf-steptext ${on ? "on" : ""}`} x={txt.x} y={txt.y}>{s.label}</text>
            </g>
          );
        })}

        {/* sector dividers */}
        {boundaries.map((a) => {
          const p0 = polar(R_IN + 4, a);
          const p1 = polar(R_OUT - 4, a);
          return <line key={a} x1={p0.x} y1={p0.y} x2={p1.x} y2={p1.y} className="pf-divider" />;
        })}

        {/* challenge edges ("do other papers question those assumptions?") */}
        {data.edges.map((e, i) => {
          const a = pos[e.from];
          const b = pos[e.to];
          if (!a || !b) return null;
          const mx = (a.x + b.x) / 2;
          const my = (a.y + b.y) / 2;
          // bow the chord toward the hub for a clean arc
          const cx = CX + (mx - CX) * 0.35;
          const cy = CY + (my - CY) * 0.35;
          const lit = active === e.from || active === e.to;
          return (
            <path key={i} d={`M ${a.x} ${a.y} Q ${cx} ${cy} ${b.x} ${b.y}`}
              className={`pf-edge ${lit ? "lit" : ""}`} markerEnd="url(#pf-arrow)" />
          );
        })}

        {/* hub */}
        <circle cx={CX} cy={CY} r={R_IN} fill="url(#pf-hub)" className="pf-hubc" />
        <circle cx={CX} cy={CY} r={R_IN - 10} className="pf-hubinner" />
        {/* rotation chevrons round the hub */}
        {[30, 102, 174, 246, 318].map((a) => {
          const c = polar(R_IN - 3, a);
          return (
            <g key={a} transform={`translate(${c.x} ${c.y}) rotate(${a + 90})`}>
              <path d="M -5 -6 L 5 0 L -5 6" className="pf-chevron" />
            </g>
          );
        })}
        {hoverStep ? (
          <>
            <text x={CX} y={CY - 40} className="pf-hub-kicker">
              {`STEP ${data.steps.findIndex((s) => s.id === hoverStep) + 1} · ${data.steps.find((s) => s.id === hoverStep)?.label.toUpperCase()}`}
            </text>
            {wrapText(data.steps.find((s) => s.id === hoverStep)!.question, 22).map((ln, k) => (
              <text key={k} x={CX} y={CY - 8 + k * 20} className="pf-hub-q">{ln}</text>
            ))}
            <text x={CX} y={CY + 62} className="pf-hub-io">in: {data.steps.find((s) => s.id === hoverStep)!.inputs}</text>
            <text x={CX} y={CY + 80} className="pf-hub-io">out: {data.steps.find((s) => s.id === hoverStep)!.outputs}</text>
          </>
        ) : (
          <>
            <text x={CX} y={CY - 20} className="pf-hub-title">The paper flywheel</text>
            <text x={CX} y={CY + 4} className="pf-hub-sub">each turn makes</text>
            <text x={CX} y={CY + 22} className="pf-hub-sub">the next cheaper &amp; sharper</text>
            <text x={CX} y={CY + 52} className="pf-hub-hint">click a paper →</text>
          </>
        )}

        {/* paper nodes */}
        {data.papers.map((p) => {
          const pt = pos[p.id];
          if (!pt) return null;
          const color = data.theories[p.theory]?.color ?? "#888";
          const isSel = selected === p.id;
          const isHov = hover === p.id;
          const isChal = activeChallengers.has(p.id);
          const dim = (selected || hover) && !isSel && !isHov && !isChal;
          return (
            <g key={p.id} className={`pf-node ${dim ? "dim" : ""}`}
              onMouseEnter={(e) => { setHover(p.id); showTip(p, e); }}
              onMouseMove={(e) => showTip(p, e)}
              onMouseLeave={() => { setHover(null); setTip(null); }}
              onClick={(e) => { e.stopPropagation(); setSelected(p.id); }}>
              {(isSel || isHov || isChal) && (
                <circle cx={pt.x} cy={pt.y} r={pt.r + 6} className={`pf-halo ${isChal && !isSel && !isHov ? "chal" : ""}`}
                  style={{ stroke: isChal && !isSel && !isHov ? "#d24b4b" : color }} />
              )}
              <circle cx={pt.x} cy={pt.y} r={pt.r} fill={color} className="pf-dot" />
              {p.star && (
                <text x={pt.x} y={pt.y + 4} className="pf-star">★</text>
              )}
              <text x={pt.x} y={pt.y + pt.r + 13} className="pf-nodelabel">{p.short}</text>
            </g>
          );
        })}
      </svg>

      {/* hover tooltip */}
      {tip && !sel && (
        <div className="pf-tip" style={{ left: tip.x + 14, top: tip.y + 14 }}>
          <strong>{tip.node.short}</strong>
          <span className="pf-tip-meta">{tip.node.journal} · {tip.node.year}
            {tip.node.rcr != null && <> · RCR {tip.node.rcr}</>}</span>
          <span className="pf-tip-title">{tip.node.title}</span>
          <span className="pf-tip-hint">click for its role in the flywheel</span>
        </div>
      )}

      {/* legend */}
      <div className="pf-legend">
        <div className="pf-legend-row">
          {Object.entries(data.theories).map(([id, t]) => (
            <span key={id} className="pf-lchip"><i style={{ background: t.color }} />{t.label}</span>
          ))}
        </div>
        <div className="pf-legend-note">
          size = influence (RCR) · <span className="pf-edge-key">— —▸</span> questions the other's assumption
        </div>
      </div>

      {/* role card */}
      {sel && <RoleCard p={sel} data={data} onClose={() => setSelected(null)} onJump={setSelected} />}
    </div>
  );
}

function RoleCard({ p, data, onClose, onJump }: {
  p: PaperNode; data: PaperflowData; onClose: () => void; onJump: (id: string) => void;
}) {
  const theory = data.theories[p.theory];
  const step = data.steps.find((s) => s.id === p.step);
  const stepNo = data.steps.findIndex((s) => s.id === p.step) + 1;
  const challengers = data.edges
    .filter((e) => e.to === p.id)
    .map((e) => ({ node: data.papers.find((x) => x.id === e.from), note: e.note }))
    .filter((c): c is { node: PaperNode; note: string } => !!c.node);
  const challenges = data.edges
    .filter((e) => e.from === p.id)
    .map((e) => ({ node: data.papers.find((x) => x.id === e.to), note: e.note }))
    .filter((c): c is { node: PaperNode; note: string } => !!c.node);

  return (
    <aside className="pf-card" onClick={(e) => e.stopPropagation()}>
      <button className="pf-card-close" onClick={onClose} aria-label="Close">×</button>
      <div className="pf-card-step" style={{ color: theory?.color }}>
        STEP {stepNo} · {step?.label}
      </div>
      <h3 className="pf-card-title">
        {p.star && <span className="pf-card-star">★</span>}
        {p.short}
      </h3>
      <div className="pf-card-meta">
        <span className="pf-chip" style={{ background: theory?.color }}>{theory?.label}</span>
        {p.rcr != null && <span className="pf-chip ghost">RCR {p.rcr}</span>}
        <span className="pf-chip ghost">{p.journal} {p.year}</span>
      </div>
      <p className="pf-card-fulltitle">{p.title}</p>

      <dl className="pf-card-grid">
        <dt>Role</dt><dd>{p.role}</dd>
        <dt>Inputs</dt><dd>{p.inputs}</dd>
        <dt>Outputs</dt><dd>{p.outputs}</dd>
        <dt>Method</dt>
        <dd>
          {p.method}{" "}
          <span className={`pf-fro ${p.fro ? "yes" : "no"}`}>{p.fro ? "FRO-able" : "not an FRO target"}</span>
          {p.fro_note && <span className="pf-fro-note">{p.fro_note}</span>}
        </dd>
        <dt>Assumption</dt><dd className="pf-assume">{p.assumption}</dd>
        <dt>Challenged by</dt>
        <dd>
          {p.challenge}
          {challengers.length > 0 && (
            <div className="pf-chal-chips">
              {challengers.map((c) => (
                <button key={c.node.id} className="pf-chal-chip" title={c.note} onClick={() => onJump(c.node.id)}>
                  {c.node.short} ↗
                </button>
              ))}
            </div>
          )}
        </dd>
        {challenges.length > 0 && (
          <>
            <dt>Questions</dt>
            <dd>
              <div className="pf-chal-chips">
                {challenges.map((c) => (
                  <button key={c.node.id} className="pf-chal-chip q" title={c.note} onClick={() => onJump(c.node.id)}>
                    {c.node.short} ↗
                  </button>
                ))}
              </div>
            </dd>
          </>
        )}
      </dl>

      <div className="pf-card-foot">
        {p.genes.length > 0 && (
          <div className="pf-card-tags">
            <span className="pf-tag-label">Genes</span>
            {p.genes.slice(0, 8).map((g) => <span key={g} className="pf-gene">{g}</span>)}
            {p.genes.length > 8 && <span className="pf-gene more">+{p.genes.length - 8}</span>}
          </div>
        )}
        <div className="pf-card-tags">
          <span className="pf-tag-label">Trials</span>
          {p.n_trials > 0 ? (
            <>
              <span className="pf-gene">{p.n_trials} linked</span>
              {p.drugs.slice(0, 4).map((d) => <span key={d} className="pf-drug">{d.toLowerCase()}</span>)}
            </>
          ) : (
            <span className="pf-gene none">0 — before the wall</span>
          )}
        </div>
        <a className="pf-card-link" href={p.url} target="_blank" rel="noreferrer">Open on PubMed ↗</a>
      </div>
    </aside>
  );
}

// tiny word-wrapper for the SVG hub question (no foreignObject needed)
function wrapText(s: string, max: number): string[] {
  const words = s.split(" ");
  const lines: string[] = [];
  let cur = "";
  for (const w of words) {
    if ((cur + " " + w).trim().length > max) {
      lines.push(cur.trim());
      cur = w;
    } else cur = (cur + " " + w).trim();
  }
  if (cur) lines.push(cur);
  return lines.slice(0, 3);
}
