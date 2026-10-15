import { useEffect, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api, type CampaignSettingKey, type CampaignSettings } from "../api/client";
import { errorText } from "../api/errors";
import { onConfigChanged } from "../appEvents";
import { CalendarConfig } from "../components/CalendarConfig";
import { CampaignModels } from "../components/inference/CampaignModels";
import { ColumnSection, PageShell } from "../components/PageShell";
import { useArtNavigation } from "../components/art/ArtNavigationProvider";

type Section = "models" | "context" | "calendar" | "advanced";
const SECTIONS: { id: Section; label: string }[] = [
  { id: "models", label: "Models" }, { id: "context", label: "Context & summaries" },
  { id: "calendar", label: "Calendar" }, { id: "advanced", label: "Advanced" },
];
const FIELDS: { key: CampaignSettingKey; label: string; section: "context" | "advanced"; hint: string }[] = [
  { key: "context_scan_depth", label: "Context scan depth", section: "context", hint: "Recent posts scanned for context. Zero uses no scan window." },
  { key: "context_budget", label: "Context budget", section: "context", hint: "Context ceiling in tokens. Zero removes the ceiling." },
  { key: "recap_depth", label: "Recap depth", section: "context", hint: "Recent scene recaps included. Zero includes none." },
  { key: "archive_depth", label: "Archive depth", section: "context", hint: "Archived scenes considered for retrieval. Zero considers none." },
  { key: "rolling_summary_every", label: "Rolling summary every", section: "context", hint: "Posts between automatic summaries. Zero disables automatic summaries." },
  { key: "scene_break_every", label: "Scene-break check every", section: "context", hint: "Posts between automatic scene-break checks. Zero disables automatic checks." },
  { key: "system_prompt", label: "System prompt", section: "advanced", hint: "An empty override suppresses the global system prompt. Turn off Override to inherit it. Campaign default prompts remain additive." },
  { key: "user_label", label: "User label", section: "advanced", hint: "Display name for your transcript posts." },
  { key: "assistant_label", label: "Assistant label", section: "advanced", hint: "Display name for narrator transcript posts." },
];

/** #73: a draft holds only touched opinions: null inherits, while "" and "0"
 *  remain real choices. Untouched fields follow fresh reads instead of being
 *  overwritten by a stale form at Save; section selection
 *  never owns a draft. Key the entire body by campaign so async completions and
 *  unsaved values cannot cross campaigns, and use the app's existing route
 *  guard to ask before a dirty body is retired. A new setting joins FIELDS. */
export default function CampaignSettingsView() {
  const { cid = "" } = useParams();
  return <CampaignSettingsBody key={cid} cid={cid} />;
}

function CampaignSettingsBody({ cid }: { cid: string }) {
  const [query] = useSearchParams();
  const asked = SECTIONS.find((s) => s.id === query.get("section"))?.id ?? "models";
  const [section, setSection] = useState<Section>(asked);
  useEffect(() => { setSection(asked); }, [asked]);
  // Keep reused editors mounted after their first visit: a section switch is
  // navigation within this page, not an implicit Cancel of another form.
  const [visited, setVisited] = useState<Set<Section>>(() => new Set([asked]));
  useEffect(() => { setVisited((v) => v.has(section) ? v : new Set([...v, section])); }, [section]);
  const [settings, setSettings] = useState<CampaignSettings | null>(null);
  const [draft, setDraft] = useState<Partial<Record<CampaignSettingKey, string | null>>>({});
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [calendarDirty, setCalendarDirty] = useState(false);
  const [modelsDirty, setModelsDirty] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const live = useRef(true);
  const pending = useRef(false);
  const readSeq = useRef(0);
  useEffect(() => { live.current = true; return () => { live.current = false; }; }, []);
  useEffect(() => onConfigChanged(() => setRevision((n) => n + 1)), []);
  useEffect(() => {
    let current = true;
    const seq = ++readSeq.current;
    setError(null);
    api.getCampaignSettings(cid).then((value) => {
      if (!current || seq !== readSeq.current) return;
      setSettings(value);
    }).catch((err: unknown) => {
      if (current && seq === readSeq.current) setError(errorText(err));
    });
    return () => { current = false; };
  }, [cid, revision]);

  const patch: Partial<Record<CampaignSettingKey, string | null>> = {};
  if (settings) {
    for (const { key } of FIELDS) {
      if (draft[key] !== undefined && draft[key] !== (settings.overrides[key] ?? null)) patch[key] = draft[key];
    }
  }
  const dirty = editing && Object.keys(patch).length > 0;
  // The page owns navigation protection for every retained editor, including
  // one whose section is currently hidden. Each editor reports its own saved
  // baseline rather than treating merely entering edit mode as a change.
  const anyDirty = dirty || calendarDirty || modelsDirty;
  const navigation = useArtNavigation();
  useEffect(() => navigation.registerDraftGuard(() => !anyDirty
    || window.confirm("Discard unsaved campaign settings?")), [navigation, anyDirty]);
  useEffect(() => {
    if (!anyDirty) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [anyDirty]);

  function cancel() {
    setDraft({});
    setEditing(false);
    setError(null);
  }
  async function save() {
    if (!dirty || pending.current) return;
    pending.current = true;
    // An already-issued read may describe the state before this publication.
    // A successful save carries its own fresh view and invalidates reads again
    // so a config-event refresh cannot install an older answer over it.
    readSeq.current += 1;
    setSaving(true);
    setError(null);
    try {
      const value = await api.putCampaignSettings(cid, patch);
      if (!live.current) return;
      readSeq.current += 1;
      setSettings(value);
      setDraft({});
      setEditing(false);
    } catch (err: unknown) {
      if (live.current) setError(errorText(err));
    } finally {
      pending.current = false;
      if (live.current) setSaving(false);
    }
  }

  const column = <>
    <ColumnSection label="Campaign settings">
      {SECTIONS.map((s) => <button key={s.id} type="button" aria-pressed={s.id === section}
        className={"column-row" + (s.id === section ? " active" : "")}
        onClick={() => setSection(s.id)}>{s.label}</button>)}
    </ColumnSection>
    <ColumnSection label="Library settings">
      <Link className="column-row" to="/config?section=storage">Storage location</Link>
    </ColumnSection>
    <ColumnSection label="This campaign">
      <Link className="column-row" to={`/campaigns/${cid}`}>Campaign hub</Link>
    </ColumnSection>
  </>;
  return <PageShell column={column} columnLabel="Campaign settings" className="config-shell"
    dismissKey={section} scrollKey={section}>
    <div className="page-wide view-anim config-page">
      <div className="eyebrow">Campaign settings</div>
      <h1 className="screen-title">{SECTIONS.find((s) => s.id === section)?.label}</h1>
      {error && <div className="banner error-banner" role="alert">{error}{!settings && <>
        {" "}<button className="subtle" onClick={() => setRevision((n) => n + 1)}>Try again</button>
      </>}</div>}
      {visited.has("models") && <div hidden={section !== "models"}><CampaignModels cid={cid} onDirtyChange={setModelsDirty} /></div>}
      {visited.has("calendar") && <div hidden={section !== "calendar"}><CalendarConfig scope={{ kind: "campaign", id: cid }} onDirtyChange={setCalendarDirty} /></div>}
      {(section === "context" || section === "advanced") && <>
        <p className="config-copy">These values follow global settings until you choose an override for this campaign.</p>
        {!settings && !error && <p className="field-hint">Reading campaign settings…</p>}
        {settings && <>
          {!editing && <div className="form-actions"><button className="subtle" onClick={() => setEditing(true)}>Edit</button></div>}
          {FIELDS.filter((f) => f.section === section).map(({ key, label, hint }) => {
            const opinion = draft[key] !== undefined ? draft[key] : settings.overrides[key];
            const own = opinion != null;
            const value = editing ? opinion ?? settings.inherited[key] : settings.effective[key];
            return <section className="campaign-setting" role="group" aria-label={label} key={key}>
              <h3>{label}</h3>
              {editing ? <>
                <label className="checkbox-row"><input type="checkbox" checked={own} disabled={saving}
                  aria-label={`Override ${label}`} onChange={(e) => setDraft((d) => ({ ...d,
                    [key]: e.target.checked ? settings.inherited[key] : null,
                  }))} /> Override for this campaign</label>
                <label className="field"><span>{label}</span>
                  {key === "system_prompt" ? <textarea rows={8} value={value} disabled={!own || saving}
                    onChange={(e) => setDraft((d) => ({ ...d, [key]: e.target.value }))} />
                    : <input type="text" inputMode={section === "context" ? "numeric" : "text"}
                      value={value} disabled={!own || saving}
                      onChange={(e) => setDraft((d) => ({ ...d, [key]: e.target.value }))} />}
                </label>
              </> : <p className="config-copy" style={{ whiteSpace: "pre-wrap" }}>{value === "" ? (key === "system_prompt"
                && settings.overrides[key] !== undefined ? "Empty override — suppresses the global system prompt." : "Empty") : value}</p>}
              <p className="field-hint">{(editing ? own : settings.overrides[key] !== undefined) ? "Campaign override" : "Inherited from global settings"}</p>
              <p className="field-hint" style={{ whiteSpace: "pre-wrap" }}>Global default: {settings.inherited[key] || "Empty"}</p>
              <p className="config-caption">{hint}</p>
            </section>;
          })}
        </>}
      </>}
      {editing && (section === "context" || section === "advanced") && <div className="form-actions">
        <button className="primary" onClick={() => { void save(); }} disabled={!dirty || saving}>{saving ? "Saving…" : "Save"}</button>
        <button className="subtle" onClick={cancel} disabled={saving}>Cancel</button>
      </div>}
    </div>
  </PageShell>;
}
