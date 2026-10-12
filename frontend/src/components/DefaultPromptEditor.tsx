import { useCallback } from "react";
import { Link } from "react-router-dom";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, type CampaignDefaultPrompt, type PromptField } from "../api/client";
import { PromptInclusionNotice } from "./PromptInclusionNotice";
import { useAuthorialField } from "./useAuthorialField";
import { sectionHref } from "../worldPaths";

type Scope = { kind: "world" | "campaign"; id: string };

export function DefaultPromptEditor({ scope }: { scope: Scope }) {
  return <ScopedDefaultPrompt key={`${scope.kind}:${scope.id}`} scope={scope} />;
}

function ScopedDefaultPrompt({ scope }: { scope: Scope }) {
  const read = useCallback(() => scope.kind === "world" ? api.getWorldDefaultPrompt(scope.id)
    : api.getCampaignDefaultPrompt(scope.id), [scope.kind, scope.id]);
  const write = useCallback((text: string) => scope.kind === "world" ? api.setWorldDefaultPrompt(scope.id, text)
    : api.setCampaignDefaultPrompt(scope.id, text), [scope.kind, scope.id]);
  const field = useAuthorialField<PromptField | CampaignDefaultPrompt>(read, write);
  const label = scope.kind === "world" ? "World default prompt" : "Campaign default prompt";
  const inherited = field.data && "world" in field.data ? field.data.world : null;

  return <div className="side-section default-prompt-editor">
    <h4>{scope.kind === "campaign" ? "Default prompts" : label}</h4>
    <div className="field-hint">
      Keep standing context compact. Shared with all actors, including NPC writers; do not include
      hidden plot facts, private knowledge or credentials. Existing repeated copies are not removed automatically.
      World edits affect future play in every bound campaign.
    </div>
    {scope.kind === "campaign" && inherited && <div className="inherited-default-prompt">
      <h5>World default prompt <span className="field-hint">— inherited live, read-only</span></h5>
      {inherited.status === "unreadable" ? <div className="banner">World default prompt could not be read.</div>
        : inherited.status === "unavailable" ? <div className="field-hint">No available bound world.</div>
        : inherited.status === "empty" ? <div className="field-hint">No world default prompt is set.</div>
        : <div className="detail-rendered"><Markdown remarkPlugins={[remarkGfm]}>{inherited.text}</Markdown></div>}
      {inherited.id && inherited.status !== "unavailable" && <Link
        to={sectionHref({ kind: "world", id: inherited.id }, { kind: "section", at: "overview" })}>World overview</Link>}
    </div>}
    <PromptInclusionNotice sectionId={scope.kind === "world" ? "world_default_prompt" : "campaign_default_prompt"} />
    {field.error && <div className="banner" role="alert">{field.error}</div>}
    {field.loading && <div className="field-hint">Loading {label.toLowerCase()}…</div>}
    {field.data?.status === "unreadable" && <div className="banner" role="alert">{label} could not be read. Retry before editing.</div>}
    {field.editing ? <>
      <label className="role">{label}
        <textarea aria-label={label} rows={6} value={field.draft} disabled={field.saving}
          onChange={(e) => field.setDraft(e.target.value)} />
      </label>
      <div className="form-actions">
        <button className="subtle" onClick={field.cancel} disabled={field.saving}>Cancel</button>
        <button className="primary" onClick={() => void field.save()} disabled={!field.readable || field.loading || field.saving}>
          {field.saving ? "Saving…" : "Save"}
        </button>
      </div>
    </> : <>
      {field.data?.status === "ok" && <div className="detail-rendered"><Markdown remarkPlugins={[remarkGfm]}>{field.data.text}</Markdown></div>}
      {field.data?.status === "empty" && <div className="field-hint">No {label.toLowerCase()} is set.</div>}
      <button className="subtle" aria-label={`Edit ${label}`} onClick={field.edit}
        disabled={!field.readable || field.loading}>Edit</button>
    </>}
    <button className="subtle" onClick={field.reload} disabled={field.loading || field.saving}>Refresh</button>
  </div>;
}
