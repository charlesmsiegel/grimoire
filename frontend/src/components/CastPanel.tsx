import { useCallback, useEffect, useState } from "react";
import {
  api, type Actor, type CharacterSummary, type EntitySummary,
  type PCSummary, type RosterEntry, type SceneLocation, type SceneDatetime,
} from "../api/client";
import { CalendarDatePicker } from "./CalendarDatePicker";
import { LOCKED_WHILE_GENERATING } from "./sceneLock";

export function CastPanel({
  cid, sid, ready, onSeeded, onSceneRenamed, initialPrompt, pcless, sceneLocked,
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
}) {
  const [cast, setCast] = useState<Actor[]>([]);
  const [chars, setChars] = useState<CharacterSummary[]>([]);
  const [pcs, setPCs] = useState<PCSummary[]>([]);
  const [roster, setRoster] = useState<RosterEntry[]>([]);
  const [locations, setLocations] = useState<EntitySummary[]>([]);
  const [setting, setSetting] = useState<SceneLocation | null>(null);
  const [locId, setLocId] = useState("");
  const [when, setWhen] = useState<SceneDatetime | null>(null);
  const [dateInput, setDateInput] = useState("");
  const [error, setError] = useState<string | null>(null);

  const [kind, setKind] = useState<"characters" | "pcs">("characters");
  const [actorId, setActorId] = useState("");
  const [role, setRole] = useState<"player" | "npc">("npc");

  const [prompt, setPrompt] = useState("");
  const [opener, setOpener] = useState("");
  const [busy, setBusy] = useState(false);

  const reloadCast = useCallback(() => api.getCast(cid, sid).then(setCast), [cid, sid]);
  const reloadSetting = useCallback(
    () => api.getSceneLocation(cid, sid).then(setSetting).catch(() => setSetting(null)),
    [cid, sid]);
  const reloadWhen = useCallback(
    () => api.getSceneDatetime(cid, sid).then((w) => {
      setWhen(w);
      // dateless scene with a suggestion: pre-fill the input, but never clobber typing
      if (!w.current && w.suggested) setDateInput((prev) => prev || w.suggested!);
    }).catch(() => setWhen(null)),
    [cid, sid]);

  useEffect(() => {
    reloadCast();
    api.listAppearances(cid).then(setRoster).catch(() => setRoster([]));
    reloadSetting();
    reloadWhen();
  }, [cid, sid, reloadCast, reloadSetting, reloadWhen]);

  // seed from the chooser's premise; reset on scene switch so a prior
  // scene's premise never lingers in another scene's opener box
  useEffect(() => {
    setPrompt(initialPrompt ?? "");
  }, [sid, initialPrompt]);

  // characters/pcs available to add: the campaign copy holds every actor
  useEffect(() => {
    api.listCharacters({ kind: "campaign", id: cid }).then(setChars);
    api.listCampaignPCs(cid).then(setPCs);
    api.listEntities({ kind: "campaign", id: cid }, "locations").then(setLocations).catch(() => setLocations([]));
  }, [cid]);

  const options = kind === "characters" ? chars : pcs;

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
      setError(err.detail ?? String(err));
    }
  }

  async function setLocation() {
    if (!locId) return;
    setError(null);
    try {
      await api.setSceneLocation(cid, sid, locId);
      setLocId("");
      await reloadSetting();
      onSeeded(); // refresh the stream so the transition line shows
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  async function applyDatetime() {
    if (!dateInput) return;
    setError(null);
    try {
      const res = await api.setSceneDatetime(cid, sid, dateInput);
      setDateInput("");
      if (res.id !== sid) {
        // first date set renames the scene file — adopt the new id; the sid
        // prop change re-runs every load effect, so skip the stale reload
        onSceneRenamed?.(res.id);
        return;
      }
      await reloadWhen();
      onSeeded(); // surface the transition line in the stream
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  async function generate() {
    if (!prompt.trim() || busy) return;
    setError(null);
    setOpener("");
    setBusy(true);
    let acc = "";
    try {
      await api.opener(cid, sid, prompt, (e) => {
        if (e.delta) {
          acc += e.delta;
          setOpener(acc);
        } else if (e.error) {
          setError(e.error.detail);
        }
      });
    } catch (err: any) {
      setError(err.detail ?? String(err));
    } finally {
      setBusy(false);
    }
  }

  async function useOpener() {
    if (!opener.trim() || busy) return;
    setError(null);
    try {
      await api.firstPost(cid, sid, opener);
      setOpener("");
      onSeeded(); // the adopted opener now shows as the scene's first post
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  async function saveOpenerAsGreeting() {
    if (!opener.trim() || kind !== "characters" || !actorId) return;
    const character = chars.find((c) => c.id === actorId);
    if (!character) return;
    const name = window.prompt("Name this greeting?", "Opener")?.trim();
    if (!name) return;
    // an opener saved as a greeting belongs to the campaign, not the world baseline
    await api.createGreeting({ kind: "campaign", id: cid }, {
      name, character: actorId, version: character.default_version, body: opener,
    });
    setOpener("");
  }

  return (
    <details className="cast-panel" open>
      <summary>Cast &amp; scene setup</summary>
      <div className="panel-body">
        {error && <div className="banner">{error}</div>}

        <div>
          <div className="role">Setting</div>
          <div className="field-hint">{setting?.current ? setting.current.name : "No setting"}</div>
          <div className="picker">
            <select aria-label="Location" value={locId} onChange={(e) => setLocId(e.target.value)}>
              <option value="">— pick —</option>
              {locations.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
            </select>
            <button className="primary" onClick={setLocation}
                    disabled={!locId || locId === setting?.current?.id}>
              {setting?.current ? "Move here" : "Set location"}
            </button>
          </div>
        </div>

        <div>
          <div className="role">When</div>
          <div className="field-hint">
            {when?.current
              ? `${when.current.friendly} (${when.current.weekday})`
              : "No date"}
          </div>
          {when?.current?.holidays_today?.length ? (
            <div className="field-hint">Holidays: {when.current.holidays_today.join(", ")}</div>
          ) : null}
          <div className="picker">
            <CalendarDatePicker scope={{ kind: "campaign", id: cid }} value={dateInput}
                                onChange={setDateInput} ariaLabel="Scene date" />
            {/* The first date set renames the scene file, so this is a rename
                control in disguise — locked for the same reason the rail's is. */}
            <button className="primary" onClick={applyDatetime}
                    disabled={!dateInput || sceneLocked}
                    title={sceneLocked ? LOCKED_WHILE_GENERATING : undefined}>
              {when?.current ? "Advance to" : "Set date"}
            </button>
          </div>
        </div>

        <div>
          <div className="role">In this scene</div>
          {cast.length === 0 && <div className="field-hint">No one cast yet.</div>}
          {cast.map((a) => {
            const ver = a.kind === "characters"
              ? roster.find((r) => r.kind === "characters" && r.id === a.id)?.version
              : undefined;
            return (
              <div className="cast-row" key={`${a.kind}/${a.id}`}>
                {ver
                  ? <img className="row-avatar" alt={`${a.id} avatar`}
                         src={api.campaignImageUrl(cid, a.id, ver, "avatar")}
                         onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = "none"; }} />
                  : null}
                <span>{a.id}</span>
                <span className="role">{a.kind === "pcs" ? "PC" : "character"} · {a.role}</span>
              </div>
            );
          })}
        </div>

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

        <div>
          <div className="role">Generate an opener</div>
          {!ready && <div className="field-hint">Set up an LLM connection in Config to generate.</div>}
          <div className="picker">
            <input type="text" aria-label="Opener prompt" placeholder="A storm over the salt marshes…"
                   value={prompt} onChange={(e) => setPrompt(e.target.value)} />
            <button className="primary" onClick={generate} disabled={!ready || busy || !prompt.trim()}>
              {busy ? "…" : "Generate"}
            </button>
          </div>
          {opener && (
            <>
              <div className="opener-preview">{opener}</div>
              <div className="form-actions">
                <button className="primary" onClick={useOpener} disabled={busy}>Use</button>
                <button className="subtle" onClick={saveOpenerAsGreeting}
                        disabled={kind !== "characters" || !actorId}
                        title={kind !== "characters" || !actorId ? "Pick a character above to attach the saved greeting" : ""}>
                  Save as greeting
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </details>
  );
}
