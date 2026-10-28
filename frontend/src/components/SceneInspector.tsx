import { memo, useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, type AuthorsNotesNext } from "../api/client";
import { AuthorsNotesPanel } from "./AuthorsNotesPanel";
import { ClockPanel } from "./ClockPanel";
import { CostPanel } from "./CostPanel";
import { EventsPanel } from "./EventsPanel";
import { CampaignModels } from "./inference/CampaignModels";
import { RecordDrawer, type DrawerTarget } from "./RecordDrawer";
import { ResponseTargetsPicker } from "./ResponseTargetsPicker";
import { SceneFocusEditor } from "./SceneFocusEditor";
import { BriefingSection } from "./sceneInspector/BriefingSection";
import { CastSection } from "./sceneInspector/CastSection";
import { PinsSection } from "./sceneInspector/PinsSection";
import { ContextSection, DecisionsSection, TurnHistorySection } from "./sceneInspector/PromptContextSections";
import { RewriteHistory, useRewriteHistory } from "./sceneInspector/RewriteHistory";
import { LocationSection, WhenSection } from "./sceneInspector/SceneMomentSections";
import { SceneProgressSections } from "./sceneInspector/SceneProgressSections";
import { SideSection, useSectionCollapse } from "./sceneInspector/SideSection";
import { StorySection } from "./sceneInspector/StorySection";
import type { InspectorProps } from "./sceneInspector/types";
import { useInspectorCast } from "./sceneInspector/useInspectorCast";
import { useInspectorPins } from "./sceneInspector/useInspectorPins";
import { usePromptHistory } from "./sceneInspector/usePromptHistory";
import { useSceneMoment } from "./sceneInspector/useSceneMoment";
import { useSceneProgress } from "./sceneInspector/useSceneProgress";
import { WeatherWidget } from "./WeatherWidget";
export type { RewrittenPost } from "./sceneInspector/types";

/** The scene's rail of everything the next prompt is made of.
 *
 *  Memoized: the play view re-renders on every composer keystroke and every
 *  streamed delta, and hands this stable handlers, so it re-renders for what
 *  it shows -- the scene, the refresh beat, the cost reads -- and not for those.
 *  With a large campaign that matters, because its pickers are an option per
 *  character and per location. */
export const SceneInspector = memo(function SceneInspector({
  cid, sid, refreshKey, onSceneChanged, onSceneRenamed, pcless, sceneLocked, onRenaming, posts,
  usage, budget, onBudgetSaved, rewritten, onTranscriptEdited, stripTasks, onTaskBusy,
}: InspectorProps) {
  // Which author's notes apply to the NEXT turn (play controls V), for the
  // section's count. Stamped with its scene, like the briefing, so a late answer
  // for the previous scene never counts against this one.
  const [notesNext, setNotesNext] = useState<{ cid: string; sid: string;
                                               data: AuthorsNotesNext } | null>(null);
  const [notesTick, setNotesTick] = useState(0);
  // Focus's canonical PUT response already refreshes its own metadata. Only
  // live composition is invalidated here: the parent's onSceneChanged reloads
  // the transcript and clears in-flight NPC boundaries/Keep-writing seeds.
  // Authorial editing is permitted during those runs and must not disturb them.
  const [focusTick, setFocusTick] = useState(0);
  const onFocusSaved = useCallback(() => setFocusTick((t) => t + 1), []);
  const [drawer, setDrawer] = useState<DrawerTarget | null>(null);

  // Action errors carry their scene just like data. A prior owner's callback
  // cannot clear or report over a new scene's error, even between commits.
  const owner = useRef(`${cid}/${sid}`);
  useLayoutEffect(() => { owner.current = `${cid}/${sid}`; }, [cid, sid]);
  const [actionError, setActionError] = useState<{ key: string; message: string } | null>(null);
  const reportError = useCallback((message: string | null) => {
    const key = `${cid}/${sid}`;
    if (owner.current !== key) return;
    setActionError(message === null ? null : { key, message });
  }, [cid, sid]);
  const error = actionError?.key === `${cid}/${sid}` ? actionError.message : null;
  const sections = useSectionCollapse();
  const { collapsed, toggleSection } = sections;
  const progress = useSceneProgress({ cid, sid, refreshKey, onTaskBusy });
  const history = usePromptHistory({ cid, sid, refreshKey, focusTick,
    contextCollapsed: !!collapsed.context, onError: reportError });
  const castView = useInspectorCast({ cid, sid, refreshKey, pcless, onSceneChanged,
    onTransition: progress.askRolling, onError: reportError });
  const rules = useInspectorPins({ cid, sid, refreshKey, onSceneChanged, onError: reportError });
  const moment = useSceneMoment({ cid, sid, refreshKey, onSceneChanged, onSceneRenamed, onRenaming,
    onTransition: progress.askRolling, onError: reportError });
  const rewriteHistory = useRewriteHistory({ cid, sid, onSceneChanged, onTranscriptEdited });
  const { cast } = castView;
  useEffect(() => {
    let live = true;
    api.getAuthorsNotesNext(cid, sid)
      .then((data) => { if (live) setNotesNext({ cid, sid, data }); })
      .catch(() => { if (live) setNotesNext(null); });
    return () => { live = false; };
  }, [cid, sid, refreshKey, notesTick]);
  const nextNotes = notesNext && notesNext.cid === cid && notesNext.sid === sid
    ? notesNext.data : null;
  const onNotesSaved = useCallback(() => setNotesTick((t) => t + 1), []);

  return (
    <aside className="inspector">
      {error && <div className="banner">{error}</div>}
      <BriefingSection cid={cid} sid={sid} refreshKey={refreshKey} posts={posts} {...sections} />
      {pcless && (
        <SideSection id="offscreen" title="Offscreen scene" collapsed={!!collapsed.offscreen} onToggle={toggleSection}>
          <div className="field-hint">No player character — you direct the NPCs.</div>
        </SideSection>
      )}

      <StorySection cid={cid} sid={sid} refreshKey={refreshKey} {...sections} />
      <SceneProgressSections cid={cid} sid={sid} sceneLocked={sceneLocked} stripTasks={stripTasks}
        progress={progress} {...sections} />
      <CastSection cid={cid} sid={sid} pcless={pcless} castView={castView} pinActions={rules}
        setDrawer={setDrawer} {...sections} />
      <PinsSection cid={cid} sid={sid} rules={rules} chars={castView.chars} pcs={castView.pcs} {...sections} />
      <LocationSection cid={cid} location={moment.location} setDrawer={setDrawer} {...sections} />
      <SideSection id="style" title="Response preset" collapsed={!!collapsed.style} onToggle={toggleSection}>
        <ResponseTargetsPicker scope="scene" cid={cid} sid={sid} onChanged={onSceneChanged} />
      </SideSection>

      {/* Beside the response preset, and campaign-scoped like the Cost section's
          budget: the two questions a reader asks here are "how long should
          replies be" and "which model is writing them" (#142). This campaign's
          overrides of Primary, Fast and Decision and of its routes (spec 10).
          Collapsed by default -- most campaigns never override a model, and
          `SideSection` renders no body while shut, so nothing is read until it
          is opened. */}
      <SideSection id="models" title="Models" collapsed={collapsed.models ?? true}
                   onToggle={toggleSection}>
        <CampaignModels cid={cid} />
        <Link className="subtle" to={`/campaigns/${cid}/settings`}>Campaign settings</Link>
      </SideSection>

      {/* Standing instructions placed in the history at a depth (play controls
          V). Collapsed by default, like Models: set once, then left alone.
          The count is every note due on the next turn. */}
      <SideSection id="authors_notes" title="Author's notes"
                   collapsed={collapsed.authors_notes ?? true} onToggle={toggleSection}
                   extra={nextNotes && nextNotes.count > 0
                     ? <span className="chip on" aria-label="Notes applying next turn">
                         {nextNotes.count}</span>
                     : undefined}>
        <AuthorsNotesPanel cid={cid} sid={sid} cast={cast} next={nextNotes}
                           onSaved={onNotesSaved} />
      </SideSection>

      {/* Authorial metadata, not a transcript/run mutation: editing remains
          available during generation and affects future compositions only.
          Refresh only live context, including comparisons against it. */}
      <SideSection id="scene_focus" title="Scene focus"
                   collapsed={collapsed.scene_focus ?? true} onToggle={toggleSection}>
        <SceneFocusEditor cid={cid} sid={sid} onSaved={onFocusSaved} />
      </SideSection>

      <WhenSection cid={cid} sid={sid} sceneLocked={sceneLocked} date={moment.date} {...sections} />
      {/* Campaign-scoped, next to the scene's own When: advancing the clock is
          the same question one level up, and this is where a reader already
          comes to ask it (#100). Keyed on `refreshKey` so setting this scene's
          date — which carries the clock forward with it — shows up here too, and
          `onAdvanced` reloads When because a dateless scene takes its date
          pre-fill from the clock this just moved.
          Collapsed by default: this is the one section about the campaign rather
          than the scene, so it should not push the scene's own state down the
          rail until a reader asks for it. */}
      <SideSection id="clock" title="Campaign clock" collapsed={collapsed.clock ?? true}
                   onToggle={toggleSection}>
        <ClockPanel cid={cid} refreshKey={refreshKey} onAdvanced={() => { void moment.date.reloadWhen(); }} />
      </SideSection>

      {/* The clock's other half (#101): events are authored here and fired by
          the panel above — or by this scene taking a date past one. Keyed on the
          same `refreshKey` for that reason. Collapsed by default, like the clock
          it belongs to. */}
      <SideSection id="events" title="Scheduled events" collapsed={collapsed.events ?? true}
                   onToggle={toggleSection}>
        <EventsPanel cid={cid} refreshKey={refreshKey} />
      </SideSection>

      <SideSection id="weather" title="Weather" collapsed={!!collapsed.weather} onToggle={toggleSection}>
        {/* Renders nothing when there is no location or moment yet, the same
            way the When and Location sections above it degrade. */}
        <WeatherWidget cid={cid} sid={sid} refreshKey={refreshKey} />
      </SideSection>

      <ContextSection cid={cid} history={history} {...sections} />
      {/* Directly under Context, and that adjacency is the point (#153): the
          section above is what grimoire COMPOSED, measured by a local
          tokenizer; this is what the provider said it charged for sending it.
          Collapsed by default — cost is a question a reader comes to ask, not
          one worth pushing the scene's own state down the rail for. */}
      <SideSection id="cost" title="Cost" collapsed={collapsed.cost ?? true}
                   onToggle={toggleSection}>
        <CostPanel cid={cid} sid={sid} refreshKey={refreshKey}
                   usage={usage} budget={budget} onBudgetSaved={onBudgetSaved} />
      </SideSection>

      <TurnHistorySection history={history} {...sections}>
        <RewriteHistory cid={cid} sid={sid} rewritten={rewritten} sceneLocked={sceneLocked}
          history={rewriteHistory} />
      </TurnHistorySection>
      <DecisionsSection history={history} {...sections} />
      {drawer && <RecordDrawer cid={cid} sid={sid} target={drawer} onClose={() => setDrawer(null)} />}
    </aside>
  );
});
