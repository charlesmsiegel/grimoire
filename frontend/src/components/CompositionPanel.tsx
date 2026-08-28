import { useCallback, useEffect, useRef, useState } from "react";
import { api, type CompositionLock, type CompositionRow, type CompositionState,
  type IncomingRef } from "../api/client";

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

type Row = CompositionRow & { key: string };

const refKey = (ref: IncomingRef) => `${ref.kind}/${ref.id}`;
const kindLabel = (kind: string) => KIND_LABELS[kind] ?? kind;

const STATE_LABELS: Record<CompositionState, string> = {
  conflict: "conflict", update: "update pending",
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
function lockHint(rec: CompositionLock): string {
  const scenes = rec.scenes.length;
  return `Pinned to world version “${rec.version}” as ${rec.role}, in `
    + `${scenes} ${scenes === 1 ? "scene" : "scenes"}. Another version of this actor `
    + "reaches the campaign only by being imported, never by accepting an update.";
}

function Detail({ row, busy, onReview, onPin }: {
  row: Row; busy: boolean; onReview: (ref: IncomingRef) => void;
  onPin: (row: Row, pinned: boolean) => void;
}) {
  const pending = row.state === "conflict" || row.state === "update";
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
          {row.pinned && (
            <p className="field-hint">
              Pinned: world updates for this record are not offered while the
              pin holds. Nothing is rejected — resuming restores exactly the
              update that was waiting.
            </p>
          )}
        </div>
        {row.lock && (
          <div className="side-section">
            <h4>Version lock</h4>
            <p className="field-hint">{lockHint(row.lock)}</p>
          </div>
        )}
      </div>
      <aside className="detail-sidebar">
        <div className="form-actions">
          {/* The diff, and the accept/reject that goes with it, live in World
              updates — one panel that owns the comparison, field by field, with
              the hint about what a blob cannot show. Sending the reader there
              on this exact ref beats a second, thinner rendering of the same
              change beside it. A pinned ref's change is held out of that panel
              by the pin itself, so the door is closed while the pin holds. */}
          <button className="primary" disabled={!pending || row.pinned}
                  onClick={() => onReview(row.ref)}>See the change</button>
          <button className="subtle" disabled={busy}
                  onClick={() => onPin(row, !row.pinned)}>
            {row.pinned ? "Resume world updates" : "Stop offering world updates"}
          </button>
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
          {row.pinned && <span className="chip on">pinned</span>}
          {row.lock && <span className="chip on">version-locked</span>}
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
 *  One read, `/composition` (#71): every sync.md ref plus every version-locked
 *  actor, each with the state the engine's own hash comparison derives — so a
 *  record the campaign follows with nothing pending IS a row here, and so is a
 *  character edited without ever being version-locked, the two answers the
 *  panel could not give while it joined `/incoming` and `/diverged` by hand.
 *
 *  Read-only for accept, on purpose. Accepting a world change is destructive
 *  and has no undo, and the panel that owns that decision shows the diff it
 *  turns on; a second Accept button next to a status word would be the same
 *  irreversible call made with less in front of the reader. The one write this
 *  panel makes is the pin — reversible by construction, since it never touches
 *  a base hash. */
export function CompositionPanel({ cid, onReview, onPinned, refreshKey = 0 }: {
  cid: string; onReview: (ref: IncomingRef) => void;
  /** Fired after a pin toggle has landed and this panel has re-read: a pin
   *  changes what `/incoming` answers, and `IncomingReview` is mounted beside
   *  this panel over that same read — the mirror of its `onResolved`. */
  onPinned?: () => void;
  /** Bumped when something this panel reports has been resolved elsewhere —
   *  today, an accept or reject landing in the World updates panel beside it.
   *  A number rather than a callback handed the other way because the two
   *  panels never meet: their common parent is the route. */
  refreshKey?: number;
}) {
  const [rows, setRows] = useState<Row[] | null>(null);
  const [sel, setSel] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // `cid` is a route param and `/campaigns/:cid` keeps this instance across a
  // switch, so a read started for one campaign can settle while another is on
  // screen. Same guard, and the same reason, as `IncomingReview`'s.
  const liveCid = useRef(cid);
  useEffect(() => { liveCid.current = cid; }, [cid]);

  const load = useCallback(async () => {
    setErr(null);
    try {
      const got = await api.getComposition(cid);
      if (liveCid.current !== cid) return;
      setRows(got.rows
        .map((r) => ({ ...r, key: refKey(r.ref) }))
        .sort((a, b) => {
          const ka = KIND_ORDER.indexOf(a.ref.kind), kb = KIND_ORDER.indexOf(b.ref.kind);
          if (ka !== kb) return ka - kb;
          return a.name.localeCompare(b.name);
        }));
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

  const pin = useCallback(async (row: Row, pinned: boolean) => {
    setBusy(true);
    try {
      await api.setSyncPin(cid, row.ref, pinned);
      if (liveCid.current !== cid) return;
      await load();
      onPinned?.();
    } catch (e) {
      if (liveCid.current !== cid) return;
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      if (liveCid.current === cid) setBusy(false);
    }
  }, [cid, load, onPinned]);

  const all = rows ?? [];
  // A pinned ref's pending change is deliberately out of this count: the
  // banner advertises what the review panel will show, and the pin holds that
  // change out of it.
  const pending = all.filter((r) => !r.pinned
                                    && (r.state === "conflict" || r.state === "update"));
  const conflicts = pending.filter((r) => r.state === "conflict");
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
          Nothing here yet. A record joins the composition when this campaign
          takes a copy of its own — an edit here, or a version lock on an actor
          — and until then it simply follows the world through the overlay.
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
                      {row.pinned && <span className="chip on">pinned</span>}
                      {row.lock && <span className="chip on">v{row.lock.version}</span>}
                    </button>
                  ))}
                </div>
              );
            })}
          </div>
          <div className="editor-body">
            {active
              ? <Detail row={active} busy={busy} onReview={onReview} onPin={(r, p) => void pin(r, p)} />
              : <p className="field-hint">Select a record to see where it stands.</p>}
          </div>
        </div>
      )}
    </div>
  );
}
