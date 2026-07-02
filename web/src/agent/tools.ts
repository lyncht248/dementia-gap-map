// Tool definitions (OpenAI function-calling schema) + a dispatcher that runs a
// tool call locally: `query_data` hits DuckDB-Wasm, the rest drive the map via
// the AgentController. Everything executes in the browser; the serverless proxy
// only relays model turns.
import { runSql } from "../lib/duckdb";
import type { AgentController } from "./types";

export interface ToolSpec {
  type: "function";
  function: {
    name: string;
    description: string;
    parameters: Record<string, unknown>;
  };
}

const strArray = { type: "array", items: { type: "string" } };

export const TOOLS: ToolSpec[] = [
  {
    type: "function",
    function: {
      name: "query_data",
      description:
        "Run a read-only DuckDB SQL query over the translational-evidence tables " +
        "and return rows. Use this to answer any factual/analytical question " +
        "(underserved loci, which papers mention a gene, trial counts, etc.) and " +
        "to resolve entities to paper_ids before controlling the map. Results are " +
        "capped at 200 rows — aggregate or LIMIT. List columns support " +
        "list_contains(col,'APOE').",
      parameters: {
        type: "object",
        properties: {
          sql: { type: "string", description: "A single SELECT statement." },
        },
        required: ["sql"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "select_papers",
      description:
        "Set the map's selection to these paper_ids (the selection feed updates). " +
        "paper_id looks like 'pmid:12345678'.",
      parameters: {
        type: "object",
        properties: { paper_ids: strArray },
        required: ["paper_ids"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "highlight_papers",
      description:
        "Spotlight these paper_ids with a transient amber ring (does not change " +
        "the selection). Use to point at specific papers.",
      parameters: {
        type: "object",
        properties: { paper_ids: strArray },
        required: ["paper_ids"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "zoom_to_papers",
      description: "Fit/animate the camera to the bounding box of these paper_ids.",
      parameters: {
        type: "object",
        properties: { paper_ids: strArray },
        required: ["paper_ids"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "zoom_to_community",
      description:
        "Fit the camera to a visual community by its topic_id (e.g. 'c0'). See the " +
        "clusters table for topic_id/label.",
      parameters: {
        type: "object",
        properties: { topic_id: { type: "string" } },
        required: ["topic_id"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "set_filters",
      description:
        "Filter which papers are shown. Provide any subset. pathway_groups filters " +
        "to those groups; year_start/year_end bound the publication year.",
      parameters: {
        type: "object",
        properties: {
          pathway_groups: strArray,
          year_start: { type: "number" },
          year_end: { type: "number" },
        },
      },
    },
  },
  {
    type: "function",
    function: {
      name: "focus_entity",
      description:
        "Convenience: resolve a gene symbol, variant rsID, or pathway_group to " +
        "papers, then select + zoom to them in one step. For complex resolution " +
        "prefer query_data followed by select_papers/zoom_to_papers.",
      parameters: {
        type: "object",
        properties: {
          gene: { type: "string", description: "Gene symbol, e.g. APOE" },
          variant: { type: "string", description: "rsID, e.g. rs429358" },
          pathway_group: { type: "string" },
        },
      },
    },
  },
  {
    type: "function",
    function: {
      name: "clear_selection",
      description: "Clear the current selection.",
      parameters: { type: "object", properties: {} },
    },
  },
  {
    type: "function",
    function: {
      name: "clear_highlight",
      description: "Remove any agent highlight rings.",
      parameters: { type: "object", properties: {} },
    },
  },
  {
    type: "function",
    function: {
      name: "reset_view",
      description: "Reset the camera to fit the whole map.",
      parameters: { type: "object", properties: {} },
    },
  },
  {
    type: "function",
    function: {
      name: "get_state",
      description:
        "Read the current map state: selection, highlight, filters, transform, " +
        "visible/total paper counts.",
      parameters: { type: "object", properties: {} },
    },
  },
];

/** Execute one tool call and return a JSON-serializable result. */
export async function dispatchTool(
  controller: AgentController,
  name: string,
  args: Record<string, unknown>
): Promise<unknown> {
  switch (name) {
    case "query_data": {
      const sql = String(args.sql ?? "");
      if (!sql.trim()) return { error: "empty sql" };
      try {
        return await runSql(sql);
      } catch (e) {
        return { error: String(e instanceof Error ? e.message : e) };
      }
    }
    case "select_papers":
      return controller.selectPapers(asStrArray(args.paper_ids));
    case "highlight_papers":
      return controller.highlightPapers(asStrArray(args.paper_ids));
    case "zoom_to_papers":
      return controller.zoomToPapers(asStrArray(args.paper_ids));
    case "zoom_to_community":
      return controller.zoomToCommunity(String(args.topic_id ?? ""));
    case "set_filters":
      return controller.setFilters({
        pathway_groups: args.pathway_groups
          ? asStrArray(args.pathway_groups)
          : undefined,
        yearRange:
          args.year_start != null || args.year_end != null
            ? [Number(args.year_start ?? -Infinity), Number(args.year_end ?? Infinity)]
            : undefined,
      });
    case "focus_entity":
      return controller.focusEntity({
        gene: args.gene ? String(args.gene) : undefined,
        variant: args.variant ? String(args.variant) : undefined,
        pathway_group: args.pathway_group ? String(args.pathway_group) : undefined,
      });
    case "clear_selection":
      controller.clearSelection();
      return { ok: true };
    case "clear_highlight":
      controller.clearHighlight();
      return { ok: true };
    case "reset_view":
      controller.resetView();
      return { ok: true };
    case "get_state":
      return controller.getState();
    default:
      return { error: `unknown tool: ${name}` };
  }
}

function asStrArray(v: unknown): string[] {
  if (Array.isArray(v)) return v.map(String);
  if (typeof v === "string" && v) return [v];
  return [];
}
