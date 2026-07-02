import type { LensFile, MapData } from "../types";

// Base path aware fetch so it works under any Vercel deployment URL.
const DATA_URL = `${import.meta.env.BASE_URL}data/map_data.json`;
const LENS_URL = `${import.meta.env.BASE_URL}data/label_lenses.json`;

export async function loadMapData(): Promise<MapData> {
  const res = await fetch(DATA_URL);
  if (!res.ok) {
    throw new Error(`Failed to load map data (${res.status}) from ${DATA_URL}`);
  }
  const data = (await res.json()) as MapData;
  return data;
}

/** Alternative label lenses (theme / pathway / subtype). Optional: if the file
 *  is missing the map still renders with the baked-in default lens. */
export async function loadLensFile(): Promise<LensFile | null> {
  try {
    const res = await fetch(LENS_URL);
    if (!res.ok) return null;
    return (await res.json()) as LensFile;
  } catch {
    return null;
  }
}
