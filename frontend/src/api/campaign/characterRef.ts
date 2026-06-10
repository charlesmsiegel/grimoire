/**
 * Collapse any recognized character-ref spelling to one canonical string.
 *
 * Mirrors the backend `canonicalize_character_ref` (grimoire/util.py). Refs are
 * stored in several equivalent spellings: the canonical
 * `library:worlds/<world>/characters/<id>` and
 * `campaign:emergent/character/<id>` forms, plus shorthands the campaign wizard
 * registers (bare `<world>/<id>`, `emergent/<id>`, the singular `character`
 * segment, …). Normalizing both sides of a comparison lets PC-identity checks
 * line up regardless of which spelling was stored — without it, the remove-PC
 * action never appears on wizard-created campaigns (#517, #464).
 *
 * Unrecognized refs are returned unchanged.
 */
export function canonicalizeCharacterRef(ref: string): string {
  const raw = ref.trim();
  if (!raw) return ref;
  // Emergent (campaign-local): every spelling carries the asset id as the
  // trailing path segment.
  if (raw.startsWith("campaign:emergent/") || raw.startsWith("emergent/")) {
    const trimmed = raw.replace(/\/+$/, "");
    const asset = trimmed.slice(trimmed.lastIndexOf("/") + 1);
    return asset ? `campaign:emergent/character/${asset}` : ref;
  }
  // Library: pull (world, id) from the full, scheme-less, or singular spelling.
  const body = raw.startsWith("library:") ? raw.slice("library:".length) : raw;
  const parts = body.split("/").filter((p) => p.length > 0);
  const n = parts.length;
  // Match `worlds/<w>/characters/<id>` at the tail of the path. Anchoring on
  // the tail also collapses an over-qualified `<world>/worlds/<world>/characters/<id>`.
  if (
    n >= 4 &&
    parts[n - 4] === "worlds" &&
    (parts[n - 2] === "characters" || parts[n - 2] === "character")
  ) {
    return `library:worlds/${parts[n - 3]}/characters/${parts[n - 1]}`;
  }
  // Bare `<world>/<id>` shorthand the campaign wizard can register.
  if (n === 2 && !raw.includes(":") && parts[0] !== "worlds") {
    return `library:worlds/${parts[0]}/characters/${parts[1]}`;
  }
  return ref;
}

/**
 * Canonical ref for a resolved character row. The same asset id can exist
 * in more than one composed world, so identity checks (cast membership,
 * PC flags, deep links) must key on the full ref, never the bare id.
 */
export function characterRefFor(character: { world_id: string | null; id: string }): string {
  return character.world_id !== null
    ? `library:worlds/${character.world_id}/characters/${character.id}`
    : `campaign:emergent/character/${character.id}`;
}
