import { useCallback, useEffect, useState } from "react";
import { ApiError, api, type Appearance, type CharacterSummary, type Edges, type EntityScope, type Greeting, type GreetingMark } from "../api/client";
import { Field } from "./Field";
import { GreetingMarkdown } from "./GreetingMarkdown";
import { StaleRecordBanner } from "./StaleRecordBanner";
import { SubjectsPopover } from "./SubjectsPopover";
import { TaggingQueue } from "./TaggingQueue";

const BLANK = { name: "", character: "", version: "", body: "", present: [] as string[], requires_tags: [] as string[], predecessor_join: "all" as "all" | "any", pcless: false };
const NO_EDGES: Edges = { leads_to: [], excludes: [] };

export function GreetingEditor({ scope, wid, onOpenCharacter, focus }:
  { scope: EntityScope; wid: string;
    onOpenCharacter?: (cid: string, vid: string) => void; focus?: string | null }) {
  const worldScope = scope.kind === "world";
  const [greetings, setGreetings] = useState<Greeting[]>([]);
  const [chars, setChars] = useState<CharacterSummary[]>([]);
  const [tags, setTags] = useState<Record<string, string>>({});
  const [gid, setGid] = useState<string | null>(null); // null = new
  const [form, setForm] = useState(BLANK);
  const [edges, setEdges] = useState<Edges>(NO_EDGES);
  const [predecessors, setPredecessors] = useState<string[]>([]);
  const [mode, setMode] = useState<"view" | "edit">("edit"); // existing greetings open in view
  const [error, setError] = useState<string | null>(null);
  // The rev of the greeting as loaded, echoed back on save so a write cannot
  // land on top of an edit made outside the app (#35).
  const [rev, setRev] = useState<string | null>(null);
  const [stale, setStale] = useState<{ rev: string | null } | null>(null);
  const [subjects, setSubjects] = useState<Record<string, string[]>>({});
  const [picking, setPicking] = useState<string | null>(null); // image name being edited
  const [untagged, setUntagged] = useState<Appearance[]>([]);
  const [queueOpen, setQueueOpen] = useState(false);
  // Rail filters. `query` matches names; `hiddenMarks` holds the marks the
  // reader has switched OFF, so the default (an empty set) hides nothing -- a
  // list that silently starts short is worse than one that starts long.
  const [query, setQuery] = useState("");
  const [hiddenMarks, setHiddenMarks] = useState<Set<Exclude<GreetingMark, null>>>(new Set());

  // Both lists, because the rail's verdict needs both: greetings are what it
  // counts, and character names are half of what search matches -- so before
  // they land, a query on a character reads as "no matches" while it is really
  // "not looked yet". Only the scope effect clears this; the `reload()` calls
  // that follow a save must not put the rail back into a loading state.
  const [listsReady, setListsReady] = useState(false);

  const reload = useCallback(() => api.listGreetings(scope).then(setGreetings), [scope.kind, scope.id]);
  useEffect(() => {
    setListsReady(false);
    Promise.all([reload(), api.listCharacters(scope).then(setChars)])
      .catch(() => {})                   // a failed list is still a settled one
      .finally(() => setListsReady(true));
    api.listTags(wid).then(setTags);  // tag vocabulary stays a world concern
    if (worldScope) api.listUntaggedImages(wid).then(setUntagged).catch(() => setUntagged([]));
    // The rail filters describe THIS scope's list and must not survive into the
    // next one: this component is reused across a scope change, so a search and
    // a hidden mark set in one campaign would silently omit rows from another
    // -- against the "everything shown by default" rule, and invisibly, since
    // a campaign -> world -> campaign trip hides the chips in the middle leg
    // while the exclusion they represent is still in force (Codex review).
    setQuery("");
    setHiddenMarks(new Set());
  }, [wid, worldScope, reload]);  // eslint-disable-line react-hooks/exhaustive-deps -- scope is captured by reload

  function closeQueue() {
    setQueueOpen(false);
    api.listUntaggedImages(wid).then(setUntagged).catch(() => setUntagged([]));
  }

  function queueSaved(savedGid: string) {
    if (savedGid === gid) api.getGreetingSubjects(wid, savedGid).then(setSubjects).catch(() => {});
  }

  // arrived via a character page's world-greeting link: open that greeting
  useEffect(() => {
    if (focus) select(focus);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focus, wid]);

  function resetForm() {
    setGid(null);
    setRev(null);
    setStale(null);
    setForm(BLANK);
    setEdges(NO_EDGES);
    setPredecessors([]);
    setMode("edit"); // a brand-new greeting goes straight to the form
  }

  async function select(id: string) {
    setError(null);
    setStale(null);
    const g = await api.readGreeting(scope, id);
    setGid(id);
    setRev(g.rev);
    setForm({
      name: g.meta.name, character: g.meta.character, version: g.meta.version,
      body: g.body.trim(), present: g.meta.present ?? [], requires_tags: g.meta.requires_tags,
      predecessor_join: g.meta.predecessor_join, pcless: g.meta.pcless ?? false,
    });
    setEdges(g.edges);
    setPredecessors(g.predecessors ?? []);
    setMode("view"); // existing greetings are read-only until Edit
    setPicking(null);
    if (worldScope) api.getGreetingSubjects(wid, id).then(setSubjects).catch(() => setSubjects({}));
  }

  const versions = chars.find((c) => c.id === form.character)?.versions ?? [];

  /** `base` is the rev this save claims to be replacing -- normally the one
   *  loaded, and on an explicit overwrite the one the 409 reported. */
  async function save(base: string | null = rev) {
    if (!form.name.trim() || (form.character && !form.version)) return;
    setError(null);
    setStale(null);
    try {
      let id = gid;
      if (id) {
        await api.updateGreeting(scope, id, {
          name: form.name, body: form.body, present: form.present,
          requires_tags: form.requires_tags, predecessor_join: form.predecessor_join,
          pcless: form.pcless, ...(base ? { rev: base } : {}),
        });
      } else {
        id = (await api.createGreeting(scope, { ...form })).id;
      }
      // Edges live in plotmap.json, not the greeting file, so they are outside
      // what `rev` describes -- and they are only written once the body has
      // landed, so a refused save leaves the plot map untouched too.
      await api.setEdges(scope, id, { leads_to: edges.leads_to, excludes: edges.excludes });
      await reload();
      await select(id);
    } catch (err: any) {
      if (err instanceof ApiError && err.kind === "stale_record") {
        setStale({ rev: (err.body?.rev as string | null) ?? null });
        return;
      }
      setError(err.detail ?? String(err));
    }
  }

  async function discardAndReload() {
    setStale(null);
    if (!gid) return;
    await reload();
    try {
      await select(gid);
    } catch {
      resetForm(); // the greeting is gone from disk entirely
    }
  }

  async function importFromCharacter() {
    if (!form.character || !form.version) return;
    await api.importGreetings(wid, { character: form.character, version: form.version });
    await reload();
  }

  async function remove(g: Greeting) {
    if (!window.confirm(`Delete greeting '${g.name}'?`)) return;
    await api.deleteGreeting(scope, g.id);
    if (gid === g.id) resetForm();
    await reload();
  }

  function toggle(list: "leads_to" | "excludes", id: string) {
    const cur = edges[list];
    setEdges({ ...edges, [list]: cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id] });
  }

  function toggleTag(tid: string) {
    const cur = form.requires_tags;
    setForm({ ...form, requires_tags: cur.includes(tid) ? cur.filter((t) => t !== tid) : [...cur, tid] });
  }

  function togglePresent(cid: string) {
    const cur = form.present;
    setForm({ ...form, present: cur.includes(cid) ? cur.filter((c) => c !== cid) : [...cur, cid] });
  }

  const mark: GreetingMark = greetings.find((g) => g.id === gid)?.mark ?? null;

  async function setMark(status: "completed" | "skipped" | "none") {
    if (!gid) return;
    try {
      await api.markGreeting(scope.id, gid, status);
      await reload();
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  const others = greetings.filter((g) => g.id !== gid);
  const charName = (id: string) => chars.find((c) => c.id === id)?.name ?? id;

  // --- rail filtering -------------------------------------------------------
  // Marks are campaign-only (a world has no play history), so the chips are
  // too; search works in both scopes.
  const MARKS: Exclude<GreetingMark, null>[] = ["played", "completed", "skipped"];
  const MARK_LABEL: Record<string, string> = { played: "played", completed: "done", skipped: "skip" };
  const markCounts = MARKS.map((m) => [m, greetings.filter((g) => g.mark === m).length] as const);

  function toggleMark(m: Exclude<GreetingMark, null>) {
    setHiddenMarks((prev) => {
      const next = new Set(prev);
      if (next.has(m)) next.delete(m); else next.add(m);
      return next;
    });
  }

  // NFC on both sides before folding case. Names arrive from hand-written
  // markdown and imported cards, so an accented one can be stored decomposed
  // (e + combining acute) while the reader types it composed; the two render
  // identically and would otherwise never match (Codex review). This normalizes
  // form, not accents: "cafe" still does not find "café", which keeps the match
  // rule something a reader can predict.
  const fold = (s: string) => s.normalize("NFC").toLowerCase();

  // Name, source character, and every present character -- all of which the
  // list payload already carries, so this costs no request. Bodies are NOT
  // searched: they only exist on the per-greeting read.
  function matchesQuery(g: Greeting, needle: string): boolean {
    if (!needle) return true;
    const hay = [g.name, charName(g.character), ...(g.present ?? []).map(charName)];
    return hay.some((s) => fold(s).includes(needle));
  }

  const needle = fold(query.trim());
  const shownGreetings = greetings.filter((g) => {
    // The open greeting always stays listed. Its content is on screen either
    // way, and dropping its row would leave the body with no visible source --
    // the reader would see a record the list denies having.
    if (g.id === gid) return true;
    if (!worldScope && g.mark && hiddenMarks.has(g.mark)) return false;
    return matchesQuery(g, needle);
  });
  const hiddenCount = greetings.length - shownGreetings.length;
  const greetName = (id: string) => greetings.find((g) => g.id === id)?.name ?? id;
  // the version a present character is cast at: source at the greeting's version, others at their default
  const presentVid = (id: string) => (id === form.character ? form.version : (chars.find((c) => c.id === id)?.default_version ?? ""));
  const presentLabel = (id: string) =>
    chars.find((c) => c.id === id)?.versions.find((v) => v.id === presentVid(id))?.name ?? charName(id);
  const imageName = (src: string) => src.split("/").pop() ?? "";

  async function saveSubjects(name: string, cids: string[]) {
    await api.setImageSubjects(wid, gid!, name, cids);
    setSubjects(await api.getGreetingSubjects(wid, gid!));
    setPicking(null);
  }

  function sideList(label: string, items: string[], render: (id: string) => string,
                    onItem?: (id: string) => void) {
    if (items.length === 0) return null;
    return (
      <div className="side-section">
        <h4>{label}</h4>
        <div className="chips">{items.map((id) => onItem
          ? <button key={id} className="chip on" onClick={() => onItem(id)}>{render(id)}</button>
          : <span key={id} className="chip on">{render(id)}</span>)}</div>
      </div>
    );
  }

  return (
    <div className="editor">
      <div className="editor-list">
        <button className="primary new" onClick={resetForm}>+ New greeting</button>
        {worldScope && untagged.length > 0 && (
          <button className="subtle new" onClick={() => setQueueOpen(true)}>
            ▶ Tag images ({untagged.length})
          </button>
        )}
        <input className="rail-search" type="search" value={query} aria-label="Search greetings"
               placeholder="Search name or character…"
               onChange={(e) => setQuery(e.target.value)} />
        {!worldScope && markCounts.some(([, n]) => n > 0) && (
          <div className="rail-filters" role="group" aria-label="Filter by mark">
            {markCounts.filter(([, n]) => n > 0).map(([m, n]) => (
              <button key={m} className={"chip" + (hiddenMarks.has(m) ? "" : " on")}
                      aria-pressed={!hiddenMarks.has(m)}
                      title={hiddenMarks.has(m) ? `Show ${MARK_LABEL[m]}` : `Hide ${MARK_LABEL[m]}`}
                      onClick={() => toggleMark(m)}>
                {MARK_LABEL[m]} {n}
              </button>
            ))}
          </div>
        )}
        {shownGreetings.map((g) => (
          <button
            key={g.id}
            className={"row" + (gid === g.id ? " active" : "")}
            onClick={() => select(g.id)}
          >
            {g.name}
            {!worldScope && g.mark && (
              <span className={`mark-badge ${g.mark}`}>
                {MARK_LABEL[g.mark]}
              </span>
            )}
          </button>
        ))}
        {/* One status line, always in the DOM rather than mounted on demand:
            filtering happens while focus is still in the search box, so a
            result count that only appears afterwards is never announced. Live
            regions have to exist before the text changes to be read out. */}
        {/* One status line, always in the DOM rather than mounted on demand:
            filtering happens while focus is still in the search box, so a
            result count that only appears afterwards is never announced. Live
            regions have to exist before the text changes to be read out. */}
        <div className="field-hint rail-empty" role="status" aria-live="polite">
          {!listsReady
            ? ""                        /* silence, not a verdict, while loading */
            : shownGreetings.length === 0
              ? "No greetings match."
              : hiddenCount > 0 ? `${hiddenCount} hidden` : ""}
        </div>
      </div>

      <div className="editor-body">
        {error && <div className="banner">{error}</div>}
        {stale && (
          <StaleRecordBanner label="greeting" rev={stale.rev} onReload={discardAndReload}
                             onOverwrite={() => save(stale.rev)} />
        )}
        {worldScope && queueOpen ? (
          <TaggingQueue wid={wid} chars={chars} greetings={greetings} queue={untagged}
                        onClose={closeQueue} onSaved={queueSaved} />
        ) : mode === "view" && gid ? (
          <div className="detail-view">
            <div className="detail-main">
              <h3>{form.name}</h3>
              <GreetingMarkdown imageExtras={!worldScope ? undefined : (src) => {
                const name = imageName(src);
                return (
                  <>
                    {(subjects[name] ?? []).map((cid) => (
                      <button key={cid} className="chip on"
                              onClick={() => onOpenCharacter?.(cid, presentVid(cid))}>{charName(cid)}</button>
                    ))}
                    <button className="chip" onClick={() => setPicking(name)}>＋ subjects</button>
                    {picking === name && (
                      <SubjectsPopover chars={chars} present={form.present} value={subjects[name] ?? []}
                                       onSave={(cids) => saveSubjects(name, cids)}
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
                            onClick={() => setMark("completed")}>Mark complete</button>
                    <button className={"chip" + (mark === "skipped" ? " on" : "")} disabled={mark === "played"}
                            onClick={() => setMark("skipped")}>Won't do</button>
                    <button className="chip" disabled={!mark}
                            onClick={() => setMark("none")}>Clear</button>
                  </div>
                </div>
              )}
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
                    <button key={id} className="chip on" onClick={() => select(id)}>{greetName(id)}</button>
                  ))}</div>
                </div>
              )}
              {sideList("Unlocks", edges.leads_to, greetName, select)}
              {sideList("Excludes", edges.excludes, greetName, select)}
              {sideList("Requires tags", form.requires_tags, (t) => tags[t] ?? t)}
            </aside>
          </div>
        ) : (
        <div className="form">
          <h3>{gid ? "Edit greeting" : "New greeting"}</h3>
          <Field label="Name">
            <input type="text" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </Field>
          <Field label="Character" hint={gid ? "character and version are fixed after creation" : undefined}>
            <select value={form.character} aria-label="Character" disabled={!!gid}
                    onChange={(e) => setForm({ ...form, character: e.target.value, version: "" })}>
              <option value="">— no character (narrator-only) —</option>
              {chars.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </Field>
          {form.character && (
            <Field label="Version">
              <select value={form.version} aria-label="Version" disabled={!!gid}
                      onChange={(e) => setForm({ ...form, version: e.target.value })}>
                <option value="">— pick a version —</option>
                {versions.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
              </select>
            </Field>
          )}
          {worldScope && form.character && (
            <div className="form-actions">
              <button className="subtle" onClick={importFromCharacter} disabled={!form.character || !form.version}>
                Import greetings from this character/version
              </button>
            </div>
          )}
          <Field label="Greeting text">
            <textarea value={form.body} rows={6} onChange={(e) => setForm({ ...form, body: e.target.value })} />
          </Field>
          <Field label="Offscreen"
                 hint="an NPC-only opener — no player character; {{user}} becomes your PC's name">
            <div className="chips">
              <button className={"chip" + (form.pcless ? " on" : "")}
                      onClick={() => setForm({ ...form, pcless: !form.pcless })}>
                Offscreen (no PC)
              </button>
            </div>
          </Field>
          {form.character && (
            <Field label="Present characters" hint="everyone cast into the scene when it starts from this greeting">
              <div className="chips">
                {chars.map((c) => (
                  <button key={c.id} className={"chip" + (form.present.includes(c.id) ? " on" : "")}
                          onClick={() => togglePresent(c.id)}>{c.name}</button>
                ))}
                {chars.length === 0 && <span className="field-hint">No characters in this world yet.</span>}
              </div>
            </Field>
          )}
          <Field label="Required tags" hint="the greeting unlocks only if a player PC carries these">
            <div className="chips">
              {Object.keys(tags).sort().map((tid) => (
                <button key={tid} className={"chip" + (form.requires_tags.includes(tid) ? " on" : "")}
                        onClick={() => toggleTag(tid)}>{tags[tid]}</button>
              ))}
              {Object.keys(tags).length === 0 && <span className="field-hint">No tags in this world yet.</span>}
            </div>
          </Field>
          <Field label="Predecessor join">
            <select value={form.predecessor_join} aria-label="Predecessor join"
                    onChange={(e) => setForm({ ...form, predecessor_join: e.target.value as "all" | "any" })}>
              <option value="all">all predecessors must be played</option>
              <option value="any">any predecessor unlocks it</option>
            </select>
          </Field>
          <Field label="Leads to" hint="greetings this one unlocks once played">
            <div className="chips">
              {others.map((g) => (
                <button key={g.id} className={"chip" + (edges.leads_to.includes(g.id) ? " on" : "")}
                        onClick={() => toggle("leads_to", g.id)}>{g.name}</button>
              ))}
              {others.length === 0 && <span className="field-hint">No other greetings yet.</span>}
            </div>
          </Field>
          <Field label="Excludes" hint="playing this one locks these (mutually exclusive)">
            <div className="chips">
              {others.map((g) => (
                <button key={g.id} className={"chip" + (edges.excludes.includes(g.id) ? " on" : "")}
                        onClick={() => toggle("excludes", g.id)}>{g.name}</button>
              ))}
            </div>
          </Field>
          <div className="form-actions">
            {gid && <button className="subtle" onClick={() => remove(greetings.find((g) => g.id === gid)!)}>Delete</button>}
            {gid && <button className="subtle" onClick={() => setMode("view")}>Cancel</button>}
            <button className="primary" onClick={() => save()}
                    disabled={!form.name.trim() || (!!form.character && !form.version)}>
              {gid ? "Save greeting" : "Create greeting"}
            </button>
          </div>
        </div>
        )}
      </div>
    </div>
  );
}
