import { useEffect, useRef, useState } from "react";
import { api } from "../../api/client";
import type { Card } from "../../api/types";
import { ColumnSection } from "../PageShell";

/** A campaign-local character's one-line capsule and its tags (#62).
 *
 *  Campaign scope only: an emergent character has no world copy to describe,
 *  so both live on the campaign's side. The capsule is a sidecar, saved by PUT;
 *  the tags are the card's own free-text list, so saving them writes the version
 *  -- the same write the card tab makes.
 *
 *  A generated draft fills the two fields and touches nothing until Save. Save
 *  is refused until the stored capsule has been read, for the reason the voice
 *  anchor gives: a failed read must not let a draft overwrite a sentence the
 *  user never saw.
 *
 *  Mounted keyed on (campaign, character), so switching character remounts it.
 */
export function CapsuleSection(
  { campaignId, cid, vid, card, onSaved, onError }: {
    campaignId: string;
    cid: string;
    vid: string;
    card: Card;
    /** Called once something was written, so the page re-reads the card. */
    onSaved: () => void;
    onError: (err: unknown) => void;
  },
) {
  const currentTags = card.data.tags ?? [];
  const [capsule, setCapsule] = useState("");
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState("");
  const [tagsText, setTagsText] = useState("");
  const [busy, setBusy] = useState(false);      // generating
  const [saving, setSaving] = useState(false);
  const live = useRef(true);
  // Set to true on mount, not only false on unmount: StrictMode mounts,
  // unmounts and mounts again in development, so a ref that is only ever
  // cleared stays cleared for the second mount's whole life.
  useEffect(() => { live.current = true; return () => { live.current = false; }; }, []);
  /** What the server last confirmed, so Cancel has something to restore. */
  const stored = useRef("");

  useEffect(() => {
    api.getCampaignCharacterTagline(campaignId, cid)
      .then((r) => {
        if (!live.current) return;
        stored.current = r.tagline;
        setCapsule(r.tagline);
        setState("ready");
      })
      .catch(() => { if (live.current) setState("error"); });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function beginEdit() {
    setText(capsule);
    setTagsText(currentTags.join(", "));
    setEditing(true);
  }

  function cancel() {
    setEditing(false);
  }

  async function save() {
    if (state !== "ready" || saving) return;
    const nextCapsule = text.trim();
    const nextTags = parseTags(tagsText);
    const tagsChanged = !sameList(nextTags, currentTags);
    setSaving(true);
    let wrote = false;
    try {
      if (nextCapsule !== stored.current) {
        await api.setCampaignCharacterTagline(campaignId, cid, nextCapsule);
        wrote = true;
        stored.current = nextCapsule;
        if (live.current) setCapsule(nextCapsule);
      }
      if (tagsChanged) {
        await api.updateVersion({ kind: "campaign", id: campaignId }, cid, vid,
                                { ...card, data: { ...card.data, tags: nextTags } });
        wrote = true;
      }
      if (live.current) setEditing(false);
    } catch (err: unknown) {
      onError(err);
    } finally {
      if (live.current) setSaving(false);
      if (wrote) onSaved();
    }
  }

  async function generate() {
    // Only from a read that succeeded: a draft installed over an unread capsule
    // would arm a Save that overwrites a sentence nobody has seen.
    if (busy || saving || state !== "ready") return;
    setBusy(true);
    try {
      const r = await api.generateCampaignCapsule(campaignId, cid, vid);
      if (!live.current) return;
      // An empty completion is a failed generation, not a draft.
      if (!r.capsule.trim()) {
        onError("The model returned an empty capsule — nothing was changed.");
        return;
      }
      setText(r.capsule);
      setTagsText(r.tags.join(", "));
      setEditing(true);
    } catch (err: unknown) {
      onError(err);
    } finally {
      if (live.current) setBusy(false);
    }
  }

  return (
    <ColumnSection label="Capsule">
      {!editing ? <>
        {state === "loading" ? <p className="field-hint">Reading…</p>
          : state === "error" ? (
            <p className="field-hint">Could not read the capsule — reload to try
              again. Editing is disabled so a failed read cannot overwrite the
              stored capsule with a blank.</p>
          ) : capsule.trim() ? <p className="column-prose">{capsule}</p>
            : <p className="field-hint">No capsule yet.</p>}
        {currentTags.length > 0 && <p className="field-hint">Tags: {currentTags.join(", ")}</p>}
        <div className="column-actions">
          <button className="subtle" type="button" disabled={state !== "ready" || busy}
                  onClick={beginEdit}>{capsule.trim() ? "Edit" : "Write one"}</button>
          <button className="subtle" type="button"
                  disabled={busy || saving || state !== "ready"}
                  onClick={() => void generate()}>
            {busy ? "Generating…" : "Generate"}
          </button>
        </div>
      </> : <>
        <p className="field-hint">
          Blank leaves the campaign without its own capsule; a library record's
          capsule shows through again where there is one.
        </p>
        <textarea aria-label="Capsule" className="column-draft" rows={3} value={text}
                  disabled={busy} onChange={(e) => setText(e.target.value)} />
        <input aria-label="Tags" type="text" value={tagsText} disabled={busy}
               placeholder="Comma-separated tags"
               onChange={(e) => setTagsText(e.target.value)} />
        <div className="column-actions">
          <button className="primary" type="button" disabled={saving || busy} onClick={() => void save()}>
            {saving ? "Saving…" : "Save"}
          </button>
          <button className="subtle" type="button" disabled={saving} onClick={cancel}>
            Cancel
          </button>
        </div>
      </>}
    </ColumnSection>
  );
}

/** Comma-separated text to a tag list: trimmed, non-empty, de-duplicated
 *  case-insensitively, in the order typed. */
function parseTags(text: string): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  for (const part of text.split(",")) {
    const tag = part.trim();
    if (!tag || seen.has(tag.toLowerCase())) continue;
    seen.add(tag.toLowerCase());
    out.push(tag);
  }
  return out;
}

function sameList(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((t, i) => t === b[i]);
}

export default CapsuleSection;
