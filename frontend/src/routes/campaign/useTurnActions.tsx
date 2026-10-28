import type { Dispatch, SetStateAction } from "react";
import { useRef, useState } from "react";
import type { NavigateFunction } from "react-router-dom";
import type { Message, ProposalRecord, ResponseFields } from "../../api/client";
import {
  api
} from "../../api/client";
import { errorText } from "../../api/errors";
import type { ChatEvent } from "../../api/stream";
import {
  TRANSITION_SPEAKER
} from "../../components/play/TranscriptPost";
import { canGenerateOnRoute, NO_REROLL_ROUTE, rerollOverrides, type RerollRoute } from "../../components/RerollRoute";
import { sceneUrl } from "./campaignShared";
import type { DirectorNote } from "./campaignTypes";
import type { RunSession } from "./useRunSession";

export function useTurnActions({
  busy, rolling, renamesInFlight, canGenerate, input, activeId, cid, loadScenes, currentCampaign,
  selectIdentity, navigate, consumeText, followTail, directing, setProposalNow, runStream, pendingResponse,
  recoverPrompt, setPendingResponse, session, showOptimistically, sceneLocked, editing, transcriptIsActive,
  extendDisabled, swipeTarget, messages, rerollAt, firstIndex, setReplayAt, fail,
}: {
  fail: (err: unknown, retryable?: boolean, from?: string) => void;
  busy: boolean;
  rolling: boolean;
  renamesInFlight: number;
  canGenerate: boolean;
  input: string;
  activeId: string | null;
  cid: string;
  loadScenes: () => Promise<void>;
  currentCampaign: () => string;
  selectIdentity: (scope: Readonly<{ cid: string; sid: string; }> | null) => void;
  navigate: NavigateFunction;
  consumeText: () => string;
  followTail: () => void;
  directing: boolean;
  setProposalNow: (record: ProposalRecord | null) => void;
  runStream: (id: string, start: (onEvent: (e: ChatEvent) => void, signal: AbortSignal, attempt: string, onIndex: (index: number) => void) => Promise<void>, recoverText?: () => void, rerolling?: boolean, durableText?: string, ephemeral?: boolean, note?: DirectorNote | null, route?: RerollRoute) => Promise<boolean>;
  pendingResponse: Partial<ResponseFields> | null;
  recoverPrompt: (pcid: string, sid: string, text: string, director?: boolean) => void;
  setPendingResponse: Dispatch<SetStateAction<Partial<ResponseFields> | null>>;
  session: Pick<RunSession, "clearNoteIf">;
  showOptimistically: (edit: (m: Message[]) => Message[]) => void;
  sceneLocked: boolean;
  editing: { index: number; text: string; } | null;
  transcriptIsActive: boolean;
  extendDisabled: boolean;
  swipeTarget: Message | null;
  messages: Message[];
  rerollAt: number;
  firstIndex: number;
  setReplayAt: Dispatch<SetStateAction<number | null>>;
}) {
  const [rerollPrompt, setRerollPrompt] = useState<string | null>(null); // null = popover closed
  // Which connection and model THIS reroll runs on (#77). One-shot, like the
  // guidance beside it and like `pendingResponse`: reset whenever the popover
  // opens, so a route chosen for one reroll cannot silently steer the next.
  const [rerollRoute, setRerollRoute] = useState<RerollRoute>(NO_REROLL_ROUTE);

  async function send(speakerRef?: string) {
    if (busy || rolling || renamesInFlight || !canGenerate) return;
    const content = input.trim();
    let id = activeId;
    if (!id) {
      if (!content) return;
      try { id = (await api.createScene(cid)).id; }
      catch (err: unknown) { fail(err, false); return; }
      try { await loadScenes(); }
      catch (err: unknown) {
        if (currentCampaign() === cid) fail(new Error(`The scene was created, but the scene list could not be refreshed: ${errorText(err)}`), false);
        return;
      }
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
      if (currentCampaign() !== cid) return;
      selectIdentity({ cid, sid: id });
      // Same pairing as the rename: an id adopted without the reader going
      // anywhere, so the URL follows it in the same batch with `replace`
      // (there was no scene here to go Back to). Without this the resolver
      // sees `activeId` moved while the URL still names no scene, and bounces
      // the turn onto whatever the list happens to sort first.
      void navigate(sceneUrl(cid, id), { replace: true });
    }
    consumeText();
    // the player just spoke: put them back at the tail even if they had
    // scrolled up into older history to re-read something
    followTail();
    await sendText(id, content, directing, true, speakerRef);
  }

  /** One turn's worth of text, sent to scene `id` -- the body of `send` from
   *  the point the composer has been read. Takes the text and the turn kind as
   *  arguments, so a quick reply can send without writing composer state first:
   *  it never reads or clears `input`, so a draft survives a quick reply.
   *
   *  `recover` is whether a failed turn hands `content` back to the composer
   *  (and arms the durable copy). True for what the player typed; false for a
   *  quick reply, whose canned text is re-tappable and would otherwise land in
   *  the box -- and flip its mode -- on a failure. The one-shot response
   *  targets are consumed exactly as Send consumes them. */
  async function sendText(id: string, content: string, director: boolean, recover: boolean,
    speakerRef?: string) {
    if (!canGenerate) return;
    // A new prompt supersedes a failed reroll: whatever Retry would have
    // repeated, the player has moved on from it.
    rerollToRetryRef.current = null;
    // a new turn supersedes any pending proposal durably on the backend —
    // clear the chip optimistically rather than wait for the re-fetch. Ordered,
    // so a read issued before this send cannot put the chip back afterwards.
    setProposalNow(null);
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
    const note = director && content ? { cid, sid: id, text: content } : null;
    // Trailing argument only when a chip named a speaker, so every other send
    // is the same call it always was.
    const speakerArg: [] | [string] = speakerRef ? [speakerRef] : [];
    if (director || !content) {
      try {
        const landed = await runStream(id,
          (onEvent, signal, attempt, onIndex) =>
            api.chat(cid, id, content, onEvent, pendingResponse ?? undefined,
              signal, attempt, onIndex, director, ...speakerArg),
          // An empty send has nothing to give back — it is the "next NPC round"
          // fast path, not words anyone typed.
          content && recover ? () => recoverPrompt(cid, id, content, true) : undefined,
          false, "", true, note);
        if (landed) setPendingResponse(null);
      } finally {
        session.clearNoteIf(note);
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
        api.chat(cid, id, content, onEvent, pendingResponse ?? undefined,
          signal, attempt, onIndex, false, ...speakerArg),
      recover ? () => recoverPrompt(cid, id, content) : undefined, false,
      // The words the player typed, held until the outcome proves them durable.
      recover ? content : "");
    if (landed) setPendingResponse(null);
  }

  /** A ledger reroll -- the swipe's ›, the reroll box (↻, `r`, → at the
   *  newest) and the Response variants disclosure alike. It streams the frozen
   *  snapshot and the server ignores `response`, so it never sends the pending
   *  one-shot override and never clears it: doing either would spend the
   *  player's next-turn length chip on a request that never used it. The
   *  legacy `reroll` path, which recomposes, still carries and spends it. */
  async function rerollResponse(id: string, guidance: string, route: RerollRoute = NO_REROLL_ROUTE) {
    if (!activeId || busy || rolling || sceneLocked || editing || renamesInFlight || !transcriptIsActive
      || !canGenerateOnRoute(canGenerate, route)) return;
    const sid = activeId;
    await runStream(sid, (onEvent, signal, attempt, onIndex) =>
      api.regenerateResponse(cid, sid, id, onEvent, { guidance, ...rerollOverrides(route) }, signal, attempt, onIndex),
      undefined, true, "", true, null, route);
    // No swipe refresh here: `runStream`'s finally asks again on every
    // outcome, including a failed generate that kept the previous variant.
  }

  /** Keep writing: continue the trailing response where it stops, landing as a
   *  new variant (the unextended one stays a swipe back). The reroll's guards,
   *  plus Keep writing's own disabled flag, re-checked here because a stale
   *  click must do nothing the button could not. Like `rerollResponse`, it
   *  never sends or clears the pending one-shot override. No bare key: it
   *  spends money, so it is only ever a click. */
  async function extendResponse(id: string, guidance: string, route: RerollRoute = NO_REROLL_ROUTE) {
    if (!activeId || busy || rolling || sceneLocked || editing || renamesInFlight || !transcriptIsActive
      || !canGenerateOnRoute(canGenerate, route)) return;
    if (extendDisabled || swipeTarget?.response_id !== id) return;
    const sid = activeId;
    await runStream(sid, (onEvent, signal, attempt, onIndex) =>
      api.extendResponse(cid, sid, id, onEvent, { guidance, ...rerollOverrides(route) }, signal, attempt, onIndex),
      undefined, true, "", true, null, route);
  }

  async function respondAs(ref: string) {
    if (!activeId || busy || rolling || sceneLocked || renamesInFlight || !transcriptIsActive || !canGenerate) return;
    const sid = activeId;
    const landed = await runStream(sid, (onEvent, signal, attempt, onIndex) =>
      api.chat(cid, sid, "", onEvent, pendingResponse ?? undefined, signal, attempt, onIndex, false, ref),
      undefined, false, "", true);
    if (landed) setPendingResponse(null);
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

  // A failed explicit reroute retries the provider it chose, not the broken
  // default. The banner and shortcut read the same route-aware availability.
  const canRetry = canGenerateOnRoute(canGenerate,
    rerollToRetryRef.current?.sid === activeId ? rerollToRetryRef.current.route : NO_REROLL_ROUTE);

  async function retry() {
    if (!activeId || busy || rolling || renamesInFlight || !canRetry) return;
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
    if (!canGenerateOnRoute(canGenerate, route)) return;
    setRerollPrompt(null);
    rerollToRetryRef.current = { sid: activeId, guidance, route };
    const selectedResponse = messages[rerollAt - firstIndex];
    if (selectedResponse?.response_id) {
      rerollToRetryRef.current = null;
      if (selectedResponse.response_can_reroll) await rerollResponse(selectedResponse.response_id, guidance, route);
      else setReplayAt(rerollAt);
      return;
    }
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
    const body = {
      guidance, ...(pendingResponse == null ? {} : { response: pendingResponse }),
      ...rerollOverrides(route)
    };
    const landed = await runStream(activeId, (onEvent, signal, attempt, onIndex) =>
      // `rerolling`: a reroll that fails wants a different recovery offered
      // than a failed send does — see `runStream`.
      api.regenerate(cid, activeId, onEvent, body, signal, attempt, onIndex),
      undefined, true, "", false, null, route);
    if (landed) {
      setPendingResponse(null);
      rerollToRetryRef.current = null;   // it worked; Retry is a plain retry again
    }
  }

  // A scene already in the chronicle comes back as 409 "already_absorbed" rather
  // than silently re-absorbing: lore edits append and plot movements add a beat,
  // so a second pass duplicates both (#235). Confirm, then retry with force.
  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send().catch((err: unknown) => fail(err, false, "scene"));
    }
  }
  function repointReroll(oldId: string, newId: string) {
    const again = rerollToRetryRef.current;
    if (again && again.sid === oldId) rerollToRetryRef.current = { ...again, sid: newId };
  }
  function clearRerollRetry(sid: string) {
    if (rerollToRetryRef.current?.sid === sid) rerollToRetryRef.current = null;
  }
  return {
    setRerollPrompt, setRerollRoute, sendText, repointReroll, clearRerollRetry, reroll, rerollResponse,
    extendResponse, rerollPrompt, rerollRoute, send, canRetry, retry, onKeyDown, respondAs,
  };
}
