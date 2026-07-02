// In-browser SQL over the Track B Parquet exports via DuckDB-Wasm.
//
// The agent's `query_data` tool runs here — no server round-trip for data.
// Parquet files are produced by scripts/build-agent-parquet.py into
// web/public/data/parquet/ and registered as views (genes, pathways, trials,
// gwas, functional_links, papers, clusters).
import * as duckdb from "@duckdb/duckdb-wasm";

export const TABLES = [
  "genes",
  "pathways",
  "trials",
  "gwas",
  "functional_links",
  "papers",
  "clusters",
  "entity_metrics",
  "target_evidence",
  "graph_nodes",
  "graph_edges",
] as const;

export interface QueryResult {
  columns: string[];
  rows: Record<string, unknown>[];
  rowCount: number; // rows actually returned (after cap)
  truncated: boolean;
}

const MAX_ROWS = 200;

let dbPromise: Promise<duckdb.AsyncDuckDB> | null = null;

async function initDb(): Promise<duckdb.AsyncDuckDB> {
  const bundles = duckdb.getJsDelivrBundles();
  const bundle = await duckdb.selectBundle(bundles);

  const workerUrl = URL.createObjectURL(
    new Blob([`importScripts("${bundle.mainWorker}");`], {
      type: "text/javascript",
    })
  );
  const worker = new Worker(workerUrl);
  const db = new duckdb.AsyncDuckDB(new duckdb.VoidLogger(), worker);
  await db.instantiate(bundle.mainModule, bundle.pthreadWorker);
  URL.revokeObjectURL(workerUrl);

  // Register each Parquet file by URL and expose it as a view.
  const origin = window.location.origin;
  const base = import.meta.env.BASE_URL;
  const conn = await db.connect();
  try {
    for (const t of TABLES) {
      const url = new URL(`${base}data/parquet/${t}.parquet`, origin).toString();
      await db.registerFileURL(
        `${t}.parquet`,
        url,
        duckdb.DuckDBDataProtocol.HTTP,
        false
      );
      await conn.query(
        `CREATE OR REPLACE VIEW ${t} AS SELECT * FROM parquet_scan('${t}.parquet')`
      );
    }
  } finally {
    await conn.close();
  }
  return db;
}

export function getDb(): Promise<duckdb.AsyncDuckDB> {
  if (!dbPromise) dbPromise = initDb();
  return dbPromise;
}

/** Kick off initialization early (e.g. on first render) without blocking. */
export function warmupDuckDb(): void {
  void getDb().catch(() => {
    /* surfaced later on first query */
  });
}

export type TableSchema = Record<string, { name: string; type: string }[]>;

let schemaPromise: Promise<TableSchema> | null = null;

/** Live column/type schema of each registered view (cached). Source of truth for
 * the agent — robust to Parquet schema changes without editing the prompt. */
export function getSchema(): Promise<TableSchema> {
  if (!schemaPromise) {
    schemaPromise = (async () => {
      const db = await getDb();
      const conn = await db.connect();
      const out: TableSchema = {};
      try {
        for (const t of TABLES) {
          const res = await conn.query(`DESCRIBE ${t}`);
          out[t] = res.toArray().map((r) => {
            const j = r.toJSON() as { column_name?: unknown; column_type?: unknown };
            return { name: String(j.column_name), type: String(j.column_type) };
          });
        }
      } finally {
        await conn.close();
      }
      return out;
    })();
  }
  return schemaPromise;
}

/** Compact one-line-per-table rendering of the live schema for the prompt. */
export async function schemaText(): Promise<string> {
  const s = await getSchema();
  return TABLES.map(
    (t) => `${t}(${(s[t] ?? []).map((c) => `${c.name} ${c.type}`).join(", ")})`
  ).join("\n");
}

function stripSqlComments(sql: string): string {
  return sql.replace(/\/\*[\s\S]*?\*\//g, " ").replace(/--[^\n]*/g, " ");
}

/** Reject anything that isn't a single read-only SELECT/WITH statement, so the
 * model can't CREATE/DROP/INSERT and mutate the registered views. */
export function assertSelectOnly(sql: string): void {
  const cleaned = stripSqlComments(sql).trim().replace(/;\s*$/, "");
  if (!cleaned) throw new Error("Empty query.");
  if (cleaned.includes(";")) {
    throw new Error("Only a single statement is allowed (no ';').");
  }
  if (!/^(select|with)\b/i.test(cleaned)) {
    throw new Error("Only read-only SELECT / WITH queries are allowed.");
  }
}

function normalize(v: unknown): unknown {
  if (v == null) return null;
  if (typeof v === "bigint") return Number(v);
  if (Array.isArray(v)) return v.map(normalize);
  // Arrow Vector (list columns) -> plain array
  if (typeof (v as { toArray?: unknown }).toArray === "function") {
    return Array.from(v as Iterable<unknown>).map(normalize);
  }
  return v;
}

/** Run a read-only SQL query. Returns row objects (BigInt/list-safe). */
export async function runSql(sql: string): Promise<QueryResult> {
  assertSelectOnly(sql);
  const db = await getDb();
  const conn = await db.connect();
  try {
    const table = await conn.query(sql);
    const columns = table.schema.fields.map((f) => f.name);
    const rows: Record<string, unknown>[] = [];
    let total = 0;
    for (const row of table) {
      total++;
      if (rows.length >= MAX_ROWS) continue;
      const obj: Record<string, unknown> = {};
      const json = row.toJSON() as Record<string, unknown>;
      for (const c of columns) obj[c] = normalize(json[c]);
      rows.push(obj);
    }
    return {
      columns,
      rows,
      rowCount: rows.length,
      truncated: total > rows.length,
    };
  } finally {
    await conn.close();
  }
}
