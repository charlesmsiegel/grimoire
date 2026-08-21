import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { api, type EntitySummary, type SceneImportDraft } from "../api/client";
import { CalendarDatePicker } from "./CalendarDatePicker";
import { errorText } from "../api/errors";

/** Import an existing grimoire transcript as a scene (#92).
 *
 *  Read → review → import. Reading parses the file server-side and writes
 *  NOTHING; this pane is the metadata-review form over the draft that comes
 *  back, and only "Import scene" creates anything. Everything the file could
 *  not settle on its own arrives as a warning or an unmatched speaker rather
 *  than as a guess, so what the reviewer confirms is what lands.
 *
 *  Unlike `SceneConfirmForm`, the commit is ONE request: the backend creates
 *  the scene, dates it, places it, writes every post and seats the cast in one
 *  call, and removes the scene again if any of that fails. So there is no
 *  half-written scene for a dismissal or a campaign switch to strand — the
 *  reason that form needs a `writing` gate and this one does not.
 */
export function SceneImport({ cid, onBack, onCancel, onImported, onWriting }: {
  cid: string;
  /** back to the mode cards, matching what Back means in the other panes */
  onBack: () => void;
  onCancel: () => void;
  onImported: (sid: string) => void;
  /** reports the import in and out of flight, so the orchestrator can refuse to
   *  dismiss mid-write. Unmounting does not cancel the request: an Escape while
   *  it is in flight would leave a real scene that nothing is ever told about,
   *  since `onImported` is (correctly) skipped once this pane is gone. */
  onWriting?: (active: boolean) => void;
}) {
  const [draft, setDraft] = useState<SceneImportDraft | null>(null);
  const [title, setTitle] = useState("");
  const [date, setDate] = useState("");
  const [location, setLocation] = useState("");
  const [pcless, setPcless] = useState(false);
  const [seated, setSeated] = useState<string[]>([]);
  const [locations, setLocations] = useState<EntitySummary[]>([]);
  // The select can only offer what has loaded, so a location the draft resolved
  // must not reach the import before this settles -- and a draft read while it
  // was still in flight would otherwise lose its place silently.
  const [locationsLoading, setLocationsLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  // The one write here is a single request, but it still must not report a
  // scene into a `CampaignView` that has moved on: `NewSceneChooser` unmounts
  // this pane the instant its `cid` changes, so its own unmount is the only
  // signal available. A LAYOUT effect, so the flag flips inside the commit
  // that unmounts rather than in a later task a `.then` could beat, and set on
  // the way in as well as out, since StrictMode runs setup/cleanup/setup.
  const live = useRef(true);
  useLayoutEffect(() => {
    live.current = true;
    return () => { live.current = false; };
  }, []);

  useEffect(() => {
    setLocationsLoading(true);
    // A location that fails to load is not fatal: the select falls back to
    // "no location" and the scene can be placed from its own Where row later.
    api.listEntities({ kind: "campaign", id: cid }, "locations")
      .then((ls) => { setLocations(ls); setLocationsLoading(false); })
      .catch(() => { setLocations([]); setLocationsLoading(false); });
  }, [cid]);

  // A pre-filled location this campaign turns out not to have is dropped once
  // the list is known, rather than sitting in state as a value the reviewer was
  // never shown and the commit would then be refused for.
  useEffect(() => {
    if (locationsLoading) return;
    setLocation((prev) => (prev && !locations.some((l) => l.id === prev)) ? "" : prev);
  }, [locationsLoading, locations]);

  async function read() {
    const file = fileRef.current?.files?.[0];
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const d = await api.sceneImportParse(cid, file);
      if (!live.current) return;
      setDraft(d);
      setTitle(d.title);
      setDate(d.date);
      setLocation(d.location);   // dropped by the effect above if it is not offered
      setPcless(d.pcless);
      // Every matched speaker starts seated. They are the cast the transcript
      // itself names, and unchecking one is the deliberate act, not checking it.
      setSeated(d.cast.map((c) => `${c.kind}/${c.id}`));
    } catch (err) {
      if (live.current) { setDraft(null); setError(errorText(err)); }
    } finally {
      if (live.current) setBusy(false);
    }
  }

  async function commit() {
    if (!draft) return;
    setBusy(true);
    onWriting?.(true);
    setError(null);
    try {
      const { id } = await api.sceneImport(cid, {
        title: title.trim() || draft.title,
        date, location, pcless,
        messages: draft.messages,
        cast: chosen().map((c) => ({ kind: c.kind, id: c.id, role: c.role })),
      });
      if (!live.current) return;   // switched campaigns mid-import: the scene is
      onWriting?.(false);          // real, but this is no longer its campaign
      onImported(id);
    } catch (err) {
      if (live.current) { setError(errorText(err)); setBusy(false); onWriting?.(false); }
    }
  }

  /** The seats the import will actually ask for. A player is excluded while the
   *  scene is marked offscreen -- the backend refuses that seat, and a checkbox
   *  that is visibly unchecked must not send one anyway. */
  function chosen() {
    return (draft?.cast ?? []).filter(
      (c) => seated.includes(`${c.kind}/${c.id}`) && !(pcless && c.role === "player"));
  }

  function toggle(ref: string) {
    setSeated((cur) => cur.includes(ref) ? cur.filter((r) => r !== ref) : [...cur, ref]);
  }

  if (!draft) {
    return (
      <>
        {error && <div className="banner">{error}</div>}
        <div className="picker">
          <input ref={fileRef} type="file" accept=".md,.markdown,.txt,text/markdown,text/plain"
                 aria-label="Transcript file" />
          <button className="primary" disabled={busy} onClick={() => void read()}>
            {busy ? "…" : "Read file"}
          </button>
        </div>
        <div className="field-hint">
          A grimoire scene file, or one chapter of a Markdown export — a transcript of{" "}
          <code>**Speaker:**</code> blocks. Reading it writes nothing: you review what it
          found before anything is created.
        </div>
        <div className="form-actions">
          <button className="subtle" disabled={busy} onClick={onBack}>← Back</button>
          <button className="subtle" disabled={busy} onClick={onCancel}>Cancel</button>
        </div>
      </>
    );
  }

  const posts = draft.messages.length;
  // Computed once, not per row: the checkbox list and the commit have to agree
  // about which seats are actually being asked for.
  const seats = chosen();
  const opening = draft.messages[0];
  return (
    <>
      {error && <div className="banner">{error}</div>}
      {draft.warnings.map((w) => <div className="banner" key={w}>{w}</div>)}

      <label className="role" htmlFor="import-title">Title</label>
      <input id="import-title" aria-label="Title" type="text" value={title} disabled={busy}
             onChange={(e) => setTitle(e.target.value)} />

      <div className="role">When</div>
      <CalendarDatePicker scope={{ kind: "campaign", id: cid }} value={date} disabled={busy}
                          onChange={setDate} ariaLabel="Scene date" />

      <div className="role">Where</div>
      <select aria-label="Location" value={location} disabled={busy}
              onChange={(e) => setLocation(e.target.value)}>
        <option value="">— no location —</option>
        {locations.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
      </select>

      <div className="role">In this scene</div>
      {draft.cast.length === 0 && <div className="field-hint">No speaker matched this campaign.</div>}
      {draft.cast.map((c) => {
        const ref = `${c.kind}/${c.id}`;
        // A player cannot be seated in an offscreen scene (the backend refuses
        // it), so the box says so rather than letting the import fail on it.
        const blocked = pcless && c.role === "player";
        return (
          <label className="radio-row" key={ref}>
            <input type="checkbox" checked={seats.some((x) => `${x.kind}/${x.id}` === ref)}
                   disabled={busy || blocked} onChange={() => toggle(ref)}
                   aria-label={`Seat ${c.name}`} />
            {c.name}
            {c.label !== c.name && <span className="field-hint"> — written as “{c.label}”</span>}
            {blocked && <span className="field-hint"> — not in an offscreen scene</span>}
          </label>
        );
      })}
      {draft.unmatched.length > 0 && (
        <div className="field-hint">
          No one in this campaign is called {draft.unmatched.map((u) => `“${u}”`).join(", ")} —
          their posts still import, but nobody is seated for them. Add the character first if
          they should be cast.
        </div>
      )}

      <label className="radio-row">
        <input type="checkbox" checked={pcless} disabled={busy}
               onChange={(e) => setPcless(e.target.checked)} aria-label="Offscreen scene" />
        Offscreen scene (no player character)
      </label>

      <div className="field-hint">
        {posts} {posts === 1 ? "post" : "posts"} will be imported, unchanged.
      </div>
      {/* The first post, so "unchanged" is something the reviewer can check
          rather than take on trust: a file whose speakers or blocks were read
          wrongly shows it here, before anything is created. */}
      {opening && (
        <blockquote className="detail-rendered">
          <strong>{opening.speaker ?? (opening.role === "user" ? "You" : "Grimoire")}:</strong>{" "}
          {opening.content.length > 240 ? `${opening.content.slice(0, 240)}…` : opening.content}
        </blockquote>
      )}

      <div className="form-actions">
        <button className="subtle" disabled={busy} onClick={() => setDraft(null)}>← Back</button>
        <button className="subtle" disabled={busy} onClick={onCancel}>Cancel</button>
        <button className="primary" disabled={busy || locationsLoading} onClick={() => void commit()}>
          {busy ? "…" : "Import scene"}
        </button>
      </div>
    </>
  );
}
