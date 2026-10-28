import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ApiError, api, type Appearance, type CharacterSummary, type Edges, type EntityScope, type EntitySummary, type Greeting, type GreetingMark } from "../api/client";
import { errorText } from "../api/errors";
import { encodeSegment } from "../urlSegment";
import { DemotePanel } from "./DemotePanel";
import { GreetingMarkdown } from "./GreetingMarkdown";
import { LibraryPanel } from "./LibraryPanel";
import { StaleRecordBanner } from "./StaleRecordBanner";
import { SubjectsPopover } from "./SubjectsPopover";
import { TaggingQueue } from "./TaggingQueue";
import { useCreationRequest } from "./useCreationRequest";
import { BLANK, NO_EDGES, greetingDraft, sameEdges } from "./greetingEditor/form";
import { GreetingFields } from "./greetingEditor/GreetingFields";
import { GreetingRail } from "./greetingEditor/GreetingRail";

export function GreetingEditor({ scope, wid, onOpenCharacter, onOpenLocation, selected, isNew = false, sectionPath, recordHref,
                                 onChanged, onBusy, onEdgeDraft, hold = null, refreshKey = 0 }:
  { scope: EntityScope; wid: string;
    onOpenCharacter?: (cid: string, vid: string) => void;
    onOpenLocation?: (eid: string) => void;
    /** The record the URL names, or null for the section's own screen --
     *  which browses until New is requested. */
    selected?: string | null;
    isNew?: boolean;
    /** This section's own address, for `+ New`. */
    sectionPath: string;
    /** ...and one record's, for a rail row. A CALLBACK rather than a string
     *  this file concatenates onto `sectionPath`: `sectionHref` is the only
     *  thing allowed to build these paths, and an id containing a slash or a
     *  space is exactly what hand-joining gets wrong. */
    recordHref: (rid: string) => string;
    /** Fired once a write here has landed, so a second view of the same
     *  records can re-read (#9: the plot map draws these greetings' edges,
     *  and a save that lands while it is open would leave it holding a
     *  snapshot its next whole-array write would send back). */
    onChanged?: () => void;
    /** True while a write here is in flight. The plot map is the other writer
     *  of these same edges, and both send WHOLE arrays -- so one has to hold
     *  still while the other is mid-write (#9). */
    onBusy?: (busy: boolean) => void;
    /** True while this form holds edge changes it has not saved. The plot map
     *  has to hold still for those: this save replaces the greeting's whole
     *  arrays, so a draft that predates a graph edit would undo it. */
    onEdgeDraft?: (dirty: boolean) => void;
    /** Why this editor may not write right now -- the map has a request of its
     *  own still on the wire, and the two would overlap. */
    hold?: string | null;
    /** Bumped when that other view writes. A greeting open in `view` mode is
     *  re-read; a draft in `edit` mode is never touched. */
    refreshKey?: number }) {
  const navigate = useNavigate();
  const worldScope = scope.kind === "world";
  const [greetings, setGreetings] = useState<Greeting[]>([]);
  const [chars, setChars] = useState<CharacterSummary[]>([]);
  const [locations, setLocations] = useState<EntitySummary[]>([]);
  // Whether that read has come back CLEANLY. An empty list means three
  // different things -- not read yet, read and failed, read and there are
  // none -- and only the third licenses telling the reader their greeting's
  // location is missing.
  const [locationsRead, setLocationsRead] = useState(false);
  const [tags, setTags] = useState<Record<string, string>>({});
  const [gid, setGid] = useState<string | null>(null); // null = new
  const [form, setForm] = useState(BLANK);
  const [edges, setEdges] = useState<Edges>(NO_EDGES);
  /** The edges as `select` read them. A save resends the whole pair of arrays,
   *  which is how a chip list nobody touched can undo an edge the plot map
   *  wrote after it was loaded -- so an unchanged list is not sent at all. */
  const [loadedEdges, setLoadedEdges] = useState<Edges>(NO_EDGES);
  const [predecessors, setPredecessors] = useState<string[]>([]);
  const [mode, setMode] = useState<"view" | "edit">("edit"); // existing greetings open in view
  const [error, setError] = useState<string | null>(null);
  // The rev of the greeting as loaded, echoed back on save so a write cannot
  // land on top of an edit made outside the app (#35).
  const [rev, setRev] = useState<string | null>(null);
  const [stale, setStale] = useState<{ rev: string | null } | null>(null);
  const { begin: beginSave, retire: retireSave } = useCreationRequest(
    selected || isNew ? `${scope.kind}:${scope.id}:${selected ?? "new"}` : null, { keepOnQueryChange: true });
  /** Bumped by anything that means "the reader is looking at something else
   *  now", so an answer to an older question can tell that it is stale. */
  const intent = useRef(0);
  /** How many writes from here are in flight. A COUNT rather than a flag: two
   *  overlapping saves both report busy, and the first to finish would
   *  otherwise report the pair done -- freeing the plot map to write while the
   *  second is still approaching its own `setEdges`. */
  const writes = useRef(0);
  const [writing, setWriting] = useState(false);

  function beginWrite() {
    writes.current += 1;
    setWriting(true);
    onBusy?.(true);
  }
  function endWrite() {
    writes.current -= 1;
    if (writes.current <= 0) {
      writes.current = 0;
      setWriting(false);
      onBusy?.(false);
    }
  }
  const [subjects, setSubjects] = useState<Record<string, string[]>>({});
  const [picking, setPicking] = useState<string | null>(null); // image name being edited
  const [untagged, setUntagged] = useState<Appearance[]>([]);
  const [queueOpen, setQueueOpen] = useState(false);
  // Both lists, because the rail's verdict needs both: greetings are what it
  // counts, and character names are half of what search matches -- so before
  // they land, a query on a character reads as "no matches" while it is really
  // "not looked yet". Only the scope effect clears this; the `reload()` calls
  // that follow a save must not put the rail back into a loading state.
  const [listsReady, setListsReady] = useState(false);

  const scopeKey = `${scope.kind}:${scope.id}`;
  const { kind: scopeKind, id: scopeId } = scope;
  const currentScope = useRef(scopeKey);
  currentScope.current = scopeKey;
  const currentGreeting = useRef({ gid, selected, scopeKey });
  currentGreeting.current = { gid, selected, scopeKey };
  const catalogEpoch = useRef(0);
  const catalogRequest = useRef(0);
  const reload = useCallback(async (still: () => boolean = () => true) => {
    // A write from a retired scope may finish after the next scope started
    // loading. It must not take that visit's catalog token before we reject it.
    if (currentScope.current !== scopeKey || !still()) return;
    const epoch = catalogEpoch.current;
    const request = ++catalogRequest.current;
    const list = await api.listGreetings({ kind: scopeKind, id: scopeId });
    // Equal scope names do not identify one visit (A → B → A), and a newer
    // refresh owns the list even when an older response arrives last.
    if (epoch === catalogEpoch.current && request === catalogRequest.current
        && currentScope.current === scopeKey && still()) setGreetings(list);
  }, [scopeKind, scopeId, scopeKey]);
  useEffect(() => {
    let current = true;
    // This ref is a lifetime counter, not a DOM node. Retire callbacks from
    // the effect being cleaned up, including a return to the same scope.
    const lifetime = catalogEpoch;
    setListsReady(false);
    setGreetings([]);
    setChars([]);
    setTags({});
    setLocations([]);
    setUntagged([]);
    // Both searches must settle, including a failed one. Promise.all would
    // call the rail ready as soon as one fails while the other still loads.
    void Promise.allSettled([reload(), api.listCharacters(scope).then((items) => {
      if (current) setChars(items);
    })]).then(() => { if (current) setListsReady(true); });
    // Tag vocabulary stays a world concern. When it is unavailable, saved
    // references still render their raw ids rather than becoming an error
    // from an unrelated or retired scope.
    void api.listTags(wid).then((items) => { if (current) setTags(items); },
                              () => { if (current) setTags({}); });
    // Not in the `listsReady` gate above: that gate exists so the rail's "no
    // matches" verdict waits for what it SEARCHES (names and characters), and
    // locations are neither. A failed read leaves the picker empty; the stored
    // id still renders, as its raw slug and without the "(missing)" the
    // picker has not earned the right to say.
    setLocationsRead(false);
    api.listEntities(scope, "locations")
      .then((ls) => { if (current) { setLocations(ls); setLocationsRead(true); } })
      .catch(() => { if (current) setLocations([]); });
    if (worldScope) api.listUntaggedImages(wid)
      .then((items) => { if (current) setUntagged(items); })
      .catch(() => { if (current) setUntagged([]); });
    return () => { current = false; lifetime.current++; };
  }, [wid, worldScope, reload]);  // eslint-disable-line react-hooks/exhaustive-deps -- scope is captured by reload

  function closeQueue() {
    setQueueOpen(false);
    const epoch = catalogEpoch.current;
    api.listUntaggedImages(wid)
      .then((items) => { if (epoch === catalogEpoch.current) setUntagged(items); })
      .catch(() => { if (epoch === catalogEpoch.current) setUntagged([]); });
  }

  function queueSaved(savedGid: string) {
    // The queue's save landed; a re-read that fails leaves the chips as they
    // were read, until the greeting is next opened.
    // Close can retire the queue while its PUT is pending. Its old callback
    // must still address the loaded and selected greeting before starting a
    // new read; taking the latest sequence alone would adopt the next view.
    const view = currentGreeting.current;
    if (savedGid !== view.gid || savedGid !== view.selected || scopeKey !== view.scopeKey) return;
    const request = selectionRead.current;
    if (savedGid === gid) api.getGreetingSubjects(wid, savedGid)
      .then((items) => { if (request === selectionRead.current) setSubjects(items); })
      .catch(() => {}); // optional refresh: retain the selected record's chips
  }

  /** Bumped on every selection change, the null one included. Without that,
   *  navigating `/greetings/tide-watch` -> `/greetings` resets the form and
   *  then Tide Watch's slower read lands and reopens it at an address naming
   *  no greeting. */
  const selReq = useRef(0);
  useEffect(() => {
    const req = ++selReq.current;
    if (selected) {
      // A bookmarked/reload address is exactly what this branch made
      // possible -- so a read that 404s here has to say so rather than
      // leaving a blank "new greeting" form up with nothing to explain it.
      void select(selected, () => req === selReq.current)
        .catch((err: unknown) => { if (req === selReq.current) setError(errorText(err)); });
    } else resetForm();
    return () => { selectionRead.current += 1; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected, isNew, scope.kind, scope.id]);

  // The other view of these records wrote (#9). A greeting being READ is
  // re-read, so its chips say what the store now says; a greeting being
  // EDITED is left exactly as it is -- a draft is the one thing here that
  // exists nowhere else, and its save no longer resends untouched edges.
  const firstRefresh = useRef(true);
  useEffect(() => {
    if (firstRefresh.current) { firstRefresh.current = false; return; }
    // Checked again when the READ COMES BACK, not only before it is sent: a
    // slow refresh that started while the greeting was being read would
    // otherwise land on a draft the reader began in the meantime -- replacing
    // the form and dropping `mode` back to view, which is exactly the loss
    // this branch exists to prevent.
    const mine = ++intent.current;
    if (!gid) return;
    if (mode === "view") {
      void reload();
      void select(gid, () => intent.current === mine);
      return;
    }
    // A draft whose chips are still untouched is not an opinion about the plot
    // map, and leaving it on a pre-write snapshot is how touching one chip
    // later sends the whole stale pair of arrays back -- undoing the graph edit
    // without ever showing it. So the EDGES are rebased and the form is not:
    // nothing the reader typed moves, and what they did not touch stops being
    // a stale claim.
    if (sameEdges(edges, loadedEdges)) {
      void api.readGreeting(scope, gid).then((g) => {
        if (intent.current !== mine) return;
        setEdges(g.edges);
        setLoadedEdges(g.edges);
        setPredecessors(g.predecessors ?? []);
      }).catch(() => {});   // the draft keeps the edges it had: no worse than before the rebase
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshKey]);

  // Every deliberate move by the reader -- opening a record, starting an edit,
  // starting a new greeting -- invalidates a refresh already in flight.
  useEffect(() => { intent.current++; }, [gid, mode]);

  useEffect(() => { onEdgeDraft?.(!!gid && !sameEdges(edges, loadedEdges)); },
            [gid, edges, loadedEdges, onEdgeDraft]);

  function resetForm() {
    setGid(null);
    setRev(null);
    setStale(null);
    setForm(BLANK);
    setEdges(NO_EDGES);
    setLoadedEdges(NO_EDGES);
    setPredecessors([]);
    setMode("edit"); // a brand-new greeting goes straight to the form
  }

  // URL selection and Cancel share one read sequence: a late reload cannot
  // overwrite the record the reader moved to, or banner it with an old error.
  const selectionRead = useRef(0);
  function claimSelectionRead(still: () => boolean = () => true) {
    const request = ++selectionRead.current;
    return () => request === selectionRead.current && still();
  }

  async function readSelection(id: string, current: () => boolean) {
    try {
      const g = await api.readGreeting(scope, id);
      if (!current()) return;   // the reader moved on while this was in flight
      setGid(id);
      setRev(g.rev);
      setStale(null);
      setForm(greetingDraft(g));
      setEdges(g.edges);
      setLoadedEdges(g.edges);
      setPredecessors(g.predecessors ?? []);
      setMode("view"); // existing greetings are read-only until Edit
      setPicking(null);
      setSubjects({});
      if (worldScope) api.getGreetingSubjects(wid, id)
        .then((items) => { if (current()) setSubjects(items); })
        .catch(() => { if (current()) setSubjects({}); });
    } catch (err: unknown) {
      if (current()) throw err;
    }
  }

  async function select(id: string, still: () => boolean = () => true) {
    const current = claimSelectionRead(still);
    setError(null);
    setStale(null);
    await readSelection(id, current);
  }

  /** `base` is the rev this save claims to be replacing -- normally the one
   *  loaded, and on an explicit overwrite the one the 409 reported. */
  async function save(base: string | null = rev) {
    if (!form.name.trim() || (form.character && !form.version)) return;
    if (hold) { setError(hold); return; }
    const request = beginSave();
    if (!request) return;
    setError(null);
    setStale(null);
    beginWrite();
    try {
      let id = gid;
      if (id) {
        await api.updateGreeting(scope, id, {
          name: form.name, body: form.body, present: form.present,
          requires_tags: form.requires_tags, predecessor_join: form.predecessor_join,
          pcless: form.pcless, location: form.location, phase: form.phase,
          sequence: form.sequence, optional: form.optional,
          // re-point (#17): the id and its plot-map edges survive, which is
          // what delete-and-recreate loses. Unchanged values are a no-op.
          character: form.character, version: form.version,
          ...(base ? { rev: base } : {}),
        });
      } else {
        id = (await api.createGreeting(scope, { ...form })).id;
      }
      // Edges live in plotmap.json, not the greeting file, so they are outside
      // what `rev` describes -- and they are only written once the body has
      // landed, so a refused save leaves the plot map untouched too.
      //
      // Only when this form actually changed them. The write replaces the
      // greeting's whole pair of arrays, so resending a list nobody touched is
      // how a save of the BODY silently reverts an edge the plot map drew
      // after this record was loaded (#9). A new greeting still writes: there
      // is nothing behind it to revert.
      if (!gid || !sameEdges(edges, loadedEdges)) {
        await api.setEdges(scope, id, { leads_to: edges.leads_to, excludes: edges.excludes });
      }
      await reload();
      if (currentScope.current === scopeKey) onChanged?.();
      if (!request.current()) return;
      if (gid) {
        await select(id, request.current);
      } else {
        // A create leaves the address on the section root, so `+ New
        // greeting` (a `Link` to `sectionPath`) would point at the screen
        // already showing and do nothing. Navigating to the new record's own
        // address instead keeps the two in agreement -- the URL-driven
        // `selected` effect is what actually opens it read-only, the same
        // way a rail-row click does.
        void navigate(recordHref(id));
      }
    } catch (err: unknown) {
      if (!request.current()) return;
      if (err instanceof ApiError && err.kind === "stale_record") {
        setStale({ rev: (err.body?.rev as string | null) ?? null });
        return;
      }
      setError(errorText(err));
    } finally {
      endWrite();
      request.finish();
    }
  }

  async function discardAndReload() {
    if (!gid) return;
    // Reserve before the list refresh: starting a new selection only after
    // that await would let an old reload supersede the reader's newer move.
    const current = claimSelectionRead();
    retireSave();
    setError(null);
    try {
      await reload(current);
    } catch (err: unknown) {
      if (current()) setError(errorText(err));
      return; // even a list 404 says nothing about the selected record
    }
    if (!current()) return;
    try {
      await readSelection(gid, current);
    } catch (err: unknown) {
      if (!current()) return;
      if (err instanceof ApiError && err.status === 404) resetForm();
      else setError(errorText(err)); // keep the draft and stale retry choice
    }
  }

  async function importFromCharacter() {
    if (!form.character || !form.version) return;
    if (hold) { setError(hold); return; }
    const request = selectionRead.current;
    beginWrite();
    try {
      await api.importGreetings(wid, { character: form.character, version: form.version });
      await reload();
      if (currentScope.current === scopeKey) onChanged?.();
    } catch (err: unknown) {
      if (request === selectionRead.current) setError(errorText(err));
    } finally {
      endWrite();
    }
  }

  async function remove(g: Greeting) {
    // Held for the same reason Save is, and more sharply: `delete_greeting`
    // sweeps the greeting out of every OTHER greeting's edges, so it and a map
    // write in flight are two unlocked read-modify-writes of one plotmap.json.
    if (hold) { setError(hold); return; }
    if (!window.confirm(`Delete greeting '${g.name}'?`)) return;
    beginWrite();
    try {
      await api.deleteGreeting(scope, g.id);
      if (gid === g.id) resetForm();
      await reload();
      onChanged?.();   // a delete takes the greeting's edges with it
    } finally {
      endWrite();
    }
  }

  const mark: GreetingMark = greetings.find((g) => g.id === gid)?.mark ?? null;

  async function setMark(status: "completed" | "skipped" | "none") {
    if (!gid) return;
    try {
      await api.markGreeting(scope.id, gid, status);
      await reload();
    } catch (err: unknown) {
      setError(errorText(err));
    }
  }

  // Keep reader name lookups stable across draft keystrokes. The rail and
  // field components own the corresponding memoized lists: a world can hold
  // many greetings and characters, so rebuilding every chip for each letter
  // typed would make editing pay for the whole browse surface.
  const charById = useMemo(() => new Map(chars.map((c) => [c.id, c])), [chars]);
  const charName = useCallback((id: string) => charById.get(id)?.name ?? id, [charById]);
  // Falls back to the raw id, as every other reference chip here does: a
  // location the campaign has deleted (or a list that failed to load) still
  // shows *something* the reader can recognise and clear.
  const locName = (id: string) => locations.find((l) => l.id === id)?.name ?? id;
  const locationKnown = locations.some((l) => l.id === form.location);

  const greetName = (id: string) => greetings.find((g) => g.id === id)?.name ?? id;
  // the version a present character is cast at: source at the greeting's version, others at their default
  const presentVid = (id: string) => (id === form.character ? form.version : (chars.find((c) => c.id === id)?.default_version ?? ""));
  // "Name:Version", because the chip opens the character AT that version and a
  // version name alone ("main", "alt") says nothing about whose it is. The bare
  // name when the version cannot be resolved -- a stored version the list no
  // longer offers, or a character the list does not know -- rather than a
  // colon with nothing after it.
  const presentLabel = (id: string) => {
    const version = chars.find((c) => c.id === id)?.versions.find((v) => v.id === presentVid(id))?.name;
    return version ? `${charName(id)}:${version}` : charName(id);
  };
  const imageName = (src: string) => {
    if (!src.startsWith("/api/worlds/")) return src.replace(/^https?:/i, (scheme) => scheme.toLowerCase());
    const path = src.split(/[?#]/)[0].split("/").map((segment) => {
      try { return encodeSegment(decodeURIComponent(segment)); }
      catch { return encodeSegment(segment); }
    }).join("/");
    const prefix = `/api/worlds/${encodeSegment(wid)}/greetings/${encodeSegment(gid ?? "")}/images/`;
    return path.startsWith(prefix) ? decodeURIComponent(path.slice(prefix.length)) : path;
  };

  async function saveSubjects(name: string, cids: string[]) {
    if (!gid) return;
    const request = selectionRead.current;
    const current = () => request === selectionRead.current;
    try {
      await api.setImageSubjects(wid, gid, name, cids);
      if (!current()) return;
      const subjects = await api.getGreetingSubjects(wid, gid);
      if (!current()) return;
      setSubjects(subjects);
      setPicking(null);
      const untagged = await api.listUntaggedImages(wid, true);
      if (current()) setUntagged(untagged);
    } catch (err: unknown) {
      if (current()) setError(errorText(err));
    }
  }

  /** `href`, when given, is the only case that means "another record in THIS
   *  section" -- Unlocks/Excludes chip to a sibling greeting, addressable by
   *  `recordHref` alone. `onItem` stays for a chip that opens a record in a
   *  DIFFERENT section (the location chip, via the parent's `onOpenLocation`),
   *  which this editor cannot turn into a `Link` because it does not know that
   *  section's address -- the callback is what asks the parent to navigate. */
  function sideList(label: string, items: string[], render: (id: string) => string,
                    opts?: { onItem?: ((id: string) => void) | undefined; href?: (id: string) => string }) {
    if (items.length === 0) return null;
    return (
      <div className="side-section">
        <h4>{label}</h4>
        <div className="chips">{items.map((id) => opts?.href
          ? <Link key={id} to={opts.href(id)} className="chip on">{render(id)}</Link>
          : opts?.onItem
            ? <button key={id} className="chip on" onClick={() => opts.onItem?.(id)}>{render(id)}</button>
            : <span key={id} className="chip on">{render(id)}</span>)}</div>
      </div>
    );
  }

  return (
    <div className="editor">
      {/* Filters describe this scope's list: a campaign → world → campaign
          trip must not silently carry hidden marks or search into the next
          scope (Codex review). The rail owns that browse-only reset. */}
      <GreetingRail scopeKey={JSON.stringify([wid, scope.kind, scope.id])} scope={scope}
                    greetings={greetings} chars={chars} gid={gid} sectionPath={sectionPath}
                    recordHref={recordHref} untaggedCount={untagged.length}
                    listsReady={listsReady} onTagImages={() => setQueueOpen(true)} />

      <div className="editor-body">
        {error && <div className="banner">{error}</div>}
        {stale && (
          <StaleRecordBanner label="greeting" rev={stale.rev} onReload={() => { void discardAndReload(); }}
                             onOverwrite={() => { void save(stale.rev); }} />
        )}
        {worldScope && queueOpen ? (
          <TaggingQueue wid={wid} chars={chars} greetings={greetings} queue={untagged}
                        onClose={closeQueue} onSaved={queueSaved} />
        ) : !selected && !isNew ? <div className="editor-empty">Choose a greeting to read, or create one.</div>
          : mode === "view" && gid ? (
          <div className="detail-view">
            <div className="detail-main">
              <h3>{form.name}</h3>
              <GreetingMarkdown imageExtras={!worldScope ? undefined : (src) => {
                // Byte ownership never identifies who is in the picture. The
                // greeting's sidecar also holds references to other records'
                // art, and collection controls pass the selected member here.
                if (!src.startsWith("/api/worlds/") && !/^https?:\/\//i.test(src)) return null;
                const name = imageName(src);
                return (
                  <>
                    {(subjects[name] ?? []).map((cid) => (
                      <button key={cid} className="chip on"
                              onClick={() => onOpenCharacter?.(cid, presentVid(cid))}>{charName(cid)}</button>
                    ))}
                    {Object.prototype.hasOwnProperty.call(subjects, name) && subjects[name].length === 0 && (
                      <span className="chip on">None</span>
                    )}
                    <button className="chip" onClick={() => setPicking(name)}>＋ subjects</button>
                    {picking === name && (
                      <SubjectsPopover chars={chars} present={form.present} value={subjects[name] ?? []}
                                       onSave={(cids) => { void saveSubjects(name, cids); }}
                                       onClose={() => setPicking(null)} />
                    )}
                  </>
                );
              }}>{form.body}</GreetingMarkdown>
            </div>
            <aside className="detail-sidebar">
              <div className="form-actions">
                <button className="subtle" onClick={() => setMode("edit")}>Edit</button>
              </div>
              {gid && (worldScope ? (
                <DemotePanel key={`${scope.id}:greetings:${gid}`}
                             wid={scope.id} kind="greetings" id={gid}
                             // resetForm, not setGid(null): clearing the id alone
                             // leaves `mode` on "view" and `form` full of the
                             // deleted greeting, so the editor falls through to
                             // the NEW-greeting form pre-filled with it — and
                             // Save then recreates the world greeting the demote
                             // just removed (Codex review).
                             onDemoted={() => { void reload(); resetForm(); }} />
              ) : (
                <LibraryPanel key={`${scope.id}:greetings:${gid}`}
                              cid={scope.id} kind="greetings" id={gid}
                              onMoved={() => { void reload(); }} />
              ))}
              {form.pcless && (
                <div className="side-section">
                  <h4>Offscreen</h4>
                  <span className="chip on">NPC-only opener</span>
                </div>
              )}
              {!worldScope && (
                <div className="side-section">
                  <h4>Status</h4>
                  <div className="field-hint">
                    {mark === "played" ? "Started this greeting in a scene. Clearing only works if no scene still records it."
                      : mark === "completed" ? "Marked complete: successors are unlocked."
                      : mark === "skipped" ? "Won't do: hidden from new scenes; the plot routes around it."
                      : "Unmarked."}
                  </div>
                  <div className="chips">
                    <button className={"chip" + (mark === "completed" ? " on" : "")} disabled={mark === "played"}
                            onClick={() => { void setMark("completed"); }}>Mark complete</button>
                    <button className={"chip" + (mark === "skipped" ? " on" : "")} disabled={mark === "played"}
                            onClick={() => { void setMark("skipped"); }}>Won't do</button>
                    <button className="chip" disabled={!mark}
                            onClick={() => { void setMark("none"); }}>Clear</button>
                  </div>
                </div>
              )}
              {/* Clickable only once the list confirms the record is there. A
                  chip for a deleted location would navigate to a record that
                  404s -- a section switch with no explanation -- while the
                  edit-mode picker calls the very same id "(missing)". Same
                  three states as that picker: silent until the read lands,
                  labelled only once it has. */}
              {sideList("Location", form.location ? [form.location] : [],
                        (id) => (locationsRead && !locationKnown ? `${id} (missing)` : locName(id)),
                        { onItem: locationKnown ? onOpenLocation : undefined })}
              {form.present.length > 0 && (
                <div className="side-section">
                  <h4>Present characters</h4>
                  <div className="chips">
                    {form.present.map((id) => (
                      <button key={id} className="chip on"
                              onClick={() => onOpenCharacter?.(id, presentVid(id))}>
                        {presentLabel(id)}
                      </button>
                    ))}
                  </div>
                </div>
              )}
              {predecessors.length > 0 && (
                <div className="side-section">
                  <h4>Depends on</h4>
                  <div className="field-hint">
                    {form.predecessor_join === "all" ? "all must be played" : "any unlocks it"}
                  </div>
                  <div className="chips">{predecessors.map((id) => (
                    <Link key={id} to={recordHref(id)} className="chip on">{greetName(id)}</Link>
                  ))}</div>
                </div>
              )}
              {sideList("Unlocks", edges.leads_to, greetName, { href: recordHref })}
              {sideList("Excludes", edges.excludes, greetName, { href: recordHref })}
              {sideList("Requires tags", form.requires_tags, (t) => tags[t] ?? t)}
            </aside>
          </div>
        ) : (
        <div className="form">
          <h3>{gid ? "Edit greeting" : "New greeting"}</h3>
          <GreetingFields form={form} setForm={setForm} edges={edges} setEdges={setEdges}
                          chars={chars} locations={locations} tags={tags}
                          greetings={greetings} gid={gid} listsReady={listsReady}
                          locationsRead={locationsRead} worldScope={worldScope}
                          onImport={() => { void importFromCharacter(); }} />
          <div className="form-actions">
            {gid && <button className="subtle" title={hold ?? undefined} disabled={!!hold || writing}
                            onClick={() => remove(greetings.find((g) => g.id === gid)!)}>Delete</button>}
            {gid ? <button className="subtle" disabled={writing} onClick={() => {
              // Reload only after the write settles, never the old bytes ahead of it.
              if (writes.current > 0) return;
              retireSave(); void select(gid).catch((err: unknown) => setError(errorText(err)));
            }}>Cancel</button>
              : <Link className="subtle" to={sectionPath}>Cancel</Link>}
            <button className="primary" onClick={() => { void save(); }} title={hold ?? undefined}
                    disabled={!form.name.trim() || (!!form.character && !form.version)
                              || !!hold || writing}>
              {gid ? "Save greeting" : "Create greeting"}
            </button>
          </div>
        </div>
        )}
      </div>
    </div>
  );
}
