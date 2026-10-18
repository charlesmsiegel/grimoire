/** The response targets as the composer edits them: the one-shot values typed
 *  (`""` when none), and what the next reply is budgeted at without them. */
export type ComposerTargets = {
  words: string; paragraphs: string;
  /** The server's resolved figures, shown as each box's placeholder. */
  wordsDefault: string; paragraphsDefault: string;
};

/** The composer's first row: what kind of turn this is, how long the next
 *  reply should run, who answers it, and the way to what the model was shown.
 *
 *  Its own row, not cells inside the input bar. As a cell it was `flex: none`
 *  at 334px of un-shrinkable nowrap text -- 63% of the bar's 529px minimum --
 *  so a narrow column squeezed the textarea to 36px and then pushed Send clean
 *  out of the column. Here it is free to shrink and Send cannot be displaced.
 *
 *  Where the composer is wide enough for every knob it is one row whatever
 *  else is on: the sentences on it -- what Direct does, a held draft, the
 *  dossier and quick-reply notices -- give way (ellipsis, the whole sentence
 *  in the tooltip) rather than wrapping the strip onto a second row, which
 *  jumped the composer every time Direct, a dossier or a one-shot target came
 *  on. Where it is not (a phone), it wraps rather than scrolling sideways:
 *  knobs past the edge of a hidden-scrollbar strip were knobs nobody could
 *  see were there. The stylesheet decides which, from the composer's own
 *  width. */
export function ComposerMeta({
  directing, pcless, onDirect, held, targets, onTarget, onClearTargets,
  order, orderOpen, onOrder, dossierOpen, notice, inspectorOpen, onInspector,
}: {
  directing: boolean;
  /** An offscreen scene: there is no player character to speak as. */
  pcless: boolean;
  onDirect: (direct: boolean) => void;
  /** A recovered draft of the other kind waiting behind what is in the box. */
  held: "note" | "post" | null;
  targets: ComposerTargets;
  onTarget: (key: "response_continuation_words" | "response_continuation_paragraphs",
             value: string) => void;
  onClearTargets: () => void;
  /** The group's reply order, as named on its toggle; null with no group. */
  order: string | null;
  orderOpen: boolean;
  onOrder: () => void;
  dossierOpen: boolean;
  /** What the last quick reply has to say, for this scene. */
  notice: string | null;
  inspectorOpen: boolean;
  onInspector: () => void;
}) {
  const oneShot = !!(targets.words || targets.paragraphs);
  const heldText = held && `${held} held · clear the box to get it back`;
  return (
    <div className="composer-meta">
      {/* #83. The two things this composer can send have always been two
          things; until now only one of them had a name on screen. Shown in
          every scene, because the director turn is not offscreen-only — an
          empty send takes the same path in an ordinary scene, and a control
          that appeared only when `pcless` would hide half of what the feature
          does. */}
      <div className="composer-mode" role="group" aria-label="Turn kind">
        <button type="button" aria-pressed={!directing}
                disabled={pcless}
                title={pcless ? "No player character is in this scene" : "Post as your character"}
                onClick={() => onDirect(false)}>Speak</button>
        <button type="button" aria-pressed={directing}
                title="Steer the scene without posting"
                onClick={() => onDirect(true)}>Direct</button>
      </div>
      {/* What Direct actually costs and buys, said once, beside the control —
          the note is spent on this generation and the transcript never sees
          it. */}
      {directing && (
        <span className="composer-meta-hint composer-direct-hint"
              title="Steers the reply · never posted">steers the reply · never posted</span>
      )}
      {/* A note that could not be handed back because a draft of the other
          kind holds the box. Without this it waits in a place with nothing on
          screen naming it, which is its own way of losing what somebody
          wrote. */}
      {heldText && <span className="composer-meta-hint" title={heldText}>{heldText}</span>}
      {/* The next reply's length, one-shot: it rides the next turn and is
          spent once a reply lands. Each box's placeholder is what the reply is
          budgeted at without it, so the figures are not said a second time
          beside them; once one is typed, the badge says it is for the next
          reply only, and ✕ takes it back. Each box and its unit are one label,
          so the strip wraps between them and never inside one. */}
      <label className="composer-target">
        <input type="number" min="1" step="1" aria-label="Next reply words"
               className="response-target-input" value={targets.words}
               placeholder={targets.wordsDefault}
               onChange={(e) => onTarget("response_continuation_words", e.target.value)} />
        <span className="composer-meta-label">words</span>
      </label>
      <label className="composer-target">
        <input type="number" min="1" step="1" aria-label="Next reply paragraphs"
               className="response-target-input" value={targets.paragraphs}
               placeholder={targets.paragraphsDefault}
               onChange={(e) => onTarget("response_continuation_paragraphs", e.target.value)} />
        <span className="composer-meta-label">paragraphs</span>
      </label>
      {oneShot && (
        <span className="composer-oneshot">
          <span className="chip-oneshot" title="These targets apply to the next reply only">
            one-shot
          </span>
          <button type="button" className="chip-clear" title="Cancel the one-shot targets"
                  aria-label="Cancel the one-shot response targets"
                  onClick={onClearTargets}>✕</button>
        </span>
      )}
      {/* Who replies next, and in what order. Not disabled by a live run: the
          settings are read at the next round, and the PUT is not a transcript
          write. */}
      {order !== null && (
        <button type="button" className="composer-link" aria-expanded={orderOpen}
                title="Who replies next, and in what order"
                onClick={onOrder}>
          Order: {order}
        </button>
      )}
      <span className="header-spacer" />
      {/* Opening a dossier does not take the turn away from you, and this is
          where the app says so — beside the control you were about to use,
          while the draft and the caret are still exactly where you left them. */}
      {dossierOpen && (
        <span className="composer-notice" title="Still your turn · dossier open">
          Still your turn · dossier open
        </span>
      )}
      {notice !== null && (
        <span className="composer-notice" role="status" title={notice}>{notice}</span>
      )}
      <button type="button" className="composer-link"
              aria-expanded={inspectorOpen} onClick={onInspector}>
        {inspectorOpen ? "Hide what the model saw" : "What the model saw →"}
      </button>
    </div>
  );
}
