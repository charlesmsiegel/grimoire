/**
 * Synthetic "attribution-only" source kinds emitted for the always-on prompt
 * blocks (see assembler `_append_block_sources`). They are appended *after*
 * the Context Builder applies pin/exclude filtering, so pinning or excluding
 * them is a no-op the next preview never consults — hide those controls.
 */
export const ATTRIBUTION_ONLY_KINDS: ReadonlySet<string> = new Set([
  "system",
  "scene_header",
  "mechanics",
  "recent_posts",
  "player_input",
]);

export function isPinnable(kind: string): boolean {
  return !ATTRIBUTION_ONLY_KINDS.has(kind);
}
