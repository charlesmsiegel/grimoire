import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import {
  api, type Actor, type SceneContext, type SceneLocation, type ChronicleEntry,
  type CalendarConfig, type RosterEntry, type SceneDatetime,
  type CharacterSummary, type PCSummary, type Briefing, type BriefingRow,
} from "../api/client";
import { getModels, type Model } from "../api/models";
import { Portrait } from "./Portrait";
import { RecordDrawer, type DrawerTarget } from "./RecordDrawer";
import { CalendarDatePicker } from "./CalendarDatePicker";
import { WeatherWidget } from "./WeatherWidget";
import { ResponsePresetPicker } from "./ResponsePresetPicker";
import { LOCKED_WHILE_GENERATING } from "./sceneLock";

const SECTIONS_KEY = "grimoire.inspector.sections";

/** Posts after which the briefing (#118) stops opening itself. It is a *pre*-scene
 *  briefing: past a few exchanges the scene has said what it is about and the
 *  section is just occupying the top of the rail. Only the DEFAULT moves — once
 *  the reader toggles it, `collapsed.briefing` is set and their choice wins in
 *  both directions forever, which auto-collapsing on a timer could not do. */
const BRIEFING_OPEN_POSTS = 6;

function loadSectionCollapse(): Record<string, boolean> {
  try {
    return JSON.parse(localStorage.getItem(SECTIONS_KEY) ?? "{}");
  } catch {
    return {};
  }
}

function SideSection({ id, title, collapsed, onToggle, extra, children }: {
  id: string; title: string; collapsed: boolean;
  /** Handed the state the reader is actually looking at, not just the id. Every
   *  section but the briefing derives `collapsed` from the stored map, so for
   *  those the two agree — but the briefing's default comes from the post count
   *  (#118), and flipping the *stored* `undefined` there would write `true` on a
   *  click meant to expand, leaving the section shut and the click inert. */
  onToggle: (id: string, collapsed: boolean) => void;
  extra?: ReactNode; children: ReactNode;
}) {
  return (
    <div className="side-section">
      <button type="button" className="side-section-head" aria-expanded={!collapsed}
              onClick={() => onToggle(id, collapsed)}>
        <h4>{title}</h4>
        <span className="side-section-head-right">
          {extra}
          <span className="side-section-chev" aria-hidden>{collapsed ? "▸" : "▾"}</span>
        </span>
      </button>
      {!collapsed && <div className="side-section-body">{children}</div>}
    </div>
  );
}

/** What a failed briefing load degrades to: the empty state, never a stuck
 *  "Loading…" and never a section that outlives the scene it described. */
const NO_BRIEFING: Briefing = {
  focus: [], plot: [], commitments: [], relationships: [], last_time: null,
};

// One component for threads and commitments: they differ by `due` alone, which
// only commitments carry, so an optional field is the whole variation and a
// second near-identical component would be repetition with a footnote.
function BriefingRows({ label, rows }:
  { label: string; rows: (BriefingRow & { due?: string })[] }) {
  if (!rows.length) return null;
  return (
    <div className="ledger-group">
      <h5>{label}</h5>
      {rows.map((r) => (
        <div className="ledger-row" key={r.id}>
          <div className="ledger-row-head">
            <strong>{r.title}</strong>
            <span className="chip on">{r.status}</span>
            {r.due && <span className="chip on">due {r.due}</span>}
            {/* The flag the view exists for. Names rather than a tick, because a
                scene with two players needs to know which of them it is. */}
            {r.involves.length > 0 && (
              <span className="chip on">involves {r.involves.join(", ")}</span>
            )}
          </div>
          {r.latest_beat && <p className="ledger-beat">{r.latest_beat}</p>}
        </div>
      ))}
    </div>
  );
}

export function SceneInspector({ cid, sid, refreshKey, onSceneChanged, onSceneRenamed, pcless,
                                 sceneLocked, onRenaming, posts }:
  { cid: string; sid: string; refreshKey: number; onSceneChanged: () => void;
    onSceneRenamed?: (id: string) => void; pcless?: boolean;
    /** A turn is streaming into this scene, so anything that can rename its
     *  file has to wait: the id is the filename, and moving it mid-turn strands
     *  `finalize`, `_persist_reply` and the abort write alike (#95). The first
     *  date set re-slugs, so both date actions below are rename surfaces. */
    sceneLocked?: boolean;
    /** Reports a scene-renaming request in and out of flight. The parent blocks
     *  new turns while one is pending: until the PUT answers, the scene's id is
     *  in doubt, and a turn handed the old one writes nowhere (#95). */
    onRenaming?: (active: boolean) => void;
    /** How many posts the scene already has, which is the only thing the
     *  briefing's default open/closed state depends on (#118). Absent counts as
     *  a fresh scene, so a caller that does not track it gets the briefing open.
     *  The caller's count is windowed (#94) and so understates a long
     *  transcript — which does not matter here, because a window is a whole page
     *  and the only distinction this draws is "barely started or not". */
    posts?: number }) {
  const [cast, setCast] = useState<Actor[]>([]);
  const [roster, setRoster] = useState<RosterEntry[]>([]);
  const [names, setNames] = useState<Record<string, string>>({});
  const [setting, setSetting] = useState<SceneLocation | null>(null);
  const [locImages, setLocImages] = useState<string[]>([]);
  const [ctx, setCtx] = useState<SceneContext | null>(null);
  const [recap, setRecap] = useState<ChronicleEntry[]>([]);
  // Held with the scene it came FROM, the way `LedgerPanel` holds its campaign:
  // the inspector stays mounted across a scene switch, so a bare `Briefing`
  // would go on flagging the previous scene's cast under the new scene's name
  // until the new request settles. Comparing during render makes that window
  // impossible rather than short.
  const [brief, setBrief] = useState<{ sid: string; data: Briefing } | null>(null);
  const [models, setModels] = useState<Model[]>([]);
  const [drawer, setDrawer] = useState<DrawerTarget | null>(null);
  const [cfg, setCfg] = useState<CalendarConfig | null>(null);
  const [when, setWhen] = useState<SceneDatetime | null>(null);
  const [provider, setProvider] = useState("gregorian");
  const [calendars, setCalendars] = useState<{ id: string; name: string }[]>([]);
  const [dateInput, setDateInput] = useState("");
  const [locations, setLocations] = useState<{ id: string; name: string }[]>([]);
  const [locPick, setLocPick] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>(loadSectionCollapse);
  const toggleSection = useCallback((id: string, current: boolean) => {
    setCollapsed((prev) => {
      const next = { ...prev, [id]: !current };
      localStorage.setItem(SECTIONS_KEY, JSON.stringify(next));
      return next;
    });
  }, []);

  useEffect(() => {
    Promise.all([api.listCharacters({ kind: "campaign", id: cid }), api.listCampaignPCs(cid)])
      .then(([chars, pcs]) => {
        const m: Record<string, string> = {};
        for (const x of chars) m[`characters/${x.id}`] = x.name;
        for (const x of pcs) m[`pcs/${x.id}`] = x.name;
        setNames(m);
      });
    getModels().then(setModels).catch(() => setModels([]));
    api.listEntities({ kind: "campaign", id: cid }, "locations")
      .then((ls) => setLocations(ls.map((l) => ({ id: l.id, name: l.name }))))
      .catch(() => setLocations([]));
    api.getCalendarProviders().then((r) => setCalendars(r.providers)).catch(() => setCalendars([]));
  }, [cid]);

  const [chars, setChars] = useState<CharacterSummary[]>([]);
  const [pcs, setPcs] = useState<PCSummary[]>([]);
  const [addKind, setAddKind] = useState<"characters" | "pcs">("characters");
  const [addActorId, setAddActorId] = useState("");
  const [addRole, setAddRole] = useState<"player" | "npc">("npc");

  useEffect(() => {
    api.listCharacters({ kind: "campaign", id: cid }).then(setChars).catch(() => setChars([]));
    api.listCampaignPCs(cid).then(setPcs).catch(() => setPcs([]));
  }, [cid]);

  const addOptions = (addKind === "characters" ? chars : pcs)
    .filter((o) => !cast.some((a) => a.kind === addKind && a.id === o.id));

  async function addCastMember() {
    if (!addActorId) return;
    setError(null);
    try {
      await api.addToCast(cid, sid, {
        kind: addKind, id: addActorId,
        role: pcless ? "npc" : addKind === "pcs" ? "player" : addRole,
      });
      setAddActorId("");
      await reloadCast();
      onSceneChanged();
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  async function removeCastMember(a: Actor) {
    setError(null);
    try {
      await api.removeFromCast(cid, sid, a.kind, a.id);
      await reloadCast();
      onSceneChanged();
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  const reloadWhen = useCallback(
    () => api.getSceneDatetime(cid, sid).then((w) => {
      setWhen(w);
      // dateless scene with a suggestion: pre-fill the input, but never clobber typing
      if (!w.current && w.suggested) setDateInput((prev) => prev || w.suggested!);
    }).catch(() => setWhen(null)),
    [cid, sid]);
  const reloadCfg = useCallback(
    () => api.getCalendarConfig(cid).then(setCfg).catch(() => setCfg(null)),
    [cid]);
  const reloadCast = useCallback(
    () => api.getCast(cid, sid).then(setCast).catch(() => setCast([])),
    [cid, sid]);

  useEffect(() => {
    reloadCast();
    api.listAppearances(cid).then(setRoster).catch(() => setRoster([]));
    api.getSceneLocation(cid, sid).then(setSetting).catch(() => setSetting(null));
    api.getSceneContext(cid, sid).then(setCtx).catch(() => setCtx(null));
    api.getChronicle(cid).then(setRecap).catch(() => setRecap([]));
    reloadWhen();
    reloadCfg();
  }, [cid, sid, refreshKey, reloadWhen, reloadCfg, reloadCast]);

  // Its own effect rather than a line in the load above, because it is the one
  // request here whose late answer can be *wrong* rather than merely stale: a
  // superseded briefing describes a different scene's cast. `live` drops it.
  useEffect(() => {
    let live = true;
    api.sceneBriefing(cid, sid)
      .then((b) => { if (live) setBrief({ sid, data: b }); })
      .catch(() => { if (live) setBrief({ sid, data: NO_BRIEFING }); });
    return () => { live = false; };
  }, [cid, sid, refreshKey]);

  // the location section shows the primary image when the store has one
  useEffect(() => {
    const loc = setting?.current;
    if (!loc) { setLocImages([]); return; }
    api.listEntityImages({ kind: "campaign", id: cid }, "locations", loc.id)
      .then((imgs) => setLocImages(imgs.map((i) => i.name)))
      .catch(() => setLocImages([]));
  }, [cid, setting]);

  async function chooseCalendar() {
    if (!cfg) return;
    setError(null);
    try {
      await api.setCalendarConfig(cid, {
        ...cfg, primary: { ...cfg.primary, provider }, confirmed: true });
      await reloadCfg();
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  async function moveTo() {
    if (!locPick) return;
    setError(null);
    try {
      await api.setSceneLocation(cid, sid, locPick);
      setLocPick("");
      await api.getSceneLocation(cid, sid).then(setSetting).catch(() => setSetting(null));
      onSceneChanged(); // surface the location-transition line in the stream
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  async function applyDatetime() {
    if (!dateInput) return;
    setError(null);
    onRenaming?.(true);      // the first date set re-slugs the file
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
      onSceneChanged();  // surface the "Time passes…" transition line in the stream
    } catch (err: any) {
      setError(err.detail ?? String(err));
    } finally {
      onRenaming?.(false);
    }
  }

  const modelLen = useMemo(
    () => models.find((m) => m.id === ctx?.model)?.context ?? 0,
    [models, ctx]);
  // Percentages measure against whatever actually bounds the prompt. With both
  // a packer budget and a model window known, that is the SMALLER of the two:
  // a 32k budget left over from a 32k model would otherwise report a full 8k
  // window as a quarter used, hiding the overflow this panel exists to show.
  // Either may be absent (no budget configured; an unknown model), so fall back
  // to whichever is present.
  const limits = [ctx?.budget_tokens ?? 0, modelLen].filter((n) => n > 0);
  const ctxLen = limits.length ? Math.min(...limits) : 0;

  const pct = (t: number) => (ctxLen > 0 ? ` · ${Math.round((t / ctxLen) * 100)}%` : "");
  const pctNumber = (t: number) => (ctxLen > 0 ? Math.round((t / ctxLen) * 100) : 0);
  const nameOf = (a: Actor) => names[`${a.kind}/${a.id}`] ?? a.id;

  const briefing = brief && brief.sid === sid ? brief.data : null;
  // Rendered only when it has something to say. An empty briefing is the normal
  // state of a brand-new campaign, and an always-present "Nothing yet" heading
  // at the top of the rail would push the sections that do have content down for
  // every scene of the first session.
  const hasBriefing = !!briefing && (briefing.commitments.length > 0 || briefing.plot.length > 0
    || briefing.relationships.length > 0 || !!briefing.last_time);

  return (
    <aside className="inspector">
      {error && <div className="banner">{error}</div>}
      {hasBriefing && briefing && (
        <SideSection id="briefing" title="Briefing"
                     collapsed={collapsed.briefing ?? (posts ?? 0) > BRIEFING_OPEN_POSTS}
                     onToggle={toggleSection}>
          {briefing.last_time && (
            <p className="ledger-beat">
              <span className="field-hint">Last time · {briefing.last_time.title}
                {briefing.last_time.date ? ` (${briefing.last_time.date})` : ""}</span>
              <br />{briefing.last_time.one_line}
            </p>
          )}
          {/* Commitments before threads, the order `LedgerPanel` argues for: what
              the story still OWES is the question, and a thread is only in motion. */}
          <BriefingRows label="Commitments" rows={briefing.commitments} />
          <BriefingRows label="Open plot threads" rows={briefing.plot} />
          {briefing.relationships.length > 0 && (
            <div className="ledger-group">
              <h5>Between them</h5>
              {/* Index keys: two distinct pairs of same-named actors render the
                  same line, and this list is replaced wholesale on every load
                  and never reordered or edited — so position IS the identity. */}
              {briefing.relationships.map((line, i) => (
                <div className="field-hint" key={i}>{line}</div>
              ))}
            </div>
          )}
        </SideSection>
      )}
      {pcless && (
        <SideSection id="offscreen" title="Offscreen scene" collapsed={!!collapsed.offscreen} onToggle={toggleSection}>
          <div className="field-hint">No player character — you direct the NPCs.</div>
        </SideSection>
      )}
      {recap.length > 0 && (
        <SideSection id="story" title="Story so far" collapsed={!!collapsed.story} onToggle={toggleSection}>
          {[...recap].reverse().map((r) => (
            <div className="field-hint" key={r.id}>{r.one_line || r.summary}</div>
          ))}
        </SideSection>
      )}
      <SideSection id="cast" title="Active characters" collapsed={!!collapsed.cast} onToggle={toggleSection}>
        {cast.length === 0 && <div className="field-hint">No one cast yet.</div>}
        {cast.map((a) => {
          const ver = a.kind === "characters"
            ? roster.find((r) => r.kind === "characters" && r.id === a.id)?.version
            : undefined;
          const pc = a.role === "player";
          return (
            <div className="inspector-row-item" key={`${a.kind}/${a.id}`}>
              <button className={"inspector-row" + (pc ? " pc" : "")}
                      onClick={() => setDrawer({ type: "actor", kind: a.kind, id: a.id })}>
                <Portrait src={ver ? api.campaignImageUrl(cid, a.id, ver, "avatar") : null}
                          name={nameOf(a)} />
                <span className="inspector-name">{nameOf(a)}</span>
                <span className="role-chip">{pc ? "player" : "npc"}</span>
              </button>
              <button className="inspector-row-remove" aria-label={`Remove ${nameOf(a)} from scene`}
                      onClick={() => removeCastMember(a)}>✕</button>
            </div>
          );
        })}
        <div className="picker">
          {!pcless && (
            <select aria-label="Cast kind to add" value={addKind}
                    onChange={(e) => { setAddKind(e.target.value as "characters" | "pcs"); setAddActorId(""); }}>
              <option value="characters">Character</option>
              <option value="pcs">PC</option>
            </select>
          )}
          <select aria-label="Character or PC to add" value={addActorId}
                  onChange={(e) => setAddActorId(e.target.value)}>
            <option value="">— pick —</option>
            {addOptions.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
          </select>
          {addKind === "characters" && !pcless && (
            <select aria-label="Role for new cast member" value={addRole}
                    onChange={(e) => setAddRole(e.target.value as "player" | "npc")}>
              <option value="npc">npc</option>
              <option value="player">player</option>
            </select>
          )}
          <button className="primary" onClick={addCastMember} disabled={!addActorId}>+ Add</button>
        </div>
      </SideSection>

      <SideSection id="location" title="Location" collapsed={!!collapsed.location} onToggle={toggleSection}>
        {setting?.current
          ? <button className={"inspector-row" + (locImages.includes("avatar") ? " inspector-loc" : "")}
                    onClick={() => setDrawer({ type: "location", id: setting.current!.id })}>
              {locImages.includes("avatar") && (
                <img className="inspector-loc-thumb" alt={setting.current.name}
                     src={api.entityImageUrl({ kind: "campaign", id: cid }, "locations", setting.current.id, "avatar")}
                     onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = "none"; }} />
              )}
              <span>{setting.current.name}</span>
            </button>
          : <div className="field-hint">No setting</div>}
        {locations.length > 0 && (
          <div className="picker">
            <select aria-label="Move to location" value={locPick}
                    onChange={(e) => setLocPick(e.target.value)}>
              <option value="">Move to…</option>
              {locations
                .filter((l) => l.id !== setting?.current?.id)
                .map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
            </select>
            <button className="primary" onClick={moveTo} disabled={!locPick}>Move to</button>
          </div>
        )}
      </SideSection>

      <SideSection id="style" title="Response preset" collapsed={!!collapsed.style} onToggle={toggleSection}>
        <ResponsePresetPicker scope="scene" cid={cid} sid={sid} onChanged={onSceneChanged} />
      </SideSection>

      <SideSection id="when" title="When" collapsed={!!collapsed.when} onToggle={toggleSection}>
        {when?.current ? (
          <>
            <div className="field-hint">{when.current.friendly} ({when.current.weekday})</div>
            {when.current.holidays_today.length > 0 && (
              <div className="field-hint">Holidays: {when.current.holidays_today.join(", ")}</div>
            )}
            <div className="picker">
              <CalendarDatePicker scope={{ kind: "campaign", id: cid }} value={dateInput}
                                  onChange={setDateInput} ariaLabel="Scene date" />
              <button className="primary" onClick={applyDatetime}
                      disabled={!dateInput || sceneLocked}
                      title={sceneLocked ? LOCKED_WHILE_GENERATING : undefined}>Advance to</button>
            </div>
          </>
        ) : cfg && !cfg.confirmed ? (
          <>
            <div className="field-hint">Select a calendar to track dates.</div>
            <div className="picker">
              <select aria-label="Calendar" value={provider} onChange={(e) => setProvider(e.target.value)}>
                {calendars.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              </select>
              <button className="primary" onClick={chooseCalendar}>Use this calendar</button>
            </div>
          </>
        ) : (
          <>
            <div className="field-hint">No date</div>
            <div className="picker">
              <CalendarDatePicker scope={{ kind: "campaign", id: cid }} value={dateInput}
                                  onChange={setDateInput} ariaLabel="Scene date" />
              <button className="primary" onClick={applyDatetime}
                      disabled={!dateInput || sceneLocked}
                      title={sceneLocked ? LOCKED_WHILE_GENERATING : undefined}>Set date</button>
            </div>
          </>
        )}
      </SideSection>

      <SideSection id="weather" title="Weather" collapsed={!!collapsed.weather} onToggle={toggleSection}>
        {/* Renders nothing when there is no location or moment yet, the same
            way the When and Location sections above it degrade. */}
        <WeatherWidget cid={cid} sid={sid} refreshKey={refreshKey} />
      </SideSection>

      <SideSection id="context" title="Context" collapsed={!!collapsed.context} onToggle={toggleSection}
                   extra={ctx && ctxLen > 0 ? <span className="ctx-pct">{pctNumber(ctx.total_tokens)}%</span> : undefined}>
        {ctx && (
          <>
            <div className="ctx-bar">
              <div className="ctx-bar-fill" style={{ width: `${Math.min(100, pctNumber(ctx.total_tokens))}%` }} />
            </div>
            <div className="ctx-tokens">
              {ctx.total_tokens.toLocaleString()}{ctxLen > 0 ? ` / ${ctxLen.toLocaleString()}` : ""} tok
            </div>
            {ctx.dropped_tokens > 0 && (
              <div className="ctx-tokens">
                {ctx.dropped_tokens.toLocaleString()} tok dropped to fit the budget
              </div>
            )}
            <div className="ctx-caption">Breakdown · click a row to inspect</div>
          </>
        )}
        {ctx?.sections.map((s) => (
          <details className={"ctx-section" + (s.dropped ? " dropped" : "")} key={s.label}>
            <summary>
              <span className={"ctx-dot" + (s.label.toLowerCase().includes("transcript") ? " hot" : "")} />
              <span className="ctx-label">{s.label}</span>
              {s.dropped && <span className="ctx-drop">dropped</span>}
              {s.trimmed > 0 && <span className="ctx-drop">{s.trimmed} trimmed</span>}
              <span className="ctx-meta">{s.tokens.toLocaleString()}{pct(s.tokens)}</span>
            </summary>
            <div className="ctx-mini">
              <div style={{ width: `${Math.min(100, pctNumber(s.tokens))}%` }} />
            </div>
            <pre className="ctx-text">{s.text}</pre>
          </details>
        ))}
      </SideSection>

      {drawer && <RecordDrawer cid={cid} sid={sid} target={drawer} onClose={() => setDrawer(null)} />}
    </aside>
  );
}
