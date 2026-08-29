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
export function ImagesView({ wid, forCampaign = null }:
                           { wid: string;
                             /** The campaign this gallery is being read FOR, when
                              *  one is open. The rail's Images row is a campaign
                              *  row that points at the world's gallery, so the
                              *  reader arriving here is usually asking about one
                              *  game's cast while looking at every record the
                              *  world has. That is the whole reason the filter
                              *  below exists; without a campaign there is no
                              *  question to ask and it is not offered. */
                             forCampaign?: string | null }) {
  const [tab, setTab] = useState<Tab>("gallery");
  const [images, setImages] = useState<GalleryImage[] | null>(null);
  const [chars, setChars] = useState<CharacterSummary[]>([]);
  const [greetings, setGreetings] = useState<Greeting[]>([]);
  const [untagged, setUntagged] = useState<Appearance[] | null>(null);
  const [kind, setKind] = useState<string | null>(null);
  const [unfinished, setUnfinished] = useState(false);
  /** `kind/id` for every actor the campaign has seated, or null when there is
   *  no campaign to ask about and when the read failed. Null both ways on
   *  purpose: the filter is only offered when it can actually be applied, and
   *  an empty set would offer a chip that hides the entire gallery. */
  const [appearedRefs, setAppearedRefs] = useState<Set<string> | null>(null);
  const [appearedOnly, setAppearedOnly] = useState(false);
  const [sel, setSel] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  // `wid` arrives from the route and this component is not remounted when a
  // campaign's fork switches world underneath it, so a read started for one
  // world can settle while another is on screen. Every commit is gated on the
  // world it was made for still being the one displayed.
  const liveWid = useRef(wid);
  useEffect(() => { liveWid.current = wid; }, [wid]);

  const scope: EntityScope = useMemo(() => ({ kind: "world", id: wid }), [wid]);

  /** Re-read the gallery only.
   *
   *  Split from the full read because `TaggingQueue` holds its own copy of the
   *  backlog and measures progress against the prop it was handed: refreshing
   *  `untagged` underneath a running queue leaves its internal list at the
   *  original length while `total` shrinks, so the second of two images
   *  announces itself as "1 / 1" — and a skipped image comes back in the
   *  refreshed prop the local list has already walked past. The backlog is
   *  re-read when the queue closes instead, which is what `GreetingEditor` has
   *  always done with the same component. */
  const loadGallery = useCallback(async () => {
    try {
      // `fresh`: this runs immediately after a subjects PUT, and the client
      // shares an in-flight GET by path. Handed one issued before that write,
      // the tile just tagged comes back still unfinished — and stays that way
      // until the view is remounted.
      // The same choice `load` makes: refreshing a campaign's gallery from the
      // world's would drop its own art on every subjects save.
      const got = forCampaign
        ? await api.listCampaignGallery(forCampaign, true)
        : await api.listWorldImages(wid, true);
      if (liveWid.current === wid) setImages(got);
    } catch {
      // Deliberately quiet: the tile this refreshes is a detail beside a save
      // that has already landed, and the next full read reports the failure.
    }
  }, [wid, forCampaign]);

  /** `fresh` when this read follows writes — closing the queue, or a Retry the
   *  reader asked for. Not on mount, where sharing an in-flight GET with
   *  whatever else asked for the same path is free and is the point of it.
   *  Only the two reads a tagging save can move take the flag; a character or
   *  greeting listing is not changed by writing subjects. */
  const load = useCallback(async (fresh = false) => {
    setErr(null);
    try {
      const [got, cs, gs, ut] = await Promise.all([
        // The campaign's own gallery when there is one: its art is in the
        // campaign root, and a world sweep cannot see it. The other three stay
        // world-scoped -- the tagging queue walks the subjects sidecar, which
        // is written world-side, and the character list is what names it.
        forCampaign ? api.listCampaignGallery(forCampaign, fresh)
                    : api.listWorldImages(wid, fresh),
        api.listCharacters(scope),
        api.listGreetings(scope), api.listUntaggedImages(wid, fresh),
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
      // never give. `err` also SUPPRESSES the empty states below — without that
      // the failure is rendered next to "this world has no art yet", which is
      // the same wrong answer in a second voice.
      setErr(e instanceof Error ? e.message : String(e));
      setImages([]);
      setUntagged([]);
    }
  }, [wid, scope, forCampaign]);

  useEffect(() => { void load(); }, [load]);

  // Its own read, deliberately outside `load`: the gallery is this view's
  // subject and the filter is an extra, so one failing must not blank the
  // other. `load` reports its failure into `err`, which suppresses the whole
  // gallery -- the right blast radius for "the world's art could not be read"
  // and much too wide for "the appearance record could not be".
  useEffect(() => {
    if (!forCampaign) { setAppearedRefs(null); setAppearedOnly(false); return; }
    let live = true;
    setAppearedRefs(null);
    setAppearedOnly(false);
    api.listAppearances(forCampaign)
      .then((roster) => {
        if (live) setAppearedRefs(new Set(roster.map((r) => `${r.kind}/${r.id}`)));
      })
      // Left null, which withdraws the chip rather than offering one that
      // would filter everything away. A reader who cannot see the control does
      // not silently get its effect.
      .catch(() => { if (live) setAppearedRefs(null); });
    return () => { live = false; };
  }, [forCampaign]);

  const charName = useCallback(
    (cid: string) => chars.find((c) => c.id === cid)?.name ?? cid, [chars]);

  const all = images ?? [];
  /** Art belonging to a record the campaign has actually seated.
   *
   *  Membership is by `kind/id`, the appearance record's own spelling, so a
   *  kind it does not store -- a location, a piece of lore, greeting art --
   *  matches nothing and drops out while the filter is on. That is the filter
   *  working, not a gap in it: "who has appeared" is a question about cast. */
  const hasAppeared = useCallback(
    (i: GalleryImage) => !!appearedRefs?.has(`${i.kind}/${i.id}`), [appearedRefs]);

  const shown = all.filter((i) => (kind === null || i.kind === kind)
                                  && (!unfinished || needsAttention(i))
                                  && (!appearedOnly || hasAppeared(i)));
  const active = shown.find((i) => imgKey(i) === sel) ?? null;
  const attention = all.filter(needsAttention).length;
  const appearedCount = appearedRefs ? all.filter(hasAppeared).length : 0;

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
          <button className="retry" onClick={() => void load(true)}>Retry</button>
        </p>
      )}
      {images === null && !err && <p className="field-hint">Reading the world’s art…</p>}
      {images !== null && !err && tab === "queue" && (
        untagged && untagged.length > 0 ? (
          <TaggingQueue wid={wid} chars={chars} greetings={greetings} queue={untagged}
                        // Closing is when the backlog is re-read: the queue has
                        // finished stepping, so nothing is measuring progress
                        // against the list any more.
                        onClose={() => { setTab("gallery"); void load(true); }}
                        // A save makes the tile it was about stop being
                        // unfinished, so the gallery is refreshed — but NOT the
                        // backlog this queue is still walking. See `loadGallery`.
                        onSaved={() => void loadGallery()} />
        ) : (
          <p className="field-hint">
            Every greeting image in this world has been tagged.
          </p>
        )
      )}
      {images !== null && !err && tab === "gallery" && (
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
            {/* Only with a campaign to ask about, and only once its roster has
                answered. Off by default: the gallery is the world's, and this
                narrows it to one game's cast because the reader said so. */}
            {appearedRefs && (
              <div className="side-section">
                <h4>This campaign</h4>
                <button className={"chip" + (appearedOnly ? " on" : "")}
                        aria-pressed={appearedOnly}
                        onClick={() => { setAppearedOnly((v) => !v); setSel(null); }}>
                  Appeared in this campaign · {appearedCount}
                </button>
              </div>
            )}
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
