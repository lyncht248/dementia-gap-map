# Agent panel — data + graph control

An in-app research co-pilot for the Dementia Gap Map. It can **query** the
translational-evidence data and **control** the graph (select / highlight / zoom /
filter / focus an entity). UI mirrors the Claude Science layout: agent panel on
the left, draggable divider, graph on the right, horizontal chat tabs with
"New chat".

Implements §5 of `docs/agent-graph-control-brief.md`.

## Architecture

```
Browser (client-orchestrated tool loop)
  AgentPanel ──user msg──▶ runConversation()  (src/agent/client.ts)
       ▲                        │
       │                   POST /api/agent  ── one model turn ──▶ OpenAI-compatible API
       │                        │            (serverless proxy; key stays server-side)
       │                   assistant + tool_calls
       │                        ▼
       │                 dispatchTool()  (src/agent/tools.ts)
       │                   ├─ query_data  ─▶ DuckDB-Wasm over Parquet   (src/lib/duckdb.ts)
       │                   └─ map control ─▶ AgentController             (src/agent/controller.ts)
       └────────────────── final answer                                    │
                                                                      React state + MapCanvas ref
```

The **browser** runs the tool loop: it asks the proxy for a single model turn,
executes any tool calls locally (DuckDB queries + map control), feeds results
back, and repeats until the model answers (`MAX_STEPS` in `client.ts`). The
server is a **stateless proxy** — no DB, no session store.

## Pieces

| File | Role |
|---|---|
| `scripts/build-agent-parquet.py` | JSONL + `map_data.json` → 7 Parquet tables in `web/public/data/parquet/` |
| `web/src/lib/duckdb.ts` | Lazy DuckDB-Wasm; registers Parquet as views; `runSql()`, `getSchema()` (live columns) |
| `web/src/agent/systemPrompt.ts` | Schema + join keys + scoring semantics + behavior |
| `web/src/agent/tools.ts` | Tool schemas (`query_data`, `describe_schema` + map control) + dispatcher |
| `web/src/agent/client.ts` | Client-side model/tool loop against `/api/agent` |
| `web/src/agent/controller.ts` | Adapter: intents → React state setters + camera calls |
| `web/src/agent/types.ts` | `AgentController`, `MapHandle`, `MapState` |
| `web/src/components/AgentPanel.tsx` | Chat tabs, timeline, tool chips, input |
| `web/src/components/MapCanvas.tsx` | `forwardRef` handle (`zoomToPapers`…) + highlight rendering |
| `web/api/agent.ts` | Vercel serverless function (handler + `GET` health check) |
| `web/server/agentProxy.ts` | Shared proxy core + `checkAccess` gate (NOT under `api/` — Vercel drops `_`-prefixed helpers; NOT under `src/` — keeps it out of the app typecheck) |
| `web/vite.config.ts` | Dev middleware serving `/api/agent` locally |

## Data tables (DuckDB, SELECT-only, 200-row cap)

`papers, clusters, genes, pathways, trials, gwas, functional_links` — see
`systemPrompt.ts` for columns/joins. Anchored on stable IDs only (PMID, Ensembl
`gene_id`/`symbol`, NCT, rsID, `disease_group`), per the brief — never on
coordinates or community numbers. The agent reads the **live schema** at runtime
(injected into the prompt + a `describe_schema` tool), so column/score changes
are picked up automatically — no prompt edit needed. Regenerate after a data
refresh (the build validates and fails loudly on empty/broken tables):

```bash
cd web && npm run build:data    # map_data.json + Parquet (both committed)
# or just the Parquet: npm run gen-parquet
```

## Control API (`AgentController`)

`selectPapers · highlightPapers · clearSelection · clearHighlight · zoomToPapers ·
zoomToCommunity · setFilters · focusEntity · resetView · getState`. Entity
resolution: prefer `query_data` (e.g. gene → `gwas.pmid` → `papers`) then
`select_papers`/`zoom_to_papers`; `focus_entity` is a best-effort shortcut over
in-memory paper attributes.

## Running

```bash
cd web
cp .env.example .env.local     # set OPENAI_API_KEY (OpenRouter: set OPENAI_BASE_URL/MODEL)
npm install
npm run dev                    # /api/agent served by Vite middleware
```

### Deploy (Vercel)
- Project **Root Directory = `web/`** (Settings → General). It serves `api/agent.ts`
  as a Node function and the committed Parquet statically.
- Set env vars for **Production + Preview** (Settings → Environment Variables):
  `OPENAI_API_KEY` (required), optionally `OPENAI_BASE_URL`, `OPENAI_MODEL`
  (default `gpt-5.5`), `OPENAI_TEMPERATURE`. **Redeploy** after adding them.
- Health check: open `https://<deploy>/api/agent` in a browser — it returns
  `{ ok, hasKey, model, baseUrl }`. `hasKey:false` ⇒ the key isn't set for that
  environment.
- The shared proxy core lives in `web/server/` (not `api/`): Vercel excludes
  `_`-prefixed files in `api/` from the function bundle, which crashes the import
  at runtime (`FUNCTION_INVOCATION_FAILED`). `api/agent.ts` sets
  `maxDuration = 300` (Pro ceiling; Hobby caps at 60) for slower reasoning-model
  turns — only actual execution time is billed.
- **Env vars are per-project.** `OPENAI_API_KEY` must be set on the
  **`dementia-gap-map`** project (or as a Team Shared Variable *linked* to it) —
  a key on a sibling project (e.g. `et-al`) is not visible here. Confirm with the
  health check.

### Access control (open-proxy risk)
`/api/agent` relays to the model with the server key. `checkAccess` blocks
cross-origin browser calls (best-effort). For a public deploy this is **not**
sufficient on its own — keep **Vercel Deployment Protection** on (it's on for
previews by default) or put a real auth/rate-limit gateway in front. A browser
app can't hold a secret, so there's no client-side token.

Dev-only: `window.mapAgent` exposes the controller for console debugging.

## Extending / gotchas
- **Two groupings**: 16 visual communities (`clusters`) vs analytic topics — the
  map uses the visual ones. Don't assume a gene/pathway maps 1:1 to a community.
- Track B evidence attaches to papers by **PMID**, robust to re-clustering.
- New tool → add a schema in `tools.ts`, a `case` in `dispatchTool`, and (if it
  controls the map) a method on `AgentController`.
- If `map_data.json`/schemas change, rerun `npm run build:data`. Column/score
  changes are picked up automatically via the live schema + `describe_schema`;
  you only need to touch `systemPrompt.ts` for *semantic* guidance (new joins,
  scoring meaning), not for column names.
