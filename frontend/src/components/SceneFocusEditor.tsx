import { useCallback } from "react";
import { api } from "../api/client";
import { normalizeSceneFocus } from "./sceneDraft";
import { SceneFocusInput } from "./SceneFocusInput";
import { PromptInclusionNotice } from "./PromptInclusionNotice";
import { useAuthorialField } from "./useAuthorialField";

export function SceneFocusEditor({ cid, sid, onSaved }: { cid: string; sid: string; onSaved?: () => void }) {
  return <ScopedFocus key={`${cid}:${sid}`} cid={cid} sid={sid} onSaved={onSaved} />;
}

function ScopedFocus({ cid, sid, onSaved }: { cid: string; sid: string; onSaved?: () => void }) {
  const read = useCallback(() => api.getSceneFocus(cid, sid), [cid, sid]);
  const write = useCallback((text: string) => api.setSceneFocus(cid, sid, normalizeSceneFocus(text)), [cid, sid]);
  const field = useAuthorialField(read, write, onSaved);
  return <div className="scene-focus-editor">
    <div className="field-hint">
      Gentle authorial direction, not a guaranteed outcome or character knowledge.
      Shared with all actors: do not disclose hidden facts or credentials. Revise or clear it manually when it no longer fits.
    </div>
    <PromptInclusionNotice sectionId="scene_focus" />
    {field.error && <div className="banner" role="alert">{field.error}</div>}
    {field.loading && <div className="field-hint">Loading scene focus…</div>}
    {field.data?.status === "unreadable" && <div className="banner" role="alert">Scene focus could not be read. Retry before editing.</div>}
    {field.editing ? <>
      <SceneFocusInput value={field.draft} onChange={field.setDraft} disabled={field.saving} />
      <div className="form-actions">
        <button className="subtle" onClick={field.cancel} disabled={field.saving}>Cancel</button>
        <button className="primary" onClick={() => void field.save()} disabled={!field.readable || field.loading || field.saving}>
          {field.saving ? "Saving…" : "Save"}
        </button>
      </div>
    </> : <>
      {field.data?.status === "ok" && <p className="scene-focus-saved">{field.data.text}</p>}
      {field.data?.status === "empty" && <div className="field-hint">What should this scene explore? Leave blank for open-ended play.</div>}
      <button className="subtle" aria-label="Edit scene focus" onClick={field.edit} disabled={!field.readable || field.loading}>Edit</button>
    </>}
    <button className="subtle" onClick={field.reload} disabled={field.loading || field.saving}>Refresh</button>
  </div>;
}
