import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { api, type RollingSummary, type SceneBreak } from "../../api/client";
import type { InspectorInputs } from "./types";

export function useSceneProgress({ cid, sid, refreshKey, onTaskBusy }:
  InspectorInputs<"cid" | "sid" | "refreshKey" | "onTaskBusy">) {
  // Stamped with the record it was read for, and `undefined` means "in flight,
  // or the read failed" — NOT the same as a scene nobody has summarized
  // (`summary: ""`), since reporting an unreachable store as a scene with
  // nothing to say would be a lie the panel tells on every select.
  //
  // The stamp is `cid/sid`, not `sid`, and review caught the difference: scene
  // ids are campaign-local and collide constantly (every campaign's first scene
  // called Saltmarch is `001--saltmarch`), and `App.tsx` renders CampaignView
  // with no key, so moving between campaigns REUSES this component. A sid-only
  // stamp would show one campaign's prose as fact under another's scene — and,
  // if the new read then failed, keep showing it, because a value that passes
  // the stamp suppresses the "could not be read" message.
  const [rolling, setRolling] =
    useState<{ key: string; data: RollingSummary } | undefined>();
  const [rollingUnread, setRollingUnread] = useState(false);
  // The record whose refold is in flight, not a bare boolean: the button belongs
  // to a scene, and review caught that one scene's pending refresh disabled
  // every other scene's button — reading "Summarizing…" about a result that
  // record can no longer use.
  const [rollingBusy, setRollingBusy] = useState<string | null>(null);
  // Stamped with its record, and kept OUT of the panel's shared `error`. The
  // token guard only retires a rejection that arrives after the reader has
  // moved on; one that lands while the scene is still selected is stored, and
  // nothing on the scene-change path clears `error` — so a provider failure for
  // one scene sat as a banner over the next indefinitely. Its own state, rather
  // than clearing the shared one on every switch, because that banner is
  // written by four other handlers whose behaviour is not this PR's to change.
  const [rollingError, setRollingError] =
    useState<{ key: string; err: unknown } | null>(null);
  // The scene-break detector (#84), stamped with its record for `rolling`'s
  // reason and no lesser one: a proposal is prose ABOUT a story, so showing one
  // campaign's under another's scene reads as fact rather than as lag. Kept
  // separate from `rolling` rather than folded into one "scene state" object,
  // because the two are read and written by different calls and a shared object
  // would make either write clobber the other's half.
  const [breakState, setBreakState] =
    useState<{ key: string; data: SceneBreak } | undefined>();
  // The record whose question is in flight, not a bare boolean — `rollingBusy`'s
  // reason: the button belongs to a scene, and one scene's pending question
  // must not disable another's.
  const [breakBusy, setBreakBusy] = useState<string | null>(null);
  // A read that failed, kept apart from a read that said nothing — `rollingUnread`'s
  // distinction, and this panel needs it more sharply, not less. "Nothing to
  // suggest yet" is an ASSERTION about the scene; rendering it out of a failed
  // GET tells the player the detector looked and found nothing when it never
  // got an answer at all.
  const [breakUnread, setBreakUnread] = useState(false);
  // Reads yield to writes: a GET issued BEFORE a write must not install its
  // pre-write answer afterwards. The effect's read can be in flight when Ask
  // now commits, and resolving second would blank the verdict the player just
  // paid for, with nothing later scheduled to put it back.
  //
  // Its own counter rather than `rolling`'s `writeSeq`, which gates the shared
  // effect's reads for BOTH features: bumping that one for a break write would
  // make the effect discard a perfectly good rolling read as superseded — one
  // feature's write silently costing the other its answer.
  //
  // Deliberately no ticket pair ordering the WRITES against each other, unlike
  // `refreshRolling`'s: `breakBusy` disables both this panel's break buttons
  // for the record a question is out on, so two of its own writes cannot
  // overlap on one scene — and a write arriving from somewhere else entirely
  // (another tab) is refused by `_break_commit`, which is where that decision
  // belongs. Ordering it here as well would be machinery for a race this panel
  // cannot produce.
  const breakSeq = useRef(0);
  const [breakError, setBreakError] =
    useState<{ key: string; err: unknown } | null>(null);
  // The stamp decides what may be RENDERED. This decides what may be STORED,
  // and one without the other is not enough — that took two review rounds:
  //
  // - a superseded read that still installs replaces the current record's data,
  //   after which the stamp rejects the value it just stored and the panel says
  //   "No summary yet" about a scene that has one;
  // - and scene identity cannot order two reads of the SAME scene, which this
  //   panel issues routinely: the effect re-runs on every `refreshKey` bump, so
  //   a pre-refresh read and the post-refresh one are in flight together and the
  //   older can land last.
  //
  // So it is a monotonic token, not an identity: only the most recently issued
  // request may write, whatever it was issued for.
  const readToken = useRef(0);
  // How many manual refolds have INSTALLED. A read carries the value it saw when
  // it was issued and may only install while that still holds, because a refold
  // that landed in between wrote the store: the read is answering from before
  // that write however recently it was issued. One shared token could not say
  // this — it ordered the two by issue time, so a routine reread starting while
  // a refold was out discarded the refold's authoritative answer.
  const writeSeq = useRef(0);
  // Ordering AMONG writes, which `writeSeq` does not give: it counts how many
  // have landed, so it can retire a stale read but cannot tell two refolds
  // apart. Review found the gap that leaves. Two transitions in quick
  // succession each ask; the first fold finishes on the server and releases its
  // claim, so the second is not coalesced and really does refold — and if the
  // first one's RESPONSE is the slower of the two, it lands second and passes
  // the scene-key check, overwriting the newer summary and coverage with its
  // own older ones, with no read scheduled behind it to put things right.
  //
  // A ticket taken when a write is ISSUED, and installed unless a newer
  // ticket has already landed: last installed wins. Deliberately not "highest coverage
  // wins", which would look equivalent and is not — a refold after a trim
  // legitimately covers fewer posts, and the manual button must be able to
  // replace a summary with a shorter one.
  const writeTicket = useRef(0);
  const writeInstalled = useRef(0);
  // The record on screen, readable from a callback created for an earlier one.
  const currentKey = useRef(`${cid}/${sid}`);
  useLayoutEffect(() => { currentKey.current = `${cid}/${sid}`; }, [cid, sid]);
  // Live composition is owned by usePromptHistory, never read with progress.
  useEffect(() => {
    const token = ++readToken.current;
    const seenWrites = writeSeq.current;
    const key = `${cid}/${sid}`;
    const mine = () => readToken.current === token && writeSeq.current === seenWrites;
    const seenBreakWrites = breakSeq.current;
    const breakMine = () => readToken.current === token && breakSeq.current === seenBreakWrites;
    setRollingUnread(false);
    // Cleared up front like `rollingUnread`, not just on the next success:
    // otherwise a scene whose read failed leaves "could not be read" standing
    // over the NEXT scene until its own read lands.
    setBreakUnread(false);
    // The previous record's summary is deliberately NOT cleared here: this
    // effect also re-runs on `refreshKey`, i.e. twice per turn, and blanking
    // would flash "No summary yet" over a summary that is about to come back.
    // The stamp below is what makes that safe.
    api.getRollingSummary(cid, sid)
      .then((data) => {
        if (!mine()) return;
        setRolling({ key, data });
        // A read that arrived is also the answer to an earlier failure. Review
        // caught that keying the banner by record was not enough on its own: a
        // manual refold can fail and a later automatic one succeed, and the
        // banner would go on reporting a failure the panel has since recovered
        // from — over the very summary that proves it did.
        setRollingError((e) => (e?.key === key ? null : e));
      })
      .catch(() => { if (mine()) setRollingUnread(true); });
    // Read beside the summary, on the same select and the same `refreshKey`
    // bump. A failed read leaves whatever was there: unlike the summary there
    // is no "may be behind" to say, because a standing proposal is about a
    // prefix of the transcript and stays true about that prefix — what a failed
    // read costs is a NEW proposal, which the next read or the next turn brings.
    api.getSceneBreak(cid, sid)
      .then((data) => {
        if (!breakMine()) return;
        setBreakState({ key, data });
        setBreakUnread(false);
        setBreakError((e) => (e?.key === key ? null : e));
      })
      .catch(() => { if (breakMine()) setBreakUnread(true); });
    return () => { readToken.current += 1; };
  }, [cid, sid, refreshKey]);

  // A transition line is a post like any other: a location move, a time advance
  // and a cast join/leave all append one (`scenes/moment.py`,
  // `appearances/transitions.py`), so any of them can be the post that crosses
  // the threshold. `onSceneChanged` only re-READS — a GET never evaluates the
  // gate — so review was right that leaving these out let a transition-crossed
  // threshold sit until some later generated turn. The reason given for
  // omitting them last round (that `refreshKey` would cover it) was simply
  // wrong.
  //
  // Not forced, so this is the same cheap question the play loop asks: a scene
  // short of the threshold answers `refreshed: false` having reached no
  // provider. Not awaited, and its rejection swallowed — none of these actions
  // should fail because a summary could not be written.
  //
  // Bounded like the turn, roll and check paths, and for the same reason: the
  // fold must not swallow a player post the transition did not include, because
  // the reply that answers it is an APPEND and would stay out of the "current"
  // summary until another threshold. This panel does not know the transcript's
  // length — the transition endpoints return a result, not a count — so it asks
  // for it, and the read's `total` is the bound.
  //
  // Honest about the residue: a post landing between that read and the POST is
  // still inside the bound. The window shrinks from a whole provider round trip
  // to two adjacent calls, which is worth having; closing it entirely would mean
  // the four transition endpoints reporting their resulting length, a wider
  // change than this one should make.
  //
  // `id` defaults to the scene this panel is showing, and is passed explicitly
  // by the one caller whose write RENAMES the scene out from under the prop:
  // the id in scope there is already stale, so asking with it would 404 while
  // the scene it means sits under a new name with a summary nobody refolded.
  function askRolling(id: string = sid) {
    const key = `${cid}/${id}`;
    const ticket = ++writeTicket.current;
    // The scene-break question rides the same read (#84): a location move and a
    // time advance are two of the three signals it scores, and both land here
    // rather than through `CampaignView`'s play-loop hook. Chained off the same
    // `getRollingSummary` bound rather than reading a second time — one read,
    // one boundary, so the two questions cannot be asked about different
    // transcripts. Rejection swallowed, like the fold's: no transition should
    // fail because a suggestion could not be written.
    const bounded = api.getRollingSummary(cid, id);
    bounded
      .then((seen) => api.askSceneBreak(cid, id, false, seen.total))
      .then((r) => { if (r.asked && currentKey.current === key) reloadBreak(id); })
      .catch(() => {});
    bounded
      .then((seen) => api.refreshRollingSummary(cid, id, false, seen.total))
      .then((data) => {
        if (currentKey.current !== key || !data.refreshed) return;
        if (ticket < writeInstalled.current) return;   // a newer refold already landed
        writeInstalled.current = ticket;
        writeSeq.current += 1;
        setRolling({ key, data });
        // This answer is the server's reconciled view of a fold that just
        // succeeded, so the two ways the panel says "what you are reading may be
        // behind" are both now false and must go with it. Review caught that
        // leaving them meant a provider failure from an earlier manual refresh,
        // or a failed read from the transition before this one, kept its warning
        // up beside prose the POST had just made current -- with nothing later
        // scheduled to retire either message. Scoped to this record, like every
        // other write here: a failure on the scene the reader LEFT is not this
        // one's to clear.
        setRollingUnread(false);
        setRollingError((e) => (e && e.key === key ? null : e));
      })
      .catch(() => {});
  }

  async function refreshRolling() {
    setRollingError(null);
    // Captured OUTSIDE the try, so success, failure and the `finally` all judge
    // themselves against the record this refold was started for.
    const key = `${cid}/${sid}`;
    // Ticketed with the automatic refolds, in the same sequence, so the two
    // orders cannot disagree: an automatic answer issued before this button was
    // pressed may not overwrite what the button installs, however late it lands.
    const ticket = ++writeTicket.current;
    setRollingBusy(key);
    onTaskBusy?.("rolling_summary", true);
    try {
      // `force`, always: this button exists so the player can ask for a summary
      // *now*, including when the automatic refresh is switched off. The
      // server still declines to spend a call when nothing has happened since
      // the last one, and says so in `refreshed`.
      const data = await api.refreshRollingSummary(cid, sid, true);
      // Retired only if the reader has moved on — NOT on the read token. This
      // answer is authoritative: the server wrote the store and reconciled what
      // it returned, so it outranks any read issued before now however recently.
      // `writeSeq` is what tells those reads so.
      if (currentKey.current !== key) return;
      // Authoritative, but not exempt from the ordering: an automatic refold
      // issued AFTER this button was pressed describes a later transcript, and
      // "the server wrote this" is true of both answers. Same rule either way.
      if (ticket < writeInstalled.current) return;
      writeInstalled.current = ticket;
      writeSeq.current += 1;
      setRolling({ key, data });
      setRollingUnread(false);
    } catch (err: unknown) {
      // Guarded like the success path, because `error` is shared by every action
      // in this panel and the scene-change effect does not clear it: a refold
      // that failed for the record the reader LEFT would otherwise sit as a
      // banner over the one they are on until something else cleared it.
      if (currentKey.current !== key) return;
      // Reported, never destructive: the summary already on screen is still the
      // best thing anyone has, so a failed refold leaves it exactly where it is.
      setRollingError({ key, err });
    } finally {
      // Only if it is still ours. The reader can leave and start a refold on
      // another record while this one is out, and clearing unconditionally
      // would free that one's button while its call is still running.
      setRollingBusy((busy) => (busy === key ? null : busy));
      onTaskBusy?.("rolling_summary", false);
    }
  }

  // Re-read after a question the panel did not itself ask. `id` is passed
  // explicitly by `askRolling`'s one caller whose write RENAMES the scene, for
  // the reason stated there: the id in the prop is already stale.
  function reloadBreak(id: string) {
    const key = `${cid}/${id}`;
    const seen = breakSeq.current;
    api.getSceneBreak(cid, id)
      .then((data) => {
        // A read, so it yields to any write that landed after it was issued —
        // the same rule the scene-select effect's read follows.
        if (currentKey.current !== key || breakSeq.current !== seen) return;
        setBreakState({ key, data });
        setBreakUnread(false);
      })
      .catch(() => {
        if (currentKey.current === key && breakSeq.current === seen) setBreakUnread(true);
      });
  }

  // The panel's own button: ask NOW, including when the automatic cadence is
  // switched off. The server still declines to spend a call when nothing has
  // happened since the last question, and says so in `asked`.
  async function askBreakNow() {
    const key = `${cid}/${sid}`;
    setBreakError(null);
    setBreakBusy(key);
    onTaskBusy?.("scene_break", true);
    try {
      const data = await api.askSceneBreak(cid, sid, true);
      // Guarded on the reader still being on this record, like every other
      // post-await write here. Installed whether or not it `asked`: a refusal
      // is still the server's reconciled view of the scene, and dropping it
      // would leave the panel showing a score from before the last turn.
      if (currentKey.current !== key) return;
      breakSeq.current += 1;
      setBreakState({ key, data });
      setBreakUnread(false);
    } catch (err: unknown) {
      // Reported, never destructive: a standing proposal is still the best
      // thing anyone has, so a failed question leaves it exactly where it is.
      if (currentKey.current !== key) return;
      setBreakError({ key, err });
    } finally {
      setBreakBusy((busy) => (busy === key ? null : busy));
      onTaskBusy?.("scene_break", false);
    }
  }

  // "Not here." The watermark moves server-side, so the same posts cannot
  // re-earn the same suggestion on the next turn — which is why this is a
  // request rather than a local `setBreakState(undefined)`.
  async function dismissBreak() {
    const key = `${cid}/${sid}`;
    setBreakError(null);
    setBreakBusy(key);
    try {
      const data = await api.dismissSceneBreak(cid, sid);
      if (currentKey.current !== key) return;
      breakSeq.current += 1;
      setBreakState({ key, data });
      setBreakUnread(false);
    } catch (err: unknown) {
      if (currentKey.current !== key) return;
      setBreakError({ key, err });
    } finally {
      setBreakBusy((busy) => (busy === key ? null : busy));
    }
  }
  return { rolling, breakState, rollingUnread, breakUnread, rollingBusy, breakBusy,
    rollingError, breakError, refreshRolling, askBreakNow, dismissBreak, askRolling };
}
export type SceneProgress = ReturnType<typeof useSceneProgress>;
