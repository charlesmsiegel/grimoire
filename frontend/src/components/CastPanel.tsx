import { useCallback, useEffect, useState } from "react";
import { api, type Actor, type CharacterSummary, type PCSummary, type RosterEntry } from "../api/client";
import { errMsg } from "./errMsg";
import { OpenerComposer } from "./OpenerComposer";
import { SceneCastList } from "./SceneCastList";
import { SceneDateField } from "./SceneDateField";
import { SceneSettingField } from "./SceneSettingField";
import { SuggestedCast } from "./SuggestedCast";

/** Set an empty scene up: where and when it happens, who is in it, and an
 *  opener to start it with.
 *
 *  Each row owns its own load/save (`SceneSettingField`, `SceneDateField`,
 *  `SuggestedCast`, `OpenerComposer`); what stays here is the state two of them
 *  share — the cast, and the actor the picker has selected, which is also the
 *  character an opener can be saved against. Errors funnel into one banner so
 *  the panel never grows a second place to look. */
export function CastPanel({
  cid, sid, ready, onSeeded, onSceneRenamed, initialPrompt, pcless, sceneLocked,
  onRenaming,
}: {
  cid: string;
  sid: string;
  ready: boolean;
  onSeeded: () => void;
  onSceneRenamed?: (id: string) => void;
  initialPrompt?: string;
  pcless?: boolean;
  /** A turn is streaming into this scene, so anything that can rename its file
   *  has to wait: the id is the filename, and moving it mid-turn strands the
   *  abort write that saves the partial (#95). */
  sceneLocked?: boolean;
  /** Reports a scene-renaming request in and out of flight. The parent blocks
   *  new turns while one is pending: until the PUT answers, the scene's id is
   *  in doubt, and a turn handed the old one writes nowhere (#95). */
  onRenaming?: (active: boolean) => void;
}) {
  const [cast, setCast] = useState<Actor[]>([]);
  const [chars, setChars] = useState<CharacterSummary[]>([]);
  const [pcs, setPCs] = useState<PCSummary[]>([]);
  const [roster, setRoster] = useState<RosterEntry[]>([]);
  const [error, setError] = useState<string | null>(null);

  const [kind, setKind] = useState<"characters" | "pcs">("characters");
  const [actorId, setActorId] = useState("");
  const [role, setRole] = useState<"player" | "npc">("npc");

  const reloadCast = useCallback(() => api.getCast(cid, sid).then(setCast), [cid, sid]);

  useEffect(() => {
    reloadCast();
    api.listAppearances(cid).then(setRoster).catch(() => setRoster([]));
  }, [cid, sid, reloadCast]);

  // characters/pcs available to add: the campaign copy holds every actor
  useEffect(() => {
    api.listCharacters({ kind: "campaign", id: cid }).then(setChars);
    api.listCampaignPCs(cid).then(setPCs);
  }, [cid]);

  const options = kind === "characters" ? chars : pcs;
  const selected = kind === "characters" ? chars.find((c) => c.id === actorId) ?? null : null;
  const nameOf = useCallback(
    (id: string) => chars.find((c) => c.id === id)?.name ?? id, [chars]);

  async function add() {
    if (!actorId) return;
    setError(null);
    try {
      await api.addToCast(cid, sid, {
        kind, id: actorId,
        role: pcless ? "npc" : kind === "pcs" ? "player" : role,
      });
      setActorId("");
      await reloadCast();
    } catch (err: any) {
      setError(errMsg(err));
    }
  }

  return (
    <details className="cast-panel" open>
      <summary>Cast &amp; scene setup</summary>
      <div className="panel-body">
        {error && <div className="banner">{error}</div>}

        <SceneSettingField cid={cid} sid={sid} onMoved={onSeeded} onError={setError} />

        <SceneDateField cid={cid} sid={sid} sceneLocked={sceneLocked} onAdvanced={onSeeded}
                        onRenamed={onSceneRenamed} onRenaming={onRenaming} onError={setError} />

        <SceneCastList cid={cid} cast={cast} roster={roster} />

        <div>
          <div className="role">Add to scene</div>
          <div className="picker">
            {!pcless && (
              <select aria-label="Actor kind" value={kind}
                      onChange={(e) => { setKind(e.target.value as "characters" | "pcs"); setActorId(""); }}>
                <option value="characters">Character</option>
                <option value="pcs">PC</option>
              </select>
            )}
            <select aria-label="Actor" value={actorId} onChange={(e) => setActorId(e.target.value)}>
              <option value="">— pick —</option>
              {options.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
            </select>
            {kind === "characters" && !pcless && (
              <select aria-label="Role" value={role} onChange={(e) => setRole(e.target.value as "player" | "npc")}>
                <option value="npc">npc</option>
                <option value="player">player</option>
              </select>
            )}
            <button className="primary" onClick={add} disabled={!actorId}>Add</button>
          </div>
        </div>

        {/* Directly under the picker it is an alternative to. The scan reads
            the cards of whoever is already seated, so it moves as the cast does
            (hence the reload key) and has nothing to say about an empty one —
            which is also why an empty cast is not worth a request. */}
        {cast.length > 0 && (
          <SuggestedCast cid={cid} sid={sid} nameOf={nameOf} refreshKey={cast.length}
                         onCast={reloadCast} />
        )}

        <OpenerComposer cid={cid} sid={sid} ready={ready} initialPrompt={initialPrompt}
                        character={selected} onSeeded={onSeeded} onError={setError} />
      </div>
    </details>
  );
}
