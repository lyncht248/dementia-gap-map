# In-browser SQL over Track B data (DuckDB-Wasm on Vercel)

Goal: run ad-hoc SQL against the Track B datasets **client-side in the deployed
Vercel app** — no backend, no Python. This is the "option 2" path.

## Why this works on Vercel
- Vercel serves static files with **HTTP range-request** support.
- **DuckDB-Wasm** runs in the browser and reads **Parquet over HTTP by range**, so a
  query downloads only the columns/rows it needs — not the whole file.
- We ship **Parquet** (not JSONL): columnar + compressed. `build_parquet.py` produces
  `data/exports/parquet/*.parquet` from the same JSONL, so the **same SQL runs locally
  (`query_te.py`) and in the browser**.

## 1. Serve the Parquet as static assets
Copy the Parquet into the web app's public dir at build time (Track A `web/`):
```
cp data/exports/parquet/*.parquet web/public/data/parquet/
```
(Or add a prebuild step. Parquet is small — see `parquet_manifest.json` for sizes.)

## 2. Add DuckDB-Wasm to the frontend
```
npm i @duckdb/duckdb-wasm
```
```ts
import * as duckdb from "@duckdb/duckdb-wasm";

let dbp: Promise<duckdb.AsyncDuckDB> | null = null;
async function getDB() {
  if (dbp) return dbp;
  dbp = (async () => {
    const bundle = await duckdb.selectBundle(duckdb.getJsDelivrBundles());
    const worker = new Worker(bundle.mainWorker!);
    const db = new duckdb.AsyncDuckDB(new duckdb.ConsoleLogger(), worker);
    await db.instantiate(bundle.mainModule, bundle.pthreadWorker);
    return db;
  })();
  return dbp;
}

// Query Parquet directly over HTTP (range requests; only fetches what's needed).
export async function q(sql: string) {
  const db = await getDB();
  const con = await db.connect();
  // register the base URL so `read_parquet('genes.parquet')` resolves
  const base = `${location.origin}${import.meta.env.BASE_URL}data/parquet/`;
  for (const name of ["genes","entity_metrics","pathways","trials","gwas_associations",
                      "functional_links","topic_evidence_links","nodes","edges",
                      "target_evidence","gene_pathways_api","drug_mechanism_api",
                      "topic_evidence_rollup"]) {
    await con.query(`CREATE VIEW IF NOT EXISTS ${name} AS
      SELECT * FROM read_parquet('${base}${name}.parquet')`);
  }
  const res = await con.query(sql);
  await con.close();
  return res.toArray().map((r) => r.toJSON());
}
```

## 3. Example queries (identical to local `query_te.py`)
```sql
-- genetically supported but clinically stalled
SELECT g.symbol, g.evidence_scores.genetic_support AS gen, m.metrics
FROM genes g JOIN entity_metrics m ON m.entity_id = g.gene_id
WHERE g.evidence_scores.genetic_support > 0.5
ORDER BY gen DESC LIMIT 20;

-- under-translated mechanisms
SELECT label, mechanism_group, scores.translation_gap AS gap
FROM pathways ORDER BY gap DESC;

-- dementia-vs-AD: genes with Lewy-body evidence
SELECT symbol FROM genes WHERE list_contains(disease_groups, 'lewy_body_dementia');
```

## Notes / gotchas
- **Same-origin** serving (Parquet under the app's own domain) avoids CORS. If cross-
  origin, the host must send `Access-Control-Allow-Origin` + allow `Range`.
- Add long cache headers for `*.parquet` (immutable build artifacts).
- Nested fields (`evidence_scores`, `metrics`, `interventions`, `sources`) are DuckDB
  STRUCT/LIST — use dot access and `unnest(...)`/`list_contains(...)`.
- For the biggest tables (`nodes`, `edges`, `gwas_associations`) prefer selecting
  specific columns so range requests stay small.
- Graph *traversal* (multi-hop) is still better in Neo4j; DuckDB-Wasm is for
  tabular/aggregate queries in the browser.

## Local parity
`python3 translational-evidence/exports/query_te.py "<sql>"` runs the exact same SQL
over the JSONL locally (and `build_parquet.py` keeps the Parquet in sync).
