import { useMemo, useState } from "react";
import { Link, useLocation, useMatch, useNavigate, useParams } from "react-router-dom";
import { errorText } from "../api/errors";
import { useFocus } from "../components/focus";
import { PageShell } from "../components/PageShell";
import CastColumn from "../components/play/CastColumn";
import Conditions from "../components/play/Conditions";
import DossierColumn from "../components/play/DossierColumn";
import { PlayBanners } from "../components/play/PlayBanners";
import {
  type TranscriptActions
} from "../components/play/TranscriptPost";
import type { QuickReplyContext } from "../components/quickReplies";
import { NO_REROLL_ROUTE, type RerollRoute } from "../components/RerollRoute";
import NothingToWrapUp from "../components/review/NothingToWrapUp";
import ReviewColumn, { ApproveCitedButton } from "../components/review/ReviewColumn";
import ReviewPanel from "../components/review/ReviewPanel";
import ReviewTranscript from "../components/review/ReviewTranscript";
import { useReviewAddress } from "../components/review/useReviewAddress";
import { useSceneReview } from "../components/review/useSceneReview";
import { RollProposal } from "../components/RollProposal";
import { type RewrittenPost } from "../components/SceneInspector";
import { usePublishSceneSpend, usePublishShellContext } from "../components/ShellStatus";
import { useRunRegistry } from "../runs/RunRegistryProvider";
import { CampaignComposer } from "./campaign/CampaignComposer";
import { CampaignDialogs } from "./campaign/CampaignDialogs";
import { CampaignPanels } from "./campaign/CampaignPanels";
import { CampaignReplay } from "./campaign/CampaignReplay";
import { CampaignSceneBar } from "./campaign/CampaignSceneBar";
import { CampaignSceneSetup } from "./campaign/CampaignSceneSetup";
import { NO_MOODS, NO_REWRITES, sceneUrl, useStableCallback, useStableHandlers } from "./campaign/campaignShared";
import { CampaignTranscript } from "./campaign/CampaignTranscript";
import type { Current, DirectorNote, SceneKey, WindowKey } from "./campaign/campaignTypes";
import { executeTurn, type TurnRequest } from "./campaign/executeTurn";
import { askAfterPost as askPostFollowups } from "./campaign/postWriteFollowups";
import { stopRun } from "./campaign/runSettlement";
import { useAlternateActions } from "./campaign/useAlternateActions";
import { useCampaignNavigation } from "./campaign/useCampaignNavigation";
import { useCampaignPalette } from "./campaign/useCampaignPalette";
import { useCampaignPanels } from "./campaign/useCampaignPanels";
import { useCampaignPresentation } from "./campaign/useCampaignPresentation";
import { useCampaignShortcuts } from "./campaign/useCampaignShortcuts";
import { useComposerDraft } from "./campaign/useComposerDraft";
import { useComposerRecovery } from "./campaign/useComposerRecovery";
import { usePlayErrors } from "./campaign/usePlayErrors";
import { usePostEdits } from "./campaign/usePostEdits";
import { useProposalState } from "./campaign/useProposalState";
import { useQuickReplies } from "./campaign/useQuickReplies";
import { useRunRecovery } from "./campaign/useRunRecovery";
import { useRunRecoveryTriggers } from "./campaign/useRunRecoveryTriggers";
import type { RunSession } from "./campaign/useRunSession";
import { useRunSession } from "./campaign/useRunSession";
import { useSceneAccounting } from "./campaign/useSceneAccounting";
import { useSceneAlternates } from "./campaign/useSceneAlternates";
import { useSceneCatalog } from "./campaign/useSceneCatalog";
import { useSceneContinuity } from "./campaign/useSceneContinuity";
import { useSceneIdentity } from "./campaign/useSceneIdentity";
import { useSceneMetadata } from "./campaign/useSceneMetadata";
import { useSceneMutationLatch } from "./campaign/useSceneMutationLatch";
import { useSceneMutations } from "./campaign/useSceneMutations";
import { useSceneRolls } from "./campaign/useSceneRolls";
import { useSceneSeed } from "./campaign/useSceneSeed";
import { useSceneTracker } from "./campaign/useSceneTracker";
import { useTranscriptPresentation } from "./campaign/useTranscriptPresentation";
import { useTranscriptSpeakers } from "./campaign/useTranscriptSpeakers";
import { useTranscriptSwipes } from "./campaign/useTranscriptSwipes";
import { useTranscriptWindow } from "./campaign/useTranscriptWindow";
import { useTurnActions } from "./campaign/useTurnActions";

export default function CampaignView({ ready }: { ready: boolean }) {
  const { cid = "" } = useParams();

  // Focus mode: the scene bar and the scene head go with the app header and the
  // context column, leaving the transcript and the composer. Together they were
  // the ~300px above the first line of prose on a phone — the scene bar worst of
  // all, because eleven controls at a 44px touch target wrap into four rows at
  // 375px. The review bar is NOT included: a review is not a scene being read,
  // and it carries the only rename control that can reach an absorbing scene.
  const { focus } = useFocus();
  // The scene segment is a CHILD route (App.tsx), so `useParams` here — which
  // only sees params matched down to this element's own route — never carries
  // it. `useMatch` reads it off the full location instead, and does it without
  // a second CampaignView instance for the router to swap between.
  // Two shapes, one scene. `/wrap-up` is the review's own address: the rail's
  // Wrap-up row used to point at the scene itself, which was a real
  // destination but not a distinguishable one -- the row could never light,
  // because `scenes` already owns every path under `/scenes`, and a reader
  // could not link anyone to "the thing that is waiting". Both matches resolve
  // the same `:sid`; what the second one buys is that the app can tell which
  // question is being asked about it.
  const playMatch = useMatch("/campaigns/:cid/scenes/:sid");
  const wrapMatch = useMatch("/campaigns/:cid/scenes/:sid/wrap-up");
  const sid = (playMatch ?? wrapMatch)?.params.sid;
  /** Whether the URL is asking for the review rather than the transcript.
   *
   *  It does not *cause* the review -- `useSceneReview` adopts whatever the
   *  scene is holding either way, and has since reviews became durable. What
   *  it changes is what this page says when there is nothing to adopt: at
   *  `/wrap-up` that is an answer ("nothing here is waiting"), and rendering
   *  the transcript instead would silently answer a different question. */
  const wrapUp = !!wrapMatch;
  const navigate = useNavigate();
  const location = useLocation();

  const { activeId, currentCampaign, selectIdentity, selected } = useSceneIdentity({ cid });

  // The half of that which has to outlive this component -- see
  // `RunRegistryProvider`. `runRef` is the live turn's handle for Stop; this is
  // the record that a send happened at all, and it is the only thing left after
  // a navigation or a suspended tab.
  const registry = useRunRegistry();

  const { fail, setError, providerSucceeded, error } = usePlayErrors();

  const session: RunSession = useRunSession();
  const { busy, streamingId, streaming, streamingSpeakers, directorNote, roundProgress } = session;
  const { refreshProposal, setProposalNow, claimProposalRead, applyProposalRead, proposal } = useProposalState();

  const [ctxKey, setCtxKey] = useState(0);
  // The post a reader asked to replay FROM (#79) -- the one after the retcon,
  // since the retconned post itself stands. Held here rather than in the panel
  // because the transcript gutter is what sets it.
  const [replayAt, setReplayAt] = useState<number | null>(null);
  // Part of `ReplayPanel`'s key, so bumping it re-mounts the panel and makes it
  // re-read the replay session. Adopting a detached run is the case: it
  // refreshes the transcript, which is not what that panel reads. See
  // `attachToRun`.
  const [replayEpoch, setReplayEpoch] = useState(0);
  const {
    setDrawer, showChanges, setShowChanges, showIncoming, setShowIncoming, showComposition,
    setShowComposition, showMechanics, setShowMechanics, showCalendar, setShowCalendar, showStyle,
    setShowStyle, showCover, setShowCover, showTracker, setShowTracker, openPanel, closePanels, reviewRef,
    pinEpoch, setResolvedEpoch, resolvedEpoch, setPinEpoch, setReviewRef, showInspector, showGroup,
    setShowGroup, setShowInspector, drawer,
  } = useCampaignPanels();

  /** Whether this scene's director notes are visible.
   *
   *  Per scene and not persisted: it is a way of looking at what is on screen,
   *  like scrolling, and a reader who turned notes on to check one turn should
   *  not find them on in a scene they open next week. Reset by the effect that
   *  reads a scene, beside every other piece of per-scene view state. */
  const [showNotes, setShowNotes] = useState(false);
  const { rolling, markRenaming, takeRollLatch, renamesInFlight } = useSceneMutationLatch({ cid, activeId });
  const { refreshScenes, listSequence, sceneListCid, scenes, repointRow } = useSceneCatalog({ currentCampaign });
  const { sceneTitle, activePcless, activeDone, activeClosed, activeBranched } = useSceneMetadata({ scenes, activeId });
  const {
    refreshPreset, restoreParked, restoreDurableOnce, repointPark, consumeText, input, directing,
    setDirectMode, parkedTick, recoverPrompt, setPendingResponse, setInput, pendingResponse, setQuickNotice,
    insertQuick, heldKind, sceneResponse, chooseResponseOverride, clearResponseOverride, quickNotice,
  } = useComposerDraft({ activePcless, activeId, cid, selected, registry });
  const refreshRemovedCast = useStableCallback((scope: SceneKey) => readScene(scope));
  const {
    refreshContinuity, forgetGroup, cast, setSelectedActor, groupSaved, setCitedQuote, selectedActor,
    openActor, briefing, citedQuote, roster, closeActor, removeSelectedActor, casefile, provenance,
    sceneLocation, dt, weather, group,
  } = useSceneContinuity({ cid, activeId, ctxKey, readScene: refreshRemovedCast, fail, currentCampaign, selected });

  const { clearAlternates, renameAlternates, fetchAlternates, alternates } = useSceneAlternates();
  const { name, canGenerate, moduleBound, labels, worldName, sceneProblem, notReadyId, readModuleBound, colorQuotes } = useCampaignPresentation({ cid, ready, setError, currentCampaign, refreshScenes, listSequence });
  const onSceneSwitch = useStableCallback((_scope: SceneKey) => {
    // clear the previous scene's chip/popover synchronously so scene A's
    // proposal never renders against scene B while the fetch below is in
    // flight (and so a stale checkActors list can't leak across scenes).
    setProposalNow(null);
    setRollForm(null);
    // a one-shot override belongs to the turn the player picked it for, on
    // the scene they picked it on — switching scenes must not carry it
    // silently onto an unrelated scene's next reply.
    setPendingResponse(null);
    // Director notes are per scene, and the toggle is a way of looking at
    // the one on screen. Carried across, a reader who turned them on to
    // check one turn would find them on in every scene they opened after.
    setShowNotes(false);
    // Same reasoning for the error banner: it reports what happened to a turn
    // in the scene being left, and its Retry acts on whatever scene is open.
    // Leaving it up invites the player to re-run one scene's failure against
    // another (review, #95).
    setError(null);
    // scene A's alternates must never offer themselves against scene B while
    // the fetch below is in flight — same reason the proposal chip clears here
    clearAlternates();
    // The post a reader asked to replay from is an INDEX, and an index means
    // nothing in another scene: carried across, it prices — and would cut —
    // whatever post happens to sit there. Same reasoning as the one-shot
    // response override two blocks up.
    setReplayAt(null);
    // The reroll popover and the route in it, for the same reason and with
    // more at stake than the rest of this block. It is anchored to "the post
    // a reroll would replace", which is a different post in every scene — so
    // left open across a switch it reappears over the new scene's trailing
    // reply carrying the connection and model chosen for the old one's, and
    // the next click sends B's reply to a model picked for A (Codex review,
    // #77). The hint had the same hole before the route joined it; a wrong
    // model is the version of it that costs a generation.
    setRerollPrompt(null);
    setRerollRoute(NO_REROLL_ROUTE);
  });
  const onReadRequested = useStableCallback((window: WindowKey, renamed: boolean) => {
    fetchAlternates(window.cid, window.sid, window.token);
    refreshContinuity(window.cid, window.sid, renamed);
    setSelectedActor(null);
    refreshProposal(window.cid, window.sid);
    refreshPreset(window.cid, window.sid);
  });
  const onInstalled = useStableCallback((_window: WindowKey) => {
    session.clearPreview();
    setCtxKey((n) => n + 1);
  });
  const {
    readScene, transcriptIsActive, loaded, clearScene: clearSceneWindow, readTranscript, renameTranscript,
    verifiedTotal, followTail, windowToken, invalidateTranscript, showOptimistically, messages, firstIndex,
    hasUserPost, landedScene, goToQuote, openedFrom, streamRef, onStreamScroll, loadOlder, loadingOlder,
  } = useTranscriptWindow({
    cid, activeId, setError, selectIdentity, streaming, currentCampaign, onSceneSwitch, onReadRequested,
    onInstalled, setCitedQuote,
  });
  // The end-of-scene review, whole (#378): its state, the absorb and save that
  // fill and commit it, and the counts its column and panel read off it. A
  // review is a MODE of this page rather than a panel inside it -- the
  // transcript, the composer and every scene control step aside for it -- so
  // what stays here is only what genuinely straddles the two modes: the banner
  // it reports through, the scene it absorbs, and the fact that a saved review
  // has rewritten every file the play view's panels read.
  const review = useSceneReview({
    cid, activeId, rolling, fail,
    clearError: () => setError(null),
    dismissError: (from) => setError((e) => (e?.from === from ? null : e)),
    onSaved: () => setCtxKey((n) => n + 1),
  });

  // A live REVIEW holds the scene on exactly the same terms as a turn -- `turn`
  // and `review` share one exclusion key server-side -- so an absorb in flight
  // locks these controls too. It did not need saying before #396, because an
  // absorb could not outlive the request that asked for it and the reader was
  // sitting in front of it; detached, they can start one, walk away, and come
  // back to a scene the server is refusing writes for.
  //
  // Scoped to the scene the review belongs to. A review outlives a scene
  // switch, so an absorb still running for scene A must not lock scene B --
  // the server would allow a turn there, and the reader is entitled to play it.
  //
  // `holdsScene` rather than a condition spelled out here: there are four ways
  // a review can be holding a scene -- an absorb this browser started, one it
  // found and is waiting out, a scoped retry, and a Discard whose DELETE has
  // not answered yet -- and all four are `review` runs sharing one exclusion
  // key with `turn`. Enumerating them at this call site is how three of them
  // came to be missing. The hook owns the state, so the hook answers.
  const sceneLocked = !!activeId
    && (activeId === streamingId || review.holdsScene(activeId));
  const { absorb } = review;
  const { setSeedPrompt, seedPrompt } = useSceneSeed({ location, sid, cid, navigate });

  const removeSelectedActorStable = useStableCallback(() => void removeSelectedActor());
  const openDrawerActor = useStableCallback((kind: string, id: string) => setDrawer(
    { type: "actor", kind: kind as "characters" | "pcs", id }));
  const onCastChanged = useStableCallback(() => { if (activeId) void refreshAndAsk(activeId); });
  const onInspectorSceneChanged = useStableCallback(() => { if (activeId) refreshSavedScene(activeId, "Scene saved"); });
  // A restore from Turn history is a message edit, and asks what `saveEdit`
  // asks after one: it rewrites text the rolling summary may already cover.
  const onInspectorTranscriptEdited = useStableCallback(() => {
    const id = activeId;
    if (!id) return;
    selectScene(id).then((seen) => askAfterPost(id, seen)).catch((err: unknown) => {
      if (currentCampaign() === cid && (selected()?.sid ?? null) === id) {
        setError({ text: `Post restored, but the scene could not be re-read: ${errorText(err)}`, retryable: false });
      }
    });
  });
  const sceneRenamedStable = useStableCallback((id: string) => { sceneRenamed(id).catch((err: unknown) => fail(err, false)); });
  const { postChips, sceneSpend, budget, budgetSeen, setBudgetSeen, sceneCosts, onBudgetSaved, sceneSpendIsSpend } = useSceneAccounting({ cid, transcriptIsActive, activeId, sceneListCid, scenes, ctxKey, loaded });

  const { setTrackerTick, tracker, trackerKeys, trackerRerun, dossierTracker } = useSceneTracker({ loaded, ctxKey, selectedActor, activeId, cid });

  const rerollFromSwipe = useStableCallback((id: string, guidance: string, route: RerollRoute) => rerollResponse(id, guidance, route).catch((err: unknown) => fail(err, false, "scene")));
  const mutateFromSwipe = useStableCallback((id: string, variant: string) => mutateResponse(id, variant).catch((err: unknown) => fail(err, false, "scene")));
  // The empty-scene cast panel's mount condition, which is also when a quick
  // reply may open the opener generator inside it.
  const openerOffered = !!activeId && landedScene?.cid === cid && landedScene.sid === activeId
    && messages.length === 0;

  // The same treatment for the context column and the inspector, which are
  // memoized too and were handed fresh closures on every render.
  const goToQuoteStable = useStableCallback(goToQuote);
  const closeActorStable = useStableCallback(closeActor);
  // The posts on screen a stored rewrite changed, for Turn history. Only the
  // active scene's own: a restore addresses `activeId` by these indices.
  const rewrittenPosts = useMemo<RewrittenPost[]>(() => {
    if (!transcriptIsActive) return NO_REWRITES;
    const found = messages.flatMap((message, i) =>
      message.rewritten ? [{ index: firstIndex + i, message }] : []);
    return found.length ? found : NO_REWRITES;
  }, [transcriptIsActive, messages, firstIndex]);
  /** The scene the open review was absorbed FROM, which is not necessarily the
   *  one selected: a review survives a scene switch, and `saveAbsorb` already
   *  commits against `absorbSid` for that reason. Naming `activeId`'s title
   *  here would tell the reviewer they are judging a scene they are not. */
  const absorbTitle = review.absorbSid
    ? scenes.find((s) => s.id === review.absorbSid)?.title || review.absorbSid
    : sceneTitle;
  const {
    editing, refreshAndAsk, setEditing, toggleExcluded, saveEdit, saveRetcon, setPicking, deleteMessagesFrom,
    openCharacterPassage, setRegexTest, refreshSavedScene, refreshAfterReplay, characterPassage,
    setCharacterPassage, picking, insertImage, regexTest,
  } = usePostEdits({
    currentCampaign, cid, selected, selectScene, setError, activeId, loadScenes, setCtxKey, askAfterPost,
    rolling, transcriptIsActive, activeDone, takeRollLatch, fail, busy, sceneLocked, firstIndex, messages,
  });
  const {
    setRollForm, doRoll, resolve, toggleRollPop, rollForm, enterCheckMode, checkActors, doCheck,
    rollFromForm, showRollSyntax, setShowRollSyntax,
  } = useSceneRolls({
    activeId, busy, sceneLocked, rolling, takeRollLatch, cid, selectScene, askAfterPost, moduleBound,
    renamesInFlight, canGenerate, setProposalNow, runStream,
  });
  const { adoptPendingRun } = useRunRecovery({
    registry, session, recoverPrompt, readTranscript, fail,
    attachment: {
      session, registry, fail, recoverPrompt, setProposalNow, setError,
      readTranscript, bumpReplayEpoch: () => setReplayEpoch((n) => n + 1)
    }
  });
  const {
    responseDisabled, rerollAt, canReroll, canSwipe, altCount, altTitle, editingAny, variantSwipe,
    swipeTarget, extendDisabled, responseSwipe, stepAlternate, stepVariant,
  } = useTranscriptSwipes({
    messages, alternates, transcriptIsActive, activeClosed, hasUserPost, firstIndex, cid, activeId, loaded,
    editing, busy, rolling, sceneLocked, renamesInFlight, absorb, proposal, canGenerate, rerollFromSwipe,
    mutateFromSwipe,
  });

  const { citedNeedle, lastOfResponse, pickerTarget, runs, plateAvatar, noteCount, speakerOf, matchActor, isCited } = useTranscriptSpeakers({
    busy, streamingId, activeId, streamingSpeakers, cast, citedQuote, review, messages, firstIndex, roster,
    labels, showNotes, cid,
  });

  const {
    setRerollPrompt, setRerollRoute, sendText, repointReroll, clearRerollRetry, reroll, rerollResponse,
    extendResponse, rerollPrompt, rerollRoute, send, canRetry, retry, onKeyDown, respondAs,
  } = useTurnActions({
    busy, rolling, renamesInFlight, canGenerate, input, activeId, cid, loadScenes, currentCampaign,
    selectIdentity, navigate, consumeText, followTail, directing, setProposalNow, runStream, pendingResponse,
    recoverPrompt, setPendingResponse, session, showOptimistically, sceneLocked, editing, transcriptIsActive,
    extendDisabled, swipeTarget, messages, rerollAt, firstIndex, setReplayAt, fail,
  });
  const { pickAlternate, mutateResponse } = useAlternateActions({
    activeId, busy, rolling, sceneLocked, editing, transcriptIsActive, takeRollLatch, cid, currentCampaign,
    selected, selectScene, fail, responseSwipe, alternates, clearRerollRetry, setError, setProposalNow,
    invalidateTranscript, askAfterPost,
  });

  // ---- what the memoized transcript rows are handed ----
  //
  // Every handler is stable (`useStableHandlers`) and every value is memoized
  // on the state it reads, so a render that changes none of them -- every
  // composer keystroke, every streamed delta -- reaches no row at all. See
  // `components/play/TranscriptPost` for the rows' side of the contract.
  const transcriptActions = useStableHandlers<TranscriptActions>({
    openActor,
    openReroll: () => {
      setRerollPrompt("");
      // Cleared on OPEN rather than on close, so a popover dismissed with
      // Escape and reopened does not come back wearing the route the player
      // backed out of.
      setRerollRoute(NO_REROLL_ROUTE);
    },
    stepAlternate: (delta) => { pickAlternate(stepAlternate(delta)).catch((err: unknown) => fail(err, false, "scene")); },
    stepVariant,
    edit: (index, text) => setEditing({ index, text }),
    toggleExcluded: (index, excluded) => { toggleExcluded(index, excluded).catch((err: unknown) => fail(err, false, "scene")); },
    cancelEdit: () => setEditing(null),
    saveEdit: () => { saveEdit().catch((err: unknown) => fail(err, false, "scene")); },
    saveRetcon: () => { saveRetcon().catch((err: unknown) => fail(err, false, "scene")); },
    pickImage: (index, actor, speaker) =>
      setPicking({ index, target: pickerTarget(actor, speaker) }),
    cutFrom: (index) => { deleteMessagesFrom(index).catch((err: unknown) => fail(err, false, "scene")); },
    replayFrom: (index) => setReplayAt(index),
    branchFrom: (index) => { branchFrom(index).catch((err: unknown) => fail(err, false, "scene")); },
    setRerollPrompt,
    setRerollRoute,
    reroll: () => { reroll().catch((err: unknown) => fail(err, false, "scene")); },
    deleteResponse: (id) => { mutateResponse(id).catch((err: unknown) => fail(err, false, "scene")); },
    rerollResponse: (id, guidance, route) => { rerollResponse(id, guidance, route).catch((err: unknown) => fail(err, false, "scene")); },
    extendResponse: (id, guidance, route) => { extendResponse(id, guidance, route).catch((err: unknown) => fail(err, false, "scene")); },
    activateVariant: (id, variant) => { mutateResponse(id, variant).catch((err: unknown) => fail(err, false, "scene")); },
    createCharacter: (rid) => { openCharacterPassage(rid).catch((err: unknown) => fail(err, false, "scene")); },
    // Depth over the stored transcript, which the window always ends at: the
    // newest post is 0, whatever page of older ones is loaded above it.
    testRules: (index, m) =>
      setRegexTest({ message: m, depth: firstIndex + messages.length - 1 - index }),
    refreshTracker: () => setTrackerTick((n) => n + 1),
  });

  // The column is one swap zone: cast, or one actor. `columnMode` is derived
  // from `selectedActor` rather than stored beside it, so the two can never
  // disagree about which is showing.
  //
  // Both are memoized and every handler here is stable, so neither re-renders
  // for a keystroke or a streamed delta -- only for the state it shows.
  const column = selectedActor
    ? <DossierColumn cid={cid} casefile={casefile} provenance={provenance}
      onHoverQuote={setCitedQuote} onGoToTurn={goToQuoteStable}
      onBack={closeActorStable}
      onOpenActor={openDrawerActor}
      onRemove={removeSelectedActorStable} busy={sceneLocked}
      tracker={dossierTracker}
      onTrackerChanged={transcriptActions.refreshTracker} />
    : <CastColumn cid={cid} sid={activeId ?? ""} hasPosts={messages.length > 0} refreshKey={ctxKey}
      cast={cast} roster={roster} briefing={briefing}
      moods={tracker?.moods ?? NO_MOODS}
      onOpen={transcriptActions.openActor}
      // A confirmed enter or leave writes a transition line into the
      // transcript as well as moving the cast, so the scene is
      // re-read whole rather than the cast alone.
      onCastChanged={onCastChanged} />;
  const { transcript } = useTranscriptPresentation({
    cid, activeId, loaded, busy, rolling, transcriptIsActive, activeClosed, activeDone, responseDisabled,
    canGenerate, firstIndex, messages, rerollAt, canReroll, postChips, citedNeedle, lastOfResponse, tracker,
    trackerKeys, trackerRerun, canSwipe, alternates, altCount, altTitle, editingAny, sceneLocked,
    variantSwipe, rerollPrompt, rerollRoute, swipeTarget, extendDisabled, runs, plateAvatar, labels,
    transcriptActions, editing,
  });

  const goToScene = useStableCallback((id: string) => {
    // Clicking the row already open is a re-read, which is what that click did
    // before the scene lived in the URL. Routed through the resolver it would
    // do nothing at all instead: the pathname does not change, so the effect
    // never re-runs.
    if (id === selected()?.sid && selected()?.cid === cid) { readScene({ cid, sid: id }); return; }
    void navigate(sceneUrl(cid, id));
  });
  const clearScene = useStableCallback(() => {
    clearSceneWindow();
    forgetGroup(null);
  });
  const {
    newScene, branchFrom, sceneRenamed, renamingScene, setRenamingScene, commitSceneRename, forkAtScene,
    deleteScene, openBranch, chooserOpen, closeChooser, sceneCreated,
  } = useSceneMutations({
    loadScenes, currentCampaign, cid, setError, setSeedPrompt, goToScene, refreshAndAsk, markRenaming,
    registry, adoptSceneId, selected, selectScene, fail, activeId, setCtxKey, scenes, name, navigate,
    rolling, activeDone, takeRollLatch,
  });
  const { taskRunning, stripTasks, onInspectorTaskBusy, openerRequest, quickSet, runQuickReply } = useQuickReplies({
    cid, activeId, busy, rolling, renamesInFlight, canGenerate, followTail, sendText, doRoll, setQuickNotice,
    newScene, sceneLocked, ready, messages, firstIndex, currentCampaign, selected, setCtxKey, insertQuick,
  });
  // What decides each quick reply: the guards of the controls they stand for.
  const quickCtx: QuickReplyContext = {
    busy, rolling, renaming: renamesInFlight > 0, sceneLocked, posts: messages.length,
    moduleKnown: moduleBound !== null, pcless: activePcless, ready, canGenerate,
    openerOffered, taskRunning,
  };
  // The setup date field retains this callback across its PUT. Unlike the
  // inspector's latest-render adapter, it must retain the render that started
  // the write: the child reports only the new id, not the initiating scene.
  function onSetupSceneRenamed(id: string) {
    const origin = activeId ? { cid, sid: activeId } : null;
    sceneRenamed(id).catch((err: unknown) => {
      if (origin && currentCampaign() === origin.cid && selected()?.cid === origin.cid
          && (selected()?.sid === origin.sid || selected()?.sid === id)) fail(err, false);
    });
  }

  function loadScenes() { return refreshScenes(cid); }
  function selectScene(id: string, stillWanted?: Current, renamed = false) {
    return readTranscript({ cid, sid: id }, { stillWanted, renamed });
  }

  // A scene's id is its filename, so a rename mints a new one. `scene_refs.repoint`
  // carries every *persisted* reference across; the rest live only in this
  // browser, where no server-side repointer can see them. Three of those belong
  // to an open review and are repointed by `useSceneReview.sceneRenamed`, whose
  // comment is where the reasoning for each of them lives; the fourth is the
  // reroll-alternates state, and it is here.
  function reviewSceneRenamed(oldId: string, newId: string) {
    review.sceneRenamed(oldId, newId);
    // The alternates sidecar moves with the scene file (`scene_refs.repoint`),
    // but the id this state is scoped to lives only here — left stale, the
    // scope gate reads the set as another scene's and the control vanishes
    // until something else re-selects the scene.
    renameAlternates({ cid, sid: oldId }, newId, windowToken());
    renameTranscript({ cid, sid: oldId }, newId);

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
    // `selected`, not the render-captured `activeId`: this runs from handlers
    // that awaited a request, and the reader can have moved on since. Adopting
    // the new id for a scene they are no longer looking at moves them to it.
    //
    // And the CAMPAIGN as well as the id, because the id alone does not say
    // which scene it is. A rename in campaign A finishing after the reader
    // moved to B, whose active scene carries the same id — routine, since each
    // campaign numbers its scenes from 001 — would otherwise adopt A's new id
    // and replace B's history entry with A's scene URL, moving the reader to
    // another campaign entirely (codex review).
    // `currentCampaign` as well as `selected`, and the difference is not academic:
    // `selected` says which campaign the loaded scene came from, which is
    // still the OLD one during the window after the reader has navigated to
    // another campaign but before its first scene has loaded. A rename landing
    // in that window passed an `selected`-only test and navigated them
    // straight back. Only `currentCampaign` answers "is the view still here".
    if (currentCampaign() === cid
      && (selected()?.sid ?? null) === oldId && (selected()?.cid ?? null) === cid) {
      selectIdentity({ cid, sid: newId });
      // …and the URL with it, in the same batch as `selectIdentity`, so the resolver
      // sees one consistent pair and leaves this rename alone. `replace`: the
      // reader did not go anywhere, and the entry being replaced names a file
      // that no longer exists. React 18 batches both updates into one render,
      // so there is no intermediate state where the URL still says `oldId`
      // while the ref says `newId` — which the resolver would read as the
      // reader having navigated, and chase back to a scene that is now gone.
      void navigate(sceneUrl(cid, newId), { replace: true });
    }
    repointRow({ cid, sid: oldId }, newId);
    // Compared against this handler's OWN campaign, not the one on screen: the
    // scene that was renamed is this campaign's, so its premise follows the new
    // id wherever the reader happens to be standing.
    setSeedPrompt((p) =>
      (p && p.cid === cid && p.sid === oldId ? { ...p, sid: newId } : p));
    // Review, alternates, and transcript ownership follow the same source scope.
    reviewSceneRenamed(oldId, newId);
    // Campaign-scoped like the seed prompt just above, and for the same
    // sentence: the renamed scene is THIS handler's campaign's, so a bare
    // scene id would move a park belonging to whichever campaign shares that
    // number (Codex review, round 4).
    repointPark(cid, oldId, newId);
    repointReroll(oldId, newId);
  }

  function askAfterPost(id: string, upto: number) {
    askPostFollowups({ cid, sid: id }, upto, selected, () => setCtxKey((n) => n + 1));
  }
  function runStream(id: string, start: TurnRequest["start"], recoverText?: () => void,
    rerolling = false, durableText = "", ephemeral = false,
    note: DirectorNote | null = null, route: RerollRoute = NO_REROLL_ROUTE) {
    return executeTurn({
      scope: { cid, sid: id }, start, recoverText, rerolling,
      durableText, ephemeral, note, route
    }, {
      canGenerate, renamesInFlight, session, registry, setError, verifiedTotal, fail,
      setProposalNow, providerSucceeded, readTranscript, refreshSwipe: responseSwipe.refresh,
      attachment: {
        session, registry, fail, recoverPrompt, setProposalNow, setError,
        readTranscript, bumpReplayEpoch: () => setReplayEpoch((n) => n + 1)
      },
      settlement: { session, selected, claimProposalRead, applyProposalRead, readTranscript },
    });
  }

  // No-op unless a turn is in flight; the abort rejects the fetch, `runStream`
  // recognises it and unwinds without an error banner.
  //
  // Closing the connection is NO LONGER the cancel, and that is the whole point
  // of detaching a turn from its request: a locked phone must not kill the
  // generation, so neither does an abort. The run has to be told separately, or
  // Stop would leave the provider generating, spending, and persisting a reply
  // the player explicitly stopped -- while holding the scene against the next
  // send. The abort still runs, and first, because that is what unwinds this
  // view; the cancel is a request the backend answers on its own schedule.
  //
  // The handle is read BEFORE the abort: `runStream`'s `finally` clears it.
  function cancelTurn() {
    const handle = session.handle();
    const observing = !!session.observerActive();
    session.abortObserver();
    // Stored, not awaited here: this is an onClick and the button must not hang
    // on it. `runStream`'s teardown awaits it before releasing the scene, which
    // is where the waiting belongs -- see `cancelRef`.
    if (handle) {
      if (observing) session.storeCancellation(stopRun(handle));
      else {
        // A reload can be locked while recovery is still discovering. There
        // is no stream's finally to await cancellation in that state, so this
        // path owns its result and lets recovery establish the durable facts.
        void stopRun(handle).then(() => adoptPendingRun(handle.cid, handle.sid)).catch(() => {
          if (registry.pending(handle.cid, handle.sid)?.attempt === handle.attempt) {
            setError({
              text: "could not reach the server to stop this turn — it "
                + "may still be generating", retryable: false
            });
          }
        });
      }
    }
  }

  useReviewAddress({ cid, activeId, wrapUp, review });

  // The global status bar can't work this out for itself: the router hands it
  // a cid, not a name, and which scene is open is state that lives only here.
  // During a review it names the scene being absorbed, not the one selected —
  // the scene is off the screen, so the pill is the only thing still saying it.
  // The other half of the header's scene pill. Published rather than read by
  // the chrome for `usePublishShellContext`'s reason: the header has a
  // pathname, and which scene is open is state that lives only here.
  usePublishSceneSpend(absorb ? null : sceneSpend);
  usePublishShellContext(
    name ? {
      campaign: name,
      scene: absorb ? `Absorbing ${absorbTitle}`
        : wrapUp ? `Wrap-up · ${sceneTitle}` : sceneTitle
    } : null);

  // Navigation initiates reads before draft restoration and run adoption.
  useCampaignNavigation({ selected, cid, readScene, navigate, sceneListCid, sid, scenes, clearScene, activeId });
  useComposerRecovery({ restoreParked, cid, activeId, parkedTick, input, directing, restoreDurableOnce, registry });
  useRunRecoveryTriggers({ activeId, adoptPendingRun, cid, selected, fail });
  useCampaignShortcuts({
    absorb, activeDone, activeClosed, busy, rolling, renamesInFlight, canGenerate, send, newScene, editing,
    canReroll, setRerollPrompt, variantSwipe, stepVariant, setRerollRoute, error, canRetry, retry, fail,
  });
  useCampaignPalette({ cast, openActor, briefing, scenes, goToScene, newScene, cid });

  // The wrap-up address with nothing to wrap up -- see `NothingToWrapUp`.
  //
  // `review.absorbing` covers the window where an absorb IS running and its
  // payload has not landed: that is a review arriving, not one that is absent.
  if (wrapUp && !absorb && !review.absorbing) {
    return <NothingToWrapUp cid={cid} campaignName={name} sid={activeId}
      sceneTitle={sceneTitle} scenes={scenes} />;
  }

  return (
    <PageShell
      className={absorb ? "review" : "play"}
      columnLabel={absorb ? "Proposals" : selectedActor ? "Dossier" : "Cast and continuity"}
      column={absorb ? <ReviewColumn review={review} /> : column}
      footer={absorb
        ? <ApproveCitedButton review={review} />
        : <Conditions cid={cid} worldName={worldName} location={sceneLocation}
          datetime={dt} weather={weather} />}
    >
      <div className="workspace">
        {/* Export, Ledger, Calendar, Cover, End scene: all of them act on the
            scene, and a review is not the scene. The bar is replaced by one
            that names what is being judged, so the header still says where you
            are once the transcript stops being the thing on screen. */}
        <CampaignSceneBar
          absorb={absorb}
          name={name}
          renamingScene={renamingScene}
          setRenamingScene={setRenamingScene}
          commitSceneRename={commitSceneRename}
          review={review}
          activeId={activeId}
          absorbTitle={absorbTitle}
          focus={focus}
          cid={cid}
          noteCount={noteCount}
          showNotes={showNotes}
          setShowNotes={setShowNotes}
          newScene={newScene}
          showChanges={showChanges}
          setShowChanges={setShowChanges}
          showIncoming={showIncoming}
          setShowIncoming={setShowIncoming}
          showComposition={showComposition}
          setShowComposition={setShowComposition}
          showMechanics={showMechanics}
          setShowMechanics={setShowMechanics}
          showCalendar={showCalendar}
          setShowCalendar={setShowCalendar}
          showStyle={showStyle}
          setShowStyle={setShowStyle}
          showCover={showCover}
          setShowCover={setShowCover}
          showTracker={showTracker}
          setShowTracker={setShowTracker}
          openPanel={openPanel}
          closePanels={closePanels}
          activeClosed={activeClosed}
          fail={fail}
          busy={busy}
          rolling={rolling}
          streamingId={streamingId}
          sceneLocked={sceneLocked} />
        {/* The scene rail is gone. It cost 220px of transcript on every turn of every scene
            to answer a question asked a few times a session, and it could not be
            searched. ⌘K lists the same scenes, costs nothing until asked, and
            covers the rest of the app besides. */}
        <section className="main">
          {/* A review REPLACES the scene; it does not sit on top of it (4c).
              The transcript, the composer, the scene's own actions and every
              panel they open are the play view, and a reviewer judging eighteen
              proposals is not playing — leaving them mounted underneath put the
              review at the top of a scroll that still ran on past it, and put
              End scene one mis-click from discarding every proposal already
              judged. The transcript is still on screen, as the third pane
              below: read-only, and there to check a quote against. */}
          {/* The page's banners sit ABOVE that split, and are the only things
              that do (`PlayBanners` says why). */}
          <PlayBanners ready={canGenerate} problem={sceneProblem} readyId={notReadyId}
            budget={budget} budgetSeen={budgetSeen} onBudgetSeen={setBudgetSeen}
            error={error} onRetry={() => { retry().catch((err: unknown) => fail(err, false, "scene")); }} retryDisabled={busy || rolling || !canRetry}
            onDismissError={() => setError(null)} />
          <CampaignPanels
            absorb={absorb}
            focus={focus}
            showCalendar={showCalendar}
            cid={cid}
            showMechanics={showMechanics}
            readModuleBound={readModuleBound}
            showStyle={showStyle}
            activeId={activeId}
            refreshSavedScene={refreshSavedScene}
            showCover={showCover}
            showTracker={showTracker}
            transcriptActions={transcriptActions}
            showChanges={showChanges}
            showIncoming={showIncoming}
            reviewRef={reviewRef}
            pinEpoch={pinEpoch}
            setResolvedEpoch={setResolvedEpoch}
            showComposition={showComposition}
            resolvedEpoch={resolvedEpoch}
            setPinEpoch={setPinEpoch}
            setReviewRef={setReviewRef}
            setShowIncoming={setShowIncoming}
            review={review} />
          {absorb && <ReviewPanel review={review} />}
          {!absorb && (<>
            <CampaignSceneSetup
              showInspector={showInspector}
              activeId={activeId}
              cid={cid}
              ctxKey={ctxKey}
              onInspectorSceneChanged={onInspectorSceneChanged}
              sceneRenamedStable={sceneRenamedStable}
            onSetupSceneRenamed={onSetupSceneRenamed}
              activePcless={activePcless}
              sceneLocked={sceneLocked}
              markRenaming={markRenaming}
              messages={messages}
              transcriptIsActive={transcriptIsActive}
              sceneCosts={sceneCosts}
              budget={budget}
              onBudgetSaved={onBudgetSaved}
              rewrittenPosts={rewrittenPosts}
              onInspectorTranscriptEdited={onInspectorTranscriptEdited}
              stripTasks={stripTasks}
              onInspectorTaskBusy={onInspectorTaskBusy}
              focus={focus}
              scenes={scenes}
              sceneSpend={sceneSpend}
              sceneSpendIsSpend={sceneSpendIsSpend}
              renamingScene={renamingScene}
              setRenamingScene={setRenamingScene}
              commitSceneRename={commitSceneRename}
              sceneTitle={sceneTitle}
              activeBranched={activeBranched}
              activeClosed={activeClosed}
              forkAtScene={forkAtScene}
              fail={fail}
              deleteScene={deleteScene}
              openerOffered={openerOffered}
              openerRequest={openerRequest}
              ready={ready}
              refreshAndAsk={refreshAndAsk}
              seedPrompt={seedPrompt}
              openedFrom={openedFrom} />
            <CampaignTranscript
              colorQuotes={colorQuotes}
              streamRef={streamRef}
              onStreamScroll={onStreamScroll}
              firstIndex={firstIndex}
              loadOlder={loadOlder}
              loadingOlder={loadingOlder}
              transcript={transcript}
              directorNote={directorNote}
              busy={busy}
              cid={cid}
              activeId={activeId}
              streaming={streaming}
              streamingId={streamingId}
              streamingSpeakers={streamingSpeakers}
              messages={messages}
              speakerOf={speakerOf}
              labels={labels}
              plateAvatar={plateAvatar}
              matchActor={matchActor}
              roundProgress={roundProgress} />
            {proposal && activeId && (
              <RollProposal key={proposal.id} record={proposal} busy={busy || rolling} generationDisabled={!canGenerate}
                onResolve={(body) => { resolve(body).catch((err: unknown) => fail(err, false, "scene")); }} />
            )}
            {activeId && (
              /* Keyed by scene: the panel holds the walk's local state (whether a
                 turn has been run and is waiting on a verdict), and carrying that
                 across a scene switch would offer Accept for a reply in another
                 scene. `onChanged` is the same refresh every transcript mutation
                 here funnels through. */
              <CampaignReplay
                activeId={activeId}
                replayEpoch={replayEpoch}
                cid={cid}
                replayAt={replayAt}
                busy={busy}
                rolling={rolling}
                canGenerate={canGenerate}
                takeRollLatch={takeRollLatch}
                adoptPendingRun={adoptPendingRun}
                fail={fail}
                setReplayAt={setReplayAt}
                activeDone={activeDone}
                activeClosed={activeClosed}
                openBranch={openBranch}
                navigate={navigate}
                refreshAfterReplay={refreshAfterReplay} />
            )}
            {activeClosed && !activeDone ? (
              /* A closed branch: replaced like an absorbed scene's composer, and
                 with no editing either -- the sibling's absorb is the past the
                 campaign holds, and every write here is refused. */
              <div className="scene-complete scene-closed">
                A sibling branch was absorbed:{" "}
                <Link to={sceneUrl(cid, activeClosed.sid)}>{activeClosed.title}</Link>.
                {" "}This branch is read-only — delete it to discard it.
              </div>
            ) : activeDone ? (
              /* The whole composer, not a disabled entry box. This scene's summary
                 is written and its changes are applied, so a post added now would
                 sit outside the record taken of it -- and a greyed-out textarea
                 still says "you could type here". Editing and rerolling existing
                 posts stay available: those change what was absorbed, rather than
                 adding to a scene that is closed. */
              <div className="scene-complete">✓ Scene complete</div>
            ) : (
              <CampaignComposer
                directing={directing}
                activePcless={activePcless}
                setDirectMode={setDirectMode}
                heldKind={heldKind}
                pendingResponse={pendingResponse}
                sceneResponse={sceneResponse}
                chooseResponseOverride={chooseResponseOverride}
                clearResponseOverride={clearResponseOverride}
                activeId={activeId}
                group={group}
                showGroup={showGroup}
                setShowGroup={setShowGroup}
                selectedActor={selectedActor}
                quickNotice={quickNotice}
                cid={cid}
                showInspector={showInspector}
                setShowInspector={setShowInspector}
                cast={cast}
                currentCampaign={currentCampaign}
                selected={selected}
                groupSaved={groupSaved}
                quickSet={quickSet}
                quickCtx={quickCtx}
                runQuickReply={runQuickReply}
                moduleBound={moduleBound}
                sceneLocked={sceneLocked}
                busy={busy}
                messages={messages}
                toggleRollPop={toggleRollPop}
                rollForm={rollForm}
                rolling={rolling}
                setRollForm={setRollForm}
                enterCheckMode={enterCheckMode}
                checkActors={checkActors}
                doCheck={doCheck}
                fail={fail}
                rollFromForm={rollFromForm}
                showRollSyntax={showRollSyntax}
                setShowRollSyntax={setShowRollSyntax}
                input={input}
                setInput={setInput}
                onKeyDown={onKeyDown}
                renamesInFlight={renamesInFlight}
                canGenerate={canGenerate}
                send={send}
                respondAs={respondAs}
                cancelTurn={cancelTurn}
                notReadyId={notReadyId} />
            )}
          </>)}
        </section>
        <CampaignDialogs
          characterPassage={characterPassage}
          cid={cid}
          activeId={activeId}
          setCharacterPassage={setCharacterPassage}
          currentCampaign={currentCampaign}
          selected={selected}
          refreshSavedScene={refreshSavedScene}
          drawer={drawer}
          setDrawer={setDrawer}
          chooserOpen={chooserOpen}
          ready={ready}
          closeChooser={closeChooser}
          sceneCreated={sceneCreated}
          fail={fail}
          picking={picking}
          insertImage={insertImage}
          setPicking={setPicking}
          regexTest={regexTest}
          setRegexTest={setRegexTest} />
      </div>
      {/* A SIBLING of the workspace, not a child of it. `.shell.review
          .shell-main` is a row flex whose two items are the review and this —
          which is the whole "three panes" layout — and nested inside the
          workspace's column flex it was never beside the review at all, only
          stacked under it and clipped to 320px. */}
      {/* The third pane, and the reason the review is worth its own layout:
            judging a proposal means reading the line it came from, and reading
            it in another tab means losing the row. Rendered flat — speaker,
            then text — rather than through the transcript's own machinery,
            which carries edit, reroll and alternate controls that have no
            business in a review. */}
      {absorb && (
        <ReviewTranscript messages={messages} speakerOf={speakerOf} isCited={isCited} />
      )}
    </PageShell>
  );
}
