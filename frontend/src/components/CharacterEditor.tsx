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

type Mode = "grid" | "detail" | "edit";

export function CharacterEditor({ wid, resetSignal }: { wid: string; resetSignal?: number }) {
  const [chars, setChars] = useState<CharacterSummary[]>([]);
  const [detail, setDetail] = useState<CharacterDetail | null>(null);
  const [vid, setVid] = useState("");
  const [card, setCard] = useState<Card | null>(null);
  const [greetings, setGreetings] = useState<string[]>([]);
  const [mode, setMode] = useState<Mode>("grid");
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const avatarRef = useRef<HTMLInputElement>(null);
  const [avatarBust, setAvatarBust] = useState(0);
  const [bookMsg, setBookMsg] = useState<string | null>(null);

  const reload = useCallback(() => api.listCharacters(wid).then(setChars), [wid]);
  useEffect(() => {
    reload();
  }, [reload]);

  // re-clicking the Characters tab (resetSignal bumps) returns to the grid
  useEffect(() => {
    setMode("grid");
    setDetail(null);
    setCard(null);
  }, [resetSignal]);

  const hasAvatar = (detail && card)
    ? (detail.versions.find((v) => v.id === vid)?.images ?? []).includes("avatar")
    : false;

  function loadVersion(d: CharacterDetail, id: string) {
    const v = d.versions.find((x) => x.id === id) ?? d.versions[0];
    setVid(v.id);
    setCard(v.card);
    setGreetings(v.card.data.alternate_greetings ?? []);
    setBookMsg(null);
  }

  const bookCount = card?.data.character_book?.entries?.length ?? 0;

  async function select(cid: string) {
    setError(null);
    const d = await api.readCharacter(wid, cid);
    setDetail(d);
    loadVersion(d, d.meta.default_version);
  }

  async function openDetail(cid: string) {
    await select(cid);
    setMode("detail");
  }

  async function openEdit(cid: string) {
    await select(cid);
    setMode("edit");
  }

  function backToGrid() {
    setDetail(null);
    setCard(null);
    setMode("grid");
    reload();
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
    await openEdit(character);
  }

  async function save() {
    if (!detail || !card) return;
    setError(null);
    try {
      await api.updateVersion(wid, detail.meta.id, vid, buildCard());
      await select(detail.meta.id);
      await reload();
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

  async function deleteCharacter(cid: string, name: string) {
    if (!window.confirm(`Delete character '${name}'?`)) return;
    await api.deleteCharacter(wid, cid);
    backToGrid();
  }

  async function importBook() {
    if (!detail) return;
    setBookMsg(null);
    try {
      const { created } = await api.importCharacterBook(wid, detail.meta.id, vid);
      setBookMsg(`Imported ${created.length} entr${created.length === 1 ? "y" : "ies"} to world lore`);
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  async function onAvatar(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file || !detail) return;
    setError(null);
    try {
      await api.putImage(wid, detail.meta.id, vid, "avatar", file);
      await select(detail.meta.id);
      await reload();
      setAvatarBust((n) => n + 1);
    } catch (err: any) {
      setError(err.detail ?? String(err));
    } finally {
      e.target.value = "";
    }
  }

  async function removeAvatar() {
    if (!detail) return;
    await api.deleteImage(wid, detail.meta.id, vid, "avatar");
    await select(detail.meta.id);
    await reload();
    setAvatarBust((n) => n + 1);
  }

  async function onImport(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setError(null);
    const ext = file.name.split(".").pop()?.toLowerCase();
    const fmt = ext === "png" ? "png" : ext === "charx" ? "charx" : "json";
    try {
      const { character } = await api.importCharacter(wid, file, fmt);
      await reload();
      await openDetail(character);
    } catch (err: any) {
      setError(err.detail ?? String(err));
    } finally {
      e.target.value = "";
    }
  }

  const avatarSrc = (cid: string, version: string, bust = false) =>
    api.imageUrl(wid, cid, version, "avatar") + (bust ? `?v=${avatarBust}` : "");

  if (mode === "grid" || !detail || !card) {
    return (
      <div className="character-editor">
        <div className="grid-toolbar">
          <button className="primary" onClick={newCharacter}>+ New character</button>
          <button className="subtle" onClick={() => fileRef.current?.click()}>Import card</button>
          <input ref={fileRef} type="file" accept=".json,.png,.charx" hidden aria-label="Import character card" onChange={onImport} />
        </div>
        {error && <div className="banner">{error}</div>}
        {chars.length === 0 ? (
          <div className="editor-empty">No characters yet. Create one or import a card.</div>
        ) : (
          <div className="char-grid">
            {chars.map((c) => (
              <div key={c.id} className="char-card">
                <button className="char-card-main" onClick={() => openDetail(c.id)}>
                  {c.has_avatar
                    ? <img className="char-card-avatar" alt="" src={avatarSrc(c.id, c.default_version)} />
                    : <div className="char-card-avatar char-card-avatar-empty">no avatar</div>}
                  <span className="char-card-name">{c.name}</span>
                </button>
                <div className="char-card-actions">
                  <button className="subtle" onClick={() => openEdit(c.id)}>Edit</button>
                  <button className="subtle" onClick={() => deleteCharacter(c.id, c.name)}>Delete</button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    );
  }

  if (mode === "detail") {
    const tags = card.data.tags ?? [];
    return (
      <div className="character-editor">
        <div className="editor-body">
          <button className="subtle back" onClick={backToGrid}>‹ All characters</button>
          {error && <div className="banner">{error}</div>}
          <div className="detail">
            <div className="detail-head">
              {hasAvatar
                ? <img className="detail-avatar" alt="" src={avatarSrc(detail.meta.id, vid, true)} />
                : <div className="detail-avatar avatar-empty">no avatar</div>}
              <div className="detail-meta">
                <h3>{card.data.name || detail.meta.name}</h3>
                {card.data.creator ? <div className="field-hint">by {card.data.creator}</div> : null}
                {tags.length > 0 && (
                  <div className="chips">{tags.map((t) => <span className="chip" key={t}>{t}</span>)}</div>
                )}
                {detail.versions.length > 1 && (
                  <select value={vid} onChange={(e) => loadVersion(detail, e.target.value)} aria-label="Version">
                    {detail.versions.map((v) => (
                      <option key={v.id} value={v.id}>
                        {v.name}{v.id === detail.meta.default_version ? " (default)" : ""}
                      </option>
                    ))}
                  </select>
                )}
              </div>
              <div className="detail-actions">
                <button className="primary" onClick={() => setMode("edit")}>Edit</button>
                <button className="subtle" onClick={() => deleteCharacter(detail.meta.id, detail.meta.name)}>Delete</button>
              </div>
            </div>

            {TEXT_FIELDS.map((f) => {
              const val = (card.data[f.key] as string) ?? "";
              return val.trim() ? (
                <div className="detail-field" key={f.key}>
                  <div className="role">{f.label}</div>
                  <div className="detail-text">{val}</div>
                </div>
              ) : null;
            })}

            {greetings.length > 0 && (
              <div className="detail-field">
                <div className="role">Alternate greetings</div>
                {greetings.map((g, i) => <div className="detail-text detail-greeting" key={i}>{g}</div>)}
              </div>
            )}
          </div>
        </div>
      </div>
    );
  }

  // mode === "edit"
  return (
    <div className="character-editor">
      <div className="editor-body">
        <button className="subtle back" onClick={backToGrid}>‹ All characters</button>
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
            <button className="subtle" onClick={() => deleteCharacter(detail.meta.id, detail.meta.name)}>Delete</button>
          </div>

          <div className="avatar-block">
            {hasAvatar ? (
              <img className="avatar" alt="avatar" src={avatarSrc(detail.meta.id, vid, true)} />
            ) : (
              <div className="avatar avatar-empty" aria-label="no avatar">no avatar</div>
            )}
            <div className="avatar-actions">
              <button className="subtle" type="button" onClick={() => avatarRef.current?.click()}>
                {hasAvatar ? "Replace" : "Upload"}
              </button>
              {hasAvatar && <button className="subtle" type="button" onClick={removeAvatar}>Remove</button>}
              <input ref={avatarRef} type="file" accept="image/*" hidden
                     aria-label="Upload avatar" onChange={onAvatar} />
            </div>
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

          {bookCount > 0 && (
            <div className="book-import">
              <button className="subtle" type="button" onClick={importBook}>
                Import {bookCount} embedded lore {bookCount === 1 ? "entry" : "entries"} to world
              </button>
              {bookMsg && <span className="field-hint">{bookMsg}</span>}
            </div>
          )}

          <div className="form-actions">
            <button className="primary" onClick={save}>Save version</button>
          </div>
        </div>
      </div>
    </div>
  );
}
