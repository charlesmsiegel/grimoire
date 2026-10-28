import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import {
  api, type Config, type ConfigUpdate, type PlayDecisionsInfo, type PromptLayoutSection,
} from "../api/client";
import { ColumnSection, PageShell } from "../components/PageShell";
import { RegexRulesEditor } from "../components/RegexRulesEditor";
import { PromptLayoutEditor } from "../components/PromptLayoutEditor";
import { ResponseTargetsPicker } from "../components/ResponseTargetsPicker";
import { ThemePicker } from "../components/ThemePicker";
import { isDecision } from "../components/turnLabels";
import { useThemeSetting } from "../theme/useThemeSetting";
import { DRAFT_FIELDS, GROUPS, LINKS, SECTIONS, type Draft, type DraftField, type SectionId, type SectionDef } from "../configSections";
import { ContextSection } from "./configView/ContextSection";
import { InstallSections } from "./configView/InstallSections";
import { NumField } from "./configView/NumField";
import { draftOf, sameLayout, type Probe } from "./configView/settingsDraft";

/** Ids of panes that became pages of their own (Settings → Inference), and
 *  where each went, so a link written before still lands where its setting
 *  is. A Map rather than an object literal: the id comes from the address
 *  bar, and an object answers `toString` or `constructor` with what it
 *  inherits. */
const MOVED = new Map<string, string>([
  ["models", "/models"], ["semantic", "/models"], ["connection", "/models"],
  ["routing", "/models/edit"], ["samplers", "/presets"], ["pricing", "/models#rates"],
]);

/** Find one real prompt to draw the context bar against.
 *
 *  There is no campaign-independent source for this. Every context breakdown
 *  the store holds is scoped to a scene of a campaign — `GET
 *  /campaigns/{cid}/scenes/{sid}/context` for the live composition, and the
 *  frozen per-turn snapshots beside it — and Config is not inside a campaign.
 *  So the honest answer to "the last prompt" is the newest snapshot of the
 *  most recently played campaign's newest scene, NAMED as that campaign's, and
 *  nothing at all when there isn't one. It is never computed here: the numbers
 *  come out of the prompt log exactly as the packer wrote them.
 *
 *  Three chained reads, so it is deliberately not part of the page load: only
 *  the Context section shows the bar, and only that section pays for it. Any
 *  failure — no campaigns, a store with `prompt_log_depth` at 0, an api double
 *  that never heard of these routes — is the same answer as no data, because
 *  the alternative to a bar is no bar, never a made-up one. */
async function lastPrompt(): Promise<Probe> {
  try {
    const campaigns = await api.listCampaigns();
    // `activity` folds in the newest scene; `updated` alone only moves on
    // metadata writes. Same ranking the campaigns shelf uses.
    const stamp = (c: { activity?: string; updated: string }) => c.activity || c.updated || "";
    const newest = [...campaigns].sort((a, b) => stamp(b).localeCompare(stamp(a)))[0];
    if (!newest) return null;
    const scenes = await api.listScenes(newest.id);   // newest first
    if (!scenes.length) return null;
    const { entries } = await api.listScenePrompts(newest.id, scenes[0].id);   // newest first
    // The last TURN: a decision's prompt (a speaker pick, a scene-break check)
    // is not what a turn's budget is spent on.
    const turn = entries.find((e) => !isDecision(e));
    if (!turn) return null;
    const snapshot = await api.getScenePrompt(newest.id, scenes[0].id, turn.id);
    return { ctx: snapshot, campaign: newest.name };
  } catch {
    return null;
  }
}

export default function ConfigView() {
  const [config, setConfig] = useState<Config | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  // `?section=` opens one section from a link elsewhere. An id whose pane
  // became a page of its own (`MOVED`: `semantic`, `pricing`, ...) redirects
  // to that page. Anything that is neither a section id nor a moved one is
  // ignored and the page opens where it always has.
  const asked = useSearchParams()[0].get("section") ?? "";
  const navigate = useNavigate();
  const askedSection = SECTIONS.find((s) => s.id === asked)?.id ?? null;
  const movedTo = MOVED.get(asked) ?? null;
  useEffect(() => { if (movedTo) void navigate(movedTo, { replace: true }); }, [movedTo, navigate]);
  const [section, setSection] = useState<SectionId>(askedSection ?? "storage");
  // ...and a changed query is followed while the page stays mounted. Keyed on
  // the asked id, so the reader's own clicks in the column are not undone by a
  // re-render carrying the same query.
  useEffect(() => { if (askedSection) setSection(askedSection); }, [askedSection]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [probe, setProbe] = useState<Probe>(null);
  // Bumped when the store pointer moves, to remount anything describing the
  // old library rather than leave it showing a report about a folder the app
  // is no longer using.
  const [storeEpoch, setStoreEpoch] = useState(0);
  // A ref, not state: as state it would be a dependency of the effect that sets
  // it, so flipping it would re-run the effect and its cleanup would cancel the
  // read it had just started — the bar would never arrive.
  const probeStarted = useRef(false);
  /** The prompt layout is edited HERE rather than inside `PromptLayoutEditor`,
   *  which is presentational. The panel used to own its own draft and its own
   *  Save button, and that made the page lie: reorder a section, and the footer
   *  still read "no unsaved changes" while Save — the page's one Save — wrote
   *  everything except the reordering the reader had just done. One dirty
   *  count, one Save, or the affordance is a trap. */
  const [layout, setLayout] = useState<PromptLayoutSection[] | null>(null);
  const [layoutSaved, setLayoutSaved] = useState<PromptLayoutSection[] | null>(null);
  const [layoutFailed, setLayoutFailed] = useState(false);
  const layoutStarted = useRef(false);
  const [decisionsInfo, setDecisionsInfo] = useState<PlayDecisionsInfo | null>(null);
  const decisionsStarted = useRef(false);

  useEffect(() => {
    // This required read owns its failure; a page left before it settles has
    // no draft or error left to install. Optional section reads stay separate.
    let live = true;
    api.getConfig().then((c) => {
      if (!live) return;
      setConfig(c);
      setDraft(draftOf(c));
    }).catch((e: unknown) => {
      if (!live) return;
      setError(typeof e === "object" && e !== null && "detail" in e && typeof e.detail === "string"
        ? e.detail : "Could not load these settings");
    });
    return () => { live = false; };
  }, []);

  // Fetched when the Context section is first opened, not on mount: it is
  // three round trips into a campaign for one bar, and ten of the eleven
  // sections never show it.
  //
  // No liveness guard on the way back: this is a one-shot read of a record
  // that cannot change while the page is open, so there is no later response
  // for a stale one to install over, and a late `setProbe` on an unmounted
  // component is a no-op.
  useEffect(() => {
    if (section !== "context" || probeStarted.current) return;
    probeStarted.current = true;
    void lastPrompt().then(setProbe);
  }, [section]);

  // Same lazy rule as the probe: thirty rows fetched when the Prompt layout
  // section is first opened, never on mount.
  useEffect(() => {
    if (section !== "layout" || layoutStarted.current) return;
    layoutStarted.current = true;
    api.getPromptLayout()
      .then((l) => { setLayout(l.sections); setLayoutSaved(l.sections); })
      .catch(() => setLayoutFailed(true));
  }, [section]);

  // Whether the Decision model reports a distribution, read when Play
  // decisions is first opened: it resolves a route, which nine sections
  // never need.
  useEffect(() => {
    if (section !== "decisions" || decisionsStarted.current) return;
    decisionsStarted.current = true;
    api.getPlayDecisionsInfo().then(setDecisionsInfo).catch(() => setDecisionsInfo(null));
  }, [section]);

  const saved = config ? draftOf(config) : null;
  const dirty = draft && saved ? DRAFT_FIELDS.filter((f) => draft[f] !== saved[f]) : [];
  /** The layout counts as ONE unsaved change however many rows moved — it is a
   *  single stored document, and a count of moved rows would be a number the
   *  reader cannot act on row by row. */
  const layoutDirty = !!layout && !!layoutSaved && !sameLayout(layout, layoutSaved);
  const dirtyCount = dirty.length + (layoutDirty ? 1 : 0);
  const dirtyIn = (s: SectionDef) =>
    s.fields.some((f) => dirty.includes(f)) || (s.id === "layout" && layoutDirty);

  /** What a column row says it is currently set to, or "" for no answer.
   *
   *  The design gives every row its current value, and the reason is that a
   *  settings index of bare nouns makes you open each one to find out what it
   *  says. Not every row HAS a one-word answer, though, and inventing one is
   *  worse than leaving the slot empty -- so this covers the rows whose value
   *  is genuinely a word or a number, and the rest render nothing.
   *
   *  Read off the DRAFT, not the saved config, so a row shows what the form in
   *  front of you says. That is only reachable when the row is not dirty (see
   *  the call site), so the two can never contradict each other.
   */
  function valueOf(id: SectionId): string {
    switch (id) {
      case "logging": return draft?.log_level ?? "";
      case "backups": return draft?.backup_enabled === "on" ? "on" : "off";
      // A budget of 0 is no ceiling at all, which "0 tok" read as the opposite.
      case "context": return !draft?.context_budget ? ""
        : Number(draft.context_budget) === 0 ? "no ceiling" : `${draft.context_budget} tok`;
      case "timeouts": return draft?.llm_timeout ? `${draft.llm_timeout}s` : "";
      case "storage": return config?.data_dir ? "set" : "";
      case "appearance": return theme.mode;
      case "layout": return draft?.prompt_layout_enabled === "on" ? "on" : "off";
      // Unset reads as on, like the checkbox: the shipped default is on.
      case "tracker": return draft?.tracker === "off" ? "off" : "on";
      // The turn decision is the one that costs a call per reply, so it is
      // the one worth reading at a glance.
      case "decisions": return draft?.decide_turn_plan || "off";
      // Deliberately blank: the rate table, the presets and the response
      // preset are each a document of their own, and "12 entries" is a size
      // rather than a setting.
      default: return "";
    }
  }

  function edit(field: DraftField, value: string) {
    setDraft((d) => (d ? { ...d, [field]: value } : d));
  }

  /** The look is applied and stored in one act, through the hook every control
   *  that offers it shares. It is not part of this page's draft, so Save never
   *  writes it and Revert has nothing to put back — which is what stops a
   *  choice made from the header being undone by an unrelated save here. */
  const theme = useThemeSetting();

  function revert() {
    if (!saved) return;
    setDraft(saved);
    setLayout(layoutSaved);
    setError(null);
  }

  async function save() {
    if (!draft || !dirtyCount || busy) return;
    setBusy(true);
    setError(null);
    // Only the fields that actually changed: a whole-form PUT would carry
    // every value into an unlocked read-modify-write of one file and
    // overwrite anything another tab (or the Models page) moved while this
    // form sat open.
    const patch: ConfigUpdate = {};
    for (const f of dirty) patch[f] = draft[f];
    const sent = draft;
    // The layout first, and only when it changed: it is a separate document at
    // a separate route, and PUTting it on every Save would rewrite thirty rows
    // because someone edited a timeout.
    //
    // Its own try, deliberately: the settings catch below puts the theme
    // preview back, which is the right repair for a failed config write and a
    // non-sequitur for a failed layout one — the theme is not in this request.
    if (layoutDirty && layout) {
      try {
        const stored = await api.putPromptLayout(layout.map(
          (r) => ({ id: r.id, label: r.label, enabled: r.enabled })));
        setLayout(stored.sections);
        setLayoutSaved(stored.sections);
      } catch (e: unknown) {
        setError(typeof e === "object" && e !== null && "detail" in e && typeof e.detail === "string" ? e.detail : "Could not save the prompt layout");
        setBusy(false);
        return;   // the settings are not written either: one Save, one outcome
      }
    }
    // Nothing more to do when only the layout moved: an empty patch is a
    // read-modify-write of config.md that stores no field.
    if (!dirty.length) {
      setBusy(false);
      return;
    }
    try {
      const next = await api.putConfig(patch);
      setConfig(next);
      // The new baseline is what the server says it stored — but only the
      // fields nobody touched while the write was in flight. Save disables its
      // own buttons, not the inputs, so a keystroke landing during the request
      // is a real edit, and adopting the response wholesale would silently
      // throw it away.
      setDraft((d) => {
        const merged = draftOf(next);
        if (d) for (const f of DRAFT_FIELDS) if (d[f] !== sent[f]) merged[f] = d[f];
        return merged;
      });
    } catch (e: unknown) {
      setError(typeof e === "object" && e !== null && "detail" in e && typeof e.detail === "string" ? e.detail : "Could not save these settings");
    } finally {
      setBusy(false);
    }
  }

  async function resetLayout() {
    setBusy(true);
    try {
      const stored = await api.putPromptLayout([]);
      setLayout(stored.sections);
      setLayoutSaved(stored.sections);
    } catch {
      setLayoutFailed(true);
    } finally {
      setBusy(false);
    }
  }

  const current = SECTIONS.find((s) => s.id === section)!;

  const column = (
    <>
      <div className="column-head">
        <div className="eyebrow">Settings</div>
        {config && <div className="column-head-sub">{config.data_dir}</div>}
      </div>
      <ColumnSection label={LINKS[0].group}>
        {LINKS.map((l) => (
          <Link key={l.key} to={l.to} className="column-row">
            <span className="column-row-label">
              {l.label}
              {/* The dot is the state, so the state is also spelled out. */}
              {l.key === "inference-models" && config && (
                <>
                  <span className={"conn-dot " + (config.ready ? "ok" : "off")} aria-hidden> ●</span>
                  <span className="sr-only">{config.ready ? " ready" : " not ready"}</span>
                </>
              )}
            </span>
          </Link>
        ))}
      </ColumnSection>
      {GROUPS.map((group) => (
        <ColumnSection key={group} label={group}>
          {SECTIONS.filter((s) => s.group === group).map((s) => (
            <button key={s.id} className={"column-row" + (s.id === section ? " active" : "")}
                    onClick={() => setSection(s.id)}>
              <span className="column-row-label">
                {s.label}
                {/* `off` used to be appended to these two labels. It is the
                    row's VALUE now, in the slot every other row uses, so it is
                    said once and in the same place. */}
              </span>
              {/* The dot and the value share one slot, and the dot wins:
                  "this row has an edit you have not saved" is the more urgent
                  of the two, and showing the SAVED value beside an unsaved
                  edit would be the row contradicting the form. */}
              {dirtyIn(s) ? (
                <>
                  <span className="sr-only">unsaved</span>
                  <span className="column-row-dirty" aria-hidden>●</span>
                </>
              ) : valueOf(s.id) ? (
                <span className="column-row-count">{valueOf(s.id)}</span>
              ) : null}
            </button>
          ))}
        </ColumnSection>
      ))}
    </>
  );

  // The only theme control on the page, and it is pinned rather than living in
  // the Appearance section: it is the one setting whose effect is the whole
  // screen, so it wants to be reachable — and visible — from whichever section
  // you happen to be reading. A second copy inside Appearance would be the
  // same state rendered twice, one of them always off screen.
  const footer = <ThemePicker value={theme.mode} onPick={(m) => { void theme.pick(m); }}
                              disabled={theme.busy} />;

  // `config-shell`, not the old `config`: this page's CSS is written against the
  // redesign's tokens, and the legacy `.config …` block is a whole set of input
  // rules from the long-scroll era that would out-specify half of it.
  return (
    <PageShell column={column} footer={footer} columnLabel="Settings" className="config-shell">
      <div className="page-wide view-anim config-page">
        <div className="eyebrow">{current.group}</div>
        <h1 className="screen-title">{current.label}</h1>

        {error && <div className="banner error-banner">{error}</div>}
        {!draft && !error && <p className="empty-state">Loading…</p>}

        {draft && (
          <InstallSections section={section} draft={draft} config={config} edit={edit}
                           storeEpoch={storeEpoch} onStoreMoved={() => setStoreEpoch((n) => n + 1)} />
        )}

        {draft && section === "context" && (
          <ContextSection draft={draft} edit={edit} reach={config?.send_images_reach} probe={probe} />
        )}

        {draft && section === "layout" && (
          <>
            <p className="config-copy">
              The system message is assembled from about thirty sections in a fixed order — the
              system prompts, the character cards, the world info, the reply format. This is that
              order, and yours to change: move a section, or switch one off to stop sending it at
              all. Everything here is off until you turn it on, and turning it off again keeps
              the layout, so you can put a change back without rebuilding it.
            </p>
            <label className="checkbox-row">
              <input
                type="checkbox"
                aria-label="Use my section order"
                checked={draft.prompt_layout_enabled === "on"}
                onChange={(e) => edit("prompt_layout_enabled", e.target.checked ? "on" : "off")}
              />
              Use my section order
            </label>
            <PromptLayoutEditor
              rows={layout} failed={layoutFailed} busy={busy}
              onChange={setLayout}
              onReset={() => { void resetLayout(); }} />
            <p className="config-copy">
              The tier beside each section is the order the budget packer drops things in when a
              prompt will not fit, and it is not editable — recalled lore sits below the recalled
              scenes on purpose, so that a semantic hit can only ever add to a prompt and never
              push something else out of it. Where a section sits in the message and what gives
              way under pressure are two different questions.
            </p>
            <p className="config-copy">
              To change what a section <em>says</em>, edit its template in <code>templates/</code>
              — they are read from disk, so a saved edit is live on the next turn.
            </p>
          </>
        )}

        {draft && section === "tracker" && (
          <>
            <p className="config-copy">
              After every post, the scene tracker keeps a small record of each character: where
              they are, what they are holding and doing, and what they have seen or heard. It
              costs one extra call per post, made on the <em>Scene state tracker</em> model route,
              which you can point at a cheaper model from <Link to="/models">Models</Link>. A campaign can
              switch it on or off for itself.
            </p>
            <label className="checkbox-row">
              <input
                type="checkbox"
                aria-label="Track each character's state after every post"
                checked={draft.tracker !== "off"}
                onChange={(e) => edit("tracker", e.target.checked ? "on" : "off")}
              />
              Track each character's state after every post
            </label>
            <label className="checkbox-row">
              <input
                type="checkbox"
                aria-label="Ask each character to note what it heard or saw before replying"
                checked={draft.perception_rider !== "off"}
                onChange={(e) => edit("perception_rider", e.target.checked ? "on" : "off")}
              />
              Ask each character to note what it heard or saw before replying
            </label>
            <p className="config-copy">
              The second switch adds a short instruction to each character's turn, asking it to
              note what it perceived before it replies. Turn it off to keep the tracker's record
              while shedding that prompt cost.
            </p>
          </>
        )}

        {draft && section === "decisions" && (
          <>
            <p className="config-copy">
              Small closed questions asked during play, answered by the <em>Decision</em> model
              from <Link to="/models">Models</Link>. A campaign can set each of these for itself.
              What a decision reads is text the turn already sends, plus the replying
              character's own card summary, which goes to the Decision model's provider.
            </p>
            <label className="checkbox-row">
              <input
                type="checkbox"
                aria-label="Draw the next speaker from the pick's odds"
                checked={draft.decide_speaker_sampling === "on"}
                onChange={(e) => edit("decide_speaker_sampling", e.target.checked ? "on" : "off")}
              />
              Draw the next speaker from the pick's odds
            </label>
            <p className="config-copy">
              Adds no call: the speaker pick is already asked. A Decision model that reports
              odds has the speaker drawn from them, limited to whoever the last post addressed;
              one that does not has its answer taken as before.{" "}
              {decisionsInfo?.pick_samples === false
                && "With the global Decision model the pick is always the most likely speaker."}
              {decisionsInfo?.pick_samples === true
                && "The global Decision model reports odds, so the pick is drawn."}
            </p>
            <div className="config-field">
              <label htmlFor="cfg-turn-plan">Turn decision before each reply</label>
              <div className="config-input">
                <select id="cfg-turn-plan" value={draft.decide_turn_plan || "off"}
                        onChange={(e) => edit("decide_turn_plan", e.target.value)}>
                  <option value="off">Off</option>
                  <option value="intent">Intent: the character's stance</option>
                  <option value="plan">Plan: stance, what to reveal, tension</option>
                </select>
              </div>
              <p className="config-caption">
                One extra call before every character's reply, on the <em>Turn planning</em>
                route, adding up to twenty seconds before the first word. Its answer is a short
                note in that reply's prompt; if it fails, the reply plays on without it.
              </p>
            </div>
            <label className="checkbox-row">
              <input
                type="checkbox"
                aria-label="Let a character ask one closed question mid-reply"
                checked={draft.decide_play_tool === "on"}
                onChange={(e) => edit("decide_play_tool", e.target.checked ? "on" : "off")}
              />
              Let a character ask one closed question mid-reply
            </label>
            <p className="config-copy">
              Offers the scene model a decide tool it may call once, before it writes any of
              its reply. The tool's definition is sent with every character's reply, a few more
              prompt tokens each; a reply that calls it adds one Decision call on the{" "}
              <em>Turn planning</em> route and a second scene turn, which re-sends the whole
              prompt.
            </p>
          </>
        )}

        {draft && section === "investigation" && (
          <>
            <p className="config-copy">
              A model with tools reads this campaign's scenes and ledger to settle something
              the cheaper checks could not. Each is something you start, on the <em>History
              investigation</em> route, and switching one on here spends nothing. Both are
              experimental: off until their evaluation is recorded.
            </p>
            <label className="checkbox-row">
              <input
                type="checkbox"
                aria-label="Offer Investigate on a continuity finding"
                checked={draft.investigation_continuity === "on"}
                onChange={(e) => edit("investigation_continuity", e.target.checked ? "on" : "off")}
              />
              Offer Investigate on a continuity finding
            </label>
            <p className="config-copy">
              Reads the history behind one finding and suggests a decision for it, citing the
              scenes it read. Nothing is applied until you apply it.
            </p>
            <label className="checkbox-row">
              <input
                type="checkbox"
                aria-label="Offer Ask the history on the ledger"
                checked={draft.investigation_question === "on"}
                onChange={(e) => edit("investigation_question", e.target.checked ? "on" : "off")}
              />
              Offer Ask the history on the ledger
            </label>
            <p className="config-copy">
              Answers a question about what happened, citing only scenes it read. The answer
              is not saved.
            </p>
            <NumField id="cfg-investigation-ceiling" label="Spend ceiling per investigation"
                      unit="USD" placeholder="none" decimal
                      value={draft.investigation_spend_ceiling}
                      caption={"Blank is no ceiling: a run is still bounded by its turns, tool "
                        + "calls, time and the model's context. With a ceiling, a model with no "
                        + "price is refused before it starts; setting rates for it, or clearing "
                        + "the ceiling, lifts that."}
                      onChange={(v) => edit("investigation_spend_ceiling", v)} />
          </>
        )}

        {draft && section === "system-prompt" && (
          <>
            <p className="config-copy">
              Sent with every scene, ahead of the characters and the reply format — the
              standing instruction the narrator is never allowed to forget.
            </p>
            <label className="sr-only" htmlFor="cfg-system-prompt">
              System prompt (sent with every scene)
            </label>
            <textarea
              id="cfg-system-prompt"
              rows={6}
              className="config-textarea"
              placeholder="e.g. Never speak or act for the player character."
              value={draft.system_prompt}
              onChange={(e) => edit("system_prompt", e.target.value)}
            />
          </>
        )}

        {draft && section === "response" && (
          <>
            <p className="config-copy">
              The default length and style every campaign inherits, and what a campaign,
              a scene or a single turn overrides. Use Save response targets below to save
              changes here.
            </p>
            {/* The wrapper is this page's: the picker is shared with the scene
                inspector and the play view, which lay it out for themselves. */}
            <div className="config-response">
              <ResponseTargetsPicker scope="global" />
            </div>
          </>
        )}

        {draft && section === "transcript" && (
          <>
            <p className="config-copy">
              How the scene reads back to you. None of this reaches the model: the labels
              name the two voices in the transcript you are looking at.
            </p>
            <label className="checkbox-row">
              <input
                type="checkbox"
                aria-label="Color quoted dialogue"
                checked={draft.quote_color === "on"}
                onChange={(e) => edit("quote_color", e.target.checked ? "on" : "off")}
              />
              Color quoted dialogue
            </label>
            <div className="config-fields">
              <div className="config-field">
                <label htmlFor="cfg-user-label">Your label</label>
                <div className="config-input">
                  <input id="cfg-user-label" type="text" value={draft.user_label} placeholder="You"
                         onChange={(e) => edit("user_label", e.target.value)} />
                </div>
              </div>
              <div className="config-field">
                <label htmlFor="cfg-assistant-label">Narrator label</label>
                <div className="config-input">
                  <input id="cfg-assistant-label" type="text" value={draft.assistant_label}
                         placeholder="Grimoire"
                         onChange={(e) => edit("assistant_label", e.target.value)} />
                </div>
              </div>
            </div>
          </>
        )}

        {draft && section === "playing" && (
          <>
            <p className="config-copy">
              The scene inspector keeps a running summary of the scene you are playing, refolded
              in the background once this many posts have landed since the last one. Each refresh
              is one extra model call, so this is what the feature costs. <code>0</code> turns the
              automatic refresh off — the inspector's own <em>Refresh</em> button still works. The
              summary is a reading aid only: it is never added to what the model is told.
            </p>
            <p className="config-copy">
              It also watches for the moment a scene has run its course — a long stretch of
              posts, a move, a jump in the clock — and asks the model whether that is really
              where the scene ends. The number below is only how often it may look; the
              signals still have to agree before anything reaches the model, so it costs
              well under one call per that many posts. It never ends or splits a scene: the
              answer is a suggestion in the inspector, and yours to take or wave off.
              <code>0</code> turns it off, panel and all.
              A retcon replay re-runs every turn after the post you rewrote, one model
              call each, so a long one is expensive in both money and waiting. Past this
              many turns the transcript offers to fork the campaign first and replay in
              the copy, leaving what you were playing untouched.
              Skipping the campaign clock is one click and a month of story is behind
              you — every scheduled beat it crosses stamped as fired, every thread it
              leaves open that much staler. Past this many days the clock offers to save
              a checkpoint first: a copy of the campaign as it stands, left on the shelf
              where the story was while you skip on in the one you are playing.
            </p>
            <div className="config-fields">
              <NumField id="cfg-rolling-every" label="Summarize the scene every"
                        unit="posts" placeholder="10" caption="0 = only on demand"
                        value={draft.rolling_summary_every}
                        onChange={(v) => edit("rolling_summary_every", v)} />
              <NumField id="cfg-break-every" label="Scene-break check, at most every"
                        unit="posts" placeholder="20" caption="0 = off"
                        value={draft.scene_break_every}
                        onChange={(v) => edit("scene_break_every", v)} />
              {/* A threshold, not a limit: nothing refuses a long replay, and
                  both buttons are always offered. What this moves is where the
                  transcript starts recommending the fork. */}
              <NumField id="cfg-replay-fork" label="Offer to fork before replaying more than"
                        unit="turns" placeholder="10" caption="0 = suggest it every time"
                        value={draft.replay_fork_threshold}
                        onChange={(v) => edit("replay_fork_threshold", v)} />
              {/* Also a threshold and not a limit: the prompt always offers to
                  skip on without a checkpoint, and nothing here refuses a long
                  skip. The comparison is the server's -- this only moves it. */}
              <NumField id="cfg-advance-fork" label="Offer a checkpoint before skipping more than"
                        unit="days" placeholder="30" caption="0 = suggest it every time"
                        value={draft.advance_fork_threshold}
                        onChange={(v) => edit("advance_fork_threshold", v)} />
            </div>
          </>
        )}

        {draft && section === "output" && (
          <>
            <p className="config-copy">
              Regex rules that rewrite what you read, what the model is sent, or both. These
              global rules run after a connection's own and before a world's or a campaign's.
              Each saves as you set it.
            </p>
            <RegexRulesEditor scope={{ kind: "global" }} />
          </>
        )}

        {draft && section === "appearance" && (
          <p className="config-copy">
            One theme in two modes. <strong>System</strong> follows whatever the machine
            says it is right now, and changes with it; Light and Dark hold. The control is
            pinned at the foot of the list beside this, and a pick is applied and saved the
            moment you make it, so you can see what you are choosing — there is nothing to
            Save, and Revert leaves it alone.
          </p>
        )}
      </div>

      {/* The bar saves this page's draft, and a section that owns none of it --
          Storage, First-run setup, Response targets, Output processing,
          Appearance -- saves on its own, through its own control. There the bar
          is shown only while another section holds an edit, and says so:
          a disabled Save beside a section's own Save was two save models on
          one screen, and the one that could never act read as the one to
          press. */}
      {(current.fields.length > 0 || dirtyCount > 0) && (
        <div className="config-bar">
          <span className={"config-dirty" + (dirtyCount ? " on" : "")}>
            {dirtyCount
              ? `${dirtyCount} unsaved ${dirtyCount === 1 ? "change" : "changes"}`
                + (current.fields.length ? "" : " in other sections")
              : "No unsaved changes"}
          </span>
          <div className="config-bar-actions">
            <button className="btn-outline" onClick={revert} disabled={!dirtyCount || busy}>
              Revert
            </button>
            <button className="btn-accent" onClick={() => { void save(); }} disabled={!dirtyCount || busy}>
              {busy ? "Saving…" : "Save"}
            </button>
          </div>
        </div>
      )}
    </PageShell>
  );
}
