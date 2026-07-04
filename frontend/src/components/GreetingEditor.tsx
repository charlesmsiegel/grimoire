import { useCallback, useEffect, useState } from "react";
import { api, type Appearance, type CharacterSummary, type Edges, type Greeting } from "../api/client";
import { Field } from "./Field";
import { GreetingMarkdown } from "./GreetingMarkdown";
import { SubjectsPopover } from "./SubjectsPopover";
import { TaggingQueue } from "./TaggingQueue";

const BLANK = { name: "", character: "", version: "", body: "", present: [] as string[], requires_tags: [] as string[], predecessor_join: "all" as "all" | "any" };
const NO_EDGES: Edges = { leads_to: [], excludes: [] };

export function GreetingEditor({ wid, onOpenCharacter, focus }:
  { wid: string; onOpenCharacter?: (cid: string, vid: string) => void; focus?: string | null }) {
  const [greetings, setGreetings] = useState<Greeting[]>([]);
  const [chars, setChars] = useState<CharacterSummary[]>([]);
  const [tags, setTags] = useState<Record<string, string>>({});
  const [gid, setGid] = useState<string | null>(null); // null = new
  const [form, setForm] = useState(BLANK);
  const [edges, setEdges] = useState<Edges>(NO_EDGES);
  const [predecessors, setPredecessors] = useState<string[]>([]);
  const [mode, setMode] = useState<"view" | "edit">("edit"); // existing greetings open in view
  const [error, setError] = useState<string | null>(null);
  const [subjects, setSubjects] = useState<Record<string, string[]>>({});
  const [picking, setPicking] = useState<string | null>(null); // image name being edited
  const [untagged, setUntagged] = useState<Appearance[]>([]);
  const [queueOpen, setQueueOpen] = useState(false);

  const reload = useCallback(() => api.listGreetings(wid).then(setGreetings), [wid]);
  useEffect(() => {
    reload();
    api.listCharacters(wid).then(setChars);
    api.listTags(wid).then(setTags);
    api.listUntaggedImages(wid).then(setUntagged).catch(() => setUntagged([]));
  }, [wid, reload]);

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
    setForm(BLANK);
    setEdges(NO_EDGES);
    setPredecessors([]);
    setMode("edit"); // a brand-new greeting goes straight to the form
  }

  async function select(id: string) {
    setError(null);
    const g = await api.readGreeting(wid, id);
    setGid(id);
    setForm({
      name: g.meta.name, character: g.meta.character, version: g.meta.version,
      body: g.body.trim(), present: g.meta.present ?? [], requires_tags: g.meta.requires_tags,
      predecessor_join: g.meta.predecessor_join,
    });
    setEdges(g.edges);
    setPredecessors(g.predecessors ?? []);
    setMode("view"); // existing greetings are read-only until Edit
    setPicking(null);
    api.getGreetingSubjects(wid, id).then(setSubjects).catch(() => setSubjects({}));
  }

  const versions = chars.find((c) => c.id === form.character)?.versions ?? [];

  async function save() {
    if (!form.name.trim() || !form.character || !form.version) return;
    setError(null);
    try {
      let id = gid;
      if (id) {
        await api.updateGreeting(wid, id, {
          name: form.name, body: form.body, present: form.present,
          requires_tags: form.requires_tags, predecessor_join: form.predecessor_join,
        });
      } else {
        id = (await api.createGreeting(wid, { ...form })).id;
      }
      await api.setEdges(wid, id, { leads_to: edges.leads_to, excludes: edges.excludes });
      await reload();
      await select(id);
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  async function importFromCharacter() {
    if (!form.character || !form.version) return;
    await api.importGreetings(wid, { character: form.character, version: form.version });
    await reload();
  }

  async function remove(g: Greeting) {
    if (!window.confirm(`Delete greeting '${g.name}'?`)) return;
    await api.deleteGreeting(wid, g.id);
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

  const others = greetings.filter((g) => g.id !== gid);
  const charName = (id: string) => chars.find((c) => c.id === id)?.name ?? id;
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
        {untagged.length > 0 && (
          <button className="subtle new" onClick={() => setQueueOpen(true)}>
            ▶ Tag images ({untagged.length})
          </button>
        )}
        {greetings.map((g) => (
          <button
            key={g.id}
            className={"row" + (gid === g.id ? " active" : "")}
            onClick={() => select(g.id)}
          >
            {g.name}
          </button>
        ))}
      </div>

      <div className="editor-body">
        {error && <div className="banner">{error}</div>}
        {queueOpen ? (
          <TaggingQueue wid={wid} chars={chars} greetings={greetings} queue={untagged}
                        onClose={closeQueue} onSaved={queueSaved} />
        ) : mode === "view" && gid ? (
          <div className="detail-view">
            <div className="detail-main">
              <h3>{form.name}</h3>
              <GreetingMarkdown imageExtras={(src) => {
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
              <option value="">— pick a character —</option>
              {chars.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </Field>
          <Field label="Version">
            <select value={form.version} aria-label="Version" disabled={!!gid}
                    onChange={(e) => setForm({ ...form, version: e.target.value })}>
              <option value="">— pick a version —</option>
              {versions.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
            </select>
          </Field>
          <div className="form-actions">
            <button className="subtle" onClick={importFromCharacter} disabled={!form.character || !form.version}>
              Import greetings from this character/version
            </button>
          </div>
          <Field label="Greeting text">
            <textarea value={form.body} rows={6} onChange={(e) => setForm({ ...form, body: e.target.value })} />
          </Field>
          <Field label="Present characters" hint="everyone cast into the scene when it starts from this greeting">
            <div className="chips">
              {chars.map((c) => (
                <button key={c.id} className={"chip" + (form.present.includes(c.id) ? " on" : "")}
                        onClick={() => togglePresent(c.id)}>{c.name}</button>
              ))}
              {chars.length === 0 && <span className="field-hint">No characters in this world yet.</span>}
            </div>
          </Field>
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
            <button className="primary" onClick={save}
                    disabled={!form.name.trim() || !form.character || !form.version}>
              {gid ? "Save greeting" : "Create greeting"}
            </button>
          </div>
        </div>
        )}
      </div>
    </div>
  );
}
