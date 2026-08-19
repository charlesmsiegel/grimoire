/** The fork tree, flattened into rows the shelf can render (#72).
 *
 *  A campaign records the campaign it was forked from (`parent`, an id), and
 *  nothing else — no children list, no depth, no root. That is deliberate on
 *  the store side: one field per campaign cannot disagree with itself, where a
 *  parent holding a list of children and each child holding a parent can. So
 *  the tree is derived, here, from whatever set of campaigns the caller is
 *  showing.
 *
 *  Order is the caller's. Roots come out in the order they went in and so do
 *  siblings, so a shelf sorted by "last played" stays sorted by last played
 *  within each generation — the tree groups the rows, it does not re-rank them.
 */

export type Lineal = { id: string; parent?: string };
export type LineageRow<T> = { item: T; depth: number };

/** `rows` re-ordered so each fork follows the campaign it came from, each
 *  tagged with how many generations deep it sits. Every input row comes back
 *  exactly once, whatever its `parent` says. */
export function lineage<T extends Lineal>(rows: T[]): LineageRow<T>[] {
  const present = new Set(rows.map((r) => r.id));
  const children = new Map<string, T[]>();
  const roots: T[] = [];
  for (const row of rows) {
    // A parent outside this set is not a parent here. That covers three real
    // cases with one rule: the parent was deleted, the world filter is hiding
    // it, and a campaign whose `parent` names itself after a hand edit. Each
    // leaves the child as a root, which is the only place it can be shown at
    // all — dropping it would hide a campaign from its own shelf.
    const parent = row.parent && row.parent !== row.id && present.has(row.parent)
      ? row.parent : "";
    if (!parent) { roots.push(row); continue; }
    const kin = children.get(parent);
    if (kin) kin.push(row); else children.set(parent, [row]);
  }

  const out: LineageRow<T>[] = [];
  const seen = new Set<string>();
  const walk = (row: T, depth: number) => {
    if (seen.has(row.id)) return;
    seen.add(row.id);
    out.push({ item: row, depth });
    for (const child of children.get(row.id) ?? []) walk(child, depth + 1);
  };
  for (const root of roots) walk(root, 0);
  // A cycle has no root, so nothing above reached it. The store cannot write
  // one — a fork's parent always exists before the fork does — but the store is
  // plain files the user owns and syncs, and a hand-edited pair pointing at each
  // other would otherwise vanish from the page entirely. `seen` is what makes
  // the walk terminate; this is what makes it complete.
  for (const row of rows) walk(row, 0);
  return out;
}
