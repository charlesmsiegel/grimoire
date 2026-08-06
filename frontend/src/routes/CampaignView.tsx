import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  api, ApiError, type Actor, type AbsorbPhase, type Dossiers, type EditConflict, type SceneMeta,
  type Message, type RosterEntry, type SceneAbsorb, type SceneAlternates,
  type SceneDatetime, type StagedEdit, type ProposalRecord, type SceneCheckActor,
  type ResponsePresetSummary, type ResponseOverride, type ResponseBundle,
} from "../api/client";
import { isAbortError, type ChatEvent } from "../api/stream";
import { EditableRow } from "../components/EditableRow";
import { LOCKED_WHILE_GENERATING } from "../components/sceneLock";
import { CastPanel } from "../components/CastPanel";
import { NewSceneChooser } from "../components/NewSceneChooser";
import { ChangesPanel } from "../components/ChangesPanel";
import { LedgerPanel } from "../components/LedgerPanel";
import { CalendarConfig } from "../components/CalendarConfig";
import MechanicsConfig from "../components/MechanicsConfig";
import { ResponsePresetPicker } from "../components/ResponsePresetPicker";
import { Portrait } from "../components/Portrait";
import { RecordDrawer, type DrawerTarget } from "../components/RecordDrawer";
import { SceneInspector } from "../components/SceneInspector";
import { RollProposal, type ResolveBody } from "../components/RollProposal";
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
// Scrolled this close to the top, "show me more" is what the reader means.
const NEAR_TOP_PX = 120;
// ...and this close to the bottom, they are following the reply as it streams,
// so new content should keep scrolling itself into view. Farther up than this
// they are reading something specific and must not be yanked away from it.
const NEAR_BOTTOM_PX = 80;

// Reader-facing names for the absorb steps the API reports in `phases`. The
// wire names say where the work happens; these say what the reviewer lost.
const PHASE_LABELS: Record<AbsorbPhase["name"], string> = {
  extraction: "the scene summary",
  dossiers: "NPC dossiers",
  voice: "voice checks",
  audit: "mechanics audit",
};

// Staged edit kinds whose payload stamps the scene the beat came from, and so
// have to follow a scene rename made while the review is open — see
// `reviewSceneRenamed`.
const SCENE_STAMPED: StagedEdit["kind"][] = ["plot", "commitment", "fact"];

// What the backend proved about a proposal's cited speaker (#112), said the way
// a reviewer would say it. The wire names are tiers; these are the reason the
// row is banded where it is, which is the only thing worth a chip.
const AUTHORITY_LABELS: Record<NonNullable<StagedEdit["review"]>["authority"], string> = {
  narration: "narrated",
  self: "said of themself",
  other: "said by someone else",
  // Not "speaker not in this scene": the tier also covers a name TWO speakers
  // answer to, and telling a reviewer their model invented a citation it did
  // not invent is a worse error than the vaguer wording.
  unattributed: "no one speaker matches",
  uncited: "nothing cited",
};

// A row's band, with the fallback that keeps the pre-#110 behaviour intact:
// dossier, voice and sheet proposals are staged after the extraction and rest
// on no citation, so they route as `medium` — shown, and pre-approved.
function editBand(e: StagedEdit): NonNullable<StagedEdit["review"]>["band"] {
  return e.review?.band ?? "medium";
}

// Only `low` starts unticked. Withholding a default approval is the safe
// direction and the only relaxation of the review-everything invariant this
// ships: nothing is applied that the reviewer did not tick and Save.
function approvedByDefault(e: StagedEdit): boolean {
  return editBand(e) !== "low";
}

// The dossier phase has five distinguishable bad endings and the wording has to
// match the edit list beside it: "prepared", never "refreshed" (a dossier is
// staged here and only written on save, #235), and never "failed" for a phase
// that produced something. Ordered most-specific first.
function dossierNotice(d: Dossiers): string {
  if (d.budget_exhausted && !d.attempted) return `No NPC dossier was prepared: ${d.reason}`;
  if (d.failed.length > 0) {
    return d.status === "failed" ? "No NPC dossier could be prepared"
                                 : "Some NPC dossiers could not be prepared";
  }
  // Nothing went wrong per-NPC, so the reason is the whole phase's story: a
  // partial run (some prepared, the rest dropped) or a phase that never got off
  // the ground at all (an unreadable cast).
  return d.status === "degraded" ? `Some NPC dossiers were not prepared: ${d.reason}`
                                 : `NPC dossier refresh failed: ${d.reason}`;
}

// Resolves when the signal aborts, for racing a wait that has no cancellation
// of its own against Stop. `once`, so a long-lived controller does not
// accumulate listeners across the turns that share it.
function settleOn<T>(signal: AbortSignal, value: T): Promise<T> {
  return new Promise<T>((resolve) => {
    if (signal.aborted) resolve(value);
    else signal.addEventListener("abort", () => resolve(value), { once: true });
  });
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

type SceneSort = "updated" | "date" | "order";

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
function sortScenes(scenes: SceneMeta[], sort: SceneSort): SceneMeta[] {
  if (sort === "updated") return scenes;
  const arr = [...scenes];
  if (sort === "order") {
    arr.sort((a, b) => sceneNumber(b.id, 0) - sceneNumber(a.id, 0));
  } else {
    arr.sort((a, b) => {
      if (!a.date && !b.date) return 0;
      if (!a.date) return 1;
      if (!b.date) return -1;
      return b.date.localeCompare(a.date);
    });
  }
  return arr;
}

// Memoized so typing in the input bar (which re-renders CampaignView on every
// keystroke) doesn't re-parse the markdown of every unchanged message.
const RenderedMarkdown = memo(function RenderedMarkdown({ content }: { content: string }) {
  return (
    <Markdown remarkPlugins={[remarkGfm]} rehypePlugins={[quotePlugin]}>{content}</Markdown>
  );
});

export default function CampaignView({ ready, topbarCollapsed = false, onToggleTopbar = () => {} }: {
  ready: boolean; topbarCollapsed?: boolean; onToggleTopbar?: () => void;
}) {
  const { cid = "" } = useParams();
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [worldName, setWorldName] = useState("");
  const [dt, setDt] = useState<SceneDatetime | null>(null);
  const [showCalendar, setShowCalendar] = useState(false);
  const [showMechanics, setShowMechanics] = useState(false);
  const [showStyle, setShowStyle] = useState(false);
  const [scenes, setScenes] = useState<SceneMeta[]>([]);
  const [sceneSort, setSceneSort] = useState<SceneSort>("updated");
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

  // Every LOCAL edit to the transcript goes through here, and each one drops
  // `loaded`. Matching tokens only prove a set and a *fetch* describe the same
  // scene — an optimistic edit changes the posts under both without touching
  // either, so a stale-but-consistent pair still passes. Reroll is the case
  // that bites: it removes the displayed reply, and the set keyed to that reply
  // stays valid, so the picker drops onto the user post above it.
  //
  // Loading and paging deliberately do NOT come through here: they are what
  // `loaded` describes, and a prepend extends that transcript rather than
  // replacing it.
  function showOptimistically(edit: (m: Message[]) => Message[]) {
    setMessages(edit);
    setLoaded(null);
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
  const sceneLocked = !!activeId && activeId === streamingId;
  const markRenaming = useCallback(
    (active: boolean) => setRenamesInFlight((n) => n + (active ? 1 : -1)), []);
  // Held for the life of one turn so Cancel can reach it. A ref, not state:
  // the controller is not rendered, and rebuilding the component tree on every
  // send just to store it would remount the transcript mid-stream.
  const abortRef = useRef<AbortController | null>(null);
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
  const [error, setError] = useState<{ text: string; retryable: boolean } | null>(null);
  const fail = (e: any, retryable = true) =>
    setError({ text: e?.detail ?? String(e), retryable });
  const [ctxKey, setCtxKey] = useState(0);
  const [editing, setEditing] = useState<{ index: number; text: string } | null>(null);
  const [rerollPrompt, setRerollPrompt] = useState<string | null>(null); // null = popover closed
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
  function setActive(id: string | null) {
    activeIdRef.current = id;
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
  const [drawer, setDrawer] = useState<DrawerTarget | null>(null);
  const [showChanges, setShowChanges] = useState(false);
  const [showLedger, setShowLedger] = useState(false);
  const [absorb, setAbsorb] = useState<SceneAbsorb | null>(null);
  // The scene this review was absorbed FROM. Switching scenes leaves the panel
  // open, so saving against the currently selected scene would commit scene A's
  // review onto scene B (#235).
  const [absorbSid, setAbsorbSid] = useState<string | null>(null);
  const [absorbing, setAbsorbing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [editRows, setEditRows] = useState<(StagedEdit & { approved: boolean })[]>([]);
  // Whether the collapsed low-confidence rows are showing (#110). Rows stay in
  // `editRows` at their original index either way: the conflict verdicts the
  // server sends back are bound to positions in the submitted batch, so the
  // routing is a rendering decision and never a reordering one.
  const [showLow, setShowLow] = useState(false);
  const [editFailures, setEditFailures] = useState<
    { id: string; reason: string; kind: "conflict" | "error"; label: string }[]>([]);
  // Rows the server refused because their target moved since the scene was
  // absorbed (#111), each already bound to its index in `editRows`. The save is
  // rejected whole and before anything is written, so this is a state the
  // review sits IN rather than a report of what landed: it clears a row at a
  // time as the reviewer keeps, replaces or merges.
  const [conflicts, setConflicts] =
    useState<{ row: number; conflict: EditConflict }[]>([]);
  // A failed SAVE gets its own surface, not the shared `error` banner: that
  // banner's Retry is wired to chat generation, so pointing a save failure at
  // it invites the user to generate another reply with the review still open.
  const [saveError, setSaveError] = useState<string | null>(null);
  const [chooserOpen, setChooserOpen] = useState(false);
  const [seedPrompt, setSeedPrompt] = useState<{ sid: string; prompt: string } | null>(null);
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
  const [responseChipOpen, setResponseChipOpen] = useState(false);
  const streamRef = useRef<HTMLDivElement>(null);
  const [railCollapsed, setRailCollapsed] = useState(
    () => localStorage.getItem("grimoire.rail.collapsed") === "1");
  const [inspectorCollapsed, setInspectorCollapsed] = useState(
    () => localStorage.getItem("grimoire.inspector.collapsed") === "1");

  function toggleRail() {
    setRailCollapsed((v) => {
      localStorage.setItem("grimoire.rail.collapsed", v ? "0" : "1");
      return !v;
    });
  }
  function toggleInspector() {
    setInspectorCollapsed((v) => {
      localStorage.setItem("grimoire.inspector.collapsed", v ? "0" : "1");
      return !v;
    });
  }
  const [subheaderCollapsed, setSubheaderCollapsed] = useState(
    () => localStorage.getItem("grimoire.subheader.collapsed") === "1");
  function toggleSubheader() {
    setSubheaderCollapsed((v) => {
      localStorage.setItem("grimoire.subheader.collapsed", v ? "0" : "1");
      return !v;
    });
  }

  useEffect(() => {
    api.getCampaign(cid).then((c) => {
      setName(c.meta.name);
      setWorldName(c.meta.world_name ?? ""); // embedded: no second fetch
    });
    api.listScenes(cid).then((list) => {
      setScenes(list);
      if (list.length) selectScene(list[0].id);
    });
    api.getConfig().then((c) => {
      setColorQuotes(c.quote_color === "on");
      setLabels({ user: c.user_label || "You", assistant: c.assistant_label || "Grimoire" });
    }).catch(() => {});
    api.listResponsePresets().then(setResponsePresets).catch(() => setResponsePresets([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cid]);

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
  const budgetCutPhases = useMemo(
    () => (absorb?.phases ?? []).filter((p) => p.budget_exhausted),
    [absorb?.phases]);

  // offscreen scenes take director notes instead of PC dialogue
  const activePcless = useMemo(
    () => scenes.find((s) => s.id === activeId)?.pcless ?? false,
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
    setResponseChipOpen(false);
  }
  function clearResponseOverride() {
    setPendingResponse(null);
    setResponseChipOpen(false);
  }
  const responseChipRef = useRef<HTMLDivElement>(null);
  // matches the reroll popover / roll form: Escape closes it; this dropdown
  // additionally closes on an outside click, since (unlike those) it has no
  // focused input to anchor a keydown handler to.
  useEffect(() => {
    if (!responseChipOpen) return;
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") setResponseChipOpen(false);
    }
    function onPointerDown(e: MouseEvent) {
      if (responseChipRef.current && !responseChipRef.current.contains(e.target as Node)) {
        setResponseChipOpen(false);
      }
    }
    document.addEventListener("keydown", onKeyDown);
    document.addEventListener("mousedown", onPointerDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.removeEventListener("mousedown", onPointerDown);
    };
  }, [responseChipOpen]);
  const [directorNote, setDirectorNote] = useState<string | null>(null);


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
  const parkedPrompts = useRef<Map<string, string>>(new Map());
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
    const held = parkedPrompts.current.get(activeId);
    if (held === undefined) return;
    parkedPrompts.current.delete(activeId);
    giveBackPrompt(held);
  }, [activeId, parkedTick, giveBackPrompt]);

  // Where a recovered prompt goes: the composer if the player is still on the
  // scene it belongs to, the parking map otherwise. `activeIdRef`, not
  // `activeId` — this runs from a callback that outlives the render it started
  // in, and a captured `activeId` would be the scene the turn began on, which
  // is exactly the stale answer this exists to avoid.
  const recoverPrompt = useCallback((sid: string, text: string) => {
    if (activeIdRef.current === sid) {
      giveBackPrompt(text);
      return;
    }
    parkedPrompts.current.set(sid, text);
    setParkedTick((n) => n + 1);
  }, [giveBackPrompt]);
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
      // a new scene opens at its most recent page, at the bottom
      windowSizeRef.current = PAGE_SIZE;
      atBottomRef.current = true;
    }
    fetchAlternates(cid, id);
    api.getSceneDatetime(cid, id).then(setDt).catch(() => setDt(null));
    api.getCast(cid, id).then(setCast).catch(() => setCast([]));
    api.listAppearances(cid).then(setRoster).catch(() => setRoster([]));
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

  async function sceneCreated(id: string, initialPrompt?: string) {
    setChooserOpen(false);
    if (initialPrompt) setSeedPrompt({ sid: id, prompt: initialPrompt });
    setScenes(await api.listScenes(cid));
    selectScene(id);
  }

  // A scene's id is its filename, so a rename mints a new one. `scene_refs.repoint`
  // carries every *persisted* reference across; four more live only in this
  // browser, where no server-side repointer can see them:
  //   - `absorbSid`, the id an open review's save and audit retry POST;
  //   - `payload.scene` on each staged plot or commitment edit, which
  //     absorb.materialize embedded and apply_edits passes straight to
  //     plot.set_movement / commitments.set_movement / facts.record — so a save
  //     after a rename would file the movement under a scene that is gone. All
  //     three kinds, because each stamps its record with the scene it came from
  //     (#115, #114). A fact row needs nothing beyond its payload: its staged
  //     `before` is a `conflicts.fact_line`, which carries no scene id at all —
  //     deliberately, so that the whole class of staleness the commitment
  //     fingerprint forces on this function cannot arise for facts;
  //   - the staged CONFLICT BASIS of a commitment row. `conflicts.commitment_line`
  //     ends `[N beats, last moved in <scene>]`, and `scene_refs.repoint` rewrites
  //     that scene id in the stored record — so a row left holding the old id no
  //     longer matches what the store says and saves as a spurious conflict, on a
  //     commitment nobody touched. `resolve_from` gets the same treatment: it is
  //     the value the reviewer was shown, and it is compared the same way; and
  //   - the id the reroll-alternates state is scoped to (below).
  function reviewSceneRenamed(oldId: string, newId: string) {
    // Anchored to the END of the line, so a beat that happens to quote the old
    // scene id in its own text is left alone — only the fingerprint moves. The
    // beat count sits in front of this and is not matched: it is "1 beat" in the
    // singular and "N beats" otherwise, and matching the plural alone silently
    // skipped every commitment with exactly one beat.
    const from = `, last moved in ${oldId}]`;
    const to = `, last moved in ${newId}]`;
    const repoint = (v: string) => (v.endsWith(from) ? v.slice(0, -from.length) + to : v);
    setAbsorbSid((s) => (s === oldId ? newId : s));
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
    setEditRows((rows) => rows.map((r) => {
      if (!SCENE_STAMPED.includes(r.kind)) return r;
      const next = { ...r };
      if (r.payload?.scene === oldId) next.payload = { ...r.payload, scene: newId };
      if (r.kind === "commitment") {
        next.before = repoint(r.before);
        if (r.resolve_from !== undefined) next.resolve_from = repoint(r.resolve_from);
      }
      return next;
    }));
    // An UNANSWERED conflict carries the same fingerprint, and it is the value
    // `resolveConflict` copies into `resolve_from` when the reviewer clicks
    // Replace. The server's own repoint has already moved the stored record onto
    // the new id, so a stale snapshot here means the retry is refused as changed
    // again — the reviewer answering a conflict that no longer exists, twice.
    // It is also what the panel SHOWS them, so leaving it stale would display an
    // id no scene has.
    // No kind check needed: `repoint` only rewrites a string ENDING in the
    // commitment fingerprint's suffix, and a plot conflict's `stored` is a
    // `plot_line`, which does not carry one.
    setConflicts((cs) => cs.map(({ row, conflict }) => (
      { row, conflict: { ...conflict, stored: repoint(conflict.stored) } })));
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
    if (activeIdRef.current === oldId) setActive(newId);
    // The rail's own metadata, not just the things pointing at it. `pcless` and
    // the title are read off the row whose id matches `activeId`, so adopting
    // the id without re-keying the row loses both — an offscreen scene silently
    // offering the PC composer for sends the backend still handles as director
    // notes. A relist would fix it, and a relist that FAILS is exactly the
    // state this has to survive.
    setScenes((list) => list.map((s) => (s.id === oldId ? { ...s, id: newId } : s)));
    setSeedPrompt((p) => (p && p.sid === oldId ? { ...p, sid: newId } : p));
    reviewSceneRenamed(oldId, newId);
    const parked = parkedPrompts.current.get(oldId);
    if (parked !== undefined) {
      parkedPrompts.current.delete(oldId);
      parkedPrompts.current.set(newId, parked);
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
      adoptSceneId(id, newId);
      try {
        setScenes(await api.listScenes(cid));
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
      setScenes(await api.listScenes(cid));
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

  async function deleteScene(s: SceneMeta) {
    if (!window.confirm(`Delete '${s.title}'?`)) return;
    await api.deleteScene(cid, s.id);
    const list = await api.listScenes(cid);
    setScenes(list);
    // Same reason as `renameScene`: the ledger resolves every thread,
    // commitment and fact against the scene list, so a deletion changes what
    // it returns. Deleting an INACTIVE scene selects nothing, and deleting the
    // last one takes the `else` branch below, so neither reaches the
    // `selectScene` that would otherwise bump this.
    setCtxKey((k) => k + 1);
    if (activeId === s.id) {
      if (list.length) selectScene(list[0].id);
      else {
        windowTokenRef.current += 1; // drop any page still in flight for it
        setActive(null);
        setMessages([]);
        setLoaded(null);
        setFirstIndex(0);
        setHasUserPost(null);
      }
    }
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
  // could cross the threshold repeatedly with nothing ever asking.
  function askForRollingSummary(id: string) {
    api.refreshRollingSummary(cid, id)
      .then((r) => { if (r.refreshed && activeIdRef.current === id) setCtxKey((n) => n + 1); })
      .catch(() => {});
  }

  async function runStream(
    id: string,
    start: (onEvent: (e: ChatEvent) => void, signal: AbortSignal) => Promise<void>,
    onPromptUnstored?: () => void,
    rerolling = false,
  ) {
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
        } else if (e.error) {
          fail(e.error, !(rerolling && acc.trim().length > 0));
          errored = true;
          // The post is gone from the transcript, so the composer has to give
          // the player their words back. Otherwise a failed send destroys what
          // they typed and Retry cannot help — it calls /retry, which has no
          // prompt of its own and 400s on a scene with nothing else in it (#95).
          if (e.error.post_returned) onPromptUnstored?.();        } else if (e.proposal) {
          // Live from the stream, so it outranks any read still in flight.
          setProposalNow({ id: e.proposal.id, status: "pending", payload: e.proposal, resolution: null });
        } else if (e.done) {
          finished = true;
        }
      }, controller.signal);
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
      if (!isAbortError(err)) fail(err, !(rerolling && acc.trim().length > 0));
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
      abortRef.current = null;
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
      // Only `abortRef`, unlike the poll's predicate. A scene switch is already
      // covered for this call: switching runs `selectScene`, which bumps
      // `windowTokenRef` and so retires the page this one has in flight. The
      // poll needs the scene check too because it *issues* fresh selects, which
      // bump that token themselves and cannot be retired by it.
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
      if (((unreached || refused) && (unverifiable || nothingLanded))
          || (interrupted && nothingLanded)) {
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
      askForRollingSummary(id);
    }
    // Landed means the backend said so, not that the promise resolved.
    return finished && !errored;
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
      setScenes(await api.listScenes(cid));
      setActive(id);
    }
    setInput("");
    // the player just spoke: put them back at the tail even if they had
    // scrolled up into older history to re-read something
    atBottomRef.current = true;
    // ephemeral turns are never stored: a director note (offscreen scene) or —
    // in any scene — an empty send meaning "next NPC round"
    if (activePcless || !content) {
      if (activePcless) setDirectorNote(content || null);
      try {
        const landed = await runStream(id, (onEvent, signal) => pendingResponse
          ? api.chat(cid, id!, content, onEvent, pendingResponse, signal)
          : api.chat(cid, id!, content, onEvent, undefined, signal));
        if (landed) setPendingResponse(null);
      } finally {
        setDirectorNote(null);
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
    const landed = await runStream(id, (onEvent, signal) => pendingResponse
      ? api.chat(cid, id!, content, onEvent, pendingResponse, signal)
      : api.chat(cid, id!, content, onEvent, undefined, signal),
      () => recoverPrompt(id!, content));
    if (landed) setPendingResponse(null);
  }

  async function saveEdit() {
    // `rolling` for the same reason the swipe buttons read it: an edit saved
    // while a swap is in flight carries the index and text of the message on
    // screen, which the promotion is in the middle of replacing. Whichever
    // write loses the race silently discards the other, and the two refreshes
    // can land out of order on top of that.
    if (!editing || !activeId || rolling) return;
    await api.editMessage(cid, activeId, editing.index, editing.text);
    setEditing(null);
    await selectScene(activeId);
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
  const rerollToRetryRef = useRef<{ sid: string; guidance: string } | null>(null);

  async function retry() {
    if (!activeId || busy || rolling || renamesInFlight) return;
    const again = rerollToRetryRef.current;
    if (again && again.sid === activeId) return void await reroll(again.guidance);
    const landed = await runStream(activeId, (onEvent, signal) => pendingResponse
      ? api.retry(cid, activeId, onEvent, pendingResponse, signal)
      : api.retry(cid, activeId, onEvent, undefined, signal));
    if (landed) setPendingResponse(null);
  }

  async function reroll(repeatGuidance?: string) {
    if (!activeId || busy || rolling || renamesInFlight) return;
    const guidance = repeatGuidance ?? (rerollPrompt ?? "").trim();
    setRerollPrompt(null);
    rerollToRetryRef.current = { sid: activeId, guidance };
    // one turn is a run of assistant posts — drop the whole trailing run, but
    // keep any trailing transition lines, which the backend also preserves
    showOptimistically((m) => {
      let end = m.length;
      const kept: Message[] = [];
      while (end > 0 && m[end - 1].speaker === TRANSITION_SPEAKER) kept.unshift(m[--end]);
      while (end > 0 && m[end - 1].role === "assistant") end--;
      return [...m.slice(0, end), ...kept];
    });
    // The trailing arguments stay positional-explicit here rather than being
    // omitted: the signal sits behind them, so a plain reroll still has to say
    // `undefined, undefined` to reach it. What the four branches preserve is
    // the promise retry makes — a pending one-shot override rides regenerate.
    const landed = await runStream(activeId, (onEvent, signal) => {
      if (guidance && pendingResponse) return api.regenerate(cid, activeId!, onEvent, guidance, pendingResponse, signal);
      if (guidance) return api.regenerate(cid, activeId!, onEvent, guidance, undefined, signal);
      if (pendingResponse) return api.regenerate(cid, activeId!, onEvent, undefined, pendingResponse, signal);
      return api.regenerate(cid, activeId!, onEvent, undefined, undefined, signal);
      // `rerolling`: a reroll that fails wants a different recovery offered
      // than a failed send does — see `runStream`.
    }, undefined, true);
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
      try {
        await selectScene(sid);
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
          await selectScene(sid);
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
      await selectScene(activeId);
      askForRollingSummary(activeId);   // a roll is a post too (#85)
    } catch (err: any) {
      setRollForm({ ...rollForm, error: err.detail ?? String(err) });
    } finally {
      releaseLatch();
    }
  }

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
      await selectScene(activeId);
      askForRollingSummary(activeId);   // as is a check (#85)
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
    await runStream(activeId, (onEvent, signal) =>
      api.resolveProposal(cid, activeId!, body, onEvent, signal));
  }

  // No-op unless a turn is in flight; the abort rejects the fetch, `runStream`
  // recognises it and unwinds without an error banner. Nothing is sent to the
  // server — closing the connection IS the cancel, and the backend persists
  // whatever the model had produced when it sees the disconnect (#95).
  function cancelTurn() {
    abortRef.current?.abort();
  }

  // A scene already in the chronicle comes back as 409 "already_absorbed" rather
  // than silently re-absorbing: lore edits append and plot movements add a beat,
  // so a second pass duplicates both (#235). Confirm, then retry with force.
  async function endScene() {
    // `rolling` too, not only `sceneLocked`. Absorb takes its transcript
    // snapshot once, so a swap committing after it means the review summarises
    // the take the reader replaced — and saving that review marks the *swapped*
    // transcript absorbed, with staged edits derived from narration it never
    // read. The same reasoning as the rule above, for the other latch.
    if (!activeId || absorbing || rolling) return;
    setAbsorbing(true);
    setError(null);
    setEditFailures([]);
    setConflicts([]);
    try {
      let a;
      try {
        a = await api.absorbScene(cid, activeId);
      } catch (err: any) {
        if (err?.kind !== "already_absorbed") throw err;
        if (!window.confirm(
          "This scene has already been absorbed. Absorbing again re-proposes every " +
          "change from scratch, so appended lore and plot beats can end up duplicated. " +
          "Absorb it again?")) return;
        a = await api.absorbScene(cid, activeId, true);
      }
      setAbsorb(a);
      setAbsorbSid(activeId);
      setEditRows(a.edits.map((e) => ({ ...e, approved: approvedByDefault(e) })));
      setShowLow(false);
    } catch (err: any) {
      fail(err);
    } finally {
      setAbsorbing(false);
    }
  }

  // Commit is replayable server-side and plot movements append a beat per apply,
  // so a second PUT of the same review duplicates them (#235) -- the `saving`
  // latch is what keeps a double-click from being a double-commit. A failed save
  // leaves the review standing so it can be retried rather than silently lost.
  async function saveAbsorb() {
    const sid = absorbSid ?? activeId;
    if (!absorb || !sid || saving) return;
    setSaving(true);
    setSaveError(null);
    // captured before editRows is cleared below -- failures only carry
    // id/reason/kind, so the row's label has to come from what was on screen.
    const labels = new Map(editRows.map((e) => [e.id, e.label]));
    try {
      const res = await api.saveChronicle(cid, sid, {
        one_line: absorb.one_line, summary: absorb.summary, keywords: absorb.keywords,
        timeline_events: absorb.timeline_events,
        edits: editRows.filter((e) => e.approved).map(({ approved, ...e }) => e),
        // Same token on every attempt, so the retry below cannot commit twice
        // when the first PUT landed and only its response was lost (#235).
        commit_token: absorb.commit_token });
      setEditFailures(res.failures.map((f) => ({ ...f, label: labels.get(f.id) ?? f.id })));
      setAbsorb(null);
      setAbsorbSid(null);
      setEditRows([]);
      setConflicts([]);
      setCtxKey((n) => n + 1);
    } catch (err: any) {
      // A contradiction is not a failed save (#111): the server refused the
      // batch before writing anything, so the review stands exactly as it was
      // and the same commit token is still good. Show the rows, let the
      // reviewer answer each one, and save again -- no `saveError`, whose
      // "Try saving again" would just re-post the batch that was refused.
      if (err?.kind === "edit_conflicts") {
        // Resolve each verdict to the ROW it belongs to, here and now, while
        // `editRows` is still the exact array this batch was built from
        // (`saving` latches the panel for the whole round-trip). The server
        // stamps a batch index; `approvedIdx` is that batch's row numbers, so
        // the two line up positionally even when the response has dropped the
        // unconflicted rows in between. Storing row numbers rather than the
        // raw verdicts also survives what comes next: unapproving a row is the
        // keep answer, and it would shift every batch index after it.
        const approvedIdx = editRows.flatMap((r, i) => (r.approved ? [i] : []));
        const rows = ((err.body?.conflicts ?? []) as EditConflict[])
          .map((c) => ({ row: approvedIdx[c.index] ?? -1, conflict: c }))
          .filter((p) => p.row >= 0);
        setConflicts(rows);
        // A refusal on a collapsed row has to be answerable, and the save is
        // refused whole -- so leaving the section shut would leave the panel
        // insisting something is unanswered with nothing on screen to answer.
        // Latched here rather than derived from `conflicts`: a derived flag
        // goes false the instant the reviewer clicks Keep stored (which
        // unapproves the row and drops its verdict), collapsing the section
        // and the row they are looking at out from under them.
        if (rows.some(({ row }) => editBand(editRows[row]) === "low")) setShowLow(true);
        setSaveError(null);
        return;
      }
      setSaveError(err.detail ?? String(err));
    } finally {
      setSaving(false);
    }
  }

  // Conflicts still showing, keyed by their row. Already bound to a row when
  // the refusal arrived; all that is left is to drop the ones whose row has
  // since been unapproved, which IS the keep answer.
  const conflictByRow = useMemo(() => {
    const out = new Map<number, EditConflict>();
    for (const { row, conflict } of conflicts) {
      if (editRows[row]?.approved) out.set(row, conflict);
    }
    return out;
  }, [conflicts, editRows]);

  // The low-confidence rows, each carrying the index it holds in `editRows`
  // (#110). Kept as pairs rather than filtered into a second array: every
  // handler on a row addresses it positionally, and a row rendered under its
  // position in the FILTERED list would edit whichever row happened to sit
  // there in the real one.
  const lowRows = useMemo(
    () => editRows.flatMap((e, i) => (editBand(e) === "low" ? [[e, i] as const] : [])),
    [editRows]);
  // The reviewer's answer to one conflict. **keep** is not here: it unapproves
  // the row, which drops it from the batch entirely -- the stored value wins by
  // the edit never being sent. `replace` keeps the staged text, `merge` swaps in
  // the draft the server prefilled from both sides for the reviewer to trim.
  //
  // `resolve_from` rides along because the flag alone is not standing
  // permission: it records WHICH value was on screen when they answered, so a
  // save that lands after the record has moved again is refused instead of
  // overwriting something nobody saw.
  function resolveConflict(i: number, conflict: EditConflict,
                           resolve: "replace" | "merge", after?: string) {
    setEditRows((rows) => rows.map((r, j) => (j === i
      ? { ...r, resolve, resolve_from: conflict.stored,
          ...(after === undefined ? {} : { after }) }
      : r)));
    // By row, not by edit id — the duplicate-id case: a sibling row sharing
    // this one's id keeps its own conflict and its own unanswered badge.
    setConflicts((cs) => cs.filter((c) => c.row !== i));
  }

  // Replaces absorb.mechanics with a fresh audit and swaps in its sheet
  // proposals, leaving every other staged edit (prose/relationship/etc.)
  // exactly as the reviewer had it.
  async function retryAudit() {
    // `absorbSid`, not `activeId` — the same reason saveAbsorb uses it. A review
    // survives a scene switch (only Discard or a successful save clears it), so
    // reading the rail would audit whatever the user has since opened and write
    // that scene's verdict, sheet edits and phase row into this review.
    const sid = absorbSid ?? activeId;
    if (!sid) return;
    try {
      const res = await api.retryAudit(cid, sid);
      // The audit phase row is a projection of `mechanics` (backend:
      // _phase_report), so it has to move with it — otherwise the panel keeps
      // reporting a budget that ran out for a step this retry has since run.
      setAbsorb((a) => (a ? { ...a, mechanics: res.mechanics,
        phases: a.phases.map((p) => (p.name === "audit"
          ? { ...p, status: res.mechanics.status, reason: res.mechanics.reason,
              attempted: res.mechanics.attempted,
              budget_exhausted: res.mechanics.budget_exhausted }
          : p)) } : a));
      setEditRows((rows) => [
        ...rows.filter((r) => r.kind !== "sheet"),
        ...res.edits.map((e) => ({ ...e, approved: approvedByDefault(e) })),
      ]);
      // Conflicts are bound to row numbers, and this rebuilds the array — so
      // any that survived a refusal would now point at whichever row inherited
      // their index. Sheet edits never conflict, so there is nothing to carry
      // over; the next save re-reports whatever is still drifted.
      setConflicts([]);
    } catch (err: any) {
      fail(err);
    }
  }


  // One staged-edit row. Lifted out of the list because #110 renders the rows
  // in two places -- the ordinary list, and the collapsed low-confidence
  // section under it -- and both must render an identical row bound to the
  // SAME index. `i` is the row's position in `editRows`, which is what the
  // conflict verdicts (#111) and the submitted batch are both keyed on, so it
  // is passed in rather than recomputed from either list's own ordering.
  function renderEditRow(e: StagedEdit & { approved: boolean }, i: number) {
    const isNewRecord = e.kind === "new_character" || e.kind === "new_location" || e.kind === "new_lore";
    const conflict = conflictByRow.get(i);
    const setPayload = (patch: Record<string, unknown>) =>
      setEditRows((rows) => rows.map((r, j) =>
        j === i ? { ...r, payload: { ...r.payload, ...patch } } : r));
    return (
      <div className={"absorb-edit" + (e.authored ? " authored" : "")} key={e.id}>
        <label>
          <input type="checkbox" aria-label={`Approve ${e.label}`} checked={e.approved}
                 onChange={() => setEditRows((rows) => rows.map((r, j) =>
                   j === i ? { ...r, approved: !r.approved } : r))} />
          {e.label}{e.authored ? " · card edit" : ""}
          {e.review && (
            <span className={`chip absorb-band absorb-band-${e.review.band}`}
                  title={`certainty ${e.review.certainty ?? "not given"}` +
                         ` · score ${e.review.score}`}>
              {e.review.band} · {AUTHORITY_LABELS[e.review.authority] ?? e.review.authority}
            </span>)}
          {conflict && <span className="chip on absorb-conflict-badge">Changed</span>}
        </label>
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
              <button className="subtle" aria-label={`Keep stored ${e.label}`}
                      onClick={() => setEditRows((rows) => rows.map((r, j) =>
                        j === i ? { ...r, approved: false } : r))}>
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
                      onChange={(ev) => setEditRows((rows) => rows.map((r, j) =>
                        j === i ? { ...r, after: ev.target.value } : r))} />
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
      </div>
    );
  }

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
  const canReroll = rerollIndex >= 0 &&
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
  const altTitle = (() => {
    if (alternates.active === null) return "no alternate is showing — the last reroll didn't land";
    const variant = alternates.alternates[alternates.active];
    if (!variant) return undefined;
    return variant.guidance ? `Guided: ${variant.guidance}\n\n${variant.preview}` : variant.preview;
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
    if (!run.actor || run.actor.kind !== "characters") return null;
    const ver = roster.find((r) => r.kind === "characters" && r.id === run.actor!.id)?.version;
    return ver ? api.campaignImageUrl(cid, run.actor.id, ver, "avatar") : null;
  }

  return (
    <div className="workspace">
      <div className="chrome-bar">
        <button className="chrome-toggle" aria-pressed={!topbarCollapsed} onClick={onToggleTopbar}>
          {topbarCollapsed ? "▾ Nav" : "▴ Nav"}
        </button>
        <button className="chrome-toggle" aria-pressed={!subheaderCollapsed} onClick={toggleSubheader}>
          {subheaderCollapsed ? "▾ Bar" : "▴ Bar"}
        </button>
      </div>
      {!subheaderCollapsed && (
      <div className="subheader">
        <Link to="/" className="sub-back">‹ Campaigns</Link>
        <span className="sub-divider" />
        <span className="sub-name">{name}</span>
        {worldName && (
          <Link to={`/campaigns/${cid}/world`} className="sub-world">World ▸ {worldName} ↗</Link>
        )}
        <div className="sub-actions">
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
          <button className="sub-changes" onClick={() => setShowLedger((v) => !v)}>
            {showLedger ? "Close" : "Ledger"}
          </button>
          <button className="sub-changes" onClick={() => setShowChanges((v) => !v)}>
            {showChanges ? "Close" : "Changes"}
          </button>
          <button className="sub-mechanics" onClick={() => setShowMechanics((v) => !v)}>
            {showMechanics ? "Close" : "Mechanics"}
          </button>
          {/* `busy` is not the whole of "a turn can still write here": it clears
              when the socket dies, and the backend's shielded abort write lands
              seconds later — which is the window `streamingId` covers. Absorb
              inside it and the chronicle summarises a transcript the partial
              has not reached yet, then the partial lands underneath a scene
              already marked absorbed. That one does not come back: the review
              is committed against a transcript that no longer matches (#95). */}
          <button className="sub-end" onClick={endScene}
                  disabled={!activeId || absorbing || busy || sceneLocked || rolling}>
            {absorbing ? "Ending…" : "End scene"}
          </button>
        </div>
      </div>
      )}
      <div className={"layout" + (railCollapsed ? " rail-collapsed" : "") + (inspectorCollapsed ? " inspector-collapsed" : "")}>
      {railCollapsed ? (
        <button className="rail-tab" aria-label="Expand scene list" onClick={toggleRail}>›</button>
      ) : (
      <aside className="scene-rail">
        <button className="rail-collapse" aria-label="Collapse scene list" onClick={toggleRail}>‹</button>
        <div className="rail-counter">Scenes / {String(scenes.length).padStart(2, "0")}</div>
        <button className="btn-chrome rail-new" onClick={newScene}>+ New Scene</button>
        <select className="rail-sort" aria-label="Sort scenes by" value={sceneSort}
                onChange={(e) => setSceneSort(e.target.value as SceneSort)}>
          <option value="updated">Sort: Last updated</option>
          <option value="date">Sort: Scene date</option>
          <option value="order">Sort: Order</option>
        </select>
        <div className="rail-scenes">
          {sortScenes(scenes, sceneSort).map((s, i) => (
            <EditableRow
              key={s.id}
              label={s.title}
              prefix={String(sceneNumber(s.id, scenes.length - i)).padStart(2, "0")}
              subtitle={s.pcless ? "Offscreen" : undefined}
              active={s.id === activeId}
              // A scene's id is its filename, and renaming re-slugs it — so a
              // rename mid-turn moves the file out from under the stream, and
              // the abort write that would have saved the partial fails with
              // `SceneNotFound` and is swallowed during teardown. Deleting is
              // the same mechanism. Locked for the turn's duration; the other
              // rows stay editable, since only this scene is being written to
              // (review, #95).
              //
              // Keyed on the scene being *streamed into*, not the one on screen:
              // navigating away mid-turn does not move the write, so a lock that
              // followed the view would unlock the very row that is still being
              // written to — and lock an unrelated one.
              locked={s.id === streamingId}
              lockedReason={LOCKED_WHILE_GENERATING}
              onSelect={() => selectScene(s.id)}
              onRename={(title) => renameScene(s.id, title)}
              onDelete={() => deleteScene(s)}
            />
          ))}
        </div>
        <div className="rail-foot">
          <button className="btn-outline rail-world" onClick={() => navigate(`/campaigns/${cid}/world`)}>
            Campaign World ↗
          </button>
          {dt?.current && (
            <button className="rail-date" onClick={() => setShowCalendar((v) => !v)}
                    title="Calendar settings">
              {dt.current.weekday} {dt.current.friendly}
              {dt.current.holidays_today.length > 0 && (
                <span className="rail-holiday">✦ {dt.current.holidays_today[0]}</span>
              )}
            </button>
          )}
          <button className="rail-date" onClick={() => setShowStyle((v) => !v)}
                  title="Response preset and length settings">
            Response
          </button>
        </div>
      </aside>
      )}
      <section className="main">
        {showCalendar && (
          <div className="panel-slot">
            <CalendarConfig cid={cid} />
          </div>
        )}
        {showMechanics && (
          <div className="panel-slot">
            <MechanicsConfig cid={cid} />
          </div>
        )}
        {showStyle && (
          <div className="panel-slot">
            <ResponsePresetPicker scope="campaign" cid={cid}
                                  onChanged={() => activeId && selectScene(activeId)} />
          </div>
        )}
        {/* `ctxKey` bumps on every successful absorb save (and on scene select),
            which is what re-reads a ledger the user left open across one — the
            mount alone only covers toggling it shut and back. */}
        {showLedger && <LedgerPanel cid={cid} refreshKey={ctxKey} />}
        {showChanges && <ChangesPanel cid={cid} />}
        {editFailures.length > 0 && (
          <div className="mechanics-notice">
            <p>{editFailures.length} change{editFailures.length === 1 ? "" : "s"} did not apply</p>
            {editFailures.map((f, i) => (
              <p className="field-hint" key={i}>{f.label}: {f.reason} ({f.kind})</p>
            ))}
            <button className="subtle" onClick={() => setEditFailures([])}>Dismiss</button>
          </div>
        )}
        {absorb && (
          <div className="absorb-panel">
            <h4>Review scene summary</h4>
            <label className="field-hint" htmlFor="absorb-oneline">One line</label>
            <input id="absorb-oneline" aria-label="Scene one-line" value={absorb.one_line}
                   onChange={(e) => setAbsorb({ ...absorb, one_line: e.target.value })} />
            <label className="field-hint" htmlFor="absorb-summary">Summary</label>
            <textarea id="absorb-summary" aria-label="Scene summary" rows={5} value={absorb.summary}
                      onChange={(e) => setAbsorb({ ...absorb, summary: e.target.value })} />
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
                    has its own scoped Retry below; the dossier phase has none, so
                    the honest advice is the setting, not a destructive re-run. */}
                <p className="field-hint">
                  Cut short: {budgetCutPhases.map((p) => PHASE_LABELS[p.name]).join(", ")}. The
                  summary and its edits above are complete and safe to save. Raise the absorb
                  budget on the Configuration page so the next scene gets the rest.
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
                <button onClick={retryAudit}>Retry validation</button>
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
                <h5>Proposed changes</h5>
                {editRows.map((e, i) => (editBand(e) === "low" ? null : renderEditRow(e, i)))}
                {lowRows.length > 0 && (
                  <div className="absorb-low">
                    {/* The count is stated whether or not the section is open:
                        a proposal withheld from the default approval has to be
                        visible AS withheld, or routing becomes a silent drop. */}
                    <button className="subtle" aria-expanded={showLow}
                            onClick={() => setShowLow((v) => !v)}>
                      {showLow ? "Hide" : "Show"} {lowRows.length} low-confidence
                      {lowRows.length === 1 ? " change" : " changes"}
                    </button>
                    {!showLow && (
                      <p className="field-hint">
                        Not approved by default — the transcript does not clearly support them.
                      </p>)}
                    {showLow && lowRows.map(([e, i]) => renderEditRow(e, i))}
                  </div>)}
              </div>
            )}
            {saveError && (
              <div className="mechanics-notice">
                <p>Could not save this review: {saveError}</p>
                <button className="subtle" onClick={saveAbsorb} disabled={saving}>
                  Try saving again</button>
              </div>
            )}
            <div className="form-actions">
              <button className="subtle" disabled={saving}
                      onClick={() => { setAbsorb(null); setAbsorbSid(null); setEditRows([]);
                                       setEditFailures([]); setSaveError(null);
                                       setConflicts([]); }}>Cancel</button>
              <button className="primary" onClick={saveAbsorb} disabled={saving}>
                {saving ? "Saving…" : "Save summary"}</button>
            </div>
          </div>
        )}
        {!ready && (
          <div className="banner">
            No LLM connection ready. <Link to="/config">Set one up in Config</Link>.
          </div>
        )}
        {error && (
          <div className="banner error-banner">
            <span>{error.text}</span>
            {error.retryable && (
              <button className="retry" onClick={retry} disabled={busy || rolling}>
                Retry
              </button>
            )}
          </div>
        )}
        {activeId && messages.length === 0 && (
          <CastPanel
            cid={cid}
            sid={activeId}
            ready={ready}
            onSeeded={() => selectScene(activeId)}
            onSceneRenamed={sceneRenamed}
            initialPrompt={seedPrompt?.sid === activeId ? seedPrompt.prompt : undefined}
            pcless={activePcless}
            sceneLocked={sceneLocked}
            onRenaming={markRenaming}
          />
        )}
        {activeId && (
          <h2 className="scene-title">
            {scenes.find((s) => s.id === activeId)?.title ?? ""}
            {activePcless && <span className="chip on offscreen-badge">Offscreen</span>}
          </h2>
        )}
        <div className={"stream" + (colorQuotes ? " color-quotes" : "")} ref={streamRef}
             onScroll={onStreamScroll}>
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
                    <button className="plate-avatar" aria-label={`Open ${run.speaker} record`}
                            onClick={() => setDrawer({ type: "actor", kind: run.actor!.kind, id: run.actor!.id })}>
                      <Portrait src={plateAvatar(run)} name={run.speaker} />
                    </button>
                    <button className="plate-name"
                            onClick={() => setDrawer({ type: "actor", kind: run.actor!.kind, id: run.actor!.id })}>
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
                <div className={`msg ${m.role}`} key={index}>
                  <span className="msg-gutter">
                    {editing?.index !== index && !busy && (
                      <span className="gutter-icons">
                        {index === rerollAt && canReroll && (
                          <button className="msg-edit" title="Reroll" aria-label="Reroll"
                                  disabled={rolling} onClick={() => setRerollPrompt("")}>↻</button>
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
                        {m.speaker !== ROLL_SPEAKER && (
                          <button className="msg-edit" title="Edit message" aria-label={`Edit message ${index + 1}`}
                                  disabled={rolling}
                                  onClick={() => setEditing({ index, text: m.content })}>✎</button>
                        )}
                      </span>
                    )}
                    {rerollPrompt !== null && !busy &&
                     index === rerollAt && canReroll && (
                      <span className="reroll-pop">
                        <input
                          autoFocus
                          placeholder="Guide the reroll (optional)…"
                          aria-label="Reroll guidance"
                          value={rerollPrompt}
                          onChange={(e) => setRerollPrompt(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") reroll();
                            if (e.key === "Escape") setRerollPrompt(null);
                          }}
                        />
                        <button className="btn-chrome" onClick={() => reroll()} disabled={rolling}>Reroll ▸</button>
                      </span>
                    )}
                  </span>
                  <div className="msg-body">
                    {editing?.index === index ? (
                      <div className="msg-edit-form">
                        <textarea aria-label="Edit message" rows={4} value={editing.text}
                                  onChange={(e) => setEditing({ index, text: e.target.value })} />
                        <div className="form-actions">
                          <button className="subtle" onClick={() => setEditing(null)}>Cancel</button>
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
          {directorNote && busy && (
            <div className="run director-note">
              <div className="msg assistant">
                <span className="msg-gutter" />
                <div className="msg-body">🎬 {directorNote}</div>
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
                  <RenderedMarkdown content={streaming} />
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
        <div className="inputbar">
          <button className="roll-btn"
                  title={sceneLocked ? LOCKED_WHILE_GENERATING : "Roll dice"}
                  aria-label="Roll dice"
                  disabled={!activeId || busy || sceneLocked || messages.length === 0}
                  onClick={toggleRollPop}>
            🎲
          </button>
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
                  if (e.key === "Enter") doRoll();
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
                  if (e.key === "Enter") doRoll();
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
            placeholder={activePcless ? "Direct the scene (optional)…" : "Speak your intent…"}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
          />
          <div className="response-length-chip" ref={responseChipRef}>
            <button type="button" className="chip-toggle" aria-haspopup="listbox"
                    aria-expanded={responseChipOpen}
                    onClick={() => setResponseChipOpen((v) => !v)}>
              Response length: {responseChipLabel}
              {/* A one-shot pick and an inherited setting read identically
                  without this — and they mean very different things: one is
                  spent by the next reply, the other is the scene's standing
                  answer. */}
              {responseChipPending && <span className="chip-oneshot">next reply only</span>}
            </button>
            {responseChipPending && (
              <button type="button" className="chip-clear" title="Cancel the one-shot pick"
                      aria-label="Cancel the one-shot response length"
                      onClick={clearResponseOverride}>×</button>
            )}
            {responseChipOpen && (
              <ul className="chip-menu" role="listbox" aria-label="Response length options">
                {responsePresets.map((p) => (
                  <li key={p.id} role="option" aria-selected={p.id === responseChipPresetId}
                      onClick={() => chooseResponseOverride(p.id)}>
                    {p.name}
                  </li>
                ))}
              </ul>
            )}
          </div>
          {/* Replaces Send rather than sitting beside it: Send is already
              disabled for the whole turn, so the slot is dead space at exactly
              the moment a way out is wanted. */}
          {busy ? (
            <button className="send cancel-turn" onClick={cancelTurn}>Stop ■</button>
          ) : (
            <button className="send" onClick={send} disabled={rolling || renamesInFlight > 0}>
              {!input.trim() ? "Continue ▶" : "Send ▸"}
            </button>
          )}
        </div>
      </section>
      {inspectorCollapsed ? (
        <button className="inspector-tab" aria-label="Expand sidebar" onClick={toggleInspector}>‹</button>
      ) : (
        <div className="inspector-slot">
          <button className="inspector-collapse" aria-label="Collapse sidebar" onClick={toggleInspector}>›</button>
          {activeId && (
            <SceneInspector cid={cid} sid={activeId} refreshKey={ctxKey}
                            onSceneChanged={() => selectScene(activeId)}
                            onSceneRenamed={sceneRenamed} pcless={activePcless}
                            sceneLocked={sceneLocked}
                            onRenaming={markRenaming}
                            posts={messages.length} />
          )}
        </div>
      )}
      {drawer && activeId && (
        <RecordDrawer cid={cid} sid={activeId} target={drawer} onClose={() => setDrawer(null)} />
      )}
      {chooserOpen && (
        <NewSceneChooser cid={cid} afterSid={activeId} ready={ready}
                         onClose={() => setChooserOpen(false)} onCreated={sceneCreated} />
      )}
      </div>
    </div>
  );
}
