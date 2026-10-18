import { useEffect, useMemo, useRef, useState } from "react";
import Markdown from "react-markdown";
import { useNavigate } from "react-router-dom";
import remarkGfm from "remark-gfm";
import {
  api, type Availability, type ModuleSummary, type PCSummary, type Persona, type WorldMeta,
} from "../api/client";
import type { ChatEvent, OpenerContribution, OpenerSpeaker } from "../api/stream";
import { ErrorNote } from "../components/ErrorNote";
import { GreetingAvailabilityPreview } from "../components/GreetingAvailabilityPreview";
import { generatedCount } from "../components/OpenerComposer";
import { PlainShell } from "../components/PageShell";
import { byName } from "../sortByName";
import { NeedsModel } from "../components/inference/NeedsModel";

/** One starting location as typed. `made` once `commit` has written it, after
 *  which a retry skips it -- see `Made`. */
type LocationDraft = { name: string; body: string; keys: string; made?: boolean };
type CastRef = { kind: "pcs"; id: string; version?: string };
/** What `commit` has written so far, so Create pressed again after a failure
 *  picks up where the last attempt stopped.
 *
 *  A campaign is five kinds of write -- the campaign, its PC, the opening
 *  scene, the PC's seat in it, the locations -- and they are separate
 *  requests. Every Create used to start again from the first, so a failure at
 *  any later one left the campaign it had made behind, and the retry made the
 *  next one beside it (`-2`, `-3`, one per click). Remembering what landed is
 *  the whole fix; nothing is undone, because a half-made campaign the reader
 *  is about to finish is not garbage.
 *
 *  It does change what the earlier steps may offer. A retry uses these
 *  records, not the fields they were made from, so a step whose record exists
 *  is no longer one the reader can go back and edit: Back is not offered into
 *  it, and a written location's fields are locked. A step that wrote nothing
 *  -- a PC the server refused -- stays open, which is how a refused name is
 *  fixed. */
type Made = { cid?: string; cast?: CastRef; sid?: string; seated?: boolean };
const blankPersona: Persona = { name: "", pronouns: "", summary: "", description: "" };
const STEPS = ["Backdrop", "Character", "Locations", "Opening"];
const showOpener = (parts: OpenerContribution[]) =>
  parts.map((part) => `**${part.speaker}:** ${part.content}`).join("\n\n");

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
  // id -> label, kept whole: a greeting's `requires_tags` names tag IDS, so the
  // wizard suggests the label and commits the id. Offering `Object.values` here
  // seated PCs carrying "Lost Prince" against greetings gated on `lost-prince`,
  // which no comparison in `greetings.availability` ever folds together.
  const [worldTags, setWorldTags] = useState<Record<string, string>>({});
  const [worldPCs, setWorldPCs] = useState<PCSummary[]>([]);
  const [pickedPC, setPickedPC] = useState<string | null>(null); // an existing world PC to play

  /** Why the typed name cannot be this campaign's PC, from the check Next
   *  makes before leaving step 2; cleared by typing. */
  const [nameRefusal, setNameRefusal] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);
  const nameCheck = useRef({ generation: 0 });
  useEffect(() => {
    const checks = nameCheck.current;
    setChecking(false);
    // A late answer belongs to the checked name/world and character step.
    // Editing or navigating away cancels its authority to advance the wizard.
    return () => { checks.generation++; };
  }, [world, persona.name, pickedPC, step]);

  // step 3
  const [locations, setLocations] = useState<LocationDraft[]>([{ name: "", body: "", keys: "" }]);
  const [made, setMade] = useState<Made>({});

  // step 4 (live campaign)
  const [committed, setCommitted] = useState<{ cid: string; sid: string } | null>(null);
  const [avail, setAvail] = useState<Availability[]>([]);
  const [prompt, setPrompt] = useState("");
  const [opener, setOpener] = useState("");
  const [openerParts, setOpenerParts] = useState<OpenerContribution[]>([]);
  const [openerSnapshot, setOpenerSnapshot] = useState<OpenerSpeaker[]>([]);
  const [openerComplete, setOpenerComplete] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.listWorlds().then((ws) => {
      setWorlds(ws);
      // Seeded from the response's own order, which is newest-first, and NOT
      // from the A-Z order the picker below renders in. A default is not an
      // ordering: the world you touched last is the one you are most likely
      // starting a campaign in, and picking whichever world happens to sort
      // first would be an answer chosen by spelling.
      if (ws.length) setWorld(ws[0].id);
    });
    api.getCalendarProviders().then((r) => setCalendars(r.providers)).catch(() => setCalendars([]));
    api.listClimates().then((r) => setClimates(r.climates)).catch(() => setClimates([]));
    api.listModules().then(setModules).catch(() => setModules([]));
  }, []);

  /** The picker's own order, A-Z. A dropdown you scan for a name is the case
   *  alphabetical exists for; `setWorld` above deliberately reads the
   *  unsorted response instead. */
  const worldOptions = useMemo(() => byName(worlds), [worlds]);

  // The vocabulary as last rendered, read by the world-change effect before
  // that effect clears it (#68).
  const vocabRef = useRef(worldTags);
  useEffect(() => { vocabRef.current = worldTags; }, [worldTags]);

  useEffect(() => {
    if (!world) return;
    let live = true;
    // These lists belong to the chosen world as much as its calendar does.
    // A late PC or vocabulary answer must not supply another world's tags to
    // the opening preview (or seat that world's PC on commit) (#68).
    const previousIds = new Set(Object.keys(vocabRef.current));
    setWorldTags({});
    setWorldPCs([]);
    setPickedPC(null); // a pick belongs to one world
    // A draft tag that is one of the previous world's vocabulary IDs names
    // another tag here, so it cannot carry across. Free-form tags are not IDs
    // and stay (#68).
    setTags((prev) => prev.filter((t) => !previousIds.has(t)));
    api.listTags(world)
      .then((rows) => { if (live) setWorldTags(rows); })
      .catch(() => { if (live) setWorldTags({}); });
    api.listPCs({ kind: "world", id: world })
      .then((rows) => { if (live) setWorldPCs(rows); })
      .catch(() => { if (live) setWorldPCs([]); });
    // Seeded from the world, not left on this component's own default (#223).
    // `commit` ALWAYS passes `calendar`, and `create_campaign` reads any value
    // it is given as an explicit choice that overwrites what the world says —
    // so an unseeded picker meant a world set to Hebrew, or to a user-authored
    // plugin calendar, silently produced Gregorian campaigns unless the reader
    // noticed and re-picked. Still a picker: the world is the default here, not
    // a constraint. A world whose calendar cannot be read leaves the current
    // selection alone rather than resetting it to something nobody chose.
    // Guarded, like the list fills above it: this one writes a control the
    // reader will commit, so a slower answer for a world they have already left
    // would silently change what the new campaign is created with.
    api.getCalendarConfig({ kind: "world", id: world })
      .then((cfg) => {
        if (!live) return;
        setCalendar(cfg.primary.provider);
        setRegion(cfg.primary.region);
      })
      .catch(() => {});
    return () => { live = false; };
  }, [world]);

  /** The id for what was typed: an id as-is, otherwise a label matched back to
   *  its id (a reader who picks from the datalist sees the label), otherwise
   *  the free string — a campaign tag need not be one the world declares. */
  function tagId(typed: string): string {
    if (worldTags[typed]) return typed;
    const hit = Object.entries(worldTags)
      .find(([, label]) => label.toLowerCase() === typed.toLowerCase());
    return hit ? hit[0] : typed;
  }

  function addTag() {
    const t = tagInput.trim();
    if (t) {
      const id = tagId(t);
      if (!tags.includes(id)) setTags([...tags, id]);
    }
    setTagInput("");
  }

  function setLoc(i: number, patch: Partial<LocationDraft>) {
    setLocations(locations.map((l, j) => (j === i ? { ...l, ...patch } : l)));
  }

  /** Leave step 2, once the server has said the new PC's name is free.
   *
   *  Asked here rather than learned from the commit, because the commit can
   *  only learn it from `createCampaignPC` -- after the campaign exists. A
   *  check that cannot be answered lets the reader on: the PC route's own
   *  refusal still binds, and `Made` turns a refusal there into a fix on this
   *  step rather than a second campaign. A picked world PC is not a new name,
   *  so it is not asked about. */
  async function leaveCharacterStep() {
    const request = ++nameCheck.current.generation;
    if (pickedPC === null) {
      setChecking(true);
      let refusal: string | null = null;
      try {
        refusal = (await api.checkCampaignPCName(world, persona.name.trim())).refusal;
      } catch {
        // Advice that did not arrive; see above.
      } finally {
        if (request === nameCheck.current.generation) setChecking(false);
      }
      if (request !== nameCheck.current.generation) return;
      if (refusal) { setNameRefusal(refusal); return; }
    }
    setStep(3);
  }

  async function commit() {
    setError(null);
    setBusy(true);
    // Updated as each write lands, and published at once, so a throw at any
    // point leaves `made` saying exactly what exists.
    const progress: Made = { ...made };
    const landed = (patch: Partial<Made>) => { Object.assign(progress, patch); setMade({ ...progress }); };
    try {
      const usesRegion = calendar === "gregorian" || calendar === "hebrew";
      let cid = progress.cid;
      if (cid === undefined) {
        cid = (await api.createCampaign(
          name.trim(), world, usesRegion ? region || undefined : undefined, calendar,
          moduleId || undefined, climate || undefined)).id;
        landed({ cid });
      }
      let cast = progress.cast;
      if (cast === undefined) {
        // an existing world PC is already copied into the new campaign — just seat it
        if (pickedPC) {
          cast = { kind: "pcs", id: pickedPC };
        } else {
          const { pc, version } = await api.createCampaignPC(cid, {
            name: persona.name.trim(), tags, persona: { ...persona, name: persona.name.trim() },
          });
          cast = { kind: "pcs", id: pc, version };
        }
        landed({ cast });
      }
      let sid = progress.sid;
      if (sid === undefined) {
        sid = (await api.createScene(cid)).id;
        landed({ sid });
      }
      if (!progress.seated) {
        await api.addToCast(cid, sid, cast);
        landed({ seated: true });
      }
      for (const [i, loc] of locations.entries()) {
        if (loc.made || !loc.name.trim()) continue;
        await api.createEntity({ kind: "campaign", id: cid }, "locations",
          { name: loc.name.trim(), body: loc.body, keys: loc.keys });
        setLocations((ls) => ls.map((l, j) => (j === i ? { ...l, made: true } : l)));
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
      // No `seedLocation` argument, unlike SceneConfirmForm's call: this step
      // has no location picker, so nothing here has decided where the opening
      // scene is and the greeting's own location is the only answer available
      // (#218). The default is load-bearing -- do not "tidy" it away.
      await api.startFromGreeting(committed.cid, committed.sid, gid);
      navigate(`/campaigns/${committed.cid}`);
    } catch (err: unknown) {
      setError(err);
    }
  }

  /** An opener whole enough to post: the run finished and every speaker it
   *  expected has a part. Only this is offered to the scene. */
  const openerReady = openerComplete && !busy && openerParts.length > 0
    && openerParts.length === generatedCount(openerSnapshot);

  /** Leave the wizard. With a whole opener on screen it is posted first, as
   *  the opening scene's first post, through the call the play view's Use
   *  makes (`firstPost`) -- it used to be dropped here, so the reader landed
   *  on an empty scene having paid for a preview nothing kept. Then to the
   *  scene it now opens, rather than to the hub. Without one, to the hub as
   *  before. */
  async function finish(useOpener: boolean) {
    if (!committed) return;
    if (!useOpener) { navigate(`/campaigns/${committed.cid}`); return; }
    setError(null);
    setBusy(true);
    try {
      await api.firstPost(committed.cid, committed.sid, opener, openerParts, openerSnapshot);
      navigate(`/campaigns/${committed.cid}/scenes/${committed.sid}`);
    } catch (err: unknown) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  async function generate(resume = false) {
    if (!committed || !prompt.trim() || busy) return;
    setError(null);
    const existing = resume ? openerParts : [];
    const snapshot = resume ? openerSnapshot : [];
    if (!resume) { setOpener(""); setOpenerParts([]); setOpenerSnapshot([]); }
    setOpenerComplete(false);
    setBusy(true);
    let acc = "";
    let completed = [...existing];
    let speaker = "";
    try {
      await api.opener(committed.cid, committed.sid, prompt, (e: ChatEvent) => {
        if (e.snapshot) setOpenerSnapshot(e.snapshot);
        if (e.speaker_start) { speaker = e.speaker_start.speaker; acc = ""; }
        if (e.delta) {
          acc += e.delta;
          setOpener(`${showOpener(completed)}${completed.length ? "\n\n" : ""}${speaker ? `**${speaker}:** ${acc}` : acc}`);
        }
        if (e.speaker_done) {
          completed = [...completed, e.speaker_done];
          setOpenerParts(completed);
          setOpener(showOpener(completed));
          speaker = "";
          acc = "";
        }
        else if (e.done) setOpenerComplete(true);
        else if (e.error) { setOpener(showOpener(completed)); setError(e.error); }
      }, undefined, existing, snapshot);
    } catch (err: unknown) {
      setOpener(showOpener(completed));
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  const canNext1 = name.trim() !== "" && world !== "";
  const canNext2 = (pickedPC !== null || persona.name.trim() !== "") && !checking;
  const previewTags = pickedPC ? worldPCs.find((p) => p.id === pickedPC)?.tags ?? [] : tags;
  const who = (pickedPC && worldPCs.find((p) => p.id === pickedPC)?.name)
    || persona.name.trim() || "your character";

  return (
    <PlainShell>
      <div className="page page-narrow view-anim wizard">
        <div className="eyebrow">Campaigns</div>
        <h1 className="screen-title">New campaign</h1>

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
                {worldOptions.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
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
              {/* The way out. The rail is not drawn beside a wizard, so without
                  this the only exit was the browser's Back. Nothing is written
                  before step 3, so nothing is left behind. */}
              <button className="subtle" onClick={() => navigate("/")}>Cancel</button>
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
                     aria-describedby={nameRefusal ? "wiz-pc-name-refusal" : undefined}
                     onChange={(e) => {
                       setPersona({ ...persona, name: e.target.value });
                       setNameRefusal(null);
                     }} />
              {nameRefusal && (
                <div id="wiz-pc-name-refusal" className="field-hint error" role="alert">
                  {nameRefusal}
                </div>
              )}
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
                      {worldTags[t] ?? t} ✕
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
                  {Object.entries(worldTags).sort().map(([tid, label]) => (
                    <option key={tid} value={label} />
                  ))}
                </datalist>
                <button className="subtle" onClick={addTag} disabled={!tagInput.trim()}>Add</button>
              </div>
            </div>
            </>}
            <GreetingAvailabilityPreview key={JSON.stringify([world, previewTags])}
                                         world={world} tags={previewTags} />
            <div className="wizard-footer">
              {/* Not back into a backdrop the campaign was already made from;
                  see `Made`. */}
              {made.cid === undefined ? (
                <button className="subtle" onClick={() => setStep(1)}>Back</button>
              ) : <span />}
              <button className="btn-accent" disabled={!canNext2}
                      onClick={() => void leaveCharacterStep()}>
                {checking ? "Checking…" : "Next ▸"}
              </button>
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
                       disabled={loc.made} onChange={(e) => setLoc(i, { name: e.target.value })} />
                <textarea aria-label="Location description" rows={3} placeholder="Description…" value={loc.body}
                          disabled={loc.made} onChange={(e) => setLoc(i, { body: e.target.value })} />
                <input aria-label="Location keys" type="text" placeholder="Keys to match in play (comma-separated, optional)"
                       value={loc.keys} disabled={loc.made} onChange={(e) => setLoc(i, { keys: e.target.value })} />
                {loc.made && <div className="field-hint">Saved to the campaign.</div>}
                {locations.length > 1 && !loc.made && (
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
              {/* Not back into a character that is already written; see `Made`. */}
              {made.cast === undefined ? (
                <button className="subtle" onClick={() => setStep(2)} disabled={busy}>Back</button>
              ) : <span />}
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
                : <>
                  {avail.some((g) => g.available) ? (
                    <div className="chips">
                      {avail.filter((g) => g.available).map((g) => (
                        <button key={g.id} className="chip" onClick={() => startGreeting(g.id)}>
                          {g.name}
                        </button>
                      ))}
                    </div>
                  ) : <div className="field-hint">None of them can open this campaign yet.</div>}
                  {/* Named with the reason, as step 2's preview names them --
                      the reason used to be a tooltip on a disabled chip, which
                      a touch screen never shows. */}
                  {avail.some((g) => !g.available) && (
                    <ul className="field-hint wizard-blocked" aria-label="Greetings that cannot open it">
                      {avail.filter((g) => !g.available).map((g) => (
                        <li key={g.id}><span>{g.name}</span> — {g.reasons.join("; ")}</li>
                      ))}
                    </ul>
                  )}
                </>}
            </div>
            <div className="field">
              <div className="role">Generate an opener</div>
              {!ready && <NeedsModel />}
              <div className="picker">
                <input type="text" aria-label="Opener prompt" placeholder="A storm over the salt marshes…"
                       value={prompt} onChange={(e) => setPrompt(e.target.value)} />
                <button className="primary" disabled={!ready || busy || !prompt.trim()} onClick={() => void generate()}>
                  {busy ? "…" : "Generate"}
                </button>
              </div>
              {/* Rendered, because each part is `**Speaker:** text` and the
                  reply itself is markdown -- shown raw, the asterisks were the
                  first thing the reader saw. */}
              {opener && (
                <div className="opener-preview rendered">
                  <Markdown remarkPlugins={[remarkGfm]}>{opener}</Markdown>
                </div>
              )}
              {!openerComplete && openerParts.length > 0 && !busy && (
                <button type="button" className="subtle" onClick={() => void generate(true)}>
                  Retry remaining speakers
                </button>
              )}
              {opener && !busy && (
                <div className="field-hint">
                  {openerReady
                    ? "Begin with it and it is the opening scene's first post."
                    : "Not saved: only a finished opener can begin the scene."}
                </div>
              )}
            </div>
            <div className="wizard-footer">
              {openerReady ? (
                <button className="subtle" disabled={busy} onClick={() => void finish(false)}>
                  Finish without it
                </button>
              ) : <span />}
              <button className="btn-accent" disabled={busy} onClick={() => void finish(openerReady)}>
                {openerReady ? "Begin with this opener ▸" : "Finish ▸"}
              </button>
            </div>
          </div>
        )}
      </div>
    </PlainShell>
  );
}
