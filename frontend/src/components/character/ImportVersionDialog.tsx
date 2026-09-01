import { useMemo, useState } from "react";
import { type CharacterSummary } from "../../api/client";
import { useHotkeys } from "../../shortcuts/useHotkeys";

export type ImportChoice = {
  /** The character this card joins, or null to make a new one. */
  into: string | null;
  /** What the version is called. Blank falls back to the card's own
   *  `character_version`, then to the id — never to the character's name, which
   *  is the same for every version. */
  versionName: string;
  /** Where to fetch the card from, in URL mode. Empty for a file import. */
  url: string;
};

/** Where a card lands, asked before it is imported rather than derived.
 *
 *  Without this, a card imported into an existing character took its version
 *  name from the card's `character_version` or, failing that, the character's
 *  name — so the usual case, importing a second era of somebody you already
 *  have, produced a version indistinguishable from the first. There was also no
 *  way to say WHICH character a card should join except by opening that
 *  character's own form first.
 *
 *  Single-file imports only. A thirty-card drop is answered by making thirty
 *  characters, and a dialog per file would be worse than no dialog at all.
 */
export function ImportVersionDialog(
  { fileName, urlMode, characters, fixedTo, onCancel, onConfirm }: {
    /** The chosen file's name. Absent in URL mode, where nothing is chosen yet. */
    fileName?: string;
    /** Ask for a URL to fetch the card from instead of taking a chosen file.
     *  A chub.ai link brings the avatar, gallery and linked lorebooks with it;
     *  any other URL is fetched and parsed as a bare PNG or JSON card. */
    urlMode?: boolean;
    characters: CharacterSummary[];
    /** Pre-targeted at one character (the page's own `+ Import version…`), so
     *  the picker is not offered and only the name is asked for. */
    fixedTo?: { id: string; name: string };
    onCancel: () => void;
    onConfirm: (choice: ImportChoice) => void;
  },
) {
  const [asVersion, setAsVersion] = useState(!!fixedTo);
  const [into, setInto] = useState(fixedTo?.id ?? "");
  const [query, setQuery] = useState("");
  const [versionName, setVersionName] = useState("");
  const [url, setUrl] = useState("");

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    const rows = q ? characters.filter((c) => c.name.toLowerCase().includes(q)) : characters;
    return rows.slice(0, 8);
  }, [characters, query]);

  const chosen = fixedTo ?? characters.find((c) => c.id === into);
  const ready = (!asVersion || !!chosen) && (!urlMode || !!url.trim());

  useHotkeys(
    [{ keys: "escape", label: "Cancel", group: "THIS PANEL",
       whileTyping: true, run: onCancel }],
    { modal: true },
  );

  return (
    <div className="tagline-modal-backdrop" role="dialog" aria-label="Import card">
      <div className="tagline-modal import-dialog">
        <h3>{urlMode ? "Import from URL" : `Import ${fileName}`}</h3>

        {urlMode && (
          <label className="field">
            <span className="data-label">Card URL</span>
            <input type="url" aria-label="Card URL" value={url}
                   placeholder="chub.ai/characters/… · creator/slug · or a direct card URL"
                   onChange={(e) => setUrl(e.target.value)} />
          </label>
        )}

        {!fixedTo && (
          <div className="chips" role="group" aria-label="Import as">
            <button className={"chip" + (asVersion ? "" : " on")} aria-pressed={!asVersion}
                    onClick={() => setAsVersion(false)}>A new character</button>
            <button className={"chip" + (asVersion ? " on" : "")} aria-pressed={asVersion}
                    onClick={() => setAsVersion(true)}>A version of…</button>
          </div>
        )}

        {asVersion && !fixedTo && (
          <div className="import-target">
            <input type="search" aria-label="Find a character" placeholder="Find a character…"
                   value={query} onChange={(e) => setQuery(e.target.value)} />
            <div className="chips">
              {matches.map((c) => (
                <button key={c.id} className={"chip" + (into === c.id ? " on" : "")}
                        aria-pressed={into === c.id}
                        onClick={() => setInto(c.id)}>{c.name}</button>
              ))}
              {matches.length === 0 && <span className="field-hint">No character matches that.</span>}
            </div>
          </div>
        )}

        {asVersion && (
          <label className="field">
            <span className="data-label">Version name</span>
            <input type="text" aria-label="Version name" value={versionName}
                   placeholder="young · after the flood · chub v3"
                   onChange={(e) => setVersionName(e.target.value)} />
            <span className="field-hint">
              What this version is called in the list. Left blank, the card's own
              version field is used, and failing that the id.
            </span>
          </label>
        )}

        {asVersion && chosen && (
          <p className="field-hint">
            Added to <strong>{chosen.name}</strong> as
            {versionName.trim() ? <> “{versionName.trim()}”</> : " a new version"}.
          </p>
        )}

        <div className="form-actions">
          <button className="primary" type="button" disabled={!ready}
                  onClick={() => onConfirm({
                    into: asVersion ? (chosen?.id ?? null) : null,
                    versionName: asVersion ? versionName.trim() : "",
                    url: urlMode ? url.trim() : "",
                  })}>
            Import
          </button>
          <button className="subtle" type="button" onClick={onCancel}>Cancel</button>
        </div>
      </div>
    </div>
  );
}

export default ImportVersionDialog;
