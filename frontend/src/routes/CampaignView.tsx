import { memo, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  api, type Actor, type AbsorbPhase, type Dossiers, type EditConflict, type SceneMeta,
  type Message, type RosterEntry, type SceneAbsorb,
  type SceneDatetime, type StagedEdit, type ProposalRecord, type SceneCheckActor,
  type ResponsePresetSummary, type ResponseOverride, type ResponseBundle,
} from "../api/client";
import type { ChatEvent } from "../api/stream";
import { EditableRow } from "../components/EditableRow";
import { CastPanel } from "../components/CastPanel";
import { NewSceneChooser } from "../components/NewSceneChooser";
import { ChangesPanel } from "../components/ChangesPanel";
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
  const [error, setError] = useState<string | null>(null);
  const [ctxKey, setCtxKey] = useState(0);
  const [editing, setEditing] = useState<{ index: number; text: string } | null>(null);
  const [rerollPrompt, setRerollPrompt] = useState<string | null>(null); // null = popover closed
  // null = closed; open holds the in-progress notation/label/error, plus
  // the popover's mode (dice notation vs. a module check) and check fields.
  const [rollForm, setRollForm] = useState<{
    mode: "dice" | "check"; notation: string; label: string; error: string | null;
    checkActor: string; checkId: string; difficulty: number | ""; modifier: number;
  } | null>(null);
  const [checkActors, setCheckActors] = useState<SceneCheckActor[]>([]);
  const checksFetched = useRef(false); // one getSceneChecks per popover session
  const [rolling, setRolling] = useState(false);
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
  const [absorb, setAbsorb] = useState<SceneAbsorb | null>(null);
  // The scene this review was absorbed FROM. Switching scenes leaves the panel
  // open, so saving against the currently selected scene would commit scene A's
  // review onto scene B (#235).
  const [absorbSid, setAbsorbSid] = useState<string | null>(null);
  const [absorbing, setAbsorbing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [editRows, setEditRows] = useState<(StagedEdit & { approved: boolean })[]>([]);
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

  async function selectScene(id: string) {
    // selectScene also runs to *refresh* the current scene (runStream's
    // finally, doRoll/doCheck, saveEdit, …) — only an actual scene switch
    // should clear the chip/popover synchronously below; clearing on every
    // refresh would tear down and re-mount a live SSE-delivered proposal
    // for no reason (flicker, and a stale ref by the time the re-fetch lands).
    const switchingScenes = id !== activeId;
    const token = ++windowTokenRef.current; // retires any page still in flight
    setActiveId(id);
    if (switchingScenes) {
      // clear the previous scene's chip/popover synchronously so scene A's
      // proposal never renders against scene B while the fetch below is in
      // flight (and so a stale checkActors list can't leak across scenes).
      setProposal(null);
      setRollForm(null);
      // a one-shot override belongs to the turn the player picked it for, on
      // the scene they picked it on — switching scenes must not carry it
      // silently onto an unrelated scene's next reply.
      setPendingResponse(null);
      // a new scene opens at its most recent page, at the bottom
      windowSizeRef.current = PAGE_SIZE;
      atBottomRef.current = true;
    }
    api.getSceneDatetime(cid, id).then(setDt).catch(() => setDt(null));
    api.getCast(cid, id).then(setCast).catch(() => setCast([]));
    api.listAppearances(cid).then(setRoster).catch(() => setRoster([]));
    // a proposal already superseded/narrated/declined is no longer live —
    // RollProposal only knows how to render pending or resolved records.
    api.getRollProposal(cid, id).then((r) => setProposal(
      r.record && r.record.status !== "superseded" && r.record.status !== "narrated"
        && r.record.status !== "declined" ? r.record : null,
    )).catch(() => setProposal(null));
    // Re-read on every selectScene, refresh included: the inspector's picker
    // calls onSceneChanged after a save, so this is what keeps the chip from
    // showing a preset the scene no longer has.
    Promise.resolve(api.getSceneResponse?.(cid, id))
      .then((r) => setSceneResponse(r ?? null))
      .catch(() => setSceneResponse(null));
    const scene = await api.getScene(cid, id, { limit: windowSizeRef.current });
    if (windowTokenRef.current !== token) return; // a later select already landed
    setMessages(scene.messages);
    // an unwindowed reply (no `offset`) is the whole transcript, which starts at 0
    setFirstIndex(scene.offset ?? 0);
    setHasUserPost(scene.has_user_message ?? null);
    setSceneResponsePreset(scene.meta.response_preset ?? "");
    setStreaming("");
    setCtxKey((n) => n + 1);
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
  // carries every *persisted* reference across; an open review holds two more that
  // live only in this browser and no server-side repointer can see:
  //   - `absorbSid`, the id its save and its audit retry POST; and
  //   - `payload.scene` on each staged plot edit, which absorb.materialize
  //     embedded and apply_edits passes straight to plot.set_movement — so a save
  //     after a rename would append beats pointing at a scene that is gone.
  function reviewSceneRenamed(oldId: string, newId: string) {
    setAbsorbSid((s) => (s === oldId ? newId : s));
    setEditRows((rows) => rows.map((r) => (
      r.kind === "plot" && r.payload?.scene === oldId
        ? { ...r, payload: { ...r.payload, scene: newId } } : r)));
  }

  async function renameScene(id: string, title: string) {
    const { id: newId } = await api.renameScene(cid, id, title);
    if (activeId === id) setActiveId(newId);
    setSeedPrompt((p) => (p && p.sid === id ? { ...p, sid: newId } : p));
    reviewSceneRenamed(id, newId);
    setScenes(await api.listScenes(cid));
  }

  // the first date set renames the scene file — re-list and adopt the new id
  async function sceneRenamed(id: string) {
    setSeedPrompt((p) => (p && p.sid === activeId ? { ...p, sid: id } : p));
    if (activeId) reviewSceneRenamed(activeId, id);
    setScenes(await api.listScenes(cid));
    selectScene(id);
  }

  async function deleteScene(s: SceneMeta) {
    if (!window.confirm(`Delete '${s.title}'?`)) return;
    await api.deleteScene(cid, s.id);
    const list = await api.listScenes(cid);
    setScenes(list);
    if (activeId === s.id) {
      if (list.length) selectScene(list[0].id);
      else {
        windowTokenRef.current += 1; // drop any page still in flight for it
        setActiveId(null);
        setMessages([]);
        setFirstIndex(0);
        setHasUserPost(null);
      }
    }
  }

  // Returns whether the turn actually landed (no thrown error and no e.error
  // event) — callers use this to decide whether a pending one-shot response
  // override was honoured and can be cleared, or must survive for retry/reroll.
  async function runStream(id: string, start: (onEvent: (e: ChatEvent) => void) => Promise<void>) {
    setBusy(true);
    setError(null);
    let acc = "";
    let landed = true;
    try {
      await start((e) => {
        if (e.delta) {
          acc += e.delta;
          setStreaming(acc);
        } else if (e.error) {
          setError(e.error.detail);
          landed = false;
        } else if (e.proposal) {
          setProposal({ id: e.proposal.id, status: "pending", payload: e.proposal, resolution: null });
        }
      });
    } catch (err: any) {
      setError(err.detail ?? String(err));
      landed = false;
    } finally {
      setStreaming("");
      setBusy(false);
      // the reply is persisted as per-speaker posts — re-fetch to show them
      // (selectScene also bumps ctxKey and refreshes the player name)
      await selectScene(id);
    }
    return landed;
  }

  async function send() {
    if (busy || rolling) return;
    // a new turn supersedes any pending proposal durably on the backend —
    // clear the chip optimistically rather than wait for the re-fetch.
    setProposal(null);
    const content = input.trim();
    let id = activeId;
    if (!id) {
      if (!content) return;
      id = (await api.createScene(cid)).id;
      setScenes(await api.listScenes(cid));
      setActiveId(id);
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
        const landed = await runStream(id, (onEvent) => pendingResponse
          ? api.chat(cid, id!, content, onEvent, pendingResponse)
          : api.chat(cid, id!, content, onEvent));
        if (landed) setPendingResponse(null);
      } finally {
        setDirectorNote(null);
      }
      return;
    }
    setMessages((m) => [...m, { role: "user", content }]);
    const landed = await runStream(id, (onEvent) => pendingResponse
      ? api.chat(cid, id!, content, onEvent, pendingResponse)
      : api.chat(cid, id!, content, onEvent));
    if (landed) setPendingResponse(null);
  }

  async function saveEdit() {
    if (!editing || !activeId) return;
    await api.editMessage(cid, activeId, editing.index, editing.text);
    setEditing(null);
    await selectScene(activeId);
  }

  async function retry() {
    if (!activeId || busy || rolling) return;
    const landed = await runStream(activeId, (onEvent) => pendingResponse
      ? api.retry(cid, activeId, onEvent, pendingResponse)
      : api.retry(cid, activeId, onEvent));
    if (landed) setPendingResponse(null);
  }

  async function reroll() {
    if (!activeId || busy || rolling) return;
    const guidance = (rerollPrompt ?? "").trim();
    setRerollPrompt(null);
    // one turn is a run of assistant posts — drop the whole trailing run, but
    // keep any trailing transition lines, which the backend also preserves
    setMessages((m) => {
      let end = m.length;
      const kept: Message[] = [];
      while (end > 0 && m[end - 1].speaker === TRANSITION_SPEAKER) kept.unshift(m[--end]);
      while (end > 0 && m[end - 1].role === "assistant") end--;
      return [...m.slice(0, end), ...kept];
    });
    // omit trailing arguments entirely for a plain reroll (an explicit
    // undefined would change the call shape) — but a pending one-shot
    // override must ride regenerate too, same promise as retry.
    const landed = await runStream(activeId, (onEvent) => {
      if (guidance && pendingResponse) return api.regenerate(cid, activeId!, onEvent, guidance, pendingResponse);
      if (guidance) return api.regenerate(cid, activeId!, onEvent, guidance);
      if (pendingResponse) return api.regenerate(cid, activeId!, onEvent, undefined, pendingResponse);
      return api.regenerate(cid, activeId!, onEvent);
    });
    if (landed) setPendingResponse(null);
  }

  async function doRoll() {
    if (!activeId || busy || rolling || !rollForm) return;
    const notation = rollForm.notation.trim();
    if (!notation) return;
    setRolling(true);
    try {
      await api.roll(cid, activeId, notation, rollForm.label.trim() || undefined);
      setRollForm(null);
      await selectScene(activeId);
    } catch (err: any) {
      setRollForm({ ...rollForm, error: err.detail ?? String(err) });
    } finally {
      setRolling(false);
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

  async function doCheck() {
    if (!activeId || busy || rolling || !rollForm) return;
    if (!rollForm.checkActor || !rollForm.checkId) return;
    setRolling(true);
    try {
      const body: { check: string; actor: string; difficulty?: number; modifier: number } = {
        check: rollForm.checkId, actor: rollForm.checkActor, modifier: rollForm.modifier,
      };
      if (rollForm.difficulty !== "") body.difficulty = rollForm.difficulty;
      await api.rollCheck(cid, activeId, body);
      setRollForm(null);
      await selectScene(activeId);
    } catch (err: any) {
      setRollForm({ ...rollForm, error: err.detail ?? String(err) });
    } finally {
      setRolling(false);
    }
  }

  // runStream's finally always re-fetches the scene (selectScene), which
  // also re-fetches the proposal record — so a stale/lost-CAS 409 from
  // resolveProposal surfaces the server's current record without extra
  // plumbing here; clearing eagerly avoids a stale chip lingering mid-stream.
  async function resolve(body: ResolveBody) {
    if (!activeId) return;
    setProposal(null);
    await runStream(activeId, (onEvent) => api.resolveProposal(cid, activeId!, body, onEvent));
  }

  // A scene already in the chronicle comes back as 409 "already_absorbed" rather
  // than silently re-absorbing: lore edits append and plot movements add a beat,
  // so a second pass duplicates both (#235). Confirm, then retry with force.
  async function endScene() {
    if (!activeId || absorbing) return;
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
      setEditRows(a.edits.map((e) => ({ ...e, approved: true })));
    } catch (err: any) {
      setError(err.detail ?? String(err));
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
        ...res.edits.map((e) => ({ ...e, approved: true })),
      ]);
      // Conflicts are bound to row numbers, and this rebuilds the array — so
      // any that survived a refusal would now point at whichever row inherited
      // their index. Sheet edits never conflict, so there is nothing to carry
      // over; the next save re-reports whatever is still drifted.
      setConflicts([]);
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
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
          <button className="sub-changes" onClick={() => setShowChanges((v) => !v)}>
            {showChanges ? "Close" : "Changes"}
          </button>
          <button className="sub-mechanics" onClick={() => setShowMechanics((v) => !v)}>
            {showMechanics ? "Close" : "Mechanics"}
          </button>
          <button className="sub-end" onClick={endScene}
                  disabled={!activeId || absorbing || busy}>
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
                {editRows.map((e, i) => {
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
                        {conflict && <span className="chip on absorb-conflict-badge">Changed</span>}
                      </label>
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
                })}
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
            <span>{error}</span>
            <button className="retry" onClick={retry} disabled={busy || rolling}>
              Retry
            </button>
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
                        {m.speaker !== ROLL_SPEAKER && (
                          <button className="msg-edit" title="Edit message" aria-label={`Edit message ${index + 1}`}
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
                        <button className="btn-chrome" onClick={reroll} disabled={rolling}>Reroll ▸</button>
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
                          <button className="primary" onClick={saveEdit}>Save</button>
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
          <RollProposal key={proposal.id} record={proposal} busy={busy} onResolve={resolve} />
        )}
        <div className="inputbar">
          <button className="roll-btn" title="Roll dice" aria-label="Roll dice"
                  disabled={!activeId || busy || messages.length === 0}
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
          <button className="send" onClick={send} disabled={busy || rolling}>
            {busy ? "…" : !input.trim() ? "Continue ▶" : "Send ▸"}
          </button>
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
                            onSceneRenamed={sceneRenamed} pcless={activePcless} />
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
