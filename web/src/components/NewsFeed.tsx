import { useEffect, useMemo, useState } from "react";
import type { Cluster, Paper } from "../types";

interface Props {
  selected: Paper[];
  clusters: Cluster[];
  onClear: () => void;
}

/** Facet types the newsfeed can be filtered by. */
type Facet = "topic" | "gene" | "pathway" | "trial";
/** What the newsfeed lists. "papers" shows paper cards; the rest rank a facet. */
type ViewMode = "papers" | "gene" | "pathway" | "trial";

const FACETS: Facet[] = ["topic", "gene", "pathway", "trial"];

const FACET_LABEL: Record<Facet, string> = {
  topic: "Topics",
  gene: "Top genes / loci",
  pathway: "Pathway groups",
  trial: "Linked trials / interventions",
};

const VIEW_TABS: { mode: ViewMode; label: string }[] = [
  { mode: "papers", label: "Papers" },
  { mode: "gene", label: "Genes / loci" },
  { mode: "pathway", label: "Pathways" },
  { mode: "trial", label: "Trials" },
];

function tally(items: string[], limit = Infinity): [string, number][] {
  const m = new Map<string, number>();
  for (const it of items) m.set(it, (m.get(it) ?? 0) + 1);
  return [...m.entries()].sort((a, b) => b[1] - a[1]).slice(0, limit);
}

function emptyFilters(): Record<Facet, Set<string>> {
  return { topic: new Set(), gene: new Set(), pathway: new Set(), trial: new Set() };
}

export default function NewsFeed({ selected, clusters, onClear }: Props) {
  const [view, setView] = useState<ViewMode>("papers");
  const [filters, setFilters] = useState<Record<Facet, Set<string>>>(emptyFilters);

  const clusterById = useMemo(() => {
    const m = new Map<string, Cluster>();
    for (const c of clusters) m.set(c.topic_id, c);
    return m;
  }, [clusters]);

  // A stable signature of the current selection: reset filters + view whenever
  // the user draws a new region so stale facets don't leak across selections.
  const selectionSig = useMemo(
    () => selected.map((p) => p.paper_id).join(","),
    [selected]
  );
  useEffect(() => {
    setFilters(emptyFilters());
    setView("papers");
  }, [selectionSig]);

  // The facet values a paper carries. Genes/trials roll up from the paper's
  // topic (falling back to per-paper attribution), matching the counts shown.
  const valuesOf = useMemo(() => {
    return (p: Paper, facet: Facet): string[] => {
      const c = clusterById.get(p.cluster_id);
      switch (facet) {
        case "topic":
          return [p.cluster_id];
        case "gene":
          return c && c.top_genes.length ? c.top_genes : p.genes;
        case "pathway":
          return [p.pathway_group];
        case "trial":
          return c ? c.trials ?? p.trials : p.trials;
      }
    };
  }, [clusterById]);

  // A paper passes when, for every facet with an active filter (except `skip`),
  // it matches at least one selected value (OR within a facet, AND across).
  const passesExcept = useMemo(() => {
    return (p: Paper, skip: Facet | null): boolean => {
      for (const f of FACETS) {
        if (f === skip) continue;
        const sel = filters[f];
        if (!sel.size) continue;
        if (!valuesOf(p, f).some((v) => sel.has(v))) return false;
      }
      return true;
    };
  }, [filters, valuesOf]);

  const filtered = useMemo(
    () => selected.filter((p) => passesExcept(p, null)),
    [selected, passesExcept]
  );

  const anyFilter = FACETS.some((f) => filters[f].size > 0);

  // Emerging topics present in the (filtered) view, ranked by their topic-level
  // emergence score (burst + growth + influence, computed over the whole topic).
  const emerging = useMemo(
    () =>
      [...new Set(filtered.map((p) => p.cluster_id))]
        .map((id) => clusterById.get(id))
        .filter((c): c is Cluster => !!c && c.topic_id !== "other" && !!c.emergence)
        .sort((a, b) => b.emergence!.score - a.emergence!.score)
        .slice(0, 5),
    [filtered, clusterById]
  );

  const toggle = (facet: Facet, value: string) =>
    setFilters((prev) => {
      const next = { ...prev, [facet]: new Set(prev[facet]) };
      if (next[facet].has(value)) next[facet].delete(value);
      else next[facet].add(value);
      return next;
    });

  const switchView = (mode: ViewMode) => {
    setView(mode);
    // The facet being listed can't also filter itself, so drop its filter.
    if (mode !== "papers")
      setFilters((prev) => ({ ...prev, [mode]: new Set() }));
  };

  // Aside counts for a facet are computed over papers passing *other* filters,
  // so every option in the facet you're editing stays visible and selectable.
  const asideCounts = useMemo(() => {
    const out = {} as Record<Facet, [string, number][]>;
    for (const f of FACETS) {
      const pool = selected.filter((p) => passesExcept(p, f));
      out[f] = tally(pool.flatMap((p) => valuesOf(p, f)), 12);
    }
    return out;
  }, [selected, passesExcept, valuesOf]);

  const summary = useMemo(() => {
    const years = filtered.map((p) => p.year).sort((a, b) => a - b);
    const yearRange = years.length
      ? `${years[0]}–${years[years.length - 1]}`
      : "—";
    const clinical = filtered.filter((p) => p.metrics.is_clinical).length;
    const topics = new Set(filtered.map((p) => p.cluster_id)).size;
    return { yearRange, clinical, topics };
  }, [filtered]);

  const sortedPapers = useMemo(
    () =>
      [...filtered].sort(
        (a, b) =>
          (b.metrics.citation_count ?? 0) - (a.metrics.citation_count ?? 0)
      ),
    [filtered]
  );

  // Ranked facet list shown in the newsfeed for non-"papers" views.
  const ranked = useMemo(() => {
    if (view === "papers") return [];
    return tally(filtered.flatMap((p) => valuesOf(p, view as Facet)));
  }, [view, filtered, valuesOf]);

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
            {anyFilter
              ? `${filtered.length} of ${selected.length} shown · `
              : ""}
            Years {summary.yearRange} · {summary.clinical} clinically-oriented ·{" "}
            {summary.topics} topic{summary.topics !== 1 ? "s" : ""}
            {anyFilter && (
              <button
                className="sub-clear"
                onClick={() => setFilters(emptyFilters())}
              >
                clear filters
              </button>
            )}
          </div>
        </div>
        <div className="feed-header-right">
          <div className="segmented">
            {VIEW_TABS.map((t) => (
              <button
                key={t.mode}
                className={`seg ${view === t.mode ? "on" : ""}`}
                onClick={() => switchView(t.mode)}
              >
                {t.label}
              </button>
            ))}
          </div>
          <button className="btn ghost" onClick={onClear}>
            Clear selection
          </button>
        </div>
      </div>

      <div className="feed-body">
        <aside className="feed-aside">
          <div className="aside-section">
            <h4>Emerging topics</h4>
            {emerging.length ? (
              emerging.map((c) => {
                const e = c.emergence!;
                return (
                  <div key={c.topic_id} className="emerge-row">
                    <div className="emerge-head">
                      <span className="dot" style={{ background: c.color }} />
                      <span className="chip-label">{c.label}</span>
                      <span className="emerge-score">{Math.round(e.score * 100)}</span>
                    </div>
                    <div className="emerge-bar">
                      <span style={{ width: `${Math.round(e.score * 100)}%` }} />
                    </div>
                    <div className="emerge-meta">
                      {Math.round(e.pct_new * 100)}% recent · {e.growth}× growth · RCR{" "}
                      {e.mean_rcr}
                    </div>
                  </div>
                );
              })
            ) : (
              <span className="muted">no emerging topics in view</span>
            )}
          </div>
          {FACETS.map((f) => {
            // The facet currently listed in the newsfeed can't filter itself.
            if (view === (f as ViewMode)) return null;
            return (
              <FilterSection
                key={f}
                facet={f}
                counts={asideCounts[f]}
                active={filters[f]}
                clusterById={clusterById}
                onToggle={(v) => toggle(f, v)}
              />
            );
          })}
        </aside>

        {view === "papers" ? (
          <PaperList papers={sortedPapers} clusterById={clusterById} />
        ) : (
          <RankList facet={view as Facet} rows={ranked} />
        )}
      </div>
    </div>
  );
}

function FilterSection({
  facet,
  counts,
  active,
  clusterById,
  onToggle,
}: {
  facet: Facet;
  counts: [string, number][];
  active: Set<string>;
  clusterById: Map<string, Cluster>;
  onToggle: (value: string) => void;
}) {
  return (
    <div className="aside-section">
      <h4>{FACET_LABEL[facet]}</h4>
      {counts.length === 0 ? (
        <span className="muted">
          {facet === "trial" ? "no trial links here" : "none"}
        </span>
      ) : facet === "topic" ? (
        <div className="chip-list">
          {counts.map(([id, n]) => {
            const c = clusterById.get(id);
            return (
              <button
                key={id}
                className={`chip-row click ${active.has(id) ? "on" : ""}`}
                onClick={() => onToggle(id)}
                title={c?.label ?? id}
              >
                <span className="dot" style={{ background: c?.color ?? "#999" }} />
                <span className="chip-label">{c?.label ?? id}</span>
                <span className="chip-count">{n}</span>
              </button>
            );
          })}
        </div>
      ) : (
        <div className="tag-wrap">
          {counts.map(([v, n]) => (
            <button
              key={v}
              className={`tag ${facet === "trial" ? "trial" : ""} ${
                active.has(v) ? "on" : ""
              }`}
              onClick={() => onToggle(v)}
              title={v}
            >
              {facet === "trial" && v.length > 34 ? v.slice(0, 33) + "…" : v}{" "}
              <em>{n}</em>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function RankList({ facet, rows }: { facet: Facet; rows: [string, number][] }) {
  if (rows.length === 0) {
    return (
      <div className="rank-empty">
        No {FACET_LABEL[facet].toLowerCase()} in the current view.
      </div>
    );
  }
  const max = rows[0][1] || 1;
  return (
    <div className="rank-list">
      {rows.map(([name, n]) => (
        <div key={name} className="rank-row">
          <span className="rank-name" title={name}>
            {name}
          </span>
          <span className="rank-track">
            <span
              className={`rank-fill ${facet === "trial" ? "trial" : ""}`}
              style={{ width: `${Math.max(4, (n / max) * 100)}%` }}
            />
          </span>
          <span className="rank-count">{n}</span>
        </div>
      ))}
    </div>
  );
}

function PaperList({
  papers,
  clusterById,
}: {
  papers: Paper[];
  clusterById: Map<string, Cluster>;
}) {
  if (papers.length === 0) {
    return (
      <div className="rank-empty">No papers match the active filters.</div>
    );
  }
  return (
    <div className="feed-list">
      {papers.map((p) => {
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
              <span title="Citation count">
                📈 {p.metrics.citation_count ?? 0} cites
              </span>
              {p.metrics.relative_citation_ratio != null && (
                <span title="Relative Citation Ratio">
                  RCR {p.metrics.relative_citation_ratio}
                </span>
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
  );
}
