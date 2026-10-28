import { useEffect, useRef, useState } from "react";
import type { Actor, GroupSettings } from "../../api/client";
import {
  api,
  type Briefing, type Casefile,
  type Provenance,
  type RosterEntry,
  type SceneDatetime,
  type SceneLocation, type SceneWeather
} from "../../api/client";
import { useStableCallback } from "./campaignShared";
import type { SceneKey } from "./campaignTypes";

export function useSceneContinuity({ cid, activeId, ctxKey, readScene, fail, currentCampaign, selected }: {
  currentCampaign: () => string;
  selected: () => SceneKey | null;
  cid: string;
  activeId: string | null;
  ctxKey: number;
  readScene: (scope: SceneKey) => void;
  fail: (e: unknown, retryable?: boolean, from?: string) => void;
}) {
  const [dt, setDt] = useState<SceneDatetime | null>(null);
  const [cast, setCast] = useState<Actor[]>([]);
  // Group-play settings of the active scene; null until read, and after a failed read.
  const [group, setGroup] = useState<GroupSettings | null>(null);
  // Which scene `group` was read for, and which read is the newest. A read that
  // resolves after the scene changed, or after the panel saved, is stale.
  const groupScopeRef = useRef<{ cid: string; sid: string | null }>({ cid: "", sid: null });
  const groupReadRef = useRef(0);
  const [roster, setRoster] = useState<RosterEntry[]>([]);
  /** The whole dossier feature. `null` is the cast grid; a ref is one actor's
   *  casefile in the column. Deliberately the only state the swap has —
   *  "which mode is the column in" is derived from this and never stored
   *  twice, so the two can never disagree. */
  const [selectedActor, setSelectedActor] = useState<{ kind: string; id: string } | null>(null);
  const [casefile, setCasefile] = useState<Casefile | null>(null);
  const [briefing, setBriefing] = useState<Briefing | null>(null);
  /** Why each stored line is there (#4a). Campaign-scoped and read once per
   *  absorb rather than per scene: it is a rolling log of citations for values
   *  already on screen, so a copy one absorb old is stale about a line the
   *  reader has not been shown yet either. `{}` is a normal state — a campaign
   *  absorbed before the store existed has no citations at all. */
  const [provenance, setProvenance] = useState<Provenance>({});
  /** The quote the reader is hovering a citation for, highlighted in the
   *  transcript. "" is nothing hovered. */
  const [citedQuote, setCitedQuote] = useState("");
  const [sceneLocation, setSceneLocation] = useState<SceneLocation | null>(null);
  const [weather, setWeather] = useState<SceneWeather | null>(null);

  /** Drop the group settings read for another scene (or campaign). Called
   *  wherever the active scene changes, so the chips and the panel never show
   *  the previous scene's settings while this one's read is out. */
  function forgetGroup(sid: string | null) {
    groupScopeRef.current = { cid, sid };
    groupReadRef.current += 1;
    setGroup(null);
  }

  // ---- the context column's cast/dossier swap (2a) ----

  /** Open one actor in the column. Deliberately does NOT touch the transcript,
   *  the composer or focus: this is a column state, not a modal, and the whole
   *  claim of the design is that reading a dossier costs you nothing you were
   *  holding. */
  const openActor = useStableCallback((kind: string, id: string) => {
    setSelectedActor({ kind, id });
  });

  function closeActor() {
    setSelectedActor(null);
  }

  useEffect(() => {
    if (!selectedActor || !activeId) { setCasefile(null); return; }
    // Cleared first so the column shows its reading state rather than the
    // previous actor's dossier under the new one's name -- these are two
    // people's private states, and briefly attributing one to the other is the
    // one failure this panel must not have.
    setCasefile(null);
    let live = true;
    api.getCasefile(cid, activeId, selectedActor.kind, selectedActor.id)
      .then((c) => { if (live) setCasefile(c); })
      // An actor whose record cannot be read is one this column cannot
      // describe; going back to the cast is the honest outcome, and the grid
      // still shows she is there. Every route in here names someone in the
      // scene's cast -- a tile, or a transcript plate, which is only a button
      // when its speaker resolves against that same cast -- so the grid the
      // fallback lands on is one she is in.
      .catch(() => { if (live) setSelectedActor(null); });
    return () => { live = false; };
    // `ctxKey` bumps on every successful absorb save, which is exactly when
    // every file behind this panel was rewritten.
  }, [cid, activeId, selectedActor, ctxKey]);

  async function removeSelectedActor() {
    if (!selectedActor || !activeId) return;
    const scope: SceneKey = { cid, sid: activeId };
    const actor = selectedActor;
    const stillHere = () => currentCampaign() === scope.cid
      && selected()?.cid === scope.cid && selected()?.sid === scope.sid;
    try {
      await api.removeFromCast(scope.cid, scope.sid, actor.kind, actor.id);
      // The mutation belongs to its initiating scene. Its completion must not
      // close another scene's dossier or start that scene's secondary reads.
      if (!stillHere()) return;
      setSelectedActor(null);
      // The removal landed: a cast that will not re-read is a stale panel until
      // the next read, not a failed removal. Its response can outlive this scene
      // too, so acceptance needs the same guard as issuing the read.
      api.getCast(scope.cid, scope.sid)
        .then((next) => { if (stillHere()) setCast(next); }).catch(() => { });
      // A join/leave appends a transition post (#85), so the transcript moved.
      readScene(scope);
    } catch (err: unknown) {
      if (stillHere()) fail(err, false);
    }
  }
  function refreshContinuity(cid: string, id: string, renamed: boolean) {
    api.getSceneDatetime(cid, id).then(setDt).catch(() => setDt(null));
    api.getCast(cid, id).then(setCast).catch(() => setCast([]));
    api.listAppearances(cid).then(setRoster).catch(() => setRoster([]));
    // The context column's own reads. Each settles independently and each
    // empties only its own block on failure -- the column is continuity, and a
    // weather request that 500s must not take the cast down with it.
    //
    // The dossier is deliberately NOT re-read here: it is keyed to
    // `selectedActor`, which selecting a scene clears, so a stale casefile
    // cannot outlive the scene it was opened from.
    // Started inside a promise so a read that throws *synchronously* empties
    // its own block like one that rejects, rather than taking the whole scene
    // selection down with it — this runs before the transcript is fetched.
    const columnRead = <T,>(read: () => Promise<T>, set: (v: T | null) => void): void => {
      Promise.resolve().then(read).then(set).catch(() => set(null));
    };
    columnRead(() => api.sceneBriefing(cid, id), setBriefing);
    Promise.resolve().then(() => api.campaignProvenance(cid))
      .then((p) => setProvenance(p ?? {}))
      // Uncited rows are a normal state, so a failed read degrades to exactly
      // that rather than to an error the reader can do nothing about.
      .catch(() => setProvenance({}));
    columnRead(() => api.getSceneLocation(cid, id), setSceneLocation);
    columnRead(() => api.getSceneWeather(cid, id), setWeather);
    // Failed read: nobody is treated as sitting out, and the chips still work.
    //
    // Reset on a switch of scene or campaign (a rename keeps the scene, so it
    // keeps its settings), and a response is applied only while it is still the
    // newest read for the scene on screen.
    const groupScope = groupScopeRef.current;
    if (groupScope.cid !== cid || (groupScope.sid !== id && !renamed)) forgetGroup(id);
    else groupScopeRef.current = { cid, sid: id };
    const groupRead = ++groupReadRef.current;
    const groupCurrent = () => groupReadRef.current === groupRead
      && currentCampaign() === cid && (selected()?.sid ?? null) === id;
    Promise.resolve(api.getSceneGroup?.(cid, id))
      .then((g) => { if (groupCurrent()) setGroup(g ?? null); })
      // A failed read of the scene already on screen keeps what it has; a
      // scene just switched to has nothing yet, which is the same as no settings.
      .catch(() => { });
  }
  function groupSaved(scope: SceneKey, next: GroupSettings) {
    if (currentCampaign() !== scope.cid || selected()?.sid !== scope.sid) return;
    groupReadRef.current += 1;
    setGroup(next);
  }
  return {
    refreshContinuity, forgetGroup, cast, setSelectedActor, groupSaved, setCitedQuote, selectedActor,
    openActor, briefing, citedQuote, roster, closeActor, removeSelectedActor, casefile, provenance,
    sceneLocation, dt, weather, group,
  };
}
