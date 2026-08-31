import { useState } from "react";
import { api, type Card, type CharacterDetail, type EntityScope, type VersionRef } from "../../api/client";
import { ColumnSection } from "../PageShell";

/** A version's stored label lives in the card's own `extensions.grimoire_label`
 *  — so renaming one is a card write like any other, which is what makes it
 *  work in campaign scope without a route of its own: the write materializes
 *  the campaign's copy and leaves the world's label alone, exactly as the reach
 *  warning at the foot of this column promises.
 *
 *  Blank clears it, and the backend falls back to the card's `character_version`
 *  and then the id — so a cleared label shows the id rather than an empty row.
 */
function withLabel(card: Card, label: string): Card {
  const extensions = { ...(card.data.extensions ?? {}) } as Record<string, unknown>;
  if (label.trim()) extensions.grimoire_label = label.trim();
  else delete extensions.grimoire_label;
  return { ...card, data: { ...card.data, extensions } };
}

/** Every version of this character, and the ways to get another one.
 *
 *  Until the label was stored this list showed the CARD'S NAME per row, which
 *  is the same string for every version by construction — three versions of one
 *  character listed her name three times and the only thing that told them
 *  apart, the id, was never on screen.
 */
export function VersionList(
  { scope, detail, vid, locked, campaignLabel, worldVersions, onPick, onImportFromWorld,
    onOpenVersion, onImportFile, onChanged, onError }: {
    scope: EntityScope;
    detail: CharacterDetail;
    vid: string;
    /** campaign scope: the version this campaign is locked to, if any. */
    locked: string | null;
    campaignLabel: string;
    /** campaign scope: the source world's versions, for the import picker. */
    worldVersions: VersionRef[];
    onPick: () => void;
    onImportFromWorld: (fromVid: string) => void;
    onOpenVersion: (id: string) => void;
    /** world scope: import a card file as a new, named version. */
    onImportFile: () => void;
    onChanged: () => Promise<void> | void;
    onError: (err: unknown) => void;
  },
) {
  const worldScope = scope.kind === "world";
  const [renaming, setRenaming] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [importVid, setImportVid] = useState("");
  const [busy, setBusy] = useState(false);

  async function rename(id: string) {
    const card = detail.versions.find((v) => v.id === id)?.card;
    if (!card) return;
    setBusy(true);
    try {
      await api.updateVersion(scope, detail.meta.id, id, withLabel(card, draft));
      setRenaming(null);
      await onChanged();
    } catch (err: unknown) {
      onError(err);
    } finally {
      setBusy(false);
    }
  }

  async function addVersion() {
    const name = window.prompt("New version name?")?.trim();
    if (!name) return;
    setBusy(true);
    try {
      const current = detail.versions.find((v) => v.id === vid)?.card;
      if (!current) return;
      // Named rather than cloned-and-renamed: `create_version` stores the name
      // it is given, overwriting the label this card carries from the version
      // it was copied from.
      const { version } = await api.createVersion(scope, detail.meta.id, { name, card: current });
      await onChanged();
      onOpenVersion(version);
    } catch (err: unknown) {
      onError(err);
    } finally {
      setBusy(false);
    }
  }

  async function setDefault() {
    try {
      await api.setDefaultVersion(scope, detail.meta.id, vid);
      await onChanged();
    } catch (err: unknown) {
      onError(err);
    }
  }

  return (
    <ColumnSection label="Versions" count={detail.versions.length}>
      <div className="version-list">
        {detail.versions.map((v) => (
          renaming === v.id ? (
            <div className="version-row renaming" key={v.id}>
              {/* Focused by the callback ref rather than `autoFocus`: the
                  attribute steals focus on page load, which is what the lint is
                  about; this input exists only because Rename was just clicked. */}
              <input className="row-rename" ref={(el) => el?.focus()} value={draft}
                     aria-label={`Rename version ${v.name}`}
                     onChange={(e) => setDraft(e.target.value)}
                     onKeyDown={(e) => {
                       if (e.key === "Enter") void rename(v.id);
                       else if (e.key === "Escape") setRenaming(null);
                     }} />
              <button className="version-flag" type="button" disabled={busy}
                      onClick={() => void rename(v.id)}>Save</button>
            </div>
          ) : (
            <div className={"version-row" + (v.id === vid ? " active" : "")} key={v.id}>
              {/* The badges and the rename control are SIBLINGS of the pick
                  button, not children: inside it they would join its accessible
                  name, and a version is picked by its name. */}
              <button className="version-pick" aria-pressed={v.id === vid}
                      onClick={() => onOpenVersion(v.id)}>
                {v.name}
              </button>
              <button className="version-rename" type="button" title="Rename this version"
                      aria-label={`Rename ${v.name}`}
                      onClick={() => { setDraft(v.name); setRenaming(v.id); }}>✎</button>
              {v.id === locked
                ? <span className="version-flag locked">Locked in {campaignLabel}</span>
                : v.id === detail.meta.default_version
                  ? <span className="version-flag">default</span>
                  : null}
            </div>
          )
        ))}
      </div>

      <div className="column-actions">
        {(worldScope || !locked) && (
          <button className="subtle" type="button" disabled={busy} onClick={() => void addVersion()}>
            + New version
          </button>
        )}
        {worldScope && (
          <button className="subtle" type="button" onClick={onImportFile}>+ Import version…</button>
        )}
        {vid !== detail.meta.default_version && (
          <button className="subtle" type="button" onClick={() => void setDefault()}>Set default</button>
        )}
      </div>

      {!worldScope && (
        locked ? (
          <div className="version-lock-controls">
            <select aria-label="Import version" value={importVid}
                    onChange={(e) => setImportVid(e.target.value)}>
              <option value="">— world version —</option>
              {worldVersions.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
            </select>
            <button className="subtle" disabled={!importVid}
                    onClick={() => onImportFromWorld(importVid)}>Import from world</button>
          </div>
        ) : detail.versions.length > 1 ? (
          <div className="version-lock-controls">
            <span className="field-hint">Picking locks the viewed version and removes the others from this campaign. </span>
            <button className="subtle" onClick={onPick}>Pick this version</button>
          </div>
        ) : (
          <span className="field-hint">Single version; it locks when first used in a scene.</span>
        )
      )}
    </ColumnSection>
  );
}

export default VersionList;
