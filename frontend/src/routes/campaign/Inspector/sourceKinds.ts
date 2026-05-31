/**
 * Synthetic "attribution-only" source kinds emitted for the always-on prompt
 * blocks (see assembler `_append_block_sources`). They are appended *after*
 * the Context Builder applies pin/exclude filtering, so pinning or excluding
 * them is a no-op the next preview never consults — hide those controls.
 */
export const ATTRIBUTION_ONLY_KINDS: ReadonlySet<string> = new Set([
  "system",
  "response_format",
  "scene_header",
  "mechanics",
  "recent_posts",
  "player_input",
]);

export function isPinnable(kind: string): boolean {
  return !ATTRIBUTION_ONLY_KINDS.has(kind);
}

/**
 * Human-readable names for each context-chunk kind, shown in the inspector
 * instead of the raw snake_case identifier. Unknown kinds fall back to a
 * title-cased version of the raw value (see {@link kindLabel}).
 */
export const KIND_LABELS: Record<string, string> = {
  // Always-on prompt blocks.
  system: "System instructions",
  response_format: "Response format",
  scene_header: "Scene header",
  mechanics: "Mechanics",
  recent_posts: "Recent posts (verbatim)",
  player_input: "Player input",
  // Retrieved / assembled sources.
  scene: "Scene",
  character: "Character",
  lore: "Lore",
  faction: "Faction",
  location: "Location",
  fact: "Fact",
  commitment: "Commitment",
  relationship: "Relationship",
  power: "Power",
  post: "Scene post",
  calendar: "Calendar",
  weather: "Weather",
  pin: "Pinned note",
  exclude: "Excluded",
};

export function kindLabel(kind: string): string {
  return (
    KIND_LABELS[kind] ?? kind.replace(/[_-]/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())
  );
}

/**
 * Clean a source summary for display: replace any ref/path-like token (e.g.
 * `emergent/shia`, `library:worlds/sakura-high/characters/winifred`) with the
 * title-cased final segment ("Shia", "winifred"), leaving ordinary words
 * untouched. Used for the optional per-chunk detail in the inspector row.
 */
export function cleanSummary(summary: string): string {
  return summary
    .split(/\s+/)
    .map((tok) => {
      const stripped = tok.replace(/^(library:|campaign:)/, "");
      if (!stripped.includes("/")) return tok;
      const last = stripped.split("/").filter(Boolean).pop() ?? stripped;
      return last.replace(/[-_]/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
    })
    .join(" ")
    .trim();
}

/**
 * The natural-language name shown on a collapsed inspector chunk row: the kind
 * label, plus a cleaned specific (the character/lore/etc. it refers to) when
 * the summary adds information beyond the kind itself.
 */
export function chunkLabel(source: { kind: string; summary?: string | null }): {
  label: string;
  detail: string;
} {
  const label = kindLabel(source.kind);
  const detail = source.summary ? cleanSummary(source.summary) : "";
  return { label, detail: detail && detail !== label ? detail : "" };
}
