import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import {
  api, type Actor, type SceneContext, type SceneLocation, type ChronicleEntry,
  type CalendarConfig, type RosterEntry, type SceneDatetime, type Style,
  type CharacterSummary, type PCSummary,
} from "../api/client";
import { getModels, type Model } from "../api/models";
import { Portrait } from "./Portrait";
import { RecordDrawer, type DrawerTarget } from "./RecordDrawer";
import { CalendarDatePicker } from "./CalendarDatePicker";

const SECTIONS_KEY = "grimoire.inspector.sections";

function loadSectionCollapse(): Record<string, boolean> {
  try {
    return JSON.parse(localStorage.getItem(SECTIONS_KEY) ?? "{}");
  } catch {
    return {};
  }
}

function SideSection({ id, title, collapsed, onToggle, extra, children }: {
  id: string; title: string; collapsed: boolean; onToggle: (id: string) => void;
  extra?: ReactNode; children: ReactNode;
}) {
  return (
    <div className="side-section">
      <button className="side-section-head" aria-expanded={!collapsed} onClick={() => onToggle(id)}>
        <h4>{title}</h4>
        <span className="side-section-head-right">
          {extra}
          <span className="side-section-chev">{collapsed ? "▸" : "▾"}</span>
        </span>
      </button>
      {!collapsed && <div className="side-section-body">{children}</div>}
    </div>
  );
}

export function SceneInspector({ cid, sid, refreshKey, onSceneChanged, onSceneRenamed, pcless }:
  { cid: string; sid: string; refreshKey: number; onSceneChanged: () => void;
    onSceneRenamed?: (id: string) => void; pcless?: boolean }) {
  const [cast, setCast] = useState<Actor[]>([]);
  const [roster, setRoster] = useState<RosterEntry[]>([]);
  const [names, setNames] = useState<Record<string, string>>({});
  const [setting, setSetting] = useState<SceneLocation | null>(null);
  const [locImages, setLocImages] = useState<string[]>([]);
  const [ctx, setCtx] = useState<SceneContext | null>(null);
  const [recap, setRecap] = useState<ChronicleEntry[]>([]);
  const [models, setModels] = useState<Model[]>([]);
  const [drawer, setDrawer] = useState<DrawerTarget | null>(null);
  const [cfg, setCfg] = useState<CalendarConfig | null>(null);
  const [when, setWhen] = useState<SceneDatetime | null>(null);
  const [provider, setProvider] = useState("gregorian");
  const [calendars, setCalendars] = useState<{ id: string; name: string }[]>([]);
  const [dateInput, setDateInput] = useState("");
  const [locations, setLocations] = useState<{ id: string; name: string }[]>([]);
  const [locPick, setLocPick] = useState("");
  const [styleId, setStyleId] = useState("");
  const [styleOptions, setStyleOptions] = useState<Style[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>(loadSectionCollapse);
  const toggleSection = useCallback((id: string) => {
    setCollapsed((prev) => {
      const next = { ...prev, [id]: !prev[id] };
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
    api.listStyles().then(setStyleOptions).catch(() => setStyleOptions([]));
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
    await api.addToCast(cid, sid, {
      kind: addKind, id: addActorId,
      role: pcless ? "npc" : addKind === "pcs" ? "player" : addRole,
    });
    setAddActorId("");
    await reloadCast();
    onSceneChanged();
  }

  async function removeCastMember(a: Actor) {
    await api.removeFromCast(cid, sid, a.kind, a.id);
    await reloadCast();
    onSceneChanged();
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
  const reloadStyle = useCallback(
    () => api.getSceneStyle(cid, sid).then((r) => setStyleId(r.style_id)).catch(() => setStyleId("")),
    [cid, sid]);
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
    reloadStyle();
  }, [cid, sid, refreshKey, reloadWhen, reloadCfg, reloadStyle, reloadCast]);

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

  async function chooseStyle(value: string) {
    setStyleId(value);
    setError(null);
    try {
      await api.setSceneStyle(cid, sid, value);
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
    }
  }

  const ctxLen = useMemo(
    () => models.find((m) => m.id === ctx?.model)?.context ?? 0,
    [models, ctx]);

  const pct = (t: number) => (ctxLen > 0 ? ` · ${Math.round((t / ctxLen) * 100)}%` : "");
  const pctNumber = (t: number) => (ctxLen > 0 ? Math.round((t / ctxLen) * 100) : 0);
  const nameOf = (a: Actor) => names[`${a.kind}/${a.id}`] ?? a.id;

  return (
    <aside className="inspector">
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

      <SideSection id="style" title="Prose style" collapsed={!!collapsed.style} onToggle={toggleSection}>
        <select aria-label="Prose style" value={styleId} onChange={(e) => chooseStyle(e.target.value)}>
          <option value="">— use campaign default —</option>
          {styleOptions.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
      </SideSection>

      <SideSection id="when" title="When" collapsed={!!collapsed.when} onToggle={toggleSection}>
        {error && <div className="banner">{error}</div>}
        {when?.current ? (
          <>
            <div className="field-hint">{when.current.friendly} ({when.current.weekday})</div>
            {when.current.holidays_today.length > 0 && (
              <div className="field-hint">Holidays: {when.current.holidays_today.join(", ")}</div>
            )}
            <div className="picker">
              <CalendarDatePicker scope={{ kind: "campaign", id: cid }} value={dateInput}
                                  onChange={setDateInput} ariaLabel="Scene date" />
              <button className="primary" onClick={applyDatetime} disabled={!dateInput}>Advance to</button>
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
              <button className="primary" onClick={applyDatetime} disabled={!dateInput}>Set date</button>
            </div>
          </>
        )}
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
            <div className="ctx-caption">Breakdown · click a row to inspect</div>
          </>
        )}
        {ctx?.sections.map((s) => (
          <details className="ctx-section" key={s.label}>
            <summary>
              <span className={"ctx-dot" + (s.label.toLowerCase().includes("transcript") ? " hot" : "")} />
              <span className="ctx-label">{s.label}</span>
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
