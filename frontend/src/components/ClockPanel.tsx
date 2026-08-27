import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { agingLabel } from "../aging";
import { api, type AdvanceDigest, type CalendarConfig, type CampaignClock,
         type ClockLogEntry } from "../api/client";
import { CalendarDatePicker } from "./CalendarDatePicker";
import { forkNotes } from "./forkNotes";

/** What a shown digest *is*, so one block can serve all three states without
 *  the reader having to guess which they are looking at: a preview, a move that
 *  landed, or a confirmed advance the server treated as a no-op because the
 *  clock was already at that moment (`moved: false`). Calling that last one
 *  "Advanced" would claim a write that did not happen. */
type Outcome = "preview" | "moved" | "unchanged";

/** `n` days, pluralized. Shared by the digest and the checkpoint question so a
 *  threshold of 0 — which asks about a one-day move — cannot say "1 days". */
function dayCount(n: number): string {
  return `${n} day${n === 1 ? "" : "s"}`;
}

/** The server's own sentence for a refusal, or the error itself.
 *
 *  One helper rather than an `any`-typed catch per action: `request` rejects
 *  with the body FastAPI sent, whose `detail` is the sentence written for the
 *  reader ("an advance needs a reason", "name is required"), and anything else
 *  reaching here is a transport failure with no sentence of its own.
 */
function refusal(err: unknown): string {
  const detail = err !== null && typeof err === "object"
    ? (err as { detail?: unknown }).detail
    : undefined;
  return typeof detail === "string" ? detail : String(err);
}

/** Whether a refusal is the server saying the campaign moved under a priced
 *  move (#409).
 *
 *  Read off `kind` rather than the sentence, for the reason every other refusal
 *  in this app carries one: the sentence is written for a reader and may be
 *  reworded, and this decides whether the panel throws away numbers that have
 *  stopped being true. */
function campaignMoved(err: unknown): boolean {
  return err !== null && typeof err === "object"
    && (err as { kind?: unknown }).kind === "campaign_moved";
}

/** The campaign clock (#100): where the story's present is, and the one control
 *  that moves it deliberately — by a duration or to a date, always with a reason.
 *
 *  Preview before confirm, and the digest stays on screen afterwards. The two
 *  come from the same deterministic computation on the server, so what the
 *  reader approved is what they get; the panel never recomputes anything itself.
 *
 *  Campaign-scoped state in a scene-scoped inspector, deliberately: "when is it"
 *  is the question the neighbouring When section answers for one scene, and this
 *  is that question for the campaign. Advancing writes no transcript — the only
 *  line a time change puts in a scene still comes from setting that scene's own
 *  date.
 *
 *  A large skip is asked about before it happens (#107): over the configured
 *  threshold, confirming opens the checkpoint question instead of moving the
 *  clock, offering to fork the campaign first. That is why confirming always
 *  holds a *fresh* preview — the panel's stated design was preview-before-
 *  confirm, but nothing enforced it, and in "skip to a date" mode the client
 *  cannot know how far the skip goes without asking. So `confirm` prices the
 *  move it is about to make, whether or not Preview was pressed. Composition
 *  rather than a new endpoint: the fork and the advance are two primitives
 *  that already exist, and neither knows about the other.
 */
export function ClockPanel({ cid, refreshKey, onAdvanced }: {
  cid: string;
  /** Bumped by the inspector when a scene's own date may have moved the clock. */
  refreshKey?: number;
  onAdvanced?: () => void;
}) {
  const [clock, setClock] = useState<CampaignClock | null>(null);
  // Only to answer "has a calendar been chosen yet"; the picker itself belongs to
  // the When section, which is where a reader is sent to use it.
  const [cfg, setCfg] = useState<CalendarConfig | null>(null);
  const [mode, setMode] = useState<"days" | "date">("days");
  const [days, setDays] = useState("1");
  const [target, setTarget] = useState("");
  const [reason, setReason] = useState("");
  const [digest, setDigest] = useState<AdvanceDigest | null>(null);
  const [outcome, setOutcome] = useState<Outcome>("preview");
  // The open checkpoint question, holding the digest it was asked about — so
  // the sentence and the threshold it names come from the same computation that
  // decided to ask, rather than from whatever is on screen by the time it is
  // answered. Null is "not asking".
  const [gate, setGate] = useState<AdvanceDigest | null>(null);
  // The clock reading the shown digest was priced against, so the panel can
  // notice the campaign's present moving underneath it — a scene dated forward
  // carries the clock with it (`clock.observe`), and the inspector's When
  // section is one section up from this one.
  //
  // Compared against `clock.now` and taken from the same place, rather than
  // read off `digest.from`: the digest's own anchor comes back CANONICALIZED by
  // the provider (`clock._current`) while the stored moment may be an
  // un-canonicalized seed off the chronicle, so the two spellings of one date
  // would read as a move and blank every preview the moment it arrived.
  const [pricedNow, setPricedNow] = useState<string | null>(null);
  // The campaign's write token as the shown digest was priced against (#409),
  // kept in step with `pricedNow` at every site because the two describe one
  // pricing. `pricedNow` notices the campaign's PRESENT moving, which is what
  // makes a shown span stop being true; this notices the campaign being written
  // at all, which is what makes confirming it a different move than the one on
  // screen. Neither can do the other's job: a scene edit leaves the clock
  // reading exactly the same, and the token is not a moment anything can be
  // measured from.
  const [pricedRevision, setPricedRevision] = useState("");
  const [forkName, setForkName] = useState("");
  // A checkpoint already taken for the OPEN question. Kept apart from `saved`,
  // which is only the sentence: a fork that lands and a skip that then fails
  // leaves the question open, and without this a retry would take a second full
  // copy of the campaign — silently, and `copytree`-expensively.
  const [checkpointed, setCheckpointed] = useState(false);
  const [saved, setSaved] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // The campaign this panel is showing RIGHT NOW, readable from inside a call
  // that started before the reader navigated. `cid` inside an async function is
  // the one captured when it was called, which is what the request needed and
  // exactly what cannot answer "is this still the campaign on screen?".
  //
  // Without it a pricing reply for campaign A, landing after a move to B,
  // installs A's digest and opens A's checkpoint question — and the buttons
  // under it belong to the current render, so answering would fork and skip B
  // on the strength of a span measured in A. The reset effect below does not
  // help: it runs when `cid` changes, which is BEFORE the late reply arrives.
  const showing = useRef(cid);
  //: Whether this panel is still showing the campaign a call was made FOR.
  //: Every state write that happens after an `await` is guarded by it, and the
  //: rule is one sentence: the request belongs to the campaign it named, the
  //: panel belongs to whoever is on screen now, and only the first of those is
  //: fixed when the call was made.
  //: Stable, so the reads below can depend on it without re-subscribing.
  const stillShowing = useCallback((forCid: string) => showing.current === forCid, []);
  // `useLayoutEffect` rather than a write during render, and declared above
  // every effect that starts a request: layout effects run first, so the ref is
  // already current by the time anything below can fire. Same shape and same
  // reason as `SuggestedCast`'s `live` ref.
  useLayoutEffect(() => { showing.current = cid; }, [cid]);

  // Every read here is guarded the same way, not just the pricing calls. A
  // reply that lands after the reader has moved on belongs to a campaign this
  // panel is no longer showing, and the clock is the worst one to get wrong: it
  // is the moment every span is measured FROM, so B displaying A's present
  // would misprice the next skip and mislabel the header line while doing it.
  const reload = useCallback(
    () => api.getCampaignClock(cid)
      .then((c) => { if (stillShowing(cid)) setClock(c); })
      .catch(() => { if (stillShowing(cid)) setClock(null); }),
    [cid, stillShowing]);
  useEffect(() => { reload(); }, [reload, refreshKey]);
  useEffect(() => {
    api.getCalendarConfig({ kind: "campaign", id: cid })
      .then((c) => { if (stillShowing(cid)) setCfg(c); })
      .catch(() => { if (stillShowing(cid)) setCfg(null); });
  }, [cid, stillShowing]);

  // A digest belongs to the request that produced it. Changing the target (or
  // the campaign) invalidates it, and showing a stale one next to new inputs is
  // how a reader confirms a skip they never previewed.
  // The open checkpoint question goes with it, and for a sharper version of the
  // same reason: answering "checkpoint, then advance" against a span the prompt
  // was never about would fork for one skip and take another.
  useEffect(() => {
    setDigest(null); setOutcome("preview"); setPricedNow(null); setPricedRevision("");
    setGate(null); setCheckpointed(false); setSaved(""); setError(null);
  }, [cid, mode, days, target]);

  // ...and the same rule for the other way a shown span goes stale: the inputs
  // held still and the campaign's present moved. A preview describes a span
  // FROM somewhere, so a clock that has moved since leaves it describing a
  // skip nobody is being offered — and an open checkpoint question asked about
  // one.
  //
  // Keyed on the clock's own reading rather than on `refreshKey`, which the
  // inspector bumps twice per turn: taking the question back over turns that
  // never touched the clock would blank it under a reader mid-decision. The
  // decision itself no longer rests on this — `confirm` re-prices every time —
  // so this is about not showing numbers that have stopped being true.
  useEffect(() => {
    if (!clock || outcome !== "preview" || pricedNow === null) return;
    if (pricedNow === clock.now) return;
    setDigest(null); setGate(null); setCheckpointed(false);
    setPricedNow(null); setPricedRevision("");
  }, [clock, outcome, pricedNow]);

  // A taken checkpoint stops being one the moment the campaign moves on. The
  // marker says "a copy of this campaign AS IT STANDS exists", which is what
  // lets a retry skip the fork — and a turn played, or any scene edit, between
  // the copy and the retry makes that false: the reader would be handed a
  // restore point missing everything since.
  //
  // `refreshKey` IS the right signal here, where it was the wrong one for the
  // question above, because the two fail in opposite directions. Firing too
  // often there blanks a question under a reader mid-decision; firing too often
  // here costs one extra copy of a campaign that may not have needed it, which
  // is the side to be wrong on.
  //
  //: The counter exists because `refreshKey` cannot answer this question from
  //: inside an `await`: it is a prop, so a handler reads whatever it captured
  //: when it was called. Clearing the marker in the effect is not enough on its
  //: own — a fork still in flight writes the marker back afterwards and undoes
  //: the invalidation, which is the case this counter closes.
  const refreshGen = useRef(0);
  useEffect(() => { refreshGen.current += 1; setCheckpointed(false); }, [refreshKey]);


  /** Show a refusal, and throw away a pricing the server has just said is stale.
   *
   *  `campaign_moved` (#409) is the one refusal that invalidates what is on
   *  screen rather than merely explaining why nothing happened: the campaign was
   *  written between the pricing and the confirm, so the digest, the gate's
   *  question and the token behind them all describe a state the campaign has
   *  left. Clearing them puts the reader back at Preview, which is where the
   *  server's own sentence sends them. The clock is re-read for the same reason
   *  — a write that moved the token may have moved the present too. */
  function onRefusal(err: unknown) {
    setError(refusal(err));
    if (!campaignMoved(err)) return;
    setDigest(null); setGate(null); setCheckpointed(false);
    setPricedNow(null); setPricedRevision("");
    void reload();
  }

  const request = () => (mode === "days" ? { days: parseInt(days, 10) } : { to: target });
  const ready = mode === "days" ? !isNaN(parseInt(days, 10)) : !!target;

  async function preview() {
    setError(null);
    setBusy(true);
    try {
      const r = await api.previewAdvance(cid, request());
      if (!stillShowing(cid)) return;         // the reader moved on; this is A's answer
      setDigest(r.digest);
      setPricedNow(clock?.now ?? "");
      setPricedRevision(r.revision);
      setOutcome("preview");
    } catch (err: unknown) {
      if (stillShowing(cid)) setError(refusal(err));
    } finally {
      // Never guarded: `busy` is the panel's, not the campaign's, and a guard
      // here would leave the controls disabled for whoever is on screen.
      setBusy(false);
    }
  }

  /** The write itself. Shared by the ungated path and both answers to the gate,
   *  so there is exactly one place the clock moves.
   *
   *  `expect` is the write token this move was priced against (#409), and it is
   *  a PARAMETER rather than a read of `pricedRevision` because `confirm` prices
   *  and advances inside one handler: a state write made two lines up is not
   *  visible to the closure that made it, so reading it here would send the
   *  PREVIOUS pricing's token — refusing every confirm that followed a change,
   *  which is exactly the case the token exists to let through once re-priced.
   *  The gate's two answers pass the state, which by the time they run is the
   *  pricing the question was asked about.
   */
  async function runAdvance(expect: string) {
    const r = await api.advanceTime(cid, { ...request(), reason, expect_revision: expect });
    // The clock moved in the campaign this call named, which is right. What
    // must not follow it is this panel adopting the result: it may be showing
    // somebody else by now, and "Advanced" over another campaign's digest is
    // the mildest of the things that go wrong.
    if (!stillShowing(cid)) return;
    setDigest(r.digest);
    // The digest on screen is now a RESULT, not a preview, so nothing is
    // priced. Kept in step with `digest` at every site rather than left to the
    // `outcome` check in the effect below: two fields that must agree, kept in
    // agreement by a third, is the shape every bug in this panel has had.
    setPricedNow(null);
    setPricedRevision("");
    setOutcome(r.moved ? "moved" : "unchanged");
    setGate(null);
    setCheckpointed(false);
    // Clearing the reason is load-bearing, not just tidy: the duration is left
    // as typed (a reader who skips a week often skips another), so without this
    // the Advance button would stay live and a second click would skip a second
    // week. An empty reason disables it until the next advance is described.
    setReason("");
    await reload();
    onAdvanced?.();
  }

  /** Price the move, then either ask about a checkpoint or make it.
   *
   *  The move is priced afresh EVERY time, and a digest already on screen is
   *  never reused for the verdict. Reuse looked free — the reader had just
   *  pressed Preview, on inputs that have not changed since — and it is not: a
   *  preview describes a span from a particular moment, and the campaign's
   *  present can move underneath it without the inputs moving at all. Setting a
   *  scene's date carries the clock forward with it, and that control is one
   *  section up from this one in the same inspector.
   *
   *  Skipping TO a date is where that bites. Priced at eight days from December
   *  and confirmed after a scene dated June moved the clock, the same request is
   *  a five-month correction backwards — over any threshold, and the question
   *  would never have been asked. The stale digest is the one on screen; the
   *  clock the advance lands against is the live one, so only re-pricing can
   *  make the two agree.
   *
   *  The cost is one extra read-only call on the confirm path, against a server
   *  on this machine, for a button a person presses. That is a poor trade to
   *  refuse in exchange for a verdict that can be about a different skip.
   */
  async function confirm() {
    setError(null);
    // A new confirm is a new operation, so the previous one's checkpoint line
    // stops being the answer to what is on screen — unless this IS that
    // operation, resumed. `checkpointed` outlives a dismissal precisely so a
    // reader who cancelled after a copy landed and then asked again does not
    // get a second copy of the same campaign under the same name.
    if (!checkpointed) setSaved("");
    setBusy(true);
    try {
      const { digest: priced, revision } = await api.previewAdvance(cid, request());
      // Never install a verdict for a campaign that is no longer on screen: the
      // question would be answered with the current campaign's id.
      if (!stillShowing(cid)) return;
      setDigest(priced);
      setPricedNow(clock?.now ?? "");
      setPricedRevision(revision);
      setOutcome("preview");
      if (priced.fork) {
        setForkName(`Before ${priced.to_friendly || priced.to}`);
        setGate(priced);            // the question is asked; nothing is written
        return;
      }
      await runAdvance(revision);
    } catch (err: unknown) {
      if (stillShowing(cid)) onRefusal(err);
    } finally {
      // Never guarded: `busy` is the panel's, not the campaign's, and a guard
      // here would leave the controls disabled for whoever is on screen.
      setBusy(false);
    }
  }

  /** Copy the campaign as it stands, then skip in the original.
   *
   *  Strictly in that order, and the skip is abandoned if the copy fails — or
   *  if the campaign moved while the copy was being taken, which is the same
   *  failure in a weaker form. The reader asked to be able to come back to this
   *  moment; moving past it on a copy that may not hold it would give them the
   *  one thing they said they did not want.
   */
  async function checkpointThenAdvance() {
    setError(null);
    setBusy(true);
    try {
      if (!checkpointed) {
        const name = forkName.trim();
        const took = refreshGen.current;
        // The key is derived from the operation rather than minted at random,
        // and that is what makes it survive the thing the marker below cannot:
        // a reload between the copy and the skip. Both halves come back from the
        // server, so the same reader asking the same question about the same
        // campaign rebuilds the same key from a fresh page and is answered with
        // the fork they already have. The token is in it for the other
        // direction: a campaign that has been written since is a DIFFERENT
        // operation, so it gets a different key and a fresh copy — which is the
        // rule `checkpointed` is cleared by, spelled somewhere durable.
        const key = `checkpoint:${gate?.to ?? ""}:${pricedRevision}`;
        const report = await api.forkCampaign(cid, name, undefined, key);
        // The sharpest case for the rule above. A copy of A recorded as B's
        // means a large skip in B offers "Retry the skip", takes no copy at
        // all, and advances anyway — the feature failing silently in exactly
        // the direction it exists to prevent.
        if (!stillShowing(cid)) return;
        // A copy is of whatever the campaign was when `copytree` ran. A turn
        // landing while it ran is on one side of that or the other — the lock
        // decides, and nothing here can see which way it went. So a copy taken
        // across a change counts as a copy, not as a checkpoint: the reader is
        // told it exists and told what it might be missing, and the marker
        // stays clear so a retry takes a fresh one rather than reusing this.
        // Assuming the worse of the two costs one `copytree` on a path that is
        // already a failure; assuming the better one hands back a restore point
        // quietly missing a turn.
        const current = refreshGen.current === took;
        if (current) setCheckpointed(true);
        // A fork from where the campaign stands cuts nothing, so `forkNotes` is
        // almost always "". Shown when it is not, on the same footing the shelf
        // and the campaign page show it — a checkpoint that quietly came up
        // short is worse than one that says so.
        const notes = forkNotes(report);
        setSaved(`Checkpoint saved: “${name}” is on the campaigns shelf.`
                 + (current ? "" : " The campaign changed while it was being copied,"
                                 + " so it may not hold the latest turn and the skip"
                                 + " was not taken — preview the skip again to"
                                 + " checkpoint where the campaign stands now.")
                 + (notes ? ` ${notes}` : ""));
        // ...and the clock stays where it is. This panel's own rule, three
        // lines up in the docstring, is that a copy which fails abandons the
        // skip, because the reader asked to be able to come back to this moment
        // and moving past it anyway hands them the one thing they said they did
        // not want. A copy that may not HOLD that moment is the same failure in
        // a weaker form, and letting it through was an inconsistency rather than
        // a decision.
        //
        // The PRICING goes with it, and that is the part worth spelling out: the
        // campaign moved, so the token this operation was priced against is one
        // the server will now refuse. Leaving the question open would let the
        // reader answer it again, spend a second whole `copytree` under a key
        // that has not changed either, and only then learn from `/advance` that
        // the skip was stale all along — two copies to land one checkpoint.
        // Cleared, the next round re-prices, which is what mints both a current
        // token for the skip and a different key for the copy. It also means
        // the key needs no disown counter of its own: a campaign that really
        // changed produces a new token, and one that did not produces the same
        // key and is answered with the copy already taken, which is the right
        // answer there too.
        if (!current) {
          setDigest(null); setGate(null); setPricedNow(null); setPricedRevision("");
          return;
        }
      }
      await runAdvance(pricedRevision);
    } catch (err: unknown) {
      // The question stays open: the reader can retry, or skip without one.
      if (stillShowing(cid)) onRefusal(err);
    } finally {
      setBusy(false);
    }
  }

  /** Take the skip the gate was asked about, with no copy left behind. */
  async function skipWithoutCheckpoint() {
    setError(null);
    setBusy(true);
    try {
      await runAdvance(pricedRevision);
    } catch (err: unknown) {
      if (stillShowing(cid)) onRefusal(err);
    } finally {
      // Never guarded: `busy` is the panel's, not the campaign's, and a guard
      // here would leave the controls disabled for whoever is on screen.
      setBusy(false);
    }
  }

  // An unconfirmed calendar means the reader has not chosen how this campaign
  // reckons dates yet. The same nudge the When section gives, and for the same
  // reason: a moment recorded in the default calendar's notation becomes
  // unreadable the moment they pick a different one, and the clock is the worst
  // place to leave one -- the whole campaign inherits it. Deliberately not the
  // stricter rule of refusing the *request*: `PUT .../datetime` allows it too,
  // so this stays a nudge in the UI rather than a policy only one route knows.
  if (cfg && !cfg.confirmed) {
    return (
      <div className="clock-panel">
        <div className="field-hint">
          Select a calendar in the When section to track campaign time.
        </div>
      </div>
    );
  }

  return (
    <div className="clock-panel">
      {clock?.now
        ? <div className="field-hint">Now: {clock.friendly || clock.now}</div>
        : <div className="field-hint">No campaign date yet</div>}

      {/* Frozen while a call is in flight. A checkpoint is a `copytree` of a
          whole campaign and takes real time, and the skip that follows it is
          the one the question was asked about: editing the duration underneath
          it would leave the panel showing one span and the clock taking
          another. Changing the duration otherwise takes the question back. */}
      <div className="picker">
        <select aria-label="Advance by" value={mode} disabled={busy}
                onChange={(e) => setMode(e.target.value as "days" | "date")}>
          <option value="days">Advance by days</option>
          <option value="date">Skip to a date</option>
        </select>
        {mode === "days"
          ? <input type="number" aria-label="Days" value={days} disabled={busy}
                   onChange={(e) => setDays(e.target.value)} />
          : <CalendarDatePicker scope={{ kind: "campaign", id: cid }} value={target}
                                onChange={setTarget} ariaLabel="Skip to" disabled={busy} />}
      </div>

      {/* Frozen while the question is open, as well as while a call is in
          flight. The endpoint requires a reason and the gate's actions do not
          re-check one, so a reason cleared here and then answered with
          "Checkpoint, then advance" forks the campaign and *then* earns a 400 —
          leaving a full copy on the shelf for a skip that never happened. The
          reason is part of what was confirmed; Cancel is how it is changed. */}
      <input aria-label="Reason" placeholder="Why time passes…" value={reason}
             disabled={busy || gate !== null}
             onChange={(e) => setReason(e.target.value)} />

      {/* The question replaces the controls it was asked from, so there is only
          ever one live decision on screen. Changing the skip takes it back. */}
      {gate
        ? <CheckpointGate digest={gate} name={forkName} onName={setForkName} busy={busy}
                          checkpointed={checkpointed}
                          onCheckpoint={() => void checkpointThenAdvance()}
                          onSkip={() => void skipWithoutCheckpoint()}
                          onCancel={() => setGate(null)} />
        : (
          <div className="picker">
            <button onClick={() => void preview()} disabled={busy || !ready}>Preview</button>
            {/* A reason is required by the endpoint, so the button that needs one
                says so by being disabled rather than by earning a 400. */}
            <button className="primary" onClick={() => void confirm()}
                    disabled={busy || !ready || !reason.trim()}
                    title={reason.trim() ? undefined : "An advance needs a reason"}>
              Advance time
            </button>
          </div>
        )}

      {saved && <div className="field-hint">{saved}</div>}
      {error && <div className="field-hint error">{error}</div>}

      {digest && <AdvanceDigestView digest={digest} outcome={outcome} />}

      {clock && clock.log.length > 0 && <ClockLog log={clock.log} />}
    </div>
  );
}


/** The checkpoint question (#107): a large skip, and the offer to copy the
 *  campaign before taking it.
 *
 *  A prompt rather than a refusal, and the composition of two primitives that
 *  already exist — `POST /campaigns/{cid}/fork` and `POST .../advance`. The
 *  copy is what stays behind: it is taken of the campaign as it stands, and the
 *  skip then happens in the campaign the reader is already in, so play carries
 *  on where they are and the checkpoint is a thing on the shelf to come back to.
 *
 *  Every number it says comes off the digest that opened it, so the sentence
 *  cannot disagree with the comparison that produced it.
 */
function CheckpointGate({ digest, name, onName, busy, checkpointed,
                         onCheckpoint, onSkip, onCancel }: {
  digest: AdvanceDigest; name: string; onName: (v: string) => void; busy: boolean;
  /** A checkpoint already taken for this question — the skip that followed it
   *  is what failed. The primary button retries only that half. */
  checkpointed: boolean;
  onCheckpoint: () => void; onSkip: () => void; onCancel: () => void;
}) {
  return (
    <div className="clock-checkpoint">
      {/* A backward move of the same size is asked about too, and says which
          direction it is going: "90 days" alone would read as the wrong one. */}
      <div className="field-hint">
        This is a large time skip — {dayCount(Math.abs(digest.elapsed_days))}
        {digest.backward ? " backward" : ""}, more than{" "}
        {dayCount(digest.fork_threshold)}. Save a checkpoint of this campaign first?
      </div>
      {/* The checkpoint holds the moment the campaign is at now, and is named
          for the skip it comes before rather than for that moment — "Before 24
          March 2027" is how the reader will look for it on the shelf, because
          the skip is the thing they will remember happening. */}
      <input aria-label="Checkpoint name" placeholder="Name for the checkpoint…"
             value={name} onChange={(e) => onName(e.target.value)}
             disabled={busy || checkpointed} />
      <div className="picker">
        {/* The fork endpoint refuses an empty name with a 400, so the button
            that needs one says so by being disabled rather than by earning it. */}
        <button className="primary" disabled={busy || !name.trim()} onClick={onCheckpoint}
                title={name.trim() ? undefined : "A checkpoint needs a name"}>
          {checkpointed ? "Retry the skip" : "Checkpoint, then advance"}
        </button>
        <button className="subtle" disabled={busy} onClick={onSkip}>Skip without one</button>
        <button className="subtle" disabled={busy} onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}


/** What an advance crossed, and what it leaves the campaign owing.
 *
 *  Its own component because the panel above it is a form and this is a report:
 *  they change for different reasons, and holding both in one function is what
 *  made the original 197 lines long enough for the code-health sweep to flag.
 *  Everything here is server-computed — the panel never recomputes a number, so
 *  what the reader approved is exactly what they got.
 */
function AdvanceDigestView({ digest, outcome }: { digest: AdvanceDigest; outcome: Outcome }) {
  const span = digest.backward
    ? `${dayCount(Math.abs(digest.elapsed_days))} back`
    : dayCount(digest.elapsed_days);
  return (
    <div className="clock-digest">
      <div className="field-hint">
        {{ preview: "Would advance", moved: "Advanced",
           unchanged: "Already at" }[outcome]}{" "}
        {digest.from_friendly ? `${digest.from_friendly} → ` : ""}{digest.to_friendly}
        {" · "}{span}
      </div>
      {/* Two ways `truncated` gets set and they need different sentences: the
          span was past what the digest scans (nothing listed) or a list hit
          its row cap (some of it listed). Saying "too long to itemize" above
          sixty itemized rows would be a plain lie. */}
      {digest.truncated && (
        <div className="field-hint">
          {digest.holidays.length || digest.birthdays.length
            ? "More was crossed than is listed here."
            : "Too long a span to itemize — holidays and birthdays are not listed."}
        </div>
      )}
      {digest.events.length > 0 && (
        <div className="side-section-body">
          {/* The one list here that is also a WRITE: confirming stamps these as
              fired (#101), which is why the heading changes with the outcome
              rather than saying "fired" over a preview that fired nothing. A
              backward correction reports what it un-lived and stamps nothing,
              so it keeps the neutral wording too. */}
          <h4>{outcome === "moved" && !digest.backward ? "Events fired" : "Scheduled events"}</h4>
          {digest.events.map((e) => (
            <div className="field-hint" key={e.id}>{e.name} — {e.friendly || e.date}</div>
          ))}
        </div>
      )}
      {digest.holidays.length > 0 && (
        <div className="side-section-body">
          <h4>Holidays crossed</h4>
          {digest.holidays.map((h) => (
            <div className="field-hint" key={`${h.name}-${h.native}`}>
              {h.name} — {h.friendly}
            </div>
          ))}
        </div>
      )}
      {digest.birthdays.length > 0 && (
        <div className="side-section-body">
          <h4>Birthdays crossed</h4>
          {/* Index-keyed: two actors can share a name and a birthday
              (twins, or one character cast at two versions), and a
              duplicate key silently drops a row from a list whose whole
              job is to be complete. The list is regenerated whole on
              every digest, never reordered in place. */}
          {digest.birthdays.map((b, i) => (
            <div className="field-hint" key={i}>
              {b.name} turns {b.age} — {b.friendly}
            </div>
          ))}
        </div>
      )}
      {digest.open_threads.length > 0 && (
        <div className="side-section-body">
          {/* Untouched by construction: a skip contains no scenes, so
              nothing in it can have moved a thread. The badge says how long
              that has been true on the far side of this move (#103). */}
          <h4>Still open</h4>
          {digest.open_threads.map((t) => (
            <div className="field-hint" key={t.id}>{t.title}{badge(agingLabel(t.aging))}</div>
          ))}
        </div>
      )}
      {digest.commitments.length > 0 && (
        <div className="side-section-body">
          {/* Commitments before threads is the ledger's order, and the reason
              holds here: a promise with a deadline is what a skip can break. */}
          <h4>Still owed</h4>
          {digest.commitments.map((c) => (
            <div className="field-hint" key={c.id}>{c.title}{badge(agingLabel(c.aging))}</div>
          ))}
        </div>
      )}
    </div>
  );
}

/** An aging label as a trailing clause, or nothing at all. Written once because
 *  a badge that appeared on one of the two lists and not the other would read
 *  as a claim about the difference between them. */
function badge(label: string) {
  return label ? <span className="field-hint"> · {label}</span> : null;
}

/** Where the campaign's present has been, newest first. */
function ClockLog({ log }: { log: ClockLogEntry[] }) {
  return (
    <div className="clock-log">
      <h4>Recent advances</h4>
      {log.slice(-5).reverse().map((e, i) => (
        <div className="field-hint" key={i}>
          {e.from ? `${e.from} → ` : ""}{e.to}{e.reason ? ` — ${e.reason}` : ""}
        </div>
      ))}
    </div>
  );
}
