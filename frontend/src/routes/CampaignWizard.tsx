import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  api, type Availability, type Persona, type WorldMeta,
} from "../api/client";
import type { ChatEvent } from "../api/stream";

type LocationDraft = { name: string; body: string; keys: string };
const blankPersona: Persona = { name: "", pronouns: "", summary: "", description: "" };
const STEPS = ["Backdrop", "Character", "Locations", "Opening"];

export default function CampaignWizard({ keySet }: { keySet: boolean }) {
  const navigate = useNavigate();
  const [step, setStep] = useState(1);
  const [error, setError] = useState<string | null>(null);

  // step 1
  const [worlds, setWorlds] = useState<WorldMeta[]>([]);
  const [name, setName] = useState("");
  const [world, setWorld] = useState("");

  // step 2
  const [persona, setPersona] = useState<Persona>(blankPersona);
  const [tags, setTags] = useState<string[]>([]);
  const [tagInput, setTagInput] = useState("");
  const [worldTags, setWorldTags] = useState<string[]>([]);

  // step 3
  const [locations, setLocations] = useState<LocationDraft[]>([{ name: "", body: "", keys: "" }]);

  // step 4 (live campaign)
  const [committed, setCommitted] = useState<{ cid: string; sid: string } | null>(null);
  const [avail, setAvail] = useState<Availability[]>([]);
  const [prompt, setPrompt] = useState("");
  const [opener, setOpener] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.listWorlds().then((ws) => {
      setWorlds(ws);
      if (ws.length) setWorld(ws[0].id);
    });
  }, []);

  useEffect(() => {
    if (!world) return;
    api.listTags(world).then((m) => setWorldTags(Object.values(m))).catch(() => setWorldTags([]));
  }, [world]);

  function addTag() {
    const t = tagInput.trim();
    if (t && !tags.includes(t)) setTags([...tags, t]);
    setTagInput("");
  }

  function setLoc(i: number, patch: Partial<LocationDraft>) {
    setLocations(locations.map((l, j) => (j === i ? { ...l, ...patch } : l)));
  }

  async function commit() {
    setError(null);
    setBusy(true);
    try {
      const { id: cid } = await api.createCampaign(name.trim(), world);
      const { pc, version } = await api.createCampaignPC(cid, {
        name: persona.name.trim(), tags, persona: { ...persona, name: persona.name.trim() },
      });
      const { id: sid } = await api.createScene(cid);
      await api.addToCast(cid, sid, { kind: "pcs", id: pc, version });
      for (const loc of locations.filter((l) => l.name.trim())) {
        await api.createEntity({ kind: "campaign", id: cid }, "locations",
          { name: loc.name.trim(), body: loc.body, keys: loc.keys });
      }
      setCommitted({ cid, sid });
      api.availableGreetings(cid).then(setAvail).catch(() => setAvail([]));
      setStep(4);
    } catch (err: any) {
      setError(err.detail ?? String(err));
    } finally {
      setBusy(false);
    }
  }

  async function startGreeting(gid: string) {
    if (!committed) return;
    setError(null);
    try {
      await api.startFromGreeting(committed.cid, committed.sid, gid);
      navigate(`/campaigns/${committed.cid}`);
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  async function generate() {
    if (!committed || !prompt.trim() || busy) return;
    setError(null);
    setOpener("");
    setBusy(true);
    let acc = "";
    try {
      await api.opener(committed.cid, committed.sid, prompt, (e: ChatEvent) => {
        if (e.delta) { acc += e.delta; setOpener(acc); }
        else if (e.error) setError(e.error.detail);
      });
    } catch (err: any) {
      setError(err.detail ?? String(err));
    } finally {
      setBusy(false);
    }
  }

  const canNext1 = name.trim() !== "" && world !== "";
  const canNext2 = persona.name.trim() !== "";
  const who = persona.name.trim() || "your character";

  return (
    <div className="view wizard">
      <h2>New campaign</h2>

      <ol className="wizard-steps">
        {STEPS.map((label, i) => {
          const n = i + 1;
          const state = step === n ? "on" : step > n ? "done" : "";
          return (
            <li key={label} className={`wizard-step ${state}`}>
              <span className="num">{step > n ? "✓" : n}</span>
              {step === n && <span className="label">{label}</span>}
              {i < STEPS.length - 1 && <span className="bar" />}
            </li>
          );
        })}
      </ol>

      {error && <div className="banner error-banner">{error}</div>}

      {step === 1 && (
        <div className="wizard-body">
          <h3>Name your campaign</h3>
          <p className="wizard-intro">Give it a title and choose the world it draws on.</p>
          <div className="field">
            <label htmlFor="wiz-name">Campaign name</label>
            <input id="wiz-name" type="text" value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="wiz-world">World</label>
            <select id="wiz-world" value={world} onChange={(e) => setWorld(e.target.value)}>
              {worlds.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
            </select>
          </div>
          <div className="wizard-footer">
            <span />
            <button className="primary" disabled={!canNext1} onClick={() => setStep(2)}>Next</button>
          </div>
        </div>
      )}

      {step === 2 && (
        <div className="wizard-body">
          <h3>Create your character</h3>
          <p className="wizard-intro">The player character you’ll inhabit. Tags unlock matching openings.</p>
          <div className="field">
            <label htmlFor="wiz-pc-name">Character name</label>
            <input id="wiz-pc-name" type="text" value={persona.name}
                   onChange={(e) => setPersona({ ...persona, name: e.target.value })} />
          </div>
          <div className="field">
            <label htmlFor="wiz-pc-pronouns">Pronouns</label>
            <input id="wiz-pc-pronouns" type="text" value={persona.pronouns}
                   onChange={(e) => setPersona({ ...persona, pronouns: e.target.value })} />
          </div>
          <div className="field">
            <label htmlFor="wiz-pc-summary">Summary</label>
            <input id="wiz-pc-summary" type="text" value={persona.summary}
                   onChange={(e) => setPersona({ ...persona, summary: e.target.value })} />
          </div>
          <div className="field">
            <label htmlFor="wiz-pc-desc">Description</label>
            <textarea id="wiz-pc-desc" rows={5} value={persona.description}
                      onChange={(e) => setPersona({ ...persona, description: e.target.value })} />
          </div>
          <div className="field">
            <label htmlFor="wiz-tag">Tags</label>
            {tags.length > 0 && (
              <div className="chips">
                {tags.map((t) => (
                  <button key={t} className="chip on" onClick={() => setTags(tags.filter((x) => x !== t))}>
                    {t} ✕
                  </button>
                ))}
              </div>
            )}
            <div className="picker">
              <input id="wiz-tag" type="text" aria-label="Add tag" list="wizard-tags" value={tagInput}
                     placeholder="Add a tag…"
                     onChange={(e) => setTagInput(e.target.value)}
                     onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addTag(); } }} />
              <datalist id="wizard-tags">
                {worldTags.map((t) => <option key={t} value={t} />)}
              </datalist>
              <button className="subtle" onClick={addTag} disabled={!tagInput.trim()}>Add</button>
            </div>
          </div>
          <div className="wizard-footer">
            <button className="subtle" onClick={() => setStep(1)}>Back</button>
            <button className="primary" disabled={!canNext2} onClick={() => setStep(3)}>Next</button>
          </div>
        </div>
      )}

      {step === 3 && (
        <div className="wizard-body">
          <h3>Add starting locations</h3>
          <p className="wizard-intro">Places relevant to {who}. Optional — add as many as you like.</p>
          {locations.map((loc, i) => (
            <div className="wizard-location" key={i}>
              <input aria-label="Location name" type="text" placeholder="Location name…" value={loc.name}
                     onChange={(e) => setLoc(i, { name: e.target.value })} />
              <textarea aria-label="Location description" rows={3} placeholder="Description…" value={loc.body}
                        onChange={(e) => setLoc(i, { body: e.target.value })} />
              <input aria-label="Location keys" type="text" placeholder="Keys to match in play (comma-separated, optional)"
                     value={loc.keys} onChange={(e) => setLoc(i, { keys: e.target.value })} />
              {locations.length > 1 && (
                <button className="subtle remove" onClick={() => setLocations(locations.filter((_, j) => j !== i))}>
                  Remove
                </button>
              )}
            </div>
          ))}
          <button className="subtle wizard-add"
                  onClick={() => setLocations([...locations, { name: "", body: "", keys: "" }])}>
            + Add another location
          </button>
          <div className="wizard-footer">
            <button className="subtle" onClick={() => setStep(2)} disabled={busy}>Back</button>
            <button className="primary" onClick={commit} disabled={busy}>
              {busy ? "Creating…" : "Create campaign"}
            </button>
          </div>
        </div>
      )}

      {step === 4 && committed && (
        <div className="wizard-body">
          <h3>Begin the opening scene</h3>
          <p className="wizard-intro">
            Start from a greeting, sketch an opener for inspiration, or finish and begin in an empty scene.
          </p>
          <div className="field">
            <div className="role">Start from a greeting</div>
            {avail.length === 0
              ? <div className="field-hint">No greetings available in this world.</div>
              : (
                <div className="chips">
                  {avail.map((g) => (
                    <button key={g.id} className="chip" disabled={!g.available}
                            title={g.available ? "" : g.reasons.join("; ")} onClick={() => startGreeting(g.id)}>
                      {g.name}
                    </button>
                  ))}
                </div>
              )}
          </div>
          <div className="field">
            <div className="role">Generate an opener</div>
            {!keySet && <div className="field-hint">Set an OpenRouter key in Config to generate.</div>}
            <div className="picker">
              <input type="text" aria-label="Opener prompt" placeholder="A storm over the salt marshes…"
                     value={prompt} onChange={(e) => setPrompt(e.target.value)} />
              <button className="primary" disabled={!keySet || busy || !prompt.trim()} onClick={generate}>
                {busy ? "…" : "Generate"}
              </button>
            </div>
            {opener && <div className="opener-preview">{opener}</div>}
          </div>
          <div className="wizard-footer">
            <span />
            <button className="primary" onClick={() => navigate(`/campaigns/${committed.cid}`)}>Finish</button>
          </div>
        </div>
      )}
    </div>
  );
}
