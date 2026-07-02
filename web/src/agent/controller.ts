// Adapter: turns declarative agent intents (brief §5) into the app's existing
// React state changes + MapCanvas camera calls. Reads go through getters (so the
// agent always sees current state even from async tool loops); writes use the
// provided setters.
import type { MapData, Paper } from "../types";
import type { AgentController, FilterPatch, MapHandle, MapState } from "./types";

export interface ControllerDeps {
  getData: () => MapData;
  getSelectedIds: () => Set<string>;
  getHighlightedIds: () => Set<string>;
  getActiveGroups: () => Set<string>;
  getYearRange: () => [number, number];
  setSelectedIds: (s: Set<string>) => void;
  setHighlightedIds: (s: Set<string>) => void;
  setActiveGroups: (s: Set<string>) => void;
  setYearRange: (r: [number, number]) => void;
  map: () => MapHandle | null;
}

export function createController(deps: ControllerDeps): AgentController {
  const validIds = (ids: string[]): string[] => {
    const known = new Set(deps.getData().papers.map((p) => p.paper_id));
    return ids.filter((id) => known.has(id));
  };

  const isVisible = (p: Paper): boolean => {
    const groups = deps.getActiveGroups();
    const [lo, hi] = deps.getYearRange();
    return groups.has(p.pathway_group) && p.year >= lo && p.year <= hi;
  };

  return {
    selectPapers(paperIds) {
      const valid = validIds(paperIds);
      deps.setSelectedIds(new Set(valid));
      return { selected: valid.length };
    },

    highlightPapers(paperIds) {
      const valid = validIds(paperIds);
      deps.setHighlightedIds(new Set(valid));
      return { highlighted: valid.length };
    },

    clearSelection() {
      deps.setSelectedIds(new Set());
    },

    clearHighlight() {
      deps.setHighlightedIds(new Set());
    },

    zoomToPapers(paperIds) {
      const valid = validIds(paperIds);
      deps.map()?.zoomToPapers(valid);
      return { zoomed: valid.length };
    },

    zoomToCommunity(topicId) {
      const data = deps.getData();
      const cluster = data.clusters.find((c) => c.topic_id === topicId);
      if (!cluster) return { error: `no community with topic_id '${topicId}'` };
      const members = data.papers.filter((p) => p.cluster_id === topicId);
      if (members.length) deps.map()?.zoomToPoints(members);
      else deps.map()?.zoomToPoints([cluster.centroid]);
      return { topic_id: topicId, members: members.length };
    },

    setFilters(patch: FilterPatch) {
      const data = deps.getData();
      // Track the values we actually apply and return THOSE — the getters below
      // still read the previous render's refs, so re-reading them would report
      // stale filters to the agent.
      let nextGroups = deps.getActiveGroups();
      let nextYear = deps.getYearRange();
      if (patch.pathway_groups) {
        const allowed = new Set(data.clusters.map((c) => c.pathway_group));
        const filtered = new Set(patch.pathway_groups.filter((g) => allowed.has(g)));
        nextGroups = filtered.size ? filtered : new Set(allowed);
        deps.setActiveGroups(nextGroups);
      }
      if (patch.yearRange) {
        const years = data.papers.map((p) => p.year);
        const min = Math.min(...years);
        const max = Math.max(...years);
        const lo = Math.max(min, Math.min(patch.yearRange[0], max));
        const hi = Math.min(max, Math.max(patch.yearRange[1], min));
        nextYear = [Math.min(lo, hi), Math.max(lo, hi)];
        deps.setYearRange(nextYear);
      }
      return { pathway_groups: [...nextGroups], yearRange: nextYear };
    },

    resetView() {
      deps.map()?.resetView();
    },

    focusEntity(entity) {
      const data = deps.getData();
      let by = "none";
      let ids: string[] = [];

      if (entity.gene) {
        const g = entity.gene.toUpperCase();
        ids = data.papers
          .filter((p) => p.genes.some((x) => x.toUpperCase() === g))
          .map((p) => p.paper_id);
        by = "paper.genes";
        if (ids.length === 0) {
          // fall back to communities whose top_genes include the symbol
          const topics = new Set(
            data.clusters
              .filter((c) => c.top_genes.some((x) => x.toUpperCase() === g))
              .map((c) => c.topic_id)
          );
          if (topics.size) {
            ids = data.papers
              .filter((p) => topics.has(p.cluster_id))
              .map((p) => p.paper_id);
            by = "cluster.top_genes";
          }
        }
      } else if (entity.pathway_group) {
        const pg = entity.pathway_group;
        ids = data.papers
          .filter((p) => p.pathway_group === pg)
          .map((p) => p.paper_id);
        by = "paper.pathway_group";
      } else if (entity.variant) {
        // rsIDs aren't carried on papers; resolve via query_data instead.
        by = "unsupported_use_query_data";
      }

      if (ids.length) {
        deps.setSelectedIds(new Set(ids));
        deps.map()?.zoomToPapers(ids);
      }
      return { resolved: ids.length, paperIds: ids.slice(0, 200), by };
    },

    getState(): MapState {
      const data = deps.getData();
      const visibleCount = data.papers.filter(isVisible).length;
      const t = deps.map()?.getTransform() ?? { scale: 1, tx: 0, ty: 0 };
      return {
        selectedIds: [...deps.getSelectedIds()],
        highlightedIds: [...deps.getHighlightedIds()],
        visibleCount,
        totalPapers: data.papers.length,
        transform: t,
        filters: {
          pathway_groups: [...deps.getActiveGroups()],
          yearRange: deps.getYearRange(),
        },
      };
    },
  };
}
