import type { SceneMeta } from "../api/client";
import { byName } from "../sortByName";
import { sceneNumber } from "./sceneNumber";

export type SceneOrder = { by: "number" | "played" | "title"; descending: boolean };
const STORAGE_KEY = "grimoire.sort.scenes";
const DEFAULT_ORDER: SceneOrder = { by: "number", descending: true };

/** A display preference, independent of the server's activity order used by
 * the chooser. Unknown keys stay at the end even when direction is reversed. */
export function loadSceneOrder(): SceneOrder {
  try {
    const saved: unknown = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "null");
    if (saved && typeof saved === "object" && "by" in saved && "descending" in saved
      && (saved.by === "number" || saved.by === "played" || saved.by === "title")
      && typeof saved.descending === "boolean") {
      return { by: saved.by, descending: saved.descending };
    }
  } catch { /* Storage can be unavailable in a locked-down WebView. */ }
  return DEFAULT_ORDER;
}

export function saveSceneOrder(order: SceneOrder): void {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(order)); }
  catch { /* The current page can still sort when preferences cannot be saved. */ }
}

function activity(scene: SceneMeta): number | null {
  const updated = Date.parse(scene.updated);
  if (Number.isFinite(updated)) return updated;
  const created = Date.parse(scene.created);
  return Number.isFinite(created) ? created : null;
}

export function orderScenes(rows: readonly SceneMeta[], order: SceneOrder): SceneMeta[] {
  const byId = (a: SceneMeta, b: SceneMeta) => a.id.localeCompare(b.id);
  if (order.by === "title") {
    // Title is the scene's name on this surface. Start with ID order so equal
    // names have the same placement regardless of the server's activity order.
    const named = [...rows].sort(byId).map(scene => ({ name: scene.title, scene }));
    const ranked = byName(named).map(row => row.scene);
    return order.descending ? ranked.reverse() : ranked;
  }
  return [...rows].sort((a, b) => {
    const left = order.by === "number" ? sceneNumber(a.id) : activity(a);
    const right = order.by === "number" ? sceneNumber(b.id) : activity(b);
    if (left === null) return right === null ? byId(a, b) : 1;
    if (right === null) return -1;
    return (order.descending ? right - left : left - right) || byId(a, b);
  });
}

export function sceneOrderLabel(order: SceneOrder): string {
  if (order.by === "number") return order.descending ? "highest number first" : "lowest number first";
  if (order.by === "played") return order.descending ? "last played first" : "least recently played first";
  return order.descending ? "title Z–A" : "title A–Z";
}
