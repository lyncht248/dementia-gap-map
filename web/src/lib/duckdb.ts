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
  "drugs",
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

/** Run a query returning ALL rows (no 200-row cap). Internal use. */
async function queryAll(sql: string): Promise<Record<string, unknown>[]> {
  const db = await getDb();
  const conn = await db.connect();
  try {
    const table = await conn.query(sql);
    const columns = table.schema.fields.map((f) => f.name);
    const rows: Record<string, unknown>[] = [];
    for (const row of table) {
      const json = row.toJSON() as Record<string, unknown>;
      const obj: Record<string, unknown> = {};
      for (const c of columns) obj[c] = normalize(json[c]);
      rows.push(obj);
    }
    return rows;
  } finally {
    await conn.close();
  }
}

// --- evidence-graph traversal --------------------------------------------
// The graph (graph_nodes ~15k, graph_edges ~13k) is tiny, so we load it once
// into in-memory adjacency maps and BFS. node_id = '<type>:<id>'.

interface GraphCache {
  nodeInfo: Map<string, { type: string; label: string }>;
  labelIndex: Map<string, string>; // UPPER(label) -> node_id (genes preferred)
  adjOut: Map<string, { to: string; type: string }[]>;
  adjIn: Map<string, { to: string; type: string }[]>;
}

export interface GraphHop {
  node_id: string;
  type: string;
  label: string;
  hop: number;
  via: string;
  path: string;
}

export interface GraphResult {
  start: { node_id: string; type: string; label: string } | null;
  resolved_from: string;
  hops: number;
  direction: string;
  nodes: GraphHop[];
  count: number;
  truncated: boolean;
  error?: string;
}

let graphPromise: Promise<GraphCache> | null = null;

function getGraph(): Promise<GraphCache> {
  if (!graphPromise) {
    graphPromise = (async () => {
      const nodes = await queryAll(`SELECT node_id, node_type, label FROM graph_nodes`);
      const edges = await queryAll(`SELECT source_id, target_id, edge_type FROM graph_edges`);
      const nodeInfo = new Map<string, { type: string; label: string }>();
      const labelIndex = new Map<string, string>();
      for (const n of nodes) {
        const id = String(n.node_id);
        const type = String(n.node_type ?? "");
        const label = n.label != null ? String(n.label) : id;
        nodeInfo.set(id, { type, label });
        const key = label.toUpperCase();
        if (key && (!labelIndex.has(key) || type === "gene")) labelIndex.set(key, id);
      }
      const adjOut = new Map<string, { to: string; type: string }[]>();
      const adjIn = new Map<string, { to: string; type: string }[]>();
      const push = (m: typeof adjOut, k: string, v: { to: string; type: string }) => {
        const l = m.get(k);
        if (l) l.push(v);
        else m.set(k, [v]);
      };
      for (const e of edges) {
        const s = String(e.source_id), t = String(e.target_id), ty = String(e.edge_type ?? "");
        push(adjOut, s, { to: t, type: ty });
        push(adjIn, t, { to: s, type: ty });
      }
      return { nodeInfo, labelIndex, adjOut, adjIn };
    })();
  }
  return graphPromise;
}

function resolveNode(g: GraphCache, input: string): string | null {
  if (g.nodeInfo.has(input)) return input;
  return g.labelIndex.get(input.toUpperCase()) ?? null;
}

function buildPath(
  g: GraphCache,
  parent: Map<string, { from: string; edge: string }>,
  startId: string,
  id: string
): string {
  const chain: { node: string; edge: string }[] = [];
  let cur = id;
  while (cur && cur !== startId) {
    const p = parent.get(cur);
    if (!p) break;
    chain.unshift({ node: cur, edge: p.edge });
    cur = p.from;
  }
  let s = g.nodeInfo.get(startId)?.label ?? startId;
  for (const step of chain) {
    s += ` -[${step.edge}]-> ${g.nodeInfo.get(step.node)?.label ?? step.node}`;
  }
  return s;
}

export async function traverseGraph(opts: {
  from: string;
  edgeTypes?: string[];
  hops?: number;
  direction?: string;
  limit?: number;
}): Promise<GraphResult> {
  const { from } = opts;
  const direction = opts.direction ?? "both";
  const maxHops = Math.max(1, Math.min(4, opts.hops ?? 2));
  const limit = Math.max(1, Math.min(200, opts.limit ?? 60));
  const g = await getGraph();
  const startId = resolveNode(g, from);
  if (!startId) {
    return {
      start: null, resolved_from: from, hops: maxHops, direction,
      nodes: [], count: 0, truncated: false,
      error: `could not resolve '${from}' to a graph node (try a node_id like 'gene:ENSG…' or a gene symbol)`,
    };
  }
  const etSet = opts.edgeTypes && opts.edgeTypes.length ? new Set(opts.edgeTypes) : null;
  const visited = new Set([startId]);
  const parent = new Map<string, { from: string; edge: string }>();
  const queue: { id: string; hop: number }[] = [{ id: startId, hop: 0 }];
  const out: GraphHop[] = [];
  let truncated = false;
  while (queue.length) {
    const { id, hop } = queue.shift()!;
    if (hop >= maxHops) continue;
    const neigh: { to: string; type: string }[] = [];
    if (direction === "out" || direction === "both") neigh.push(...(g.adjOut.get(id) ?? []));
    if (direction === "in" || direction === "both") neigh.push(...(g.adjIn.get(id) ?? []));
    for (const e of neigh) {
      if (etSet && !etSet.has(e.type)) continue;
      if (visited.has(e.to)) continue;
      visited.add(e.to);
      parent.set(e.to, { from: id, edge: e.type });
      const info = g.nodeInfo.get(e.to) ?? { type: "unknown", label: e.to };
      out.push({
        node_id: e.to, type: info.type, label: info.label, hop: hop + 1,
        via: e.type, path: buildPath(g, parent, startId, e.to),
      });
      if (out.length >= limit) { truncated = true; break; }
      queue.push({ id: e.to, hop: hop + 1 });
    }
    if (truncated) break;
  }
  const s = g.nodeInfo.get(startId)!;
  return {
    start: { node_id: startId, type: s.type, label: s.label },
    resolved_from: from, hops: maxHops, direction,
    nodes: out, count: out.length, truncated,
  };
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
