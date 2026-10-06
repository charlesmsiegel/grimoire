import { useCallback, useEffect, useRef, useState } from "react";
import { api, type DriversSnapshot, type Notice } from "../api/client";
import { onNoticesChanged } from "../appEvents";
import { useHotkeys } from "../shortcuts/useHotkeys";
import { NoticeBanner } from "./NoticeBanner";
import { SceneConfirmForm } from "./SceneConfirmForm";
import { SceneIdeaPicker } from "./SceneIdeaPicker";
import { SceneImport } from "./SceneImport";
import type { SceneDraft } from "./sceneDraft";
import { StoryPressure } from "./StoryPressure";
import { NO_PRESSURE, applySeed, dropRefs, isActive, pruneControls, timeLabel, toRequest,
         type ChooserSeed, type PressureControls } from "./pressureControls";
import { useSceneSuggestions } from "./useSceneSuggestions";

/** What a stale refusal leaves behind: the reason no ideas came back, and the
 *  one thing to do about it. */
const STALE_NOTE = "Some selections are no longer current and were reset — press Regenerate.";
const SEED_UNREAD = "Story pressure could not be read; the Story Graph selection was not applied.";
/** A re-read that failed while controls were held: the disclosure is hidden,
 *  so what it held is reset rather than sent unseen. */
const PRESSURE_UNREAD = "Story pressure could not be read, so its selections were reset.";

function staleNote(names: string[], note = STALE_NOTE): string {
  return names.length ? `${note} Reset: ${names.join(", ")}.` : note;
}

/** Mode → pick → confirm → create. Props are unchanged from the
 *  commit-on-click version, so CampaignView's usage is untouched. */
export function NewSceneChooser({ cid, afterSid, ready, onClose, onCreated, seed }: {
  cid: string;
  afterSid: string | null;          // ranking reference: the selected (or latest) scene
  ready: boolean;
  /** `createdSid` is set only when a scene was actually created before this
   *  dismissal (a soft-failure "salvaged" scene abandoned via Escape/backdrop
   *  rather than "Continue to scene") -- see `salvagedSid` below. */
  onClose: (createdSid?: string) => void;
  onCreated: (sid: string, initialPrompt?: string) => void;
  /** A Story Graph handoff (§16.5): drivers to focus, or an anchor. Applied
   *  once, against this chooser's first drivers read, and never across a
   *  campaign switch. */
  seed?: ChooserSeed;
}) {
  // scene mode is picked first; nothing is fetched until then. "import" is
  // the one mode that never reaches the picker or the confirm form: an
  // imported scene brings its own title, cast, moment and transcript, so
  // there is nothing to suggest and nothing to open it with (#92).
  const [mode, setMode] = useState<"pc" | "offscreen" | "import" | null>(null);
  const [draft, setDraft] = useState<SceneDraft | null>(null);
  // Bumped every time a new draft is set. A late `onPicked` (an extraction
  // that resolves after the user already clicked a card) can replace `draft`
  // while SceneConfirmForm is already mounted; without a changing `key` React
  // reuses the existing instance and its useState initializers never re-run,
  // so the pane ends up mixing controls from the stale draft with state from
  // the new one. Keying on this counter forces a remount on every replacement.
  const [draftGen, setDraftGen] = useState(0);
  // A warning from the picker (an extraction that failed) has to outlive the
  // picker, which unmounts the moment a draft is emitted.
  const [notice, setNotice] = useState<string | null>(null);

  // The confirm form's create sequence is several writes long, and unmounting
  // it does not cancel them — a dismissal mid-sequence would strand a scene
  // nobody is told about. So the form reports when it is writing and the
  // orchestrator refuses to close.
  const [writing, setWriting] = useState(false);

  // Set once SceneConfirmForm salvages a soft failure into a real, created
  // scene (see its `salvaged` state). `writing` goes false at that point, so
  // Escape and the backdrop can dismiss the modal from here -- and unlike
  // every other dismissal, a scene now exists that CampaignView's scene list
  // does not know about yet. Read at dismiss time so `onClose` can report it;
  // CampaignView reloads its list only when this is non-null, which is
  // narrower than reloading on every dismissal (most dismissals wrote
  // nothing).
  const [salvagedSid, setSalvagedSid] = useState<string | null>(null);

  // Lifted out of SceneIdeaPicker (issue #319): that component unmounts the
  // instant a draft is picked, and again on Back (which clears `draft`
  // below). While this hook lived inside it, either unmount threw the
  // in-flight ranking away, and Back's remount re-ran the hook's mount
  // effect at `rank=true` -- a fresh, expensive, re-shufflable LLM call for
  // what the user experiences as "go back", discarding the typed direction
  // with it. Living here, the hook survives both: Back only swaps which pane
  // is shown. `direction` moves up for the same reason -- it has to survive
  // the picker unmounting on Back too.
  const [direction, setDirection] = useState("");
  // What is about to happen in the campaign and has not been mentioned to the
  // reader yet (#106). The second surface after the scene panel, and the one
  // that matters more: this is where a scene gets planned, and "the coronation
  // is in three days" is a thing to know BEFORE deciding what the next scene
  // is about.
  //
  // Campaign-scoped rather than scene-scoped, because there is no scene yet --
  // it reads from the campaign clock. A plain read with no model behind it, so
  // unlike the idea ranking it neither waits on `ready` nor on a mode: the
  // ranked call waits for a mode because it spends a generation, and this
  // spends nothing. A failed read leaves the list empty -- a banner is
  // the least important thing in this modal, and it must never be what stops a
  // scene being made.
  const [upcoming, setUpcoming] = useState<Notice[]>([]);
  const [noticeGen, setNoticeGen] = useState(0);
  const reloadNotices = useCallback(() => setNoticeGen((n) => n + 1), []);
  useEffect(() => {
    let live = true;
    api.campaignNotices(cid)
      .then((r) => { if (live) setUpcoming(r.notices); })
      .catch(() => { if (live) setUpcoming([]); });
    return () => { live = false; };
  }, [cid, noticeGen]);
  // The inspector underneath this modal shows the same ledger, so an
  // acknowledgement made there lands here too -- and vice versa (`appEvents`).
  useEffect(() => onNoticesChanged(reloadNotices), [reloadNotices]);

  // Story Pressure (capstone §16.2): what the reader has set on the campaign's
  // drivers and the batch's time. Beside `direction`, for the same reason --
  // it survives Back -- and read by the hook at dispatch, never as part of
  // the question, so setting a control spends nothing.
  const [pressure, setPressure] = useState<PressureControls>(NO_PRESSURE);
  // What the controls offer: `GET /continuity/drivers`, read once per open
  // and per campaign, and again after a stale refusal (`driversGen`). A
  // failed read hides the controls; Direction and Suggest keep working.
  const [driversRead, setDriversRead] = useState<
    { cid: string; snap: DriversSnapshot | null; failed: boolean } | null>(null);
  const [driversGen, setDriversGen] = useState(0);
  const [pressureNote, setPressureNote] = useState("");
  // Collapsed by default (§16.2); only an applied seed opens it.
  const [pressureOpen, setPressureOpen] = useState(false);
  // A seed is applied once, to the first read that settles. A ref because
  // the hook's `hold` reads it in the same render the read's result lands in.
  const seedApplied = useRef(false);
  const seedRef = useRef(seed);
  seedRef.current = seed;
  // Read inside the read's callback, which closes over an older render.
  const pressureRef = useRef(pressure);
  pressureRef.current = pressure;
  // The last read that succeeded, for naming what a refusal or a prune reset
  // by label rather than by ref; and the names one stale cycle has reset.
  const lastSnap = useRef<DriversSnapshot | null>(null);
  const resetNames = useRef<string[]>([]);

  useEffect(() => {
    let live = true;
    const labelOf = (ref: string) =>
      lastSnap.current?.drivers.find((d) => d.ref === ref)?.label
      ?? lastSnap.current?.anchors.find((a) => a.ref === ref)?.label ?? ref;
    api.continuityDrivers(cid)
      .then((snap) => {
        if (!live) return;
        const seeded = seedRef.current;
        if (seeded && !seedApplied.current) {
          // Before the read lands in state, so the render it causes is the one
          // that releases the hold -- with the seed already in `pressure`.
          seedApplied.current = true;
          const { controls, dropped } = applySeed(seeded, snap);
          setPressure(controls);
          setPressureNote(dropped.length
            ? `Not current any more, so not applied: ${dropped.join(", ")}` : "");
          if (isActive(controls)) setPressureOpen(true);
        } else {
          // Held to what this read lists (Decision 19). A refusal's refs are
          // the server's canonical spellings and can differ from what is held,
          // so this is what clears a control the reader can no longer see.
          const { controls, dropped } = pruneControls(pressureRef.current, snap);
          if (dropped.length) {
            setPressure(controls);
            resetNames.current = [...new Set([...resetNames.current, ...dropped.map(labelOf)])];
            setPressureNote(staleNote(resetNames.current));
          }
        }
        lastSnap.current = snap;
        setDriversRead({ cid, snap, failed: false });
      })
      .catch(() => {
        if (!live) return;
        if (seedRef.current && !seedApplied.current) {
          seedApplied.current = true;
          setPressureNote(SEED_UNREAD);
        } else if (isActive(pressureRef.current)) {
          // A re-read (after a stale refusal) failed with controls still held
          // -- a refusal spelled differently clears nothing. The disclosure is
          // about to be hidden, and a control nobody can see or reset must not
          // keep steering, or keep drawing the same refusal.
          const held = pressureRef.current;
          const names = [
            ...Object.keys(held.drivers).filter((ref) => held.drivers[ref] !== "normal")
              .map(labelOf),
            ...(held.time === "anchor" && held.anchor ? [labelOf(held.anchor)]
              : held.time !== "auto" ? [timeLabel(held)] : []),
          ];
          setPressure(NO_PRESSURE);
          resetNames.current = [...new Set([...resetNames.current, ...names])];
          setPressureNote(staleNote(resetNames.current, PRESSURE_UNREAD));
        }
        setDriversRead({ cid, snap: null, failed: true });
      });
    return () => { live = false; };
  }, [cid, driversGen]);

  // A control named a driver the campaign no longer lists. Reset what the
  // refusal names, say so, and read again -- the re-read's prune catches a
  // held ref the server spelled differently.
  function onStale(refs: string[]) {
    const snap = driversRead?.snap ?? null;
    const labelOf = (ref: string) => snap?.drivers.find((d) => d.ref === ref)?.label
      ?? snap?.anchors.find((a) => a.ref === ref)?.label ?? ref;
    setPressure((p) => dropRefs(p, refs));
    resetNames.current = refs.map(labelOf);
    setPressureNote(staleNote(resetNames.current));
    setDriversGen((n) => n + 1);
  }

  // The ranked call is made when a mode is picked (§3.10): `ready && playable`
  // is what gates it, and the picker's Regenerate is the reader's. "import"
  // never reaches the picker, and an imported scene brings its own title,
  // cast and transcript, so there is nothing to rank (#92). A seeded open
  // holds that call until the drivers read settles, so it carries the seed.
  const playable = mode === "pc" || mode === "offscreen";
  const pressureSnap = driversRead && driversRead.cid === cid && !driversRead.failed
    ? driversRead.snap : null;
  const suggestionsState = useSceneSuggestions(
    cid, afterSid, ready && playable, mode === "offscreen", {
      // Only what the reader can see steers: with the disclosure hidden (no
      // read, or a failed one) the request carries no pressure at all.
      controls: () => toRequest(pressureSnap ? pressure : NO_PRESSURE, pressureSnap),
      hold: !!seed && !seedApplied.current,
      onStale,
    });

  // CampaignView reuses this component across a `cid` navigation -- it stays
  // mounted, `chooserOpen` is untouched by the switch, so without an explicit
  // reset a draft picked in campaign A survives into campaign B and Create
  // would send A's title/location/cast/greeting id there. Adjusting state
  // during render (the documented React pattern for "reset state when a prop
  // changes") means the stale draft never gets a chance to paint against the
  // new cid, unlike resetting from an effect. `draftGen` only ever moves
  // forward here, same as `onPicked` already does -- never fed a fixed value
  // that could later collide with a key SceneConfirmForm has already used.
  const [seenCid, setSeenCid] = useState(cid);
  if (cid !== seenCid) {
    setSeenCid(cid);
    setMode(null);
    setDraft(null);
    setNotice(null);
    setDraftGen((n) => n + 1);
    // Synchronously, not from the effect above: that effect's request is still
    // in flight when the new campaign first paints, and until it settles the
    // banner would be showing campaign A's notices while already holding B's
    // `cid` -- so a dismissal would write A's occurrence key into B's ledger,
    // silencing a warning B never showed. Clearing here means the worst case is
    // an empty banner for one round trip.
    setUpcoming([]);
    // Direction now lives here rather than inside SceneIdeaPicker, so it no
    // longer resets for free when the picker unmounts on a `mode` reset --
    // a campaign switch must not leave campaign A's typed steer sitting in
    // campaign B's box. (`suggestionsState` resets itself: the hook drops
    // back to idle whenever `cid`, `afterSid` or the mode changes the
    // question, and the mode pick then asks campaign B's -- campaign A's cards
    // never sit in campaign B's picker.)
    setDirection("");
    // Story pressure is the campaign's, like the direction: a control on
    // campaign A's thread means nothing in B. A seed never crosses either --
    // it was sent for the campaign the chooser opened on.
    setPressure(NO_PRESSURE);
    setDriversRead(null);
    setPressureNote("");
    setPressureOpen(false);
    seedApplied.current = true;
    resetNames.current = [];
    lastSnap.current = null;
    // `writing` must reset here too. SceneConfirmForm's own create() sequence
    // stops issuing writes once its `live` ref notices this same switch (see
    // its comment) -- but every `setWriting(false)` on that abandoned path is
    // now guarded by that same `live` check and so never runs. Left alone,
    // `writing` would stay stuck true forever, and `dismiss()` below refuses
    // Escape, the backdrop, and every Cancel button while it is true -- for
    // the NEW campaign's freshly reset (mode-select) chooser, not just the
    // old one, since `writing` is not itself reset by anything else. That
    // would permanently lock the modal until a whole new create cycle
    // happened to flip it back through a live component (Critical, review).
    setWriting(false);
    // A soft failure may have salvaged a real scene (`salvagedSid`) that the
    // reader never dismissed before switching campaigns. There is no safe
    // way to report it from here the way `dismiss()` does: `onClose` (like
    // `cid`) is already bound to the NEW campaign by the time this branch
    // runs -- CampaignView redefines both together in the same render that
    // changed `cid`, so there is no live closure left pointing at the
    // campaign the reader just left. Even if there were, CampaignView's own
    // `installScenes` refuses to install a scene list for any campaign other
    // than the one it is currently showing, by design, so a call here could
    // not make that campaign's rail reflect the scene anyway. The scene is
    // not lost -- it exists on the backend and surfaces normally the next
    // time the reader navigates back to that campaign and it does its own
    // mount read -- it is just not pushed there proactively (Important,
    // review).
    setSalvagedSid(null);
  }

  // The single path every dismissal (Cancel, Escape, the backdrop) goes
  // through, so `salvagedSid` is reported consistently rather than only from
  // the two sites (Escape/backdrop) that can still fire once a scene is
  // salvaged -- the other dismissals just always carry `null`.
  function dismiss() {
    if (writing) return;
    onClose(salvagedSid ?? undefined);
  }

  // Escape, and — because this is a modal — the scene's own bindings held off
  // while it is up: `n` over the chooser must not open a second one. Disabled
  // rather than absent while `writing`, which is the same refusal `dismiss()`
  // makes and for the same reason: a create sequence several writes long does
  // not stop because the modal went away.
  useHotkeys(
    [{ keys: "escape", label: "Close the chooser", group: "THIS PANEL",
       enabled: !writing, whileTyping: true, run: dismiss }],
    { modal: true },
  );

  return (
    <div className="chooser-backdrop" role="dialog" aria-label="New scene"
         onClick={dismiss}>
      <div className="chooser" onClick={(e) => e.stopPropagation()}>
        <h3>New scene</h3>
        {/* Above the mode cards and every pane after them: it is context for
            the decision being made here, not a step in it. Dismissing writes to
            the same campaign-wide ledger the scene panel's banner does, so a
            warning closed in either place is closed in both. */}
        <NoticeBanner cid={cid} notices={upcoming} />

        {mode === null ? (
          <>
            <div className="role">What kind of scene?</div>
            <button className="chooser-card" onClick={() => setMode("pc")}>
              <span className="chooser-card-title">With your PC</span>
              <span className="chooser-card-premise">Your player character takes part.</span>
            </button>
            <button className="chooser-card" onClick={() => setMode("offscreen")}>
              <span className="chooser-card-title">Offscreen (NPCs only)</span>
              <span className="chooser-card-premise">
                What happens away from your PC — NPC plans, motivations, and events you don't witness.
              </span>
            </button>
            <button className="chooser-card" onClick={() => setMode("import")}>
              <span className="chooser-card-title">Import a transcript</span>
              <span className="chooser-card-premise">
                A scene you already have — a grimoire scene file, or a chapter of a Markdown
                export — read in as a scene here.
              </span>
            </button>
            <div className="form-actions">
              <button className="subtle" onClick={dismiss}>Cancel</button>
            </div>
          </>
        ) : mode === "import" ? (
          /* No draft, no picker, no confirm form: the file IS the draft, and
             `SceneImport` runs its own read → review → import over it. Back
             returns to the mode cards rather than to a picker that was never
             shown. `onCreated` takes no initial prompt — an imported scene
             opens on a transcript that is already written. */
          <SceneImport cid={cid} onBack={() => setMode(null)} onCancel={dismiss}
                       onImported={(sid) => onCreated(sid)} onWriting={setWriting} />
        ) : draft === null ? (
          <SceneIdeaPicker cid={cid} afterSid={afterSid} ready={ready}
                           pcless={mode === "offscreen"}
                           direction={direction} onDirectionChange={setDirection}
                           {...suggestionsState}
                           storyPressure={pressureSnap ? (
                             <StoryPressure snap={pressureSnap} value={pressure}
                                            onChange={setPressure} disabled={!ready}
                                            open={pressureOpen} onToggle={setPressureOpen} />
                           ) : null}
                           pressureNote={pressureNote}
                           onPicked={(d, warning) => {
                             setDraft(d); setNotice(warning ?? null);
                             setDraftGen((n) => n + 1);
                           }}
                           onCancel={dismiss} />
        ) : (
          <SceneConfirmForm key={draftGen} cid={cid} draft={draft} notice={notice} ready={ready}
                            onBack={() => setDraft(null)} onCancel={dismiss} onCreated={onCreated}
                            onWriting={setWriting} onSalvaged={setSalvagedSid} />
        )}
      </div>
    </div>
  );
}
