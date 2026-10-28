import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { api, type Actor, type CharacterSummary, type PCSummary, type RosterEntry } from "../../api/client";
import { errorText } from "../../api/errors";
import type { InspectorInputs } from "./types";

export function useInspectorCast({ cid, sid, refreshKey, pcless, onSceneChanged, onTransition, onError }:
  InspectorInputs<"cid" | "sid" | "refreshKey" | "pcless" | "onSceneChanged"> & {
    onTransition: (id?: string) => void; onError: (error: string | null) => void;
  }) {
  const key = `${cid}/${sid}`;
  const owner = useRef(key);
  const read = useRef(0);
  useLayoutEffect(() => {
    owner.current = key;
    return () => { owner.current = ""; };
  }, [key]);
  const [castState, setCast] = useState<{ cid: string; key: string; rows: Actor[] } | null>(null);
  const [catalog, setCatalog] = useState<{ cid: string; chars: CharacterSummary[]; pcs: PCSummary[] } | null>(null);
  const [rosterState, setRoster] = useState<{ cid: string; rows: RosterEntry[] } | null>(null);
  // Preserve the existing same-campaign row display while the next scene is
  // loading; pin state is separately scene-stamped. Cross-campaign rows never
  // render, and every read/mutation still installs only for its current scene.
  const cast = castState?.cid === cid ? castState.rows : [];
  const chars = useMemo(() => catalog?.cid === cid ? catalog.chars : [], [catalog, cid]);
  const pcs = useMemo(() => catalog?.cid === cid ? catalog.pcs : [], [catalog, cid]);
  const roster = rosterState?.cid === cid ? rosterState.rows : [];
  const names = useMemo(() => {
    const result: Record<string, string> = {};
    for (const x of chars) result[`characters/${x.id}`] = x.name;
    for (const x of pcs) result[`pcs/${x.id}`] = x.name;
    return result;
  }, [chars, pcs]);
  // Names are display niceties. Independent failures leave the other catalog
  // usable; ids remain the fallback, and no rejected names promise escapes.
  useEffect(() => {
    let live = true;
    Promise.all([
      api.listCharacters({ kind: "campaign", id: cid }).catch(() => []),
      api.listCampaignPCs(cid).catch(() => []),
    ]).then(([chars, pcs]) => { if (live) setCatalog({ cid, chars, pcs }); })
      .catch(() => { if (live) setCatalog({ cid, chars: [], pcs: [] }); });
    return () => { live = false; };
  }, [cid]);
  const reloadCast = useCallback(async () => {
    if (owner.current !== `${cid}/${sid}`) return;
    const token = ++read.current;
    try {
      const rows = await api.getCast(cid, sid);
      if (owner.current === `${cid}/${sid}` && read.current === token) setCast({ cid, key: `${cid}/${sid}`, rows });
    } catch {
      if (owner.current === `${cid}/${sid}` && read.current === token) setCast({ cid, key: `${cid}/${sid}`, rows: [] });
    }
  }, [cid, sid]);
  useEffect(() => {
    void reloadCast();
    let live = true;
    api.listAppearances(cid).then((rows) => { if (live) setRoster({ cid, rows }); })
      .catch(() => { if (live) setRoster({ cid, rows: [] }); });
    return () => { live = false; read.current += 1; };
  }, [cid, refreshKey, reloadCast]);
  async function addMember(kind: "characters" | "pcs", id: string, role: "player" | "npc") {
    if (!id) return false;
    onError(null);
    try {
      await api.addToCast(cid, sid, { kind, id, role: pcless ? "npc" : kind === "pcs" ? "player" : role });
      if (owner.current !== key) return false;
      await reloadCast();
      if (owner.current !== key) return false;
      onSceneChanged();
      onTransition(); // a join/leave appends a transition post (#85)
      return true;
    } catch (err: unknown) {
      if (owner.current === key) onError(errorText(err));
      return false;
    }
  }
  async function removeCastMember(a: Actor) {
    onError(null);
    try {
      await api.removeFromCast(cid, sid, a.kind, a.id);
      if (owner.current !== key) return;
      await reloadCast();
      if (owner.current !== key) return;
      onSceneChanged();
      onTransition(); // a join/leave appends a transition post (#85)
    } catch (err: unknown) {
      if (owner.current === key) onError(errorText(err));
    }
  }
  function suggestedCastAdded() {
    if (owner.current !== key) return;
    void reloadCast();
    onSceneChanged();
    onTransition(); // a join appends a transition post (#85)
  }
  const nameOf = (a: Actor) => names[`${a.kind}/${a.id}`] ?? a.id;
  return { cast, chars, pcs, roster, names, nameOf, addMember, removeCastMember, suggestedCastAdded };
}
export type InspectorCast = ReturnType<typeof useInspectorCast>;
