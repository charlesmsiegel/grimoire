// One staged-edit row. Its own component because #110 renders the rows in two
// places -- the ordinary list, and the collapsed low-confidence section under
// it -- and both must render an identical row bound to the SAME index. `i` is
// the row's position in `editRows`, which is what the conflict verdicts (#111)
// and the submitted batch are both keyed on, so it is passed in rather than
// recomputed from either list's own ordering.
import { AUTHORITY_LABELS, CONTRADICTION_SOURCES, isUncited, type EditRow } from "./editRows";
import type { SceneReview } from "./useSceneReview";

export default function AbsorbEditRow({ e, i, review }: {
  e: EditRow; i: number; review: SceneReview;
}) {
  const {
    absorb, conflictByRow, contradictionById, decide, editRow, editPayload,
    resolveConflict, setReviewQuote,
  } = review;
  const isNewRecord = e.kind === "new_character" || e.kind === "new_location" || e.kind === "new_lore";
  const conflict = conflictByRow.get(i);
  const setPayload = (patch: Record<string, unknown>) => editPayload(i, patch);
  // An approved row collapses to one dimmed line. Not hidden — a decision you
  // cannot see is a decision you cannot revisit, and UNDO has to have
  // something to sit on. A row with an unanswered conflict never collapses:
  // it is approved AND blocking, and folding it away would hide the only
  // thing standing between the reviewer and a refused save.
  if (e.approved && e.judged && !conflict) {
    return (
      <div className="absorb-edit done">
        <span className="absorb-done-mark" aria-hidden>✓</span>
        <span className="absorb-done-label">
          APPROVED · {e.label}{e.authored ? " · card edit" : ""}
        </span>
        <button className="subtle absorb-undo" aria-label={`Undo ${e.label}`}
                onClick={() => decide(i, "undecided")}>Undo</button>
      </div>
    );
  }
  return (
    // `.approved` is the card's standing verdict made visible: a row that
    // arrived pre-approved by band looks different from one still waiting on
    // a reviewer, and that difference is the panel's whole claim about which
    // rows need them.
    <div className={"absorb-edit" + (e.authored ? " authored" : "")
                    + (isUncited(e) ? " uncited" : "")
                    + (e.approved ? " approved" : "")
                    + (e.rejected ? " rejected" : "")}>
      <div className="absorb-edit-head">
        <span className="absorb-edit-label">
          {e.label}{e.authored ? " · card edit" : ""}
        </span>
        {/* The stamp says what the row rests on, in the words the reviewer
            needs: not "medium · self" but whether anybody was quoted and how
            sure the model was. An uncited row reads NO QUOTE, which is the
            whole reason it is in the panel's first drawer. */}
        <span className={"absorb-stamp" + (isUncited(e) ? " alert" : "")}>
          {isUncited(e) ? "NO QUOTE" : (e.review?.speaker || "NO SPEAKER")}
          {" · "}
          {e.review && e.review.certainty !== null
            ? `CERTAINTY ${e.review.certainty.toFixed(2)}`
            : "CERTAINTY UNRATED"}
        </span>
        {e.review && (
          <span className={`chip absorb-band absorb-band-${e.review.band}`}
                title={`certainty ${e.review.certainty ?? "not given"}` +
                       ` · score ${e.review.score}`}>
            {e.review.band} · {AUTHORITY_LABELS[e.review.authority] ?? e.review.authority}
          </span>)}
        {conflict && <span className="chip on absorb-conflict-badge">Changed</span>}
        {/* A row a LATER scene already answered differently (#78). Advisory,
            and worded as attribution rather than as a verdict: the badge
            names the scene and the save is unchanged by it. */}
        {contradictionById.get(e.id) && (
          <span className="chip absorb-contradiction-badge"
                title={`${CONTRADICTION_SOURCES[contradictionById.get(e.id)!.source]} in ` +
                       `"${contradictionById.get(e.id)!.label}"`}>
            later scene disagrees
          </span>)}
      </div>
      {/* Under the label rather than the diff for the rows whose "diff" is an
          editable textarea: the citation is what the proposal RESTS on, and a
          reviewer weighing the row needs it before they start rewriting the
          text. Display only — the server never reads it back. */}
      {e.review && (e.review.quote || e.review.speaker) && (
        <p className="field-hint absorb-evidence">
          {e.review.quote && <q>{e.review.quote}</q>}
          {e.review.speaker && (e.review.quote ? ` — ${e.review.speaker}` : e.review.speaker)}
        </p>)}
      {conflict && (
        <div className="absorb-conflict">
          <p className="field-hint">{conflict.reason} — it now reads:</p>
          <div className="absorb-stored">{conflict.stored}</div>
          <div className="form-actions">
            {/* Keeping what is stored means this proposal must NOT be written,
                and rejecting is the only thing that excludes one now. Merely
                un-approving would leave it in the batch, which is the exact
                opposite of what the button says. */}
            <button className="subtle" aria-label={`Keep stored ${e.label}`}
                    onClick={() => decide(i, "rejected")}>
              Keep stored</button>
            <button className="subtle" aria-label={`Replace stored ${e.label}`}
                    onClick={() => resolveConflict(i, conflict, "replace")}>
              Replace</button>
            {conflict.mergeable && (
              <button className="subtle" aria-label={`Merge stored ${e.label}`}
                      onClick={() => resolveConflict(i, conflict, "merge",
                                                    conflict.merged)}>
                Merge</button>)}
          </div>
        </div>)}
      {isNewRecord && (
        <input aria-label={`Name ${e.label}`} value={(e.payload?.name as string) ?? ""}
               onChange={(ev) => setPayload({ name: ev.target.value })} />
      )}
      {e.kind === "sheet" ? (
        <>
          {e.before && <div className="absorb-before">{e.before}</div>}
          <div className="absorb-after">{e.after}</div>
          {typeof e.payload?.note === "string" && e.payload.note && (
            <p className="field-hint">{e.payload.note}</p>
          )}
        </>
      ) : e.kind === "relationship" || e.kind === "bond" ? (
        <div className="absorb-diff">
          {e.before && <span className="absorb-before">{e.before}</span>}
          <span className="absorb-after">{e.after}</span>
        </div>
      ) : (
        <>
          {e.before && <div className="absorb-before">{e.before}</div>}
          <textarea aria-label={`After ${e.label}`} rows={2} value={e.after}
                    onChange={(ev) => editRow(i, { after: ev.target.value })} />
        </>
      )}
      {e.kind === "new_character" && (
        <>
          <textarea aria-label={`Personality ${e.label}`} rows={2}
                    placeholder="Personality"
                    value={(e.payload?.personality as string) ?? ""}
                    onChange={(ev) => setPayload({ personality: ev.target.value })} />
          <textarea aria-label={`Example dialogue ${e.label}`} rows={2}
                    placeholder="Example dialogue"
                    value={(e.payload?.mes_example as string) ?? ""}
                    onChange={(ev) => setPayload({ mes_example: ev.target.value })} />
          <textarea aria-label={`Evidence ${e.label}`} rows={2}
                    placeholder="Evidence"
                    value={(e.payload?.evidence as string) ?? ""}
                    onChange={(ev) => setPayload({ evidence: ev.target.value })} />
          <select aria-label={`Confidence ${e.label}`}
                  value={(e.payload?.confidence as string) ?? "thin"}
                  onChange={(ev) => setPayload({ confidence: ev.target.value })}>
            <option value="thin">Thin</option>
            <option value="sketched">Sketched</option>
            <option value="established">Established</option>
          </select>
          <textarea aria-label={`Open questions ${e.label}`} rows={2}
                    placeholder="Open questions"
                    value={(e.payload?.open_questions as string) ?? ""}
                    onChange={(ev) => setPayload({ open_questions: ev.target.value })} />
        </>
      )}
      {(e.kind === "new_character" || e.kind === "new_location") && (
        <input aria-label={`Suggested image prompt ${e.label}`}
               placeholder="Suggested image prompt"
               value={(e.payload?.sd_prompt as string) ?? ""}
               onChange={(ev) => setPayload({ sd_prompt: ev.target.value })} />
      )}
      {e.kind === "new_location" && !absorb?.location && (
        <label>
          <input type="checkbox" aria-label={`This is where the scene happened ${e.label}`}
                 checked={!!e.payload?.current_setting}
                 onChange={(ev) => setPayload({ current_setting: ev.target.checked })} />
          This is where the scene happened
        </label>
      )}
      <div className="absorb-verdict">
        <button className="btn-accent" aria-label={`Approve ${e.label}`}
                onClick={() => decide(i, "approved")}>Approve</button>
        {/* "Edit" is where the caret already is: every one of these rows
            renders its `after` as a textarea, so the button focuses it rather
            than opening a second editing mode nobody asked for. */}
        <button className="subtle" aria-label={`Edit ${e.label}`}
                onClick={(ev) => {
                  const card = (ev.currentTarget.closest(".absorb-edit") as HTMLElement | null);
                  card?.querySelector("textarea")?.focus();
                }}>Edit</button>
        <button className="subtle" aria-label={`Reject ${e.label}`}
                aria-pressed={!!e.rejected}
                onClick={() => decide(i, e.rejected ? "undecided" : "rejected")}>
          {e.rejected ? "Rejected" : "Reject"}
        </button>
        {/* Only offered when there is something to find: a quote the
            transcript pane can scroll to. */}
        {!isUncited(e) && (
          <button className="subtle absorb-find"
                  aria-label={`Find ${e.label} in transcript`}
                  onClick={() => setReviewQuote(e.review!.quote)}>
            Find in transcript →
          </button>
        )}
      </div>
    </div>
  );
}
