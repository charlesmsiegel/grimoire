import { useCallback, useEffect, useState } from "react";
import { api, type CharacterSummary, type Edges, type Greeting } from "../api/client";
import { Field } from "./Field";

const BLANK = { name: "", character: "", version: "", body: "", requires_tags: [] as string[], predecessor_join: "all" as "all" | "any" };
const NO_EDGES: Edges = { leads_to: [], excludes: [] };

export function GreetingEditor({ wid }: { wid: string }) {
  const [greetings, setGreetings] = useState<Greeting[]>([]);
  const [chars, setChars] = useState<CharacterSummary[]>([]);
  const [tags, setTags] = useState<Record<string, string>>({});
  const [gid, setGid] = useState<string | null>(null); // null = new
  const [form, setForm] = useState(BLANK);
  const [edges, setEdges] = useState<Edges>(NO_EDGES);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(() => api.listGreetings(wid).then(setGreetings), [wid]);
  useEffect(() => {
    reload();
    api.listCharacters(wid).then(setChars);
    api.listTags(wid).then(setTags);
  }, [wid, reload]);

  function resetForm() {
    setGid(null);
    setForm(BLANK);
    setEdges(NO_EDGES);
  }

  async function select(id: string) {
    setError(null);
    const g = await api.readGreeting(wid, id);
    setGid(id);
    setForm({
      name: g.meta.name, character: g.meta.character, version: g.meta.version,
      body: g.body.trim(), requires_tags: g.meta.requires_tags, predecessor_join: g.meta.predecessor_join,
    });
    setEdges(g.edges);
  }

  const versions = chars.find((c) => c.id === form.character)?.versions ?? [];

  async function save() {
    if (!form.name.trim() || !form.character || !form.version) return;
    setError(null);
    try {
      let id = gid;
      if (id) {
        await api.updateGreeting(wid, id, {
          name: form.name, body: form.body,
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

  const others = greetings.filter((g) => g.id !== gid);

  return (
    <div className="editor">
      <div className="editor-list">
        <button className="primary new" onClick={resetForm}>+ New greeting</button>
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
        <div className="form">
          {error && <div className="banner">{error}</div>}
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
            <button className="primary" onClick={save}
                    disabled={!form.name.trim() || !form.character || !form.version}>
              {gid ? "Save greeting" : "Create greeting"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
