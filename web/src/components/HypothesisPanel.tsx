import type { AtlasHypothesis } from "../lib/atlasRender";

interface Props {
  hypotheses: AtlasHypothesis[];
  hidden: string[];
  unclassified: number;
  total: number;
  onToggle: (id: string) => void;
}

const pct = (v: number | null) => (v == null ? "—" : `${Math.round(v * 100)}%`);
const num = (v: number | null) => (v == null ? "—" : v.toFixed(2));

// The 8 mechanistic "Alzheimer's cure" bets, ranked by translation gap — the
// gap-map signal (strong genetics / biology, little clinical translation). Each
// row is both a legend entry (colour + metrics) and a filter (click to hide that
// mechanism on the map). The metrics are the Track B pathway rollup; note the
// paper count is a literature-footprint measure and is deliberately separate from
// the evidence gap (tau is the widest gap yet has almost no papers here).
export default function HypothesisPanel({
  hypotheses,
  hidden,
  unclassified,
  total,
  onToggle,
}: Props) {
  if (!hypotheses.length) return null;
  const classified = total - unclassified;
  return (
    <section className="hyp-panel" aria-label="Mechanistic hypotheses">
      <div className="hyp-head">
        <div>
          <h2>Alzheimer&rsquo;s cure hypotheses</h2>
          <p className="hyp-sub">
            8 mechanistic bets, ranked by <strong>translation gap</strong> — strong
            genetics or biology with little clinical translation. The map recolours
            the {classified.toLocaleString()} mechanism-linked papers by their
            dominant pathway; {unclassified.toLocaleString()} papers with no
            mechanism link stay grey. Click a row to hide it on the map.
          </p>
        </div>
      </div>
      <div className="hyp-table" role="table">
        <div className="hyp-row hyp-row-head" role="row">
          <span className="hyp-c-name">Hypothesis</span>
          <span className="hyp-c-num" title="mean genetic + functional support of the mechanism's genes">support</span>
          <span className="hyp-c-num" title="translation gap = support × (1 − clinical translation)">gap</span>
          <span className="hyp-c-num" title="mapped clinical trials">trials</span>
          <span className="hyp-c-num" title="genes in the mechanism">genes</span>
          <span className="hyp-c-num" title="papers on this map linked to the mechanism">papers</span>
        </div>
        {hypotheses.map((h) => {
          const off = hidden.includes(h.id);
          return (
            <button
              key={h.id}
              type="button"
              className={`hyp-row ${off ? "off" : ""}`}
              role="row"
              onClick={() => onToggle(h.id)}
              title={`${h.statement}${off ? "\n\n(hidden on map — click to show)" : "\n\n(click to hide on map)"}`}
            >
              <span className="hyp-c-name">
                <span className="hyp-sw" style={{ background: h.color }} />
                <span className="hyp-name">{h.label}</span>
              </span>
              <span className="hyp-c-num">{num(h.combined_support)}</span>
              <span className="hyp-c-num hyp-gap">{num(h.translation_gap)}</span>
              <span className="hyp-c-num">{h.trial_count}</span>
              <span className="hyp-c-num">{h.gene_count ?? "—"}</span>
              <span className="hyp-c-num hyp-muted">{h.count.toLocaleString()}</span>
            </button>
          );
        })}
      </div>
      <p className="hyp-foot">
        Clinical translation (share of the mechanism that has reached trials):{" "}
        {hypotheses
          .slice()
          .sort((a, b) => (b.clinical_translation ?? 0) - (a.clinical_translation ?? 0))
          .map((h, i, a) => (
            <span key={h.id}>
              {h.short} {pct(h.clinical_translation)}
              {i < a.length - 1 ? " · " : ""}
            </span>
          ))}
      </p>
    </section>
  );
}
