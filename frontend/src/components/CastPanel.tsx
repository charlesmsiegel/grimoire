import { useCallback, useEffect, useState } from "react";
import {
  api, type Actor, type Availability, type CharacterSummary, type PCSummary,
} from "../api/client";

export function CastPanel({
  cid, sid, sceneEmpty, keySet, onSeeded,
}: {
  cid: string;
  sid: string;
  sceneEmpty: boolean;
  keySet: boolean;
  onSeeded: () => void;
}) {
  const [cast, setCast] = useState<Actor[]>([]);
  const [chars, setChars] = useState<CharacterSummary[]>([]);
  const [pcs, setPCs] = useState<PCSummary[]>([]);
  const [avail, setAvail] = useState<Availability[]>([]);
  const [error, setError] = useState<string | null>(null);

  const [kind, setKind] = useState<"characters" | "pcs">("characters");
  const [actorId, setActorId] = useState("");
  const [role, setRole] = useState<"player" | "npc">("npc");

  const [prompt, setPrompt] = useState("");
  const [opener, setOpener] = useState("");
  const [busy, setBusy] = useState(false);

  const reloadCast = useCallback(() => api.getCast(cid, sid).then(setCast), [cid, sid]);

  useEffect(() => {
    reloadCast();
    api.availableGreetings(cid).then(setAvail).catch(() => setAvail([]));
  }, [cid, sid, reloadCast]);

  // the world's characters/pcs are needed to add actors; load lazily from the campaign's world
  useEffect(() => {
    api.getCampaign(cid).then((c) => {
      api.listCharacters(c.meta.world).then(setChars);
      api.listPCs(c.meta.world).then(setPCs);
    });
  }, [cid]);

  const options = kind === "characters" ? chars : pcs;

  async function add() {
    if (!actorId) return;
    setError(null);
    try {
      await api.addToCast(cid, sid, { kind, id: actorId, role: kind === "pcs" ? "player" : role });
      setActorId("");
      await reloadCast();
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  async function start(gid: string) {
    setError(null);
    try {
      await api.startFromGreeting(cid, sid, gid);
      onSeeded();
      await reloadCast();
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

  async function saveOpenerAsGreeting() {
    if (!opener.trim() || kind !== "characters" || !actorId) return;
    const character = chars.find((c) => c.id === actorId);
    if (!character) return;
    const name = window.prompt("Name this greeting?", "Opener")?.trim();
    if (!name) return;
    const c = await api.getCampaign(cid);
    await api.createGreeting(c.meta.world, {
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
          <div className="role">In this scene</div>
          {cast.length === 0 && <div className="field-hint">No one cast yet.</div>}
          {cast.map((a) => (
            <div className="cast-row" key={`${a.kind}/${a.id}`}>
              <span>{a.id}</span>
              <span className="role">{a.kind === "pcs" ? "PC" : "character"} · {a.role}</span>
            </div>
          ))}
        </div>

        <div>
          <div className="role">Add to scene</div>
          <div className="picker">
            <select aria-label="Actor kind" value={kind}
                    onChange={(e) => { setKind(e.target.value as "characters" | "pcs"); setActorId(""); }}>
              <option value="characters">Character</option>
              <option value="pcs">PC</option>
            </select>
            <select aria-label="Actor" value={actorId} onChange={(e) => setActorId(e.target.value)}>
              <option value="">— pick —</option>
              {options.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
            </select>
            {kind === "characters" && (
              <select aria-label="Role" value={role} onChange={(e) => setRole(e.target.value as "player" | "npc")}>
                <option value="npc">npc</option>
                <option value="player">player</option>
              </select>
            )}
            <button className="primary" onClick={add} disabled={!actorId}>Add</button>
          </div>
        </div>

        <div>
          <div className="role">Start from a greeting</div>
          {!sceneEmpty && <div className="field-hint">Available only for an empty scene.</div>}
          {avail.length === 0 && <div className="field-hint">No greetings in this world.</div>}
          <div className="chips">
            {avail.map((g) => (
              <button
                key={g.id}
                className="chip"
                disabled={!sceneEmpty || !g.available}
                title={g.available ? "" : g.reasons.join("; ")}
                onClick={() => start(g.id)}
              >
                {g.name}
              </button>
            ))}
          </div>
        </div>

        <div>
          <div className="role">Generate an opener</div>
          {!keySet && <div className="field-hint">Set an OpenRouter key in Config to generate.</div>}
          <div className="picker">
            <input type="text" aria-label="Opener prompt" placeholder="A storm over the salt marshes…"
                   value={prompt} onChange={(e) => setPrompt(e.target.value)} />
            <button className="primary" onClick={generate} disabled={!keySet || busy || !prompt.trim()}>
              {busy ? "…" : "Generate"}
            </button>
          </div>
          {opener && (
            <>
              <div className="opener-preview">{opener}</div>
              <div className="form-actions">
                <button className="subtle" onClick={() => navigator.clipboard?.writeText(opener)}>Copy</button>
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
