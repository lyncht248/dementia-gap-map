import type { MapData } from "../types";

// Base path aware fetch so it works under any Vercel deployment URL.
const DATA_URL = `${import.meta.env.BASE_URL}data/map_data.json`;

export async function loadMapData(): Promise<MapData> {
  const res = await fetch(DATA_URL);
  if (!res.ok) {
    throw new Error(`Failed to load map data (${res.status}) from ${DATA_URL}`);
  }
  const data = (await res.json()) as MapData;
  return data;
}
