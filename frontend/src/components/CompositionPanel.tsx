import { useCallback, useEffect, useRef, useState } from "react";
import { api, type DivergedRecord, type EntityScope, type IncomingItem,
  type IncomingRef, type RosterEntry } from "../api/client";

/** What a ref's kind is called in a sentence. The same nine the incoming review
 *  names, and the same reason: the ref carries the store's plural slug, which is
 *  not what a reader calls one of them. */
const KIND_LABELS: Record<string, string> = {
  locations: "Locations", lore: "Lore", items: "Items", groups: "Groups",
  creatures: "Creatures", greetings: "Greetings", characters: "Characters",
  pcs: "PCs", plotmap: "Plot map",
};

/** The order the rail groups kinds in. Actors first, because a locked version is
 *  the composition fact readers come here for; the plot map last, because there
 *  is at most one of it. */
const KIND_ORDER = ["characters", "pcs", "locations", "items", "groups",
                    "creatures", "lore", "greetings", "plotmap"];

/** Which side of the world/campaign split currently holds a ref — the
 *  "priority" the pre-rebuild feature list asked this view for.
 *
 *  Deliberately ONE state per ref rather than a set, in this precedence:
 *  a conflict outranks a plain update (both sides moved, so the decision is
 *  bigger), and either outranks a divergence (the world has moved too, which is
 *  the more urgent half of the same sentence). `locked` is not in the ladder at
 *  all — see `Row.locked`. */
type State = "conflict" | "update" | "new" | "diverged" | "insync";

type Row = {
  key: string;
  ref: IncomingRef;
  name: string;
  state: State;
  /** The world version this actor is pinned to, if any.
   *
   *  Carried BESIDE the state rather than as one of its values, because a
   *  version lock and a sync ref are two different systems with two different
   *  upgrade verbs (`import_version` against a lock, `sync.accept` against a
   *  ref) — and an actor can be in both at once. Folding them into one status
   *  is how a single Accept button ends up firing the wrong call. */
  locked?: RosterEntry;
};

const refKey = (ref: IncomingRef) => `${ref.kind}/${ref.id}`;
const kindLabel = (kind: string) => KIND_LABELS[kind] ?? kind;

const STATE_LABELS: Record<State, string> = {
  conflict: "conflict", update: "update pending", new: "new in the world",
  diverged: "campaign override", insync: "following the world",
};

/** What the state means for this campaign, said as a consequence rather than as
 *  a word. The words alone do not say which side wins, which is the entire
 *  question a composition view is opened to answer. */
function stateHint(row: Row): string {
  switch (row.state) {
    case "conflict":
      return "Both sides changed. Neither wins until you choose one in World updates.";
    case "update":
      return "The world moved and this campaign's copy did not, so taking the world's loses nothing.";
    case "new":
      return "The world has this record and this campaign has no copy of it.";
    case "diverged":
      return "This campaign's copy wins: it was changed here and the world has not moved since.";
    default:
      return "Nothing pending either way.";
  }
}

/** What a version lock means, which is not what a sync state means.
 *
 *  A locked actor is pinned to one world version. Edits the world makes to THAT
 *  version show up as an incoming change like any other; a different version
 *  appearing in the world does not reach this campaign at all until somebody
 *  imports it. Saying so here is the whole reason the lock is rendered
 *  separately from the state. */
function lockHint(rec: RosterEntry): string {
  const scenes = rec.scenes.length;
  return `Pinned to world version “${rec.version}” as ${rec.role}, in `
    + `${scenes} ${scenes === 1 ? "scene" : "scenes"}. Another version of this actor `
    + "reaches the campaign only by being imported, never by accepting an update.";
}

/** Every ref this campaign holds that has something to say about itself, folded
 *  into one row each.
 *
 *  Three reads, because there are three systems and no endpoint that joins them:
 *  `/incoming` (the world moved), `/diverged` (this campaign moved) and
 *  `/appearances` (an actor is pinned to a version). A ref in more than one is
 *  ONE row — the whole point of the view is that a reader should not have to
 *  hold three lists side by side to answer "where does this record stand". */
function rowsOf(incoming: IncomingItem[], diverged: DivergedRecord[],
                roster: RosterEntry[], nameOf: (ref: IncomingRef) => string): Row[] {
  const by = new Map<string, Row>();

  for (const item of incoming) {
    const key = refKey(item.ref);
    by.set(key, { key, ref: item.ref, name: item.world.name || nameOf(item.ref),
                  state: item.status });
  }
  for (const rec of diverged) {
    const key = refKey(rec.ref);
    // Only when `/incoming` has nothing to say about it: a ref both lists claim
    // is already a conflict, and that is the stronger statement.
    if (!by.has(key))
      by.set(key, { key, ref: rec.ref, name: rec.name || nameOf(rec.ref), state: "diverged" });
  }
  for (const rec of roster) {
    const ref = { kind: rec.kind, id: rec.id };
    const key = refKey(ref);
    const found = by.get(key);
    if (found) found.locked = rec;
    else by.set(key, { key, ref, name: nameOf(ref), state: "insync", locked: rec });
  }

  return [...by.values()].sort((a, b) => {
    const ka = KIND_ORDER.indexOf(a.ref.kind), kb = KIND_ORDER.indexOf(b.ref.kind);
    if (ka !== kb) return ka - kb;
    return a.name.localeCompare(b.name);
  });
}

function Detail({ row, onReview }: { row: Row; onReview: (ref: IncomingRef) => void }) {
  const pending = row.state === "conflict" || row.state === "update" || row.state === "new";
  return (
    <div className="detail-view">
      <div className="detail-main">
        <h3>
          {row.name}
          <span className="field-hint"> · {kindLabel(row.ref.kind)}</span>
        </h3>
        <div className="side-section">
          <h4>Where it stands</h4>
          <p className="field-hint">{stateHint(row)}</p>
        </div>
        {row.locked && (
          <div className="side-section">
            <h4>Version lock</h4>
            <p className="field-hint">{lockHint(row.locked)}</p>
          </div>
        )}
      </div>
      <aside className="detail-sidebar">
        <div className="form-actions">
          {/* The diff, and the accept/reject that goes with it, live in World
              updates — one panel that owns the comparison, field by field, with
              the hint about what a blob cannot show. Sending the reader there
              on this exact ref beats a second, thinner rendering of the same
              change beside it. */}
          <button className="primary" disabled={!pending}
                  onClick={() => onReview(row.ref)}>See the change</button>
        </div>
        {!pending && (
          <p className="field-hint">
            {row.state === "diverged"
              ? "Nothing incoming to review. Promote or push this record from its own editor."
              : "Nothing incoming to review."}
          </p>
        )}
        <div className="side-section">
          <h4>Priority</h4>
          <span className={"chip composition-badge composition-" + row.state}>
            {STATE_LABELS[row.state]}
          </span>
          {row.locked && <span className="chip on">version-locked</span>}
        </div>
        <div className="side-section">
          <h4>Ref</h4>
          <span className="chip on">{refKey(row.ref)}</span>
        </div>
      </aside>
    </div>
  );
}

/** The composition view (#199): what this campaign is made of, and which side of
 *  the world/campaign split currently holds each piece.
 *
 *  Assembled client-side from `/incoming`, `/diverged` and `/appearances`
 *  because there is no endpoint that joins them — #71 proposes one, and this
 *  view is what it is for. Until then two things follow, and the panel says both
 *  rather than implying otherwise: a record the campaign follows with nothing
 *  pending is invisible here (no read enumerates the manifest), and pinning a
 *  ref is not offered (there is nothing to write it to).
 *
 *  Read-only on purpose. Accepting a world change is destructive and has no
 *  undo, and the panel that owns that decision shows the diff it turns on; a
 *  second Accept button next to a status word would be the same irreversible
 *  call made with less in front of the reader. */
export function CompositionPanel({ cid, onReview, refreshKey = 0 }: {
  cid: string; onReview: (ref: IncomingRef) => void;
  /** Bumped when something this panel reports has been resolved elsewhere —
   *  today, an accept or reject landing in the World updates panel beside it.
   *  A number rather than a callback handed the other way because the two
   *  panels never meet: their common parent is the route. */
  refreshKey?: number;
}) {
  const [rows, setRows] = useState<Row[] | null>(null);
  const [sel, setSel] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  // `cid` is a route param and `/campaigns/:cid` keeps this instance across a
  // switch, so a read started for one campaign can settle while another is on
  // screen. Same guard, and the same reason, as `IncomingReview`'s.
  const liveCid = useRef(cid);
  useEffect(() => { liveCid.current = cid; }, [cid]);

  const load = useCallback(async () => {
    const scope: EntityScope = { kind: "campaign", id: cid };
    setErr(null);
    try {
      const [incoming, diverged, roster, chars, pcs] = await Promise.all([
        api.getIncoming(cid), api.listDiverged(cid), api.listAppearances(cid),
        api.listCharacters(scope), api.listPCs(scope),
      ]);
      if (liveCid.current !== cid) return;
      // `/appearances` answers with ids and no names, deliberately: it runs over
      // the whole record on every read and a name costs a card read per actor.
      // The two listings the campaign already publishes carry them, so the name
      // is resolved here rather than paid for there.
      const names = new Map<string, string>([
        ...chars.map((c) => [`characters/${c.id}`, c.name] as const),
        ...pcs.map((p) => [`pcs/${p.id}`, p.name] as const),
      ]);
      setRows(rowsOf(incoming, diverged, roster,
                     (ref) => names.get(refKey(ref)) ?? ref.id));
    } catch (e) {
      if (liveCid.current !== cid) return;
      // Reported, not swallowed: an unread failure looks exactly like a campaign
      // with nothing outstanding, which is the one wrong answer this panel must
      // never give.
      setErr(e instanceof Error ? e.message : String(e));
      setRows([]);
    }
  }, [cid]);

  // `refreshKey` belongs to the EFFECT, not to `load`: the function it names
  // does not read it, and hanging it off the callback is a dependency lint can
  // see is unused. Bumping it re-runs the read without rebuilding the reader.
  useEffect(() => { void load(); }, [load, refreshKey]);

  const all = rows ?? [];
  const pending = all.filter((r) => r.state === "conflict" || r.state === "update"
                                    || r.state === "new");
  const conflicts = all.filter((r) => r.state === "conflict");
  const active = all.find((r) => r.key === sel) ?? null;

  return (
    <div className="composition-panel">
      <div className="incoming-head">
        <h4>Composition</h4>
        <span className="header-spacer" />
        <button className="subtle" onClick={() => void load()}>Refresh</button>
      </div>
      {err && (
        <p className="banner error-banner" role="alert">
          <span>{err}</span>
          <button className="retry" onClick={() => void load()}>Retry</button>
        </p>
      )}
      {rows === null && <p className="field-hint">Reading this campaign’s composition…</p>}
      {rows !== null && pending.length > 0 && (
        // The upgrade banner. It does not accept anything: it says how much is
        // waiting and opens the panel that shows what each change actually is.
        <div className="banner composition-banner">
          <span>
            {pending.length} update{pending.length === 1 ? "" : "s"} pending
            {conflicts.length > 0
              && `, ${conflicts.length} of them in conflict with this campaign’s own edits`}.
          </span>
          <button className="retry" onClick={() => onReview(pending[0].ref)}>
            Review world updates
          </button>
        </div>
      )}
      {rows !== null && all.length === 0 && !err && (
        <p className="field-hint">
          Nothing outstanding: no world change waiting, no flat record changed
          here, and no actor pinned to a version. A character or PC this
          campaign has edited without pinning is not among the three reads —
          see below.
        </p>
      )}
      {rows !== null && all.length > 0 && (
        <div className="editor">
          <div className="editor-list">
            {KIND_ORDER.map((kind) => {
              const group = all.filter((r) => r.ref.kind === kind);
              if (!group.length) return null;
              return (
                <div key={kind} className="side-section">
                  <h4>{kindLabel(kind)}</h4>
                  {group.map((row) => (
                    <button key={row.key} className={"row" + (row.key === sel ? " active" : "")}
                            onClick={() => setSel(row.key)}>
                      {row.name}
                      <span className={"chip composition-badge composition-" + row.state}>
                        {STATE_LABELS[row.state]}
                      </span>
                      {row.locked && <span className="chip on">v{row.locked.version}</span>}
                    </button>
                  ))}
                </div>
              );
            })}
          </div>
          <div className="editor-body">
            {active
              ? <Detail row={active} onReview={onReview} />
              : <p className="field-hint">
                  Select a record to see where it stands. Two things are absent
                  rather than in-sync, because no read this panel makes reports
                  them: a record the campaign follows with nothing pending, and
                  a character or PC edited here but never pinned —
                  <code>/diverged</code> covers flat records only, and an actor
                  carries its base in the appearance record instead. #71’s
                  endpoint is what closes both.
                </p>}
          </div>
        </div>
      )}
    </div>
  );
}
