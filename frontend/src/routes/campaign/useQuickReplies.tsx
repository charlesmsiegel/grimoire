import type { Dispatch, SetStateAction } from "react";
import { useCallback, useEffect, useMemo, useState } from "react";
import type { Message } from "../../api/client";
import {
  api,
  type QuickReply, type QuickReplyTask
} from "../../api/client";
import { errorText } from "../../api/errors";
import { taskKey } from "./campaignShared";

export function useQuickReplies({
  cid, activeId, busy, rolling, renamesInFlight, canGenerate, followTail, sendText, doRoll, setQuickNotice,
  newScene, sceneLocked, ready, messages, firstIndex, currentCampaign, selected, setCtxKey, insertQuick,
}: {
  cid: string;
  activeId: string | null;
  busy: boolean;
  rolling: boolean;
  renamesInFlight: number;
  canGenerate: boolean;
  followTail: () => void;
  sendText: (id: string, content: string, director: boolean, recover: boolean, speakerRef?: string) => Promise<void>;
  doRoll: (notation: string, label: string | undefined) => Promise<"skipped" | "ok" | { error: string; }>;
  setQuickNotice: Dispatch<SetStateAction<{ cid: string; sid: string; text: string; } | null>>;
  newScene: () => void;
  sceneLocked: boolean;
  ready: boolean;
  messages: Message[];
  firstIndex: number;
  currentCampaign: () => string;
  selected: () => Readonly<{ cid: string; sid: string; }> | null;
  setCtxKey: Dispatch<SetStateAction<number>>;
  insertQuick: (text: string, director: boolean) => void;
}) {
  // The composer's quick replies: the campaign's effective set (world replies
  // with the campaign's layered on), read per campaign. Tagged with the
  // campaign it was read for, so a set still on screen across a campaign
  // switch is never offered for the new one.
  const [quickSet, setQuickSet] = useState<{ cid: string; replies: QuickReply[] } | null>(null);
  // The two manual tasks in flight, from the strip or the inspector, keyed
  // `${origin}:${cid}/${sid}:${task}` -- so the same task is never started
  // twice from two surfaces, and a run in one scene never holds another's.
  // Bumped by an `opener` quick reply: the cast panel expands and focuses its
  // opener prompt (see `CastPanel`'s `openerRequest`).
  const [openerRequest, setOpenerRequest] = useState(0);
  const [taskBusy, setTaskBusy] = useState<Record<string, true>>({});
  const markTask = useCallback((key: string, on: boolean) => {
    setTaskBusy((cur) => {
      if (on === !!cur[key]) return cur;
      const next = { ...cur };
      if (on) next[key] = true;
      else delete next[key];
      return next;
    });
  }, []);
  useEffect(() => {
    let live = true;
    api.getEffectiveQuickReplies(cid)
      .then((b) => { if (live) setQuickSet({ cid, replies: b.replies }); })
      // A set that cannot be read is a strip that is not there, not a page
      // that is broken.
      .catch(() => { if (live) setQuickSet({ cid, replies: [] }); });
    return () => { live = false; };
  }, [cid]);
  const stripSummary = !!activeId && !!taskBusy[taskKey("strip", cid, activeId, "rolling_summary")];
  const stripBreak = !!activeId && !!taskBusy[taskKey("strip", cid, activeId, "scene_break")];
  // Stable unless one of the two flags moves: the inspector is memoized.
  const stripTasks = useMemo(() => ({ rolling_summary: stripSummary, scene_break: stripBreak }),
    [stripSummary, stripBreak]);
  // Keyed on the scene it was handed to, so a run the inspector started in one
  // scene clears its own flag even after the reader has moved on.
  const onInspectorTaskBusy = useCallback(
    (task: "rolling_summary" | "scene_break", on: boolean) => {
      if (activeId) markTask(taskKey("inspector", cid, activeId, task), on);
    }, [cid, activeId, markTask]);
  function taskRunning(task: "rolling_summary" | "scene_break"): boolean {
    return !!activeId && (!!taskBusy[taskKey("strip", cid, activeId, task)]
      || !!taskBusy[taskKey("inspector", cid, activeId, task)]);
  }

  /** A quick reply's send: the canned text, as the player or as a director
   *  note, through the same path Send takes -- but the composer is neither read
   *  nor cleared, and a failure does not hand the text back (it is still on the
   *  button). The guard is Send's own. */
  async function sendQuick(text: string, director: boolean) {
    if (!activeId || busy || rolling || renamesInFlight || !canGenerate || !text.trim()) return;
    followTail();
    try { await sendText(activeId, text.trim(), director, false); }
    catch (err: unknown) {
      setQuickNotice({ cid, sid: activeId, text: `Reply failed: ${errorText(err)}` });
    }
  }

  /** A saved roll: the dice button's route, with its own notice on failure. */
  async function rollQuick(notation: string, label: string | undefined) {
    const forCid = cid;
    const sid = activeId;
    const outcome = await doRoll(notation, label);
    if (sid && typeof outcome === "object")
      setQuickNotice({ cid: forCid, sid, text: `Roll failed: ${outcome.error}` });
  }

  /** A task on the strip: the rolling summary or the scene-break check run now
   *  (forced, and bounded at the transcript on screen so a fold cannot swallow
   *  a post the reply to which has not landed), or the next-scene chooser. */
  async function runQuickTask(task: QuickReplyTask) {
    if (task === "next_scene") {
      newScene();
      return;
    }
    if (!activeId || sceneLocked || !ready || messages.length === 0 || taskRunning(task)) return;
    const forCid = cid;
    const sid = activeId;
    const key = taskKey("strip", forCid, sid, task);
    // Absolute: the transcript is read in windows, and `messages[0]` is post
    // `firstIndex`.
    const upto = firstIndex + messages.length;
    const here = () => currentCampaign() === forCid && (selected()?.sid ?? null) === sid;
    const notice = (text: string) => { if (here()) setQuickNotice({ cid: forCid, sid, text }); };
    markTask(key, true);
    try {
      if (task === "rolling_summary") {
        const r = await api.refreshRollingSummary(forCid, sid, true, upto);
        if (r.refreshed) { if (here()) setCtxKey((n) => n + 1); }
        else notice("Summary already current — nothing new to fold");
      } else {
        const r = await api.askSceneBreak(forCid, sid, true, upto);
        if (r.asked) { if (here()) setCtxKey((n) => n + 1); }
        else notice("No scene-break question asked");
      }
    } catch (err: unknown) {
      notice(`${task === "rolling_summary" ? "Summary" : "Scene-break check"} failed: ${errorText(err)}`);
    } finally {
      markTask(key, false);
    }
  }

  function runQuickReply(r: QuickReply) {
    setQuickNotice(null);
    switch (r.kind) {
      case "send":
      case "direct":
        if (r.mode === "insert") insertQuick(r.text ?? "", r.kind === "direct");
        else void sendQuick(r.text ?? "", r.kind === "direct");
        return;
      case "roll":
        void rollQuick(r.notation ?? "", r.roll_label);
        return;
      case "task":
        if (r.task) void runQuickTask(r.task);
        return;
      case "opener":
        setOpenerRequest((n) => n + 1);
        return;
      default:
        return;
    }
  }
  return { taskRunning, stripTasks, onInspectorTaskBusy, openerRequest, quickSet, runQuickReply };
}
