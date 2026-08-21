/** What a reader calls each sheetable file kind.
 *
 *  Plural, and titled: these label a *count* ("Characters 12/40") or a section
 *  of the cast, never one record — `EntityEditor.KIND_LABELS` is the singular
 *  map for "this location", and `IncomingReview`'s is a third set for the same
 *  reason. Shared rather than copied because the three screens that render
 *  sheet coverage — the campaign's mechanics panel, the world's, and the sheets
 *  room — have to name the same kind the same way or the numbers read as being
 *  about different things.
 *
 *  Keyed by the store's file kind (`store.sheets.FILE_KINDS`), so `pcs` is here
 *  beside `characters` even though the two share a module sheet *type* kind.
 */
export const SHEET_KIND_LABELS: Record<string, string> = {
  characters: "Characters", pcs: "PCs", locations: "Locations", lore: "Lore",
  items: "Items", groups: "Groups", creatures: "Creatures",
};

/** The label, falling back to the raw kind — a module can only sheet the kinds
 *  the store defines, but a store that grows one should render it, not blank. */
export const sheetKindLabel = (kind: string) => SHEET_KIND_LABELS[kind] ?? kind;
