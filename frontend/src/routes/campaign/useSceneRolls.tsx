import { useEffect, useRef, useState } from "react";
import type { ProposalRecord, SceneCheckActor } from "../../api/client";
import {
  api
} from "../../api/client";
import { errorText } from "../../api/errors";
import type { ChatEvent } from "../../api/stream";
import { type RerollRoute } from "../../components/RerollRoute";
import { type ResolveBody } from "../../components/RollProposal";
import type { Current, DirectorNote } from "./campaignTypes";

export function useSceneRolls({
  activeId, busy, sceneLocked, rolling, takeRollLatch, cid, selectScene, askAfterPost, moduleBound,
  renamesInFlight, canGenerate, setProposalNow, runStream,
}: {
  activeId: string | null;
  busy: boolean;
  sceneLocked: boolean;
  rolling: boolean;
  takeRollLatch: (forSid: string) => () => void;
  cid: string;
  selectScene: (id: string, stillWanted?: Current, renamed?: boolean) => Promise<number>;
  askAfterPost: (id: string, upto: number) => void;
  moduleBound: boolean | null;
  renamesInFlight: number;
  canGenerate: boolean;
  setProposalNow: (record: ProposalRecord | null) => void;
  runStream: (id: string, start: (onEvent: (e: ChatEvent) => void, signal: AbortSignal, attempt: string, onIndex: (index: number) => void) => Promise<void>, recoverText?: () => void, rerolling?: boolean, durableText?: string, ephemeral?: boolean, note?: DirectorNote | null, route?: RerollRoute) => Promise<boolean>;
}) {

  // null = closed; open holds the in-progress notation/label/error, plus
  // the popover's mode (dice notation vs. a module check) and check fields.
  const [rollForm, setRollForm] = useState<{
    mode: "dice" | "check"; notation: string; label: string; error: string | null;
    checkActor: string; checkId: string; difficulty: number | ""; modifier: number;
  } | null>(null);
  const [checkActors, setCheckActors] = useState<SceneCheckActor[]>([]);
  const checksFetched = useRef(false);
  const [showRollSyntax, setShowRollSyntax] = useState(false);

  // `sceneLocked`, not just `busy`: a roll appends a transcript line, and a
  // reroll cancelled before its first token is at that moment waiting to put
  // the reply it deleted back. `restore_trailing_assistant_run` steps over
  // trailing *transitions* only — a roll deliberately blocks it, because the
  // line has to stay in lockstep with rolls.json — so a roll landing in the
  // flush window makes the restore refuse and the rerolled reply is gone for
  // good. The backend cannot rescue that one; only not racing it can (#95).
  /** Roll `notation` into the active scene: the popover's Roll ▸ and a saved
   *  roll on the strip both come here. Takes the roll latch and asks for the
   *  follow-ups exactly as before; says how it went rather than writing the
   *  popover's form error itself, since the strip has no form. */
  async function doRoll(notation: string, label: string | undefined):
    Promise<"skipped" | "ok" | { error: string }> {
    if (!activeId || busy || sceneLocked || rolling) return "skipped";
    if (!notation.trim()) return "skipped";
    const sid = activeId;
    const releaseLatch = takeRollLatch(sid);
    try {
      await api.roll(cid, sid, notation.trim(), label);
      const seen = await selectScene(sid);
      // The length the re-read saw is this write's boundary, for the reason the
      // turn loop passes one: nothing holds the scene once this returns.
      askAfterPost(sid, seen);   // a roll is a post too (#85, #84)
      return "ok";
    } catch (err: unknown) {
      return { error: errorText(err) };
    } finally {
      releaseLatch();
    }
  }

  /** The popover's Enter / Roll ▸. */
  async function rollFromForm() {
    if (!rollForm) return;
    const form = rollForm;
    const outcome = await doRoll(form.notation.trim(), form.label.trim() || undefined);
    if (outcome === "ok") setRollForm(null);
    else if (outcome !== "skipped") setRollForm({ ...form, error: outcome.error });
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
    setRollForm({
      mode: "dice", notation: "", label: "", error: null,
      checkActor: "", checkId: "", difficulty: "", modifier: 0
    });
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
    } catch (err: unknown) {
      setRollForm({ ...rollForm, error: errorText(err) });
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
    if (renamesInFlight || !canGenerate) return;
    setProposalNow(null);
    await runStream(activeId, (onEvent, signal, attempt, onIndex) =>
      api.resolveProposal(cid, activeId, body, onEvent, signal, attempt, onIndex));
  }
  return {
    setRollForm, doRoll, resolve, toggleRollPop, rollForm, enterCheckMode, checkActors, doCheck,
    rollFromForm, showRollSyntax, setShowRollSyntax,
  };
}
