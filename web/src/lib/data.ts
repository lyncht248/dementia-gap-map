import type { MapData } from "../types";

/** Which embedding/layout backs the map. */
export type MapSource = "cocitation" | "specter2" | "specter2_clean";

const FILE: Record<MapSource, string> = {
  cocitation: "map_data.json",
  specter2: "map_data.specter2.json",
  specter2_clean: "map_data.specter2_clean.json",
};

export const SOURCE_LABEL: Record<MapSource, string> = {
  cocitation: "Co-citation",
  specter2: "SPECTER2 (semantic)",
  specter2_clean: "SPECTER2 (cleaned)",
};

// Base path aware fetch so it works under any Vercel deployment URL.
export async function loadMapData(source: MapSource = "cocitation"): Promise<MapData> {
  const url = `${import.meta.env.BASE_URL}data/${FILE[source]}`;
  const res = await fetch(url);
  if (!res.ok) {
    throw new Error(`Failed to load map data (${res.status}) from ${url}`);
  }
  return (await res.json()) as MapData;
}
