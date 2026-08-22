import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Link, useMatch, useNavigate, useParams } from "react-router-dom";
import Markdown from "react-markdown";
import { hideArtHandles } from "../artHandles";
import remarkGfm from "remark-gfm";
import {
  api, ApiError, invalidateConfigCache, type Actor, type SceneMeta,
  type Message, type RosterEntry, type SceneAlternates,
  type SceneDatetime, type ProposalRecord, type SceneCheckActor,
  type ResponsePresetSummary, type ResponseOverride, type ResponseBundle,
  type Briefing, type Casefile, type Provenance, type SceneLocation, type SceneWeather,
  type CampaignBudget,
  type UsagePostBucket,
} from "../api/client";
import { isAbortError, newAttemptId, type ChatEvent } from "../api/stream";
import { useRunRegistry } from "../runs/RunRegistryProvider";
import { forkNotes } from "../components/forkNotes";
import { ErrorNote } from "../components/ErrorNote";
import { errorText, isProviderFailure } from "../api/errors";
import { configChanged } from "../appEvents";
import { LOCKED_WHILE_GENERATING } from "../components/sceneLock";
import { CastPanel } from "../components/CastPanel";
import { NewSceneChooser } from "../components/NewSceneChooser";
import { PostImagePicker, type PickerTarget } from "../components/PostImagePicker";
import { ChangesPanel } from "../components/ChangesPanel";
import { ReplayPanel } from "../components/ReplayPanel";
import { IncomingReview } from "../components/IncomingReview";
import { CalendarConfig } from "../components/CalendarConfig";
import { CampaignCover } from "../components/CampaignCover";
import { SceneInspector } from "../components/SceneInspector";
import { PostCost, money } from "../components/cost";
import MechanicsConfig from "../components/MechanicsConfig";
import { ResponsePresetPicker } from "../components/ResponsePresetPicker";
import RerollRoutePicker, {
  NO_REROLL_ROUTE, type RerollRoute,
} from "../components/RerollRoute";
import { initialsOf, Portrait } from "../components/Portrait";
import { RecordDrawer, type DrawerTarget } from "../components/RecordDrawer";
import { onConfigChanged } from "../appEvents";
import { usePublishSceneModel, usePublishShellContext } from "../components/ShellStatus";
import { RollProposal, type ResolveBody } from "../components/RollProposal";
import { PageShell } from "../components/PageShell";
import { useFocus } from "../components/focus";
import CastColumn from "../components/play/CastColumn";
import ReviewColumn from "../components/review/ReviewColumn";
import ReviewPanel from "../components/review/ReviewPanel";
import ReviewTranscript from "../components/review/ReviewTranscript";
import { useSceneReview } from "../components/review/useSceneReview";
import DossierColumn from "../components/play/DossierColumn";
import Conditions from "../components/play/Conditions";
import { usePaletteSource, type PaletteItem } from "../components/palette";
import { useHotkeys } from "../shortcuts/useHotkeys";
import { commentPlugin } from "../markdown/commentPlugin";
import { quotePlugin } from "../markdown/quotePlugin";

// Marks a manual dice-roll transcript line's speaker (backend: scenes.ROLL_SPEAKER).
// Prefixed with an invisible separator so it can never collide with a real
// typed speaker label or cast name — a character actually named "Roll" is
// unaffected.
const ROLL_SPEAKER = "⁣Roll";
// Marks a scene transition line — join/leave, location change, time advance
// (backend: scenes.TRANSITION_SPEAKER); same invisible-separator prefix as
// ROLL_SPEAKER. Purely internal metadata: drift measurement uses it as a turn
// separator and reroll steps over it, but it is NEVER displayed — a transition
// renders as the unlabelled narration it was before the tag existed.
const TRANSITION_SPEAKER = "⁣Scene";
// The window token of a transcript that has been edited optimistically. Real
// tokens come from `++windowTokenRef` and so start at 1; this identifies posts
// that no fetch produced, so every readiness gate comparing against a fetch's
// token stays correctly closed while the ownership beside it stays readable.
// Not -1, which `NO_ALTERNATES.window` already uses — matching there would be a
// coincidence rather than a meaning.
const OPTIMISTIC_TOKEN = -2;

// A scene with no reroll alternates — the initial state and what every failed
// fetch falls back to. `cid`/`sid` are the campaign and scene the set was
// fetched FOR: switching scenes does not cancel an in-flight fetch, so scene
// A's response can land after scene B's and would otherwise show A's indices
// against B — where clicking the control swaps by index and would hit the
// wrong variant.
//
// The campaign half is not redundant. React Router reuses this component
// between /campaigns/A and /campaigns/B, so during a campaign switch `cid` is
// already B while `activeId` and the loaded set are still A's — a sid-only
// gate compares two stale values, passes, and offers A's set against B.
//
// `window` is the third part of the key and the one campaign+scene cannot
// supply: a REFRESH of the scene already on screen (every reroll, roll and
// edit ends in one) leaves both halves equal, so they cannot tell a set
// fetched for the transcript being rendered from one fetched for the transcript
// still in flight. Reroll optimistically drops the trailing run, so that window
// is exactly when the messages on screen end at the user post — and a set
// matched against them hangs the picker off that post.
type ScopedAlternates = SceneAlternates & {
  cid: string | null; sid: string | null; window: number;
};
const NO_ALTERNATES: ScopedAlternates = {
  cid: null, sid: null, window: -1, active: null, alternates: [],
};

// Scene history loads a page at a time from the tail (#94). A scene that has
// run for months is hundreds of posts, and mounting all of them costs a
// visible pause on every scene switch; the reader almost always wants the
// recent end. Older posts arrive by scrolling up (or the button that scroll
// falls back to), which prepends the next page and holds the viewport still.
const PAGE_SIZE = 60;
// How many extra rounds Stop will ask the server before giving the composer
// back. The cancel route waits for the run itself, but that wait is bounded, so
// a provider slow to unwind can answer while still `running`; each round is
// another full server-side wait, so a handful covers minutes without the client
// having to know what the server's timeout is.
const STOP_POLLS = 4;
// Scrolled this close to the top, "show me more" is what the reader means.
const NEAR_TOP_PX = 120;
// ...and this close to the bottom, they are following the reply as it streams,
// so new content should keep scrolling itself into view. Farther up than this
// they are reading something specific and must not be yanked away from it.
const NEAR_BOTTOM_PX = 80;

// Resolves when the signal aborts, for racing a wait that has no cancellation
// of its own against Stop. `once`, so a long-lived controller does not
// accumulate listeners across the turns that share it.
function settleOn<T>(signal: AbortSignal, value: T): Promise<T> {
  return new Promise<T>((resolve) => {
    if (signal.aborted) resolve(value);
    else signal.addEventListener("abort", () => resolve(value), { once: true });
  });
}

// Where the scene on screen lives (#87). The scene is IN the URL rather than in
// component state, so a reload, a bookmark or a shared link comes back to the
// scene the reader was in — rather than to whichever scene was edited last,
// which is all `list_scenes`' updated-descending order can offer.
function sceneUrl(cid: string, sid: string): string {
  return `/campaigns/${encodeURIComponent(cid)}/scenes/${encodeURIComponent(sid)}`;
}

// The scene rail lists scenes most-recently-edited first, but the displayed
// number must reflect story order — the id's own leading number (its
// filename stem is "<NNN>--<date>--<slug>"), never the list position, which
// drifts out of story order as soon as any earlier scene is re-edited.
function sceneNumber(id: string, fallback: number): number {
  const m = /^(\d+)--/.exec(id);
  return m ? parseInt(m[1], 10) : fallback;
}

// Where a resolved response field came from, for the composer chip. Mirrors
// ResponsePresetPicker's scopeLabel, shortened for a chip's worth of space.
function responseScopeLabel(scope: string | undefined): string {
  switch (scope) {
    case "turn": return "this turn";
    case "scene": return "this scene";
    case "campaign": return "this campaign";
    case "global": return "global";
    case "default": return "built-in default";
    default: return "inherited";
  }
}


// A superseded or narrated proposal is finished. `declined` is NOT, and used to
// be filtered out with them: the backend keeps re-streaming a declined record's
// continuation on request (mechanics.post_roll_proposal), so a decline whose
// narration never landed is recoverable — but only if something renders it.
// Dropping it stranded the record with no way back, which Stop made easy to
// reach and an upstream failure could always reach; RollProposal now offers it
// the same Continue narration it offers a stopped accept.
function liveProposal(record: ProposalRecord | null): ProposalRecord | null {
  return record && record.status !== "superseded" && record.status !== "narrated"
    ? record : null;
}

// All three sorts put the most-recent thing first, matching "updated" (the
// API's own order, most-recently-edited first — the existing default):
// "date" is latest in-story date first, "order" is the highest scene number
// first. Scenes with no in-story date always sort after every dated scene.

// Memoized so typing in the input bar (which re-renders CampaignView on every
// keystroke) doesn't re-parse the markdown of every unchanged message.
const RenderedMarkdown = memo(function RenderedMarkdown({ content }: { content: string }) {
  // commentPlugin runs first, though nothing depends on it doing so: quotePlugin
  // scans `text` nodes only and steps over `raw`/`comment` ones without reading
  // their values, so a quote mark inside a note could never have opened a run
  // either way. The order is for reading, not for correctness.
  return (
    <Markdown remarkPlugins={[remarkGfm]} rehypePlugins={[commentPlugin, quotePlugin]}>{content}</Markdown>
  );
});

export default function CampaignView({ ready }: { ready: boolean }) {
  const { cid = "" } = useParams();
  // Focus mode: the scene bar and the scene head go with the app header and the
  // context column, leaving the transcript and the composer. Together they were
  // the ~300px above the first line of prose on a phone — the scene bar worst of
  // all, because eleven controls at a 44px touch target wrap into four rows at
  // 375px. The review bar is NOT included: a review is not a scene being read,
  // and it carries the only rename control that can reach an absorbing scene.
  const { focus } = useFocus();
  // The scene segment is a CHILD route (App.tsx), so `useParams` here — which
  // only sees params matched down to this element's own route — never carries
  // it. `useMatch` reads it off the full location instead, and does it without
  // a second CampaignView instance for the router to swap between.
  const sid = useMatch("/campaigns/:cid/scenes/:sid")?.params.sid;
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [worldName, setWorldName] = useState("");
  const [dt, setDt] = useState<SceneDatetime | null>(null);
  const [showCalendar, setShowCalendar] = useState(false);
  const [showMechanics, setShowMechanics] = useState(false);
  const [showStyle, setShowStyle] = useState(false);
  const [showCover, setShowCover] = useState(false);
  const [scenes, setScenes] = useState<SceneMeta[]>([]);
  // Which campaign `scenes` describes. The router reuses this component across
  // /campaigns/A → /campaigns/B, so between the switch and B's list landing
  // `scenes` still holds A's rows — and the resolver below would read B's sid
  // as missing from them and redirect the reader to one of A's scenes under
  // B's id. Every wholesale replacement goes through `installScenes` so the
  // two cannot drift; the one plain `setScenes` left is a rename re-keying the
  // list it already has, which cannot change whose list it is.
  const [sceneListCid, setSceneListCid] = useState<string | null>(null);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  // Which transcript is actually ON SCREEN, campaign and scene. `activeId` is
  // what the user picked; this is what has landed. They differ while a select is
  // in flight, and anything keyed to the transcript — the swipe control above
  // all — has to follow this one, or it renders against the previous scene's
  // posts. Both halves, for the reason the alternate state carries both: scene
  // ids repeat across campaigns, so A→B with a colliding id reads as a refresh
  // and a sid-only key never notices the transcript is still A's.
  const [loaded, setLoaded] = useState<{ cid: string; sid: string; token: number } | null>(null);

  // "The transcript on screen is the active scene's own."
  //
  // They diverge for exactly one read: `runStream`'s finally refreshes the
  // TURN's scene, which is allowed to install over the scene the reader has
  // since opened (the pull-back is load-bearing — a failed turn's recovered
  // prompt has to appear against the scene it was written for). The resolver
  // then corrects `activeId` back to the URL's scene, and until that read lands
  // the previous scene's messages are still rendered.
  //
  // EVERY control that derives an index, an affordance or an optimistic removal
  // from the RENDERED messages and then writes to `activeId` has to read this,
  // or it shows the reader one scene's transcript and mutates another's. Named
  // once because doing it per control is how the second one got missed: the
  // edit form was gated and reroll was not, so Regenerate replaced a reply of
  // the active scene that the reader had never been shown (codex review, P1).
  //
  // Null `loaded` is UNKNOWN ownership, not safe ownership. This used to read
  // `!loaded ||`, on the reasoning that the only thing that nulls it is an
  // optimistic edit, which extends the active scene's own transcript. True at
  // the moment of the edit and false immediately after: nothing re-establishes
  // it when the reader navigates, so a send or reroll followed by opening
  // another scene left the previous scene's posts rendered, `activeId` moved,
  // and this predicate answering "active" — which is exactly the state it
  // exists to forbid (codex review, P1). `showOptimistically` now keeps the
  // ownership and drops only the fetch identity, so the honest answer is
  // available and the carve-out is gone.
  //
  // The other two nulls — a rename that outdated the set, a swap whose re-read
  // failed — mean "nobody knows what this transcript is" and are correctly
  // false here too.
  const transcriptIsActive =
    !!loaded && loaded.cid === cid && loaded.sid === activeId;

  // Every LOCAL edit to the transcript goes through here, and each one retires
  // `loaded`'s FETCH IDENTITY while keeping its ownership. Matching tokens only
  // prove a set and a *fetch* describe the same scene — an optimistic edit
  // changes the posts under both without touching either, so a
  // stale-but-consistent pair still passes. Reroll is the case that bites: it
  // removes the displayed reply, and the set keyed to that reply stays valid,
  // so the picker drops onto the user post above it.
  //
  // It used to null the whole record, which threw away the answer to a
  // different question — WHICH scene these posts are — and `transcriptIsActive`
  // had no choice but to guess.
  //
  // The owner is CARRIED, never re-derived. Stamping the active scene's id
  // looks equivalent and is not: Send stays enabled while a freshly selected
  // scene is still loading (see `runStream`'s baseline read), so the reader can
  // select B and send while B's GET is in flight — `activeIdRef` already says B
  // while the messages being extended are still A's. Deriving the owner there
  // labels A's posts as B's, which is worse than not knowing, because the guard
  // then believes them and offers edits whose indices address A against a file
  // that is B (codex review, P1).
  //
  // Null stays null for the same reason: an unowned transcript is one nothing
  // may act on, and inventing an owner is exactly the mistake above.
  //
  // Loading and paging deliberately do NOT come through here: they are what
  // `loaded` describes, and a prepend extends that transcript rather than
  // replacing it.
  function showOptimistically(edit: (m: Message[]) => Message[]) {
    setMessages(edit);
    setLoaded((cur) => (cur ? { ...cur, token: OPTIMISTIC_TOKEN } : null));
  }
  // `messages` is a WINDOW onto the transcript's tail, not the whole of it.
  // `firstIndex` is the absolute index of `messages[0]`, so every index this
  // component hands the API (edit, reroll) stays the index the backend means,
  // and `firstIndex > 0` is exactly "there are older posts to load".
  const [firstIndex, setFirstIndex] = useState(0);
  // The guard is the REF; the state only drives the button's label. Scroll
  // fires in bursts and setState is async, so two events in one tick would
  // both read `loadingOlder === false` and prepend the same page twice.
  const [loadingOlder, setLoadingOlder] = useState(false);
  const loadingOlderRef = useRef(false);
  // How much history is on screen. A refresh of the *same* scene (after a
  // reply, an edit, a roll) re-reads this much rather than snapping back to
  // one page, so pages the reader already scrolled up to survive the re-fetch.
  const windowSizeRef = useRef(PAGE_SIZE);
  // Whether a player turn exists ANYWHERE in the transcript (the server
  // answers for the whole of it; null until a windowed read says). Reroll
  // hangs off this: an offscreen scene never stores a user post, so no amount
  // of unloaded history above the window implies one exists.
  const [hasUserPost, setHasUserPost] = useState<boolean | null>(null);
  // Every scene load takes the next token; a page that resolves after the
  // window it was asked for has moved on is DROPPED. Without it an older-page
  // request for scene A, still in flight when the reader switches to scene B,
  // prepends A's posts onto B and installs A's offset — after which an edit
  // sends B's id with an A-derived index and overwrites an unrelated post.
  const windowTokenRef = useRef(0);
  const [streaming, setStreaming] = useState("");
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  // Which scene a turn is streaming into, or null. Distinct from `activeId`,
  // which is the scene being *looked at* — a player can start a turn on A and
  // navigate to B while it runs, and the write still lands in A. Anything that
  // would move A's file has to stay locked for as long as that is true, so the
  // lock follows the stream rather than the view (review, #95). State, not a
  // ref, because the rail renders it.
  const [streamingId, setStreamingId] = useState<string | null>(null);
  // Which turn owns `streamingId`. The lock outlives `busy` — it is held across
  // the post-cancel flush poll — so a newer turn can claim it while an older
  // one is still unwinding, and only the current owner may release it.
  const streamTokenRef = useRef(0);
  // How many scene-renaming requests are in flight. A rename is a PUT that
  // moves the scene file, and until it answers nobody knows which id is
  // current — so a turn started inside that window can be handed the old one
  // and have its write land nowhere. `streamingId` closes the other direction
  // (rename blocked during a turn); this closes turn-blocked-during-a-rename
  // (review, #95). A counter, not a flag: the rail and the two date controls
  // can overlap, and the last one to finish must not unblock the others.
  //
  // Still a list of surfaces, which is the same shape of fragility the lock
  // has — but the guard is now at the one place turns start, so a surface that
  // forgets to report is a narrower miss than one that forgets to lock.
  const [renamesInFlight, setRenamesInFlight] = useState(0);
  // "A turn can still write to the scene on screen." NOT `busy`, which clears
  // the instant the socket dies while the shielded abort write lands seconds
  // later — the whole point of `streamingId`.
  //
  // Named and derived once because review has now found four surfaces sitting
  // on the wrong one of these, one at a time: rename, the two date controls,
  // End scene, and the dice roll. Every control that mutates *this* scene's
  // transcript without going through `runStream` reads this, so the next one
  // is a missing `sceneLocked` rather than a fifth rediscovery of the rule.
  //
  // It covers a run this component never started, too, and that is what makes
  // it right once a turn outlives its request: `attachToRun` sets
  // `streamingId` for an ADOPTED run exactly as `runStream` does for one it
  // began, so a turn recovered after a reload -- or one still generating when
  // the tab came back -- locks the scene the same way. The server refuses
  // these mutations either way (`scene_busy`, keyed on the `turn`/`review`
  // exclusion key); this is the affordance that stops the player being told no.
  // Declared just below `review`, because a live REVIEW holds the scene on
  // exactly the same terms -- see there.
  const markRenaming = useCallback(
    (active: boolean) => setRenamesInFlight((n) => n + (active ? 1 : -1)), []);
  // Held for the life of one turn so Cancel can reach it. A ref, not state:
  // the controller is not rendered, and rebuilding the component tree on every
  // send just to store it would remount the transcript mid-stream.
  const abortRef = useRef<AbortController | null>(null);
  // How Stop reaches the turn in flight. Closing the connection is no longer
  // the cancel, so the run has to be addressable -- and addressable BEFORE its
  // leading frame arrives, because the window where the server has accepted and
  // detached the work but no frame has reached the browser is exactly the
  // window a dying connection lands in. `attempt` is minted client-side and
  // sent with the request, so it names the turn from the first byte; `id` is
  // filled in when the leading frame confirms it, and Stop falls back to
  // discovery by attempt when it has not.
  const runRef = useRef<
    { cid: string; sid: string; attempt: string; id: string | null } | null>(null);
  // The half of that which has to outlive this component -- see
  // `RunRegistryProvider`. `runRef` is the live turn's handle for Stop; this is
  // the record that a send happened at all, and it is the only thing left after
  // a navigation or a suspended tab.
  const registry = useRunRegistry();
  // The in-flight `cancelRun`, so the stream's teardown can wait for it before
  // releasing the scene. The route answers only once the run is really
  // terminal, which is the authoritative "the partial is written and the
  // exclusion key is free" signal -- without waiting for it, Stop re-enables
  // Send while the old run still holds the scene and the next send is refused.
  const cancelRef = useRef<Promise<void> | null>(null);
  // The flush poll can outlive the view by up to its whole budget, and it calls
  // setState on every tick. Nothing else here needs an unmount guard — a stream
  // deliberately runs to completion after a navigation, and its refresh is a
  // React warning at worst — but a loop that keeps refetching a scene nobody is
  // looking at is waste with no upside.
  const mountedRef = useRef(true);
  // The transcript length the server last reported. A turn that failed without
  // a response compares against this to find out whether its post landed after
  // all — `beforeResponse` proves only that no response arrived, not that the
  // request never did (review caught the difference). Counts, not content:
  // `post_chat` expands macros before storing, so what came back never has to
  // equal what was typed.
  const totalRef = useRef(0);
  // Which scene `totalRef` is a count of. It is written by whichever read
  // landed last, which is not necessarily the scene a turn is about to run on.
  const totalSceneRef = useRef<string | null>(null);
  // Orders writes to the chip, because the proposal is read from two places
  // that can answer out of order. `selectScene` fires a read and does not await
  // it; the post-cancel `settleProposal` fires a later one deliberately. Making
  // the fresh read bypass the shared promise (#95) was not enough on its own —
  // review caught that it still left the older read free to resolve afterwards
  // and put its pre-flush `null` back over the record that had just been
  // settled, with the poll already finished. So: every write bumps this, and a
  // read may only apply while its own bump is still the newest.
  const proposalSeqRef = useRef(0);
  // The banner offers Retry, which *generates*. That is the right recovery for
  // a turn that failed, and the wrong one for anything else: retrying a failed
  // alternate swap by generating appends a consecutive reply, which moves the
  // slot and hides the very set the user was trying to cycle. So the failure
  // carries whether a generation is what it wants, rather than the banner
  // assuming so.
  // `from` names the operation that raised it. The banner is global but the
  // reviews are not: an absorb review survives a scene switch, so a chat error
  // raised in scene B can be on screen while scene A's review is still open.
  // Without the tag, A's retry clearing "the previous attempt's error" would
  // take B's unrelated failure -- and its generate-a-reply Retry -- with it.
  // `err` is the rejection itself, kept alongside its text so the banner can
  // tell a model it could not reach from any other refusal (#210) -- the
  // `network` kind lives on the rejection and nowhere in the sentence. Only
  // `fail` has one to keep: half a dozen other raisers compose their own
  // sentence out of nothing that failed, and set `text` alone.
  const [error, setError] =
    useState<{ text: string; retryable: boolean; from?: string; err?: unknown } | null>(null);
  /** Whether the dot has been told this connection is failing, so that the
   *  next turn which works can tell it otherwise. Recovery is only visible if
   *  something announces it, and the shell has no other way to hear. */
  const sawProviderFailure = useRef(false);
  const fail = (e: any, retryable = true, from?: string) => {
    setError({ text: errorText(e), retryable, from, err: e });
    // A turn that failed is the freshest thing anyone knows about that
    // connection, and it arrives as an SSE frame — so it never passes through
    // the api client's rejection path, where the same signal is raised for
    // every other provider call (#146). Without this the status dot keeps
    // yesterday's verdict until the reader navigates.
    if (isProviderFailure(e)) {
      sawProviderFailure.current = true;
      invalidateConfigCache();
      configChanged();
    }
  };
  const [ctxKey, setCtxKey] = useState(0);
  // The campaign's budget, and the level the reader has already been told about
  // (#153). Held as a level rather than a boolean so dismissing the 80% warning
  // does not also swallow the one that says the budget is gone: crossing into
  // `over` is a different fact, and it gets said again.
  const [budget, setBudget] = useState<CampaignBudget | null>(null);
  const [budgetSeen, setBudgetSeen] = useState<string | null>(null);
  const [editing, setEditing] = useState<{ index: number; text: string } | null>(null);
  /** The post an image is being picked for (#376). Holds the index and who
   *  spoke it, because the picker is scoped by the speaker: an actor post
   *  offers that actor's art, a narrator post the campaign's own library. */
  const [picking, setPicking] = useState<{ index: number; target: PickerTarget } | null>(null);
  const [rerollPrompt, setRerollPrompt] = useState<string | null>(null); // null = popover closed
  // Which connection and model THIS reroll runs on (#77). One-shot, like the
  // guidance beside it and like `pendingResponse`: reset whenever the popover
  // opens, so a route chosen for one reroll cannot silently steer the next.
  const [rerollRoute, setRerollRoute] = useState<RerollRoute>(NO_REROLL_ROUTE);
  // Every variant of the generation reroll targets, refreshed by selectScene
  // (which every mutating path already funnels through). `active` is null when
  // the slot is empty — a reroll whose stream died — and picking a variant
  // then puts one back rather than swapping.
  const [alternates, setAlternates] = useState<ScopedAlternates>(NO_ALTERNATES);
  // The in-flight alternates fetch: its token identifies the latest request, and
  // its `sid` is re-keyed by a rename so a response still lands under the right
  // scene. Replaced wholesale per request, so identity comparison is the test.
  const altsReq = useRef({ token: 0, cid: "", sid: "" });
  // `activeId` for callers that must know what is selected NOW rather than what
  // was selected when they captured it — an awaited handler holds a stale one.
  // Every write goes through `setActive` so the two cannot drift: they are one
  // fact, and the rename/delete/first-send paths set it without going anywhere
  // near `selectScene`.
  const activeIdRef = useRef<string | null>(null);
  // Which campaign the active scene belongs to. Scene ids repeat freely between
  // campaigns, so "the sid in the URL is already the active one" is only true
  // if the active scene is also THIS campaign's — A/scenes/s1 → B/scenes/s1
  // otherwise reads as "already there" and B's transcript never loads.
  // Taken from the render that selected it, not `cidRef`: a handler awaited
  // across a campaign switch belongs to the campaign it started in, which is
  // the campaign whose scene it is selecting.
  const activeCidRef = useRef<string | null>(null);
  function setActive(id: string | null) {
    activeIdRef.current = id;
    activeCidRef.current = id === null ? null : cid;
    setActiveId(id);
  }
  // Same idea for the campaign: router reuses this component across
  // /campaigns/A → /campaigns/B, and scene ids repeat freely between
  // campaigns, so "still the same sid" is not "still the same scene". A
  // handler that captured A must compare against where the router is NOW,
  // which the render it was created in cannot tell it.
  const cidRef = useRef(cid);
  cidRef.current = cid;
  // null = closed; open holds the in-progress notation/label/error, plus
  // the popover's mode (dice notation vs. a module check) and check fields.
  const [rollForm, setRollForm] = useState<{
    mode: "dice" | "check"; notation: string; label: string; error: string | null;
    checkActor: string; checkId: string; difficulty: number | ""; modifier: number;
  } | null>(null);
  const [checkActors, setCheckActors] = useState<SceneCheckActor[]>([]);
  const checksFetched = useRef(false); // one getSceneChecks per popover session
  // The in-flight-store-mutation latch, shared by the dice roll, the check roll
  // and the alternate swap. Scoped to the scene that took it rather than being a
  // bare boolean: it was component-wide, so an operation belonging to a scene
  // the reader has left held every control in the scene they entered — Send,
  // Retry, reroll, edit, roll — until an unrelated request settled. Review found
  // that against the swap; the two roll paths had it already.
  //
  // The token is what makes releasing safe: a slow operation finishing after a
  // newer one took the latch must not clear the newer one's.
  const [rollingFor, setRollingFor] =
    useState<{ cid: string; sid: string; token: number } | null>(null);
  const rollTokenRef = useRef(0);
  const rolling = !!rollingFor && rollingFor.cid === cid && rollingFor.sid === activeId;

  function takeRollLatch(forSid: string) {
    const token = ++rollTokenRef.current;
    setRollingFor({ cid, sid: forSid, token });
    return () => setRollingFor((cur) => (cur?.token === token ? null : cur));
  }
  // a pending/resolved roll-proposal record surfaced by the model mid-stream
  // or rehydrated on scene select; RollProposal only renders pending/resolved.
  const [proposal, setProposal] = useState<ProposalRecord | null>(null);
  const [showRollSyntax, setShowRollSyntax] = useState(false);
  const [colorQuotes, setColorQuotes] = useState(false);
  const [labels, setLabels] = useState({ user: "You", assistant: "Grimoire" });
  const [cast, setCast] = useState<Actor[]>([]);
  const [roster, setRoster] = useState<RosterEntry[]>([]);
  /** The whole dossier feature. `null` is the cast grid; a ref is one actor's
   *  casefile in the column. Deliberately the only state the swap has —
   *  "which mode is the column in" is derived from this and never stored
   *  twice, so the two can never disagree. */
  const [selectedActor, setSelectedActor] = useState<{ kind: string; id: string } | null>(null);
  const [casefile, setCasefile] = useState<Casefile | null>(null);
  const [briefing, setBriefing] = useState<Briefing | null>(null);
  /** Why each stored line is there (#4a). Campaign-scoped and read once per
   *  absorb rather than per scene: it is a rolling log of citations for values
   *  already on screen, so a copy one absorb old is stale about a line the
   *  reader has not been shown yet either. `{}` is a normal state — a campaign
   *  absorbed before the store existed has no citations at all. */
  const [provenance, setProvenance] = useState<Provenance>({});
  /** The quote the reader is hovering a citation for, highlighted in the
   *  transcript. "" is nothing hovered. */
  const [citedQuote, setCitedQuote] = useState("");
  const [sceneLocation, setSceneLocation] = useState<SceneLocation | null>(null);
  const [weather, setWeather] = useState<SceneWeather | null>(null);
  const [drawer, setDrawer] = useState<DrawerTarget | null>(null);
  const [showChanges, setShowChanges] = useState(false);
  /** The campaign side of push/sync (#6). Its own toggle rather than a tab
   *  inside Changes: that panel is what this campaign's own play changed, and
   *  this is what the world changed underneath it — the same shape of question
   *  about a different author. */
  const [showIncoming, setShowIncoming] = useState(false);
  // The post a reader asked to replay FROM (#79) -- the one after the retcon,
  // since the retconned post itself stands. Held here rather than in the panel
  // because the transcript gutter is what sets it.
  const [replayAt, setReplayAt] = useState<number | null>(null);
  // Part of `ReplayPanel`'s key, so bumping it re-mounts the panel and makes it
  // re-read the replay session. Adopting a detached run is the case: it
  // refreshes the transcript, which is not what that panel reads. See
  // `attachToRun`.
  const [replayEpoch, setReplayEpoch] = useState(0);
  // The end-of-scene review, whole (#378): its state, the absorb and save that
  // fill and commit it, and the counts its column and panel read off it. A
  // review is a MODE of this page rather than a panel inside it -- the
  // transcript, the composer and every scene control step aside for it -- so
  // what stays here is only what genuinely straddles the two modes: the banner
  // it reports through, the scene it absorbs, and the fact that a saved review
  // has rewritten every file the play view's panels read.
  const review = useSceneReview({
    cid, activeId, rolling, fail,
    clearError: () => setError(null),
    dismissError: (from) => setError((e) => (e?.from === from ? null : e)),
    onSaved: () => setCtxKey((n) => n + 1),
  });

  // A live REVIEW holds the scene on exactly the same terms as a turn -- `turn`
  // and `review` share one exclusion key server-side -- so an absorb in flight
  // locks these controls too. It did not need saying before #396, because an
  // absorb could not outlive the request that asked for it and the reader was
  // sitting in front of it; detached, they can start one, walk away, and come
  // back to a scene the server is refusing writes for.
  //
  // Scoped to the scene the review belongs to. A review outlives a scene
  // switch, so an absorb still running for scene A must not lock scene B --
  // the server would allow a turn there, and the reader is entitled to play it.
  const sceneLocked = !!activeId
    && (activeId === streamingId
        || (review.absorbing && (review.absorbSid ?? activeId) === activeId));
  const { absorb } = review;

  const [chooserOpen, setChooserOpen] = useState(false);
  // Campaign-scoped, like `loaded`, `rollingFor` and the alternate set, and for
  // the reason all three carry a cid: scene ids repeat between campaigns. The
  // premise is recorded before the relist that follows it, so a reader who
  // switches campaigns during that await leaves it behind — and a sid-only
  // match then hands campaign A's generated premise to an empty scene of B's
  // that happens to wear the same id (codex review).
  const [seedPrompt, setSeedPrompt] =
    useState<{ cid: string; sid: string; prompt: string } | null>(null);
  // Response-length chip beside Send: the scene's own preset (its saved
  // setting, from the loaded scene's frontmatter) versus a one-shot pending
  // override the player just picked for the next reply only. `pendingResponse`
  // is never persisted — it rides the next chat/retry/regenerate call and is
  // cleared once a reply actually lands, but survives a failed stream so
  // retry/reroll still honour it (see runStream/send/retry/reroll below).
  const [responsePresets, setResponsePresets] = useState<ResponsePresetSummary[]>([]);
  const [sceneResponsePreset, setSceneResponsePreset] = useState("");
  // The server's resolved bundle for this scene — the ONLY source of truth for
  // what the next reply is actually budgeted at. The cascade (turn → scene →
  // campaign → global → built-in default) is deliberately not re-implemented
  // here; a campaign-scope preset is invisible to the scene's own frontmatter.
  const [sceneResponse, setSceneResponse] = useState<ResponseBundle | null>(null);
  const [pendingResponse, setPendingResponse] = useState<ResponseOverride | null>(null);
  // Whether this campaign resolves to a mechanics pack. `null` while unknown:
  // the dice button renders on `true` only, so a control never appears and then
  // vanishes a moment later once the read lands.
  const [moduleBound, setModuleBound] = useState<boolean | null>(null);
  const streamRef = useRef<HTMLDivElement>(null);
  /** The scene inspector, which used to be a permanently-open third column.
   *
   *  It is a panel now, opened from the composer's "What the model saw →".
   *  That is a promotion rather than a demotion: what it answers — what went
   *  into the last prompt, and what was dropped to fit — is a question about a
   *  turn, so it belongs beside the control that takes the next one. The
   *  continuity it also carried (who is here, where, when) does not live here
   *  any more; that is the context column, permanently visible, which is the
   *  whole point of the redesign.
   *
   *  Closed by default: an open panel over the transcript is the state the old
   *  third column was in permanently. */
  const [showInspector, setShowInspector] = useState(false);
  /** The scene being renamed in place, or null. The rail's `EditableRow` used
   *  to own this; the control moved to the scene's own heading with it. */
  const [renamingScene, setRenamingScene] = useState<{ id: string; title: string } | null>(null);
  // Read on every `cid` change and again whenever MechanicsConfig saves, so
  // binding or clearing a pack updates the input bar without a reload.
  //
  // TWO guards, because they stop different things (Codex review):
  //
  // - `moduleReq` drops a read that STARTED earlier and landed later, exactly
  //   as `lockReq`/`anchorReq` do.
  // - `liveCid` drops a read that started later but belongs to a campaign the
  //   reader has left. A token cannot catch that one: MechanicsConfig's save
  //   holds the `onChanged` it was handed, so a PUT issued in campaign A and
  //   settling after a move to B calls A's callback, which would start a
  //   *newer* read of A and commit A's answer over B's.
  //
  // `reset` separates the two callers. A campaign change genuinely knows
  // nothing yet, so it blanks to `null`; a refresh in place keeps the value it
  // has, or the dice button would blink out and back on every save -- and the
  // popover-closing effect below would read that blank as "unbound".
  const liveCid = useRef(cid);
  liveCid.current = cid;
  const moduleReq = useRef(0);
  function readModuleBound(reset = false) {
    const forCid = liveCid.current;
    const req = ++moduleReq.current;
    if (reset) setModuleBound(null);
    const settle = (v: boolean) => {
      if (moduleReq.current === req && liveCid.current === forCid) setModuleBound(v);
    };
    api.getCampaignModule(forCid)
      .then((m) => settle(m.resolved !== null))
      // A failed READ is not evidence of anything. On a campaign change there
      // is nothing better to fall back on, so it errs toward "no dice" -- the
      // same side it errs on while the read is out. On a refresh in place there
      // IS something better: the answer already on screen. Keeping it stops a
      // transient failure from retracting the dice button and discarding a
      // half-typed roll with it (Codex review round 2).
      .catch(() => { if (reset) settle(false); });
  }

  useEffect(() => {
    api.getCampaign(cid).then((c) => {
      setName(c.meta.name);
      setWorldName(c.meta.world_name ?? ""); // embedded: no second fetch
    });
    // Which scene to open is NOT decided here: the URL says, and the resolver
    // below is the one place that reads it.
    loadScenes().catch(() => {});
    api.getConfig().then((c) => {
      setColorQuotes(c.quote_color === "on");
      setLabels({ user: c.user_label || "You", assistant: c.assistant_label || "Grimoire" });
    }).catch(() => {});
    api.listResponsePresets().then(setResponsePresets).catch(() => setResponsePresets([]));
    readModuleBound(true);   // new campaign: nothing known about it yet
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cid]);

  // The campaign check lives HERE rather than at each call site, because every
  // caller reaches this after an await and the mistake is invisible from the
  // outside: `cid` is the render's, so a relist for the campaign the reader has
  // left installs its rows and labels them with the campaign that asked. If the
  // new campaign's own list already landed, `sceneListCid` then names a
  // campaign the resolver will never see again — and it returns early forever,
  // leaving the new campaign wearing the old one's rail with every rail click
  // and URL change dead until a reload (codex review).
  //
  // The mount read carried this guard alone and five mutation relists did not,
  // which is the shape of bug this file has now been bitten by repeatedly: a
  // rule spelled out at call sites is a rule the next call site forgets.
  function installScenes(list: SceneMeta[]) {
    if (cidRef.current !== cid) return;
    setScenes(list);
    setSceneListCid(cid);
  }

  // Every read of the scene list goes through here, and only the newest one
  // ISSUED may install — response order is not request order.
  //
  // Two mutations overlap easily: a rename's relist is still open when the
  // reader deletes another row, and nothing in the UI serializes them
  // (`renamesInFlight` blocks turns, not deletions). If the rename's older
  // answer lands last it puts the deleted row back. That used to be a stale
  // rail and nothing more; now the list decides which sid the URL may name, so
  // the restored row is a ghost the reader can click straight into a read that
  // 404s (codex review).
  //
  // Sequenced PER CAMPAIGN. A single global counter looked equivalent — the
  // reasoning being that a read for the campaign the reader left is always
  // older than the new campaign's own — and that reasoning is false: a
  // mutation in A can finish, and issue its relist, AFTER B's mount read was
  // already in flight. The global counter then hands A's later request the
  // newer number, B's answer is retired by it, and A's own answer is refused by
  // `installScenes` for being the wrong campaign. Nothing installs,
  // `sceneListCid` never becomes B, and the resolver is disabled until reload —
  // the exact stranding the guard exists to prevent, reintroduced by the fix
  // for it (codex review).
  //
  // Keyed by campaign, each campaign's reads order only among themselves, and
  // neither can retire the other's. One entry per campaign opened this session.
  const sceneListSeq = useRef(new Map<string, number>());
  // The newest read in flight per campaign, so a retired one can wait for it.
  const sceneListPending = useRef(new Map<string, Promise<void>>());

  // Resolves when a list at least as new as this call's has been INSTALLED —
  // not merely when this call's own response arrived.
  //
  // The difference is what a caller does next. `sceneCreated` and the first
  // send navigate to a row they have just created, and the resolver bounces a
  // sid the installed list does not have. The rail stays interactive while a
  // creation's relist is open, so a rename or delete can issue a newer read
  // that retires it — and if that newer read has not landed yet, the installed
  // list is still the one from before the scene existed. Returning here would
  // send the caller to navigate against it, and the brand-new id would be read
  // as stale and redirected away (codex review).
  //
  // Awaiting the newer read is not a self-deadlock: this branch is only
  // reached when a newer read exists, so the map holds that one, never this.
  function loadScenes(): Promise<void> {
    const seq = (sceneListSeq.current.get(cid) ?? 0) + 1;
    sceneListSeq.current.set(cid, seq);
    // A later read FOR THIS CAMPAIGN was issued: it, not this one, is the
    // answer, so the caller gets its completion rather than a false one.
    const retired = () => sceneListSeq.current.get(cid) !== seq;
    const followNewer = () => sceneListPending.current.get(cid);
    const p = (async () => {
      let list: SceneMeta[];
      try {
        // `listScenes` never coalesces, which is what makes the sequence number
        // above honest: a read cannot be handed a promise older than the moment
        // it was issued, so "newest number" and "newest request" agree.
        list = await api.listScenes(cid);
      } catch (err) {
        // Retirement applies to a FAILED read exactly as it does to a
        // successful one, and covering only the success half left the mirror
        // image of the bug it fixed: a superseded read that rejects would
        // reject its caller, so `sceneCreated` never navigated to the scene it
        // had created even though the newer read installed a list containing
        // it — and, being called from a dropped event-handler promise, it did
        // so as an unhandled rejection (codex review).
        //
        // A retired read's failure describes a request nobody is waiting on.
        // Only the campaign's newest read may speak for the list, in either
        // direction.
        if (retired()) return void await followNewer();
        throw err;
      }
      if (retired()) return void await followNewer();
      installScenes(list);
    })();
    sceneListPending.current.set(cid, p);
    return p;
  }

  // The two places that open a scene without an awaiting caller — the resolver
  // and a rail click on the row already open. `selectScene`'s rejection used to
  // go nowhere from here (an unhandled rejection, no banner), which left a
  // transcript that could not be read looking like a scene with no posts. Not
  // retryable: the banner's Retry generates, and there is nothing to generate.
  //
  // Scoped to the read that failed. A rejection carries no window token of its
  // own, so an unscoped handler raises its banner over whatever is on screen
  // when it finally lands — and the scene switch that retired the read has
  // already cleared the errors belonging to it, so the banner sticks, blaming
  // the current scene for a failure in one the reader has left (codex review).
  function readScene(id: string) {
    const p = selectScene(id);
    // Minted synchronously by the call above, before its first await.
    const token = windowTokenRef.current;
    p.catch((err: any) => {
      if (windowTokenRef.current !== token) return;   // a later select retired it
      setError({ text: `The scene could not be read: ` + (err?.detail ?? String(err)),
                 retryable: false });
    });
  }

  // Nothing is on screen: the campaign has no scenes, or the last one was just
  // deleted. Mirrors what `selectScene` installs, in reverse.
  function clearScene() {
    windowTokenRef.current += 1;   // drop any page still in flight for it
    setActive(null);
    setMessages([]);
    setLoaded(null);
    setFirstIndex(0);
    setHasUserPost(null);
  }

  // THE RESOLVER (#87). The URL names the scene; the rail's list says whether
  // it still exists; `activeIdRef` says what is already loaded. Reconciling
  // those three is this effect's whole job, and it is the ONLY place that
  // decides what to read or where to send the reader instead — so a rail
  // click, Back/Forward, a deep link, a delete and a rename all converge here
  // rather than each re-deciding for itself.
  //
  // Two rules make it terminate and keep it honest:
  //
  //   - It reads `activeIdRef`, not `activeId`. The paths that adopt an id
  //     WITHOUT navigating — a rename mints a new id for the scene already on
  //     screen, and the first send into an empty campaign creates one — set
  //     the ref and point the URL at it in the same batch. This then sees them
  //     agreeing and does nothing, which is what preserves the rename's
  //     `renamed` refresh (turn state kept) over a switch (turn state cleared).
  //   - Every redirect it issues is a `replace`. A scene the reader never
  //     chose — a fallback from a dead id, the scene a delete left behind — is
  //     not a place they went, and Back must not return them to a URL that
  //     resolves to nothing.
  //
  // `activeId` is in the deps because a divergence is exactly what has to be
  // repaired: a background refresh (runStream's finally, the flush poll) calls
  // `selectScene` with the scene the TURN belongs to, which is not always the
  // scene the reader is now looking at. The URL is authoritative, so the fix
  // is to read the URL's scene back — not to leave the two disagreeing.
  useEffect(() => {
    if (sceneListCid !== cid) return;      // the list is still the last campaign's
    if (sid && scenes.some((s) => s.id === sid)) {
      const loadedHere = sid === activeIdRef.current && activeCidRef.current === cid;
      if (!loadedHere) readScene(sid);
      return;
    }
    // No scene in the URL, or one that names nothing: scene ids are filenames
    // and a rename, a first date stamp or a repad all mint a new one, so a
    // bookmarked id goes stale on its own. Never fetch it — a dead id is a 404
    // and an error banner where a fallback belongs.
    if (scenes.length) {
      navigate(sceneUrl(cid, scenes[0].id), { replace: true });
    } else {
      if (sid) navigate(`/campaigns/${encodeURIComponent(cid)}`, { replace: true });
      if (activeIdRef.current) clearScene();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cid, sid, scenes, sceneListCid, activeId]);

  // A scene the reader chose: a real history entry, so Back returns to the one
  // they were reading. Everything else moves the URL with `replace`.
  function goToScene(id: string) {
    // Clicking the row already open is a re-read, which is what that click did
    // before the scene lived in the URL. Routed through the resolver it would
    // do nothing at all instead: the pathname does not change, so the effect
    // never re-runs.
    if (id === activeIdRef.current && activeCidRef.current === cid) {
      readScene(id);
      return;
    }
    navigate(sceneUrl(cid, id));
  }

  // Following the tail is the default and stays the default — but only while
  // the reader is AT the tail. Two things now move the stream and they must
  // not fight: new content at the bottom (scroll to it) and a page of older
  // posts prepended at the top (hold the reader where they were, which means
  // restoring the distance from the BOTTOM, since everything above them just
  // grew). `atBottomRef` is written by onScroll below and starts true, so an
  // untouched stream — and jsdom, where every scroll metric is 0 — follows.
  const atBottomRef = useRef(true);
  // distance from the bottom captured just before a prepend, consumed once
  const prependAnchorRef = useRef<number | null>(null);
  useLayoutEffect(() => {
    const el = streamRef.current;
    if (!el) return;
    const anchor = prependAnchorRef.current;
    if (anchor !== null) {
      prependAnchorRef.current = null;
      el.scrollTop = el.scrollHeight - anchor;
      return;
    }
    if (atBottomRef.current) el.scrollTo({ top: el.scrollHeight });
  }, [messages, streaming]);

  function onStreamScroll() {
    const el = streamRef.current;
    if (!el) return;
    atBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight <= NEAR_BOTTOM_PX;
    if (el.scrollTop <= NEAR_TOP_PX) loadOlder();
  }

  // unstamped user lines fall back to the sole player's name on their plate
  const playerName = useMemo(() => {
    const players = cast.filter((a) => a.role === "player");
    return players.length === 1 ? players[0].name : null;
  }, [cast]);

  // The compounding silent failure this closes: one slow-but-healthy extraction
  // can eat the whole shared budget, and the trailing steps then come back with
  // nothing to show — which reads exactly like a model that had nothing to
  // suggest. Named up front, before the reviewer reads the (short) edit list.
  // offscreen scenes take director notes instead of PC dialogue
  const activePcless = useMemo(
    () => scenes.find((s) => s.id === activeId)?.pcless ?? false,
    [scenes, activeId]);

  // A scene absorbed into the chronicle is finished: its summary is written and
  // its changes are applied, so anything appended now sits outside the record
  // that was taken of it. The composer goes entirely rather than being disabled
  // -- a greyed-out entry box still says "you could type here".
  const activeDone = useMemo(
    () => scenes.find((s) => s.id === activeId)?.done ?? false,
    [scenes, activeId]);

  // The response-length chip. A pending one-shot pick beats the scene's own
  // saved preset; with neither, the label comes from what the SERVER resolved
  // (api.getSceneResponse), because a preset set at campaign or global scope
  // names nothing the scene knows about. Claiming "Standard" in that case is
  // simply false whenever a broader scope supplies something else — so with no
  // preset to name we report the effective budget and where it came from.
  const responseChipPresetId = pendingResponse?.response_preset || sceneResponsePreset;
  const responseChipPending = !!pendingResponse?.response_preset;
  const presetName = (id: string) =>
    responsePresets.find((p) => p.id === id)?.name ?? id;
  const responseChipLabel = responseChipPresetId
    ? presetName(responseChipPresetId)
    : sceneResponse
      ? `${sceneResponse.effective.reply_words} words · ${
          responseScopeLabel(sceneResponse.provenance.reply_words?.scope)}`
      : "Inherited";

  function chooseResponseOverride(id: string) {
    setPendingResponse({ response_preset: id });
  }
  function clearResponseOverride() {
    setPendingResponse(null);
  }
  // The custom listbox this used to be needed its own open state, an
  // outside-click listener and an Escape handler. It is a native <select> now,
  // which the browser opens, closes, dismisses and keyboard-drives for free --
  // so all three are gone rather than reimplemented.
  // The transient 🎬 note, and the scene it is steering. Codex review (P2): a
  // bare string plus the GLOBAL `busy` rendered one scene's note inside
  // another's transcript, because a turn deliberately survives navigation --
  // the reader moves, the run does not. Campaign as well as scene: ids are
  // campaign-local, so `s1` names a different scene in every campaign and a
  // scene-only guard answers true for the wrong one.
  const [directorNote, setDirectorNote] =
    useState<{ cid: string; sid: string; text: string } | null>(null);

  // #83: the composer's two modes. Speak writes a player post; Direct writes a
  // director note — sent to steer one generation, never stored. Both have
  // always existed; only Direct had no way to say so, which left it inferred
  // (from an offscreen scene, or from an empty box) and unlabelled.
  //
  // Sticky across a scene switch, ON PURPOSE. The composer is one shared box
  // whose text deliberately survives the move, and a mode that reset under
  // surviving text would post as the player's own words something written as
  // direction — the exact confusion the control exists to end.
  const [directMode, setDirectMode] = useState(false);
  // An offscreen scene has no other kind of turn, so it forces the mode rather
  // than merely defaulting it: the toggle is shown locked, not hidden, because
  // "there is no PC here to speak as" is the fact worth teaching.
  const directing = activePcless || directMode;
  // ...and the force is STAMPED, not merely applied for as long as the reader
  // stays. `activePcless` is the scene's property; `directMode` is the
  // composer's, and the composer is the thing the words are in. Without this,
  // a note typed in an offscreen scene and carried into a normal one by the
  // shared box arrived staged as a player post — the mode having quietly
  // reverted while the text it labelled did not, which is the sticky rule
  // above broken in the one case that writes it into the transcript.
  // ...and only while there are WORDS for it to belong to (Codex review). The
  // stamp exists because the composer's text survives a scene switch and its
  // mode has to survive with it — so with an empty box it has nothing to
  // protect, and stamping anyway left an ordinary scene silently in Direct
  // after a visit to an offscreen one, spending the next line typed as a note
  // instead of posting it. Keyed on `input` rather than on leaving, because
  // leaving is when `activePcless` goes FALSE and this would no longer run.
  useEffect(() => {
    if (activePcless && input.trim()) setDirectMode(true);
  }, [activePcless, input]);

  // Prompts recovered from a turn that stored nothing, held under the scene
  // they were written for until that scene is on screen.
  //
  // The composer is one shared box that survives a scene switch, so writing a
  // recovered prompt into it straight away puts scene A's words in front of a
  // player looking at scene B — and the next Send posts them to B. Review
  // caught the same shape once before, in the reroll Retry that followed the
  // player to another scene; this is the other half of it, and the one that
  // moves text rather than an action.
  //
  // Parked rather than dropped, because dropping is the whole thing this
  // machinery exists to prevent: the prompt is nowhere else. A map, not one
  // slot, so a second scene failing does not evict the first — both are the
  // player's and neither can be retyped from anywhere.
  // Codex review (round 2, P2): the KIND travels with the words, not just the
  // words. The mode buttons stay live while a turn runs — the composer is
  // deliberately editable — so a player can be in Speak by the time a Direct
  // turn fails, and text handed back into a Speak composer is posted as
  // dialogue by the next Send. That is the "never posted" contract broken by
  // the recovery meant to protect it.
  // Keyed by CAMPAIGN AND SCENE (Codex review, round 4). Scene ids are
  // campaign-local — `s1` names a different scene in every campaign — and this
  // component is reused across campaign routes, so a scene-only key both
  // collides between campaigns and hands A's words to B's composer. The
  // durable registry has always been campaign-keyed; this half had not caught
  // up, and since #83 getting it wrong also flips the wrong composer's mode.
  const parkedPrompts = useRef<Map<string, { text: string; director: boolean }>>(new Map());
  const parkKey = (c: string, sid: string) => `${c}\u0000${sid}`;
  // Mirrors of the composer's two live facts, for the same reason
  // `activeIdRef` exists: `recoverPrompt` runs from a callback that outlives
  // the render it started in, and a captured `input`/`directing` would answer
  // for the moment the turn began rather than the moment it failed.
  const inputRef = useRef("");
  const directingRef = useRef(false);
  inputRef.current = input;
  directingRef.current = directing;
  // Something recovered for THIS scene that the box is not free to take yet.
  // Read off the ref during render rather than mirrored into state: every
  // trigger that can change the answer — parking, switching scene, typing,
  // flipping the mode — is already a re-render.
  const heldForScene = activeId
    ? parkedPrompts.current.get(parkKey(cid, activeId)) : undefined;
  // EITHER kind, not just the note (Codex review). The hold rule is symmetric —
  // a rolled-back post waits behind a note being drafted exactly as a note
  // waits behind dialogue — so a notice that only fired for notes left the
  // player's own words waiting in a place nothing on screen named, which is
  // the whole failure the notice exists to prevent.
  const heldKind: "note" | "post" | null =
    heldForScene && !!input.trim() && heldForScene.director !== directing
      ? (heldForScene.director ? "note" : "post")
      : null;
  const [parkedTick, setParkedTick] = useState(0);

  // Appending, not replacing: the composer stays editable while a turn runs,
  // so the player may already be typing the next thing. Failed prompt first,
  // since it was typed first, joined visibly so it reads as two things to edit
  // rather than one. Shared by both delivery paths so they cannot drift.
  const giveBackPrompt = useCallback((text: string) => {
    setInput((cur) => (cur.trim() ? `${text}\n\n${cur}` : text));
  }, []);

  // Hand back anything parked for the scene now on screen. Runs on selection
  // as well as on a fresh park, so a prompt recovered while the player was
  // elsewhere arrives the moment they come back rather than on the next
  // failure.
  useEffect(() => {
    if (!activeId) return;
    const held = parkedPrompts.current.get(parkKey(cid, activeId));
    if (held === undefined) return;
    // Same rule as `recoverPrompt`: a draft of the other kind still owns the
    // box, so the held text waits rather than merging into it. `input` is a
    // dependency for exactly this — clearing or sending the draft is what
    // releases it, and that is the moment it comes back.
    if (input.trim() && held.director !== directing) return;
    parkedPrompts.current.delete(parkKey(cid, activeId));
    giveBackPrompt(held.text);
    // Restored HERE rather than when the note was parked: the player may have
    // been writing something of their own in another scene in the meantime,
    // and flipping the shared composer's mode under that draft would mis-stage
    // it. The mode arrives with the words it belongs to.
    setDirectMode(held.director);   // both directions, as above
  }, [cid, activeId, parkedTick, giveBackPrompt, input, directing]);

  // Where a recovered prompt goes: the composer if the player is still on the
  // scene it belongs to, the parking map otherwise. `activeIdRef`, not
  // `activeId` — this runs from a callback that outlives the render it started
  // in, and a captured `activeId` would be the scene the turn began on, which
  // is exactly the stale answer this exists to avoid.
  const recoverPrompt = useCallback((pcid: string, sid: string, text: string,
                                     director = false) => {
    // Merged only into a draft of the SAME kind (Codex review, round 3).
    // `giveBackPrompt` prepends, and one composer carries one mode — so
    // folding a recovered note into dialogue typed since would give the
    // combined text a single mode that is wrong for half of it: as Direct the
    // dialogue is spent as an ephemeral note and never posted, as Speak the
    // note is posted as dialogue. Neither is acceptable, and discarding either
    // text is worse than both. So the odd one out waits: it stays parked until
    // the box is free, and `heldNote` says so on screen meanwhile.
    if (activeCidRef.current === pcid && activeIdRef.current === sid
        && (!inputRef.current.trim() || director === directingRef.current)) {
      giveBackPrompt(text);
      // BOTH directions (Codex review). One-sided, this restored dialogue into
      // a composer left in Direct, and the next send spent it as an ephemeral
      // note instead of posting it — the round-two bug with its mirror still
      // in place. The recovered kind decides the mode, whichever kind it is.
      setDirectMode(director);
      return;
    }
    parkedPrompts.current.set(parkKey(pcid, sid), { text, director });
    setParkedTick((n) => n + 1);
  }, [giveBackPrompt]);
  // Words the registry is still holding for a resolved-but-undurable send, put
  // back where the player can see them. This is what makes the durable copy
  // worth having: after a reload or an Android renderer restart, the component
  // state that `recoverPrompt` wrote into is gone, and this is the pass that
  // reads them out of storage again.
  //
  // Once per (cid, sid): `giveBackPrompt` APPENDS, so running twice for one
  // scene would hand the player their words back doubled.
  const reparked = useRef(new Set<string>());
  useEffect(() => {
    if (!activeId) return;
    const once = `${cid}\u0000${activeId}`;
    if (reparked.current.has(once)) return;
    const held = registry.parked(cid, activeId);
    if (held === undefined) return;
    reparked.current.add(once);
    recoverPrompt(cid, activeId, held);
  }, [cid, activeId, registry, recoverPrompt]);

  // Set on the way in as well as cleared on the way out. StrictMode runs the
  // setup/cleanup/setup cycle on mount in development, so a cleanup-only effect
  // leaves the flag false for the whole life of the view — and `owns()` below
  // reads it, which would have every post-cancel flush poll bow out before its
  // first look and leave a flushed partial invisible until the next refresh
  // (review, #95). Dev-only, but dev is where cancelling gets exercised.
  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  // A write the caller knows the answer to — a live SSE proposal, or a clear
  // the player's own action implies. Retires every read still in flight, since
  // all of them were issued against a world older than this.
  function setProposalNow(record: ProposalRecord | null) {
    proposalSeqRef.current += 1;
    setProposal(record);
  }

  // A write from a read: `claim` is what `claimProposalRead` handed out when the
  // request went out, and it applies only if nothing has been written or read
  // since. Last *issued* wins rather than last resolved, which is the whole
  // point — the slow answer is by definition the one asked earliest.
  function claimProposalRead(): number {
    proposalSeqRef.current += 1;
    return proposalSeqRef.current;
  }

  function applyProposalRead(claim: number, record: ProposalRecord | null) {
    if (claim === proposalSeqRef.current) setProposal(liveProposal(record));
  }

  // `stillWanted`, when given, is re-asked immediately before any state is
  // applied. Only background refreshes need it — the post-cancel flush poll,
  // which can be running while the player starts a new turn or opens another
  // scene. Returns the transcript's total length, or -1 if it bowed out — to
  // this predicate or to a later select retiring its page.
  // Only the LATEST request may write the alternate state — a stale success and
  // a stale rejection are both able to clobber the current scene's set, and the
  // rejection path is the one a scoped success cannot guard. Minting a fresh
  // token is also how a request is *retired*: a rename cannot change the scene
  // id already sent in an outstanding GET, so that GET has to stop being
  // authoritative rather than be re-labelled, or its eventual rejection clears
  // a set that is perfectly valid under the new id.
  function fetchAlternates(forCid: string, forSid: string) {
    const req = { token: ++altsReq.current.token, cid: forCid, sid: forSid,
                  // the transcript request this set describes: `selectScene`
                  // mints it before calling here, so both land under the same one
                  window: windowTokenRef.current };
    altsReq.current = req;
    api.getAlternates(forCid, forSid)
      .then((a) => {
        if (altsReq.current === req) {
          setAlternates({ ...a, cid: req.cid, sid: req.sid, window: req.window });
        }
      })
      .catch(() => { if (altsReq.current === req) setAlternates(NO_ALTERNATES); });
  }

  // `renamed` is for the one caller whose id CHANGES without the reader going
  // anywhere: a rename mints a new scene id, so the `id !== activeId` test below
  // reads it as a switch and throws away the turn state — a one-shot response
  // preset picked for the next reply, and an open roll form. Same scene, same
  // reader; only the filename moved.
  async function selectScene(id: string, stillWanted?: () => boolean, renamed = false) {
    // This closure belongs to the campaign it was created in. A background
    // refresh outlives the view it started in — `runStream`'s finally and the
    // flush poll both call this for the scene the TURN owns, long after the
    // reader may have moved to another campaign — and every existing guard on
    // those paths compares scene ids, which repeat freely between campaigns.
    //
    // So A's refresh for "s1" passes `activeIdRef.current === "s1"` while the
    // reader is on B's "s1", installs A's transcript under B's URL, and leaves
    // edits addressing B's file with A's message indices. The resolver could
    // not repair it either: `setActiveId` is handed the value it already has,
    // React bails out of the render, and the effect never re-runs (codex
    // review, P1).
    //
    // Checked here rather than at the two call sites for the reason the rest of
    // this file keeps re-learning: this is the one funnel every refresh passes
    // through, so a future background path cannot forget it.
    if (cidRef.current !== cid) return -1;
    if (stillWanted && !stillWanted()) return -1;
    // selectScene also runs to *refresh* the current scene (runStream's
    // finally, doRoll/doCheck, saveEdit, …) — only an actual scene switch
    // should clear the chip/popover synchronously below; clearing on every
    // refresh would tear down and re-mount a live SSE-delivered proposal
    // for no reason (flicker, and a stale ref by the time the re-fetch lands).
    const switchingScenes = !renamed && id !== activeId;
    const token = ++windowTokenRef.current; // retires any page still in flight
    setActive(id);
    if (switchingScenes) {
      // clear the previous scene's chip/popover synchronously so scene A's
      // proposal never renders against scene B while the fetch below is in
      // flight (and so a stale checkActors list can't leak across scenes).
      setProposalNow(null);
      setRollForm(null);
      // a one-shot override belongs to the turn the player picked it for, on
      // the scene they picked it on — switching scenes must not carry it
      // silently onto an unrelated scene's next reply.
      setPendingResponse(null);
      // Same reasoning for the error banner: it reports what happened to a turn
      // in the scene being left, and its Retry acts on whatever scene is open.
      // Leaving it up invites the player to re-run one scene's failure against
      // another (review, #95).
      setError(null);
      // scene A's alternates must never offer themselves against scene B while
      // the fetch below is in flight — same reason the proposal chip clears here
      setAlternates(NO_ALTERNATES);
      // The post a reader asked to replay from is an INDEX, and an index means
      // nothing in another scene: carried across, it prices — and would cut —
      // whatever post happens to sit there. Same reasoning as the one-shot
      // response override two blocks up.
      setReplayAt(null);
      // The reroll popover and the route in it, for the same reason and with
      // more at stake than the rest of this block. It is anchored to "the post
      // a reroll would replace", which is a different post in every scene — so
      // left open across a switch it reappears over the new scene's trailing
      // reply carrying the connection and model chosen for the old one's, and
      // the next click sends B's reply to a model picked for A (Codex review,
      // #77). The hint had the same hole before the route joined it; a wrong
      // model is the version of it that costs a generation.
      setRerollPrompt(null);
      setRerollRoute(NO_REROLL_ROUTE);
      // a new scene opens at its most recent page, at the bottom
      windowSizeRef.current = PAGE_SIZE;
      atBottomRef.current = true;
    }
    fetchAlternates(cid, id);
    api.getSceneDatetime(cid, id).then(setDt).catch(() => setDt(null));
    api.getCast(cid, id).then(setCast).catch(() => setCast([]));
    api.listAppearances(cid).then(setRoster).catch(() => setRoster([]));
    // The context column's own reads. Each settles independently and each
    // empties only its own block on failure -- the column is continuity, and a
    // weather request that 500s must not take the cast down with it.
    //
    // The dossier is deliberately NOT re-read here: it is keyed to
    // `selectedActor`, which selecting a scene clears, so a stale casefile
    // cannot outlive the scene it was opened from.
    // Started inside a promise so a read that throws *synchronously* empties
    // its own block like one that rejects, rather than taking the whole scene
    // selection down with it — this runs before the transcript is fetched.
    const columnRead = <T,>(read: () => Promise<T>, set: (v: T | null) => void) =>
      Promise.resolve().then(read).then(set).catch(() => set(null));
    columnRead(() => api.sceneBriefing(cid, id), setBriefing);
    Promise.resolve().then(() => api.campaignProvenance(cid))
      .then((p) => setProvenance(p ?? {}))
      // Uncited rows are a normal state, so a failed read degrades to exactly
      // that rather than to an error the reader can do nothing about.
      .catch(() => setProvenance({}));
    columnRead(() => api.getSceneLocation(cid, id), setSceneLocation);
    columnRead(() => api.getSceneWeather(cid, id), setWeather);
    setSelectedActor(null);
    const claim = claimProposalRead();
    api.getRollProposal(cid, id).then((r) => applyProposalRead(claim, r.record))
      .catch(() => applyProposalRead(claim, null));
    // Re-read on every selectScene, refresh included: the inspector's picker
    // calls onSceneChanged after a save, so this is what keeps the chip from
    // showing a preset the scene no longer has.
    Promise.resolve(api.getSceneResponse?.(cid, id))
      .then((r) => setSceneResponse(r ?? null))
      .catch(() => setSceneResponse(null));
    const scene = await api.getScene(cid, id, { limit: windowSizeRef.current });
    if (windowTokenRef.current !== token) return -1; // a later select already landed
    // Asked again here, not only on entry: this fetch is an await, and a turn
    // starting during it would otherwise have the stale response clear the new
    // stream's live preview (`setStreaming`) below. Distinct from the window
    // token above, which retires a page superseded by a *later select on this
    // same view*; this one retires a refresh whose whole reason for existing
    // has expired.
    if (stillWanted && !stillWanted()) return -1;
    setMessages(scene.messages);
    setLoaded({ cid, sid: id, token });
    // an unwindowed reply (no `offset`) is the whole transcript, which starts at 0
    setFirstIndex(scene.offset ?? 0);
    setHasUserPost(scene.has_user_message ?? null);
    setSceneResponsePreset(scene.meta.response_preset ?? "");
    setStreaming("");
    setCtxKey((n) => n + 1);
    // `total`, not `messages.length`: the fetch is windowed now (#94), so once a
    // transcript is longer than a page the window size is a constant and would
    // report no growth however much lands. The flush poll compares this across
    // ticks to notice a cancelled turn's partial arriving.
    totalRef.current = scene.total ?? scene.messages.length;
    totalSceneRef.current = id;
    return totalRef.current;
  }

  // Prepend the page of posts just above the window. Called by the scroll
  // handler and by the button it backs; either way it is a no-op once the top
  // of the transcript is on screen or another page is already in flight.
  async function loadOlder() {
    const id = activeId;
    if (!id || loadingOlderRef.current || firstIndex <= 0) return;
    loadingOlderRef.current = true;
    setLoadingOlder(true);
    const el = streamRef.current;
    const token = windowTokenRef.current;
    try {
      const page = await api.getScene(cid, id, { limit: PAGE_SIZE, before: firstIndex });
      // the reader moved on (another scene, or a refresh that re-read the
      // window): this page describes a transcript that is no longer on screen
      if (windowTokenRef.current !== token) return;
      if (!page.messages.length) {
        setFirstIndex(0); // the transcript shrank under us; nothing older is left
        return;
      }
      // Measure from the bottom, not the top: the prepend pushes everything
      // down by however tall the new posts render, and only the layout effect
      // (after paint, when that height exists) can undo it.
      if (el) prependAnchorRef.current = el.scrollHeight - el.scrollTop;
      windowSizeRef.current += page.messages.length;
      setMessages((m) => [...page.messages, ...m]);
      setFirstIndex(page.offset ?? 0);
    } finally {
      loadingOlderRef.current = false;
      setLoadingOlder(false);
    }
  }

  function newScene() {
    setChooserOpen(true);
  }

  /** Scroll the transcript to the post a citation was taken from.
   *
   *  Matched by substring against the post bodies, which is the only join
   *  available: the citation records the model's excerpt, not a post index, and
   *  an index would rot the moment a post was edited or a reroll replaced one.
   *  A quote that matches nothing scrolls nowhere rather than guessing — the
   *  transcript may simply be paged past it, and jumping to an arbitrary post
   *  would be worse than not moving.
   */
  function goToQuote(quote: string) {
    const needle = quote.trim().toLowerCase();
    if (!needle) return;
    if (!messages.some((m) => m.content.toLowerCase().includes(needle))) return;
    setCitedQuote(quote);
    // Queued behind the render that paints the highlight, so the element it
    // scrolls to is the one that is about to be marked.
    requestAnimationFrame(() => {
      const el = streamRef.current?.querySelector(".msg.cited") as HTMLElement | null;
      el?.scrollIntoView?.({ block: "center" });
    });
  }

  // ---- the context column's cast/dossier swap (2a) ----

  /** Open one actor in the column. Deliberately does NOT touch the transcript,
   *  the composer or focus: this is a column state, not a modal, and the whole
   *  claim of the design is that reading a dossier costs you nothing you were
   *  holding. */
  function openActor(kind: string, id: string) {
    setSelectedActor({ kind, id });
  }

  function closeActor() {
    setSelectedActor(null);
  }

  useEffect(() => {
    if (!selectedActor || !activeId) { setCasefile(null); return; }
    // Cleared first so the column shows its reading state rather than the
    // previous actor's dossier under the new one's name -- these are two
    // people's private states, and briefly attributing one to the other is the
    // one failure this panel must not have.
    setCasefile(null);
    let live = true;
    api.getCasefile(cid, activeId, selectedActor.kind, selectedActor.id)
      .then((c) => { if (live) setCasefile(c); })
      // An actor whose record cannot be read is one this column cannot
      // describe; going back to the cast is the honest outcome, and the grid
      // still shows she is there. Every route in here names someone in the
      // scene's cast -- a tile, or a transcript plate, which is only a button
      // when its speaker resolves against that same cast -- so the grid the
      // fallback lands on is one she is in.
      .catch(() => { if (live) setSelectedActor(null); });
    return () => { live = false; };
    // `ctxKey` bumps on every successful absorb save, which is exactly when
    // every file behind this panel was rewritten.
  }, [cid, activeId, selectedActor, ctxKey]);

  // Where this campaign stands against its budget (#153). Re-read on `ctxKey`,
  // which bumps once a turn -- the moment the number can have moved -- rather
  // than on a timer, so a campaign with no budget costs one request per scene
  // and the server does not even scan the ledger for it (`level: "off"`).
  //
  // Every rejection is swallowed to a cleared banner, the rule this whole view
  // follows: a budget lookup that failed is not a reason to put an error over a
  // turn that landed, and the Cost section in the inspector is where a reader
  // who actually wants the figure goes.
  useEffect(() => {
    let live = true;
    api.getCampaignBudget(cid)
      .then((b) => { if (live) setBudget(b); })
      .catch(() => { if (live) setBudget(null); });
    return () => { live = false; };
  }, [cid, ctxKey]);

  // What each player post in this scene has cost, keyed by transcript index
  // (#153). Read on the same `ctxKey` beat as the budget above.
  //
  // Held WITH the scene it was read for, and that is load-bearing rather than
  // tidy. This view deliberately keeps scene A's messages on screen until B's
  // transcript read lands, and the usage read is a separate request that can
  // land FIRST — so a plain index-keyed map put B's costs beside A's posts, and
  // left them there for good if B's transcript read then failed. Matching the
  // costs to `loaded` (the transcript actually rendered) rather than to `cid`/
  // `activeId` (the one requested) is what closes that window.
  //
  // Swallowed to "no chips" on failure, the rule this view follows everywhere:
  // an accounting read that failed is not a reason to put an error over a
  // transcript. The inspector's Cost section is where a reader who wants the
  // figure and an explanation goes.
  const [postCosts, setPostCosts] =
    useState<{ cid: string; sid: string; byPost: Record<number, UsagePostBucket> } | null>(null);
  useEffect(() => {
    if (!activeId) { setPostCosts(null); return; }
    let live = true;
    // NOT cleared here on the way to a refetch. `postChips` below already
    // refuses to render a bucket whose cid/sid is not the transcript on
    // screen, so clearing bought nothing -- and it made the chips blink out
    // and back on every `ctxKey` change, which is a real gap under load: the
    // suite's own `findByText` for a cost could open and close inside it, and
    // this test reds CI intermittently (#351's shape).
    const forCid = cid;
    const forSid = activeId;
    api.getSceneUsage(forCid, forSid)
      .then((u) => {
        if (!live) return;
        setPostCosts({ cid: forCid, sid: forSid,
                       byPost: Object.fromEntries(u.by_post.map((b) => [b.post, b])) });
      })
      .catch(() => { if (live) setPostCosts(null); });
    return () => { live = false; };
  }, [cid, activeId, ctxKey]);

  /** The chips, but only where they describe the transcript on screen. */
  const postChips =
    postCosts && loaded && postCosts.cid === loaded.cid && postCosts.sid === loaded.sid
      ? postCosts.byPost : null;

  // A dismissal belongs to the campaign it was made in, to the level it was
  // made at, and to the budget it was made against. Without the campaign, "I
  // have seen it" carried onto a different campaign's budget; without the limit
  // and period, raising a cap that had been waved off at 80% and then filling
  // it again said nothing, because the level had not changed since the
  // dismissal even though the number it describes had.
  useEffect(() => { setBudgetSeen(null); },
            [cid, budget?.limit_usd, budget?.period]);

  async function removeSelectedActor() {
    if (!selectedActor || !activeId) return;
    try {
      await api.removeFromCast(cid, activeId, selectedActor.kind, selectedActor.id);
      setSelectedActor(null);
      api.getCast(cid, activeId).then(setCast).catch(() => {});
      // A join/leave appends a transition post (#85), so the transcript moved.
      readScene(activeId);
    } catch (err: any) {
      fail(err, false);
    }
  }

  /** What this page contributes to ⌘K: its scenes, its cast, and the two
   *  actions that used to be buttons in a chrome bar.
   *
   *  This is where the scene rail went. A rail is a list you pay for in width
   *  on every turn of every scene, to answer a question you ask a few times a
   *  session; the same list behind ⌘K costs nothing until asked and searches,
   *  which the rail never did. */
  const paletteSource = useCallback((): PaletteItem[] => {
    const out: PaletteItem[] = [];
    for (const a of cast) {
      out.push({
        id: `cast:${a.kind}/${a.id}`, group: "IN THIS CAMPAIGN", label: a.name,
        meta: `${a.role === "player" ? "player" : "character"} · in scene`,
        badge: initialsOf(a.name),
        run: () => openActor(a.kind, a.id),
      });
    }
    for (const t of briefing?.plot ?? []) {
      out.push({ id: `thread:${t.id}`, group: "IN THIS CAMPAIGN", label: t.title,
                 meta: `thread · ${t.status}` });
    }
    for (const c of briefing?.commitments ?? []) {
      out.push({ id: `owed:${c.id}`, group: "IN THIS CAMPAIGN", label: c.title,
                 meta: `${c.kind} · ${c.due || "no deadline"}` });
    }
    for (const [i, sc] of scenes.entries()) {
      out.push({
        id: `scene:${sc.id}`, group: "SCENES", label: sc.title,
        meta: [`scene ${sceneNumber(sc.id, scenes.length - i)}`,
               sc.done ? "absorbed" : null].filter(Boolean).join(" · "),
        run: () => goToScene(sc.id),
      });
    }
    out.push({ id: "action:new-scene", group: "SCENES", label: "New scene",
               meta: "in this campaign", action: true, run: newScene });
    out.push({ id: "action:campaign-world", group: "ELSEWHERE", label: "This campaign's world",
               meta: "locations, lore, cast", to: `/campaigns/${cid}/world` });
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cid, cast, briefing, scenes]);
  usePaletteSource(paletteSource);

  // Dismissing the chooser (Escape/backdrop) after a soft failure -- once
  // SceneConfirmForm's "Continue to scene" is the only OTHER exit -- still
  // leaves a real, created scene behind, and `writing` is already clear by
  // then so the dismissal goes through. NewSceneChooser reports that scene's
  // id as `createdSid`; every other dismissal (Cancel, an idle Escape) reports
  // none, so this stays a no-op for them -- reloading unconditionally would
  // relist on every plain Cancel too, and most of those wrote nothing.
  //
  // Reuses `loadScenes` -- the same relist `sceneCreated` runs after a normal
  // create -- rather than inventing a second refresh path. Unlike
  // `sceneCreated`, this must NOT navigate: the user chose to leave the scene
  // behind, not open it. The error handling mirrors `sceneCreated`'s, for the
  // reason given in its comment below: this too is called from an event
  // handler with the promise dropped, so a failure here would otherwise be an
  // unhandled rejection describing a scene that really was created.
  function closeChooser(createdSid?: string) {
    setChooserOpen(false);
    if (!createdSid) return;
    loadScenes().catch((err: any) => {
      if (cidRef.current === cid) {
        setError({ text: `The scene was created, but the scene list could not be `
                         + `refreshed: ` + (err?.detail ?? String(err)), retryable: false });
      }
    });
  }

  async function sceneCreated(id: string, initialPrompt?: string) {
    setChooserOpen(false);
    if (initialPrompt) setSeedPrompt({ cid, sid: id, prompt: initialPrompt });
    try {
      await loadScenes();
    } catch (err: any) {
      // Reports rather than throws, for the reason `renameScene` does: the
      // chooser calls this from an event handler and drops the promise, so
      // anything escaping is an unhandled rejection the player never sees —
      // and the scene WAS created, so a silent failure leaves a real scene
      // that the rail does not list and nothing navigates to. Not retryable:
      // the banner's Retry generates, and there is nothing to generate.
      // Scoped, like the success path right below it. The switch to another
      // campaign already cleared that campaign's errors, so an unscoped late
      // rejection raises a banner over it claiming ITS scene list failed
      // (codex review).
      if (cidRef.current === cid) {
        setError({ text: `The scene was created, but the scene list could not be `
                         + `refreshed: ` + (err?.detail ?? String(err)), retryable: false });
      }
      return;
    }
    // Asked AFTER the relist, not before it. The rows are guarded (a list for
    // the campaign the reader left is dropped) and the navigation was not, so
    // a switch during that await left the new scene's URL pointing back at the
    // campaign they came from — dragging them into another campaign entirely
    // (codex review). Guarding the data and not the navigation is the same
    // half-fix twice over, so both halves are asked here.
    if (cidRef.current !== cid) return;
    // The list first, then the URL: the resolver only opens a scene the list
    // knows about, so navigating to a row that has not landed yet would be
    // read as a stale id and bounced straight back to the previous scene.
    goToScene(id);
    // A scene can be BORN past the threshold: starting from a greeting appends
    // that greeting's posts, and a multi-speaker one appends several. None of
    // this component's other triggers fire here — they all hang off a write the
    // reader made in an open scene — so an opener-sized scene would sit with no
    // summary until a later turn or a manual Refresh (#85). A manual (empty)
    // scene answers `refreshed: false` having reached no provider.
    refreshAndAsk(id);
  }

  // Re-read a scene after a write made OUTSIDE this component, then ask whether
  // its summary is now due, bounded by the length that re-read verified. The
  // catch is what makes the pair safe to fire without awaiting: a failed read is
  // no boundary, and `askAfterPost` declines a negative one rather than
  // falling back to an unbounded fold.
  async function refreshAndAsk(id: string) {
    askAfterPost(id, await selectScene(id).catch(() => -1));
  }

  // A scene's id is its filename, so a rename mints a new one. `scene_refs.repoint`
  // carries every *persisted* reference across; the rest live only in this
  // browser, where no server-side repointer can see them. Three of those belong
  // to an open review and are repointed by `useSceneReview.sceneRenamed`, whose
  // comment is where the reasoning for each of them lives; the fourth is the
  // reroll-alternates state, and it is here.
  function reviewSceneRenamed(oldId: string, newId: string) {
    review.sceneRenamed(oldId, newId);
    // The alternates sidecar moves with the scene file (`scene_refs.repoint`),
    // but the id this state is scoped to lives only here — left stale, the
    // scope gate reads the set as another scene's and the control vanishes
    // until something else re-selects the scene.
    setAlternates((a) => (a.sid === oldId ? { ...a, sid: newId } : a));
    // Dropped, not re-keyed. The posts on screen are only known to be this
    // scene's *transcript as fetched* — and a swap can be in flight against the
    // old id, so what the backend holds may already differ from what is
    // rendered. Re-keying would carry that readiness across a rename and let
    // the renamed set's counter sit on pre-swap text; dropping it hides the
    // control until a real load lands, which the callers below arrange.
    setLoaded((l) => (l && l.sid === oldId ? null : l));
    // Re-key what is on screen (the sidecar moved with the scene file, so the
    // set is still correct and the control must not blink) — but retire the
    // in-flight GET rather than re-label it: it still carries the *old* id, so
    // its rejection says nothing about the renamed scene, and honouring that
    // rejection would hide the controls until something re-selects the scene.
    if (altsReq.current.sid === oldId) fetchAlternates(cid, newId);
  }

  // A scene's id is its filename, so a rename mints a new one and every piece
  // of client state keyed by the old id has to follow it here — in ONE place.
  //
  // This used to be a list spelled out at each rename site, and review found
  // two things missing from it, one of them holding the only copy of text the
  // player had written: a recovered prompt parked under the old id is looked up
  // under the new one, found missing, and lost for good when the view unmounts.
  // The other sent Retry down the `/retry` path after a failed reroll, dropping
  // the player's guidance and continuing from the reply they asked to replace.
  //
  // So a sixth piece of id-keyed state is a missing line in this function
  // rather than a sixth rediscovery of the rule. `streamingId` is deliberately
  // absent: renaming the scene a turn is writing to is blocked outright
  // (`sceneLocked`), so it cannot go stale this way, and re-pointing it here
  // would quietly legitimise the rename the lock exists to prevent.
  function adoptSceneId(oldId: string, newId: string) {
    // The ledger bump happens BEFORE the same-id guard, because it is the one
    // thing here that is not about the id. Every thread and commitment the
    // route returns carries the TITLE of the scene that last moved it, and a
    // rename that keeps the slug — a capitalisation or punctuation edit —
    // changes that title while returning the id unchanged. Guarded with
    // everything else, an open ledger kept showing the old title until an
    // unrelated refresh. The rename paths touch none of the panel's other
    // dependencies (same campaign, no absorb saved), so this is the only thing
    // that tells it to re-read.
    setCtxKey((k) => k + 1);
    if (oldId === newId) return;
    // `activeIdRef`, not the render-captured `activeId`: this runs from handlers
    // that awaited a request, and the reader can have moved on since. Adopting
    // the new id for a scene they are no longer looking at moves them to it.
    //
    // And the CAMPAIGN as well as the id, because the id alone does not say
    // which scene it is. A rename in campaign A finishing after the reader
    // moved to B, whose active scene carries the same id — routine, since each
    // campaign numbers its scenes from 001 — would otherwise adopt A's new id
    // and replace B's history entry with A's scene URL, moving the reader to
    // another campaign entirely (codex review).
    // `cidRef` as well as `activeCidRef`, and the difference is not academic:
    // `activeCidRef` says which campaign the loaded scene came from, which is
    // still the OLD one during the window after the reader has navigated to
    // another campaign but before its first scene has loaded. A rename landing
    // in that window passed an `activeCidRef`-only test and navigated them
    // straight back. Only `cidRef` answers "is the view still here".
    if (cidRef.current === cid
        && activeIdRef.current === oldId && activeCidRef.current === cid) {
      setActive(newId);
      // …and the URL with it, in the same batch as `setActive`, so the resolver
      // sees one consistent pair and leaves this rename alone. `replace`: the
      // reader did not go anywhere, and the entry being replaced names a file
      // that no longer exists. React 18 batches both updates into one render,
      // so there is no intermediate state where the URL still says `oldId`
      // while the ref says `newId` — which the resolver would read as the
      // reader having navigated, and chase back to a scene that is now gone.
      navigate(sceneUrl(cid, newId), { replace: true });
    }
    // The rail's own metadata, not just the things pointing at it. `pcless` and
    // the title are read off the row whose id matches `activeId`, so adopting
    // the id without re-keying the row loses both — an offscreen scene silently
    // offering the PC composer for sends the backend still handles as director
    // notes. A relist would fix it, and a relist that FAILS is exactly the
    // state this has to survive.
    //
    // Campaign-scoped, like the adoption above, and for a sharper reason than
    // tidiness: the rail is what the resolver reads to decide whether the sid
    // in the URL still exists. Re-keying a row in the campaign the reader moved
    // to — B's `s1` matches `oldId` whenever both campaigns number from 001 —
    // makes B's own URL look stale, and the resolver dutifully moves the reader
    // off it. Scoping only the navigation above left this door open, which is
    // how the test for that fix still failed.
    if (cidRef.current === cid) {
      setScenes((list) => list.map((s) => (s.id === oldId ? { ...s, id: newId } : s)));
    }
    // Compared against this handler's OWN campaign, not the one on screen: the
    // scene that was renamed is this campaign's, so its premise follows the new
    // id wherever the reader happens to be standing.
    setSeedPrompt((p) =>
      (p && p.cid === cid && p.sid === oldId ? { ...p, sid: newId } : p));
    reviewSceneRenamed(oldId, newId);
    // Campaign-scoped like the seed prompt just above, and for the same
    // sentence: the renamed scene is THIS handler's campaign's, so a bare
    // scene id would move a park belonging to whichever campaign shares that
    // number (Codex review, round 4).
    const parked = parkedPrompts.current.get(parkKey(cid, oldId));
    if (parked !== undefined) {
      parkedPrompts.current.delete(parkKey(cid, oldId));
      parkedPrompts.current.set(parkKey(cid, newId), parked);
      setParkedTick((n) => n + 1);   // it may now be the scene on screen
    }
    const again = rerollToRetryRef.current;
    if (again && again.sid === oldId) rerollToRetryRef.current = { ...again, sid: newId };
  }

  // Reports rather than throws. `EditableRow` calls this from an event handler
  // and drops the promise, so anything escaping here is an unhandled rejection
  // the player never sees — and one half of it (a relist that fails after the
  // rename landed) is not even a failed rename.
  async function renameScene(id: string, title: string) {
    markRenaming(true);
    try {
      const { id: newId } = await api.renameScene(cid, id, title);
      // The `sid` carries the slug, so this scene has a new one -- and an
      // unresolved send filed under the old one becomes unreachable, which for
      // a rolled-back post means the player's words are stranded under an id
      // nothing will select again.
      registry.rekey(cid, id, newId);
      adoptSceneId(id, newId);
      try {
        await loadScenes();
      } catch (err: any) {
        // The rename LANDED; only the rail is stale. Not retryable — the
        // banner's Retry generates, and there is nothing here to generate.
        setError({ text: `Renamed, but the scene list could not be refreshed: `
                         + (err?.detail ?? String(err)), retryable: false });
      } finally {
        // Re-read the transcript, not only the ids that point at it. A swap in
        // flight against the OLD id finds `activeIdRef` already moved on and
        // skips its own refresh, so without this nothing ever replaces the
        // pre-swap posts on screen — with the renamed set's counter on top of
        // them, and edits saving against indices that have shifted.
        //
        // In a `finally`, because the rail relist above is unrelated to it:
        // ordered first so the row exists before the reader is moved onto it,
        // but its failure must not decide whether the transcript gets re-read.
        // The re-read is the half that keeps edits from saving over the wrong
        // post; the rail being stale is cosmetic.
        //
        // Asked HERE, against the new id, rather than remembered from before
        // the rename: the relist above is a second await, and a flag captured
        // ahead of it says what was true then. `selectScene` calls `setActive`,
        // so a reader who moved on during the relist would not merely get a
        // refresh they did not need — they would be dragged back to it.
        //
        // The new id is the right side of the comparison because `adoptSceneId`
        // has already re-pointed the ref: a reader still on this scene is
        // wearing `newId` by now, and one who left is somewhere else. Same test
        // the first-date path makes, for the same reason.
        if (activeIdRef.current === newId) await selectScene(newId, undefined, true);
      }
    } catch (err: any) {
      fail(err, false);   // the rename itself; generating is not its recovery
    } finally {
      markRenaming(false);
    }
  }

  // the first date set renames the scene file — re-list and adopt the new id.
  // Only ever the active scene: the date controls live in the panels for the
  // scene on screen.
  async function sceneRenamed(id: string) {
    const initiator = activeId;   // the scene whose inspector asked for the stamp
    if (initiator) adoptSceneId(initiator, id);
    try {
      await loadScenes();
    } catch (err: any) {
      setError({ text: `Renamed, but the scene list could not be refreshed: `
                       + (err?.detail ?? String(err)), retryable: false });
    } finally {
      // Only pull the renamed scene onto the screen if it is still the one being
      // read. A slow first-date request can land after the reader has moved to
      // another scene, and this callback belongs to the scene that started it —
      // forcing that one back would be wrong on its own, and doing it as a
      // *rename refresh* would carry the turn state of the scene they left onto
      // the one they get.
      // Compared against the NEW id, not the initiator: `adoptSceneId` re-points
      // the ref as part of adopting, so the scene that asked for the stamp is
      // already wearing `id` by now. A reader who moved on during the re-list
      // leaves the ref somewhere else, and this stays put.
      //
      // In a `finally` for the same reason `renameScene` uses one: the relist is
      // the rail's business, the re-read is the transcript's, and a failure in
      // the first must not decide whether the second happens.
      //
      // Awaited and caught, also like `renameScene`. This is a callback the
      // inspector fires and drops, so an unawaited rejection here goes nowhere:
      // no banner, and the pre-rename posts left on screen under the new id,
      // editable against indices that may have shifted.
      if (initiator && activeIdRef.current === id) {
        try {
          await selectScene(id, undefined, true);
        } catch (err: any) {
          setError({ text: `Renamed, but the scene could not be re-read: `
                           + (err?.detail ?? String(err)), retryable: false });
        }
      }
    }
  }

  async function commitSceneRename() {
    if (!renamingScene) return;
    const { id, title } = renamingScene;
    setRenamingScene(null);
    await renameScene(id, title);
  }

  async function deleteScene(s: SceneMeta) {
    if (!window.confirm(`Delete '${s.title}'?`)) return;
    await api.deleteScene(cid, s.id);
    await loadScenes();
    // Same reason as `renameScene`: the ledger resolves every thread,
    // commitment and fact against the scene list, so a deletion changes what
    // it returns. Bumped here rather than left to the refresh, because the two
    // deletions that need it least are the ones that reach no refresh at all:
    // deleting a scene the reader is not on moves nothing, and deleting the
    // last one leaves nothing to read.
    setCtxKey((k) => k + 1);
    // Nothing else to do. The deleted row is gone from the list, so if it was
    // the one in the URL the resolver now reads that sid as stale and moves
    // the reader on — or, with nothing left, clears the view and drops the
    // scene from the URL. Deleting a row the reader is NOT on leaves the sid
    // valid, and the resolver leaves them where they are.
  }

  /** Fork the campaign at this scene (#72), leaving this one untouched.
   *
   *  Offered from the scene you are reading rather than from the shelf,
   *  because "fork at an earlier turn" needs a scene and this is the only
   *  place the app already has one in hand. The fork keeps this scene whole
   *  and takes every later one off the copy, putting back what those scenes'
   *  absorbs wrote wherever the change journal still reaches.
   *
   *  The confirmation says what a cut costs, for the reason
   *  `deleteMessagesFrom`'s does: none of it is visible on the branch
   *  afterwards. It is stated only when there ARE later scenes — forking at
   *  the newest one copies the campaign whole and has nothing to warn about.
   *
   *  Navigates to the fork. The player asked to branch, and a branch you are
   *  not looking at is a fork you have to go and find; this campaign is
   *  unchanged and one click away in the shelf.
   *
   *  The note is raised BEFORE that navigation and is meant to survive it —
   *  it describes the fork, so the fork's page is where it belongs. That works
   *  because the `[cid]` effect above clears the campaign-scoped review state
   *  and deliberately not `error`; a change there that started clearing it
   *  would silently drop this report, which is what
   *  `what the fork could not put back is reported, and outlives the
   *  navigation` pins.
   */
  async function forkAtScene(sid: string) {
    const later = scenes.filter((x) => x.id > sid).length;
    const title = scenes.find((x) => x.id === sid)?.title ?? sid;
    const ask = [
      `Fork this campaign at '${title}'?`,
      later === 0
        ? "It is the newest scene, so the fork is a copy of this campaign as it stands."
        : `The fork keeps this scene and drops the ${later} scene${later === 1 ? "" : "s"} ` +
          "after it, putting back what their absorbs wrote wherever that is still on " +
          "record. Records something else has written to since, and records a dropped " +
          "scene created, keep what they hold and are reported.",
      "This campaign is not changed.",
    ].join("\n\n");
    if (!window.confirm(ask)) return;
    const forkName = window.prompt("Name the fork?", `${name} (fork)`)?.trim();
    if (!forkName) return;
    let report;
    try {
      report = await api.forkCampaign(cid, forkName, sid);
    } catch (err: any) {
      // Not retryable: the banner's Retry generates, and there is nothing here
      // to generate — the same call `deleteMessagesFrom` makes for the same
      // reason.
      fail(err, false);
      return;
    }
    const notes = forkNotes(report);
    if (notes) setError({ retryable: false, text: notes });
    navigate(`/campaigns/${report.id}`);
  }

  // How long to keep looking for a cancelled turn's partial. Aborting rejects
  // the fetch the instant the socket is torn down here, but the backend only
  // then notices the disconnect and runs its shielded flush — so the refresh
  // that follows a Stop can legitimately read a transcript the partial has not
  // reached yet, and the text would sit on disk while the screen denied it
  // existed until some later refresh.
  //
  // The budget has to clear the backend's own cancellation grace, which review
  // caught an earlier flat 1.5s falling short of: `llm._settle` gives an
  // unresponsive provider up to `_CLOSE_TIMEOUT` (5s) to unwind and `_aclose`
  // another 5s, and on the common path — cancellation arriving while waiting on
  // the next delta — that unwinding happens *before* the write. Hence a
  // doubling delay: the ordinary case (a provider that lets go at once) still
  // resolves on the first retry, and the tail covers the documented worst case
  // instead of giving up in the middle of it. Still bounded, because past this
  // the write is contending on the store lock, which no amount of polling here
  // will shorten, and the player's next action refreshes anyway.
  const FLUSH_POLL_MS = 250;
  const FLUSH_POLL_MAX_MS = 2000;
  const FLUSH_POLL_BUDGET_MS = 12000;

  async function awaitFlushedPartial(id: string, seen: number) {
    // Stop the moment this poll stops being the thing that owns the view. A new
    // turn (abortRef) or a different scene (activeIdRef) has its own refresh,
    // and carrying on would have `selectScene` clear the new stream's live
    // preview — or, worse, yank the player back to the scene they just left.
    // Refs, not state: this loop runs across renders and a captured
    // `busy`/`activeId` would be stale.
    //
    // Handed to `selectScene` as well as checked here, because checking only
    // here leaves a window review caught: the check passes, then `getScene` is
    // awaited, and a turn starting during that await still gets its preview
    // cleared by the response.
    const owns = () => mountedRef.current && !abortRef.current && activeIdRef.current === id;
    let wait = FLUSH_POLL_MS;
    let waited = 0;
    while (waited < FLUSH_POLL_BUDGET_MS) {
      await new Promise((r) => setTimeout(r, wait));
      waited += wait;
      wait = Math.min(wait * 2, FLUSH_POLL_MAX_MS);
      if (!owns()) return;
      // A read that fails is a tick that learned nothing, not the end of the
      // wait: the flush this is watching for happens on the server whether or
      // not one GET made it there, and throwing here would escape a `finally`
      // (review, #95). Keep polling until the budget runs out.
      const n = await selectScene(id, owns).catch(() => -1);
      if (!owns()) return;
      if (n > seen) return void await settleProposal(id, owns);
    }
  }

  // The last word on the proposal, once the transcript proves the flush is done.
  //
  // `selectScene` fires its proposal fetch and awaits only `getScene`, so on the
  // tick that catches the flush the two requests raced it independently — and
  // `finalize` writes the proposal *before* the narration, so the read that saw
  // the transcript grow is the one guaranteed to be after both writes, while the
  // proposal read beside it may have landed before either. Its late `null` then
  // clears a chip that does exist, and the poll has already stopped looking:
  // a roll the player has to answer, invisible until something else refreshes.
  // One awaited read, after growth, settles it (review, #95).
  // Reaching the server is load-bearing here and is now the endpoint's own
  // guarantee: `getRollProposal` never coalesces, so this cannot be handed the
  // promise `selectScene` started before the flush — the stale answer it exists
  // to overrule — and its claim, being newer, genuinely describes a newer read.
  async function settleProposal(id: string, owns: () => boolean) {
    const claim = claimProposalRead();   // issued after selectScene's, so it wins
    const r = await api.getRollProposal(cid, id).catch(() => null);
    if (r && owns()) applyProposalRead(claim, r.record);
  }

  // Returns whether the turn actually landed (no thrown error and no e.error
  // event) — callers use this to decide whether a pending one-shot response
  // override was honoured and can be cleared, or must survive for retry/reroll.
  // `rerolling` decides what a failure OFFERS, and it turns on whether any
  // narration arrived — the two reroll failures want opposite things:
  //
  // - some narration, then the error. The backend persisted that partial, so
  //   the slot is FULL. Retry would append a second generation, move the slot
  //   and retire the set the reroll was building. The transcript still ends on
  //   an assistant post, so the gutter's own ↻ is there and replaces in place;
  //   the banner offers nothing.
  // - nothing at all. The backend archived and removed the old reply, so the
  //   slot is EMPTY and the transcript ends on the player's post — `canReroll`
  //   is false and there is no ↻ to fall back on. `/retry` streams straight
  //   into the empty slot, so Retry is both safe and the only way forward.
  //
  // Trimmed, matching `streaming.py`'s `watcher.narration.strip()`: deltas that
  // are only whitespace persist nothing, so that slot is empty too, and reading
  // them as a landed partial takes away the one button that refills it.
  // Ask the server whether the scene's running summary is now due (#85).
  //
  // Deliberately NOT awaited by any caller: the player's next action must never
  // queue behind a summarization, which is the whole meaning of "non-blocking"
  // here. Sent without `force`, so the decision — and the cost — stay on the
  // server; a scene short of the threshold answers `refreshed: false` having
  // reached no provider, which is why it is cheap enough to fire after every
  // transcript write.
  //
  // Every rejection is swallowed. A missing key, a dead provider, a busy store:
  // none is a reason to put a banner over a turn that landed, and the panel's
  // own Refresh button reports the failure when the player actually asks.
  //
  // The `ctxKey` bump is guarded on the reader still being here, like every
  // other post-await write in this component: a summary written for the scene
  // they just left must not re-read the panel for the scene they are on.
  //
  // A function rather than a line inside `runStream`, because review caught
  // that generated turns are not the only writer: a manual dice roll and a
  // check both append narrator posts, so a mechanics-heavy stretch of play
  // could cross the threshold repeatedly with nothing ever asking. Named for
  // the SIGNAL rather than for one of its consumers, since #84 joined #85 here
  // and a name that promised only a summary would be a lie the next reader
  // trips over.
  // `upto` is REQUIRED, and a negative one means "no boundary was read" — the
  // re-read that would have supplied it was retired by a newer select, or the
  // reader left. Review caught what the earlier `undefined` fallback did there:
  // it sent an UNBOUNDED request, so the one case that most needed the bound —
  // a newer turn already in flight — was the case that dropped it, and the fold
  // could cover a player post whose reply had not been written yet. That reply
  // is an append, so it would stay out of the "current" summary until another
  // threshold. Skipping costs nothing: whatever superseded this read is itself
  // a transcript write and asks again with a boundary it actually verified.
  function askAfterPost(id: string, upto: number) {
    if (upto < 0) return;
    api.refreshRollingSummary(cid, id, false, upto)
      .then((r) => { if (r.refreshed && activeIdRef.current === id) setCtxKey((n) => n + 1); })
      .catch(() => {});
    // The scene-break question rides the same signal (#84) — every call site
    // here is already "a post landed, at a boundary I verified", which is
    // exactly when both gates want re-evaluating. Fired separately rather than
    // chained, because neither answer is a precondition for the other and
    // chaining would make a failed summary silently skip the break question.
    // Not awaited and its rejection swallowed, for the summary's reason: no
    // action should fail because a suggestion could not be written.
    api.askSceneBreak(cid, id, false, upto)
      .then((r) => { if (r.asked && activeIdRef.current === id) setCtxKey((n) => n + 1); })
      .catch(() => {});
  }

  /** Resolve a send this client made and never heard the end of.
   *
   *  Runs on mount and whenever the tab becomes visible, which are the two
   *  moments a phone comes back. Four outcomes, and the fourth is the one an
   *  implementation forgets:
   *
   *  - **live** -- the turn is still generating. Attach to its buffer from one
   *    past the last frame we actually read and render the rest.
   *  - **terminal** -- it finished while we were away. Refetch the scene; the
   *    reply is in the transcript.
   *  - **gone** -- it finished, nobody attached, and the retention window
   *    passed. Indistinguishable from the above by looking at the registry, and
   *    surfacing the 404 would report a lost turn that landed perfectly.
   *  - **rolled back** -- it FAILED after the post was appended, the backend
   *    took the post off, and the record expired. The refetched transcript is
   *    correctly missing the post, so settling would discard the only surviving
   *    copy of what the player typed. `attempt-state` is what tells these last
   *    two apart, and where it cannot, the text is kept.
   */
  /** Read a run's buffered frames from where we left off, then keep reading.
   *
   *  The same rendering the live path does, driven from the run's buffer
   *  instead of from the POST's own response: deltas accumulate into the
   *  streaming preview, a proposal opens its chip, and `done` or an error ends
   *  it. The cursor is `resumeFrom` -- one past the last frame actually read --
   *  so nothing is replayed and nothing is skipped.
   */
  const attachToRun = useCallback(async (cid: string, sid: string, runId: string) => {
    const controller = new AbortController();
    abortRef.current = controller;
    runRef.current = { cid, sid, attempt: registry.pending(cid, sid)?.attempt ?? "", id: runId };
    setBusy(true);
    setStreamingId(sid);
    let acc = "";
    let finished = false;
    try {
      await api.attachRun(cid, sid, runId, registry.resumeFrom(runId), (e) => {
        if (e.delta) { acc += e.delta; setStreaming(acc); }
        else if (e.error) {
          fail(e.error, true);
          finished = true;
          // An ADOPTED run can fail the same way a watched one does, and it
          // rolls the post back the same way -- `post_returned` says so. The
          // registry is holding the only remaining copy of those words at that
          // point (the transcript no longer has them, and the component that
          // typed them may be long gone), and the `settle` below is about to
          // drop it. Recovered first, so the settle retires an entry whose
          // text has already been handed back.
          if (e.error.post_returned) {
            const held = registry.pending(cid, sid)?.text;
            if (held) recoverPrompt(cid, sid, held);
          }
        } else if (e.proposal) {
          setProposalNow({ id: e.proposal.id, status: "pending",
                           payload: e.proposal, resolution: null });
        } else if (e.done) { finished = true; }
      }, controller.signal, (index) => registry.consume(runId, index));
    } catch (err) {
      // A reattach that fails is not a failed turn: the run is on the server
      // and the reply is heading for the transcript either way. Left unsettled
      // so the next visibility change tries again.
      //
      // AND LEFT LOCKED, which review caught was not the case. Clearing `busy`
      // here put Send back over a run that still owns the scene -- and the next
      // send's `registry.begin()` overwrites this scene's one pending entry, so
      // if the original run then rolls its post back, the words it was holding
      // are gone with it. The same posture as an unconfirmed Stop, for the same
      // reason: nothing has established what became of that run, so nothing may
      // act as though something had.
      //
      // `abortRef` alone is cleared, because the adoption pass -- which is what
      // resolves this -- bows out while it is set.
      if (!isAbortError(err)) setStreaming("");
      abortRef.current = null;
      // UNLESS a Stop is what aborted it. `runStream`'s finally is where a
      // pending cancel is normally waited on, and an adopted run never goes
      // through it -- so without this the player presses Stop, the backend
      // duly cancels, and the composer stays locked forever on a turn that is
      // over. Confirmed the same way and unconfirmed the same way: the wait is
      // what makes the button honest, here as there.
      const pending = cancelRef.current;
      if (pending) {
        cancelRef.current = null;
        try {
          await pending;
          registry.settle(cid, sid);
          runRef.current = null;
          setStreamingId(null);
          setStreaming("");
          setBusy(false);
        } catch {
          setError({ text: "could not reach the server to stop this turn — it "
                           + "may still be generating", retryable: false });
        }
      }
      return;
    }
    if (finished) registry.settle(cid, sid);
    abortRef.current = null;
    setBusy(false);
    setStreamingId(null);
    // The refetch FIRST, and the preview cleared after it. Clearing first
    // leaves a window with the text nowhere -- and that window is not
    // theoretical: the aborted turn's flush poll is still refreshing this
    // scene, and `selectScene`'s window token retires whichever refetch loses.
    // Holding the preview means the reader sees the reply either way.
    await selectScene(sid).catch(() => -1);
    setStreaming("");
    // The REPLAY PANEL does not read the transcript, it reads the replay
    // session -- and it loads that once, on `cid`/`sid`. So an adopted run
    // that happened to be a replay turn refreshes everything the panel does
    // not look at: `session.pending` stays false and it keeps offering
    // "Replay next turn", which the already-pending session then 409s.
    //
    // Bumped unconditionally rather than only for replay runs, because from
    // here a run is just a run -- `attachRun` carries no kind, and inventing
    // one to avoid a re-mount that costs one GET would be the wrong trade.
    setReplayEpoch((n) => n + 1);
  }, [registry, recoverPrompt]);   // eslint-disable-line react-hooks/exhaustive-deps

  const adoptPendingRun = useCallback(async (cid: string, sid: string) => {
    const held = registry.pending(cid, sid);
    // NO HELD ATTEMPT IS NOT NOTHING TO DO. The provider is recreated empty by
    // a full reload -- and on Android the WebView's renderer can be restarted
    // out from under a perfectly healthy backend turn, which is precisely the
    // scenario this feature exists for. Returning here left that view showing
    // an unlocked composer over a stale transcript, and the next send refused
    // with `run_in_flight` by the run it could not see.
    //
    // The attemptless lookup is a different question -- "what is the newest
    // run on this scene?" -- and only its LIVE answer is acted on. A terminal
    // one belongs to a turn some other client owns the words for; there is
    // nothing here to restore and nothing to settle.
    if (!held) {
      if (abortRef.current) return;
      let live;
      try {
        live = (await api.findRun(cid, sid)).run;
      } catch {
        return;                 // a failed lookup is not an answer; ask again later
      }
      if (live && live.state === "running") {
        // Only a TURN has frames to read. A `review` run is live work on this
        // scene too -- an absorb the reader started before the phone locked --
        // but its value is a payload the review panel fetches, not a stream,
        // and attaching to its (empty, never-`done`) buffer would leave this
        // view showing an endless empty reply over a scene that is being
        // absorbed perfectly well. It is left alone rather than released:
        // `useSceneReview` adopts it, and releasing here would unlock a
        // composer the server is refusing anyway.
        if (live.cls === "review") return;
        await attachToRun(cid, sid, live.id);
        return;
      }
      // NOTHING IS RUNNING HERE, and that is a real answer -- so it is also the
      // release. A reattach that failed deliberately leaves this view locked
      // (see `attachToRun`'s catch), and with no pending attempt there is no
      // other pass that would ever let go. Unconditional because the ordinary
      // case has nothing to release and setting state to what it already is
      // costs nothing.
      runRef.current = null;
      setStreamingId(null);
      setStreaming("");
      setBusy(false);
      return;
    }
    // Not while this component is already driving a turn. Adoption is for a
    // send we LOST -- the socket died, the tab was suspended, the view
    // unmounted -- and running it alongside a live stream has two of them
    // rendering into one scene, refetching over each other and racing to
    // settle. `abortRef` is set for exactly as long as this view owns a
    // stream, which is the question being asked.
    if (abortRef.current) return;
    // By ATTEMPT, not by the run id we may or may not have seen. The id is a
    // shortcut; the attempt is what names this send in the window where no
    // frame ever arrived, which is the window worth recovering from.
    //
    // A TRANSPORT FAILURE IS NOT AN ANSWER. `visibilitychange` fires the
    // instant the tab comes forward, which is routinely before connectivity is
    // actually back -- so this request is more likely to fail here than
    // anywhere else in the app. Folding that into "no run" and carrying on
    // discards the only handle to a chat that may still be generating, and
    // then hands back a prompt that is sitting in the transcript, inviting the
    // player to send it twice. Left pending instead: the next visibility
    // change, or the next mount, asks again.
    let handle;
    try {
      handle = (await api.findRun(cid, sid, held.attempt)).run;
    } catch {
      return;
    }
    if (handle && handle.state === "running") {
      // STILL GENERATING, and this is the case the whole feature is for. Read
      // the frames produced while we were away and keep reading live -- the
      // backend buffered every one of them. Returning here instead (which an
      // earlier revision of this function did, under a comment claiming
      // otherwise) leaves the turn running perfectly on the server and the
      // screen showing nothing until a manual reload.
      await attachToRun(cid, sid, handle.id);
      return;
    }
    // Either it finished, or its record is gone. Ask the durable question --
    // and, for the reason above, only act on an ANSWER. A failed request here
    // used to read as `retained: false`, which restores a prompt that may
    // still be in the transcript AND settles the attempt, so the second
    // recovery pass that would have got it right never runs.
    let durable: boolean;
    try {
      durable = (await api.attemptState(cid, sid, held.attempt)).retained;
    } catch {
      return;
    }
    // SETTLED only when the words are safe somewhere else. A post still in the
    // transcript is durable and this entry has nothing left to protect; a
    // rolled-back one leaves this as the only copy, and settling it would move
    // that copy into component state -- which is precisely what a reload or an
    // Android renderer restart destroys, the event the record exists to
    // survive. `recovered` keeps it, flagged, so recovery stops asking about a
    // run that is over while the text stays durable until a later send.
    if (durable) registry.settle(cid, sid);
    else registry.recovered(cid, sid);
    // Whatever this component was left holding for that send is resolved now.
    // An unconfirmed Stop deliberately leaves the composer locked and `runRef`
    // set (see `runStream`'s finally), on the grounds that the run may still be
    // generating; this is the pass that finally answers that question, so it
    // has to be the pass that lets go. Unconditional because the ordinary case
    // has nothing left to release -- `runStream` cleared it all -- and setting
    // state that is already that value is a no-op.
    runRef.current = null;
    setStreamingId(null);
    setStreaming("");
    setBusy(false);
    await selectScene(sid).catch(() => -1);
    // FAILED WHILE WE WERE AWAY, and the server knows why. The rest of recovery
    // only asks whether the post was retained, refetches and unlocks, so a
    // provider error, a store failure or a check that would not resolve simply
    // vanished: the player came back to a scene with no reply, no banner and
    // nothing to act on.
    //
    // AFTER the refresh, not before. `selectScene` clears the banner on a
    // successful load -- which is right for an ordinary scene open and wrong
    // here, so this has to be the last word rather than the first.
    if (handle?.state === "failed" && handle.error) fail(handle.error, true);
    if (!durable) {
      // The post is not in the scene. Either it was rolled back or it never
      // landed -- and both mean the player's words exist only here. Ambiguity
      // resolves toward the recoverable error every time: wrong this way costs
      // one duplicate they can see and delete, wrong the other way costs them
      // their words with no trace at all.
      if (held.text) recoverPrompt(cid, sid, held.text);
    }
  }, [registry, recoverPrompt]);   // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!activeId) return;
    void adoptPendingRun(cid, activeId);
    const onVisible = () => {
      if (document.visibilityState === "visible" && activeIdRef.current) {
        void adoptPendingRun(cid, activeIdRef.current);
      }
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [cid, activeId, adoptPendingRun]);

  async function runStream(
    id: string,
    start: (onEvent: (e: ChatEvent) => void, signal: AbortSignal,
            attempt: string, onIndex: (i: number) => void) => Promise<void>,
    onPromptUnstored?: () => void,
    rerolling = false,
    // What the player typed into this turn, kept in the registry until the
    // outcome proves it durable. Empty for the turns that submit no text of
    // their own -- a reroll, a retry -- which have nothing to give back.
    recoverableText = "",
    // Whether this turn persists a post of its own. An ephemeral one does not,
    // which changes what evidence the recovery below can use — see there.
    ephemeral = false,
    // The 🎬 note this generation is steering, or null for the generations that
    // steer with nothing — an ordinary post, a reroll, a resolved proposal.
    // Owned HERE rather than at the call sites (Codex review): every stream
    // enters by this function, and `send` was only one of five ways in, so a
    // note left over from a previous turn rendered beside a reroll or a
    // proposal continuation as though it had steered them.
    note: { cid: string; sid: string; text: string } | null = null,
  ) {
    setDirectorNote(note);
    // The authoritative rename guard, not the ones in `send`/`retry`/`reroll`.
    // Those stop the optimistic UI work before it happens, but they are a list
    // of call sites — and review found the one that was missing: resolving a
    // roll proposal streams a continuation through here without going past any
    // of them. Every stream enters by this function, so this is where the
    // question belongs; the callers keep their checks so they can bail before
    // clearing a composer, not because they are the guarantee (#95).
    if (renamesInFlight) return false;
    const controller = new AbortController();
    abortRef.current = controller;
    // Minted here, before anything is sent. This is what makes the turn
    // addressable in the window between the server accepting it and its first
    // frame arriving -- see `runRef`.
    const attempt = newAttemptId();
    // Registered BEFORE the request goes out. Registering when the leading
    // frame arrives would leave nothing recorded in exactly the window this is
    // for: the server accepts the send, mutates the scene, and the response
    // dies before any frame reaches us.
    registry.begin({ cid, sid: id, attempt, text: recoverableText, runId: null });
    // The CAMPAIGN travels with the handle. React Router reuses this component
    // across `/campaigns/A` -> `/campaigns/B`, so `cid` is whichever campaign
    // is on screen when Stop is pressed -- and scene ids are campaign-local, so
    // cancelling A's turn through B's id reaches nothing and the turn runs on.
    runRef.current = { cid, sid: id, attempt, id: null };
    const streamToken = ++streamTokenRef.current;
    setBusy(true);
    setStreamingId(id);
    setError(null);
    // How long this scene's transcript was before the turn wrote anything —
    // the baseline the pre-response restore below compares against.
    //
    // `totalRef` alone is not it. It holds whichever scene was read last, and a
    // turn can start on a scene that has not been read yet: Send stays enabled
    // while a freshly selected scene is still loading, and `send` creates a
    // scene and streams into it without a read in between. Measuring this
    // scene's growth against another scene's length decides the restore by
    // which transcript happened to be longer — restoring a prompt that landed
    // (the player sends it twice) or dropping one that never did (review, #95).
    // So: use the ref only when it is this scene's, and otherwise read the one
    // number needed, which costs a request only inside that window.
    //
    // Raced against Stop rather than cancelled by it. This read runs before the
    // POST, so the turn's controller has nothing to abort yet, and review caught
    // that a stalled preflight against an unhealthy server then hung the whole
    // turn here: outside the try, with `busy` set and no way to clear it. The
    // race gives the wait an exit; the stray GET resolves into nothing, and an
    // unknown baseline is the answer this already knows how to handle.
    // Cancelling it properly would mean threading a signal through `request`,
    // whose in-flight GET sharing hands one promise to several callers — one
    // caller's Stop must not abort another component's read.
    const totalBefore = totalSceneRef.current === id
      ? totalRef.current
      : await Promise.race([
          api.getScene(cid, id, { limit: 1 })
            .then((s) => s.total ?? s.messages.length)
            .catch(() => null),   // unknown — treated as unverifiable below
          settleOn(controller.signal, null),
        ]);
    let acc = "";
    // Three separate questions, and none of them is "did the promise resolve".
    // `finished`: a `done` frame arrived, which the backend sends only after
    // finalize has persisted. `errored`: an error frame arrived, so the backend
    // ran its own handler before sending it. Review caught that a proxy closing
    // the body cleanly before `done` resolves `streamPost` normally, which used
    // to count as a landed turn while the reply was still in the flush.
    let finished = false;
    let errored = false;
    // Whether this stream answered with a roll proposal rather than narration.
    // A director turn that did is a turn whose note was SPENT, even though the
    // transcript did not grow -- see the recovery condition below.
    let proposed = false;
    // Set below, once the transcript has been re-read: this turn finished but
    // wrote nothing, so it did not land however cleanly it ended.
    let producedNothing = false;
    let unreached = false;  // the request never reached the server
    // The server answered with a status instead of a stream. A fourth question,
    // because it is the one outcome that is *complete* on arrival: `streamPost`
    // throws a non-2xx before any body exists, so there is no stream that was
    // cut short, no partial in flight and nothing for the flush poll to wait on.
    let refused = false;
    try {
      await start((e) => {
        if (e.delta) {
          acc += e.delta;
          setStreaming(acc);
        } else if (e.run) {
          // The leading frame confirms the run id, so Stop can skip the
          // discovery round trip. It is a shortcut, not the mechanism: the
          // attempt id above already names this turn.
          if (runRef.current?.attempt === attempt) runRef.current.id = e.run.id;
          registry.attach(cid, id, e.run.id);
        } else if (e.error) {
          fail(e.error, !ephemeral && !(rerolling && acc.trim().length > 0));
          errored = true;
          // The post is gone from the transcript, so the composer has to give
          // the player their words back. Otherwise a failed send destroys what
          // they typed and Retry cannot help — it calls /retry, which has no
          // prompt of its own and 400s on a scene with nothing else in it (#95).
          if (e.error.post_returned) onPromptUnstored?.();        } else if (e.proposal) {
          // Live from the stream, so it outranks any read still in flight.
          proposed = true;
          setProposalNow({ id: e.proposal.id, status: "pending", payload: e.proposal, resolution: null });
        } else if (e.done) {
          finished = true;
          // A turn that worked is as much news about the connection as one
          // that failed, and only in one direction does the shell find out on
          // its own: `fail` announces, so a Retry that succeeds without leaving
          // the page would otherwise leave the dot red until an unrelated
          // navigation. Gated on having reported a provider failure, so an
          // ordinary turn does not re-read the config every time (#146).
          if (sawProviderFailure.current) {
            sawProviderFailure.current = false;
            invalidateConfigCache();
            configChanged();
          }
        }
      }, controller.signal, attempt, (index) => {
        // The WIRE index, so a resume asks for one past what was really read.
        // Counting the events this callback's sibling surfaced would undercount
        // by every heartbeat and replay text mid-reply.
        if (runRef.current?.id) registry.consume(runRef.current.id, index);
      });
    } catch (err: any) {
      // A cancel is the user getting what they asked for, so it raises no error
      // banner. `done` is parsed before the body reports EOF and Stop stays live
      // until it does, so a press in that gap aborts a turn that is already
      // written — `finished` below is what keeps that from being refunded as a
      // cancellation and spending its response override twice (#95).
      //
      // `fail`, not `setError`: a failure carries whether GENERATING is the
      // recovery it wants, and a reroll that already landed a partial does not
      // want one — Retry would append past the reply it just parked.
      // `!ephemeral`, for the reason the other `retryable: false` sites give
      // (Codex review, round 3): the banner's Retry GENERATES, down `/retry`,
      // which regenerates from the stored transcript. A director note is in no
      // transcript, so Retry would quietly produce an undirected continuation
      // — or, in a scene with nothing else, fail with "nothing to retry". The
      // note is back in the composer by then, and Send is its retry.
      if (!isAbortError(err)) fail(err, !ephemeral && !(rerolling && acc.trim().length > 0));
      // Nothing reached the server, so nothing was stored — the same position
      // the player is in after a rollback, and the same remedy. Review caught
      // that Stop pressed during connection setup, or a server that is simply
      // down, lost the prompt exactly as the rollback did: the composer was
      // cleared, and the refresh below finds no post to restore it from. The
      // response is the line, because `post_chat` appends before returning one.
      // Deliberately not restoring here. `beforeResponse` says no response
      // arrived, which is not the same as the request never arriving: the
      // server can append the post and then have the abort beat its headers
      // back to the browser. Deciding now would put text in the composer that
      // is also in the transcript, and the next Send would duplicate it. The
      // refresh below settles it (review, #95).
      if (err?.beforeResponse) unreached = true;
      // A refusal reaches the server but may still leave nothing behind: every
      // 4xx `post_chat` raises comes from a check that runs *before* the post is
      // appended, so the prompt was cleared out of a composer and stored
      // nowhere — the same position a rollback leaves the player in. Not
      // assumed, though: a 500 out of `build_messages` lands here too, and that
      // one raises *after* the append. The transcript settles which, exactly as
      // it does for `unreached` (review, #95).
      else if (err instanceof ApiError) refused = true;
    } finally {
      // The outcome is established, however it ended: a `done` frame, an error
      // frame the backend chose to send, or a refusal. Recovery is for the
      // sends whose outcome nobody ever learned, so anything that got this far
      // with an answer stops being one of those. A stream that was cut without
      // an answer deliberately does NOT settle -- that is the case the registry
      // exists for, and it is left for the adoption pass to resolve.
      if (finished || errored || refused) registry.settle(cid, id);
      abortRef.current = null;
      // AHEAD OF EVERY RELEASE BELOW, `busy` included. Only a Stop sets this,
      // so an ordinary turn is unaffected and keeps releasing as early as it
      // always has. On a Stop it is the difference between the button lying and
      // not: the cancel route answers only once the run is really terminal --
      // after its abort hook has written the partial and freed the exclusion
      // key -- and `send` gates on `busy` alone, so clearing that first let the
      // player fire a turn the backend then refused with `run_in_flight`. The
      // streamed text stays on screen for the wait, which is the honest picture:
      // it is still being stopped.

      // Set by the branch below that could not confirm the Stop. A Stop
      // the server never heard is not a stopped turn, and the releases
      // under the guard would say it was.
      let unstopped = false;
      const stopped = !!cancelRef.current;
      if (cancelRef.current) {
        const pending = cancelRef.current;
        cancelRef.current = null;
        // An unconfirmed cancel must not read as a finished one: `stopRun`
        // rethrows when it could not reach the server, and settling anyway
        // would put Send back over a run that is still generating. Reported,
        // not swallowed -- the player pressed a button and it did not work.
        try {
          await pending;
          // A CONFIRMED stop is an established outcome. The player asked for
          // it and the server said the run is terminal, so there is nothing
          // for recovery to resolve -- and leaving it pending would have the
          // adoption pass re-litigate a turn the player already ended.
          registry.settle(cid, id);
        } catch {
          // The player pressed a button and it did not work. Reported,
          // not swallowed -- and NOT released: the backend run may still
          // be generating and still holding the scene.
          unstopped = true;
          setError({ text: "could not reach the server to stop this turn — it "
                           + "may still be generating", retryable: false });
        }
      }
      // THE STREAM DIED WITH NO ANSWER, AND THIS TAB IS STILL HERE. Mount and
      // `visibilitychange` are the only adoption triggers, and neither fires in
      // this case: the component stayed mounted and visible while the response
      // reset before its leading frame. The server accepted the POST and a run
      // is generating -- so both of the answers below are wrong. Treating it as
      // a failure restores the prompt and the player sends again, landing the
      // turn twice against a run that was always fine; leaving it locked strands
      // the scene until a remount.
      //
      // So ask, by attempt, before concluding anything. A run means attach to
      // it and skip the whole teardown, because the turn is still ours.
      // `abortRef` is already cleared above, which is what lets `attachToRun`
      // take the view back.
      //
      // And a lookup that FAILED is not an answer of "no run". The socket
      // dying and this request failing are the same outage, so of every call
      // in this file these two are the likeliest to fail together -- and
      // reading that as "no run" runs the teardown, unlocks the composer, and
      // lets the next Send overwrite the registry's one pending entry for this
      // scene while the original may still be generating. Held instead, like
      // an unconfirmed Stop: the composer stays locked, the send stays
      // pending, and `adoptPendingRun` resolves it when the tab next comes
      // forward.
      let adopted = false;
      let undiscoverable = false;
      if (!unstopped && !stopped && !finished && !errored && !refused) {
        try {
          const live = (await api.findRun(cid, id, attempt)).run;
          if (live && live.state === "running") {
            adopted = true;
            void attachToRun(cid, id, live.id);
          }
        } catch {
          undiscoverable = true;
        }
      }
      // NOT a `return` -- this is a `finally`, and returning from one
      // discards whatever the block was already returning or throwing. The
      // rest of the teardown is skipped by a guard instead.
      //
      // `abortRef` above is cleared either way: the socket really is gone,
      // and leaving it set would block the adoption pass, which is what
      // resolves this if the run ends on its own. Everything below --
      // `busy`, the streamed preview, `runRef` -- is skipped when the Stop
      // went unconfirmed, so Stop is still there to press and still knows
      // what to press about.
      if (!unstopped && !adopted && !undiscoverable) {
        // Beside `abortRef`, and load-bearing for the same reason: left set, a
        // Stop pressed while the NEXT turn is still connecting would address this
        // one -- terminal by then, so the route would answer politely and the
        // live turn would keep generating with nothing to stop it.
        runRef.current = null;
        setStreaming("");
        setBusy(false);
        // NOT released with `busy`. Review caught that clearing it here unlocks
        // the scene while `on_abort` may still be writing to it: the poll below
        // exists precisely because the backend's shielded flush lands seconds
        // after the socket died, and a rename in that gap moves the file out from
        // under the very write the poll is waiting for. The lock is released at
        // the bottom of this block instead, once there is nothing left to land.
        // the reply is persisted as per-speaker posts — re-fetch to show them
        // (selectScene also bumps ctxKey and refreshes the player name)
        //
        // Guarded, because `setBusy(false)` above already re-enabled Send and this
        // fetch is an await: the player can start the next turn while it is in
        // flight, and the stale response would then apply `setMessages` over that
        // turn's optimistic post and `setStreaming("")` over its live preview.
        // Review caught this one on the immediate refresh after it had already
        // been fixed on the polling ones — the mechanism was sitting right here.
        //
        // Only `abortRef`, unlike the poll's predicate — and deliberately so,
        // even though it means this refresh can install the turn's scene over
        // one the reader has since opened. That pull-back is load-bearing: a
        // turn that failed parks the player's words under ITS scene, and the
        // composer is one shared box, so the view has to return to that scene
        // or the recovered prompt is either invisible or shown against the
        // wrong transcript ("a recovered prompt is never shown against the
        // scene the player moved to").
        //
        // Adding a scene check here was tried and reverted: it silently broke
        // prompt recovery, which is the one thing in this file that guards text
        // existing nowhere else. The divergence it would have prevented is
        // instead made harmless where it does damage — `saveEdit` refuses to
        // write while the transcript on screen belongs to another scene.
        //
        // Its failure is caught rather than thrown, because it is the *same*
        // failure the turn just had: a POST that never reached the server usually
        // means this GET will not either. Letting it escape from a `finally`
        // skipped the restoration below and replaced the original error on the
        // way out, so the one case that most needs the player's words back was
        // the one case that dropped them (review, #95).
        let seen = -1;
        let refreshed = false;
        try {
          seen = await selectScene(id, () => !abortRef.current);
          refreshed = true;
        } catch (err: any) {
          // Keep whatever the turn itself reported; say something if it reported
          // nothing, since the view is now showing a transcript it could not
          // confirm (a cancel raises no banner of its own).
          // Built here rather than through `fail`, which overwrites: the rule is
          // to keep whatever the turn reported. Retryable — this is the failure
          // path of a *generation*, so generating again is the right recovery.
          setError((cur) => cur ?? { text: err?.detail ?? String(err), retryable: true });
        }
        // Anything that ended without `done` may have left a partial the backend
        // is still flushing — a cancel, or a body cut short. An error frame is
        // NOT one of those: the backend ran its handler before sending it, so the
        // refresh above already sees whatever it wrote.
        //
        // Not gated on having seen text, either. Gating on `acc` was wrong and
        // review caught it: what reached the client is not what the backend has to
        // flush. `FenceWatcher.feed` returns "" for the whole of a roll fence and
        // withholds anything that might yet become one, so a reply that opens with
        // a fence streams nothing at all while the server still persists a
        // proposal — invisible until some later refresh under the old gate.
        // Now the transcript has answered it: no growth means the post never
        // landed, so the prompt exists nowhere and the composer has to have it
        // back. `seen < 0` is `selectScene` bowing out to a newer owner — it did
        // not look, so it cannot say.
        //
        // And when the transcript cannot answer — the refresh failed, or this
        // scene's length before the turn was never established — restore anyway.
        // Both mean the same thing: nothing proves the post landed. Erring the
        // other way risks a duplicate the player can see and delete; erring this
        // way destroys text that exists in no other place.
        //
        // Both outcomes that can leave the post unwritten ask the same question,
        // so they share the answer: the request that never arrived, and the one
        // the server refused. `nothingLanded` is the transcript saying it, out
        // loud — not merely the absence of evidence that it did.
        //
        // `seen < 0` belongs in `unverifiable`, and review caught that it was not
        // there. It is `selectScene` retiring its own read because a newer owner
        // took the view — the comment above already calls that "it did not look,
        // so it cannot say", which is the definition of unverifiable, but the code
        // said otherwise: the await did not throw, so `refreshed` was true, and a
        // prompt that genuinely never landed went unrestored because the read that
        // would have proved it was thrown away.
        const unverifiable = !refreshed || totalBefore === null || seen < 0;
        const nothingLanded = !unverifiable && seen <= totalBefore;
        // A stream that started and then stopped without either frame is the
        // third way to end up with no post, and review caught it as the one the
        // client had no answer for: the backend rolls the post back *before* it
        // yields the error frame, so a connection dropped in between leaves a
        // rollback that happened and a client that was never told. Nothing is set
        // — not errored, not unreached, not refused — and the poll cannot help,
        // because it watches for growth and a rollback only ever shrinks.
        //
        // Only on positive proof, unlike the two above. Headers arrived, which
        // for a chat means `post_chat` already appended; so an unverifiable
        // refresh here means the post is most likely sitting in the transcript,
        // and restoring on a guess would duplicate it. `nothingLanded` is the
        // transcript saying the rollback ran.
        const interrupted = !finished && !errored && !unreached && !refused;
        // Codex review (round 2, P1). `errored` is excluded above, because for
        // an ordinary post the error frame already recovers — the backend sets
        // `post_returned` and the handler acts on it mid-stream. An ephemeral
        // turn has no post to return, so that flag is NEVER set for one and
        // the error frame recovers nothing; a provider that dies before any
        // narration therefore destroyed the note. The transcript is the only
        // witness left, and for an ephemeral turn it is a clean one:
        // `nothingLanded` means the generation left no reply, with no post of
        // its own to confuse the count. Safe to add only here — with
        // `post_returned` unreachable for these turns, this cannot double up
        // with the in-stream recovery.
        const diedWithNothing = ephemeral && errored && nothingLanded;
        // A provider can finish CLEANLY and write nothing: an empty safety
        // response is a shape the backend supports, and it still ends the
        // stream with `done` (Codex review). `landed` is then true, so none of
        // the branches above fires and an ephemeral turn's note -- already
        // cleared out of the composer -- would exist nowhere at all, with no
        // reply to show for it either.
        //
        // `!proposed` is the exception that keeps this honest: a director turn
        // that answered with a roll proposal instead of narration DID produce
        // something, and its note was spent doing so. The transcript not
        // growing is not "nothing happened" in that case.
        const finishedWithNothing = ephemeral && finished && !errored
          && nothingLanded && !proposed;
        // Recorded for the RETURN as well as the recovery (Codex review). This
        // turn reached the provider and came back with nothing at all, so
        // reporting it as landed hands the caller a lie with a cost: `send`
        // spends the one-shot response-length override on it, and the override
        // was promised to the next REPLY. The note is being handed back for
        // another attempt; the knob chosen for that attempt goes with it.
        producedNothing = finishedWithNothing;
        if (((unreached || refused) && (unverifiable || nothingLanded))
            || (interrupted && nothingLanded) || diedWithNothing
            || finishedWithNothing) {
          onPromptUnstored?.();
        }
        // Nothing to wait for when nothing on the server can produce a partial: a
        // refusal never started a stream, and a request that verifiably never
        // arrived never made a turn.
        //
        // `unreached` alone is not that proof, and review caught it standing in
        // for it. It says no *response* came back, which the server can do having
        // already appended the post and begun generating — and growth in the
        // refresh proves exactly that happened. So the poll is skipped only when
        // the transcript confirms the turn does not exist; an unverified guess
        // costs a few reads, while being wrong the other way loses the partial.
        //
        // The poll needs a length to watch for growth past, and a refresh that
        // failed did not produce one. Fall back to this scene's pre-turn length:
        // still the right question (did the flush land?), just measured from
        // where the turn started. `-1` only when even that is unknown, which
        // makes the first successful read count as growth — the poll refreshes
        // once and stops, which is what an unmeasurable scene can honestly do.
        if (!finished && !errored && !refused && !(unreached && nothingLanded)) {
          await awaitFlushedPartial(id, seen >= 0 ? seen : (totalBefore ?? -1));
        }
        // Now nothing else can write to this scene through this turn, so its file
        // is free to move again — unless a newer turn has claimed the lock in the
        // meantime. `busy` was cleared before the poll, so that is a real race and
        // clearing unconditionally would unlock the scene the *new* turn is
        // streaming into. Same token idiom as `windowTokenRef`.
        if (streamTokenRef.current === streamToken) setStreamingId(null);
        // Ask the server whether this turn was the one that makes the scene's
        // running summary due (#85). Deliberately NOT awaited: the player's next
        // send must never queue behind a summarization, which is the whole
        // meaning of "non-blocking" here. Sent without `force`, so the decision —
        // and the cost — stay on the server; an ordinary turn answers
        // `refreshed: false` having reached no provider.
        //
        // Every rejection is swallowed. A missing key, a dead provider, a busy
        // store: none of them is a reason to put a banner over a turn that landed,
        // and the panel's own Refresh button reports the failure when the player
        // actually asks for one.
        //
        // The `ctxKey` bump is guarded on the reader still being here, like every
        // other post-await write in this function: a summary written for the scene
        // they just left must not re-read the panel for the scene they are on.
        // `seen` is how long the transcript was when this turn finished, and it
        // is passed as the fold's boundary: `setStreamingId(null)` above has
        // already released the scene, so the player can send again before this
        // request reaches the server, and a fold that swallowed that unanswered
        // post would keep the reply out of the summary until another threshold.
        // A `seen` of -1 is a read that was retired rather than one that saw an
        // empty transcript, so it is no boundary at all; `askAfterPost`
        // declines it rather than falling back to an unbounded fold.
        askAfterPost(id, seen);
      }
    }
    // Landed means the backend said so, not that the promise resolved -- and
    // not that it produced anything: an ephemeral turn that finished cleanly
    // with no reply is being recovered, not completed.
    return finished && !errored && !producedNothing;
  }

  async function send() {
    if (busy || rolling || renamesInFlight) return;
    // A new prompt supersedes a failed reroll: whatever Retry would have
    // repeated, the player has moved on from it.
    rerollToRetryRef.current = null;
    // a new turn supersedes any pending proposal durably on the backend —
    // clear the chip optimistically rather than wait for the re-fetch. Ordered,
    // so a read issued before this send cannot put the chip back afterwards.
    setProposalNow(null);
    const content = input.trim();
    let id = activeId;
    if (!id) {
      if (!content) return;
      id = (await api.createScene(cid)).id;
      await loadScenes();
      // The reader left while the scene was being created. Everything below
      // this point writes to the VIEW — it clears the composer, appends the
      // prompt optimistically, and streams deltas into it — and none of it is
      // campaign-scoped, so carrying on would render this turn into the
      // campaign they moved to and leave the polluted transcript there (the
      // refresh that would replace it is refused for being the wrong campaign).
      //
      // An earlier version of this guard covered only the adopt-and-navigate
      // just below, on the reasoning that the turn "posts to `cid`, so the
      // write lands where the player typed it". True of the write, and beside
      // the point for the eight lines after it (codex review).
      //
      // So the turn does not start. The scene stays — created, empty, in the
      // campaign it belongs to — and the composer is not cleared, so the words
      // are still there to send. Abandoning an empty scene is a smaller cost
      // than either losing what was typed or showing it in the wrong campaign.
      if (cidRef.current !== cid) return;
      setActive(id);
      // Same pairing as the rename: an id adopted without the reader going
      // anywhere, so the URL follows it in the same batch with `replace`
      // (there was no scene here to go Back to). Without this the resolver
      // sees `activeId` moved while the URL still names no scene, and bounces
      // the turn onto whatever the list happens to sort first.
      navigate(sceneUrl(cid, id), { replace: true });
    }
    setInput("");
    // the player just spoke: put them back at the tail even if they had
    // scrolled up into older history to re-read something
    atBottomRef.current = true;
    // Ephemeral turns are never stored: a director note — asked for in Direct
    // mode, or the only kind of turn an offscreen scene has — or, in any scene
    // and either mode, an empty send meaning "next NPC round".
    //
    // The note is recovered in-session but NOT made durable, and that asymmetry
    // is the whole of it (Codex review, P1).
    //
    // `onPromptUnstored` is safe here: it fires on what the STREAM said --
    // refused, unreachable, or interrupted over a transcript that did not grow
    // -- and for an ephemeral turn "did not grow" means no reply landed, with
    // no post of its own to confuse the measurement. A refusal is the case that
    // matters: nothing generated, nothing to duplicate, and the composer was
    // cleared on send, so without this the player's words exist nowhere.
    //
    // `recoverableText` is deliberately NOT passed, and that is not timidity.
    // The durable half settles by asking the server `attemptState().retained`,
    // and an ephemeral turn writes no attempt record at all -- so it answers
    // false for a note that WORKED exactly as loudly as for one that died. Arm
    // it and every successful director turn hands its note back afterwards.
    // In-session recovery can tell those apart; the durable one cannot.
    // Identity is the ownership token (Codex review, round 3). `runStream`
    // unlocks the composer before it returns -- the flush poll it awaits runs
    // after `setBusy(false)` -- so a SECOND turn can be under way while this
    // `send` is still awaiting, and an unconditional clear in the finally below
    // would wipe that turn's note off the screen for the rest of its
    // generation. Cleared only if what is on screen is still this one.
    //
    // Assigned BEFORE the branch, so an ordinary post says so too (round 4).
    // With the clear made ownership-safe, a stale note otherwise outlived its
    // own generation and rendered beside the NEXT turn's reply, claiming to
    // have steered something it never saw. Every send now states what note is
    // in force, and for a Speak post that statement is "none".
    const note = directing && content ? { cid, sid: id, text: content } : null;
    if (directing || !content) {
      try {
        const landed = await runStream(id,
          (onEvent, signal, attempt, onIndex) =>
            api.chat(cid, id!, content, onEvent, pendingResponse ?? undefined,
                     signal, attempt, onIndex, directing),
          // An empty send has nothing to give back — it is the "next NPC round"
          // fast path, not words anyone typed.
          content ? () => recoverPrompt(cid, id, content, true) : undefined,
          false, "", true, note);
        if (landed) setPendingResponse(null);
      } finally {
        setDirectorNote((cur) => (cur === note ? null : cur));
      }
      return;
    }
    showOptimistically((m) => [...m, { role: "user", content }]);
    // The prompt is not in the transcript — either the backend took it back, or
    // the request never got there — so this text now exists nowhere: the
    // composer was cleared on send and the refresh dropped the optimistic copy.
    // Put it back rather than lose what the player wrote, but never over
    // something they have started typing since.
    //
    // From the callback rather than after the await: `runStream` does not
    // return until its flush poll has finished, and the player should not have
    // to wait that out to get their own words back.
    // Through `recoverPrompt`, which decides *where* the words go: the composer
    // if the player is still on this scene, parked under `id` if they have
    // moved on. The appending rule that used to live here moved with it — see
    // `giveBackPrompt`. `id`, captured here, is the scene this prompt was
    // written for, and it stays right however long the recovery takes.
    const landed = await runStream(id,
      (onEvent, signal, attempt, onIndex) =>
        // The trailing `false` is `director`: this is the branch that POSTS, so
        // it is the one send that can never be a note.
        api.chat(cid, id!, content, onEvent, pendingResponse ?? undefined,
                 signal, attempt, onIndex, false),
      () => recoverPrompt(cid, id!, content), false,
      // The words the player typed, held until the outcome proves them durable.
      content);
    if (landed) setPendingResponse(null);
  }

  async function saveEdit() {
    // `rolling` for the same reason the swipe buttons read it: an edit saved
    // while a swap is in flight carries the index and text of the message on
    // screen, which the promotion is in the middle of replacing. Whichever
    // write loses the race silently discards the other, and the two refreshes
    // can land out of order on top of that.
    if (!editing || !activeId || rolling) return;
    // The transcript ON SCREEN must be the active scene's own. `editing.index`
    // is an index into what is rendered and `activeId` is where it would be
    // written, and the two describe different scenes for exactly one read
    // whenever a background refresh installs the turn's scene over the one the
    // reader opened: the resolver then corrects `activeId` back to the URL's
    // scene, and until that read lands the old messages are still on screen.
    // Saving in that window overwrites an unrelated message of the scene now
    // active (codex review, P1).
    //
    // `transcriptIsActive` is the named form of exactly this fact.
    if (!transcriptIsActive) return;
    await api.editMessage(cid, activeId, editing.index, editing.text);
    setEditing(null);
    const seen = await selectScene(activeId);
    // An edit does not APPEND a post, so this never crosses the threshold by
    // count — but it rewrites text the stored summary already covers, which
    // moves `covered_digest` and makes that summary describe words no longer
    // in the transcript. The server reads that as a from-scratch refold being
    // due, so the ask is what turns a summary the panel can only flag as stale
    // back into a correct one. Review caught that leaving it out meant the
    // stale flag sat there until some later *generated* turn asked.
    askAfterPost(activeId, seen);   // #85, #84
  }

  /** Retcon: the same rewrite `saveEdit` makes, plus the reversal of what this
   *  scene's absorb wrote (#78).
   *
   *  Two buttons rather than one, because they are different intentions and
   *  only one of them is destructive. A plain Save fixes the words; a retcon
   *  says the scene did not happen that way, which makes the recorded summary,
   *  the write-backs and the beats descriptions of a transcript that is no
   *  longer there — so they are put back and the scene becomes unfinished,
   *  ready to be absorbed again.
   *
   *  Guarded exactly as `saveEdit` and `deleteMessagesFrom` are, and for their
   *  reasons: `rolling`, because a swap in flight is rewriting the posts this
   *  index addresses, and `transcriptIsActive`, because during a background
   *  refresh the messages on screen can belong to a scene other than `activeId`.
   */
  async function saveRetcon() {
    if (!editing || !activeId || rolling || !transcriptIsActive) return;
    if (!window.confirm(
      "Retcon this post?\n\n" +
      (activeDone
        ? "This scene has been absorbed. Every recorded write-back is put back, its " +
          "chronicle record, beats, change history and citations go, and it becomes " +
          "unfinished so it can be absorbed again — which is how the re-extraction " +
          "gets to disagree with what later scenes recorded.\n\n"
        : "") +
      "The posts after it are left alone. Replay them from the gutter if they no " +
      "longer follow.")) return;
    const release = takeRollLatch(activeId);
    let report;
    try {
      report = await api.retconMessage(cid, activeId, editing.index, editing.text);
    } catch (err: any) {
      fail(err, false);            // not retryable: Retry re-runs the CHAT retry
      return;
    } finally {
      release();
    }
    setEditing(null);
    // The rail carries `done`, which an absorbed scene has just lost.
    await loadScenes();
    const seen = await selectScene(activeId);
    setCtxKey((k) => k + 1);       // beats and citations left the ledger
    askAfterPost(activeId, seen);   // #85/#84, exactly as `saveEdit` asks
    // What the transcript afterwards cannot show, in the same two clauses the
    // cut reports and for the same reason — plus the one thing specific to a
    // retcon: which scenes were played after this one, and so can disagree with
    // what a re-absorb now extracts.
    const notes = [];
    if (report.refused.length) {
      notes.push(
        `${report.refused.length} record${report.refused.length === 1 ? "" : "s"} ` +
        "could not be put back: " + report.refused.map((r) => r.label).join(", "));
    }
    if (report.failed.length) {
      notes.push("some continuity records could not be cleaned up (" +
                 report.failed.join(", ") + ") — check them by hand");
    }
    if (report.later.length) {
      notes.push(`${report.later.length} later scene${report.later.length === 1 ? "" : "s"} ` +
                 "already recorded state from here on — absorb this scene again to see " +
                 "which of its findings they contradict");
    }
    if (notes.length) setError({ retryable: false, text: notes.join(". ") });
  }

  /** Cascade post-delete: this post and everything after it (#75).
   *
   *  The confirmation is not a formality. A cascade delete reaches records the
   *  transcript does not show — the chronicle entry, plot and commitment beats,
   *  the change history — and puts back what the scene's absorb wrote. So the
   *  prompt states the post count, names what a finished scene additionally
   *  loses, and names the two things that survive on purpose (the roll log is
   *  append-only; the timeline carries no scene attribution). Guessing at any of
   *  that after the fact is not something the player can do from what is left.
   *
   *  Guarded exactly as `saveEdit` is, and for the same reasons: `rolling`,
   *  because a swap in flight is rewriting the posts this index addresses, and
   *  `transcriptIsActive`, because during a background refresh the messages on
   *  screen can belong to a different scene than `activeId` — which here would
   *  cut an unrelated scene at an index taken from this one.
   */
  async function deleteMessagesFrom(index: number) {
    if (!activeId || rolling || !transcriptIsActive) return;
    // The window is always the transcript's TAIL, so this is the real total
    // however few posts have been paged in.
    const n = firstIndex + messages.length - index;
    const ask = [
      n === 1 ? "Delete this post?"
              : `Delete this post and the ${n - 1} after it?`,
      activeDone
        ? "This scene has been absorbed. Its chronicle record, plot and commitment " +
          "beats, change history and citations go with it, and it becomes unfinished " +
          "so it can be absorbed again. Every recorded write-back is put back, except " +
          "where the record has changed since — those are reported and left alone."
        : "This cannot be undone.",
      "Dice rolls stay in the roll log and the timeline is not rewritten.",
    ].join("\n\n");
    if (!window.confirm(ask)) return;
    setEditing(null);
    // The same latch reroll, retry and the two roll paths take, and taken for
    // the reason it exists: it holds until an unrelated request has settled, and
    // every one of those rewrites this transcript. Without it a second click
    // lands a second cut against indices the first is in the middle of moving.
    // Confirm is modal so it cannot be double-fired, but the await below is not,
    // and this is the least reversible thing in the app.
    const release = takeRollLatch(activeId);
    let report;
    try {
      report = await api.deleteMessagesFrom(cid, activeId, index);
    } catch (err: any) {
      // Not retryable: Retry re-runs the CHAT retry, which would generate a
      // reply into the scene the player was trying to cut back.
      fail(err, false);
      return;
    } finally {
      // Released before the refreshes rather than after: they are reads, and
      // holding a latch that greys out the gutter across them buys nothing.
      release();
    }
    // The rail carries `done`, which an absorbed scene has just lost — without
    // this the composer stays hidden and the row still reads as absorbed.
    await loadScenes();
    const seen = await selectScene(activeId);
    // The ledger resolves every thread, commitment and fact against the scenes,
    // and this has just removed beats from it — same reason `deleteScene` bumps.
    setCtxKey((k) => k + 1);
    // The stored fold covers a prefix that may no longer exist. The server
    // reports that as stale on its own (`at > total`, or a moved digest); this
    // is what turns the flag back into a correct summary.
    askAfterPost(activeId, seen);   // #85, #84
    // What the shortened transcript cannot show. Two distinct outcomes, and
    // reported as two clauses because conflating them would mislead:
    //
    //  - `refused` — records that could not be put back and still hold what the
    //    deleted scene gave them. Both a record something else wrote to since
    //    (the reversal declines rather than discarding that later change) and a
    //    kind with no reversal at all (a character or lore entry the scene
    //    CREATED, the one a player is most likely to meet) land here.
    //    Deliberately not claiming which: naming the records is what lets the
    //    player go and look, and asserting the wrong reason is worse than none.
    //  - `failed` — cleanup that could not run, typically a store file edited by
    //    hand into something that will not parse. The cut still happened, so
    //    saying nothing would leave stale continuity looking authoritative.
    const notes = [];
    if (report.refused.length) {
      notes.push(
        `${report.refused.length} record${report.refused.length === 1 ? "" : "s"} ` +
        `could not be put back and ${report.refused.length === 1 ? "was" : "were"} ` +
        "left as it stands: " + report.refused.map((r) => r.label).join(", "));
    }
    if (report.failed.length) {
      notes.push("some continuity records could not be cleaned up (" +
                 report.failed.join(", ") + ") — check them by hand");
    }
    if (notes.length) setError({ retryable: false, text: notes.join(". ") });
  }

  // What the error banner's Retry re-runs. `/retry` continues from the
  // transcript as it stands, which is the right redo for a chat or a retry —
  // but not for a reroll. A failed reroll now puts the old reply back (#95), so
  // "try that again" through `/retry` would generate a *continuation* of the
  // reply the player asked to replace, and silently drop their guidance with
  // it. Review caught this as a consequence of the restore: before it, the
  // reply was gone, so `/retry` happened to do the right thing by accident.
  //
  // Reset on every turn that lands, so Retry only ever repeats the operation
  // that actually failed.
  //
  // Carries the scene it belongs to, because the error banner does not: a
  // reroll that fails in one scene leaves both the banner and this ref standing
  // while the player moves to another, and Retry there would have rerolled the
  // *new* scene — replacing a reply nobody asked to replace, with guidance
  // written for a different scene (review, #95). A switch also clears the
  // banner now, so the button is usually gone; the scene check is what makes
  // that airtight rather than merely likely.
  //
  // The route rides along with the guidance (#77) for the same reason: "try
  // that again" means the same reroll, and a Retry that quietly dropped the
  // local endpoint the player chose would answer with the campaign's model
  // while looking like a repeat.
  const rerollToRetryRef =
    useRef<{ sid: string; guidance: string; route: RerollRoute } | null>(null);

  async function retry() {
    if (!activeId || busy || rolling || renamesInFlight) return;
    const again = rerollToRetryRef.current;
    if (again && again.sid === activeId) return void await reroll(again);
    const landed = await runStream(activeId, (onEvent, signal, attempt, onIndex) =>
      pendingResponse
        ? api.retry(cid, activeId, onEvent, pendingResponse, signal, attempt, onIndex)
        : api.retry(cid, activeId, onEvent, undefined, signal, attempt, onIndex));
    if (landed) setPendingResponse(null);
  }

  async function reroll(repeat?: { guidance: string; route: RerollRoute }) {
    if (!activeId || busy || rolling || renamesInFlight) return;
    // The affordance and the optimistic removal below both read the RENDERED
    // messages while `api.regenerate` targets `activeId`, so during the
    // divergence window this replaces a reply of the active scene that the
    // reader was never shown. Guarded here as well as on `canReroll`, because
    // `retry` reaches this function without going past the button.
    if (!transcriptIsActive) return;
    const guidance = repeat?.guidance ?? (rerollPrompt ?? "").trim();
    const route = repeat?.route ?? rerollRoute;
    setRerollPrompt(null);
    rerollToRetryRef.current = { sid: activeId, guidance, route };
    // one turn is a run of assistant posts — drop the whole trailing run, but
    // keep any trailing transition lines, which the backend also preserves
    showOptimistically((m) => {
      let end = m.length;
      const kept: Message[] = [];
      while (end > 0 && m[end - 1].speaker === TRANSITION_SPEAKER) kept.unshift(m[--end]);
      while (end > 0 && m[end - 1].role === "assistant") end--;
      return [...m.slice(0, end), ...kept];
    });
    // One object, and `api.regenerate` drops the empty fields — what used to be
    // a branch per combination of the two it could carry, which the route
    // override would have doubled again. The pending one-shot response override
    // rides regenerate here for the reason retry carries it: it is the promise
    // both make.
    const body = { guidance, response: pendingResponse ?? undefined, ...route };
    const landed = await runStream(activeId, (onEvent, signal, attempt, onIndex) =>
      // `rerolling`: a reroll that fails wants a different recovery offered
      // than a failed send does — see `runStream`.
      api.regenerate(cid, activeId!, onEvent, body, signal, attempt, onIndex),
      undefined, true);
    if (landed) {
      setPendingResponse(null);
      rerollToRetryRef.current = null;   // it worked; Retry is a plain retry again
    }
  }

  // Cycling is a swap, not a delete: whatever is on screen becomes an alternate
  // in the slot the chosen one vacates, so ‹ and › tour the set indefinitely.
  async function pickAlternate(index: number) {
    // `editing` as well as `rolling` — the guard runs both ways. An edit form
    // open on another post of this generation survives the swap: `selectScene`
    // refreshes the messages without clearing it, so the form rebinds to the
    // *promoted* variant's message at the same absolute index and Save would
    // overwrite it with a draft of the text that variant replaced.
    // `sceneLocked`, not just `busy`: `busy` clears the moment the socket dies
    // while the post-cancel flush is still writing to this scene, and a swap in
    // that window races the abort hook — landing first it loses the cancelled
    // partial the hook was about to persist, landing second it parks it. Same
    // rule every other transcript mutation outside `runStream` reads (#95).
    if (!activeId || busy || rolling || editing || sceneLocked) return;
    const sid = activeId;
    // Send the variant's id, taken from the same snapshot the index came from.
    // Retention shifts every index when a full set gains a take, so a position
    // this tab computed before another tab rerolled would name different text
    // by the time it arrives; an id that is no longer in the set 404s instead.
    const target = alternates.alternates[index];
    if (!target) return;
    // The in-flight-store-mutation latch doRoll uses, and every control that
    // must not fire alongside one already reads it. Without it a double-click
    // computes both indices from the same `alternates.active` snapshot — two ‹
    // clicks step back once — and the two selectScene refreshes can land out of
    // order. Taken for `sid`, so leaving the scene releases the controls there
    // rather than waiting on a request that no longer concerns the reader.
    const releaseLatch = takeRollLatch(sid);
    // Only the scene the swap was for, and only while it is still the one on
    // screen: the user may have moved on while the POST was in flight, and
    // refreshing would navigate them back to a scene they left. The campaign is
    // half of "the same scene" — `selectScene` is this render's closure, so in
    // campaign B it would fetch B's sid out of A.
    const stillHere = () => cidRef.current === cid && activeIdRef.current === sid;
    try {
      try {
        await api.pickAlternate(cid, sid, target.id);
      } catch (err: any) {
        // Scoped like every other write this component reports. Switching
        // scenes clears the banner deliberately — one scene's failure must not
        // offer its Retry against another — and a swap rejecting after the move
        // would put it straight back under a scene it has nothing to do with.
        if (!stillHere()) return;
        fail(err, false);
        // `promote` removes the live run and then appends the chosen one. If
        // the append is what failed, the slot is now EMPTY — the sidecar still
        // holds both variants, but the transcript does not, and leaving the old
        // reply on screen means every message index below it is a lie and an
        // edit saves over the wrong post. Re-read so the empty slot and the
        // control that refills it are what the reader sees.
        await selectScene(sid).catch(() => {});
        return;
      }
      // Past here the swap has COMMITTED, and nothing that fails below may be
      // reported as a failed swap: it would deny a change that happened, and
      // the recovery it implies — do it again — is not the one that helps.
      //
      // The swap is also the recovery the player chose over Retry. Leaving a
      // failed reroll's banner up offers a Retry that regenerates over the take
      // just selected, with the guidance that failed, undoing the choice.
      if (rerollToRetryRef.current?.sid === sid) rerollToRetryRef.current = null;
      // Everything below writes shared view state, so it waits on the scope
      // check. The destination scene is deliberately usable while this POST is
      // open — that is the point of the scoped latch — so it can have raised a
      // banner of its own, and clearing it here would take a failure that has
      // nothing to do with this swap.
      if (!stillHere()) return;
      setError(null);
      // The backend retires the pending decision as part of the swap, so the
      // chip goes now rather than at the next read: left up it adjudicates
      // narration no longer on screen, and its 409 surfaces a Retry that
      // generates. Ordered before the refresh below, whose own proposal read is
      // issued after this and so puts the chip back if the swap was a no-op.
      setProposalNow(null);
      let seen = -1;
      try {
        seen = await selectScene(sid);
      } catch (err: any) {
        // Asked again, not only before the first read. `selectScene` calls
        // `setActive`, so a retry issued after the reader moved on does not
        // merely refresh the wrong scene — it navigates them back to it, or in
        // a campaign switch installs the old campaign's scene id in the new
        // view. The banner below is scoped for the same reason.
        if (!stillHere()) return;
        // One more attempt before giving up — the read that failed is the only
        // thing between the reader and a transcript that is already correct on
        // disk.
        try {
          seen = await selectScene(sid);
        } catch {
          if (!stillHere()) return;
          // Still stale. Drop the readiness the counter and the ‹/› control
          // depend on, so nothing acts on a transcript the set no longer
          // indexes, and say what actually happened.
          setLoaded(null);
          setError({ text: `The take was swapped, but the scene could not be re-read: `
                           + (err?.detail ?? String(err)), retryable: false });
        }
      }
      // A swap replaces the reply in place: the transcript is no longer than it
      // was, but the words the stored summary covers are a take the player
      // rejected. Same reasoning as `saveEdit` — the moved digest is what makes
      // the refold due, and without this ask the panel would show a summary of
      // the discarded take, flagged stale, until the next generated turn.
      // Scoped like every other write above, and skipped when neither re-read
      // produced a boundary.
      if (stillHere()) askAfterPost(sid, seen);   // #85, #84
    } finally {
      releaseLatch();
    }
  }

  // `sceneLocked`, not just `busy`: a roll appends a transcript line, and a
  // reroll cancelled before its first token is at that moment waiting to put
  // the reply it deleted back. `restore_trailing_assistant_run` steps over
  // trailing *transitions* only — a roll deliberately blocks it, because the
  // line has to stay in lockstep with rolls.json — so a roll landing in the
  // flush window makes the restore refuse and the rerolled reply is gone for
  // good. The backend cannot rescue that one; only not racing it can (#95).
  async function doRoll() {
    if (!activeId || busy || sceneLocked || rolling || !rollForm) return;
    const notation = rollForm.notation.trim();
    if (!notation) return;
    const releaseLatch = takeRollLatch(activeId);
    try {
      await api.roll(cid, activeId, notation, rollForm.label.trim() || undefined);
      setRollForm(null);
      const seen = await selectScene(activeId);
      // The length the re-read saw is this write's boundary, for the reason the
      // turn loop passes one: nothing holds the scene once this returns.
      askAfterPost(activeId, seen);   // a roll is a post too (#85, #84)
    } catch (err: any) {
      setRollForm({ ...rollForm, error: err.detail ?? String(err) });
    } finally {
      releaseLatch();
    }
  }

  // Unbinding the pack removes the dice button, which is the popover's only
  // way in and its only way out -- left open it would be a form nothing can
  // dismiss, offering a Check whose actor list is now empty.
  //
  // `=== false`, not `!== true`: `null` means "not known yet", and treating
  // that as unbound threw away a half-typed roll every time the read ran --
  // including a save that left the SAME pack bound (Codex review).
  useEffect(() => {
    if (moduleBound === false) setRollForm(null);
  }, [moduleBound]);

  function toggleRollPop() {
    if (rollForm) {
      setRollForm(null);
      return;
    }
    checksFetched.current = false; // each popover session re-fetches once
    setRollForm({ mode: "dice", notation: "", label: "", error: null,
                  checkActor: "", checkId: "", difficulty: "", modifier: 0 });
  }

  // the actor/check lists load lazily on first entering Check mode — a
  // dice-only popover session never fires the request
  function enterCheckMode() {
    if (!rollForm) return;
    setRollForm({ ...rollForm, mode: "check" });
    if (activeId && !checksFetched.current) {
      checksFetched.current = true;
      api.getSceneChecks(cid, activeId).then((r) => setCheckActors(r.actors)).catch(() => setCheckActors([]));
    }
  }

  async function doCheck() {   // same window, same loss — see doRoll
    if (!activeId || busy || sceneLocked || rolling || !rollForm) return;
    if (!rollForm.checkActor || !rollForm.checkId) return;
    const releaseLatch = takeRollLatch(activeId);
    try {
      const body: { check: string; actor: string; difficulty?: number; modifier: number } = {
        check: rollForm.checkId, actor: rollForm.checkActor, modifier: rollForm.modifier,
      };
      if (rollForm.difficulty !== "") body.difficulty = rollForm.difficulty;
      await api.rollCheck(cid, activeId, body);
      setRollForm(null);
      const seen = await selectScene(activeId);
      askAfterPost(activeId, seen);   // as is a check (#85, #84)
    } catch (err: any) {
      setRollForm({ ...rollForm, error: err.detail ?? String(err) });
    } finally {
      releaseLatch();
    }
  }

  // runStream's finally always re-fetches the scene (selectScene), which
  // also re-fetches the proposal record — so a stale/lost-CAS 409 from
  // resolveProposal surfaces the server's current record without extra
  // plumbing here; clearing eagerly avoids a stale chip lingering mid-stream.
  async function resolve(body: ResolveBody) {
    if (!activeId) return;
    // Before the eager clear, not after. `runStream` refuses while a rename is
    // in flight, and review caught that this cleared the chip first — so the
    // resolution was never sent and the roll became unreachable, since a rename
    // on another rail row only re-lists scenes and never refreshes this one.
    // Bailing here leaves the chip exactly as it was, which is the honest state:
    // the decision is still pending (#95).
    if (renamesInFlight) return;
    setProposalNow(null);
    await runStream(activeId, (onEvent, signal, attempt, onIndex) =>
      api.resolveProposal(cid, activeId!, body, onEvent, signal, attempt, onIndex));
  }

  // No-op unless a turn is in flight; the abort rejects the fetch, `runStream`
  // recognises it and unwinds without an error banner.
  //
  // Closing the connection is NO LONGER the cancel, and that is the whole point
  // of detaching a turn from its request: a locked phone must not kill the
  // generation, so neither does an abort. The run has to be told separately, or
  // Stop would leave the provider generating, spending, and persisting a reply
  // the player explicitly stopped -- while holding the scene against the next
  // send. The abort still runs, and first, because that is what unwinds this
  // view; the cancel is a request the backend answers on its own schedule.
  //
  // The handle is read BEFORE the abort: `runStream`'s `finally` clears it.
  function cancelTurn() {
    const handle = runRef.current;
    abortRef.current?.abort();
    // Stored, not awaited here: this is an onClick and the button must not hang
    // on it. `runStream`'s teardown awaits it before releasing the scene, which
    // is where the waiting belongs -- see `cancelRef`.
    if (handle) cancelRef.current = stopRun(handle);
  }

  async function stopRun(
    handle: { cid: string; sid: string; attempt: string; id: string | null },
  ) {
    // Every rejection is swallowed. A run that landed a moment before the press
    // is already terminal and the route says so without complaining; anything
    // else is a network failure on a request whose whole purpose was to stop
    // work that is about to stop anyway, and a banner for it would be noise.
    const { cid: from, sid, attempt } = handle;
    try {
      // No leading frame yet: cancel by ATTEMPT rather than looking the run up.
      // A lookup is a one-shot question, and the POST being stopped may have
      // been accepted and then blocked in the server's setup -- so "no run for
      // this attempt" does not mean nothing will happen, and the reservation
      // that follows would detach a turn the player had already stopped. The
      // attempt route is remembered by the server and consumed when the
      // reservation happens, which is the only version of this without a race.
      // BY ID WHEN THERE IS ONE, on every round and not just the first. An
      // adopted run is recorded with `attempt: ""` -- the provider was
      // recreated empty, so this client never knew the id the backend reserved
      // under -- and re-asking by that empty attempt matches nothing, so every
      // poll answered null and a cancellation that was completing normally was
      // reported unconfirmed. The id is the better handle wherever it exists;
      // the attempt is what covers the window before any frame named one.
      const ask = () => (handle.id
        ? api.cancelRun(from, sid, handle.id)
        : api.cancelAttempt(from, sid, attempt));
      let state = (await ask()).run?.state;
      // The server's wait is bounded (30s) and can answer while the run is
      // still unwinding. Taking that as "it is over" is what re-enabled Send
      // over a run that still held the scene, which is the whole failure this
      // wait exists to prevent -- so ask again rather than assume.
      //
      // NO RUN AT ALL IS THE SAME ANSWER, and review caught that it was read
      // as the opposite. A precancel recorded before the POST reserved returns
      // `run: null`, which is not "there is nothing to stop" -- it is
      // "cancellation is pending": the original request is still in its
      // synchronous setup and will consume that record when it reserves.
      // Resolving on it settled the attempt and unlocked the composer while
      // that was still to happen, so the next Send raced the very request it
      // had just cancelled.
      //
      // A short wait between rounds only on that branch: an unwinding run is
      // already being waited on server-side, but a pending one needs the other
      // request to get somewhere first, and four immediate re-asks would all
      // arrive before it did.
      for (let tries = 0;
           (state === "running" || state === undefined) && tries < STOP_POLLS;
           tries++) {
        if (state === undefined) await new Promise((r) => setTimeout(r, 100));
        state = (await ask()).run?.state;
      }
      if (state === "running" || state === undefined) {
        // Out of rounds with the run still going, or still not reserved.
        // Resolving here would settle the turn on a guess: the backend still
        // holds the scene (or is about to), so Send comes back and the next
        // turn is refused. Exhaustion is an UNCONFIRMED cancellation, which is
        // the same answer as one that never arrived -- and it takes the same
        // path below.
        throw new Error("the run did not stop");
      }
    } catch {
      // The cancel never reached the server -- connectivity dropped as the
      // phone was backgrounded, most likely. Swallowing this and settling would
      // be the worst answer available: aborting the subscriber does NOT stop a
      // detached run, so the provider keeps spending, persists a reply the
      // player stopped, and holds the scene against the next send. Re-thrown so
      // the teardown leaves the turn unsettled and the composer keeps saying
      // Stop, which is the truth: it has not been stopped yet.
      throw new Error("the stop request could not be delivered");
    }
  }

  // A scene already in the chronicle comes back as 409 "already_absorbed" rather
  // than silently re-absorbing: lore edits append and plot movements add a beat,
  // so a second pass duplicates both (#235). Confirm, then retry with force.
  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  // rerolling regenerates the trailing assistant run; a run that reaches the
  // first message is the opener and is not rerollable. Trailing scene
  // transitions are stepped OVER (they are not model output and the backend
  // preserves them), so the reroll affordance hangs off the last generated
  // message beneath them rather than off the true last message. A manual dice
  // roll (backend tags it speaker "Roll") still blocks reroll outright — its
  // entry lives on in rolls.json and the line must stay in lockstep.
  // window-relative; `rerollAt` below is the absolute index the rendered posts
  // carry. Both the run and the transitions above it live in the loaded tail,
  // so a windowed transcript reaches the same answer as a whole one.
  const rerollIndex = (() => {
    let i = messages.length - 1;
    while (i >= 0 && messages[i].speaker === TRANSITION_SPEAKER) i--;
    return i;
  })();
  const canReroll = transcriptIsActive &&
    rerollIndex >= 0 &&
    messages[rerollIndex].role === "assistant" &&
    messages[rerollIndex].speaker !== ROLL_SPEAKER &&
    // "not the opener" — the regenerate route refuses an all-assistant
    // transcript, so this has to be true of the WHOLE scene, not the window.
    // Only the server can say (an offscreen scene stores no user posts however
    // long it runs, so "there is history above" proves nothing); the local
    // scan is the fallback for an unwindowed read, which holds everything.
    (hasUserPost ?? messages.some((x) => x.role === "user"));
  const rerollAt = rerollIndex < 0 ? -1 : firstIndex + rerollIndex;

  // The swipe control hangs off the same message as Reroll — they act on the
  // same generation, so it renders against `rerollAt` (absolute), not
  // `rerollIndex` (window-relative), like every other per-post affordance.
  // Shown with two or more variants to tour, and also with a single one while
  // nothing is live: that is a reroll whose stream died, and the one affordance
  // that puts the lost reply back.
  // Scope gate: a set fetched for another campaign or scene is not this one's,
  // whatever order the responses came back in.
  const altCount =
    alternates.cid === cid && alternates.sid === activeId
    && loaded?.cid === cid && loaded?.sid === alternates.sid
    && loaded.token === alternates.window
      ? alternates.alternates.length : 0;
  const canSwipe = rerollIndex >= 0 && (altCount > 1 || (altCount > 0 && alternates.active === null));
  // Wraps, so ‹/› tour the set. With the slot empty, ‹ reaches for the newest
  // variant and › for the oldest, which is what "one step off nothing" means.
  const stepAlternate = (delta: number) =>
    alternates.active === null
      ? (delta > 0 ? 0 : altCount - 1)
      : (alternates.active + delta + altCount) % altCount;
  // What the counter says on hover. The guidance leads: while cycling, "which
  // instruction produced this take" is the thing the preview cannot tell you,
  // and it is the only place the stored hint is visible at all.
  //
  // The model follows it whenever the variant carries one (#77). Not filtered
  // against the campaign's current model, deliberately: the control only
  // appears while there are takes to tour, which is precisely when "which model
  // produced this one" is the question — and a take generated somewhere else
  // reads no differently from one that was not, so this is the only place it
  // can be said. Every variant archived before the override existed, and every
  // one reconciled out of the transcript, stores "" and adds no line.
  const altTitle = (() => {
    if (alternates.active === null) return "no alternate is showing — the last reroll didn't land";
    const variant = alternates.alternates[alternates.active];
    if (!variant) return undefined;
    const lines = [
      variant.guidance ? `Guided: ${variant.guidance}` : "",
      variant.model ? `Model: ${variant.model}` : "",
    ].filter(Boolean);
    return lines.length ? `${lines.join("\n")}\n\n${variant.preview}` : variant.preview;
  })();

  // The transition tag is internal drift metadata, never a speaker: a
  // transition renders as the unlabelled narration it was before the tag
  // existed, so tagged and pre-existing untagged transitions look the same.
  const speakerOf = (m: Message) =>
    (m.speaker === TRANSITION_SPEAKER ? undefined : m.speaker)
    ?? (m.role === "user" ? playerName ?? labels.user : labels.assistant);

  // A speaker label names a cast member if it matches exactly (case-insensitive)
  // or is a word-boundary prefix of exactly one name — "Winifred" is Winifred
  // Vance; an ambiguous or mid-word label matches no one. Mirrors the
  // backend's scenes.match_name so role attribution and plates agree.
  function matchActor(speaker: string): Actor | undefined {
    const low = speaker.trim().toLowerCase();
    if (!low) return undefined;
    const exact = cast.filter((a) => a.name.toLowerCase() === low);
    if (exact.length) return exact.length === 1 ? exact[0] : undefined;
    const prefixed = cast.filter((a) => {
      const n = a.name.toLowerCase();
      return n.startsWith(low) && !/[\p{L}\p{N}]/u.test(n[low.length] ?? "");
    });
    return prefixed.length === 1 ? prefixed[0] : undefined;
  }

  // One highlight, two sources: the dossier's provenance popover and the
  // review's "find in transcript". They can never both be open — the review
  // replaces the play view outright — so one piece of state serves both.
  const citedNeedle = (citedQuote || review.reviewQuote).trim().toLowerCase();
  const isCited = (text: string) =>
    citedNeedle !== "" && text.toLowerCase().includes(citedNeedle);

  // consecutive messages by the same speaker form one run under a single plate
  type Run = { speaker: string; pc: boolean; actor: Actor | undefined;
               posts: { m: Message; index: number }[] };
  const runs: Run[] = [];
  messages.forEach((m, i) => {
    const index = firstIndex + i; // absolute: what edit/reroll address it by
    const speaker = speakerOf(m);
    const last = runs[runs.length - 1];
    if (last && last.speaker === speaker) {
      last.posts.push({ m, index });
      return;
    }
    const actor = matchActor(speaker);
    runs.push({ speaker, pc: actor ? actor.role === "player" : m.role === "user",
                actor, posts: [{ m, index }] });
  });

  function plateAvatar(run: Run): string | null {
    if (!run.actor) return null;
    // Either actor kind: a speaker plate used to fall back to initials for
    // every PC, because PCs had no images to point at (#219).
    const { kind, id } = run.actor;
    const ver = roster.find((r) => r.kind === kind && r.id === id)?.version;
    return ver ? api.actorImageUrl({ kind: "campaign", id: cid }, kind, id, ver, "avatar") : null;
  }

  /** Whose images the picker offers for a post in `run` (#376).
   *
   *  The speaker decides: an actor post offers that actor's art at the version
   *  the roster has locked — the version that spoke, and the same one
   *  `plateAvatar` draws the portrait from — and a narrator post offers the
   *  campaign's own library, because "Grimoire" is nobody and has no record to
   *  hang art off. An actor the roster cannot place gets the library too: that
   *  is the one scope that is always there, and an empty picker would be worse
   *  than a general one. */
  function pickerTarget(run: Run): PickerTarget {
    const ver = run.actor
      && roster.find((r) => r.kind === run.actor!.kind && r.id === run.actor!.id)?.version;
    return run.actor && ver
      ? { kind: run.actor.kind, id: run.actor.id, version: ver, name: run.speaker }
      : { kind: "campaign", name: run.speaker };
  }

  /** Put one image reference into the post, by opening it for editing with the
   *  markdown already in the buffer.
   *
   *  Which is what keeps this off any new persistence path: the existing Save
   *  — and Retcon, and Cancel — is the save, with exactly the semantics it has
   *  always had. Appended after a blank line rather than spliced at a cursor,
   *  so it can never land mid-sentence, and the textarea it lands in is right
   *  there to move it. */
  function insertImage(index: number, markdown: string) {
    setPicking(null);
    // The same two conditions `saveEdit` refuses on, and for its reasons.
    // `transcriptIsActive`: the picker is a modal that outlives a background
    // refresh, and `index` addresses the transcript ON SCREEN — if the refresh
    // has installed another scene's posts under it, this would open an
    // unrelated message for editing with an image already pasted into it.
    // `rolling`: the button that opened the picker is disabled while a turn is
    // in flight, so the picker must not be a way around that.
    if (!transcriptIsActive || rolling) return;
    const post = messages[index - firstIndex];
    if (!post) return;   // scrolled out of the loaded window; nothing to edit
    const current = (editing?.index === index ? editing.text : post.content).trimEnd();
    setEditing({ index, text: current ? `${current}\n\n${markdown}` : markdown });
  }

  const sceneTitle = scenes.find((s) => s.id === activeId)?.title ?? "";
  /** The scene the open review was absorbed FROM, which is not necessarily the
   *  one selected: a review survives a scene switch, and `saveAbsorb` already
   *  commits against `absorbSid` for that reason. Naming `activeId`'s title
   *  here would tell the reviewer they are judging a scene they are not. */
  const absorbTitle = review.absorbSid
    ? scenes.find((s) => s.id === review.absorbSid)?.title || review.absorbSid
    : sceneTitle;
  // The global status bar can't work this out for itself: the router hands it
  // a cid, not a name, and which scene is open is state that lives only here.
  // During a review it names the scene being absorbed, not the one selected —
  // the scene is off the screen, so the pill is the only thing still saying it.
  usePublishShellContext(
    name ? { campaign: name, scene: absorb ? `Absorbing ${absorbTitle}` : sceneTitle } : null);

  // The model THIS campaign's turns run on, which since #142 is not necessarily
  // the active connection's: a campaign can route its scene turns elsewhere, and
  // so can the global routing page. Resolved here because the header has a
  // pathname and no cid, and answered by the same bundle the picker renders --
  // one place decides what a route means. A failed read publishes nothing and
  // the header keeps naming the active connection, which is what it did before.
  const [sceneModel, setSceneModel] = useState<string | null>(null);
  const [sceneReady, setSceneReady] = useState<boolean | null>(null);
  const [routingRev, setRoutingRev] = useState(0);
  useEffect(() => onConfigChanged(() => setRoutingRev((n) => n + 1)), []);
  useEffect(() => {
    let live = true;
    // Cleared first: switching campaigns must not leave the previous one's
    // model in the header for as long as this read takes.
    setSceneModel(null);
    setSceneReady(null);
    api.getCampaignRouting(cid).then((r) => {
      if (!live) return;
      const id = r.effective.scene || r.active_connection_id;
      const conn = r.connections.find((c) => c.id === id);
      setSceneModel(conn?.model || null);
      // Only when this campaign routes somewhere OTHER than the active
      // connection. Otherwise the global read behind the header already
      // describes the same connection, and publishing a second opinion about
      // it would just be a slower copy that can disagree while it loads.
      setSceneReady(id && id !== r.active_connection_id ? !!conn?.usable : null);
    }).catch(() => { if (live) { setSceneModel(null); setSceneReady(null); } });
    return () => { live = false; };
  }, [cid, routingRev]);
  usePublishSceneModel(sceneModel, sceneReady);

  // The scene's own keyboard (#193). Every binding here mirrors a control that
  // is on screen, and carries the same condition that control is disabled by —
  // a shortcut that could do what no button offers is a second implementation
  // of the guards, and the guards are the reason a turn cannot be sent twice.
  // The functions guard themselves as well; this is what keeps the KEY inert,
  // so nothing fires silently and the help sheet can say why.
  //
  // Nothing is bound bare that spends money without a further confirmation:
  // `r` opens the reroll's guidance box exactly as ↻ does, and End scene —
  // which absorbs a scene into the chronicle and cannot be undone by pressing
  // the key again — is deliberately not bound at all.
  useHotkeys([
    {
      keys: "mod+enter", label: "Send · continue the turn", group: "IN THIS SCENE",
      // The one binding that belongs inside prose: the composer is where the
      // reader IS. (The textarea's own Enter still handles the caret being in
      // it, and prevents the default, so this cannot send the same keystroke
      // twice.)
      whileTyping: true,
      // Nothing here about which popover is open, and that is the point. An
      // inline Enter handler that answers the key now PREVENTS it (the reroll
      // box, both dice inputs, both rename boxes), which is the signal the
      // dispatcher has always read, so a chord typed into one of them never
      // reaches this. A list of open widgets here would have to be extended by
      // whoever adds the next inline handler -- and would silently go back to
      // double-firing when they didn't (PR #400 review).
      // `activeDone` is not a disabled control but a REPLACED one: an absorbed
      // scene has its summary written and its changes applied, so the composer
      // goes entirely and a post added now would sit outside the record taken
      // of it. A chord that ignored that would put one there -- the transcript
      // and the chronicle disagreeing, with nothing to say which is right
      // (PR #400 review).
      enabled: !absorb && !activeDone && !busy && !rolling && !renamesInFlight,
      run: () => void send(),
    },
    {
      keys: "n", label: "New scene", group: "IN THIS SCENE",
      enabled: !absorb, run: newScene,
    },
    {
      keys: "r", label: "Reroll the last reply", group: "IN THIS SCENE",
      // `editing` as well, the guard `pickAlternate` takes and for its reason:
      // a reroll replaces the post at this index, and an edit form open over
      // it rebinds to the REPLACEMENT, so Save would overwrite the reply that
      // was just generated. The ↻ button is hidden while its own post is being
      // edited, so a key that fired anyway would reach past it (PR #400
      // review).
      enabled: !absorb && !busy && !rolling && !editing && canReroll,
      run: () => setRerollPrompt(""),
    },
    {
      keys: "t", label: "Retry the turn that failed", group: "IN THIS SCENE",
      // Only while the banner is up. `retry()` with nothing to recover is a
      // fresh generation the reader did not ask for, and a bare letter is far
      // too cheap to press for that.
      enabled: !absorb && !!error?.retryable && !busy && !rolling && !renamesInFlight,
      run: () => void retry(),
    },
  ]);

  // The column is one swap zone: cast, or one actor. `columnMode` is derived
  // from `selectedActor` rather than stored beside it, so the two can never
  // disagree about which is showing.
  const column = selectedActor
    ? <DossierColumn cid={cid} casefile={casefile} provenance={provenance}
                     onHoverQuote={setCitedQuote} onGoToTurn={goToQuote}
                     onBack={closeActor}
                     onOpenActor={(kind, id) => setDrawer(
                       { type: "actor", kind: kind as "characters" | "pcs", id })}
                     onRemove={removeSelectedActor} busy={sceneLocked} />
    : <CastColumn cid={cid} sid={activeId ?? ""} hasPosts={messages.length > 0} refreshKey={ctxKey}
                  cast={cast} roster={roster} briefing={briefing}
                  onOpen={openActor}
                  // A confirmed enter or leave writes a transition line into the
                  // transcript as well as moving the cast, so the scene is
                  // re-read whole rather than the cast alone.
                  onCastChanged={() => { if (activeId) refreshAndAsk(activeId); }} />;

  return (
    <PageShell
      className={absorb ? "review" : "play"}
      columnLabel={absorb ? "Proposals" : selectedActor ? "Dossier" : "Cast and continuity"}
      column={absorb ? <ReviewColumn review={review} /> : column}
      footer={absorb
        ? <button className="column-primary" onClick={review.approveAllCited}>
            Approve all cited
          </button>
        : <Conditions cid={cid} worldName={worldName} location={sceneLocation}
                      datetime={dt} weather={weather} />}
    >
      <div className="workspace">
        {/* Export, Ledger, Calendar, Cover, End scene: all of them act on the
            scene, and a review is not the scene. The bar is replaced by one
            that names what is being judged, so the header still says where you
            are once the transcript stops being the thing on screen. */}
        {absorb ? (
        <div className="scene-actions review-actions">
          <span className="eyebrow">{name}</span>
          {/* Rename lives here during a review, and is the one scene control
              that does. The rest act on a transcript this screen is not
              showing, but a scene's id is derived from its title — so a rename
              mints a new id, and `renameScene` migrates the open review's
              `absorbSid` and every staged edit's scene ref onto it. Dropping
              the control with the scene head would leave that migration with no
              way to be reached, and take with it the only chance to fix a title
              at the moment you are reading what the scene actually contained. */}
          {renamingScene ? (
            <input className="row-rename scene-rename" aria-label="Rename scene" autoFocus
                   value={renamingScene.title}
                   onChange={(e) => setRenamingScene({ id: renamingScene.id, title: e.target.value })}
                   onKeyDown={(e) => {
                     // See the reroll box: an inline Enter is claimed, not shared.
                     if (e.key === "Enter") { e.preventDefault(); commitSceneRename(); }
                     if (e.key === "Escape") setRenamingScene(null);
                   }} />
          ) : (
            <button className="review-absorbing" aria-label="Rename scene"
                    title="Rename this scene"
                    onClick={() => setRenamingScene({ id: review.absorbSid ?? activeId ?? "",
                                                      title: absorbTitle })}>
              Absorbing {absorbTitle}
            </button>
          )}
          <span className="header-spacer" />
          {/* The audit's verdict, and deliberately not a count of proposals:
              the column already carries "18 edits · 14 approved · 1 rejected ·
              3 left" and the footer the tally again. This is the one fact about
              the absorb that neither of them states. */}
          <span className="review-audit">Audit {absorb.mechanics.status}</span>
        </div>
        ) : focus ? null : (
        <div className="scene-actions">
          <span className="eyebrow">{name}</span>
          <span className="header-spacer" />
          <details className="sub-export-menu">
            <summary className="sub-export">Export</summary>
            <div className="sub-export-options">
              <a href={`/api/campaigns/${cid}/export.epub`} download>EPUB</a>
              <a href={`/api/campaigns/${cid}/export.md.zip`} download>Markdown</a>
              <a href={`/api/campaigns/${cid}/export.html`} download>HTML</a>
              <a href={`/api/campaigns/${cid}/export.txt`} download>Plain text</a>
              <a href={`/api/campaigns/${cid}/export.json`} download>JSON</a>
            </div>
          </details>
          {/* A link, not a toggle: the ledger is its own route now (4e). */}
          <Link className="scene-action" to={`/campaigns/${cid}/ledger`}>Ledger</Link>
          {/* Its other half (#198): the ledger is what is still open, this is
              what happened. Beside it because that is the pair. */}
          <Link className="scene-action" to={`/campaigns/${cid}/timeline`}>Timeline</Link>
          {/* What the campaign has cost, scene by scene, all-time (#153). Not a
              panel: a table of every scene a campaign has had is not something
              to read mid-turn, and the inspector's Cost section is where the
              scene in front of you is answered for. */}
          <Link className="scene-action" to={`/campaigns/${cid}/costs`}>Costs</Link>
          {/* The mechanics panel below binds the module; this is the cast-wide
              sheet coverage that binding produces (#201), which is a list and
              so is a route rather than a third drawer. */}
          <Link className="scene-action" to={`/campaigns/${cid}/sheets`}>Sheets</Link>
          <button className="scene-action" onClick={() => setShowChanges((v) => !v)}>
            {showChanges ? "Close" : "Changes"}
          </button>
          <button className="scene-action" onClick={() => setShowIncoming((v) => !v)}>
            {showIncoming ? "Close" : "World updates"}
          </button>
          <button className="scene-action" onClick={() => setShowMechanics((v) => !v)}>
            {showMechanics ? "Close" : "Mechanics"}
          </button>
          <button className="scene-action" onClick={() => setShowCalendar((v) => !v)}>Calendar</button>
          <button className="scene-action" onClick={() => setShowStyle((v) => !v)}>Response</button>
          <button className="scene-action" onClick={() => setShowCover((v) => !v)}>Cover</button>
          <button className="scene-action" onClick={newScene}>+ New scene</button>
          {/* `busy` is not the whole of "a turn can still write here": it clears
              when the socket dies, and the backend's shielded abort write lands
              seconds later — which is the window `streamingId` covers. Absorb
              inside it and the chronicle summarises a transcript the partial
              has not reached yet, then the partial lands underneath a scene
              already marked absorbed. That one does not come back: the review
              is committed against a transcript that no longer matches (#95). */}
          <button className="scene-action end" onClick={review.endScene}
                  disabled={!activeId || review.absorbing || busy || sceneLocked || rolling}>
            {review.absorbing ? "Ending…" : "End scene"}
          </button>
          {/* The way out of an absorb that is still running (#396). A review
              holds the scene against play for as long as it runs and
              `absorb_budget = 0` means nothing bounds that, so without this a
              wedged End scene locks the scene until the process restarts --
              and until the review lands there is no panel, and so no Cancel.
              The same argument the panel's Cancel already carries for a wedged
              retry, one step earlier. */}
          {review.absorbing && (
            <button className="scene-action" onClick={() => void review.stopAbsorb()}>
              Stop
            </button>
          )}
          {/* A review that is on disk and unusable (#396). The scene was played
              on after the absorb landed -- appended, cut, retconned -- and the
              stored review summarises posts that are no longer there. Saying so
              here is what lets the reader re-run instead of opening a panel
              whose save is going to be refused; the notice is dismissible
              because it is information, not a state to be stuck in. */}
          {review.staleReview && (
            <p className="field-hint" role="status">
              The scene changed after its review was prepared
              {" "}({review.staleReview.prepared_posts} → {review.staleReview.current_posts}
              {" "}posts). End the scene again to review what is there now.
              {" "}<button className="subtle" onClick={review.dismissStale}>Dismiss</button>
            </p>
          )}
        </div>
        )}
        {/* The scene rail is gone. It cost 220px of transcript on every turn of every scene
            to answer a question asked a few times a session, and it could not be
            searched. ⌘K lists the same scenes, costs nothing until asked, and
            covers the rest of the app besides. */}
        <section className="main">
          {/* A review REPLACES the scene; it does not sit on top of it (4c).
              The transcript, the composer, the scene's own actions and every
              panel they open are the play view, and a reviewer judging eighteen
              proposals is not playing — leaving them mounted underneath put the
              review at the top of a scroll that still ran on past it, and put
              End scene one mis-click from discarding every proposal already
              judged. The transcript is still on screen, as the third pane
              below: read-only, and there to check a quote against. */}
          {/* All three banners sit ABOVE that split, and are the only things
              that do. They are the page's, not the scene's: `error` is
              deliberately global and tagged with `from` because a review
              outlives a scene switch, and a scoped retry that fails inside the
              review reports through it — so hiding it during a review would
              swallow the failure of the one button the review offers. The
              budget (#153) is the page's for a plainer reason: it is the
              campaign's, and a review spends nothing of its own. */}
          {!ready && (
            <div className="banner">
              No LLM connection ready. <Link to="/config">Set one up in Config</Link>.
            </div>
          )}
          {/* The only one of the three a reader can dismiss: the other two
              describe something broken, while this one describes a decision
              they may well have made on purpose. Dismissing is per level, so
              spending past the cap says so again after the 80% warning was
              waved off. Below the connection notice and above the error, which
              is the order they matter in: nothing can be spent without a
              connection, and a turn that just failed outranks a running total. */}
          {budget && (budget.level === "warn" || budget.level === "over")
            && budgetSeen !== budget.level && (
            <div className="banner error-banner" role="alert">
              <span>
                {budget.level === "over" ? "Over budget: " : "Approaching budget: "}
                {money(budget.spent_usd ?? 0)} of {money(budget.limit_usd)} spent{" "}
                {budget.period === "total" ? "in total" : "this month"}
                {/* The figure is a floor whenever this is non-zero, and a
                    banner that hid that would be understating the very number
                    it exists to raise. */}
                {(budget.unpriced_calls ?? 0) > 0
                  && ` — ${budget.unpriced_calls} unpriced `
                     + `${budget.unpriced_calls === 1 ? "call" : "calls"} not counted`}
                {". "}
                {/* Where to act on it. The control is in the inspector's Cost
                    section, which is collapsed by default and so is exactly
                    what a reader given this banner cannot find — a warning
                    nobody can act on is the shape this whole view avoids. */}
                <span className="ctx-meta">Change it in the inspector’s Cost section.</span>
              </span>
              <button className="retry" onClick={() => setBudgetSeen(budget.level)}>
                Dismiss
              </button>
            </div>
          )}
          {error && (
            <div className="banner error-banner">
              {/* `?? text` for the raisers that composed a sentence rather than
                  catching something: `ErrorNote` renders a plain string
                  unchanged, so both kinds of banner go through one path. */}
              <span><ErrorNote err={error.err ?? error.text} /></span>
              {error.retryable && (
                <button className="retry" onClick={retry} disabled={busy || rolling}>
                  Retry
                </button>
              )}
            </div>
          )}
          {!absorb && (<>
          {/* Every one of these six is opened AND closed from the scene bar,
              which focus mode does not render — so one left open when focus
              starts would be a panel above the transcript with no control that
              can shut it. They keep their state and come back with the bar. The
              inspector below is deliberately not in this list: its toggle lives
              in the composer, which focus mode keeps. */}
          {!focus && showCalendar && (
            <div className="panel-slot">
              <CalendarConfig scope={{ kind: "campaign", id: cid }} />
            </div>
          )}
          {!focus && showMechanics && (
            <div className="panel-slot">
              {/* Guarded against a save that settles after the reader has moved
                  on: the callback this panel holds is the one it was handed, so
                  it can name a campaign that is no longer on screen. */}
              {/* No cid check on the callback. A save settling after the reader
                  moved on does fire a stale `onChanged`, but `readModuleBound`
                  keys off `liveCid`, so it reads and commits the campaign on
                  SCREEN -- harmless, and the correct answer. Filtering by the
                  saved cid would only skip a request MechanicsConfig's own
                  `load()` has already made anyway, while masking that keying in
                  any test (Codex review round 2). */}
              {/* `key` remounts it per campaign. Its own save is a PUT followed
                  by a re-read, both against the `cid` its render captured, and
                  every commit in that chain is unguarded -- so a save started in
                  one campaign and settling after a switch would write the old
                  campaign's module into the new campaign's editor, and the next
                  Save would then persist that selection to the wrong campaign.
                  A fresh instance has no state for the stale chain to land in
                  (Codex review round 2). */}
              <MechanicsConfig key={cid} cid={cid} onChanged={() => readModuleBound()} />
            </div>
          )}
          {!focus && showStyle && (
            <div className="panel-slot">
              <ResponsePresetPicker scope="campaign" cid={cid}
                                    onChanged={() => activeId && selectScene(activeId)} />
            </div>
          )}
          {!focus && showCover && (
            <div className="panel-slot">
              <CampaignCover cid={cid} />
            </div>
          )}
          {!focus && showChanges && <ChangesPanel cid={cid} />}
          {/* Keyed by cid for the reason MechanicsConfig is: this route keeps
              its instance across a campaign switch, so an unkeyed panel would
              show one campaign's pending list while another is on screen. */}
          {!focus && showIncoming && <IncomingReview key={cid} cid={cid} />}
          {review.editFailures.length > 0 && (
            <div className="mechanics-notice">
              <p>{review.editFailures.length} change{review.editFailures.length === 1 ? "" : "s"} did not apply</p>
              {review.editFailures.map((f, i) => (
                <p className="field-hint" key={i}>{f.label}: {f.reason} ({f.kind})</p>
              ))}
              <button className="subtle" onClick={review.dismissFailures}>Dismiss</button>
            </div>
          )}
          </>)}
          {absorb && <ReviewPanel review={review} />}
          {!absorb && (<>
          {activeId && messages.length === 0 && (
            <CastPanel
              cid={cid}
              sid={activeId}
              ready={ready}
              onSeeded={() => refreshAndAsk(activeId)}
              onSceneRenamed={sceneRenamed}
              initialPrompt={seedPrompt?.cid === cid && seedPrompt.sid === activeId
                               ? seedPrompt.prompt : undefined}
              pcless={activePcless}
              sceneLocked={sceneLocked}
              onRenaming={markRenaming}
            />
          )}
          {showInspector && activeId && (
            <div className="panel-slot">
              <SceneInspector cid={cid} sid={activeId} refreshKey={ctxKey}
                              onSceneChanged={() => selectScene(activeId)}
                              onSceneRenamed={sceneRenamed} pcless={activePcless}
                              sceneLocked={sceneLocked}
                              onRenaming={markRenaming}
                              posts={messages.length} />
            </div>
          )}
          {activeId && !focus && (
            <div className="scene-head">
              <div className="eyebrow">
                SCENE {sceneNumber(activeId, scenes.length)} · {messages.length}{" "}
                {messages.length === 1 ? "TURN" : "TURNS"}
              </div>
              {renamingScene ? (
                <input
                  className="row-rename scene-rename" aria-label="Rename scene" autoFocus
                  value={renamingScene.title}
                  onChange={(e) => setRenamingScene({ id: renamingScene.id, title: e.target.value })}
                  onKeyDown={(e) => {
                    // See the reroll box: an inline Enter is claimed, not shared.
                    if (e.key === "Enter") { e.preventDefault(); commitSceneRename(); }
                    if (e.key === "Escape") setRenamingScene(null);
                  }}
                />
              ) : (
                <h2 className="scene-title">
                  {sceneTitle}
                  {activePcless && <span className="chip on offscreen-badge">Offscreen</span>}
                </h2>
              )}
              {/* Rename and delete belong to the scene you are reading, and to
                  no other. They used to sit on every row of the rail, where a
                  ✕ was one mis-click from deleting a transcript you were not
                  even looking at. Locked while this scene is the one being
                  streamed into: renaming re-slugs the file, which moves it out
                  from under the write in flight (#95). */}
              <div className="row-actions">
                <button aria-label="Rename scene" disabled={sceneLocked}
                        title={sceneLocked ? LOCKED_WHILE_GENERATING : "Rename scene"}
                        onClick={() => setRenamingScene({ id: activeId, title: sceneTitle })}>✎</button>
                {/* Forking is not one of the two: it writes nothing here at
                    all, and is offered from this row because the scene you are
                    reading is the turn you would branch at. Locked with the
                    others while a turn is streaming — a copy taken mid-write
                    would carry half a reply. */}
                <button aria-label="Fork campaign at this scene" disabled={sceneLocked}
                        title={sceneLocked ? LOCKED_WHILE_GENERATING
                                           : "Fork the campaign at this scene"}
                        onClick={() => forkAtScene(activeId)}>⑂</button>
                <button aria-label="Delete scene" disabled={sceneLocked}
                        title={sceneLocked ? LOCKED_WHILE_GENERATING : "Delete scene"}
                        onClick={() => {
                          const meta = scenes.find((x) => x.id === activeId);
                          if (meta) deleteScene(meta);
                        }}>✕</button>
              </div>
            </div>
          )}
          <div className={"stream" + (colorQuotes ? " color-quotes" : "")} ref={streamRef}
               data-testid="stream" onScroll={onStreamScroll}>
            {firstIndex > 0 && (
              <div className="stream-older">
                <button className="subtle" onClick={loadOlder} disabled={loadingOlder}>
                  {loadingOlder ? "Loading…" : (() => {
                    const n = Math.min(PAGE_SIZE, firstIndex);
                    return `Load ${n} older post${n === 1 ? "" : "s"}`;
                  })()}
                </button>
              </div>
            )}
            {runs.map((run) => (
              <div className={"run" + (run.pc ? " pc" : "")} key={run.posts[0].index}>
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
                              onClick={() => openActor(run.actor!.kind, run.actor!.id)}>
                        <Portrait src={plateAvatar(run)} name={run.speaker} />
                      </button>
                      <button className="plate-name"
                              onClick={() => openActor(run.actor!.kind, run.actor!.id)}>
                        {run.speaker}
                      </button>
                    </>
                  ) : (
                    <>
                      <span className="plate-avatar"><Portrait src={null} name={run.speaker} /></span>
                      <span className="plate-name">{run.speaker}</span>
                    </>
                  )}
                  <span className="role-chip">{run.pc ? "pc" : "npc"}</span>
                </div>
                {run.posts.map(({ m, index }) => (
                  /* `.cited` marks the line a hovered citation was taken from.
                     Substring, for the reason `goToQuote` gives: the citation
                     records an excerpt, and there is no index to trust. */
                  <div className={`msg ${m.role}` + (isCited(m.content) ? " cited" : "")}
                       key={index}>
                    <span className="msg-gutter">
                      {editing?.index !== index && !busy && (
                        <span className="gutter-icons">
                          {index === rerollAt && canReroll && (
                            <button className="msg-edit" title="Reroll" aria-label="Reroll"
                                    disabled={rolling} onClick={() => {
                                      setRerollPrompt("");
                                      // Cleared on OPEN rather than on close, so
                                      // a popover dismissed with Escape and
                                      // reopened does not come back wearing the
                                      // route the player backed out of.
                                      setRerollRoute(NO_REROLL_ROUTE);
                                    }}>↻</button>
                          )}
                          {index === rerollAt && canSwipe && (
                            <span className="swipe-nav">
                              <button className="msg-edit" aria-label="Previous alternate"
                                      disabled={rolling || editing !== null || sceneLocked}
                                      onClick={() => pickAlternate(stepAlternate(-1))}>‹</button>
                              <span className="swipe-count" title={altTitle}>
                                {alternates.active === null ? "–" : alternates.active + 1}/{altCount}
                              </span>
                              <button className="msg-edit" aria-label="Next alternate"
                                      disabled={rolling || editing !== null || sceneLocked}
                                      onClick={() => pickAlternate(stepAlternate(1))}>›</button>
                            </span>
                          )}
                          {m.speaker !== ROLL_SPEAKER && transcriptIsActive && (
                            <button className="msg-edit" title="Edit message" aria-label={`Edit message ${index + 1}`}
                                    disabled={rolling}
                                    onClick={() => setEditing({ index, text: m.content })}>✎</button>
                          )}
                          {/* Refused on a dice-roll line for the same reason
                              Edit is: this rewrites the post, and that line's
                              text must stay in lockstep with an immutable
                              rolls.json entry. Everything else about it is
                              Edit — it opens the very same buffer, with the
                              reference already in it (#376). */}
                          {m.speaker !== ROLL_SPEAKER && transcriptIsActive && (
                            <button className="msg-edit" title="Insert an image"
                                    aria-label={`Insert an image into message ${index + 1}`}
                                    disabled={rolling}
                                    onClick={() => setPicking({ index, target: pickerTarget(run) })}>🖼</button>
                          )}
                          {/* Offered on a dice-roll line too, where Edit is not:
                              that line is refused because its text must stay in
                              lockstep with an immutable rolls.json entry, and a
                              cut removes the line rather than rewriting it. */}
                          {transcriptIsActive && (
                            <button className="msg-edit msg-cut" title="Delete this post and everything after it"
                                    aria-label={`Delete message ${index + 1} and everything after it`}
                                    disabled={rolling}
                                    onClick={() => deleteMessagesFrom(index)}>🗑</button>
                          )}
                          {/* Replay the turns AFTER this one (#79): the post
                              itself stands -- it is the one that was retconned
                              -- and everything past it is cut and re-run one
                              turn at a time. Offered only where there is
                              something after it to replay, and on a roll line
                              too: a cut span may contain one, and replaying it
                              re-posts the line while `rolls.json` keeps the
                              entry it names. */}
                          {transcriptIsActive && index < firstIndex + messages.length - 1 && (
                            <button className="msg-edit" title="Replay the turns after this post"
                                    aria-label={`Replay the turns after message ${index + 1}`}
                                    disabled={rolling}
                                    onClick={() => setReplayAt(index + 1)}>⏩</button>
                          )}
                        </span>
                      )}
                      {rerollPrompt !== null && !busy &&
                       index === rerollAt && canReroll && (
                        /* Escape-to-dismiss on the container is what the rule
                           below objects to, and it is the accessible choice
                           here rather than a lapse from it: the alternative is
                           a popover only one of its three controls can be
                           backed out of. */
                        // eslint-disable-next-line jsx-a11y/no-static-element-interactions
                        <span className="reroll-pop"
                              // On the popover, not on the guidance input it
                              // used to sit on: the route row (#77) added two
                              // more controls, and Escape backing out of one
                              // of three of them is worse than not offering it
                              // at all. Keydown bubbles from every child.
                              onKeyDown={(e) => {
                                // Both keys on the popover, so all three of its
                                // controls commit and dismiss alike. Enter used
                                // to work from the guidance input alone, which
                                // meant typing a model id and pressing Enter
                                // did nothing at all. `ModelCombobox` stops an
                                // Escape that is closing its own dropdown, so
                                // that one does not reach here.
                                if (e.key === "Escape") setRerollPrompt(null);
                                // `preventDefault` is what tells the shortcut
                                // dispatcher this keystroke is spoken for: ⌘⏎
                                // typed here means "reroll", not "send", and
                                // without it both fired (PR #400 review).
                                if (e.key === "Enter") { e.preventDefault(); reroll(); }
                              }}>
                          {/* Above the guidance, not beside it: this is where
                              the reroll goes, and the hint is what it says once
                              it gets there. Untouched, both halves are the
                              campaign's standing configuration. */}
                          <RerollRoutePicker value={rerollRoute} onChange={setRerollRoute} />
                          <span className="reroll-guide">
                            <input
                              autoFocus
                              placeholder="Guide the reroll (optional)…"
                              aria-label="Reroll guidance"
                              value={rerollPrompt}
                              onChange={(e) => setRerollPrompt(e.target.value)}
                            />
                            <button className="btn-chrome" onClick={() => reroll()} disabled={rolling}>Reroll ▸</button>
                          </span>
                        </span>
                      )}
                    </span>
                    <div className="msg-body">
                      {/* What this post cost to answer, over every reroll of it
                          (#153). On the player's own posts only: they are the
                          ones a generation was made FOR, and a chip on the
                          reply would double-count the same spend under the
                          text it paid for. Suppressed while the post is being
                          edited, where the row is a form and not a message. */}
                      {m.role === "user" && editing?.index !== index
                        && postChips?.[index] !== undefined && (
                        <PostCost bucket={postChips[index]} />
                      )}
                      {editing?.index === index ? (
                        <div className="msg-edit-form">
                          <textarea aria-label="Edit message" rows={4} value={editing.text}
                                    onChange={(e) => setEditing({ index, text: e.target.value })} />
                          <div className="form-actions">
                            <button className="subtle" onClick={() => setEditing(null)}>Cancel</button>
                            {/* Beside Save rather than instead of it: Save fixes
                                the words, Retcon says the scene did not happen
                                that way and takes back what it wrote (#78). */}
                            <button className="subtle" onClick={saveRetcon} disabled={rolling}
                                    title="Rewrite this post and take back what the scene recorded">
                              Retcon
                            </button>
                            <button className="primary" onClick={saveEdit} disabled={rolling}>Save</button>
                          </div>
                        </div>
                      ) : (
                        <RenderedMarkdown content={m.content} />
                      )}
                    </div>
                  </div>
                ))}
              </div>
            ))}
            {directorNote && busy
              && directorNote.cid === cid && directorNote.sid === activeId && (
              <div className="run director-note">
                <div className="msg assistant">
                  <span className="msg-gutter" />
                  <div className="msg-body">🎬 {directorNote.text}</div>
                </div>
              </div>
            )}
            {streaming && (
              <div className="run">
                {(messages.length === 0 ||
                  speakerOf(messages[messages.length - 1]) !== labels.assistant) && (
                  <div className="plate">
                    <span className="plate-avatar"><Portrait src={null} name={labels.assistant} /></span>
                    <span className="plate-name">{labels.assistant}</span>
                    <span className="role-chip">npc</span>
                  </div>
                )}
                <div className="msg assistant">
                  <span className="msg-gutter" />
                  <div className="msg-body">
                    {/* Art handles are hidden while the deltas arrive: the
                        server rewrites them into markdown when the reply is
                        persisted, so the reader would otherwise watch a raw
                        `[[art:...]]` sit in the prose and then turn into a
                        picture. Display only -- nothing here changes what is
                        stored. */}
                    <RenderedMarkdown content={hideArtHandles(streaming)} />
                    <span className="cursor" />
                  </div>
                </div>
              </div>
            )}
          </div>
          {proposal && activeId && (
            <RollProposal key={proposal.id} record={proposal} busy={busy || rolling}
                          onResolve={resolve} />
          )}
          {activeId && (
            /* Keyed by scene: the panel holds the walk's local state (whether a
               turn has been run and is waiting on a verdict), and carrying that
               across a scene switch would offer Accept for a reply in another
               scene. `onChanged` is the same refresh every transcript mutation
               here funnels through. */
            <ReplayPanel key={`${activeId}:${replayEpoch}`}
                         cid={cid} sid={activeId} startAt={replayAt}
                         disabled={busy || rolling}
                         // The same latch every other write to this transcript
                         // takes: a replayed turn is a generation into the scene
                         // on screen, and while one runs nothing else here may
                         // offer to start a second.
                         latch={() => takeRollLatch(activeId)}
                         // A replay stream that ended with no answer leaves a
                         // run that may still own the scene. This is the same
                         // pass mount and `visibilitychange` run; calling it
                         // now is the difference between the panel re-enabling
                         // itself over that run and the view taking it back.
                         onUnanswered={() => { void adoptPendingRun(cid, activeId); }}
                         onStartHandled={() => setReplayAt(null)}
                         // Into the SAME scene in the copy, not the campaign's
                         // front door: a fork copies the scenes wholesale, so
                         // this id is there, and the reader asked to replay one
                         // particular post. `replayAt` is deliberately left
                         // standing — the scene did not change, so the dialog
                         // they were looking at is waiting for them in the copy.
                         onForked={(forked) => navigate(sceneUrl(forked, activeId))}
                         onChanged={async () => {
                           void loadScenes();
                           const seen = await selectScene(activeId);
                           setCtxKey((k) => k + 1);
                           // Same reason the cut asks (#85/#84): the stored
                           // fold covers a prefix a replay has just cut,
                           // regenerated or put back, so its digest no longer
                           // describes the transcript — and a walk that has
                           // just rewritten several turns is exactly when the
                           // scene-break question wants re-asking too.
                           askAfterPost(activeId, seen);
                         }} />
          )}
          {activeDone ? (
            /* The whole composer, not a disabled entry box. This scene's summary
               is written and its changes are applied, so a post added now would
               sit outside the record taken of it -- and a greyed-out textarea
               still says "you could type here". Editing and rerolling existing
               posts stay available: those change what was absorbed, rather than
               adding to a scene that is closed. */
            <div className="scene-complete">✓ Scene complete</div>
          ) : (
          <div className={`composer${directing ? " directing" : ""}`}>
          {/* Its own row, not a cell inside the input bar. As a cell it was
              `flex: none` at 334px of un-shrinkable nowrap text -- 63% of the
              bar's 529px minimum -- so a narrow column squeezed the textarea to
              36px and then pushed Send clean out of the column. Here it is free
              to shrink and Send cannot be displaced. */}
          <div className="composer-meta">
            {/* #83. The two things this composer can send have always been two
                things; until now only one of them had a name on screen. Shown
                in every scene, because the director turn is not offscreen-only
                — an empty send takes the same path in an ordinary scene, and a
                control that appeared only when `pcless` would hide half of what
                the feature does. */}
            <div className="composer-mode" role="group" aria-label="Turn kind">
              <button type="button" aria-pressed={!directing}
                      disabled={activePcless}
                      title={activePcless
                        ? "No player character is in this scene"
                        : "Post as your character"}
                      onClick={() => setDirectMode(false)}>Speak</button>
              <button type="button" aria-pressed={directing}
                      title="Steer the scene without posting"
                      onClick={() => setDirectMode(true)}>Direct</button>
            </div>
            {/* What Direct actually costs and buys, said once, beside the
                control — the note is spent on this generation and the
                transcript never sees it. */}
            {directing && (
              <span className="composer-meta-hint">steers the reply · never posted</span>
            )}
            {/* A note that could not be handed back because a draft of the
                other kind holds the box. Without this it waits in a place with
                nothing on screen naming it, which is its own way of losing
                what somebody wrote. */}
            {heldKind && (
              <span className="composer-meta-hint">
                {heldKind === "note" ? "🎬 note held" : "post held"}
                {" · clear the box to get it back"}
              </span>
            )}
            <label className="composer-meta-label" htmlFor="response-length">Response</label>
            <select id="response-length" aria-label="Response length"
                    value={responseChipPresetId}
                    aria-describedby={responseChipPending ? "response-length-oneshot" : undefined}
                    onChange={(e) => (e.target.value
                      ? chooseResponseOverride(e.target.value)
                      : clearResponseOverride())}>
              {/* Offered only while nothing is picked. With no preset named at
                  scene level the value comes from campaign or global scope, which
                  names nothing the scene knows about -- so this option reports the
                  effective budget and its source instead of claiming a preset.
                  Rendering it always would also turn it into a "revert to
                  inherited" action, which this control has never had. */}
              {!responseChipPresetId && <option value="">{responseChipLabel}</option>}
              {responsePresets.map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
              {/* Same fallback ResponsePresetPicker carries: a scene can name a
                  preset the list has not loaded yet, or one since deleted. With no
                  matching option a native select silently displays the FIRST one,
                  so the strip would confidently name a preset that is not in
                  effect. Show the id instead (Codex review). */}
              {responseChipPresetId
                && !responsePresets.some((p) => p.id === responseChipPresetId) && (
                <option value={responseChipPresetId}>{responseChipPresetId}</option>
              )}
            </select>
            {/* A one-shot pick and a standing setting read identically without
                this — and they mean very different things: one is spent by the
                next reply, the other is the scene's standing answer. It used to
                sit INSIDE the control and so formed part of its accessible name;
                as a sibling it has to be tied back on with `aria-describedby`, or
                the distinction is visual only (Codex review). */}
            {responseChipPending && (
              <span className="chip-oneshot" id="response-length-oneshot">next reply only</span>
            )}
            {responseChipPending && (
              <button type="button" className="chip-clear" title="Cancel the one-shot pick"
                      aria-label="Cancel the one-shot response length"
                      onClick={clearResponseOverride}>×</button>
            )}
            {!responseChipPending && responseChipPresetId && sceneResponse && (
              <span className="composer-meta-hint">
                {sceneResponse.effective.reply_words} words
              </span>
            )}
            <span className="header-spacer" />
            {/* Opening a dossier does not take the turn away from you, and this
                is where the app says so — beside the control you were about to
                use, while the draft and the caret are still exactly where you
                left them. */}
            {selectedActor && (
              <span className="composer-notice">Still your turn · dossier open</span>
            )}
            <button type="button" className="composer-link"
                    aria-expanded={showInspector}
                    onClick={() => setShowInspector((v) => !v)}>
              {showInspector ? "Hide what the model saw" : "What the model saw →"}
            </button>
          </div>
          <div className="inputbar">
            {/* Dice are a mechanics affordance: both the popover's tabs lead to
                routes that only mean something with a pack bound (Check needs one
                outright; freeform Dice is offered alongside it as part of the same
                tool). An unbound campaign is freeform play, so the button is not
                there at all -- and not merely while the read is still out, which
                would flash a control and then remove it. */}
            {moduleBound === true && (
              <button className="roll-btn"
                      title={sceneLocked ? LOCKED_WHILE_GENERATING : "Roll dice"}
                      aria-label="Roll dice"
                      disabled={!activeId || busy || sceneLocked || messages.length === 0}
                      onClick={toggleRollPop}>
                🎲
              </button>
            )}
            {rollForm && (
              <div className="roll-pop">
                <div className="roll-mode-toggle">
                  <button type="button" className={rollForm.mode === "dice" ? "active" : ""}
                          disabled={rolling}
                          onClick={() => setRollForm({ ...rollForm, mode: "dice" })}>
                    Dice
                  </button>
                  <button type="button" className={rollForm.mode === "check" ? "active" : ""}
                          disabled={rolling}
                          onClick={enterCheckMode}>
                    Check
                  </button>
                </div>
                {rollForm.mode === "check" ? (
                  <div className="check-pop">
                    <select aria-label="Check actor" value={rollForm.checkActor} disabled={rolling}
                            onChange={(e) => setRollForm({ ...rollForm, checkActor: e.target.value, checkId: "" })}>
                      <option value="">Choose an actor…</option>
                      {checkActors.map((a) => <option key={a.ref} value={a.ref}>{a.label}</option>)}
                    </select>
                    <select aria-label="Check" value={rollForm.checkId} disabled={rolling}
                            onChange={(e) => setRollForm({ ...rollForm, checkId: e.target.value })}>
                      <option value="">Choose a check…</option>
                      {(checkActors.find((a) => a.ref === rollForm.checkActor)?.checks ?? [])
                        .map(([key, label]) => <option key={key} value={key}>{label}</option>)}
                    </select>
                    <input type="number" aria-label="Difficulty" value={rollForm.difficulty} disabled={rolling}
                           placeholder="default"
                           onChange={(e) => setRollForm({ ...rollForm,
                             difficulty: e.target.value === "" ? "" : Number(e.target.value) })} />
                    <input type="number" aria-label="Modifier" value={rollForm.modifier} disabled={rolling}
                           onChange={(e) => setRollForm({ ...rollForm, modifier: Number(e.target.value) })} />
                    <button className="btn-chrome" onClick={doCheck}
                            disabled={rolling || !rollForm.checkActor || !rollForm.checkId}>
                      Roll ▸
                    </button>
                    {rollForm.error && <span className="roll-error">{rollForm.error}</span>}
                  </div>
                ) : (
                <>
                <input
                  autoFocus
                  placeholder="2d6+3, 4d6kh3, 7d10t6…"
                  aria-label="Dice notation"
                  value={rollForm.notation}
                  disabled={rolling}
                  onChange={(e) => setRollForm({ ...rollForm, notation: e.target.value })}
                  onKeyDown={(e) => {
                    // See the reroll box: an inline Enter is claimed, not shared.
                    if (e.key === "Enter") { e.preventDefault(); doRoll(); }
                    if (e.key === "Escape") setRollForm(null);
                  }}
                />
                <input
                  placeholder="Label (optional)"
                  aria-label="Roll label"
                  value={rollForm.label}
                  disabled={rolling}
                  onChange={(e) => setRollForm({ ...rollForm, label: e.target.value })}
                  onKeyDown={(e) => {
                    // See the reroll box: an inline Enter is claimed, not shared.
                    if (e.key === "Enter") { e.preventDefault(); doRoll(); }
                    if (e.key === "Escape") setRollForm(null);
                  }}
                />
                <button className="btn-chrome" onClick={doRoll} disabled={rolling}>Roll ▸</button>
                <button type="button" className="roll-syntax-help" aria-label="Dice notation syntax"
                        aria-expanded={showRollSyntax}
                        onClick={() => setShowRollSyntax((v) => !v)}>syntax {showRollSyntax ? "▾" : "▸"}</button>
                {rollForm.error && <span className="roll-error">{rollForm.error}</span>}
                {showRollSyntax && (
                  <div className="roll-syntax">
                    <div><code>NdM</code> — roll N dice with M sides (default N = 1), e.g. <code>2d6</code></div>
                    <div><code>khN</code> / <code>klN</code> — keep highest/lowest N, e.g. <code>4d6kh3</code></div>
                    <div><code>dhN</code> / <code>dlN</code> — drop highest/lowest N instead of keeping</div>
                    <div><code>!</code> — exploding dice: max face rolls again, e.g. <code>5d6!</code></div>
                    <div><code>+K</code> / <code>-K</code> — flat modifier on the total, e.g. <code>2d6+3</code></div>
                    <div><code>tN</code> — pool mode: count dice ≥ N as successes, e.g. <code>7d10t6</code></div>
                    <div><code>vs N</code> — grade the total success/failure vs a target, e.g. <code>1d20+5 vs 15</code></div>
                    <div>Clauses combine freely (e.g. <code>4d6kh3!+2</code>); <code>tN</code> and <code>vs N</code> are mutually exclusive.</div>
                  </div>
                )}
                </>
                )}
              </div>
            )}
            <textarea
              rows={3}
              placeholder={directing ? "Direct the scene (optional)…" : "Speak your intent…"}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onKeyDown}
            />
            {/* Replaces Send rather than sitting beside it: Send is already
                disabled for the whole turn, so the slot is dead space at exactly
                the moment a way out is wanted. */}
            {busy ? (
              <button className="send cancel-turn" onClick={cancelTurn}>Stop ■</button>
            ) : (
              <button className="send" onClick={send} disabled={rolling || renamesInFlight > 0}>
                {/* Three labels, not two: an empty box is the "next NPC round"
                    fast path in EITHER mode (and the reason Direct does not
                    take it away), so it keeps its own word. */}
                {!input.trim() ? "Continue ▶" : directing ? "Direct 🎬" : "Send ▸"}
              </button>
            )}
          </div>
          </div>
          )}
          </>)}
        </section>
        {drawer && activeId && (
          <RecordDrawer cid={cid} sid={activeId} target={drawer} onClose={() => setDrawer(null)} />
        )}
        {/* The third pane, and the reason the review is worth its own layout:
            judging a proposal means reading the line it came from, and reading
            it in another tab means losing the row. Rendered flat — speaker,
            then text — rather than through the transcript's own machinery,
            which carries edit, reroll and alternate controls that have no
            business in a review. */}
        {chooserOpen && (
          <NewSceneChooser cid={cid} afterSid={activeId} ready={ready}
                           onClose={closeChooser} onCreated={sceneCreated} />
        )}
        {picking && (
          <PostImagePicker cid={cid} target={picking.target}
                           onInsert={(md) => insertImage(picking.index, md)}
                           onClose={() => setPicking(null)} />
        )}
      </div>
      {/* A SIBLING of the workspace, not a child of it. `.shell.review
          .shell-main` is a row flex whose two items are the review and this —
          which is the whole "three panes" layout — and nested inside the
          workspace's column flex it was never beside the review at all, only
          stacked under it and clipped to 320px. */}
      {absorb && (
        <ReviewTranscript messages={messages} speakerOf={speakerOf} isCited={isCited} />
      )}
    </PageShell>
  );
}
