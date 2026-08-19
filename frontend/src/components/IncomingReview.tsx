import { useCallback, useEffect, useState } from "react";
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

/** What each status means for the reader, since the word alone does not say
 *  which side is at risk. */
const STATUS_HINTS: Record<string, string> = {
  new: "the world has this and this campaign does not",
  update: "this campaign's copy is unchanged, so taking the world's loses nothing",
  conflict: "both sides changed — accepting replaces this campaign's copy",
};

const refKey = (ref: IncomingRef) => `${ref.kind}/${ref.id}`;
const kindLabel = (kind: string) => KIND_LABELS[kind] ?? kind;

type Row = { key: string; label: string; value: string };

/** The fields one side of a change is read as.
 *
 *  Which shape arrived IS the discriminant: `store/sync.py` sends a card for a
 *  locked character version, a persona for a locked PC version, and a plain
 *  body for everything else — an entity, a plot map, or the version list of an
 *  actor whose version is not pinned. */
function rowsOf(blob: IncomingBlob | undefined): Row[] {
  if (!blob) return [];
  if (blob.card) {
    const data = blob.card.data as Record<string, unknown>;
    return CARD_TEXT_FIELDS.map((f) => ({ ...f, value: String(data[f.key] ?? "") }));
  }
  if (blob.persona) {
    const persona = blob.persona as unknown as Record<string, unknown>;
    return PERSONA_FIELDS.map((f) => ({ ...f, value: String(persona[f.key] ?? "") }));
  }
  return [{ key: "body", label: "Body", value: blob.body ?? "" }];
}

/** The two sides of every field, lined up. Both sides are the same kind, so
 *  they yield the same keys; a field neither side fills is dropped rather than
 *  framed and left blank, the way the character view drops an empty one. */
function pairs(item: IncomingItem): { key: string; label: string; world: string; mine: string }[] {
  const mine = new Map(rowsOf(item.mine).map((r) => [r.key, r.value]));
  return rowsOf(item.world)
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
  return (
    <div className="detail-view">
      <div className="detail-main">
        <h3>
          {item.world.name}
          <span className="field-hint"> · {kindLabel(item.ref.kind)}
            {item.world.version ? ` · version ${item.world.version}` : ""}</span>
        </h3>
        {fields.length === 0 && <p className="field-hint">Nothing to compare.</p>}
        {fields.map((f) => (
          <div key={f.key} className="side-section">
            <h4>{f.label}</h4>
            <div className="incoming-cols">
              <div className="incoming-col">
                <div className="eyebrow">From the world</div>
                <Value text={f.world} markdown={markdown} />
              </div>
              {/* A `new` item has no campaign side, so it is one column and not
                  a column paired with an empty frame claiming a copy exists. */}
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
          <h4>{item.status}</h4>
          <p className="field-hint">{STATUS_HINTS[item.status] ?? ""}</p>
        </div>
        <div className="side-section">
          <h4>Ref</h4>
          <span className="chip on">{refKey(item.ref)}</span>
        </div>
      </aside>
    </div>
  );
}

/** The campaign side of push/sync (#6): what the world has moved on to that
 *  this campaign has not taken, one object at a time.
 *
 *  Accept copies the world's content in; Reject keeps the campaign's and
 *  advances the base so the same change stops being offered. Both are the same
 *  route with a list, so "all of them" is one call rather than a loop. */
export function IncomingReview({ cid }: { cid: string }) {
  const [items, setItems] = useState<IncomingItem[] | null>(null);
  const [sel, setSel] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setItems(await api.getIncoming(cid));
    } catch (e) {
      // Reported, not swallowed: an unread failure here looks exactly like a
      // campaign that is up to date, which is the one wrong answer this panel
      // must never give.
      setErr(e instanceof Error ? e.message : String(e));
      setItems([]);
    }
  }, [cid]);

  useEffect(() => {
    setErr(null);
    void load();
  }, [load]);

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
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [cid, load]);

  // The selection survives a refetch while its row does, and falls back to the
  // first row when it does not -- which is what resolving the selected item
  // does, every time.
  const active = items?.find((i) => refKey(i.ref) === sel) ?? items?.[0] ?? null;
  const allRefs = (items ?? []).map((i) => i.ref);

  return (
    <div className="incoming-panel">
      <div className="incoming-head">
        <h4>Incoming world changes</h4>
        <span className="header-spacer" />
        {allRefs.length > 1 && (
          <>
            <button className="subtle" disabled={busy}
                    onClick={() => void resolve(allRefs, true)}>Accept all</button>
            <button className="subtle" disabled={busy}
                    onClick={() => void resolve(allRefs, false)}>Reject all</button>
          </>
        )}
      </div>
      {err && <p className="banner error-banner">{err}</p>}
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
            {active && <Detail item={active} busy={busy} onResolve={(refs, accept) => void resolve(refs, accept)} />}
          </div>
        </div>
      )}
    </div>
  );
}
