import { useMemo, useState } from "react";

import type { CharacterSummary, WorldSummary } from "../../api/wizard";
import { PCProfileFields, type ProfileFieldValues } from "./PCProfileFields";
import type { DraftPC, WizardDraft } from "./types";

interface Props {
  draft: WizardDraft;
  update: (patch: Partial<WizardDraft>) => void;
  candidates: Map<string, CharacterSummary[]>; // by world id
  loading: boolean;
  error: string | null;
  worlds: WorldSummary[];
}

export function StepPCs({ draft, update, candidates, loading, error, worlds }: Props) {
  const [newName, setNewName] = useState("");

  const availableRoleTags = useMemo(() => {
    const composedWorldIds = new Set(draft.worldRefs.map((r) => r.world_id));
    const tags = new Set<string>();
    for (const w of worlds) {
      if (composedWorldIds.has(w.id)) {
        for (const t of w.pc_role_tags ?? []) tags.add(t);
      }
    }
    return [...tags].sort();
  }, [worlds, draft.worldRefs]);

  const removePC = (ref: string) => {
    update({ pcs: draft.pcs.filter((p) => p.character_ref !== ref) });
  };

  const addLibraryPC = (worldId: string, character: CharacterSummary) => {
    const ref = `${worldId}/${character.id}`;
    if (draft.pcs.find((p) => p.character_ref === ref)) return;
    const pc: DraftPC = {
      character_ref: ref,
      name: character.name ?? character.id,
      owner: "local",
      origin: "library",
      role_tags: character.role_tags ?? [],
      profileDescription: "",
      profileGoals: [],
      profilePlayerNotes: "",
    };
    update({ pcs: [...draft.pcs, pc] });
  };

  const addNewPC = () => {
    const trimmed = newName.trim();
    if (!trimmed) return;
    const slug = trimmed.toLowerCase().replace(/[^a-z0-9]+/g, "-");
    const ref = `emergent/${slug}`;
    if (draft.pcs.find((p) => p.character_ref === ref)) return;
    update({
      pcs: [
        ...draft.pcs,
        {
          character_ref: ref,
          name: trimmed,
          owner: "local",
          origin: "new",
          role_tags: [],
          profileDescription: "",
          profileGoals: [],
          profilePlayerNotes: "",
        },
      ],
    });
    setNewName("");
  };

  const toggleRoleTag = (pcRef: string, tag: string) => {
    const updatedPCs = draft.pcs.map((p) => {
      if (p.character_ref !== pcRef) return p;
      const has = p.role_tags.includes(tag);
      return { ...p, role_tags: has ? p.role_tags.filter((t) => t !== tag) : [...p.role_tags, tag] };
    });
    update({ pcs: updatedPCs });
  };

  return (
    <div className="wizard-step">
      <h3>Step 4 — Player characters</h3>
      <p className="wizard-step-help">
        Pick existing PCs from the composed cast, or create new emergent PCs. At least one is
        required. Each PC defaults to <code>owner: local</code>.
      </p>

      {loading && <p className="wizard-meta">Loading cast…</p>}
      {error && <p className="wizard-error">{error}</p>}

      {draft.pcs.length > 0 && (
        <ul className="wizard-pc-list">
          {draft.pcs.map((pc) => (
            <li key={pc.character_ref}>
              <div>
                <strong>{pc.name}</strong>
                <small>
                  {pc.character_ref} · {pc.origin === "library" ? "library" : "new"}
                </small>
              </div>
              <button type="button" onClick={() => removePC(pc.character_ref)}>
                Remove
              </button>
              {availableRoleTags.length > 0 && (
                <div className="wizard-pc-role-tags">
                  <small>Role tags:</small>
                  <div className="wizard-role-tag-chips">
                    {availableRoleTags.map((tag) => (
                      <label key={tag} className="wizard-role-tag-chip">
                        <input
                          type="checkbox"
                          checked={pc.role_tags.includes(tag)}
                          onChange={() => toggleRoleTag(pc.character_ref, tag)}
                        />
                        <span>{tag}</span>
                      </label>
                    ))}
                  </div>
                </div>
              )}
              <PCProfileFields
                values={{
                  description: pc.profileDescription,
                  goals: pc.profileGoals,
                  playerNotes: pc.profilePlayerNotes,
                }}
                onChange={(vals: ProfileFieldValues) => {
                  const updatedPCs = draft.pcs.map((p) =>
                    p.character_ref === pc.character_ref
                      ? {
                          ...p,
                          profileDescription: vals.description,
                          profileGoals: vals.goals,
                          profilePlayerNotes: vals.playerNotes,
                        }
                      : p,
                  );
                  update({ pcs: updatedPCs });
                }}
                defaultExpanded={pc.origin === "new"}
              />
            </li>
          ))}
        </ul>
      )}

      <div className="wizard-pc-pickers">
        {[...candidates.entries()].map(([worldId, chars]) => {
          const pcs = chars.filter((c) => (c.role ?? "") === "pc");
          if (pcs.length === 0) return null;
          return (
            <fieldset key={worldId} className="wizard-pc-world">
              <legend>{worldId}</legend>
              <ul>
                {pcs.map((c) => {
                  const ref = `${worldId}/${c.id}`;
                  const already = Boolean(draft.pcs.find((p) => p.character_ref === ref));
                  return (
                    <li key={c.id}>
                      <span>{c.name ?? c.id}</span>
                      <button
                        type="button"
                        disabled={already}
                        onClick={() => addLibraryPC(worldId, c)}
                      >
                        {already ? "Added" : "Add"}
                      </button>
                    </li>
                  );
                })}
              </ul>
            </fieldset>
          );
        })}
      </div>

      <div className="wizard-pc-new">
        <label htmlFor="wizard-new-pc">Create new PC</label>
        <div>
          <input
            id="wizard-new-pc"
            type="text"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="Character name"
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                addNewPC();
              }
            }}
          />
          <button type="button" onClick={addNewPC} disabled={!newName.trim()}>
            Add
          </button>
        </div>
        <small>Created as campaign-emergent. You can edit details after creation.</small>
      </div>
    </div>
  );
}
