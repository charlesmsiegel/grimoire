import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  api, type PromptDiff, type PromptEntry, type PromptSnapshot, type SceneContext,
} from "../../api/client";
import { errorText } from "../../api/errors";
import { getModels, type Model } from "../../api/models";
import { isDecision } from "../turnLabels";

/** The `against` value naming the composition as it stands now rather than a
 *  captured turn. Matches `routes.scenes.LIVE_SIDE`. */
export const LIVE_SIDE = "live";

export function usePromptHistory({ cid, sid, refreshKey, focusTick, contextCollapsed, onError: setError }:
  { cid: string; sid: string; refreshKey: number; focusTick: number;
    contextCollapsed: boolean; onError: (error: string | null) => void }) {
  // The live composition, stamped with the campaign, scene and refresh it was
  // composed for: the scene half so a switch never paints one scene's prompt
  // under another's heading, the refresh half so a shut section can tell a
  // figure that is still current from one a turn has since moved (see the
  // effect that reads it).
  const [ctx, setCtx] =
    useState<{ cid: string; sid: string; key: string; data: SceneContext } | null>(null);
  // Held with the campaign and scene it came from, exactly like `brief` above
  // and for the same reason: the inspector stays mounted across both switches,
  // so a bare array keeps the previous scene's turns on screen until the new
  // request settles. That is worse here than a stale label — entry ids are
  // per-campaign counters, so they collide across campaigns, and clicking a
  // stale row would fetch a DIFFERENT campaign's prompt under it. Comparing
  // during render makes the window impossible rather than short.
  const [turns, setTurns] = useState<{ cid: string; sid: string; rows: PromptEntry[] } | null>(null);
  // Scoped like `turns` and `brief`, and for a reason the clearing effect below
  // cannot cover on its own: effects run AFTER render, so on a scene or campaign
  // switch the first paint still has the old snapshot while `shown` already
  // prefers it — painting one scene's whole prompt under another's heading for a
  // frame. Comparing during render makes that impossible rather than brief.
  const [frozen, setFrozen] = useState<
    { cid: string; sid: string; data: PromptSnapshot } | null>(null);
  // What the selected turn is being compared against (#130): "" for nothing,
  // "live" for the composition as it stands now, or another entry id. Held as
  // the CHOICE rather than as the answer, so the fetch below can re-run when
  // the live side moves under it.
  const [compare, setCompare] = useState("");
  // Scoped by both ends as well as by scene, for the reason `frozen` is scoped
  // at all AND one more: the reader can switch which turn they are looking at
  // without clearing the comparison, and a diff still in flight for the turn
  // they left would otherwise be painted under the one they arrived at.
  const [diff, setDiff] = useState<
    { cid: string; sid: string; eid: string; against: string; key: string;
      data: PromptDiff } | null>(null);
  const [models, setModels] = useState<Model[]>([]);
  // Its own effect, with the same `live` cleanup and for a second reason on top
  // of the briefing's. `fresh: true` stops the post-generation read from JOINING
  // a pre-generation request, but it does not order two independent ones: the
  // initial load and the refresh a completed turn triggers are both in flight,
  // and if the newer answers first the older `.then` overwrites it with rows
  // that predate the turn — leaving Turn history one turn behind until the next
  // refresh happens to arrive in order. The cleanup makes the superseded
  // response a no-op instead.
  useEffect(() => {
    let live = true;
    api.listScenePrompts(cid, sid)
      .then((r) => { if (live) setTurns({ cid, sid, rows: r.entries }); })
      .catch(() => { if (live) setTurns({ cid, sid, rows: [] }); });
    return () => { live = false; };
  }, [cid, sid, refreshKey]);
  // Which scene is on screen *now*, readable from a fetch that started under a
  // previous one — the same "a late answer is wrong, not just stale" problem
  // the briefing effect above solves with its `live` flag. A ref rather than
  // that pattern because `showTurn` is triggered by a click rather than by an
  // effect, so there is no cleanup to close over; and a ref rather than the
  // props, because `showTurn` closes over the ones it was created with —
  // comparing those to themselves would be a guard that is always satisfied.
  //
  // Campaign AND scene. Scene numbering is campaign-local, so two campaigns
  // routinely have a live scene under the same id, and a scene-only check would
  // wave the first campaign's prompt through into the second's inspector.
  const liveScene = useRef(`${cid}/${sid}`);
  // The entry the reader most recently asked for; a response for anything else
  // is superseded and dropped. See `showTurn`. `null` means "live" — going back
  // is a newer selection too, so a detail fetch still in flight when the reader
  // returns to the live panel must not reopen the past-turn view over them.
  const wantedTurn = useRef<string | null>(null);
  // Keyed on the scene alone, deliberately NOT on refreshKey: a turn landing
  // must not yank the reader out of a past turn they are in the middle of
  // reading. Changing scene must, since the entry belongs to the old one.
  //
  // A LAYOUT effect for the same reason as `CastPanel`'s `live`: passive
  // effects are scheduled in a separate task, leaving a gap after the commit
  // in which a detail response for the scene just left can still read a ref
  // that agrees with it. Here the render-time scoping of `frozen` makes such a
  // write inert rather than harmful, so this is closing the window rather than
  // fixing a live defect — but two refs guarding the same race should not
  // disagree about when they update.
  useLayoutEffect(() => {
    liveScene.current = `${cid}/${sid}`;
    wantedTurn.current = null;
    setFrozen(null);
    setCompare("");
  }, [cid, sid]);

  const showTurn = useCallback(async (eid: string) => {
    setError(null);
    // The scene guard alone is not enough WITHIN a scene: click turn A then
    // turn B, and if A resolves second it overwrites B — the panel showing the
    // turn the reader did not pick last. So the newest request wins, tracked by
    // the entry it asked for.
    wantedTurn.current = eid;
    // Comparing against the LIVE preview survives moving to another turn: "at
    // which turn did this section change?" is walked by clicking down the rail,
    // and clearing the comparison at every step would make the reader re-pick
    // it each time. Comparing against a specific turn does not, because the
    // turn just clicked can BE that one, and a diff of an entry against itself
    // is a panel that has quietly stopped answering.
    setCompare((c) => (c === LIVE_SIDE ? c : ""));
    const current = () => liveScene.current === `${cid}/${sid}` && wantedTurn.current === eid;
    try {
      const snapshot = await api.getScenePrompt(cid, sid, eid);
      // Switching scenes mid-flight would otherwise land the old scene's prompt
      // in the new scene's panel — the exact confusion the backend refuses to
      // serve (the detail route scopes each entry to its scene), so the client
      // must not reintroduce it from the other end.
      if (current()) setFrozen({ cid, sid, data: snapshot });
    } catch (err: unknown) {
      if (!current()) return;
      // The likeliest failure is the retention window having evicted it while
      // the list was on screen, which is a 404 and not worth a scary banner.
      setFrozen(null);
      // ...and the comparison ends with it. Clearing `frozen` returns the panel
      // to live context, which is the same place "← Back to live context"
      // lands — and that clears `compare`. Leaving it set here made the two
      // exits behave differently: the choice would be invisible (no turn
      // selected, so no picker) and then the NEXT turn clicked would open
      // straight into a comparison the reader could not see themselves asking
      // for. Sticky across turns, not across leaving.
      setCompare("");
      setError(typeof err === "object" && err !== null && "status" in err && err.status === 404
        ? "That turn's prompt has aged out of the log."
        : errorText(err));
    }
  }, [cid, sid, setError]);
  // The snapshot only counts while it belongs to what is on screen.
  const seen = useMemo(
    () => (frozen && frozen.cid === cid && frozen.sid === sid ? frozen.data : null),
    [frozen, cid, sid]);
  // The live composition (GET /context) is the most expensive read this panel
  // makes: the server assembles the whole next prompt to answer it. It used to
  // ride the load effect above, and so was made on every select and every
  // `refreshKey` bump -- after every turn -- whether or not anything showed it.
  // It is asked for only while its answer is on screen: the Context section
  // open, and no past turn selected in its place (`shown` prefers the frozen
  // snapshot, and a comparison replaces the breakdown and asks the server for
  // the live side itself). An answer already on hand for this scene and this
  // refresh is not asked for again when the section reopens.
  const liveShown = !contextCollapsed && !seen;
  const ctxReq = useRef({ token: 0, key: "" });
  const compositionKey = `${refreshKey}/${focusTick}`;
  const ctxKey = `${cid}/${sid}/${compositionKey}`;
  const ctxHeld = ctx !== null && `${ctx.cid}/${ctx.sid}/${ctx.key}` === ctxKey;
  useEffect(() => {
    if (!liveShown || ctxHeld || ctxReq.current.key === ctxKey) return;
    // A token as well as the key: the newest request is the only one that may
    // write, whichever order the answers come back in.
    const req = { token: ctxReq.current.token + 1, key: ctxKey };
    ctxReq.current = req;
    // This key denotes a NEW composition, not another reader of the old one.
    // PUT invalidates cached values, but an in-flight GET may still describe
    // the pre-save fields. Keep transport freshness aligned with the key.
    api.getSceneContext(cid, sid, { fresh: true })
      .then((data) => { if (ctxReq.current === req) setCtx({ cid, sid, key: compositionKey, data }); })
      .catch(() => { if (ctxReq.current === req) setCtx(null); })
      // Settled, so a later reopen for the same key may ask again if this one
      // failed; a success is `ctxHeld` from here on.
      .finally(() => { if (ctxReq.current === req) ctxReq.current = { ...req, key: "" }; });
  }, [cid, sid, compositionKey, ctxKey, liveShown, ctxHeld]);
  const liveCtx = ctx && ctx.cid === cid && ctx.sid === sid ? ctx.data : null;
  // A selected past turn wins over the live composition — that IS the feature.
  // Both are the same shape, so everything below reads one variable.
  const shown = useMemo(() => seen ?? liveCtx, [seen, liveCtx]);
  // Empty until the rows on hand are this scene's, so a switch shows "no
  // captured turns yet" for a moment rather than the previous scene's list.
  const shownTurns = useMemo(
    () => (turns && turns.cid === cid && turns.sid === sid ? turns.rows : []),
    [turns, cid, sid]);
  // The log holds both kinds (roadmap 01b): turns, which Turn history lists,
  // and decisions -- a speaker pick, a scene-break or voice check -- which are
  // listed apart, so a reader inspecting turns is not wading through checks.
  const turnRows = useMemo(() => shownTurns.filter((t) => !isDecision(t)), [shownTurns]);
  const decisionRows = useMemo(() => shownTurns.filter((t) => isDecision(t)), [shownTurns]);
  // The comparison itself (#130). An effect rather than a fetch in the picker's
  // `onChange`, because only ONE end of it is frozen: against the live preview
  // the answer moves with the store, and `refreshKey` is bumped by the very
  // turn that moved it. A diff of a preview that has since changed describes a
  // prompt nobody would send.
  const compareWith = seen?.id;
  // A decision is never compared with the live preview (the server answers
  // 409 `not_comparable`): the live comparison that survives moving down the
  // rail ends when the reader moves onto a decision.
  const seenDecides = seen ? isDecision(seen) : false;
  // Which turn landing this comparison actually depends on. A turn-against-turn
  // diff is frozen at BOTH ends, so a completed turn cannot change it and
  // re-reading it on every `refreshKey` would be a request that can only return
  // what is already on screen. Against the live preview it is the whole point.
  const liveKey = compare === LIVE_SIDE ? compositionKey : "";
  useEffect(() => {
    if (!compareWith || !compare) { setDiff(null); return; }
    if (seenDecides && compare === LIVE_SIDE) { setDiff(null); setCompare(""); return; }
    let alive = true;
    // Ordered oldest-first before it is asked for. The route takes the two ends
    // as given and does not reorder them — that is deliberate there, so a
    // caller who means "this one as the before" gets it — but a READER picking
    // an earlier turn out of a newest-first list means "what changed since
    // then", and answering with insertions that are things the older prompt had
    // is a diff running backwards through time. Ids are a monotonic counter and
    // the live preview is newer than any of them.
    const [older, newer] = compare === LIVE_SIDE || Number(compare) > Number(compareWith)
      ? [compareWith, compare] : [compare, compareWith];
    api.getScenePromptDiff(cid, sid, older, newer)
      .then((d) => {
        if (alive)
          setDiff({ cid, sid, eid: compareWith, against: compare, key: liveKey, data: d });
      })
      .catch((err: { status?: number; detail?: string }) => {
        if (!alive) return;
        // Either end can have been evicted by the retention window while the
        // picker was open, which is a 404 and routine rather than alarming.
        setDiff(null);
        setCompare("");
        // No `String(err)` fallback, unlike its neighbours above: those read a
        // rejection typed `any`, and stringifying THIS one would print
        // "[object Object]" at the reader rather than a reason.
        setError(err.status === 404
          ? "One of those turns has aged out of the log."
          : (err.detail ?? "Those turns could not be compared."));
      });
    return () => { alive = false; };
  }, [cid, sid, compareWith, compare, liveKey, seenDecides, setError]);
  // Held to both ends as well as to the scene: the reader can move to another
  // turn while a comparison is in flight, and an answer for the one they left
  // would otherwise be painted under the one they arrived at.
  const shownDiff = useMemo(
    () => (diff && diff.cid === cid && diff.sid === sid
           && diff.eid === compareWith && diff.against === compare ? diff.data : null),
    [diff, cid, sid, compareWith, compare]);
  // Deliberately NOT part of the guard above: a turn landing must not blank the
  // panel, because what it would fall back to is this turn's BREAKDOWN — a flip
  // to a different kind of content, and to one that is refetching too. So the
  // stale comparison stays on screen and says it is stale, which also covers
  // the case a blanking guard could not: a request that never answers.
  const recomputing = !!shownDiff && diff !== null && diff.key !== liveKey;
  useEffect(() => {
    let live = true;
    getModels().then((rows) => { if (live) setModels(rows); })
      .catch(() => { if (live) setModels([]); });
    return () => { live = false; };
  }, [cid]);
  function setComparison(value: string) { setError(null); setCompare(value); }
  function backToLive() {
    wantedTurn.current = null; setFrozen(null); setCompare("");
  }
  return { seen, shown, models, shownTurns, turnRows, decisionRows, shownDiff, recomputing,
    compare, frozen: !!seen, liveShown, ctxHeld, showTurn, setComparison, backToLive };
}
export type PromptHistory = ReturnType<typeof usePromptHistory>;
