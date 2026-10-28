import { useMemo, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { api, SECRECY_LABELS, type EntityKind, type EntityScope, type EntitySummary, type ModuleDetail } from "../../api/client";
import type { RecordRef } from "../../api/loreOwners";
import { THUMB, thumbSet } from "../../api/thumbs";
import { parseRefs } from "./fields";
import { KIND_LABELS, asSecrecy, tokenClass, tokenTitle, tokenAria } from "./presentation";

/** How wide a card's image is drawn, at the breakpoints `.entity-grid` uses in
 *  index.css: one column on a phone, two to 720px, three above. A sizes hint
 *  that describes a retired rail makes the browser fetch a wider file than the
 *  half-width card shows, or a smaller one than a desktop card needs. */
const ENTITY_CARD_SIZES = "(max-width: 420px) calc(100vw - 32px), (max-width: 720px) calc((100vw - 50px) / 2), (max-width: 1120px) 33vw, 340px";

export function EntityBrowse({ scope, kind, items, editing, query, setQuery, ownerById, ownerLabel,
  newHref, recordHref, module, error, adoptAllControls, onOpenWizard, onOpenTemplate }: {
  scope: EntityScope; kind: EntityKind; items: EntitySummary[]; editing: string | null;
  query: string; setQuery: (query: string) => void; ownerById: ReadonlyMap<string, RecordRef>;
  ownerLabel: (ref: string) => string; newHref: string; recordHref: (id: string) => string;
  module: ModuleDetail | null; error: string | null; adoptAllControls: ReactNode;
  onOpenWizard: () => void; onOpenTemplate: (id: string) => void;
}) {
  const label = KIND_LABELS[kind];
  // What the rail is currently showing, and — for lore — grouped: "Unowned
  // (world)" first, then one group per distinct owner ref, in the order the
  // owners are first met.
  //
  // Memoized on the rail's own inputs. This editor re-renders on every
  // keystroke in the body, and rebuilding the rail each time -- a filter, and a
  // group per owner that re-filtered the whole list and re-split every entry's
  // owners string -- is what made typing in a large world's lore lag. Now each
  // entry's owners are parsed once, the groups are filled in one pass, and none
  // of it runs for a keystroke.
  //
  // Name and keys are searched, because those are what a reader knows a record
  // BY -- the id is a slug they never typed and the body is not in the summary.
  const { shown, groups } = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const shown = needle
      ? items.filter((e) => (e.name ?? "").toLowerCase().includes(needle)
                         || (e.keys ?? "").toLowerCase().includes(needle))
      : items;
    const groups: { key: string; label: string; rows: EntitySummary[] }[] = [];
    if (kind === "lore") {
      const unowned: EntitySummary[] = [];
      const byOwner = new Map<string, EntitySummary[]>();
      for (const e of shown) {
        const refs = parseRefs(e.owners);
        if (!refs.length) { unowned.push(e); continue; }
        // Once per group even if the owners string names someone twice, as
        // the per-owner filter this replaces did.
        for (const ref of new Set(refs)) {
          const rows = byOwner.get(ref);
          if (rows) rows.push(e); else byOwner.set(ref, [e]);
        }
      }
      if (unowned.length) groups.push({ key: "", label: "Unowned (world)", rows: unowned });
      for (const [ref, rows] of byOwner) groups.push({ key: ref, label: ownerLabel(ref), rows });
    }
    return { shown, groups };
  }, [items, query, kind, ownerLabel]);

  // The rows themselves, for the same keystroke's reason: built when what they
  // show changes -- the list, the selection, the owners' avatars -- and handed
  // back as the same elements otherwise, which React then skips outright.
  const scopeKind = scope.kind;
  const scopeId = scope.id;
  const rail = useMemo(() => {
    const at: EntityScope = { kind: scopeKind, id: scopeId };
    const row = (e: EntitySummary) => (
      <Link key={e.id}
            className={"row loc-row" + (editing === e.id ? " active" : "")}
            to={recordHref(e.id)}>
        {e.has_image && (
          // The 220px rail, or the whole width once the editor stacks on a
          // phone -- past what the 1024 fills there, so the original is offered.
          <img className="loc-row-img" alt="" loading="lazy" decoding="async"
               style={e.image_focus == null ? undefined : { objectPosition: e.image_focus + "% " + e.image_focus + "%" }}
               {...thumbSet((w) => api.entityImageUrl(at, kind, e.id, "avatar", { w, v: e.image_v }),
                            ENTITY_CARD_SIZES, THUMB.tile,
                            api.entityImageUrl(at, kind, e.id, "avatar", { v: e.image_v }))}
               onError={(ev) => { (ev.currentTarget).style.display = "none"; }} />
        )}
        <span className="row-name">{e.name}</span>
        {asSecrecy(e.secrecy) !== "public" && (
          <span className={`chip secrecy-tag ${asSecrecy(e.secrecy)}`}>
            {SECRECY_LABELS[asSecrecy(e.secrecy)]}
          </span>
        )}
        {kind === "lore" && (
          <span className="owner-stack">
            {parseRefs(e.owners).map((ref) => {
              const o = ownerById.get(ref);
              return o?.avatar ? (
                <img key={ref} className="owner-stack-img" alt="" title={o.label}
                     loading="lazy" decoding="async" src={o.avatar}
                     onError={(ev) => { (ev.currentTarget).style.display = "none"; }} />
              ) : null;
            })}
          </span>
        )}
        {/* The unit is dropped for width -- the rail is a fixed 220px -- so the
            accessible name carries it instead. The row is a link named from
            its contents, so without this a screen reader reads "Salt 1,240" and
            the number could be anything; `title` alone is not announced. */}
        {typeof e.tokens === "number" && (
          <span className={tokenClass(asSecrecy(e.secrecy), "row-tokens")}
                title={tokenTitle(asSecrecy(e.secrecy))}
                aria-label={tokenAria(e.tokens, asSecrecy(e.secrecy))}>
            {e.tokens.toLocaleString()} {e.tokens === 1 ? "token" : "tokens"}
          </span>
        )}
      </Link>
    );
    return kind === "lore"
      ? groups.map((g) => (
          <div key={g.key} className="rail-group">
            <div className="rail-group-head">{g.label}</div>
            {g.rows.map(row)}
          </div>
        ))
      : shown.map(row);
  }, [kind, shown, groups, editing, ownerById, recordHref, scopeKind, scopeId]);

  return (
    <div className="entity-browse">
      {error && <div className="banner">{error}</div>}
      <div className="entity-browse-head">
        <Link className="primary new" to={newHref}>+ New {label}</Link>
        {/* Adoption is per record on the banner, and here for the whole
            library: a world's worth of imported entries is not something to
            walk one at a time. It sweeps every kind, which is why it lives
            on the one rail an import fills, not on each. */}
      {adoptAllControls}
        {/* Shown once there is enough to lose something in. Below that the
            filter is a control that costs a row and saves nothing. */}
        {items.length > 8 && (
          <>
            <input className="rail-search" type="search" value={query}
                   aria-label={`Search ${kind}`} placeholder={`Search ${kind}…`}
                   onChange={(e) => setQuery(e.target.value)} />
            <div className="rail-count">
              {shown.length === items.length
                ? `${items.length} ${label}${items.length === 1 ? "" : "s"}`
                : `${shown.length} of ${items.length}`}
            </div>
          </>
        )}
        {module && Object.values(module.sheets.sheet_types).some((st) => st.kind === kind) && (
          <button className="subtle" onClick={onOpenWizard}>
            + New {label} with sheet…
          </button>
        )}
      </div>
      {/* Three different answers, and they must not share one line: nothing
          here yet, nothing MATCHING here, and a filter narrowing a real set. */}
      {items.length === 0 && <div className="editor-empty">No {kind} yet.</div>}
      {items.length > 0 && shown.length === 0 && (
        <div className="editor-empty">Nothing matches “{query}”.</div>
      )}
      <div className="entity-grid">
        {rail}
        {module?.content.filter((c) => c.kind === kind).map((c) => (
          <button key={`content-${c.id}`} className="row content-row"
                  onClick={() => onOpenTemplate(c.id)}>
            <span className="row-name">{c.name}</span>
            <span className="chip">template</span>
          </button>
        ))}
      </div>
    </div>
  );
}
