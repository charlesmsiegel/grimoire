// The review itself: the chronicle the absorb wrote, what each phase managed
// to do, and the drawer of proposals the reviewer is currently answering.
import AbsorbEditRow from "./AbsorbEditRow";
import { dossierNotice, PHASE_LABELS } from "./editRows";
import type { SceneReview } from "./useSceneReview";

export default function ReviewPanel({ review }: { review: SceneReview }) {
  const {
    absorb, editChronicle, budgetCutPhases, conflictByRow, editRows, shownRows,
    openSection, uncitedRows, saveError, saveAbsorb, discard,
    approvedCount, rejectedCount,
    undecidedCount, saving, reviewBusy, retryAudit, retryingAudit,
    retryDossiers, retryingDossiers,
  } = review;
  if (!absorb) return null;
  return (
    <div className="absorb-panel">
      <h4>Review scene summary</h4>
      <label className="field-hint" htmlFor="absorb-oneline">One line</label>
      <input id="absorb-oneline" aria-label="Scene one-line" value={absorb.one_line}
             onChange={(e) => editChronicle({ one_line: e.target.value })} />
      <label className="field-hint" htmlFor="absorb-summary">Summary</label>
      <textarea id="absorb-summary" aria-label="Scene summary" rows={5} value={absorb.summary}
                onChange={(e) => editChronicle({ summary: e.target.value })} />
      {absorb.timeline_events.length > 0 && (
        <ul className="absorb-timeline">
          {absorb.timeline_events.map((t, i) => (
            <li key={i}><strong>{t.date}</strong> {t.text}</li>
          ))}
        </ul>
      )}
      {budgetCutPhases.length > 0 && (
        <div className="mechanics-notice">
          <p>This scene was only partly absorbed: the absorb time budget ran out.</p>
          {/* Deliberately does NOT point at End scene: that button posts the
              *active* scene and replaces this review wholesale, discarding
              every edit the reviewer has already approved or typed. The audit
              and the dossier phase each have their own scoped Retry below
              (#286); the voice check does not, so the setting is still the
              only honest remedy for that one. */}
          <p className="field-hint">
            Cut short: {budgetCutPhases.map((p) => PHASE_LABELS[p.name]).join(", ")}. The
            summary and its edits above are complete and safe to save. Where a step
            below offers a Retry, that re-runs it alone on a fresh budget; otherwise
            raise the absorb budget on the Configuration page so the next scene gets
            the rest.
          </p>
        </div>)}
      {absorb.mechanics.status === "ok" && absorb.mechanics.warnings.length === 0 && (
        <p className="field-hint">mechanics audited clean</p>)}
      {absorb.mechanics.warnings.length > 0 && (
        <ul className="mechanics-warnings">
          {absorb.mechanics.warnings.map((w, i) => <li key={i}>⚠ {w}</li>)}
        </ul>)}
      {(absorb.mechanics.status === "failed" || absorb.mechanics.status === "degraded") && (
        <div className="mechanics-notice">
          {/* "never ran" vs "failed": an audit the clock refused to start
              asked nothing of the model, so there is no finding to doubt —
              only work still owed. Retry (which gets a fresh budget) is the
              fix for both, which is why both keep the button. */}
          <p>{absorb.mechanics.status !== "failed"
              ? "Some mechanics findings could not be validated"
              : absorb.mechanics.budget_exhausted && !absorb.mechanics.attempted
                ? `Mechanics validation never ran: ${absorb.mechanics.reason}`
                : `Mechanics validation failed: ${absorb.mechanics.reason}`}</p>
          {absorb.mechanics.dropped.map((d, i) => (
            <p className="field-hint" key={i}>{d.id} {d.field ?? ""}: {d.reason}</p>))}
          <button onClick={retryAudit} disabled={reviewBusy}>
            {retryingAudit ? "Retrying…" : "Retry validation"}</button>
        </div>)}
      {(absorb.dossiers.status === "failed" || absorb.dossiers.status === "degraded") && (
        <div className="mechanics-notice">
          <p>{dossierNotice(absorb.dossiers)}</p>
          {absorb.dossiers.failed.map((d, i) => (
            <p className="field-hint" key={i}>{d.id}: {d.reason}</p>))}
          {absorb.dossiers.skipped.length > 0 && (
            <p className="field-hint">
              Never attempted, skipped: {absorb.dossiers.skipped.join(", ")}
            </p>)}
          {/* Offered for a budget skip and an outright failure alike, for
              the audit's reason: a fresh budget is what the retry gets, and
              a phase that broke on its own merits is still worth one more
              ask before the reviewer gives up on it. */}
          <button onClick={retryDossiers} disabled={reviewBusy}>
            {retryingDossiers ? "Retrying…" : "Retry dossiers"}</button>
        </div>)}
      {(absorb.voice.status === "failed" || absorb.voice.status === "degraded") && (
        <div className="mechanics-notice">
          {/* A voice check that did not run is worth saying out loud: silence
              would read as "everyone stayed in voice" (#59). */}
          {/* Status first, then failures: a phase that only ran out of
              budget is degraded with an empty `failed`, and calling that
              "failed" would overstate it. */}
          <p>{absorb.voice.status === "degraded"
              ? "Some voice checks could not be run"
              : absorb.voice.failed.length > 0
                ? "No voice check could be run"
                : `Voice check failed: ${absorb.voice.reason}`}</p>
          {absorb.voice.failed.map((d, i) => (
            <p className="field-hint" key={i}>{d.id}: {d.reason}</p>))}
          {absorb.voice.skipped.length > 0 && (
            <p className="field-hint">
              Never attempted, skipped: {absorb.voice.skipped.join(", ")}
            </p>)}
        </div>)}
      {conflictByRow.size > 0 && (
        <div className="mechanics-notice">
          <p>{conflictByRow.size === 1
            ? "One proposed change no longer matches what is stored"
            : `${conflictByRow.size} proposed changes no longer match what is stored`}
            {" — nothing was saved. Answer each one below, then save again."}</p>
        </div>)}
      {editRows.length > 0 && (
        <div className="absorb-edits">
          {/* One drawer at a time, chosen in the column. `uncited` is a
              cross-cutting view of the same rows the store groups hold:
              a row can be uncited AND a fact, and it needs to be
              reachable as both. */}
          {shownRows.map(([e, i]) => <AbsorbEditRow key={e.id} e={e} i={i} review={review} />)}
          {shownRows.length === 0 && (
            <p className="empty-state">
              <span className="empty-what">Nothing proposed here.</span> Pick
              another store from the column.
            </p>
          )}
          {openSection === "uncited" && uncitedRows.length === 0 && (
            <p className="empty-state">
              <span className="empty-what">Every proposal is cited.</span> The
              model quoted a line of transcript for all {editRows.length} of them.
            </p>
          )}
          {openSection === "low" && (
            <p className="field-hint">
              Not approved by default — the transcript does not clearly support
              them. Each one is here, in full, to be answered.
            </p>)}
        </div>
      )}
      {saveError && (
        <div className="mechanics-notice">
          <p>Could not save this review: {saveError}</p>
          <button className="subtle" onClick={saveAbsorb} disabled={reviewBusy}>
            Try saving again</button>
        </div>
      )}
      {/* The footer says what the button is about to do, and counts it.
          Rejecting is the only thing that excludes a proposal, so a row left
          untouched is one that will be written -- and that has to be readable
          BEFORE the click, not discovered afterwards in the world. */}
      <div className="review-footer">
        <span className="review-left">
          {approvedCount} accepted · {rejectedCount} rejected
          {undecidedCount > 0 && ` · ${undecidedCount} untouched`}
        </span>
        <p className="review-explain">
          {undecidedCount > 0
            ? `Saving accepts the ${undecidedCount} you have not touched. `
              + "Reject anything you do not want first."
            : "Every proposal has a decision. Nothing will be accepted silently."}
        </p>
        {/* Deliberately NOT disabled by `reviewBusy`: a retry runs on the
            absorb budget, which is unbounded at 0, so Cancel is the only
            way out of a request that may never answer. Safe because
            `discard`'s release invalidates that request on the way out. */}
        <button className="subtle" disabled={saving} onClick={discard}>
          Discard all</button>
        <button className="primary" onClick={saveAbsorb} disabled={reviewBusy}>
          {saving ? "Saving…"
            : undecidedCount > 0
              ? `Accept all ${undecidedCount} remaining & save`
              : `Save ${approvedCount + rejectedCount} decisions`}</button>
      </div>
    </div>
  );
}
