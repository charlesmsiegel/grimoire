import { Thinking } from "./Thinking";
import { useState } from "react";
import { api, type ResponseRecord } from "../api/client";
import RerollRoutePicker, { NO_REROLL_ROUTE, type RerollRoute } from "./RerollRoute";
import { ErrorNote } from "./ErrorNote";

export function ResponseControls({ cid, sid, responseId, canReroll, status, contextChanged,
  disabled = false, onDelete, onReroll, onActivate, onReplay, onCreateCharacter }: {
  cid: string; sid: string; responseId: string; canReroll: boolean;
  status?: string; contextChanged?: boolean; disabled?: boolean;
  onDelete: (id: string) => void; onReroll: (id: string, guidance: string, route: RerollRoute) => void;
  onActivate: (id: string, variant: string) => void; onReplay: () => void;
  onCreateCharacter?: () => void;
}) {
  const [guidance, setGuidance] = useState("");
  const [route, setRoute] = useState<RerollRoute>(NO_REROLL_ROUTE);
  const [routeOpen, setRouteOpen] = useState(false);
  const [record, setRecord] = useState<ResponseRecord | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);
  // Whether "Response actions" is open. Its body -- the steer box, the route
  // disclosure, five buttons -- is built only while it is: a transcript carries
  // one of these per response, nearly all of them shut for the whole scene,
  // and their bodies were most of the DOM a long scene mounted. Everything a
  // body holds (the steer, the route, the variants read) is state up here, so
  // closing it loses nothing.
  //
  // Set on the summary's click as well as on `toggle`: the click is where the
  // reader asked, and it lands synchronously, while `toggle` is queued behind
  // it as a task -- so a keyboard or pointer user never meets an open, empty
  // disclosure. `toggle` stays authoritative for any other way it opens.
  const [open, setOpen] = useState(false);
  async function variants() {
    if (loading || disabled) return;
    setLoading(true); setError(null);
    try { setRecord(await api.getResponse(cid, sid, responseId)); }
    catch (err) { setError(err); }
    finally { setLoading(false); }
  }
  return <div className="response-controls">
    {status === "incomplete" && <p className="subtle">Incomplete response</p>}
    {contextChanged && <p className="subtle">Earlier context changed</p>}
    {/* Not a controlled `open`: the browser owns a disclosure's state, and
        this only follows it. The click handler reads the element as it is
        before its own toggle runs, so it sets what the element is about to
        become. */}
    <details onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary onClick={(event) => setOpen(
        !(event.currentTarget.parentElement as HTMLDetailsElement).open)}>Response actions</summary>
      {open && <>
      {canReroll ? <>
        <label>Response steer<input aria-label="Response steer" value={guidance} disabled={disabled}
          onChange={(event) => setGuidance(event.target.value)} placeholder="Optional direction for this response" /></label>
        <details onToggle={(event) => setRouteOpen(event.currentTarget.open)}>
          <summary>Model for this reroll</summary>
          {routeOpen && <fieldset disabled={disabled}><RerollRoutePicker value={route} onChange={setRoute} /></fieldset>}
        </details>
        <button disabled={disabled} onClick={() => onReroll(responseId, guidance.trim(), route)}>Reroll response</button>
      </> : <>
        <p className="subtle">Historical context is unavailable for this reply. Replay explicitly rebuilds from this point.</p>
      </>}
      <button disabled={disabled} onClick={onReplay}>Replay from here</button>
      <button disabled={disabled} onClick={() => onDelete(responseId)}>Delete response</button>
      <button disabled={disabled || loading} onClick={() => void variants()}>Response variants</button>
      {onCreateCharacter && <button disabled={disabled} onClick={onCreateCharacter}>Create character from this passage</button>}
      {error !== null && <p role="alert"><ErrorNote err={error} /></p>}
      {record?.variants.map((variant, index) => <div key={variant.id}>
        <Thinking content={variant.reasoning ?? ""} />
        <p>{variant.content}</p>
        {variant.issue && <p className="subtle">Response issue: {variant.issue}</p>}
        <button disabled={disabled || variant.status !== "complete" || record.active_variant === variant.id}
          onClick={() => { setRecord(null); onActivate(responseId, variant.id); }}>
          Use variant {index + 1}{variant.status === "incomplete" ? " (incomplete)" : ""}
        </button>
      </div>)}
      </>}
    </details>
  </div>;
}
