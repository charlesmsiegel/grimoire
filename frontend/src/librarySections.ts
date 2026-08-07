import { api } from "./api/client";

/** The libraries the Library hub gathers.
 *
 *  One list, two consumers: the hub renders a card per entry, and the nav rail
 *  uses the same routes to decide whether you are currently inside the
 *  library. Each section keeps the page that already owns it — the hub is a
 *  landing page in front of those routes, not a replacement for them — so
 *  adding a library here is the whole change, with no aggregate backend route
 *  to invent. */
export type LibrarySection = {
  to: string;
  label: string;
  /** Singular noun for the count line; pluralized by adding "s". */
  unit: string;
  blurb: string;
  count: () => Promise<number>;
};

export const LIBRARY_SECTIONS: LibrarySection[] = [
  {
    to: "/worlds", label: "Worlds", unit: "world",
    blurb: "Settings, and the locations, characters, lore and items inside them.",
    count: () => api.listWorlds().then((r) => r.length),
  },
  {
    to: "/modules", label: "Modules", unit: "module",
    blurb: "Mechanics packs a campaign rolls against.",
    count: () => api.listModules().then((r) => r.length),
  },
  {
    to: "/styles", label: "Styles", unit: "style",
    blurb: "Prose style guides the narrator writes to.",
    count: () => api.listStyles().then((r) => r.length),
  },
  {
    to: "/response-presets", label: "Response Presets", unit: "response preset",
    blurb: "Reply length and shape presets scenes can pick from.",
    count: () => api.listResponsePresets().then((r) => r.length),
  },
  {
    to: "/climates", label: "Climates", unit: "climate",
    blurb: "Weather models that regions and locations draw their seasons from.",
    count: () => api.listClimates().then((r) => r.climates.length),
  },
];

/** Segment-aware, so /modules-of-my-own is not mistaken for a child of
 *  /modules — a bare startsWith would light the library up on any route that
 *  merely shares a prefix. */
export function isUnder(pathname: string, base: string): boolean {
  return pathname === base || pathname.startsWith(base + "/");
}

export function inLibrary(pathname: string): boolean {
  return isUnder(pathname, "/library") ||
    LIBRARY_SECTIONS.some((s) => isUnder(pathname, s.to));
}
