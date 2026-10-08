import { memo } from "react";
import type { Actor, Message, TrackerEntry, TrackerSummary, UsagePostBucket } from "../../api/client";
import { PostCost } from "../cost";
import { Portrait } from "../Portrait";
import { ResponseControls } from "../ResponseControls";
import RerollRoutePicker, { type RerollRoute } from "../RerollRoute";
import { SavedThinking } from "../Thinking";
import { TrackerDisclosure } from "../tracker/TrackerDisclosure";
import { RenderedMarkdown } from "./StreamingMarkdown";
import { quotedIn } from "./citation";
import { useSwipe } from "./useSwipe";
import { useHotkeys } from "../../shortcuts/useHotkeys";

// Marks a manual dice-roll transcript line's speaker (backend: scenes.ROLL_SPEAKER).
// Prefixed with an invisible separator so it can never collide with a real
// typed speaker label or cast name — a character actually named "Roll" is
// unaffected.
export const ROLL_SPEAKER = "⁣Roll";
// Marks a scene transition line — join/leave, location change, time advance
// (backend: scenes.TRANSITION_SPEAKER); same invisible-separator prefix as
// ROLL_SPEAKER. Purely internal metadata: drift measurement uses it as a turn
// separator and reroll steps over it, but it is NEVER displayed — a transition
// renders as the unlabelled narration it was before the tag existed.
export const TRANSITION_SPEAKER = "⁣Scene";
// Marks a stored director note (backend: scenes.DIRECTOR_SPEAKER) — what the
// player typed to STEER a turn rather than to say in it. Same
// invisible-separator prefix as the two above.
//
// It is in the transcript so the generation it bought has an index to be
// charged against; it is not prose, so it is hidden by default and revealed by
// a per-scene toggle. Shown, it renders under a dashed rule rather than a
// speaker plate: it is a stage direction in the margin, not a line in the
// scene.
export const DIRECTOR_SPEAKER = "⁣Note";
// What a shown note is labelled. Never the model's label and never the
// player's: it is neither of them speaking.
export const DIRECTOR_LABEL = "Note";

/** Consecutive posts by one speaker, under a single plate. `index` is the
 *  post's ABSOLUTE transcript index — what an edit, a cut or a reroll
 *  addresses it by — not its position in the loaded window. */
export type TranscriptRunData = {
  speaker: string; pc: boolean; actor: Actor | undefined;
  posts: { m: Message; index: number }[];
};

/** What a row can ask the play view to do.
 *
 *  One object built once per view, whose members always run the view's latest
 *  handlers (see `CampaignView`'s `useStableHandlers`). That is what lets a row
 *  be memoized at all: a handler closed over the render that drew it would
 *  have to be a prop that changes on every keystroke, and the rows would
 *  re-render with it. */
export type TranscriptActions = {
  openActor: (kind: string, id: string) => void;
  openReroll: () => void;
  stepAlternate: (delta: number) => void;
  /** Step the last response's variants: ‹ is -1, › is +1. At the newest, +1
   *  generates a new one (the ›, the touch swipe — never the → key). */
  stepVariant: (delta: -1 | 1) => void;
  /** Open (or keep typing into) the edit form for one post. */
  edit: (index: number, text: string) => void;
  /** Hide a post from context (`true`) or return it (`false`). */
  toggleExcluded: (index: number, excluded: boolean) => void;
  cancelEdit: () => void;
  saveEdit: () => void;
  saveRetcon: () => void;
  pickImage: (index: number, actor: Actor | undefined, speaker: string) => void;
  cutFrom: (index: number) => void;
  replayFrom: (index: number) => void;
  /** Branch the scene from this post: a sibling keeping everything through it
   *  (or, for an absorbed scene, a copy of the campaign cut at it). */
  branchFrom: (index: number) => void;
  setRerollPrompt: (text: string | null) => void;
  setRerollRoute: (route: RerollRoute) => void;
  reroll: () => void;
  deleteResponse: (id: string) => void;
  rerollResponse: (id: string, guidance: string, route: RerollRoute) => void;
  /** Keep writing: continue the trailing response (play controls IV). */
  extendResponse: (id: string, guidance: string, route: RerollRoute) => void;
  activateVariant: (id: string, variant: string) => void;
  createCharacter: (responseId: string) => void;
  /** Open the regex test pane on this post's stored text (spec 6.2). */
  testRules: (index: number, m: Message) => void;
  /** Ask the view to re-read the scene's tracker after a disclosure changed it
   *  (a retry, a re-run, an edit). A member of this object rather than a
   *  closure handed down per render, so the memoized rows keep their props. */
  refreshTracker: () => void;
};

/** The swipe control, on the one row it hangs off. */
export type TranscriptSwipe = {
  active: number | null; count: number; title: string | undefined; disabled: boolean;
};

/** The ledger's swipe control, on the last response. `position` and `count`
 *  are over the COMPLETE variants only; `generates` is true at the newest,
 *  where › makes another rather than stepping. Both disabled flags are worked
 *  out by the view, which is the one that knows every guard. */
export type TranscriptVariantSwipe = {
  position: number; count: number; title?: string;
  previousDisabled: boolean; nextDisabled: boolean; generates: boolean;
};

/** What hangs off the reroll row, when this run holds it: the swipe control
 *  (the legacy alternates', or the ledger's on a response), and the reroll
 *  popover's guidance and route while it is open. */
export type TranscriptReroll = {
  swipe: TranscriptSwipe | null;
  variantSwipe: TranscriptVariantSwipe | null;
  pop: { prompt: string; route: RerollRoute } | null;
  /** Keep writing, offered on the swipe target's response controls; null
   *  where there is no trailing response to continue. */
  extend: { disabled: boolean } | null;
};

/** Everything the rows read that is the same for every row.
 *
 *  Memoized by the view on exactly these fields, so the things that re-render
 *  the view without touching the transcript — a keystroke in the composer, a
 *  streamed delta — hand every run the same object and no row re-renders.
 *
 *  What changes per keystroke of a box INSIDE the transcript — the edit form,
 *  the reroll guidance — is not in here: it is handed to the one run it
 *  concerns (`TranscriptRun`'s `editing` and `reroll`), so typing in it
 *  re-renders that run and not every plate above it. */
export type TranscriptContext = {
  cid: string;
  /** The active scene. Read only where `active` holds, which implies one. */
  sid: string;
  /** The scene the posts on screen were read for, for the saved-thinking
   *  fetch — which asks for the transcript's own scene, not the active one. */
  loadedCid: string | null; loadedSid: string | null;
  busy: boolean; rolling: boolean;
  /** `transcriptIsActive`: the posts on screen are the active scene's own. */
  active: boolean;
  /** The active scene has been absorbed: no post can be hidden from context
   *  there (there is no future prompt to protect), so the toggle is not shown. */
  absorbed: boolean;
  responseDisabled: boolean;
  /** Absolute index of the last post in the window. */
  lastIndex: number;
  rerollAt: number; canReroll: boolean;
  postChips: Record<number, UsagePostBucket> | null;
  /** The hovered citation, lowercased; "" is none. */
  citedNeedle: string;
  /** Absolute indices of each response's last part in the window — the post
   *  that carries its controls and its saved thinking. */
  lastOfResponse: Set<number>;
  /** The loaded scene's tracker read, or null before it lands. Its parts keep
   *  their identity across a re-read that finds them unchanged (`shareSummary`),
   *  which is what lets a row take its own entry as a prop. */
  tracker: TrackerSummary | null;
  /** Absolute post index -> the tracker's key for it. */
  trackerKeys: Record<number, string>;
  /** Tracker key -> how many tracked posts a re-run from it covers. */
  trackerRerun: Record<string, number>;
};

// One shared object for "no tracker yet", so a row's props do not change
// identity on every render while the read is outstanding.
const NO_NAMES: Record<string, string> = {};

/** One speaker's run: the plate, then each post.
 *
 *  Re-renders when its run, its portrait, the shared context or its own share
 *  of the in-transcript boxes changes, and each post under it compares its own
 *  props again — so a keystroke in the edit form or the reroll guidance
 *  re-renders one run, and in it one row. */
export const TranscriptRun = memo(function TranscriptRun({
  run, avatar, ctx, editing, reroll, actions,
}: {
  run: TranscriptRunData;
  avatar: string | null;
  ctx: TranscriptContext;
  /** The edit form, when the post it is open on is in this run; else null. */
  editing: { index: number; text: string } | null;
  /** The reroll row's extras, when that row is in this run; else null. */
  reroll: TranscriptReroll | null;
  actions: TranscriptActions;
}) {
  return (
    <div className={"run" + (run.pc ? " pc" : "")
                     + (run.speaker === DIRECTOR_LABEL ? " director-note" : "")}>
      <div className={"plate" + (run.pc ? " pc" : "")}>
        {run.actor ? (
          <>
            {/* The same thing clicking them in the cast grid does, and
                for the same reason: a speaker in the transcript is in
                this scene, so the column has a dossier for them. A
                drawer over the transcript to read about someone who
                is standing in it was a modal answering a question the
                column beside it already answers. */}
            <button className="plate-avatar" aria-label={`Open ${run.speaker} record`}
                    onClick={() => actions.openActor(run.actor!.kind, run.actor!.id)}>
              <Portrait src={avatar} name={run.speaker} />
            </button>
            <button className="plate-name"
                    onClick={() => actions.openActor(run.actor!.kind, run.actor!.id)}>
              {run.speaker}
            </button>
          </>
        ) : (
          <>
            <span className="plate-avatar"><Portrait src={avatar} name={run.speaker} /></span>
            <span className="plate-name">{run.speaker}</span>
          </>
        )}
        <span className="role-chip">{run.pc ? "pc" : "npc"}</span>
      </div>
      {run.posts.map(({ m, index }) => {
        const rerollRow = index === ctx.rerollAt;
        return (
          <TranscriptPost
            key={index} m={m} index={index} actor={run.actor} speaker={run.speaker}
            // Substring, for the reason `goToQuote` gives: the citation records
            // an excerpt, and there is no index to trust.
            cited={quotedIn(m, ctx.citedNeedle)}
            editingText={editing?.index === index ? editing.text : null}
            busy={ctx.busy} rolling={ctx.rolling} active={ctx.active} absorbed={ctx.absorbed}
            rerollButton={rerollRow && ctx.canReroll && !m.response_id}
            swipe={rerollRow && !m.response_id ? reroll?.swipe ?? null : null}
            variantSwipe={rerollRow && m.response_id && ctx.lastOfResponse.has(index)
              ? reroll?.variantSwipe ?? null : null}
            rerollPop={rerollRow && ctx.canReroll ? reroll?.pop ?? null : null}
            extend={rerollRow && m.response_id && ctx.lastOfResponse.has(index)
              ? reroll?.extend ?? null : null}
            canReplayAfter={index < ctx.lastIndex}
            chip={m.role === "user" || m.speaker === DIRECTOR_SPEAKER
              ? ctx.postChips?.[index] : undefined}
            lastOfResponse={ctx.lastOfResponse.has(index)}
            trackerKey={ctx.trackerKeys[index]}
            trackerEntry={ctx.trackerKeys[index] !== undefined
              ? ctx.tracker?.entries[ctx.trackerKeys[index]] : undefined}
            trackerNames={ctx.tracker?.names ?? NO_NAMES}
            trackerLabels={ctx.tracker?.labels ?? NO_NAMES}
            trackerEnabled={ctx.tracker?.enabled ?? false}
            trackerRerun={ctx.trackerKeys[index] !== undefined
              ? ctx.trackerRerun[ctx.trackerKeys[index]] ?? 1 : 0}
            loadedCid={ctx.loadedCid} loadedSid={ctx.loadedSid}
            cid={ctx.cid} sid={ctx.sid} responseDisabled={ctx.responseDisabled}
            actions={actions}
          />
        );
      })}
    </div>
  );
});

/** One post: its gutter, its body and its response controls.
 *
 *  Props are primitives or objects whose identity only moves when what they
 *  describe does. A scene refresh replaces every message object, so every row
 *  re-renders then — once per turn — but a keystroke or a delta reaches none. */
export const TranscriptPost = memo(function TranscriptPost({
  m, index, actor, speaker, cited, editingText, busy, rolling, active, absorbed, rerollButton, swipe,
  variantSwipe, rerollPop, extend, canReplayAfter, chip, lastOfResponse, trackerKey, trackerEntry, trackerNames,
  trackerLabels, trackerEnabled, trackerRerun, loadedCid, loadedSid, cid, sid, responseDisabled, actions,
}: {
  m: Message; index: number;
  /** Who spoke it, for the image picker's scope (#376). */
  actor: Actor | undefined; speaker: string;
  cited: boolean;
  /** This post's edit buffer, or null when it is not the one being edited. */
  editingText: string | null;
  busy: boolean; rolling: boolean; active: boolean; absorbed: boolean;
  rerollButton: boolean;
  swipe: TranscriptSwipe | null;
  /** The ledger's arrows, on the one row that is the swipe target; else null. */
  variantSwipe: TranscriptVariantSwipe | null;
  rerollPop: { prompt: string; route: RerollRoute } | null;
  /** Keep writing, on the trailing response's last part only; else null. */
  extend: { disabled: boolean } | null;
  canReplayAfter: boolean;
  chip: UsagePostBucket | undefined;
  lastOfResponse: boolean;
  /** This post's tracker key (absent where the post is not tracked), its entry
   *  from the summary, and the scene-wide names, labels and switch. All
   *  primitives or identity-stable parts of the summary. */
  trackerKey: string | undefined;
  trackerEntry: TrackerEntry | undefined;
  trackerNames: Record<string, string>;
  trackerLabels: Record<string, string>;
  trackerEnabled: boolean;
  trackerRerun: number;
  loadedCid: string | null; loadedSid: string | null;
  cid: string; sid: string;
  responseDisabled: boolean;
  actions: TranscriptActions;
}) {
  const editing = editingText !== null;
  // Every row calls it (a hook cannot be conditional); only the swipe target
  // spreads the handlers, and only it is enabled. `stepVariant` re-checks the
  // disabled flags, so a swipe can do nothing the arrows could not.
  const gesture = useSwipe({
    enabled: variantSwipe !== null && !editing,
    onNext: () => actions.stepVariant(1),
    onPrevious: () => actions.stepVariant(-1),
  });
  return (
    /* `.cited` marks the line a hovered citation was taken from. */
    <div className={`msg ${m.role}` + (cited ? " cited" : "") + (variantSwipe ? " swipe-target" : "")
                     + (m.excluded ? " excluded" : "")}
         {...(variantSwipe ? gesture : {})}>
      <span className="msg-gutter">
        {!editing && !busy && (
          <span className="gutter-icons">
            {rerollButton && (
              <button className="msg-edit" title="Reroll" aria-label="Reroll"
                      disabled={rolling} onClick={actions.openReroll}>↻</button>
            )}
            {swipe && (
              <span className="swipe-nav">
                <button className="msg-edit" aria-label="Previous alternate"
                        disabled={swipe.disabled}
                        onClick={() => actions.stepAlternate(-1)}>‹</button>
                <span className="swipe-count" title={swipe.title}>
                  {swipe.active === null ? "–" : swipe.active + 1}/{swipe.count}
                </span>
                <button className="msg-edit" aria-label="Next alternate"
                        disabled={swipe.disabled}
                        onClick={() => actions.stepAlternate(1)}>›</button>
              </span>
            )}
            {variantSwipe && (
              <span className="swipe-nav">
                <button className="msg-edit" aria-label="Previous reply variant"
                        disabled={variantSwipe.previousDisabled}
                        onClick={() => actions.stepVariant(-1)}>‹</button>
                <span className="swipe-count" title={variantSwipe.title}>
                  {variantSwipe.position + 1}/{variantSwipe.count}
                </span>
                <button className="msg-edit"
                        aria-label={variantSwipe.generates
                          ? "Generate a new reply variant" : "Next reply variant"}
                        disabled={variantSwipe.nextDisabled}
                        onClick={() => actions.stepVariant(1)}>›</button>
              </span>
            )}
            {m.speaker !== ROLL_SPEAKER && active && (
              <button className="msg-edit" title="Edit message" aria-label={`Edit message ${index + 1}`}
                      disabled={rolling}
                      onClick={() => actions.edit(index, m.content)}>✎</button>
            )}
            {/* Hide from context: the post stays here and in every export,
                and reaches no prompt. Player and model posts only -- a roll
                line is half of an immutable rolls.json fact, a transition is
                half of the scene's location/time history, and a note never
                reaches a story prompt anyway. Not on an absorbed scene,
                which has no future prompt to protect. */}
            {active && !absorbed && m.speaker !== ROLL_SPEAKER && m.speaker !== TRANSITION_SPEAKER
              && m.speaker !== DIRECTOR_SPEAKER && (
              <button className="msg-edit msg-exclude"
                      title={m.excluded ? "Return to context" : "Hide from context"}
                      aria-label={`${m.excluded ? "Return" : "Hide"} message ${index + 1} ${m.excluded ? "to" : "from"} context`}
                      aria-pressed={!!m.excluded}
                      disabled={rolling}
                      onClick={() => actions.toggleExcluded(index, !m.excluded)}>⊘</button>
            )}
            {/* Refused on a dice-roll line for the same reason
                Edit is: this rewrites the post, and that line's
                text must stay in lockstep with an immutable
                rolls.json entry. Everything else about it is
                Edit — it opens the very same buffer, with the
                reference already in it (#376). */}
            {m.speaker !== ROLL_SPEAKER && active && (
              <button className="msg-edit" title="Insert an image"
                      aria-label={`Insert an image into message ${index + 1}`}
                      disabled={rolling}
                      onClick={() => actions.pickImage(index, actor, speaker)}>🖼</button>
            )}
            {/* Offered on a dice-roll line too, where Edit is not:
                that line is refused because its text must stay in
                lockstep with an immutable rolls.json entry, and a
                cut removes the line rather than rewriting it. */}
            {active && (
              <button className="msg-edit msg-cut" title="Delete this post and everything after it"
                      aria-label={`Delete message ${index + 1} and everything after it`}
                      disabled={rolling}
                      onClick={() => actions.cutFrom(index)}>🗑</button>
            )}
            {/* Replay the turns AFTER this one (#79): the post
                itself stands -- it is the one that was retconned
                -- and everything past it is cut and re-run one
                turn at a time. Offered only where there is
                something after it to replay, and on a roll line
                too: a cut span may contain one, and replaying it
                re-posts the line while `rolls.json` keeps the
                entry it names. */}
            {/* Offered on a post the rules can touch, which is every post
                but a synthetic line (a roll, a transition, a director note).
                Read-only, so it needs neither `active` nor `rolling`. */}
            {m.speaker !== ROLL_SPEAKER && m.speaker !== TRANSITION_SPEAKER
              && m.speaker !== DIRECTOR_SPEAKER && (
              <button className="msg-edit" title="Test rules on this post"
                      aria-label={`Test rules on message ${index + 1}`}
                      onClick={() => actions.testRules(index, m)}>⚗</button>
            )}
            {active && canReplayAfter && (
              <button className="msg-edit" title="Replay the turns after this post"
                      aria-label={`Replay the turns after message ${index + 1}`}
                      disabled={rolling}
                      onClick={() => actions.replayFrom(index + 1)}>⏩</button>
            )}
            {/* Branch from here (play controls III): on every post of an
                active scene, roll lines included -- the branch keeps this post
                as its last. */}
            {active && (
              <button className="msg-edit" title="Branch from here"
                      aria-label={`Branch from message ${index + 1}`}
                      disabled={rolling}
                      onClick={() => actions.branchFrom(index)}>⑂</button>
            )}
          </span>
        )}
        {rerollPop && !busy && (
          <RerollPopover cid={cid} pop={rerollPop} rolling={rolling} actions={actions} />
        )}
      </span>
      <div className="msg-body">
        {m.excluded && !editing && <span className="not-in-context">not in context</span>}
        {/* What this post cost to answer, over every reroll of it
            (#153). On what the player PUT there and nothing
            else: those are the lines a generation was made FOR,
            and a chip on the reply would double-count the same
            spend under the text it paid for.
            A director note is one of them. It is assistant-role
            by the transcript format's construction, so a bare
            `role === "user"` test left the one line whose whole
            reason for being stored is that it can carry a figure
            as the only line that could not. Suppressed while the
            post is being edited, where the row is a form and not
            a message. */}
        {!editing && chip !== undefined && <PostCost bucket={chip} />}
        {editing ? (
          <div className="msg-edit-form">
            <textarea aria-label="Edit message" rows={4} value={editingText}
                      onChange={(e) => actions.edit(index, e.target.value)} />
            <div className="form-actions">
              <button className="subtle" onClick={actions.cancelEdit}>Cancel</button>
              {/* Beside Save rather than instead of it: Save fixes
                  the words, Retcon says the scene did not happen
                  that way and takes back what it wrote (#78). */}
              <button className="subtle" onClick={actions.saveRetcon} disabled={rolling}
                      title="Rewrite this post and take back what the scene recorded">
                Retcon
              </button>
              <button className="primary" onClick={actions.saveEdit} disabled={rolling}>Save</button>
            </div>
          </div>
        ) : (
          <>
            {m.response_id && m.response_thinking && loadedCid !== null && loadedSid !== null
              && lastOfResponse && <SavedThinking key={`${loadedCid}:${loadedSid}:${m.response_thinking}`}
              cid={loadedCid} sid={loadedSid} responseId={m.response_id} variantId={m.response_thinking} />}
            {/* What the display rules made of it, when they changed it.
                The edit form above still starts from `content`: the raw text
                is what is stored, and what an edit rewrites. */}
            <RenderedMarkdown content={m.shown ?? m.content} />
            {trackerKey !== undefined && loadedCid !== null && loadedSid !== null
              && (m.role === "user" || lastOfResponse)
              && <TrackerDisclosure key={`${loadedCid}:${loadedSid}:${trackerKey}`}
                cid={loadedCid} sid={loadedSid} trackerKey={trackerKey} entry={trackerEntry}
                names={trackerNames} labels={trackerLabels} enabled={trackerEnabled}
                rerunCount={trackerRerun} onChanged={actions.refreshTracker} />}
          </>
        )}
        {m.response_id && m.role === "assistant" && active && lastOfResponse && (
          <ResponseControls key={`${m.response_id}:${m.content}`} cid={cid} sid={sid}
            responseId={m.response_id} canReroll={!!m.response_can_reroll}
            status={m.response_status} contextChanged={m.context_changed}
            disabled={responseDisabled}
            onDelete={actions.deleteResponse}
            onReroll={actions.rerollResponse}
            onExtend={extend ? actions.extendResponse : undefined}
            extendDisabled={extend?.disabled ?? false}
            onActivate={actions.activateVariant}
            onReplay={() => actions.replayFrom(index)}
            onCreateCharacter={m.speaker === "Grimoire" && m.response_status === "complete"
              ? () => actions.createCharacter(m.response_id!) : undefined} />
        )}
      </div>
    </div>
  );
});

/** The reroll box: where this reroll goes, and what it is told once there.
 *
 *  An overlay, so it registers as one (`modal`): while it is up the scene's
 *  bindings are held off and Escape is its own, from any control in it or
 *  from nowhere at all. The model list is radios, which are not typing
 *  targets, so without the hold a bare ← or → on one answered the scene's
 *  variant swipe -- activating another variant behind the box, or reopening
 *  it and dropping the route being chosen -- and the dispatcher's
 *  `preventDefault` took the radios' own arrow travel with it. A dialog the
 *  picker opens (a model's Test…) is a modal that registers later, so it is
 *  on top, and its Escape closes only it.
 *
 *  Enter stays on the element, where it is a commit: from the guidance box,
 *  or from a model row. The picker keeps the Enter its own controls answer (a
 *  select, a button, the typed-id box) by stopping it before it gets here. */
function RerollPopover({ cid, pop, rolling, actions }: {
  cid: string;
  pop: { prompt: string; route: RerollRoute };
  rolling: boolean;
  actions: TranscriptActions;
}) {
  useHotkeys(
    [{ keys: "escape", label: "Close the reroll box", group: "THIS PANEL", whileTyping: true,
       run: () => actions.setRerollPrompt(null) }],
    { modal: true },
  );
  return (
    // Keydown is only Enter's commit, filtered from what its controls send;
    // they are the interactive elements.
    // eslint-disable-next-line jsx-a11y/no-static-element-interactions
    <span className="reroll-pop"
          onKeyDown={(e) => {
            // React bubbles a portal's events through the tree that rendered
            // it, so a key in the test dialog -- portalled to the body --
            // arrives here as well. It is the dialog's, not the popover's.
            if (!e.currentTarget.contains(e.target as Node)) return;
            // `preventDefault` is what tells the shortcut dispatcher this
            // keystroke is spoken for: ⌘⏎ typed here means "reroll", not
            // "send", and without it both fired (PR #400 review).
            if (e.key === "Enter") { e.preventDefault(); actions.reroll(); }
          }}>
      {/* Above the guidance, not beside it: this is where the reroll goes,
          and the hint is what it says once it gets there. Untouched, it is
          Default: the campaign's own scene route, with no override. */}
      <RerollRoutePicker cid={cid} value={pop.route} onChange={actions.setRerollRoute} />
      <span className="reroll-guide">
        <input
          autoFocus
          placeholder="Guide the reroll (optional)…"
          aria-label="Reroll guidance"
          value={pop.prompt}
          onChange={(e) => actions.setRerollPrompt(e.target.value)}
        />
        <button className="btn-chrome" onClick={actions.reroll} disabled={rolling}>Reroll ▸</button>
      </span>
    </span>
  );
}
