import type { SelectedPaper } from "../lib/atlasRender";

function pubmed(id: string): string | null {
  const m = /^pmid:(\d+)$/.exec(id);
  return m ? `https://pubmed.ncbi.nlm.nih.gov/${m[1]}/` : null;
}

// Lists the papers inside the region the user drew on the atlas. Shows what the
// Track-A atlas knows about each paper (title / year / topic / citation links);
// gene / trial / evidence-score facets arrive once Track B data is joined in.
export default function AtlasFeed({
  selected,
  onClear,
}: {
  selected: SelectedPaper[];
  onClear: () => void;
}) {
  if (selected.length === 0) {
    return (
      <section className="feed">
        <strong>No selection yet.</strong>
        <p className="feed-hint">
          Click <span className="pill">Select region</span>, then draw a region around a group of
          papers on the map to list them here.
        </p>
      </section>
    );
  }

  const shown = selected.slice(0, 200);
  return (
    <section className="feed">
      <div className="feed-head">
        <strong>{selected.length} paper{selected.length === 1 ? "" : "s"} selected</strong>
        <button className="btn ghost" onClick={onClear}>Clear</button>
      </div>
      <ul className="feed-list">
        {shown.map((p) => {
          const url = pubmed(p.paper_id);
          return (
            <li key={p.i} className="feed-item">
              {url ? (
                <a href={url} target="_blank" rel="noreferrer" className="feed-title">{p.title}</a>
              ) : (
                <span className="feed-title">{p.title}</span>
              )}
              <div className="feed-meta">
                {p.year} · {p.minor} · {p.major} · {p.degree} citation link{p.degree === 1 ? "" : "s"}
              </div>
            </li>
          );
        })}
      </ul>
      {selected.length > shown.length && (
        <div className="feed-more">… and {selected.length - shown.length} more</div>
      )}
    </section>
  );
}
