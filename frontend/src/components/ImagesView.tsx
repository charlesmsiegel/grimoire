import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type Appearance, type CharacterSummary, type EntityScope,
  type GalleryImage, type Greeting } from "../api/client";
import { TaggingQueue } from "./TaggingQueue";

/** What a base is called in the rail. The gallery spans eight of them and the
 *  row carries the store's slug, which is not what a reader calls a group of
 *  pictures. Keyed by the slug rather than listed so the labels and the counts
 *  cannot disagree about which groups exist. */
const KIND_LABELS: Record<string, string> = {
  characters: "Characters", pcs: "PCs", locations: "Locations", items: "Items",
  lore: "Lore", groups: "Groups", creatures: "Creatures", greetings: "Greetings",
};

/** The rail's order. The gallery arrives grouped by base already (the route
 *  sweeps them in a fixed order), and this is that order named once so a kind
 *  the server starts sending — or stops — cannot silently reorder the rail. */
const KINDS = Object.keys(KIND_LABELS);

type Tab = "gallery" | "queue";

/** What still wants a human: art nobody has described, and greeting art nobody
 *  has said who is in.
 *
 *  Two different sidecars and deliberately one filter, because they are the
 *  same question from the reader's side — "what have I not finished?" — and a
 *  gallery that made you pick which kind of unfinished you meant would be
 *  asking you to know the storage layout.
 *
 *  Which sidecar applies is decided by the kind, not merged. A greeting image is
 *  judged ONLY on its subjects: nothing in this app describes greeting art —
 *  there is no route for it, and the describe backlog does not walk that base —
 *  so counting one undescribed would file every greeting image in a backlog
 *  that can never be worked. Everything else is judged only on `described`,
 *  which is key presence: an image reviewed and left blank is finished. */
function needsAttention(img: GalleryImage): boolean {
  if (img.kind === "greetings")
    return img.subjects === undefined || img.subjects === null;
  return !img.described;
}

const imgKey = (img: GalleryImage) => `${img.kind}/${img.id}/${img.vid}/${img.name}`;

/** What a screen reader is told the picture is.
 *
 *  The author's description when there is one — it is a sentence written about
 *  this exact art, which is the best alt text there could be. Otherwise the
 *  record and the slot, because a bare `gallery_1` names the same thing for
 *  every record in the world and tells a reader who cannot see the grid
 *  nothing at all about which tile they are on. */
const altOf = (img: GalleryImage) => img.description || `${img.record_name} — ${img.name}`;

function Detail({ img, charName, onBack }: {
  img: GalleryImage; charName: (cid: string) => string; onBack: () => void;
}) {
  return (
    <div className="detail-view">
      <div className="detail-main">
        <h3>
          {img.record_name}
          <span className="field-hint"> · {KIND_LABELS[img.kind] ?? img.kind}</span>
        </h3>
        {/* The full-resolution image, not the tile's thumbnail: opening one is
            the whole reason a grid of downscales is acceptable. */}
        <img className="gallery-full" src={img.url} alt={altOf(img)} />
      </div>
      <aside className="detail-sidebar">
        {/* Where Edit sits in every other detail view. There is nothing to edit
            here — the gallery is a browser, and the two sidecars it reports are
            written in the editors that own them — so this is the way back to
            the grid instead. */}
        <div className="form-actions">
          <button onClick={onBack}>Back to the gallery</button>
        </div>
        <div className="side-section">
          <h4>Image</h4>
          <span className="chip on">{img.name}</span>
          {/* Entity and greeting art is keyed on a fixed `default`, so naming
              its "version" would be an implementation detail dressed as a fact
              about the picture. */}
          {img.vid !== "default" && <span className="chip on">version {img.vid}</span>}
          <span className="chip on">{img.ext}</span>
        </div>
        {/* Greeting art with no description gets no section: nothing describes
            it, so "not described yet" would be a backlog entry rather than a
            fact. A description that IS there — written by hand into the store —
            is still shown. */}
        {(img.description || img.kind !== "greetings") && (
          <div className="side-section">
            <h4>Description</h4>
            {img.description
              ? <p className="field-hint">{img.description}</p>
              : <p className="field-hint">
                  {img.described
                    ? "Reviewed, deliberately left blank — it is not offered to the model."
                    : "Not described yet. Until it is, no model handle can reach it."}
                </p>}
          </div>
        )}
        {img.kind === "greetings" && (
          <div className="side-section">
            <h4>Subjects</h4>
            {img.subjects === undefined || img.subjects === null
              ? <p className="field-hint">Not tagged yet — the tagging queue will ask about it.</p>
              : img.subjects.length === 0
                ? <p className="field-hint">Tagged: nobody in this one.</p>
                : img.subjects.map((cid) => (
                    <span key={cid} className="chip on">{charName(cid)}</span>
                  ))}
          </div>
        )}
      </aside>
    </div>
  );
}

/** The Images view (#200): every picture a world holds, and the queue of the
 *  ones still missing an answer.
 *
 *  Generation is deliberately absent rather than stubbed. The pre-rebuild
 *  feature list paired this gallery with a generation queue and per-character
 *  prompt templates; there is no image generation in this app, so a "Generate"
 *  affordance here would be a control that cannot do anything and a data model
 *  shaped for a backend that does not exist.
 *
 *  The queue tab is `TaggingQueue` itself, not a second copy of it: it already
 *  steps through exactly this backlog, and it is reached from the greeting
 *  editor too — the same queue from either door, so finishing it in one empties
 *  it in the other. */
export function ImagesView({ wid }: { wid: string }) {
  const [tab, setTab] = useState<Tab>("gallery");
  const [images, setImages] = useState<GalleryImage[] | null>(null);
  const [chars, setChars] = useState<CharacterSummary[]>([]);
  const [greetings, setGreetings] = useState<Greeting[]>([]);
  const [untagged, setUntagged] = useState<Appearance[] | null>(null);
  const [kind, setKind] = useState<string | null>(null);
  const [unfinished, setUnfinished] = useState(false);
  const [sel, setSel] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  // `wid` arrives from the route and this component is not remounted when a
  // campaign's fork switches world underneath it, so a read started for one
  // world can settle while another is on screen. Every commit is gated on the
  // world it was made for still being the one displayed.
  const liveWid = useRef(wid);
  useEffect(() => { liveWid.current = wid; }, [wid]);

  const scope: EntityScope = useMemo(() => ({ kind: "world", id: wid }), [wid]);

  const load = useCallback(async () => {
    setErr(null);
    try {
      const [got, cs, gs, ut] = await Promise.all([
        api.listWorldImages(wid), api.listCharacters(scope),
        api.listGreetings(scope), api.listUntaggedImages(wid),
      ]);
      if (liveWid.current !== wid) return;
      setImages(got);
      setChars(cs);
      setGreetings(gs);
      setUntagged(ut);
    } catch (e) {
      if (liveWid.current !== wid) return;
      // Reported rather than swallowed: a failed read here looks exactly like a
      // world with no art in it, which is the one wrong answer this view must
      // never give.
      setErr(e instanceof Error ? e.message : String(e));
      setImages([]);
      setUntagged([]);
    }
  }, [wid, scope]);

  useEffect(() => { void load(); }, [load]);

  const charName = useCallback(
    (cid: string) => chars.find((c) => c.id === cid)?.name ?? cid, [chars]);

  const all = images ?? [];
  const shown = all.filter((i) => (kind === null || i.kind === kind)
                                  && (!unfinished || needsAttention(i)));
  const active = shown.find((i) => imgKey(i) === sel) ?? null;
  const attention = all.filter(needsAttention).length;

  return (
    <div className="images-view">
      <div className="tabs" role="tablist" aria-label="Images">
        {([["gallery", "Gallery"], ["queue", "Tagging queue"]] as [Tab, string][])
          .map(([key, label]) => (
            <button key={key} role="tab" aria-selected={tab === key}
                    className={"tab" + (tab === key ? " active" : "")}
                    onClick={() => setTab(key)}>
              {label}
              {key === "queue" && untagged && untagged.length > 0 && (
                <span className="chip on">{untagged.length}</span>
              )}
            </button>
          ))}
      </div>
      {err && (
        <p className="banner error-banner" role="alert">
          <span>{err}</span>
          {/* Without this a failed read is a dead view: nothing else here asks
              again, so the only retry would be leaving the section. */}
          <button className="retry" onClick={() => void load()}>Retry</button>
        </p>
      )}
      {images === null && <p className="field-hint">Reading the world’s art…</p>}
      {images !== null && tab === "queue" && (
        untagged && untagged.length > 0 ? (
          <TaggingQueue wid={wid} chars={chars} greetings={greetings} queue={untagged}
                        onClose={() => setTab("gallery")}
                        // A save changes both halves of this view — the queue
                        // shrinks and the tile it was about stops being
                        // unfinished — so the read is the whole read, not a
                        // patch of the row.
                        onSaved={() => void load()} />
        ) : (
          <p className="field-hint">
            Every greeting image in this world has been tagged.
          </p>
        )
      )}
      {images !== null && tab === "gallery" && (
        <div className="editor">
          <div className="editor-list">
            <button className={"row" + (kind === null ? " active" : "")}
                    onClick={() => { setKind(null); setSel(null); }}>
              All images
              <span className="field-hint"> · {all.length}</span>
            </button>
            {KINDS.map((k) => {
              const n = all.filter((i) => i.kind === k).length;
              if (n === 0) return null;   // a kind with no art is not a filter
              return (
                <button key={k} className={"row" + (kind === k ? " active" : "")}
                        onClick={() => { setKind(k); setSel(null); }}>
                  {KIND_LABELS[k]}
                  <span className="field-hint"> · {n}</span>
                </button>
              );
            })}
            <div className="side-section">
              <h4>Unfinished</h4>
              <button className={"chip" + (unfinished ? " on" : "")}
                      aria-pressed={unfinished}
                      onClick={() => { setUnfinished((v) => !v); setSel(null); }}>
                Needs a description or subjects · {attention}
              </button>
            </div>
          </div>
          <div className="editor-body">
            {active ? (
              <Detail img={active} charName={charName} onBack={() => setSel(null)} />
            ) : shown.length === 0 ? (
              <p className="field-hint">
                {all.length === 0
                  ? "This world has no art yet."
                  : "Nothing here — every image in this filter is finished."}
              </p>
            ) : (
              <div className="gallery-grid">
                {shown.map((img) => (
                  <button key={imgKey(img)} className="gallery-tile"
                          onClick={() => setSel(imgKey(img))}>
                    {/* The `?w=` downscale, not the original: a world's whole
                        gallery at full resolution is tens of megabytes for
                        pictures drawn at 154px. */}
                    <img src={img.thumb} alt={altOf(img)} loading="lazy" />
                    <span>{img.record_name}</span>
                    <span className="field-hint">{img.name}</span>
                    {needsAttention(img) && <span className="chip">unfinished</span>}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
