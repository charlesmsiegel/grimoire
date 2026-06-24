import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Card, type CharacterDetail, type CharacterSummary } from "../api/client";
import { Field } from "./Field";

const TEXT_FIELDS: { key: string; label: string; area?: boolean }[] = [
  { key: "description", label: "Description", area: true },
  { key: "personality", label: "Personality", area: true },
  { key: "scenario", label: "Scenario", area: true },
  { key: "first_mes", label: "First message", area: true },
  { key: "mes_example", label: "Example dialogue", area: true },
  { key: "system_prompt", label: "System prompt", area: true },
  { key: "post_history_instructions", label: "Post-history instructions", area: true },
  { key: "creator_notes", label: "Creator notes", area: true },
];

export function CharacterEditor({ wid }: { wid: string }) {
  const [chars, setChars] = useState<CharacterSummary[]>([]);
  const [detail, setDetail] = useState<CharacterDetail | null>(null);
  const [vid, setVid] = useState("");
  const [card, setCard] = useState<Card | null>(null);
  const [greetings, setGreetings] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const reload = useCallback(() => api.listCharacters(wid).then(setChars), [wid]);
  useEffect(() => {
    reload();
  }, [reload]);

  function loadVersion(d: CharacterDetail, id: string) {
    const v = d.versions.find((x) => x.id === id) ?? d.versions[0];
    setVid(v.id);
    setCard(v.card);
    setGreetings(v.card.data.alternate_greetings ?? []);
  }

  async function select(cid: string) {
    setError(null);
    const d = await api.readCharacter(wid, cid);
    setDetail(d);
    loadVersion(d, d.meta.default_version);
  }

  function setField(key: string, value: unknown) {
    if (!card) return;
    setCard({ ...card, data: { ...card.data, [key]: value } });
  }

  function buildCard(): Card {
    return { ...card!, data: { ...card!.data, alternate_greetings: greetings.filter((g) => g.trim() !== "") } };
  }

  async function newCharacter() {
    const name = window.prompt("New character name?")?.trim();
    if (!name) return;
    const { character } = await api.createCharacter(wid, { name });
    await reload();
    await select(character);
  }

  async function save() {
    if (!detail || !card) return;
    setError(null);
    try {
      await api.updateVersion(wid, detail.meta.id, vid, buildCard());
      await select(detail.meta.id);
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  async function addVersion() {
    if (!detail) return;
    const name = window.prompt("New version name?")?.trim();
    if (!name) return;
    const { version } = await api.createVersion(wid, detail.meta.id, { name, card: buildCard() });
    await select(detail.meta.id);
    loadVersion(await api.readCharacter(wid, detail.meta.id), version);
  }

  async function setDefault() {
    if (!detail) return;
    await api.setDefaultVersion(wid, detail.meta.id, vid);
    await select(detail.meta.id);
  }

  async function deleteCharacter() {
    if (!detail) return;
    if (!window.confirm(`Delete character '${detail.meta.name}'?`)) return;
    await api.deleteCharacter(wid, detail.meta.id);
    setDetail(null);
    await reload();
  }

  async function onImport(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setError(null);
    try {
      const { character } = await api.importCharacter(wid, file, "json");
      await reload();
      await select(character);
    } catch (err: any) {
      setError(err.detail ?? String(err));
    } finally {
      e.target.value = "";
    }
  }

  return (
    <div className="editor">
      <div className="editor-list">
        <button className="primary new" onClick={newCharacter}>+ New character</button>
        <button className="subtle new" onClick={() => fileRef.current?.click()}>Import JSON</button>
        <input ref={fileRef} type="file" accept=".json" hidden aria-label="Import character JSON" onChange={onImport} />
        {chars.map((c) => (
          <button
            key={c.id}
            className={"row" + (detail?.meta.id === c.id ? " active" : "")}
            onClick={() => select(c.id)}
          >
            {c.name}
          </button>
        ))}
      </div>

      <div className="editor-body">
        {!detail || !card ? (
          <div className="editor-empty">Select or create a character.</div>
        ) : (
          <div className="form">
            {error && <div className="banner">{error}</div>}
            <div className="picker">
              <select value={vid} onChange={(e) => loadVersion(detail, e.target.value)} aria-label="Version">
                {detail.versions.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.name}{v.id === detail.meta.default_version ? " (default)" : ""}
                  </option>
                ))}
              </select>
              <button className="subtle" onClick={addVersion}>+ Version</button>
              <button className="subtle" onClick={setDefault}>Set default</button>
              <button className="subtle" onClick={deleteCharacter}>Delete</button>
            </div>

            <Field label="Name">
              <input type="text" value={card.data.name ?? ""} onChange={(e) => setField("name", e.target.value)} />
            </Field>
            <Field label="Creator">
              <input type="text" value={card.data.creator ?? ""} onChange={(e) => setField("creator", e.target.value)} />
            </Field>
            <Field label="Tags" hint="comma-separated">
              <input
                type="text"
                value={(card.data.tags ?? []).join(", ")}
                onChange={(e) => setField("tags", e.target.value.split(",").map((t) => t.trim()).filter(Boolean))}
              />
            </Field>
            {TEXT_FIELDS.map((f) => (
              <Field key={f.key} label={f.label}>
                <textarea
                  value={(card.data[f.key] as string) ?? ""}
                  rows={f.key === "description" ? 6 : 3}
                  onChange={(e) => setField(f.key, e.target.value)}
                />
              </Field>
            ))}
            <Field label="Alternate greetings" hint="each greeting may span multiple lines">
              <div className="greeting-list">
                {greetings.map((g, i) => (
                  <div className="greeting-row" key={i}>
                    <textarea
                      aria-label={`Greeting ${i + 1}`}
                      value={g}
                      rows={3}
                      onChange={(e) => setGreetings(greetings.map((x, j) => (j === i ? e.target.value : x)))}
                    />
                    <button className="subtle" type="button"
                            onClick={() => setGreetings(greetings.filter((_, j) => j !== i))}>
                      Remove
                    </button>
                  </div>
                ))}
                <button className="subtle" type="button" onClick={() => setGreetings([...greetings, ""])}>
                  + Add greeting
                </button>
              </div>
            </Field>

            <div className="form-actions">
              <button className="primary" onClick={save}>Save version</button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
