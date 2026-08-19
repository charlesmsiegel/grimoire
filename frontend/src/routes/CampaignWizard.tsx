import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  api, type Availability, type ModuleSummary, type PCSummary, type Persona, type WorldMeta,
} from "../api/client";
import type { ChatEvent } from "../api/stream";
import { ErrorNote } from "../components/ErrorNote";
import { PlainShell } from "../components/PageShell";

type LocationDraft = { name: string; body: string; keys: string };
const blankPersona: Persona = { name: "", pronouns: "", summary: "", description: "" };
const STEPS = ["Backdrop", "Character", "Locations", "Opening"];

export default function CampaignWizard({ ready }: { ready: boolean }) {
  const navigate = useNavigate();
  const [step, setStep] = useState(1);
  // Raw: the wizard generates an opener, which is a provider call, and this
  // is the first-run path -- the likeliest place for someone to meet an
  // unreachable model with no idea a local one would work (#210).
  const [error, setError] = useState<unknown>(null);

  // step 1
  const [worlds, setWorlds] = useState<WorldMeta[]>([]);
  const [name, setName] = useState("");
  const [world, setWorld] = useState("");
  const [region, setRegion] = useState("US");
  const [calendar, setCalendar] = useState("gregorian");
  const [calendars, setCalendars] = useState<{ id: string; name: string }[]>([]);
  const [climate, setClimate] = useState("temperate-interior");
  const [climates, setClimates] = useState<{ id: string; name: string }[]>([]);
  const [modules, setModules] = useState<ModuleSummary[]>([]);
  const [moduleId, setModuleId] = useState("");

  // step 2
  const [persona, setPersona] = useState<Persona>(blankPersona);
  const [tags, setTags] = useState<string[]>([]);
  const [tagInput, setTagInput] = useState("");
  const [worldTags, setWorldTags] = useState<string[]>([]);
  const [worldPCs, setWorldPCs] = useState<PCSummary[]>([]);
  const [pickedPC, setPickedPC] = useState<string | null>(null); // an existing world PC to play

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
    api.getCalendarProviders().then((r) => setCalendars(r.providers)).catch(() => setCalendars([]));
    api.listClimates().then((r) => setClimates(r.climates)).catch(() => setClimates([]));
    api.listModules().then(setModules).catch(() => setModules([]));
  }, []);

  useEffect(() => {
    if (!world) return;
    api.listTags(world).then((m) => setWorldTags(Object.values(m))).catch(() => setWorldTags([]));
    setPickedPC(null); // a pick belongs to one world
    api.listPCs({ kind: "world", id: world }).then(setWorldPCs).catch(() => setWorldPCs([]));
    // Seeded from the world, not left on this component's own default (#223).
    // `commit` ALWAYS passes `calendar`, and `create_campaign` reads any value
    // it is given as an explicit choice that overwrites what the world says —
    // so an unseeded picker meant a world set to Hebrew, or to a user-authored
    // plugin calendar, silently produced Gregorian campaigns unless the reader
    // noticed and re-picked. Still a picker: the world is the default here, not
    // a constraint. A world whose calendar cannot be read leaves the current
    // selection alone rather than resetting it to something nobody chose.
    api.getCalendarConfig({ kind: "world", id: world })
      .then((cfg) => { setCalendar(cfg.primary.provider); setRegion(cfg.primary.region); })
      .catch(() => {});
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
      const usesRegion = calendar === "gregorian" || calendar === "hebrew";
      const { id: cid } = await api.createCampaign(
        name.trim(), world, usesRegion ? region || undefined : undefined, calendar,
        moduleId || undefined, climate || undefined);
      // an existing world PC is already copied into the new campaign — just seat it
      let cast: { kind: "pcs"; id: string; version?: string };
      if (pickedPC) {
        cast = { kind: "pcs", id: pickedPC };
      } else {
        const { pc, version } = await api.createCampaignPC(cid, {
          name: persona.name.trim(), tags, persona: { ...persona, name: persona.name.trim() },
        });
        cast = { kind: "pcs", id: pc, version };
      }
      const { id: sid } = await api.createScene(cid);
      await api.addToCast(cid, sid, cast);
      for (const loc of locations.filter((l) => l.name.trim())) {
        await api.createEntity({ kind: "campaign", id: cid }, "locations",
          { name: loc.name.trim(), body: loc.body, keys: loc.keys });
      }
      setCommitted({ cid, sid });
      api.availableGreetings(cid).then(setAvail).catch(() => setAvail([]));
      setStep(4);
    } catch (err: unknown) {
      setError(err);
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
    } catch (err: unknown) {
      setError(err);
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
        else if (e.error) setError(e.error);
      });
    } catch (err: unknown) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  const canNext1 = name.trim() !== "" && world !== "";
  const canNext2 = pickedPC !== null || persona.name.trim() !== "";
  const who = (pickedPC && worldPCs.find((p) => p.id === pickedPC)?.name)
    || persona.name.trim() || "your character";

  return (
    <PlainShell>
      <div className="page page-narrow view-anim wizard">
        <h1 className="page-h1">New Campaign</h1>

        <ol className="wizard-steps">
          {STEPS.map((label, i) => {
            const n = i + 1;
            const state = step === n ? "on" : step > n ? "done" : "";
            return (
              <li key={label} className={`wizard-step ${state}`}>
                <span className="num">{step > n ? "✓" : n}</span>
                {step === n && <span className="label">{label}</span>}
              </li>
            );
          })}
        </ol>

        {error != null && (
          <div className="banner error-banner"><ErrorNote err={error} /></div>
        )}

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
            <div className="field">
              <label htmlFor="wiz-module">Mechanics module</label>
              <select id="wiz-module" value={moduleId}
                      onChange={(e) => setModuleId(e.target.value)}>
                <option value="">World default</option>
                <option value="none">None</option>
                {modules.map((m) => (
                  <option key={m.id} value={m.id}>{m.name}</option>
                ))}
              </select>
            </div>
            <div className="field-row">
              <div className="field">
                <label htmlFor="wiz-calendar">Calendar</label>
                <select id="wiz-calendar" aria-label="Calendar" value={calendar}
                        onChange={(e) => { setCalendar(e.target.value);
                                           setRegion(e.target.value === "gregorian" ? "US" : ""); }}>
                  {calendars.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                </select>
                <div className="field-caption">The campaign's primary calendar</div>
              </div>
              <div className="field">
                <label htmlFor="wiz-climate">Climate</label>
                <select id="wiz-climate" aria-label="Climate" value={climate}
                        onChange={(e) => setClimate(e.target.value)}>
                  {climates.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                </select>
                <div className="field-caption">
                  The default for locations that don't name one of their own
                </div>
              </div>
              {calendar === "gregorian" && (
                <div className="field">
                  <label htmlFor="wiz-region">Holidays</label>
                  <select id="wiz-region" aria-label="Holidays region" value={region}
                          onChange={(e) => setRegion(e.target.value)}>
                    <option value="US">United States</option>
                    <option value="GB">United Kingdom</option>
                    <option value="CA">Canada</option>
                    <option value="AU">Australia</option>
                    <option value="IL">Israel</option>
                    <option value="">None</option>
                  </select>
                  <div className="field-caption">Regional holiday set</div>
                </div>
              )}
              {calendar === "hebrew" && (
                <div className="field">
                  <label htmlFor="wiz-observance">Observance</label>
                  <select id="wiz-observance" aria-label="Observance" value={region}
                          onChange={(e) => setRegion(e.target.value)}>
                    <option value="">Diaspora</option>
                    <option value="IL">Israel</option>
                  </select>
                  <div className="field-caption">Israeli or diaspora holiday scheme</div>
                </div>
              )}
            </div>
            <div className="wizard-footer">
              <span />
              <button className="btn-accent" disabled={!canNext1} onClick={() => setStep(2)}>Next ▸</button>
            </div>
          </div>
        )}

        {step === 2 && (
          <div className="wizard-body">
            <h3>{worldPCs.length > 0 ? "Choose your character" : "Create your character"}</h3>
            <p className="wizard-intro">The player character you’ll inhabit. Tags unlock matching openings.</p>
            {worldPCs.length > 0 && (
              <div className="field">
                <div className="role">Play an existing character</div>
                <div className="chips">
                  {worldPCs.map((p) => (
                    <button key={p.id} className={"chip" + (pickedPC === p.id ? " on" : "")}
                            onClick={() => setPickedPC(pickedPC === p.id ? null : p.id)}>
                      {p.name}{p.tags.length > 0 ? ` · ${p.tags.join(", ")}` : ""}
                    </button>
                  ))}
                </div>
                <div className="field-hint">
                  {pickedPC ? "Click again to create someone new instead." : "— or create someone new below —"}
                </div>
              </div>
            )}
            {!pickedPC && <>
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
            </>}
            <div className="wizard-footer">
              <button className="subtle" onClick={() => setStep(1)}>Back</button>
              <button className="btn-accent" disabled={!canNext2} onClick={() => setStep(3)}>Next ▸</button>
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
              <button className="btn-accent" onClick={commit} disabled={busy}>
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
              {!ready && <div className="field-hint">Set up an LLM connection in Config to generate.</div>}
              <div className="picker">
                <input type="text" aria-label="Opener prompt" placeholder="A storm over the salt marshes…"
                       value={prompt} onChange={(e) => setPrompt(e.target.value)} />
                <button className="primary" disabled={!ready || busy || !prompt.trim()} onClick={generate}>
                  {busy ? "…" : "Generate"}
                </button>
              </div>
              {opener && <div className="opener-preview">{opener}</div>}
            </div>
            <div className="wizard-footer">
              <span />
              <button className="btn-chrome" style={{ boxShadow: "var(--sh4)" }} onClick={() => navigate(`/campaigns/${committed.cid}`)}>Finish ▸</button>
            </div>
          </div>
        )}
      </div>
    </PlainShell>
  );
}
