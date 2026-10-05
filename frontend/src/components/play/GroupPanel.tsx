import { useRef, useState } from "react";
import { api } from "../../api/client";
import type { Actor, GroupSettings } from "../../api/client";

type Order = GroupSettings["order"];

const ORDERS: { value: Order; label: string }[] = [
  { value: "directed", label: "Directed" },
  { value: "manual", label: "Manual" },
  { value: "list", label: "List" },
  { value: "natural", label: "Natural" },
];

/** What the backend counts for a character with no stored talkativeness: in
 *  Directed an unset value is never filtered; in Natural it is the midpoint.
 *  The slider shows that effective value, and moving it stores an explicit one. */
const effectiveTalkativeness = (s: GroupSettings, ref: string) =>
  s.talkativeness[ref] ?? (s.order === "natural" ? 50 : 100);

/** The order the list shows: the stored list first (for those still present),
 *  then anyone not yet in it, in cast order. */
function displayedRefs(s: GroupSettings, npcRefs: string[]): string[] {
  const present = new Set(npcRefs);
  const known = s.order_list.filter((r, i) => present.has(r) && s.order_list.indexOf(r) === i);
  return [...known, ...npcRefs.filter((r) => !known.includes(r))];
}

/** Write a new order for the DISPLAYED refs into the stored list without
 *  disturbing the rest. A character who left the scene still holds a slot in
 *  `order_list`; the visible refs are written, in their new order, into the
 *  slots visible refs occupied, and any visible ref that was not in the list is
 *  appended. Rewriting the list from what is on screen would drop the absent
 *  one's position, and they would come back at the end. */
function mergeOrder(stored: string[], visibleNew: string[]): string[] {
  const visible = new Set(visibleNew);
  let next = 0;
  const out = stored.map((r) => (visible.has(r) && next < visibleNew.length ? visibleNew[next++] : r));
  return [...out, ...visibleNew.slice(next)];
}

const detailOf = (e: unknown) =>
  (e as { detail?: string } | null)?.detail
  || (e instanceof Error ? e.message : "") || "could not save the group settings";

/** The scene's group-play settings, inline below the composer's meta strip.
 *
 *  Not a modal and with no key binding of its own: it closes on its button.
 *
 *  Saves are optimistic and serialized. Every control updates `latest`
 *  immediately, so a second edit made while a PUT is out builds on the first
 *  rather than on the stale prop. One PUT is in flight at a time, each built
 *  from `latest` at send time, so edits made during a PUT go out together in the
 *  next one. A failed PUT reverts `latest` to the last settings the server
 *  confirmed and says why. */
export default function GroupPanel(
  { cid, sid, cast, settings, onChange, onClose }: {
    cid: string;
    sid: string;
    cast: Actor[];
    settings: GroupSettings;
    onChange: (s: GroupSettings) => void;
    onClose: () => void;
  },
) {
  const [view, setView] = useState(settings);
  const [error, setError] = useState<string | null>(null);
  const latest = useRef(settings);
  const confirmed = useRef(settings);
  const dirty = useRef(false);
  const draining = useRef(false);

  async function drain() {
    draining.current = true;
    try {
      while (dirty.current) {
        dirty.current = false;
        try {
          const r = await api.setSceneGroup(cid, sid, latest.current);
          confirmed.current = r.settings;
          // The server's reading wins only when nothing newer is waiting.
          if (!dirty.current) { latest.current = r.settings; setView(r.settings); }
          onChange(r.settings);
        } catch (e) {
          dirty.current = false;
          latest.current = confirmed.current;
          setView(confirmed.current);
          setError(detailOf(e));
        }
      }
    } finally {
      draining.current = false;
    }
  }

  function commit(next: GroupSettings) {
    latest.current = next;
    setView(next);
    setError(null);
    dirty.current = true;
    if (!draining.current) void drain();
  }

  const npcs = cast.filter((a) => a.role === "npc");
  const refOf = (a: Actor) => `${a.kind}:${a.id}`;
  const byRef = new Map(npcs.map((a) => [refOf(a), a]));
  const refs = displayedRefs(view, npcs.map(refOf));
  const showSlider = view.order === "directed" || view.order === "natural";

  function move(ref: string, delta: number) {
    const at = refs.indexOf(ref);
    const to = at + delta;
    if (at < 0 || to < 0 || to >= refs.length) return;
    const next = [...refs];
    next.splice(at, 1);
    next.splice(to, 0, ref);
    const current = latest.current;
    commit({ ...current, order_list: mergeOrder(current.order_list, next) });
  }

  return (
    <div className="group-panel" role="region" aria-label="Speaker order settings">
      <div className="group-panel-head">
        <label className="composer-meta-label" htmlFor="group-order">Speaker order</label>
        <select id="group-order" aria-label="Speaker order" value={view.order}
          onChange={(e) => commit({ ...latest.current, order: e.target.value as Order })}>
          {ORDERS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
        <label className="composer-meta-label" htmlFor="group-rounds">Automatic rounds</label>
        <input id="group-rounds" type="number" min={0} max={5} step={1}
          aria-label="Automatic rounds" className="response-target-input"
          value={view.auto_rounds} disabled={view.order === "manual"}
          onChange={(e) => {
            const n = Math.round(Number(e.target.value));
            if (e.target.value === "" || Number.isNaN(n)) return;
            commit({ ...latest.current, auto_rounds: Math.min(5, Math.max(0, n)) });
          }} />
        <span className="header-spacer" />
        <button type="button" className="composer-link" onClick={onClose}>Close</button>
      </div>
      {error && <div className="group-panel-error" role="alert">{error}</div>}
      {view.order === "manual" && (
        <p className="composer-meta-hint">Nobody replies on their own. Tap a name to have them speak.</p>
      )}
      <ul className="group-panel-rows">
        {refs.map((ref) => {
          const a = byRef.get(ref);
          if (!a) return null;
          return (
            <li key={ref} className="group-panel-row">
              <span className="group-panel-name">{a.name}</span>
              {view.order === "list" && (
                <span className="group-panel-moves">
                  <button type="button" aria-label={`Move ${a.name} up`}
                    disabled={refs.indexOf(ref) === 0} onClick={() => move(ref, -1)}>↑</button>
                  <button type="button" aria-label={`Move ${a.name} down`}
                    disabled={refs.indexOf(ref) === refs.length - 1} onClick={() => move(ref, 1)}>↓</button>
                </span>
              )}
              {showSlider && (
                <label className="group-panel-slider">
                  <input type="range" min={0} max={100} step={5}
                    aria-label={`${a.name} talkativeness`}
                    value={effectiveTalkativeness(view, ref)}
                    onChange={(e) => {
                      const current = latest.current;
                      commit({ ...current, talkativeness:
                        { ...current.talkativeness, [ref]: Number(e.target.value) } });
                    }} />
                  <span className="composer-meta-hint">{effectiveTalkativeness(view, ref)}</span>
                </label>
              )}
              <label className="group-panel-sit">
                <input type="checkbox" aria-label={`${a.name} sits out`}
                  checked={view.sitting_out.includes(ref)}
                  onChange={(e) => {
                    const current = latest.current;
                    const rest = current.sitting_out.filter((r) => r !== ref);
                    commit({ ...current, sitting_out: e.target.checked ? [...rest, ref] : rest });
                  }} />
                <span className="composer-meta-hint">sits out</span>
              </label>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
