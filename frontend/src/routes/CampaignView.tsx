import { memo, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  api, type Actor, type SceneMeta, type Message, type RosterEntry, type SceneAbsorb,
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
  const [absorbing, setAbsorbing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [editRows, setEditRows] = useState<(StagedEdit & { approved: boolean })[]>([]);
  const [sheetFailures, setSheetFailures] = useState<
    { id: string; reason: string; kind: "conflict" | "error"; label: string }[]>([]);
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

  useEffect(() => {
    streamRef.current?.scrollTo({ top: streamRef.current.scrollHeight });
  }, [messages, streaming]);

  // unstamped user lines fall back to the sole player's name on their plate
  const playerName = useMemo(() => {
    const players = cast.filter((a) => a.role === "player");
    return players.length === 1 ? players[0].name : null;
  }, [cast]);

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
    const scene = await api.getScene(cid, id);
    setMessages(scene.messages);
    setSceneResponsePreset(scene.meta.response_preset ?? "");
    setStreaming("");
    setCtxKey((n) => n + 1);
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

  async function renameScene(id: string, title: string) {
    const { id: newId } = await api.renameScene(cid, id, title);
    if (activeId === id) setActiveId(newId);
    setSeedPrompt((p) => (p && p.sid === id ? { ...p, sid: newId } : p));
    setScenes(await api.listScenes(cid));
  }

  // the first date set renames the scene file — re-list and adopt the new id
  async function sceneRenamed(id: string) {
    setSeedPrompt((p) => (p && p.sid === activeId ? { ...p, sid: id } : p));
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
        setActiveId(null);
        setMessages([]);
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
    setSheetFailures([]);
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
    if (!absorb || !activeId || saving) return;
    setSaving(true);
    // captured before editRows is cleared below -- sheet_failures only carry
    // id/reason/kind, so the row's label has to come from what was on screen.
    const labels = new Map(editRows.map((e) => [e.id, e.label]));
    try {
      const res = await api.saveChronicle(cid, activeId, {
        one_line: absorb.one_line, summary: absorb.summary, keywords: absorb.keywords,
        timeline_events: absorb.timeline_events,
        edits: editRows.filter((e) => e.approved).map(({ approved, ...e }) => e) });
      setSheetFailures(res.sheet_failures.map((f) => ({ ...f, label: labels.get(f.id) ?? f.id })));
      setAbsorb(null);
      setEditRows([]);
      setCtxKey((n) => n + 1);
    } catch (err: any) {
      setError(err.detail ?? String(err));
    } finally {
      setSaving(false);
    }
  }

  // Replaces absorb.mechanics with a fresh audit and swaps in its sheet
  // proposals, leaving every other staged edit (prose/relationship/etc.)
  // exactly as the reviewer had it.
  async function retryAudit() {
    if (!activeId) return;
    try {
      const res = await api.retryAudit(cid, activeId);
      setAbsorb((a) => (a ? { ...a, mechanics: res.mechanics } : a));
      setEditRows((rows) => [
        ...rows.filter((r) => r.kind !== "sheet"),
        ...res.edits.map((e) => ({ ...e, approved: true })),
      ]);
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
  const rerollIndex = (() => {
    let i = messages.length - 1;
    while (i >= 0 && messages[i].speaker === TRANSITION_SPEAKER) i--;
    return i;
  })();
  const canReroll = rerollIndex >= 0 &&
    messages[rerollIndex].role === "assistant" &&
    messages[rerollIndex].speaker !== ROLL_SPEAKER &&
    messages.some((x) => x.role === "user");

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
  messages.forEach((m, index) => {
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
        {sheetFailures.length > 0 && (
          <div className="mechanics-notice">
            <p>{sheetFailures.length} sheet change{sheetFailures.length === 1 ? "" : "s"} did not apply</p>
            {sheetFailures.map((f, i) => (
              <p className="field-hint" key={i}>{f.label}: {f.reason} ({f.kind})</p>
            ))}
            <button className="subtle" onClick={() => setSheetFailures([])}>Dismiss</button>
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
            {absorb.mechanics.status === "ok" && absorb.mechanics.warnings.length === 0 && (
              <p className="field-hint">mechanics audited clean</p>)}
            {absorb.mechanics.warnings.length > 0 && (
              <ul className="mechanics-warnings">
                {absorb.mechanics.warnings.map((w, i) => <li key={i}>⚠ {w}</li>)}
              </ul>)}
            {(absorb.mechanics.status === "failed" || absorb.mechanics.status === "degraded") && (
              <div className="mechanics-notice">
                <p>{absorb.mechanics.status === "failed"
                    ? `Mechanics validation failed: ${absorb.mechanics.reason}`
                    : "Some mechanics findings could not be validated"}</p>
                {absorb.mechanics.dropped.map((d, i) => (
                  <p className="field-hint" key={i}>{d.id} {d.field ?? ""}: {d.reason}</p>))}
                <button onClick={retryAudit}>Retry validation</button>
              </div>)}
            {(absorb.dossiers.status === "failed" || absorb.dossiers.status === "degraded") && (
              <div className="mechanics-notice">
                {/* "prepared", not "refreshed": the dossier is staged here and only
                    written when the review is saved (#235). */}
                <p>{absorb.dossiers.failed.length === 0
                    ? `NPC dossier refresh failed: ${absorb.dossiers.reason}`
                    : absorb.dossiers.status === "failed"
                      ? "No NPC dossier could be prepared"
                      : "Some NPC dossiers could not be prepared"}</p>
                {absorb.dossiers.failed.map((d, i) => (
                  <p className="field-hint" key={i}>{d.id}: {d.reason}</p>))}
                {absorb.dossiers.skipped.length > 0 && (
                  <p className="field-hint">
                    Never attempted, skipped: {absorb.dossiers.skipped.join(", ")}
                  </p>)}
              </div>)}
            {editRows.length > 0 && (
              <div className="absorb-edits">
                <h5>Proposed changes</h5>
                {editRows.map((e, i) => {
                  const isNewRecord = e.kind === "new_character" || e.kind === "new_location" || e.kind === "new_lore";
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
                      </label>
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
            <div className="form-actions">
              <button className="subtle" disabled={saving}
                      onClick={() => { setAbsorb(null); setEditRows([]); setSheetFailures([]); }}>Cancel</button>
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
        <div className={"stream" + (colorQuotes ? " color-quotes" : "")} ref={streamRef}>
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
                        {index === rerollIndex && canReroll && (
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
                     index === rerollIndex && canReroll && (
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
