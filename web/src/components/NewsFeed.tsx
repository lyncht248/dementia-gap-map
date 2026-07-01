import { useMemo } from "react";
import type { Cluster, Paper } from "../types";

interface Props {
  selected: Paper[];
  clusters: Cluster[];
  onClear: () => void;
}

function tally(items: string[], limit = 8): [string, number][] {
  const m = new Map<string, number>();
  for (const it of items) m.set(it, (m.get(it) ?? 0) + 1);
  return [...m.entries()].sort((a, b) => b[1] - a[1]).slice(0, limit);
}

export default function NewsFeed({ selected, clusters, onClear }: Props) {
  const clusterById = useMemo(() => {
    const m = new Map<string, Cluster>();
    for (const c of clusters) m.set(c.topic_id, c);
    return m;
  }, [clusters]);

  const agg = useMemo(() => {
    const genes = tally(selected.flatMap((p) => p.genes));
    const clusterCounts = tally(selected.map((p) => p.cluster_id));
    const trials = tally(selected.flatMap((p) => p.trials));
    const pathways = tally(selected.map((p) => p.pathway_group));
    const years = selected.map((p) => p.year).sort((a, b) => a - b);
    const yearRange = years.length ? `${years[0]}–${years[years.length - 1]}` : "—";
    const clinical = selected.filter((p) => p.metrics.is_clinical).length;
    return { genes, clusterCounts, trials, pathways, yearRange, clinical };
  }, [selected]);

  const sorted = useMemo(
    () =>
      [...selected].sort(
        (a, b) => (b.metrics.citation_count ?? 0) - (a.metrics.citation_count ?? 0)
      ),
    [selected]
  );

  if (selected.length === 0) {
    return (
      <div className="feed feed-empty">
        <div className="feed-empty-inner">
          <strong>No selection yet.</strong>
          <p>
            Click <span className="pill-inline">Select region</span>, then draw a
            boundary around a group of papers on the map to see their attributes
            here as a feed.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="feed">
      <div className="feed-header">
        <div>
          <h2>{selected.length} papers selected</h2>
          <div className="feed-sub">
            Years {agg.yearRange} · {agg.clinical} clinically-oriented ·{" "}
            {agg.clusterCounts.length} topic{agg.clusterCounts.length !== 1 ? "s" : ""}
          </div>
        </div>
        <button className="btn ghost" onClick={onClear}>
          Clear selection
        </button>
      </div>

      <div className="feed-body">
        <aside className="feed-aside">
          <Section title="Topics">
            {agg.clusterCounts.map(([id, n]) => {
              const c = clusterById.get(id);
              return (
                <div key={id} className="chip-row">
                  <span className="dot" style={{ background: c?.color ?? "#999" }} />
                  <span className="chip-label">{c?.label ?? id}</span>
                  <span className="chip-count">{n}</span>
                </div>
              );
            })}
          </Section>
          <Section title="Top genes / loci">
            {agg.genes.length ? (
              <div className="tag-wrap">
                {agg.genes.map(([g, n]) => (
                  <span key={g} className="tag">
                    {g} <em>{n}</em>
                  </span>
                ))}
              </div>
            ) : (
              <span className="muted">none</span>
            )}
          </Section>
          <Section title="Pathway groups">
            <div className="tag-wrap">
              {agg.pathways.map(([p, n]) => (
                <span key={p} className="tag">
                  {p} <em>{n}</em>
                </span>
              ))}
            </div>
          </Section>
          <Section title="Linked trials / interventions">
            {agg.trials.length ? (
              <div className="tag-wrap">
                {agg.trials.map(([t, n]) => (
                  <span key={t} className="tag trial">
                    {t} <em>{n}</em>
                  </span>
                ))}
              </div>
            ) : (
              <span className="muted">no trial links in selection</span>
            )}
          </Section>
        </aside>

        <div className="feed-list">
          {sorted.map((p) => {
            const c = clusterById.get(p.cluster_id);
            return (
              <article key={p.paper_id} className="card">
                <div className="card-top">
                  <span className="dot" style={{ background: c?.color ?? "#999" }} />
                  <span className="card-topic">{c?.label ?? p.cluster_id}</span>
                  <span className="card-year">{p.year}</span>
                </div>
                <h3 className="card-title">
                  {p.url ? (
                    <a href={p.url} target="_blank" rel="noreferrer">
                      {p.title}
                    </a>
                  ) : (
                    p.title
                  )}
                </h3>
                <div className="card-authors">
                  {p.authors.slice(0, 3).join(", ")}
                  {p.authors.length > 3 ? " et al." : ""} · <em>{p.journal}</em>
                </div>
                <div className="card-meta">
                  <span title="Citation count">📈 {p.metrics.citation_count ?? 0} cites</span>
                  {p.metrics.relative_citation_ratio != null && (
                    <span title="Relative Citation Ratio">RCR {p.metrics.relative_citation_ratio}</span>
                  )}
                  {p.metrics.is_clinical && <span className="badge">clinical</span>}
                  {p.genes.slice(0, 3).map((g) => (
                    <span key={g} className="mini-tag">
                      {g}
                    </span>
                  ))}
                  {p.trials.map((t) => (
                    <span key={t} className="mini-tag trial">
                      {t}
                    </span>
                  ))}
                </div>
              </article>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="aside-section">
      <h4>{title}</h4>
      {children}
    </div>
  );
}
