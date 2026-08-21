import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState,
         type ReactNode } from "react";
import {
  api, type Actor, type SceneContext, type SceneLocation, type ChronicleEntry,
  type CalendarConfig, type RosterEntry, type SceneDatetime,
  type CharacterSummary, type PCSummary, type Briefing, type BriefingRow,
  type PinRule, type PromptDiff, type PromptEntry, type PromptSnapshot,
  type RollingSummary, type SceneBreak,
} from "../api/client";
import { getModels, type Model } from "../api/models";
import { ContextBreakdown, contextPercent } from "./ContextBreakdown";
import { ContextDiff } from "./ContextDiff";
import { CostPanel } from "./CostPanel";
import { Portrait } from "./Portrait";
import { RecordDrawer, type DrawerTarget } from "./RecordDrawer";
import { CalendarDatePicker } from "./CalendarDatePicker";
import { ClockPanel } from "./ClockPanel";
import { ErrorNote } from "./ErrorNote";
import { EventsPanel } from "./EventsPanel";
import { WeatherWidget } from "./WeatherWidget";
import { ResponsePresetPicker } from "./ResponsePresetPicker";
import { LOCKED_WHILE_GENERATING } from "./sceneLock";
import { taskLabel, whenLabel } from "./turnLabels";
import { SuggestedCast } from "./SuggestedCast";

const SECTIONS_KEY = "grimoire.inspector.sections";

/** Posts after which the briefing (#118) stops opening itself. It is a *pre*-scene
 *  briefing: past a few exchanges the scene has said what it is about and the
 *  section is just occupying the top of the rail. Only the DEFAULT moves — once
 *  the reader toggles it, `collapsed.briefing` is set and their choice wins in
 *  both directions forever, which auto-collapsing on a timer could not do. */
const BRIEFING_OPEN_POSTS = 6;

/** The `against` value naming the composition as it stands now rather than a
 *  captured turn. Matches `routes.scenes.LIVE_SIDE`. */
const LIVE_SIDE = "live";

/** What a pin or exclude can name (#129) — the world-info kinds plus the two
 *  actor kinds, mirroring `store/pins.py`'s KINDS. The order is the picker's. */
const PIN_KINDS = ["lore", "locations", "items", "groups", "creatures",
                   "characters", "pcs"] as const;
type PinKind = typeof PIN_KINDS[number];

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
  // Held with the campaign and scene it came from, exactly like `brief` above
  // and for the same reason: the inspector stays mounted across both switches,
  // so a bare array keeps the previous scene's turns on screen until the new
  // request settles. That is worse here than a stale label — entry ids are
  // per-campaign counters, so they collide across campaigns, and clicking a
  // stale row would fetch a DIFFERENT campaign's prompt under it. Comparing
  // during render makes the window impossible rather than short.
  const [turns, setTurns] = useState<{ cid: string; sid: string; rows: PromptEntry[] } | null>(null);
  // Scoped like `turns` and `brief`, and for a reason the clearing effect below
  // cannot cover on its own: effects run AFTER render, so on a scene or campaign
  // switch the first paint still has the old snapshot while `shown` already
  // prefers it — painting one scene's whole prompt under another's heading for a
  // frame. Comparing during render makes that impossible rather than brief.
  const [frozen, setFrozen] = useState<
    { cid: string; sid: string; data: PromptSnapshot } | null>(null);
  // What the selected turn is being compared against (#130): "" for nothing,
  // "live" for the composition as it stands now, or another entry id. Held as
  // the CHOICE rather than as the answer, so the fetch below can re-run when
  // the live side moves under it.
  const [compare, setCompare] = useState("");
  // Scoped by both ends as well as by scene, for the reason `frozen` is scoped
  // at all AND one more: the reader can switch which turn they are looking at
  // without clearing the comparison, and a diff still in flight for the turn
  // they left would otherwise be painted under the one they arrived at.
  const [diff, setDiff] = useState<
    { cid: string; sid: string; eid: string; against: string; key: number;
      data: PromptDiff } | null>(null);
  const [recap, setRecap] = useState<ChronicleEntry[]>([]);
  // Held with the campaign AND scene it came from, the way `LedgerPanel` holds
  // its campaign: the inspector stays mounted across both switches, so a bare
  // `Briefing` would go on flagging the previous scene's cast under the new
  // scene's name until the new request settles. Comparing during render makes
  // that window impossible rather than short.
  //
  // `cid` as well as `sid`, which this first got wrong (Codex review): the
  // route is `/campaigns/:cid` with no `key`, so React Router REUSES
  // `CampaignView` from one campaign to the next, and scene ids are per-campaign
  // (`0001--…`) so they repeat freely across them. Two campaigns sitting on the
  // same scene id would have matched on `sid` alone — showing one game's
  // commitments, relationships and prior fact under the other's name.
  const [brief, setBrief] = useState<{ cid: string; sid: string; data: Briefing } | null>(null);
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
  // Stamped with the record it was read for, and `undefined` means "in flight,
  // or the read failed" — NOT the same as a scene nobody has summarized
  // (`summary: ""`), since reporting an unreachable store as a scene with
  // nothing to say would be a lie the panel tells on every select.
  //
  // The stamp is `cid/sid`, not `sid`, and review caught the difference: scene
  // ids are campaign-local and collide constantly (every campaign's first scene
  // called Saltmarch is `001--saltmarch`), and `App.tsx` renders CampaignView
  // with no key, so moving between campaigns REUSES this component. A sid-only
  // stamp would show one campaign's prose as fact under another's scene — and,
  // if the new read then failed, keep showing it, because a value that passes
  // the stamp suppresses the "could not be read" message.
  const [rolling, setRolling] =
    useState<{ key: string; data: RollingSummary } | undefined>();
  const [rollingUnread, setRollingUnread] = useState(false);
  // The record whose refold is in flight, not a bare boolean: the button belongs
  // to a scene, and review caught that one scene's pending refresh disabled
  // every other scene's button — reading "Summarizing…" about a result that
  // record can no longer use.
  const [rollingBusy, setRollingBusy] = useState<string | null>(null);
  // Stamped with its record, and kept OUT of the panel's shared `error`. The
  // token guard only retires a rejection that arrives after the reader has
  // moved on; one that lands while the scene is still selected is stored, and
  // nothing on the scene-change path clears `error` — so a provider failure for
  // one scene sat as a banner over the next indefinitely. Its own state, rather
  // than clearing the shared one on every switch, because that banner is
  // written by four other handlers whose behaviour is not this PR's to change.
  const [rollingError, setRollingError] =
    useState<{ key: string; err: unknown } | null>(null);
  // The scene-break detector (#84), stamped with its record for `rolling`'s
  // reason and no lesser one: a proposal is prose ABOUT a story, so showing one
  // campaign's under another's scene reads as fact rather than as lag. Kept
  // separate from `rolling` rather than folded into one "scene state" object,
  // because the two are read and written by different calls and a shared object
  // would make either write clobber the other's half.
  const [breakState, setBreakState] =
    useState<{ key: string; data: SceneBreak } | undefined>();
  // The record whose question is in flight, not a bare boolean — `rollingBusy`'s
  // reason: the button belongs to a scene, and one scene's pending question
  // must not disable another's.
  const [breakBusy, setBreakBusy] = useState<string | null>(null);
  // A read that failed, kept apart from a read that said nothing — `rollingUnread`'s
  // distinction, and this panel needs it more sharply, not less. "Nothing to
  // suggest yet" is an ASSERTION about the scene; rendering it out of a failed
  // GET tells the player the detector looked and found nothing when it never
  // got an answer at all.
  const [breakUnread, setBreakUnread] = useState(false);
  // Reads yield to writes: a GET issued BEFORE a write must not install its
  // pre-write answer afterwards. The effect's read can be in flight when Ask
  // now commits, and resolving second would blank the verdict the player just
  // paid for, with nothing later scheduled to put it back.
  //
  // Its own counter rather than `rolling`'s `writeSeq`, which gates the shared
  // effect's reads for BOTH features: bumping that one for a break write would
  // make the effect discard a perfectly good rolling read as superseded — one
  // feature's write silently costing the other its answer.
  //
  // Deliberately no ticket pair ordering the WRITES against each other, unlike
  // `refreshRolling`'s: `breakBusy` disables both this panel's break buttons
  // for the record a question is out on, so two of its own writes cannot
  // overlap on one scene — and a write arriving from somewhere else entirely
  // (another tab) is refused by `_break_commit`, which is where that decision
  // belongs. Ordering it here as well would be machinery for a race this panel
  // cannot produce.
  const breakSeq = useRef(0);
  const [breakError, setBreakError] =
    useState<{ key: string; err: unknown } | null>(null);
  // The stamp decides what may be RENDERED. This decides what may be STORED,
  // and one without the other is not enough — that took two review rounds:
  //
  // - a superseded read that still installs replaces the current record's data,
  //   after which the stamp rejects the value it just stored and the panel says
  //   "No summary yet" about a scene that has one;
  // - and scene identity cannot order two reads of the SAME scene, which this
  //   panel issues routinely: the effect re-runs on every `refreshKey` bump, so
  //   a pre-refresh read and the post-refresh one are in flight together and the
  //   older can land last.
  //
  // So it is a monotonic token, not an identity: only the most recently issued
  // request may write, whatever it was issued for.
  const readToken = useRef(0);
  // How many manual refolds have INSTALLED. A read carries the value it saw when
  // it was issued and may only install while that still holds, because a refold
  // that landed in between wrote the store: the read is answering from before
  // that write however recently it was issued. One shared token could not say
  // this — it ordered the two by issue time, so a routine reread starting while
  // a refold was out discarded the refold's authoritative answer.
  const writeSeq = useRef(0);
  // Ordering AMONG writes, which `writeSeq` does not give: it counts how many
  // have landed, so it can retire a stale read but cannot tell two refolds
  // apart. Review found the gap that leaves. Two transitions in quick
  // succession each ask; the first fold finishes on the server and releases its
  // claim, so the second is not coalesced and really does refold — and if the
  // first one's RESPONSE is the slower of the two, it lands second and passes
  // the scene-key check, overwriting the newer summary and coverage with its
  // own older ones, with no read scheduled behind it to put things right.
  //
  // A ticket taken when a write is ISSUED, and installed only while it is still
  // the highest issued: last asked wins. Deliberately not "highest coverage
  // wins", which would look equivalent and is not — a refold after a trim
  // legitimately covers fewer posts, and the manual button must be able to
  // replace a summary with a shorter one.
  const writeTicket = useRef(0);
  const writeInstalled = useRef(0);
  // The record on screen, readable from a callback created for an earlier one.
  const currentKey = useRef(`${cid}/${sid}`);
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

  // --- pins & excludes (#129) ---
  // Held with the campaign AND scene it was read for, like `brief` and `turns`
  // above and for a sharper version of their reason: this component survives
  // both switches, and these rules do not merely LABEL the panel, they decide
  // what the row toggles do. A bare array left the previous scene's rules
  // driving the new scene's buttons -- a toggle reading "pinned" from a scene
  // the reader is no longer in, whose click then asks the server to lift a rule
  // that was never there. Comparing during render makes that impossible rather
  // than short.
  const [pinState, setPinState] =
    useState<{ cid: string; sid: string; rows: PinRule[] } | null>(null);
  const pins = pinState && pinState.cid === cid && pinState.sid === sid ? pinState.rows : [];
  // Starts unset, and the options for a kind are fetched only once one is
  // chosen: the section is open by default and most readers never touch it, so
  // a kind selected up front would mean a list request per campaign for a
  // picker nobody opened.
  const [pinKind, setPinKind] = useState<PinKind | "">("");
  const [pinTarget, setPinTarget] = useState("");
  const [pinMode, setPinMode] = useState<"pin" | "exclude">("pin");
  const [pinTtl, setPinTtl] = useState("");
  const [pinScope, setPinScope] = useState<"scene" | "campaign">("scene");
  // Lazily, one kind at a time: the picker offers seven kinds and a reader opens
  // it rarely, so loading all seven with the panel would be seven requests per
  // campaign for a section that is usually empty and shut.
  // Keyed by campaign as well as kind, for the same reason: the cache outlives
  // a campaign switch, so a bare `{lore: [...]}` offered one campaign's lore in
  // another campaign's picker -- and pinning from it would file a rule naming
  // an entry this campaign has never had.
  const [pinOptions, setPinOptions] =
    useState<Record<string, { id: string; name: string }[]>>({});

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
      askRolling();   // a join/leave appends a transition post (#85)
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
      askRolling();   // a join/leave appends a transition post (#85)
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
    () => api.getCalendarConfig({ kind: "campaign", id: cid }).then(setCfg).catch(() => setCfg(null)),
    [cid]);
  const reloadCast = useCallback(
    () => api.getCast(cid, sid).then(setCast).catch(() => setCast([])),
    [cid, sid]);
  const reloadPins = useCallback(
    () => api.getPins(cid, sid)
      .then((r) => setPinState({ cid, sid, rows: r.pins }))
      .catch(() => setPinState({ cid, sid, rows: [] })),
    [cid, sid]);

  /** Toggle one scene-scoped rule for `ref`.
   *
   *  Toggling rather than always setting is what makes one button per mode
   *  enough: clicking Pin on something already pinned is the reader undoing it,
   *  and clicking Exclude on something pinned REPLACES the rule (the store keys
   *  one rule per scope and target), which is the same thing they would mean by
   *  it. TTL is deliberately absent here — the quick toggle is the standing
   *  version, and a countdown is set from the section below, where the number
   *  is visible. */
  async function toggleRule(ref: string, mode: "pin" | "exclude") {
    setError(null);
    const held = pins.find((p) => p.ref === ref && p.scope === "scene" && p.mode === mode);
    try {
      if (held) await api.removePin(cid, ref, "scene", sid);
      else await api.setPin(cid, { ref, mode, scope: "scene", sid });
      await reloadPins();
      onSceneChanged();   // the prompt changed, so the context panel has moved
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  async function addRule() {
    if (!pinKind || !pinTarget) return;
    setError(null);
    const ttl = pinScope === "scene" ? Math.max(parseInt(pinTtl, 10) || 0, 0) : 0;
    try {
      await api.setPin(cid, { ref: `${pinKind}:${pinTarget}`, mode: pinMode,
                              scope: pinScope, sid, ttl_posts: ttl });
      setPinTarget("");
      setPinTtl("");
      await reloadPins();
      onSceneChanged();
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  async function dropRule(p: PinRule) {
    setError(null);
    try {
      await api.removePin(cid, p.ref, p.scope, p.sid);
      await reloadPins();
      onSceneChanged();
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  /** Whether a rule of this mode is in force for `ref`, at either scope — the
   *  toggle reports what the prompt is actually doing, not just what this scene
   *  asked for. */
  function ruleOn(ref: string, mode: "pin" | "exclude"): boolean {
    return pins.some((p) => p.ref === ref && p.mode === mode);
  }

  /** A campaign-wide rule naming `ref`, which is what makes the row toggles
   *  read-only for it.
   *
   *  Without this the button showed pressed (the rule IS in force) while the
   *  click below only ever touched scene scope: pressing it filed a redundant
   *  scene rule and changed nothing visible, and pressing it again removed that
   *  rule and still changed nothing. A standing campaign rule is lifted where it
   *  was set — the list below, which shows it with its own ✕ — and overridden
   *  for one scene from the picker there. */
  function standingRule(ref: string): PinRule | undefined {
    return pins.find((p) => p.ref === ref && p.scope === "campaign");
  }

  useEffect(() => {
    reloadCast();
    reloadPins();
    api.listAppearances(cid).then(setRoster).catch(() => setRoster([]));
    api.getSceneLocation(cid, sid).then(setSetting).catch(() => setSetting(null));
    api.getSceneContext(cid, sid).then(setCtx).catch(() => setCtx(null));

    api.getChronicle(cid).then(setRecap).catch(() => setRecap([]));
    const token = ++readToken.current;
    const seenWrites = writeSeq.current;
    const key = `${cid}/${sid}`;
    currentKey.current = key;
    const mine = () => readToken.current === token && writeSeq.current === seenWrites;
    const seenBreakWrites = breakSeq.current;
    const breakMine = () => readToken.current === token && breakSeq.current === seenBreakWrites;
    setRollingUnread(false);
    // Cleared up front like `rollingUnread`, not just on the next success:
    // otherwise a scene whose read failed leaves "could not be read" standing
    // over the NEXT scene until its own read lands.
    setBreakUnread(false);
    // The previous record's summary is deliberately NOT cleared here: this
    // effect also re-runs on `refreshKey`, i.e. twice per turn, and blanking
    // would flash "No summary yet" over a summary that is about to come back.
    // The stamp below is what makes that safe.
    api.getRollingSummary(cid, sid)
      .then((data) => {
        if (!mine()) return;
        setRolling({ key, data });
        // A read that arrived is also the answer to an earlier failure. Review
        // caught that keying the banner by record was not enough on its own: a
        // manual refold can fail and a later automatic one succeed, and the
        // banner would go on reporting a failure the panel has since recovered
        // from — over the very summary that proves it did.
        setRollingError((e) => (e?.key === key ? null : e));
      })
      .catch(() => { if (mine()) setRollingUnread(true); });
    // Read beside the summary, on the same select and the same `refreshKey`
    // bump. A failed read leaves whatever was there: unlike the summary there
    // is no "may be behind" to say, because a standing proposal is about a
    // prefix of the transcript and stays true about that prefix — what a failed
    // read costs is a NEW proposal, which the next read or the next turn brings.
    api.getSceneBreak(cid, sid)
      .then((data) => {
        if (!breakMine()) return;
        setBreakState({ key, data });
        setBreakUnread(false);
        setBreakError((e) => (e?.key === key ? null : e));
      })
      .catch(() => { if (breakMine()) setBreakUnread(true); });
    reloadWhen();
    reloadCfg();
  }, [cid, sid, refreshKey, reloadWhen, reloadCfg, reloadCast, reloadPins]);

  // The picker's options for the kind currently selected, fetched once per kind
  // per campaign. Actors come from lists the panel already holds.
  const pinOptionKey = `${cid}/${pinKind}`;
  useEffect(() => {
    if (!pinKind || pinKind === "characters" || pinKind === "pcs" || pinOptions[pinOptionKey]) return;
    let live = true;
    api.listEntities({ kind: "campaign", id: cid }, pinKind)
      .then((rows) => {
        if (live) setPinOptions((prev) => ({ ...prev,
          [pinOptionKey]: rows.map((r) => ({ id: r.id, name: r.name })) }));
      })
      .catch(() => { if (live) setPinOptions((prev) => ({ ...prev, [pinOptionKey]: [] })); });
    return () => { live = false; };
  }, [cid, pinKind, pinOptionKey, pinOptions]);

  const pinChoices: { id: string; name: string }[] =
    pinKind === "characters" ? chars
    : pinKind === "pcs" ? pcs
    : pinKind ? pinOptions[pinOptionKey] ?? []
    : [];

  // Its own effect rather than a line in the load above, because it is the one
  // request here whose late answer can be *wrong* rather than merely stale: a
  // superseded briefing describes a different scene's cast. `live` drops it.
  useEffect(() => {
    let live = true;
    api.sceneBriefing(cid, sid)
      .then((b) => { if (live) setBrief({ cid, sid, data: b }); })
      .catch(() => { if (live) setBrief({ cid, sid, data: NO_BRIEFING }); });
    return () => { live = false; };
  }, [cid, sid, refreshKey]);

  // Its own effect, with the same `live` cleanup and for a second reason on top
  // of the briefing's. `fresh: true` stops the post-generation read from JOINING
  // a pre-generation request, but it does not order two independent ones: the
  // initial load and the refresh a completed turn triggers are both in flight,
  // and if the newer answers first the older `.then` overwrites it with rows
  // that predate the turn — leaving Turn history one turn behind until the next
  // refresh happens to arrive in order. The cleanup makes the superseded
  // response a no-op instead.
  useEffect(() => {
    let live = true;
    api.listScenePrompts(cid, sid)
      .then((r) => { if (live) setTurns({ cid, sid, rows: r.entries }); })
      .catch(() => { if (live) setTurns({ cid, sid, rows: [] }); });
    return () => { live = false; };
  }, [cid, sid, refreshKey]);

  // Which scene is on screen *now*, readable from a fetch that started under a
  // previous one — the same "a late answer is wrong, not just stale" problem
  // the briefing effect above solves with its `live` flag. A ref rather than
  // that pattern because `showTurn` is triggered by a click rather than by an
  // effect, so there is no cleanup to close over; and a ref rather than the
  // props, because `showTurn` closes over the ones it was created with —
  // comparing those to themselves would be a guard that is always satisfied.
  //
  // Campaign AND scene. Scene numbering is campaign-local, so two campaigns
  // routinely have a live scene under the same id, and a scene-only check would
  // wave the first campaign's prompt through into the second's inspector.
  const liveScene = useRef(`${cid}/${sid}`);
  // The entry the reader most recently asked for; a response for anything else
  // is superseded and dropped. See `showTurn`. `null` means "live" — going back
  // is a newer selection too, so a detail fetch still in flight when the reader
  // returns to the live panel must not reopen the past-turn view over them.
  const wantedTurn = useRef<string | null>(null);
  // Keyed on the scene alone, deliberately NOT on refreshKey: a turn landing
  // must not yank the reader out of a past turn they are in the middle of
  // reading. Changing scene must, since the entry belongs to the old one.
  //
  // A LAYOUT effect for the same reason as `CastPanel`'s `live`: passive
  // effects are scheduled in a separate task, leaving a gap after the commit
  // in which a detail response for the scene just left can still read a ref
  // that agrees with it. Here the render-time scoping of `frozen` makes such a
  // write inert rather than harmful, so this is closing the window rather than
  // fixing a live defect — but two refs guarding the same race should not
  // disagree about when they update.
  useLayoutEffect(() => {
    liveScene.current = `${cid}/${sid}`;
    wantedTurn.current = null;
    setFrozen(null);
    setCompare("");
  }, [cid, sid]);

  const showTurn = useCallback(async (eid: string) => {
    setError(null);
    // The scene guard alone is not enough WITHIN a scene: click turn A then
    // turn B, and if A resolves second it overwrites B — the panel showing the
    // turn the reader did not pick last. So the newest request wins, tracked by
    // the entry it asked for.
    wantedTurn.current = eid;
    // Comparing against the LIVE preview survives moving to another turn: "at
    // which turn did this section change?" is walked by clicking down the rail,
    // and clearing the comparison at every step would make the reader re-pick
    // it each time. Comparing against a specific turn does not, because the
    // turn just clicked can BE that one, and a diff of an entry against itself
    // is a panel that has quietly stopped answering.
    setCompare((c) => (c === LIVE_SIDE ? c : ""));
    const current = () => liveScene.current === `${cid}/${sid}` && wantedTurn.current === eid;
    try {
      const snapshot = await api.getScenePrompt(cid, sid, eid);
      // Switching scenes mid-flight would otherwise land the old scene's prompt
      // in the new scene's panel — the exact confusion the backend refuses to
      // serve (the detail route scopes each entry to its scene), so the client
      // must not reintroduce it from the other end.
      if (current()) setFrozen({ cid, sid, data: snapshot });
    } catch (err: any) {
      if (!current()) return;
      // The likeliest failure is the retention window having evicted it while
      // the list was on screen, which is a 404 and not worth a scary banner.
      setFrozen(null);
      setError(err?.status === 404
        ? "That turn's prompt has aged out of the log."
        : (err?.detail ?? String(err)));
    }
  }, [cid, sid]);

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
      await api.setCalendarConfig({ kind: "campaign", id: cid }, {
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
      askRolling();     // ...which is a post, and may be the Nth (#85)
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
        // ...but those effects only READ, and a read never evaluates the gate.
        // The first date is set SILENTLY (no "Time passes…" line, which is why
        // this branch exists at all), so nothing else about this write asks —
        // and it still moves the scene's date FACT, which is part of the fold's
        // validity key. Ask against the NEW id: the old one no longer names a
        // file. Adopted first, so `currentKey` has moved by the time the answer
        // lands and the panel accepts it (#85).
        askRolling(res.id);
        return;
      }
      await reloadWhen();
      onSceneChanged();  // surface the "Time passes…" transition line in the stream
      askRolling();      // ...which is a post, and may be the Nth (#85)
    } catch (err: any) {
      setError(err.detail ?? String(err));
    } finally {
      onRenaming?.(false);
    }
  }

  // The snapshot only counts while it belongs to what is on screen.
  const seen = useMemo(
    () => (frozen && frozen.cid === cid && frozen.sid === sid ? frozen.data : null),
    [frozen, cid, sid]);
  // A selected past turn wins over the live composition — that IS the feature.
  // Both are the same shape, so everything below reads one variable.
  const shown = useMemo(() => seen ?? ctx, [seen, ctx]);
  // Empty until the rows on hand are this scene's, so a switch shows "no
  // captured turns yet" for a moment rather than the previous scene's list.
  const shownTurns = useMemo(
    () => (turns && turns.cid === cid && turns.sid === sid ? turns.rows : []),
    [turns, cid, sid]);
  // The comparison itself (#130). An effect rather than a fetch in the picker's
  // `onChange`, because only ONE end of it is frozen: against the live preview
  // the answer moves with the store, and `refreshKey` is bumped by the very
  // turn that moved it. A diff of a preview that has since changed describes a
  // prompt nobody would send.
  const compareWith = seen?.id;
  // Which turn landing this comparison actually depends on. A turn-against-turn
  // diff is frozen at BOTH ends, so a completed turn cannot change it and
  // re-reading it on every `refreshKey` would be a request that can only return
  // what is already on screen. Against the live preview it is the whole point.
  const liveKey = compare === LIVE_SIDE ? refreshKey : 0;
  useEffect(() => {
    if (!compareWith || !compare) { setDiff(null); return; }
    let alive = true;
    // Ordered oldest-first before it is asked for. The route takes the two ends
    // as given and does not reorder them — that is deliberate there, so a
    // caller who means "this one as the before" gets it — but a READER picking
    // an earlier turn out of a newest-first list means "what changed since
    // then", and answering with insertions that are things the older prompt had
    // is a diff running backwards through time. Ids are a monotonic counter and
    // the live preview is newer than any of them.
    const [older, newer] = compare === LIVE_SIDE || Number(compare) > Number(compareWith)
      ? [compareWith, compare] : [compare, compareWith];
    api.getScenePromptDiff(cid, sid, older, newer)
      .then((d) => {
        if (alive)
          setDiff({ cid, sid, eid: compareWith, against: compare, key: liveKey, data: d });
      })
      .catch((err: { status?: number; detail?: string }) => {
        if (!alive) return;
        // Either end can have been evicted by the retention window while the
        // picker was open, which is a 404 and routine rather than alarming.
        setDiff(null);
        setCompare("");
        // No `String(err)` fallback, unlike its neighbours above: those read a
        // rejection typed `any`, and stringifying THIS one would print
        // "[object Object]" at the reader rather than a reason.
        setError(err.status === 404
          ? "One of those turns has aged out of the log."
          : (err.detail ?? "Those turns could not be compared."));
      });
    return () => { alive = false; };
  }, [cid, sid, compareWith, compare, liveKey]);
  // Held to both ends as well as to the scene: the reader can move to another
  // turn while a comparison is in flight, and an answer for the one they left
  // would otherwise be painted under the one they arrived at.
  const shownDiff = useMemo(
    () => (diff && diff.cid === cid && diff.sid === sid
           && diff.eid === compareWith && diff.against === compare ? diff.data : null),
    [diff, cid, sid, compareWith, compare]);
  // Deliberately NOT part of the guard above: a turn landing must not blank the
  // panel, because what it would fall back to is this turn's BREAKDOWN — a flip
  // to a different kind of content, and to one that is refetching too. So the
  // stale comparison stays on screen and says it is stale, which also covers
  // the case a blanking guard could not: a request that never answers.
  const recomputing = !!shownDiff && diff !== null && diff.key !== liveKey;
  // A transition line is a post like any other: a location move, a time advance
  // and a cast join/leave all append one (`scenes/moment.py`,
  // `appearances/transitions.py`), so any of them can be the post that crosses
  // the threshold. `onSceneChanged` only re-READS — a GET never evaluates the
  // gate — so review was right that leaving these out let a transition-crossed
  // threshold sit until some later generated turn. The reason given for
  // omitting them last round (that `refreshKey` would cover it) was simply
  // wrong.
  //
  // Not forced, so this is the same cheap question the play loop asks: a scene
  // short of the threshold answers `refreshed: false` having reached no
  // provider. Not awaited, and its rejection swallowed — none of these actions
  // should fail because a summary could not be written.
  //
  // Bounded like the turn, roll and check paths, and for the same reason: the
  // fold must not swallow a player post the transition did not include, because
  // the reply that answers it is an APPEND and would stay out of the "current"
  // summary until another threshold. This panel does not know the transcript's
  // length — the transition endpoints return a result, not a count — so it asks
  // for it, and the read's `total` is the bound.
  //
  // Honest about the residue: a post landing between that read and the POST is
  // still inside the bound. The window shrinks from a whole provider round trip
  // to two adjacent calls, which is worth having; closing it entirely would mean
  // the four transition endpoints reporting their resulting length, a wider
  // change than this one should make.
  //
  // `id` defaults to the scene this panel is showing, and is passed explicitly
  // by the one caller whose write RENAMES the scene out from under the prop:
  // the id in scope there is already stale, so asking with it would 404 while
  // the scene it means sits under a new name with a summary nobody refolded.
  function askRolling(id: string = sid) {
    const key = `${cid}/${id}`;
    const ticket = ++writeTicket.current;
    // The scene-break question rides the same read (#84): a location move and a
    // time advance are two of the three signals it scores, and both land here
    // rather than through `CampaignView`'s play-loop hook. Chained off the same
    // `getRollingSummary` bound rather than reading a second time — one read,
    // one boundary, so the two questions cannot be asked about different
    // transcripts. Rejection swallowed, like the fold's: no transition should
    // fail because a suggestion could not be written.
    const bounded = api.getRollingSummary(cid, id);
    bounded
      .then((seen) => api.askSceneBreak(cid, id, false, seen.total))
      .then((r) => { if (r.asked && currentKey.current === key) reloadBreak(id); })
      .catch(() => {});
    bounded
      .then((seen) => api.refreshRollingSummary(cid, id, false, seen.total))
      .then((data) => {
        if (currentKey.current !== key || !data.refreshed) return;
        if (ticket < writeInstalled.current) return;   // a newer refold already landed
        writeInstalled.current = ticket;
        writeSeq.current += 1;
        setRolling({ key, data });
        // This answer is the server's reconciled view of a fold that just
        // succeeded, so the two ways the panel says "what you are reading may be
        // behind" are both now false and must go with it. Review caught that
        // leaving them meant a provider failure from an earlier manual refresh,
        // or a failed read from the transition before this one, kept its warning
        // up beside prose the POST had just made current -- with nothing later
        // scheduled to retire either message. Scoped to this record, like every
        // other write here: a failure on the scene the reader LEFT is not this
        // one's to clear.
        setRollingUnread(false);
        setRollingError((e) => (e && e.key === key ? null : e));
      })
      .catch(() => {});
  }

  async function refreshRolling() {
    setRollingError(null);
    // Captured OUTSIDE the try, so success, failure and the `finally` all judge
    // themselves against the record this refold was started for.
    const key = `${cid}/${sid}`;
    // Ticketed with the automatic refolds, in the same sequence, so the two
    // orders cannot disagree: an automatic answer issued before this button was
    // pressed may not overwrite what the button installs, however late it lands.
    const ticket = ++writeTicket.current;
    setRollingBusy(key);
    try {
      // `force`, always: this button exists so the player can ask for a summary
      // *now*, including when the automatic refresh is switched off. The
      // server still declines to spend a call when nothing has happened since
      // the last one, and says so in `refreshed`.
      const data = await api.refreshRollingSummary(cid, sid, true);
      // Retired only if the reader has moved on — NOT on the read token. This
      // answer is authoritative: the server wrote the store and reconciled what
      // it returned, so it outranks any read issued before now however recently.
      // `writeSeq` is what tells those reads so.
      if (currentKey.current !== key) return;
      // Authoritative, but not exempt from the ordering: an automatic refold
      // issued AFTER this button was pressed describes a later transcript, and
      // "the server wrote this" is true of both answers. Same rule either way.
      if (ticket < writeInstalled.current) return;
      writeInstalled.current = ticket;
      writeSeq.current += 1;
      setRolling({ key, data });
      setRollingUnread(false);
    } catch (err: any) {
      // Guarded like the success path, because `error` is shared by every action
      // in this panel and the scene-change effect does not clear it: a refold
      // that failed for the record the reader LEFT would otherwise sit as a
      // banner over the one they are on until something else cleared it.
      if (currentKey.current !== key) return;
      // Reported, never destructive: the summary already on screen is still the
      // best thing anyone has, so a failed refold leaves it exactly where it is.
      setRollingError({ key, err });
    } finally {
      // Only if it is still ours. The reader can leave and start a refold on
      // another record while this one is out, and clearing unconditionally
      // would free that one's button while its call is still running.
      setRollingBusy((busy) => (busy === key ? null : busy));
    }
  }

  // Re-read after a question the panel did not itself ask. `id` is passed
  // explicitly by `askRolling`'s one caller whose write RENAMES the scene, for
  // the reason stated there: the id in the prop is already stale.
  function reloadBreak(id: string) {
    const key = `${cid}/${id}`;
    const seen = breakSeq.current;
    api.getSceneBreak(cid, id)
      .then((data) => {
        // A read, so it yields to any write that landed after it was issued —
        // the same rule the scene-select effect's read follows.
        if (currentKey.current !== key || breakSeq.current !== seen) return;
        setBreakState({ key, data });
        setBreakUnread(false);
      })
      .catch(() => {
        if (currentKey.current === key && breakSeq.current === seen) setBreakUnread(true);
      });
  }

  // The panel's own button: ask NOW, including when the automatic cadence is
  // switched off. The server still declines to spend a call when nothing has
  // happened since the last question, and says so in `asked`.
  async function askBreakNow() {
    const key = `${cid}/${sid}`;
    setBreakError(null);
    setBreakBusy(key);
    try {
      const data = await api.askSceneBreak(cid, sid, true);
      // Guarded on the reader still being on this record, like every other
      // post-await write here. Installed whether or not it `asked`: a refusal
      // is still the server's reconciled view of the scene, and dropping it
      // would leave the panel showing a score from before the last turn.
      if (currentKey.current !== key) return;
      breakSeq.current += 1;
      setBreakState({ key, data });
      setBreakUnread(false);
    } catch (err: any) {
      // Reported, never destructive: a standing proposal is still the best
      // thing anyone has, so a failed question leaves it exactly where it is.
      if (currentKey.current !== key) return;
      setBreakError({ key, err });
    } finally {
      setBreakBusy((busy) => (busy === key ? null : busy));
    }
  }

  // "Not here." The watermark moves server-side, so the same posts cannot
  // re-earn the same suggestion on the next turn — which is why this is a
  // request rather than a local `setBreakState(undefined)`.
  async function dismissBreak() {
    const key = `${cid}/${sid}`;
    setBreakError(null);
    setBreakBusy(key);
    try {
      const data = await api.dismissSceneBreak(cid, sid);
      if (currentKey.current !== key) return;
      breakSeq.current += 1;
      setBreakState({ key, data });
      setBreakUnread(false);
    } catch (err: any) {
      if (currentKey.current !== key) return;
      setBreakError({ key, err });
    } finally {
      setBreakBusy((busy) => (busy === key ? null : busy));
    }
  }

  const nameOf = (a: Actor) => names[`${a.kind}/${a.id}`] ?? a.id;

  // Deliberately keyed on cid+sid and NOT on `refreshKey`, which is the same
  // call `LedgerPanel` makes and states its reason for: a refresh re-reads the
  // SAME scene, so blanking on it would tear the section down and rebuild it
  // after every save and every cast edit, shifting every section below it, to
  // show mostly the same rows back. The cost is that a resolved commitment can
  // linger for the length of one request (Codex review); the section is a hint
  // surface whose rows are already "what was true when the scene opened", so a
  // brief stale flag is inside its contract and a flicker on every save is not.
  const briefing = brief && brief.cid === cid && brief.sid === sid ? brief.data : null;
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
      {/* The scene being PLAYED, where "Story so far" above is the scenes that
          ended. Display-only — this text is never added to the prompt (#85). */}
      <SideSection id="scenesofar" title="Scene so far" collapsed={!!collapsed.scenesofar}
                   onToggle={toggleSection}>
        {(() => {
          // Only this campaign-and-scene's answer is shown: another record's is
          // not an answer about this one.
          const r = rolling?.key === `${cid}/${sid}` ? rolling.data : undefined;
          // The prose and the status line are decided separately, and review
          // caught why they have to be. A failed read used to be suppressed
          // whenever ANY cached value matched the key — which is every reread of
          // the same scene — so the panel kept presenting the coverage it read
          // before the turn as current. The prose is still the best thing anyone
          // has and stays; what it may no longer claim is to be up to date.
          const status = rollingUnread
            ? "The latest read failed, so this may be behind."
            : r?.stale
              ? "Posts it covered have changed since — it may be out of date."
              : r && `Covers ${r.at} of ${r.total} posts.`;
          if (!r?.summary) {
            return (
              <div className="field-hint">
                {rollingUnread
                  ? "The summary could not be read."
                  : `No summary yet${r && r.every > 0
                      ? ` — one is written every ${r.every} posts.` : "."}`}
              </div>
            );
          }
          return (
            <>
              <div className="field-hint">{r.summary}</div>
              {status && <div className="field-hint">{status}</div>}
            </>
          );
        })()}
        {rollingError?.key === `${cid}/${sid}` && (
          <div className="banner"><ErrorNote err={rollingError.err} /></div>
        )}
        <div className="form-actions">
          {/* Held while a turn is streaming into this scene, like the two date
              actions above. A chat appends the player's post before streaming
              and the reply only when it lands, so a refold in between covers an
              unanswered post — and the reply is an APPEND, which leaves the
              digest valid, so it would stay out of the "current" summary until
              the next threshold came round. */}
          <button className="primary" onClick={refreshRolling}
                  disabled={rollingBusy === `${cid}/${sid}` || sceneLocked}
                  title={sceneLocked ? LOCKED_WHILE_GENERATING : undefined}>
            {rollingBusy === `${cid}/${sid}` ? "Summarizing…" : "Refresh now"}
          </button>
        </div>
      </SideSection>

      {/* The scene-break detector (#84). Directly under "Scene so far", which
          is the other thing this panel says about the scene being PLAYED — and
          the two answer adjacent questions: what has happened, and whether
          enough has. Nothing here ends or splits anything; the only actions are
          asking and declining. */}
      <SideSection id="scenebreak" title="Break here?" collapsed={!!collapsed.scenebreak}
                   onToggle={toggleSection}>
        {(() => {
          // Only this campaign-and-scene's answer, `rolling`'s rule: another
          // record's proposal is not an answer about this one.
          const b = breakState?.key === `${cid}/${sid}` ? breakState.data : undefined;
          if (breakUnread && !b) {
            return <div className="field-hint">The detector could not be read.</div>;
          }
          // A standing answer outranks "the feature is off", and review caught
          // why the other order was a bug rather than a preference: `Ask now`
          // works when the cadence is 0 — that is the whole point of a button
          // that says now — so putting the off-notice first meant the player
          // pressed it, paid for a call, and watched the panel go on saying
          // "Turned off".
          // Said beside either answer, never instead of it: the prose is still
          // the best thing anyone has, and what it may no longer do is claim to
          // be about the scene on screen. `rollingUnread`'s wording for the
          // failed-read case, `stale`'s for the moved-transcript one — they are
          // different facts and the panel says both.
          const behind = breakUnread
            ? "The latest read failed, so this may be behind."
            : b?.stale
              ? "Posts it was about have changed since — it may no longer apply."
              : "";
          if (b?.verdict === "yes") {
            return (
              <>
                <div className="field-hint">{b.reason}</div>
                {b.title && (
                  <div className="field-hint">Next scene, perhaps: “{b.title}”</div>
                )}
                {behind && <div className="field-hint">{behind}</div>}
              </>
            );
          }
          if (b?.verdict === "no") {
            return (
              <>
                <div className="field-hint">Not yet — the scene is still mid-beat.</div>
                {b.reason && <div className="field-hint">{b.reason}</div>}
                {behind && <div className="field-hint">{behind}</div>}
              </>
            );
          }
          if (b && b.every === 0) {
            return (
              <div className="field-hint">
                Turned off — set “Scene-break check” in Configuration to switch it on.
                <br />Ask now still works.
              </div>
            );
          }
          // Nothing asked yet. The signals are shown even below the bar,
          // because "what the detector can see" is the honest answer to a
          // reader wondering why it has said nothing — and it is also what
          // makes the Ask now button legible.
          return (
            <>
              <div className="field-hint">
                {breakUnread ? "The latest read failed, so this may be behind."
                  : b ? `Nothing to suggest yet — checked every ${b.every} posts.`
                      : "Nothing to suggest yet."}
              </div>
              {b?.signals.map((sig) => (
                <div className="field-hint" key={sig.kind}>{sig.detail}</div>
              ))}
            </>
          );
        })()}
        {breakError?.key === `${cid}/${sid}` && (
          <div className="banner"><ErrorNote err={breakError.err} /></div>
        )}
        <div className="form-actions">
          {/* Held while a turn is streaming into this scene, like the summary's
              own button and the two date actions: a question asked over a
              half-written turn is asking about a beat whose reply has not
              arrived. */}
          <button className="primary" onClick={askBreakNow}
                  disabled={breakBusy === `${cid}/${sid}` || sceneLocked}
                  title={sceneLocked ? LOCKED_WHILE_GENERATING : undefined}>
            {breakBusy === `${cid}/${sid}` ? "Asking…" : "Ask now"}
          </button>
          {breakState?.key === `${cid}/${sid}` && breakState.data.verdict === "yes" && (
            <button onClick={dismissBreak} disabled={breakBusy === `${cid}/${sid}`}>
              Not here
            </button>
          )}
        </div>
      </SideSection>

      <SideSection id="cast" title="Active characters" collapsed={!!collapsed.cast} onToggle={toggleSection}>
        {cast.length === 0 && <div className="field-hint">No one cast yet.</div>}
        {cast.map((a) => {
          // The roster carries the locked version for either actor kind, and
          // so does the image route, so a PC gets a portrait here too (#219).
          const ver = roster.find((r) => r.kind === a.kind && r.id === a.id)?.version;
          const pc = a.role === "player";
          const ref = `${a.kind}:${a.id}`;
          return (
            <div className="inspector-row-item" key={`${a.kind}/${a.id}`}>
              <button className={"inspector-row" + (pc ? " pc" : "")}
                      onClick={() => setDrawer({ type: "actor", kind: a.kind, id: a.id })}>
                <Portrait src={ver ? api.actorImageUrl({ kind: "campaign", id: cid },
                                                            a.kind, a.id, ver, "avatar") : null}
                          name={nameOf(a)} />
                <span className="inspector-name">{nameOf(a)}</span>
                <span className="role-chip">{pc ? "player" : "npc"}</span>
              </button>
              {/* Two toggles rather than one control with a mode, because they
                  are opposite requests about the same person and both have to
                  be reachable in one click from the row they are about (#129).
                  `aria-pressed` carries the state: the glyphs alone would leave
                  a screen reader with two identically-named buttons. */}
              <button className={"inspector-row-pin" + (ruleOn(ref, "pin") ? " on" : "")}
                      aria-pressed={ruleOn(ref, "pin")} disabled={!!standingRule(ref)}
                      aria-label={`Pin ${nameOf(a)} in the prompt`}
                      title={standingRule(ref) ? "Set for the whole campaign — change it below"
                                               : "Keep in the prompt under budget pressure"}
                      onClick={() => toggleRule(ref, "pin")}>📌</button>
              <button className={"inspector-row-pin" + (ruleOn(ref, "exclude") ? " on" : "")}
                      aria-pressed={ruleOn(ref, "exclude")} disabled={!!standingRule(ref)}
                      aria-label={`Exclude ${nameOf(a)} from the prompt`}
                      title={standingRule(ref) ? "Set for the whole campaign — change it below"
                                               : "Keep out of the prompt"}
                      onClick={() => toggleRule(ref, "exclude")}>🚫</button>
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
        {/* "Who should appear" (#96), under the picker it shortcuts. Takes the
            cast, NOT `refreshKey`: the scan reads these characters' cards and
            never the transcript, so keying it on the turn counter would buy a
            request per turn to be told the same thing. */}
        <SuggestedCast cid={cid} sid={sid} cast={cast}
                       nameOf={(id) => names[`characters/${id}`]
                                       ?? chars.find((c) => c.id === id)?.name ?? id}
                       onCast={() => {
                         reloadCast();
                         onSceneChanged();
                         askRolling();   // a join appends a transition post (#85)
                       }} />
      </SideSection>

      {/* Pins & excludes (#129). Directly under the cast, because the row
          toggles above file their rules into this list and a reader needs to
          see where they went — and above Context, which is where the effect
          shows up. */}
      <SideSection id="pins" title="Pins & excludes" collapsed={!!collapsed.pins}
                   onToggle={toggleSection}
                   extra={pins.length > 0 ? <span className="chip on">{pins.length}</span> : null}>
        {pins.length === 0 && (
          <div className="field-hint">
            Nothing pinned. A pin keeps something in the prompt even when the
            budget is tight; an exclude keeps it out.
          </div>
        )}
        {pins.map((p) => (
          <div className="inspector-row-item" key={`${p.scope}/${p.sid}/${p.ref}`}>
            <div className="pin-row">
              <span className={"chip on pin-mode " + p.mode}>
                {p.mode === "pin" ? "pinned" : "excluded"}
              </span>
              <span className="inspector-name">{p.name}</span>
              <span className="field-hint pin-meta">
                {p.kind}
                {p.scope === "campaign" ? " · campaign" : ""}
                {p.remaining !== null ? ` · ${p.remaining} post${p.remaining === 1 ? "" : "s"} left` : ""}
                {p.missing ? " · deleted" : ""}
              </span>
            </div>
            <button className="inspector-row-remove" aria-label={`Lift the rule on ${p.name}`}
                    onClick={() => dropRule(p)}>✕</button>
          </div>
        ))}
        <div className="picker">
          <select aria-label="What to pin or exclude" value={pinKind}
                  onChange={(e) => { setPinKind(e.target.value as PinKind); setPinTarget(""); }}>
            <option value="">— kind —</option>
            {PIN_KINDS.map((k) => <option key={k} value={k}>{k}</option>)}
          </select>
          <select aria-label="Record to pin or exclude" value={pinTarget}
                  onChange={(e) => setPinTarget(e.target.value)}>
            <option value="">— pick —</option>
            {pinChoices.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
          </select>
          <select aria-label="Pin or exclude" value={pinMode}
                  onChange={(e) => setPinMode(e.target.value as "pin" | "exclude")}>
            <option value="pin">pin</option>
            <option value="exclude">exclude</option>
          </select>
          <select aria-label="Rule scope" value={pinScope}
                  onChange={(e) => setPinScope(e.target.value as "scene" | "campaign")}>
            <option value="scene">this scene</option>
            <option value="campaign">whole campaign</option>
          </select>
          {/* Only a scene rule can carry one: a TTL counts posts, and a
              campaign-wide rule has no scene to count them in (store/pins.py). */}
          {pinScope === "scene" && (
            <input aria-label="Posts to keep the rule for" type="number" min="0"
                   placeholder="posts (blank = keep)" value={pinTtl}
                   onChange={(e) => setPinTtl(e.target.value)} />
          )}
          {/* "+ Add rule", not "+ Add": the cast picker two sections up has an
              Add of its own, and one rail with two identically-named buttons is
              ambiguous to a screen reader before it is ambiguous to a test. */}
          <button className="primary" onClick={addRule}
                  disabled={!pinTarget}>+ Add rule</button>
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

      {/* Campaign-scoped, next to the scene's own When: advancing the clock is
          the same question one level up, and this is where a reader already
          comes to ask it (#100). Keyed on `refreshKey` so setting this scene's
          date — which carries the clock forward with it — shows up here too, and
          `onAdvanced` reloads When because a dateless scene takes its date
          pre-fill from the clock this just moved.
          Collapsed by default: this is the one section about the campaign rather
          than the scene, so it should not push the scene's own state down the
          rail until a reader asks for it. */}
      <SideSection id="clock" title="Campaign clock" collapsed={collapsed.clock ?? true}
                   onToggle={toggleSection}>
        <ClockPanel cid={cid} refreshKey={refreshKey} onAdvanced={reloadWhen} />
      </SideSection>

      {/* The clock's other half (#101): events are authored here and fired by
          the panel above — or by this scene taking a date past one. Keyed on the
          same `refreshKey` for that reason. Collapsed by default, like the clock
          it belongs to. */}
      <SideSection id="events" title="Scheduled events" collapsed={collapsed.events ?? true}
                   onToggle={toggleSection}>
        <EventsPanel cid={cid} refreshKey={refreshKey} />
      </SideSection>

      <SideSection id="weather" title="Weather" collapsed={!!collapsed.weather} onToggle={toggleSection}>
        {/* Renders nothing when there is no location or moment yet, the same
            way the When and Location sections above it degrade. */}
        <WeatherWidget cid={cid} sid={sid} refreshKey={refreshKey} />
      </SideSection>

      <SideSection id="context"
                   title={shownDiff ? "Context (compared)"
                          : frozen ? "Context (past turn)" : "Context"}
                   collapsed={!!collapsed.context} onToggle={toggleSection}
                   extra={!shownDiff && shown && contextPercent(shown, models) > 0
                     ? <span className="ctx-pct">{contextPercent(shown, models)}%</span> : undefined}>
        {seen && (
          <div className="ctx-frozen">
            <div className="field-hint">
              What the model saw · {taskLabel(seen.task)} · {whenLabel(seen.ts)}
            </div>
            {/* The comparison is offered only from a past turn, which is the
                only place it means anything: the live composition has nothing
                to be the "before" of. */}
            <label className="ctx-compare">
              <span>Compare with</span>
              {/* `setError(null)` here rather than in the effect below, the way
                  `showTurn` clears it: a failed comparison leaves a banner and
                  resets the picker, and without this the reader's retry renders
                  its diff underneath "those turns could not be compared". The
                  effect is the wrong place because it also runs on `refreshKey`,
                  where clearing a banner nobody dismissed would hide someone
                  else's error. */}
              <select value={compare}
                      onChange={(e) => { setError(null); setCompare(e.target.value); }}>
                <option value="">Nothing — show this turn</option>
                <option value={LIVE_SIDE}>The live preview</option>
                {shownTurns.filter((t) => t.id !== seen.id).map((t) => (
                  <option key={t.id} value={t.id}>
                    {taskLabel(t.task) + " · " + whenLabel(t.ts)}
                  </option>
                ))}
              </select>
            </label>
            <button className="ctx-frozen-back"
                    onClick={() => {
                      wantedTurn.current = null; setFrozen(null); setCompare("");
                    }}>
              ← Back to live context
            </button>
          </div>
        )}
        {/* A comparison replaces the breakdown rather than sitting under it.
            Both are long, the rail is one column, and a reader who asked what
            MOVED is not helped by having to scroll past what did not. */}
        {shownDiff ? <ContextDiff diff={shownDiff} recomputing={recomputing} />
                   : shown && <ContextBreakdown ctx={shown} models={models} />}
      </SideSection>

      {/* Directly under Context, and that adjacency is the point (#153): the
          section above is what grimoire COMPOSED, measured by a local
          tokenizer; this is what the provider said it charged for sending it.
          Collapsed by default — cost is a question a reader comes to ask, not
          one worth pushing the scene's own state down the rail for. */}
      <SideSection id="cost" title="Cost" collapsed={collapsed.cost ?? true}
                   onToggle={toggleSection}>
        <CostPanel cid={cid} sid={sid} refreshKey={refreshKey} />
      </SideSection>

      <SideSection id="turns" title="Turn history" collapsed={!!collapsed.turns} onToggle={toggleSection}>
        {/* The live Context section above always shows the composition as it
            stands NOW, which is not what any past turn was sent: chronicle,
            state, cast and world-info activation have all moved since. These
            are the frozen ones (#157). */}
        {shownTurns.length === 0 && (
          <div className="field-hint">No captured turns yet.</div>
        )}
        {shownTurns.map((t) => (
          <button key={t.id}
                  className={"inspector-row" + (seen?.id === t.id ? " on" : "")}
                  onClick={() => showTurn(t.id)}>
            <span className="inspector-name">{taskLabel(t.task)}</span>
            <span className="ctx-meta">{whenLabel(t.ts)}</span>
          </button>
        ))}
      </SideSection>

      {drawer && <RecordDrawer cid={cid} sid={sid} target={drawer} onClose={() => setDrawer(null)} />}
    </aside>
  );
}
