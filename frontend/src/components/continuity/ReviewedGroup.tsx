/** Reviewed links / merges (capstone §12.6): every alias and link the review
 *  has made, each with its one way back.
 *
 *  A merge is undone by Unmerge and a link by Remove link; both are journalled
 *  server-side, so the play view's Changes panel can reverse either again. A
 *  dangling alias, or a link whose ends no longer resolve, is listed here too,
 *  marked "Broken" -- it is still in continuity.json, and this is the only
 *  place a reader can find it and take it out.
 */
import type { ContinuityState } from "../../api/client";
import { brokenReason, recordName, RELATION_PHRASES } from "./labels";

const phrase = (relation: string) => RELATION_PHRASES[relation] ?? relation;

export function ReviewedGroup(
  { state, busy, onUnmerge, onRemoveLink }: {
    state: ContinuityState | null | "failed";
    busy: boolean;
    onUnmerge: (ref: string) => void;
    onRemoveLink: (id: string) => void;
  },
) {
  if (state === null) return <p className="column-empty">Reading the reviewed links…</p>;
  if (state === "failed") {
    return <p className="empty-state">The reviewed links and merges could not be read.</p>;
  }
  // A broken link is not an effective one, so `links` never carries it; the
  // raw list is where it is, and only its broken rows are new here.
  const broken = state.raw_links.filter((l) => l.state === "broken");
  // A ref-titled record is missing only if its ledger read (`recordName`).
  const name = (title: string, ref: string) => recordName(title, ref, state.unreadable);
  if (!state.aliases.length && !state.links.length && !broken.length) {
    return (
      <p className="empty-state">
        <span className="empty-what">Nothing merged or linked yet.</span> A merge or a link
        made from a finding is listed here, with the way to undo it.
      </p>
    );
  }
  return (
    <ul className="continuity-list">
      {state.aliases.map((a) => {
        const title = name(a.title, a.ref);
        return (
          <li key={`alias:${a.ref}`} className="continuity-item" aria-label={title}>
            <div className="continuity-item-text">
              <span className="continuity-item-title">{title}</span>
              <span className="continuity-item-meta">
                Merged into {name(a.to_title, a.to)}
              </span>
              {a.dangling && <span className="chip on continuity-broken">Broken</span>}
              {a.dangling && <p className="field-hint">{brokenReason(a.reason)}</p>}
            </div>
            <button type="button" className="subtle" disabled={busy}
                    aria-label={`Unmerge ${title}`} onClick={() => onUnmerge(a.ref)}>
              Unmerge
            </button>
          </li>
        );
      })}
      {state.links.map((l) => {
        const [a, b] = [name(l.a_title, l.a), name(l.b_title, l.b)];
        const label = `${a} ${phrase(l.relation)} ${b}`;
        return (
          <li key={`link:${l.id}`} className="continuity-item" aria-label={label}>
            <div className="continuity-item-text">
              <span className="continuity-item-title">{a}</span>
              <span className="continuity-item-meta">
                {phrase(l.relation)} {b}
              </span>
            </div>
            <button type="button" className="subtle" disabled={busy}
                    aria-label={`Remove link ${label}`} onClick={() => onRemoveLink(l.id)}>
              Remove link
            </button>
          </li>
        );
      })}
      {broken.map((l) => {
        // A broken link's end that no longer resolves is titled by its ref,
        // which `name` words by kind: no ref reaches the reader (§30).
        const [a, b] = [name(l.a_title, l.a), name(l.b_title, l.b)];
        const label = `${a} ${phrase(l.relation)} ${b}`;
        return (
          <li key={`broken:${l.id}`} className="continuity-item" aria-label={label}>
            <div className="continuity-item-text">
              <span className="continuity-item-title">{a}</span>
              <span className="continuity-item-meta">{phrase(l.relation)} {b}</span>
              <span className="chip on continuity-broken">Broken</span>
              <p className="field-hint">{brokenReason(l.reason)}</p>
            </div>
            <button type="button" className="subtle" disabled={busy}
                    aria-label={`Remove link ${label}`} onClick={() => onRemoveLink(l.id)}>
              Remove link
            </button>
          </li>
        );
      })}
    </ul>
  );
}

export default ReviewedGroup;
