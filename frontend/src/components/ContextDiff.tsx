import { type ContextDiffLine, type PromptDiff, type PromptDiffSection,
         type PromptDiffSide } from "../api/client";
import { taskLabel, whenLabel } from "./turnLabels";

/** The `id` the server gives the composition as it stands now, as opposed to a
 *  captured turn's numeric one. Matches `routes.scenes.LIVE_SIDE`. */
const LIVE = "live";

/** The Context panel's third state (#130): not what the prompt is now, and not
 *  what it was at one past turn, but what moved between the two.
 *
 *  Two panels read side by side do not answer "what is different about this
 *  prompt", which is the question a reader arrives with after a reply goes
 *  wrong — a hundred sections agree and one does not, and finding that one by
 *  eye is the work this replaces.
 *
 *  Deliberately built out of `ContextBreakdown`'s own rows and `ChangesPanel`'s
 *  own diff lines rather than a third visual language: this sits in the same
 *  section as the first and shows the same kind of thing as the second, and a
 *  new vocabulary for either would only say they were unrelated.
 */
export function ContextDiff({ diff, recomputing = false }:
                             { diff: PromptDiff; recomputing?: boolean }) {
  // `s.moved` as well as the status: a section that was only dragged elsewhere
  // is `unchanged` in its content and is still a change to the prompt, so
  // filtering on status alone made a layout reorder invisible.
  const moved = diff.sections.filter((s) => s.status !== "unchanged" || s.moved);
  const same = diff.sections.length - moved.length;
  const delta = diff.head.total_tokens - diff.base.total_tokens;
  // History the packer trimmed off the FRONT leaves no section behind — the
  // `history` row carries how many messages went, not what they weighed — so
  // the only record that two turns cut different amounts is this side-level
  // total. Ignoring it let the panel say every section was identical when the
  // packer had demonstrably done different work.
  const cutDiffers = diff.base.dropped_tokens !== diff.head.dropped_tokens;

  return (
    <>
      <div className="ctx-caption">
        {sideLabel(diff.base)} → {sideLabel(diff.head)}
      </div>
      {/* The live end moved under this comparison — a turn landed — and the
          replacement is in flight. Said rather than blanked: see `recomputing`
          in `SceneInspector`. */}
      {recomputing && (
        <p className="field-hint">A turn has landed since this was computed; recomputing…</p>
      )}
      <div className="ctx-tokens">
        {diff.base.total_tokens.toLocaleString()} → {diff.head.total_tokens.toLocaleString()} tok
        {delta !== 0 && <span className={"ctx-delta " + (delta > 0 ? "up" : "down")}>
          {signed(delta)}
        </span>}
      </div>
      {cutDiffers && (
        <div className="ctx-tokens">
          {diff.base.dropped_tokens.toLocaleString()} → {diff.head.dropped_tokens.toLocaleString()}
          {" "}tok dropped to fit
          <span className={"ctx-delta " + (diff.head.dropped_tokens > diff.base.dropped_tokens
                                           ? "up" : "down")}>
            {signed(diff.head.dropped_tokens - diff.base.dropped_tokens)}
          </span>
        </div>
      )}
      {/* The two ends can have been packed to different ceilings — a snapshot
          carries the budget in force when it was captured — so a reader
          comparing across a budget change is told rather than left to wonder
          why the same section survived on one side and not the other. */}
      {diff.base.budget_tokens !== diff.head.budget_tokens && (
        <div className="field-hint">
          Packed to different budgets: {budgetLabel(diff.base)} vs {budgetLabel(diff.head)}.
        </div>
      )}
      {diff.base.model !== diff.head.model && (
        <div className="field-hint">
          Different models: {diff.base.model || "unknown"} vs {diff.head.model || "unknown"}.
        </div>
      )}

      {/* The live end is composed fresh on every request, and `{{random}}` /
          `{{roll}}` resolve at render time — so a section built out of those
          can read as changed when nothing in the campaign moved. Said here
          rather than left for the reader to discover, because the whole value
          of the panel is trusting what it marks. */}
      {diff.head.id === LIVE && (
        <p className="field-hint">
          The live side is composed fresh, so random and roll macros re-resolve
          each time. Two captured turns compare exactly.
        </p>
      )}

      {/* "differ", not "changed": a section whose only difference is where it
          now sits is in this list too, and it has no changed lines to point at
          — which is also why the affordance below promises detail rather than
          promising lines. */}
      {moved.length === 0 ? (
        <p className="field-hint">
          {cutDiffers
            ? "No section differs, but the two turns dropped different amounts to"
              + " fit the budget — history cut from the front leaves no section"
              + " behind to show it."
            : "Nothing changed — every section is identical, and the same weight"
              + " was cut to fit."}
        </p>
      ) : (
        <div className="ctx-caption">
          {moved.length} {moved.length === 1 ? "section differs" : "sections differ"} ·
          click a row for detail
        </div>
      )}

      {/* Keyed on the position, deliberately: one section can appear twice —
          a key that changed is reported as the removal and the addition it is —
          and two rows can share an id outright when a snapshot frozen before
          ids existed is keyed on a duplicated label. The list is rebuilt whole
          on every fetch and never reordered in place, so position IS identity
          here and a "stable" key would be the invented one. */}
      {/* eslint-disable-next-line react/no-array-index-key */}
      {moved.map((s, i) => <DiffSection key={`${i}:${s.id}:${s.status}`} section={s} />)}

      {/* Only alongside a list of changes: after "nothing changed — every
          section is identical", a line saying some were not shown reads as a
          hedge on the sentence above it. */}
      {moved.length > 0 && same > 0 && (
        <p className="field-hint">
          {same} unchanged {same === 1 ? "section" : "sections"} not shown.
        </p>
      )}
    </>
  );
}

function DiffSection({ section }: { section: PromptDiffSection }) {
  const before = section.base?.tokens ?? 0;
  const after = section.head?.tokens ?? 0;

  return (
    <details className={"ctx-section ctx-diff-section " + section.status}>
      <summary>
        <span className="ctx-dot" />
        <span className="ctx-label">{section.label}</span>
        {section.status !== "unchanged" && (
          <span className={"ctx-status " + section.status}>{section.status}</span>
        )}
        {section.moved && <span className="ctx-status moved">moved</span>}
        <span className="ctx-meta">{signed(after - before)}</span>
      </summary>
      {section.base && section.head && section.base.label !== section.head.label && (
        <div className="field-hint">Renamed from “{section.base.label}”.</div>
      )}
      {/* What the packer did to it, which is a change the words cannot show:
          a section can be identical and still not have been sent. */}
      {flagNotes(section).map((note) => (
        <div className="field-hint" key={note}>{note}</div>
      ))}
      {section.diff.length > 0 && (
        <div className="record-diff ctx-diff-lines">
          {/* Same reason, and more strongly: identical lines repeat constantly
              in a prompt, so a diff row has no identity except where it sits. */}
          {/* eslint-disable-next-line react/no-array-index-key */}
          {section.diff.map((line, i) => <DiffRow key={i} line={line} />)}
        </div>
      )}
    </details>
  );
}

function DiffRow({ line }: { line: ContextDiffLine }) {
  if (line.op === "skip")
    return (
      <div className="diff-line diff-skip">
        ⋯ {(line.count ?? 0).toLocaleString()} unchanged{" "}
        {line.count === 1 ? "line" : "lines"}
      </div>
    );
  return <div className={"diff-line diff-" + line.op}>{line.text}</div>;
}

const DROPPED = "Dropped by the budget packer — the model did not see this.";

/** The packer's verdict on a section, in words, for the cases the lines cannot
 *  carry: a drop, a pin, and history trimmed off the front.
 *
 *  Only DIFFERENCES, once both sides exist — a section dropped on both is not
 *  what this panel is for. A section only one side has has no difference to
 *  report, but "it was dropped" still matters there: an added section the
 *  packer then cut never reached the model, and reading its inserted lines
 *  without that would be reading a prompt that was not sent.
 */
function flagNotes(section: PromptDiffSection): string[] {
  const { base, head } = section;
  const only = head ?? base;
  if (!base || !head)
    return only?.dropped ? [DROPPED] : [];
  const notes: string[] = [];
  if (section.moved)
    // Order is not decoration: the packer drops from the bottom of a tier and
    // the model reads the prompt in sequence, so where a section sits is part
    // of what was sent.
    notes.push("Moved to a different position in the prompt.");
  if (base.dropped && head.dropped)
    // Equal flags, and still the first thing to say: the words below moved and
    // NEITHER version reached the model. Without it the panel shows a textual
    // change with no sign that it is not a cause of anything.
    notes.push("Dropped by the budget packer on both sides — the model saw neither version.");
  else if (base.dropped !== head.dropped)
    notes.push(head.dropped ? DROPPED : "Kept this time; the budget packer had dropped it.");
  if (base.pinned !== head.pinned)
    notes.push(head.pinned ? "Pinned, so the packer left it alone." : "No longer pinned.");
  if (base.trimmed !== head.trimmed)
    notes.push(`History trimmed: ${base.trimmed} → ${head.trimmed} messages`
               + " cut from the front.");
  // Identical words, different cost, and nothing above accounted for it. The
  // case that produces this is Conversation history: its tokens are counted per
  // MESSAGE, so a change in how the transcript groups into messages moves the
  // total while the joined text stays byte-identical. Renaming a PC does
  // exactly that — her old blocks stop matching the player list and reparse
  // from `user` to `assistant`, which merges runs that used to alternate.
  // Without this note the row is a bare token delta with no visible cause.
  if (section.diff.length === 0 && base.tokens !== head.tokens && !notes.length)
    notes.push("Identical text, counted differently — the transcript grouped into"
               + " a different number of messages, and each one carries its own"
               + " framing allowance.");
  return notes;
}

/** Which turn an end of the comparison was — named the way the Turn history
 *  rail names it, because that is the list the reader picked it from. The live
 *  end is a preview rather than a turn that happened, so it has neither a task
 *  nor a timestamp. */
function sideLabel(side: PromptDiffSide): string {
  if (side.id === LIVE) return "Live preview";
  // Falls back to the entry id, for the same reason `whenLabel` falls back to
  // the raw string: a snapshot is only required to carry a `task` and a `ts`
  // that are strings, not ones that say anything, and a heading reading " · "
  // over a real comparison is worse than one naming the turn by its number.
  return [taskLabel(side.task), whenLabel(side.ts)].filter(Boolean).join(" · ")
         || side.id;
}

function budgetLabel(side: PromptDiffSide): string {
  return side.budget_tokens > 0 ? `${side.budget_tokens.toLocaleString()} tok` : "no budget";
}

function signed(n: number): string {
  return n === 0 ? "±0" : `${n > 0 ? "+" : "−"}${Math.abs(n).toLocaleString()}`;
}
