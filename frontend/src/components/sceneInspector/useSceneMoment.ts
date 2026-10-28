import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { api, type CalendarConfig, type SceneDatetime, type SceneLocation } from "../../api/client";
import { errorText } from "../../api/errors";
import { onNoticesChanged } from "../../appEvents";
import type { InspectorInputs } from "./types";

export function useSceneMoment({ cid, sid, refreshKey, onSceneChanged, onSceneRenamed, onRenaming,
  onTransition: askRolling, onError: setError }:
  InspectorInputs<"cid" | "sid" | "refreshKey" | "onSceneChanged" | "onSceneRenamed" | "onRenaming"> & {
    onTransition: (id?: string) => void; onError: (error: string | null) => void }) {
  const key = `${cid}/${sid}`;
  const owner = useRef(key);
  const campaign = useRef(cid);
  useLayoutEffect(() => {
    owner.current = key; campaign.current = cid;
    return () => { owner.current = ""; campaign.current = ""; };
  }, [key, cid]);
  const [setting, setSetting] = useState<SceneLocation | null>(null);
  const [locImages, setLocImages] = useState<string[]>([]);
  const [cfg, setCfg] = useState<CalendarConfig | null>(null);
  const [when, setWhen] = useState<SceneDatetime | null>(null);
  const [provider, setProvider] = useState("gregorian");
  const [calendars, setCalendars] = useState<{ id: string; name: string }[]>([]);
  const [dateInput, setDateInput] = useState("");
  const [locations, setLocations] = useState<{ id: string; name: string }[]>([]);
  const [locPick, setLocPick] = useState("");

  // Which datetime read is the newest one asked for. `fresh` and this are two
  // different guarantees and BOTH are needed: `fresh` stops a new read joining
  // an in-flight GET of the same path, but it does not cancel the older
  // promise, whose `.then` is already attached and will still call `setWhen`
  // when it lands. Arriving late, that older payload replaces the fresh one --
  // and since the banner's optimistic dismissal is component state, collapsing
  // and reopening the When section remounts it and shows the acknowledged
  // warning again (#106). Only the newest read is allowed to apply.
  const whenGen = useRef(0);

  // Clear `when` the moment the campaign or scene changes, before the reload
  // below has settled. Until it does, this holds the PREVIOUS scene's payload
  // -- and the banner reading its notices would be rendering them beside the
  // new `cid`, so a dismissal in that window writes the old campaign's
  // occurrence key into the new campaign's ledger, silencing a warning that
  // campaign never showed (#106). Adjusted during render rather than from an
  // effect, which is what makes it happen BEFORE the stale list can paint --
  // the same reason `NewSceneChooser` clears its own list this way.
  const [seenScene, setSeenScene] = useState(`${cid}/${sid}`);
  if (seenScene !== `${cid}/${sid}`) {
    setSeenScene(`${cid}/${sid}`);
    setWhen(null);
    setDateInput(""); setProvider("gregorian");
    // And retire whatever is in flight. Clearing `when` alone is not enough:
    // a read issued for the PREVIOUS scene still holds the current generation,
    // so it would pass the check below and put that scene's payload back under
    // the new ids -- the very state this clear exists to prevent. The two
    // guards only compose if the reset advances the generation too.
    whenGen.current += 1;
  }
  const reloadWhen = useCallback(
    (opts?: { fresh?: boolean }) => {
      // The campaign clock can retain this callback across a same-campaign
      // scene switch. Retire it before it can invalidate the new scene's read.
      if (owner.current !== `${cid}/${sid}`) return Promise.resolve();
      const mine = ++whenGen.current;
      return api.getSceneDatetime(cid, sid, opts).then((w) => {
        if (mine !== whenGen.current || owner.current !== `${cid}/${sid}`) return;
        setWhen(w);
        // dateless scene with a suggestion: pre-fill the input, but never clobber typing
        const suggested = w.suggested;
        if (!w.current && suggested) setDateInput((prev) => prev || suggested);
      }).catch(() => { if (mine === whenGen.current && owner.current === `${cid}/${sid}`) setWhen(null); });
    },
    [cid, sid]);

  // Any surface acknowledging a notice makes this panel's copy of the datetime
  // payload one write out of date -- including the new-scene chooser, which
  // CampaignView can have overlaid on this very inspector (`appEvents`).
  useEffect(() => onNoticesChanged(() => { void reloadWhen({ fresh: true }); }), [reloadWhen]);

  // Campaign catalogs and scene reads have independent generations. Neither a
  // late location image nor a prior campaign's calendar may replace this one.
  const cfgRead = useRef(0);
  const locationRead = useRef(0);
  const [campaignSeen, setCampaignSeen] = useState(cid);
  if (campaignSeen !== cid) {
    setCampaignSeen(cid); setCfg(null); setCalendars([]); setLocations([]); setLocPick("");
  }
  const [settingOwner, setSettingOwner] = useState(key);
  if (settingOwner !== key) { setSettingOwner(key); setSetting(null); setLocImages([]); }
  const reloadCfg = useCallback(async () => {
    if (campaign.current !== cid) return;
    const token = ++cfgRead.current;
    try {
      const result = await api.getCalendarConfig({ kind: "campaign", id: cid });
      if (campaign.current === cid && cfgRead.current === token) setCfg(result);
    } catch {
      if (campaign.current === cid && cfgRead.current === token) setCfg(null);
    }
  }, [cid]);
  const reloadLocation = useCallback(async () => {
    if (owner.current !== `${cid}/${sid}`) return;
    const token = ++locationRead.current;
    try {
      const result = await api.getSceneLocation(cid, sid);
      if (owner.current === `${cid}/${sid}` && locationRead.current === token) setSetting(result);
    } catch {
      if (owner.current === `${cid}/${sid}` && locationRead.current === token) setSetting(null);
    }
  }, [cid, sid]);
  useEffect(() => {
    let live = true;
    api.listEntities({ kind: "campaign", id: cid }, "locations")
      .then((ls) => { if (live) setLocations(ls.map((l) => ({ id: l.id, name: l.name }))); })
      .catch(() => { if (live) setLocations([]); });
    api.getCalendarProviders().then((r) => { if (live) setCalendars(r.providers); })
      .catch(() => { if (live) setCalendars([]); });
    return () => { live = false; };
  }, [cid]);
  useEffect(() => {
    void reloadWhen(); void reloadCfg(); void reloadLocation();
    return () => { whenGen.current += 1; cfgRead.current += 1; locationRead.current += 1; };
  }, [refreshKey, reloadWhen, reloadCfg, reloadLocation]);
  // the location section shows the primary image when the store has one
  useEffect(() => {
    let live = true;
    const loc = setting?.current;
    if (!loc) { setLocImages([]); return; }
    api.listEntityImages({ kind: "campaign", id: cid }, "locations", loc.id)
      .then((imgs) => { if (live) setLocImages(imgs.map((i) => i.name)); })
      .catch(() => { if (live) setLocImages([]); });
    return () => { live = false; };
  }, [cid, setting]);

  async function chooseCalendar() {
    if (!cfg) return;
    setError(null);
    try {
      await api.setCalendarConfig({ kind: "campaign", id: cid }, {
        ...cfg, primary: { ...cfg.primary, provider }, confirmed: true });
      if (owner.current !== key) return;
      await reloadCfg();
    } catch (err: unknown) {
      if (owner.current === key) setError(errorText(err));
    }
  }

  async function moveTo() {
    if (!locPick) return;
    setError(null);
    try {
      await api.setSceneLocation(cid, sid, locPick);
      if (owner.current !== key) return;
      setLocPick("");
      await reloadLocation();
      if (owner.current !== key) return;
      onSceneChanged(); // surface the location-transition line in the stream
      askRolling();     // ...which is a post, and may be the Nth (#85)
    } catch (err: unknown) {
      if (owner.current === key) setError(errorText(err));
    }
  }

  async function applyDatetime() {
    if (!dateInput) return;
    setError(null);
    onRenaming?.(true);      // the first date set re-slugs the file
    try {
      const res = await api.setSceneDatetime(cid, sid, dateInput);
      if (owner.current !== key) return;
      setDateInput("");
      if (res.id !== sid) {
        // first date set renames the scene file — adopt the new id; the sid
        // prop change re-runs every load effect, so skip the stale reload
        onSceneRenamed?.(res.id);
        // ...but those effects only READ, and a read never evaluates the gate.
        // The first date is set SILENTLY (no "Time passes…" line, which is why
        // this branch exists at all), so nothing else about this write asks —
        // and it still moves the scene's date FACT, which is part of the fold's
        // validity key. Ask against the NEW id: the old one no longer names a
        // file. Adopted first, so `currentKey` has moved by the time the answer
        // lands and the panel accepts it (#85).
        askRolling(res.id);
        return;
      }
      await reloadWhen();
      if (owner.current !== key) return;
      onSceneChanged();  // surface the "Time passes…" transition line in the stream
      askRolling();      // ...which is a post, and may be the Nth (#85)
    } catch (err: unknown) {
      if (owner.current === key) setError(errorText(err));
    } finally {
      onRenaming?.(false);
    }

  }

  return {
    date: { cfg, when, provider, calendars, dateInput, setProvider, setDateInput, chooseCalendar, applyDatetime, reloadWhen },
    location: { setting, locImages, locations, locPick, setLocPick, moveTo },
  };
}
export type SceneMoment = ReturnType<typeof useSceneMoment>;
