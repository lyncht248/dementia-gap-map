export interface Transform {
  scale: number;
  tx: number;
  ty: number;
}

export interface Point {
  x: number;
  y: number;
}

/** world -> screen */
export function toScreen(p: Point, t: Transform): Point {
  return { x: p.x * t.scale + t.tx, y: p.y * t.scale + t.ty };
}

/** screen -> world */
export function toWorld(p: Point, t: Transform): Point {
  return { x: (p.x - t.tx) / t.scale, y: (p.y - t.ty) / t.scale };
}

/** Fit a set of world points into a viewport with padding, returning a transform. */
export function fitTransform(
  points: Point[],
  width: number,
  height: number,
  padding = 60
): Transform {
  if (points.length === 0) return { scale: 1, tx: 0, ty: 0 };
  let minX = Infinity,
    minY = Infinity,
    maxX = -Infinity,
    maxY = -Infinity;
  for (const p of points) {
    if (p.x < minX) minX = p.x;
    if (p.y < minY) minY = p.y;
    if (p.x > maxX) maxX = p.x;
    if (p.y > maxY) maxY = p.y;
  }
  const w = maxX - minX || 1;
  const h = maxY - minY || 1;
  const scale = Math.min((width - 2 * padding) / w, (height - 2 * padding) / h);
  const tx = padding - minX * scale + (width - 2 * padding - w * scale) / 2;
  const ty = padding - minY * scale + (height - 2 * padding - h * scale) / 2;
  return { scale, tx, ty };
}

/** Ray-casting point-in-polygon test. polygon is a list of screen/world points. */
export function pointInPolygon(pt: Point, polygon: Point[]): boolean {
  if (polygon.length < 3) return false;
  let inside = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const xi = polygon[i].x,
      yi = polygon[i].y;
    const xj = polygon[j].x,
      yj = polygon[j].y;
    const intersect =
      yi > pt.y !== yj > pt.y &&
      pt.x < ((xj - xi) * (pt.y - yi)) / (yj - yi) + xi;
    if (intersect) inside = !inside;
  }
  return inside;
}
