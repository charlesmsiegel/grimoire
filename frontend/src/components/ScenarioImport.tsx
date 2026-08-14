import { useRef, useState } from "react";
import {
  api, type EntityKind, type LoreEntryDraft, type ScenarioCharacterDraft,
  type ScenarioGreetingDraft, type ScenarioImportResult, type ScenarioProposal,
} from "../api/client";

const FORMATS = ["json", "png", "charx"];
const CATEGORIES: EntityKind[] = ["lore", "locations", "items", "groups", "creatures"];

/** Which rows survive the review. Kept beside the proposal rather than inside
 *  it, because "skip this one" is a decision about the import, not a fact about
 *  the record — unchecking a character and then re-checking it must give back
 *  the row the model proposed, edits and all. */
type Kept = { characters: boolean[]; entries: boolean[]; greetings: boolean[] };

function allKept(p: ScenarioProposal): Kept {
  return {
    characters: p.characters.map(() => true),
    entries: p.entries.map(() => true),
    greetings: p.greetings.map(() => true),
  };
}

function describe(result: ScenarioImportResult): string {
  const made = result.characters.filter((c) => c.created).length;
  const reused = result.characters.length - made;
  const parts = [
    `${made} character${made === 1 ? "" : "s"}`,
    `${result.entries.length} ${result.entries.length === 1 ? "entry" : "entries"}`,
    `${result.greetings.length} greeting${result.greetings.length === 1 ? "" : "s"}`,
  ];
  if (reused) parts.push(`${reused} existing character${reused === 1 ? "" : "s"} reused`);
  if (result.art.localized) parts.push(`${result.art.localized} image${result.art.localized === 1 ? "" : "s"} localized`);
  if (result.art.failed) parts.push(`${result.art.failed} image${result.art.failed === 1 ? "" : "s"} failed`);
  return `Imported ${parts.join(", ")}.`;
}

/** Populate a world from a *scenario* card: one card whose text describes a
 *  whole setting and its cast (#217).
 *
 *  Parse → review → import, the same three beats as the lorebook importer, for
 *  the same reason: parsing writes nothing, so the extraction is a proposal the
 *  user edits rather than an import they have to undo. The one difference is
 *  that parsing here costs an LLM call, which is why the button says so. */
export function ScenarioImport({ wid, onImported }: { wid: string; onImported?: () => void }) {
  const [format, setFormat] = useState("json");
  const [url, setUrl] = useState("");
  const [proposal, setProposal] = useState<ScenarioProposal | null>(null);
  const [kept, setKept] = useState<Kept | null>(null);
  const [art, setArt] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  async function run(load: () => Promise<ScenarioProposal>) {
    setError(null);
    setResult(null);
    setBusy(true);
    try {
      const got = await load();
      setProposal(got);
      setKept(allKept(got));
    } catch (err: any) {
      setProposal(null);
      setKept(null);
      setError(err.detail ?? String(err));
    } finally {
      setBusy(false);
    }
  }

  const parseFile = () => {
    const file = fileRef.current?.files?.[0];
    if (!file) return;
    return run(() => api.scenarioParse(wid, file, format));
  };
  const parseUrl = () => (url.trim() ? run(() => api.scenarioParseUrl(wid, url.trim())) : undefined);

  function patchCharacter(i: number, patch: Partial<ScenarioCharacterDraft>) {
    setProposal((p) => p && { ...p, characters: p.characters.map((c, j) => (j === i ? { ...c, ...patch } : c)) });
  }
  function patchEntry(i: number, patch: Partial<LoreEntryDraft>) {
    setProposal((p) => p && { ...p, entries: p.entries.map((e, j) => (j === i ? { ...e, ...patch } : e)) });
  }
  function patchGreeting(i: number, patch: Partial<ScenarioGreetingDraft>) {
    setProposal((p) => p && { ...p, greetings: p.greetings.map((g, j) => (j === i ? { ...g, ...patch } : g)) });
  }
  function toggle(section: keyof Kept, i: number) {
    setKept((k) => k && { ...k, [section]: k[section].map((on, j) => (j === i ? !on : on)) });
  }

  async function commit() {
    if (!proposal || !kept) return;
    setError(null);
    setBusy(true);
    try {
      const got = await api.scenarioImport(wid, {
        characters: proposal.characters.filter((_c, i) => kept.characters[i]),
        entries: proposal.entries.filter((_e, i) => kept.entries[i]),
        greetings: proposal.greetings.filter((_g, i) => kept.greetings[i]),
      }, art);
      setResult(describe(got));
      setProposal(null);
      setKept(null);
      if (fileRef.current) fileRef.current.value = "";
      onImported?.();
    } catch (err: any) {
      setError(err.detail ?? String(err));
    } finally {
      setBusy(false);
    }
  }

  const total = kept
    ? kept.characters.filter(Boolean).length + kept.entries.filter(Boolean).length
      + kept.greetings.filter(Boolean).length
    : 0;
  const castNames = proposal ? proposal.characters.map((c) => c.name).filter(Boolean) : [];

  return (
    <div>
      {error && <div className="banner">{error}</div>}
      {result && <div className="banner">{result}</div>}

      <div className="picker">
        <input ref={fileRef} type="file" aria-label="Scenario card file" />
        <select value={format} onChange={(e) => setFormat(e.target.value)} aria-label="Card format">
          {FORMATS.map((f) => <option key={f} value={f}>{f}</option>)}
        </select>
        <button className="primary" onClick={parseFile} disabled={busy}>
          {busy ? "Reading…" : "Read card"}
        </button>
      </div>
      <div className="picker">
        <input type="text" aria-label="Card URL" placeholder="…or a chub.ai character URL"
               value={url} onChange={(e) => setUrl(e.target.value)} />
        <button onClick={parseUrl} disabled={busy}>Read URL</button>
      </div>
      <div className="field-hint">
        For a card that describes a whole setting rather than one person. Reading it costs one
        model call and writes nothing — review what it proposes, drop what you do not want, then
        import.
      </div>

      {proposal && (
        <>
          <h4>Cast</h4>
          {proposal.characters.length === 0 ? (
            <div className="editor-empty">
              No cast proposed. The card's own entries and openers can still be imported.
            </div>
          ) : (
            <table className="table">
              <thead>
                <tr><th>Keep</th><th>Name</th><th>Description</th><th>Personality</th></tr>
              </thead>
              <tbody>
                {proposal.characters.map((c, i) => (
                  <tr key={i}>
                    <td>
                      <input type="checkbox" aria-label={`keep character ${i}`}
                             checked={kept!.characters[i]} onChange={() => toggle("characters", i)} />
                    </td>
                    <td>
                      <input type="text" aria-label={`character name ${i}`} value={c.name}
                             onChange={(e) => patchCharacter(i, { name: e.target.value })} />
                    </td>
                    <td>
                      <textarea aria-label={`character description ${i}`} rows={3} value={c.description}
                                onChange={(e) => patchCharacter(i, { description: e.target.value })} />
                    </td>
                    <td>
                      <textarea aria-label={`character personality ${i}`} rows={3} value={c.personality}
                                onChange={(e) => patchCharacter(i, { personality: e.target.value })} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          <h4>Entries</h4>
          {proposal.entries.length === 0 ? (
            <div className="editor-empty">No world-info in that card.</div>
          ) : (
            <table className="table">
              <thead>
                <tr><th>Keep</th><th>Name</th><th>Keys</th><th>Category</th><th>Body</th></tr>
              </thead>
              <tbody>
                {proposal.entries.map((e, i) => (
                  <tr key={i}>
                    <td>
                      <input type="checkbox" aria-label={`keep entry ${i}`}
                             checked={kept!.entries[i]} onChange={() => toggle("entries", i)} />
                    </td>
                    <td>
                      <input type="text" aria-label={`entry name ${i}`} value={e.name}
                             onChange={(ev) => patchEntry(i, { name: ev.target.value })} />
                    </td>
                    <td>
                      <input type="text" aria-label={`entry keys ${i}`} value={e.keys.join(",")}
                             onChange={(ev) => patchEntry(i, { keys: ev.target.value.split(",").map((k) => k.trim()).filter(Boolean) })} />
                    </td>
                    <td>
                      <select aria-label={`entry category ${i}`} value={e.category}
                              onChange={(ev) => patchEntry(i, { category: ev.target.value as EntityKind })}>
                        {CATEGORIES.map((k) => <option key={k} value={k}>{k}</option>)}
                      </select>
                    </td>
                    <td>{e.body.length > 80 ? e.body.slice(0, 80) + "…" : e.body}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          <h4>Openers</h4>
          {proposal.greetings.length === 0 ? (
            <div className="editor-empty">No greetings in that card.</div>
          ) : (
            <table className="table">
              <thead>
                <tr><th>Keep</th><th>Name</th><th>Opens on</th><th>Body</th></tr>
              </thead>
              <tbody>
                {proposal.greetings.map((g, i) => (
                  <tr key={i}>
                    <td>
                      <input type="checkbox" aria-label={`keep greeting ${i}`}
                             checked={kept!.greetings[i]} onChange={() => toggle("greetings", i)} />
                    </td>
                    <td>
                      <input type="text" aria-label={`greeting name ${i}`} value={g.name}
                             onChange={(e) => patchGreeting(i, { name: e.target.value })} />
                    </td>
                    <td>
                      {/* Cast NAMES, not ids — nothing has been created yet. Renaming a
                          character above moves every opener that names them. */}
                      <select aria-label={`greeting character ${i}`} value={g.character}
                              onChange={(e) => patchGreeting(i, { character: e.target.value, present: e.target.value ? [e.target.value] : [] })}>
                        <option value="">(nobody)</option>
                        {castNames.map((n) => <option key={n} value={n}>{n}</option>)}
                      </select>
                    </td>
                    <td>{g.body.length > 80 ? g.body.slice(0, 80) + "…" : g.body}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          <div className="form-actions">
            <label className="field-hint">
              <input type="checkbox" checked={art} onChange={(e) => setArt(e.target.checked)} />
              {" "}Download the openers' images into this world
            </label>
            <button className="primary" onClick={commit} disabled={busy || total === 0}>
              Import {total} record{total === 1 ? "" : "s"}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
