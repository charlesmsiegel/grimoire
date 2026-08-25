import { useCallback, useEffect, useRef, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, type IncomingBlob, type IncomingItem, type IncomingRef } from "../api/client";
import { CARD_TEXT_FIELDS, PERSONA_FIELDS } from "./cardFields";

/** What a ref's kind is called in a sentence. A campaign syncs nine kinds and
 *  the ref carries the store's plural slug, which is not what a reader calls
 *  one of them. */
const KIND_LABELS: Record<string, string> = {
  locations: "Location", lore: "Lore", items: "Item", groups: "Group",
  creatures: "Creature", greetings: "Greeting", characters: "Character",
  pcs: "PC", plotmap: "Plot map",
};

const refKey = (ref: IncomingRef) => `${ref.kind}/${ref.id}`;
const kindLabel = (kind: string) => KIND_LABELS[kind] ?? kind;

/** What the status means for the reader, since the word alone does not say
 *  which side is at risk -- and the word alone is not enough to tell:
 *  `store/sync.py` grades an item by comparing hashes, and grades one whose
 *  campaign copy is *gone* a conflict, because a missing copy does not match the
 *  base either. So "both sides changed" is only true when there is a copy to
 *  have changed, and `mine` is what says whether there is.
 *
 *  `new` is in the API's vocabulary and in the world-side counts, but no pass in
 *  `incoming()` emits it today; it is handled rather than assumed away. */
function statusHint(item: IncomingItem): string {
  if (!item.mine)
    return item.status === "new"
      ? "the world has this and this campaign does not"
      : "this campaign has no copy of its own, so accepting only stops the nagging";
  if (item.status === "update")
    return "this campaign's copy is unchanged, so taking the world's loses nothing";
  return "both sides changed — accepting replaces this campaign's copy";
}

type Row = { key: string; label: string; value: string };

/** The fields one side of a change is read as.
 *
 *  Which shape arrived IS the discriminant: `store/sync.py` sends a card for a
 *  locked character version, a persona for a locked PC version, and a plain
 *  body for everything else — an entity, a plot map, or the version list of an
 *  actor whose version is not pinned. */
function rowsOf(blob: IncomingBlob | undefined, kind: string): Row[] {
  if (!blob) return [];
  if (blob.card) {
    const data = blob.card.data as Record<string, unknown>;
    return CARD_TEXT_FIELDS.map((f) => ({ ...f, value: String(data[f.key] ?? "") }));
  }
  if (blob.persona) {
    const persona = blob.persona as unknown as Record<string, unknown>;
    return PERSONA_FIELDS.map((f) => ({ ...f, value: String(persona[f.key] ?? "") }));
  }
  // The name is compared, not just printed as the heading. `entity_hash` covers
  // the whole file -- front matter included -- so a world-side *rename* is a
  // pending change whose bodies match, and showing only bodies would present it
  // as a change with nothing in it. The exception is a plot map, whose name is
  // the constant "Plot map" on both sides and so is never news.
  const name: Row[] = kind === "plotmap"
    ? [] : [{ key: "name", label: "Name", value: blob.name }];
  return [...name, { key: "body", label: "Body", value: blob.body ?? "" }];
}

/** The two sides of every field, lined up. Both sides are the same kind, so
 *  they yield the same keys; a field neither side fills is dropped rather than
 *  framed and left blank, the way the character view drops an empty one. */
function pairs(item: IncomingItem): { key: string; label: string; world: string; mine: string }[] {
  const mine = new Map(rowsOf(item.mine, item.ref.kind).map((r) => [r.key, r.value]));
  return rowsOf(item.world, item.ref.kind)
    .map((r) => ({ key: r.key, label: r.label, world: r.value, mine: mine.get(r.key) ?? "" }))
    .filter((r) => r.world.trim() || r.mine.trim());
}

/** Entity, greeting and actor-summary bodies are markdown and render as it;
 *  card and persona fields are prose the card stores verbatim (template braces
 *  and all), and a plot map is JSON — neither is markdown, so neither is put
 *  through it. */
function isMarkdown(item: IncomingItem): boolean {
  return item.ref.kind !== "plotmap" && !item.world.card && !item.world.persona;
}

/** What a change this view cannot show could have been, per kind. The hashes
 *  behind a pending item cover the whole record, and these blobs do not: a card
 *  carries greetings, tags, an embedded lorebook and `extensions` beyond its
 *  prose, and an entity blob is only `{name, body}` -- so once the name is
 *  compared too, what is left unreachable is the rest of the front matter. When
 *  every compared field matches, the change is in there somewhere, and saying
 *  nothing would let it read as no change at all. */
function invisibleChangeHint(item: IncomingItem): string {
  if (item.world.card)
    return "greetings, tags, an embedded lorebook, or other card metadata";
  if (item.world.persona) return "the persona's birthdate, which this view does not compare";
  if (item.ref.kind === "greetings")
    return "the greeting's presence list, its required tags, or its edges to other greetings";
  return "the record's keys, owners, or secrecy — front matter this view is not sent";
}

function Value({ text, markdown }: { text: string; markdown: boolean }) {
  if (!text.trim()) return <p className="field-hint">(empty)</p>;
  if (markdown)
    return (
      <div className="detail-rendered">
        <Markdown remarkPlugins={[remarkGfm]}>{text}</Markdown>
      </div>
    );
  return <pre className="incoming-text">{text}</pre>;
}

function Detail({ item, busy, onResolve }: {
  item: IncomingItem; busy: boolean;
  onResolve: (refs: IncomingRef[], accept: boolean) => void;
}) {
  const markdown = isMarkdown(item);
  const fields = pairs(item);
  // Only worth saying when there are two sides to have matched.
  const identical = Boolean(item.mine) && fields.every((f) => f.world === f.mine);
  return (
    <div className="detail-view">
      <div className="detail-main">
        <h3>
          {item.world.name}
          <span className="field-hint"> · {kindLabel(item.ref.kind)}
            {item.world.version ? ` · version ${item.world.version}` : ""}</span>
        </h3>
        {fields.length === 0 && <p className="field-hint">Nothing to compare.</p>}
        {identical && (
          <p className="banner">
            Every field below is identical. The world's change is in something
            this view is not shown: {invisibleChangeHint(item)}.
          </p>
        )}
        {fields.map((f) => (
          <div key={f.key} className="side-section">
            <h4>{f.label}</h4>
            <div className="incoming-cols">
              <div className="incoming-col">
                <div className="eyebrow">From the world</div>
                <Value text={f.world} markdown={markdown} />
              </div>
              {/* No campaign copy means one column, not a column paired with an
                  empty frame claiming a copy exists. Keyed off `mine` rather
                  than the status, because a conflict can be a missing copy. */}
              {item.mine && (
                <div className="incoming-col">
                  <div className="eyebrow">In this campaign</div>
                  <Value text={f.mine} markdown={markdown} />
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
      <aside className="detail-sidebar">
        <div className="form-actions">
          <button className="primary" disabled={busy}
                  onClick={() => onResolve([item.ref], true)}>Accept</button>
          <button className="subtle" disabled={busy}
                  onClick={() => onResolve([item.ref], false)}>Reject</button>
        </div>
        <div className="side-section">
          <h4>Status</h4>
          <span className={"chip incoming-badge incoming-" + item.status}>{item.status}</span>
          <p className="field-hint">{statusHint(item)}</p>
        </div>
        <div className="side-section">
          <h4>Ref</h4>
          <span className="chip on">{refKey(item.ref)}</span>
        </div>
      </aside>
    </div>
  );
}

/** The bulk actions, behind a confirmation.
 *
 *  Accepting is destructive and has no undo: `sync.accept` deletes the
 *  campaign's copy so the record reverts to the world's (`store/sync.py`), and
 *  no journal entry stands behind that the way one stands behind an absorb. One
 *  object at a time is an informed click -- the reader is looking at that diff.
 *  A list is not, so this one names what it is about to overwrite. */
function BulkActions({ items, busy, onResolve }: {
  items: IncomingItem[]; busy: boolean;
  onResolve: (refs: IncomingRef[], accept: boolean) => void;
}) {
  const [pending, setPending] = useState<"accept" | "reject" | null>(null);
  // A refetch takes the confirmation with it. `items` is a new array on every
  // read, and a per-object Accept is still clickable while this is open -- so
  // without this, confirming afterwards would send the refs of a list the
  // reader was never shown the count of.
  useEffect(() => { setPending(null); }, [items]);
  const refs = items.map((i) => i.ref);
  const conflicts = items.filter((i) => i.status === "conflict" && i.mine).length;

  if (pending) {
    const accept = pending === "accept";
    return (
      <>
        {/* Announced: the reader who clicked "Accept all" and cannot see the
            header change needs to hear what is being asked. */}
        <span className="field-hint" role="status" aria-live="polite">
          {accept
            ? `Replace ${refs.length} record${refs.length === 1 ? "" : "s"} in this campaign` +
              (conflicts ? `, discarding ${conflicts} the campaign changed itself?` : "?")
            : `Keep this campaign's ${refs.length} record${refs.length === 1 ? "" : "s"} ` +
              "and stop offering these changes?"}
        </span>
        <button className={accept ? "primary" : "subtle"} disabled={busy}
                onClick={() => { setPending(null); onResolve(refs, accept); }}>
          {accept ? "Yes, accept all" : "Yes, reject all"}
        </button>
        <button className="subtle" disabled={busy}
                onClick={() => setPending(null)}>Cancel</button>
      </>
    );
  }
  return (
    <>
      <button className="subtle" disabled={busy}
              onClick={() => setPending("accept")}>Accept all</button>
      <button className="subtle" disabled={busy}
              onClick={() => setPending("reject")}>Reject all</button>
    </>
  );
}

/** The campaign side of push/sync (#6): what the world has moved on to that
 *  this campaign has not taken, one object at a time.
 *
 *  Accept copies the world's content in; Reject keeps the campaign's and
 *  advances the base so the same change stops being offered. Both are the same
 *  route with a list, so "all of them" is one call rather than a loop. */
export function IncomingReview({ cid, focus, onResolved }: {
  cid: string; focus?: IncomingRef | null;
  /** Fired after an accept or reject has LANDED and this panel has re-read.
   *  The composition panel (#199) is mounted beside this one and reads the same
   *  `/incoming`, so without it a resolved change stays on its rows and in its
   *  pending count until the reader refreshes by hand -- and one accept can
   *  resolve several of its rows at once. */
  onResolved?: () => void;
}) {
  const [items, setItems] = useState<IncomingItem[] | null>(null);
  const [sel, setSel] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // `cid` is a route param, and `/campaigns/:cid` keeps this instance across a
  // switch rather than remounting it -- so a read or a resolve started for one
  // campaign can settle while another is on screen, and commit its answer into
  // the wrong panel. Every commit below is gated on the campaign it was made
  // for still being the one displayed. (CampaignView also mounts this keyed by
  // cid, which clears the panel on a switch; this is the half that holds even
  // if that key is ever dropped.) Declared before the effects that read it so
  // it is current by the time they run.
  const liveCid = useRef(cid);
  useEffect(() => { liveCid.current = cid; }, [cid]);

  const load = useCallback(async () => {
    try {
      const got = await api.getIncoming(cid);
      if (liveCid.current !== cid) return;
      setItems(got);
      setErr(null);   // a read that lands clears the last one that did not
    } catch (e) {
      if (liveCid.current !== cid) return;
      // Reported, not swallowed: an unread failure here looks exactly like a
      // campaign that is up to date, which is the one wrong answer this panel
      // must never give.
      setErr(e instanceof Error ? e.message : String(e));
      setItems([]);
    }
  }, [cid]);

  useEffect(() => { void load(); }, [load]);

  // Opened on one ref by the composition panel (#199), which is where a reader
  // reads "conflict" and asks what the conflict IS.
  //
  // CHECKED AGAINST THE LIST before it is stored, because `sel` naming nothing
  // is not inert: `active` below falls back to `items[0]`, so a ref that is not
  // here would silently open an UNRELATED change with its Accept and Reject
  // enabled. The two panels read `/incoming` separately and the other one's
  // list can be a moment older, so a stale deep link is reachable and has to
  // leave the current selection alone.
  //
  // Applied once per `focus` object, tracked by identity rather than by value:
  // `items` is in the dependency list so a focus arriving before the first read
  // lands still gets applied when it does, and without the latch every later
  // refetch would drag the selection back off whatever the reader has since
  // clicked. Re-boxing the same ref (`CampaignView` does) is a new object, so
  // pointing at it twice deliberately re-selects.
  const appliedFocus = useRef<IncomingRef | null>(null);
  useEffect(() => {
    if (!focus || !items || focus === appliedFocus.current) return;
    const key = refKey(focus);
    if (!items.some((i) => refKey(i.ref) === key)) return;
    appliedFocus.current = focus;
    setSel(key);
  }, [focus, items]);

  const resolve = useCallback(async (refs: IncomingRef[], accept: boolean) => {
    setBusy(true);
    setErr(null);
    try {
      if (accept) await api.acceptIncoming(cid, refs);
      else await api.rejectIncoming(cid, refs);
      // Re-read rather than dropping the row here: accepting one ref can
      // resolve others (a whole-actor accept dematerializes the copy every
      // locked version of that actor was diffed against), so the server's list
      // is the only one that knows what is left.
      await load();
      // After the re-read, not before: a listener re-reading on the announcement
      // should see the same server state this panel just took.
      if (liveCid.current === cid) onResolved?.();
    } catch (e) {
      if (liveCid.current === cid) setErr(e instanceof Error ? e.message : String(e));
    } finally {
      if (liveCid.current === cid) setBusy(false);
    }
  }, [cid, load, onResolved]);

  // The selection survives a refetch while its row does, and falls back to the
  // first row when it does not -- which is what resolving the selected item
  // does, every time.
  const active = items?.find((i) => refKey(i.ref) === sel) ?? items?.[0] ?? null;
  const onResolve = (refs: IncomingRef[], accept: boolean) => void resolve(refs, accept);

  return (
    <div className="incoming-panel">
      <div className="incoming-head">
        <h4>Incoming world changes</h4>
        <span className="header-spacer" />
        {items && items.length > 1 && (
          <BulkActions items={items} busy={busy} onResolve={onResolve} />
        )}
      </div>
      {err && (
        <p className="banner error-banner">
          <span>{err}</span>
          {/* Without this a failed read is a dead panel: nothing else here asks
              again, so the only retry would be closing and reopening it. */}
          <button className="retry" disabled={busy} onClick={() => void load()}>Retry</button>
        </p>
      )}
      {items === null && <p className="field-hint">Checking the world…</p>}
      {items !== null && items.length === 0 && !err && (
        <p className="field-hint">This campaign is up to date with its world.</p>
      )}
      {items !== null && items.length > 0 && (
        <div className="editor">
          <div className="editor-list">
            {items.map((item) => {
              const key = refKey(item.ref);
              return (
                <button key={key} className={"row" + (active && key === refKey(active.ref) ? " active" : "")}
                        onClick={() => setSel(key)}>
                  {item.world.name}
                  <span className="field-hint"> · {kindLabel(item.ref.kind)}</span>
                  <span className={"chip incoming-badge incoming-" + item.status}>{item.status}</span>
                </button>
              );
            })}
          </div>
          <div className="editor-body">
            {active && <Detail item={active} busy={busy} onResolve={onResolve} />}
          </div>
        </div>
      )}
    </div>
  );
}
