import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import {
  api, type Config, type EmbeddingSelection, type HealthCheckResult, type InferenceSelection,
  type InferenceSettings, type LLMConnectionKind, type ProviderPresetOption,
} from "../api/client";
import { errorText } from "../api/errors";
import { Field } from "../components/Field";
import { migrationLine, migrationUnfinished } from "../components/inference/migration";
import { ProviderModelPicker } from "../components/inference/ProviderModelPicker";
import { upgradePollDelay, upgradeStalled } from "../components/inference/useInferenceSettings";
import { StorageLocation } from "../components/StorageLocation";
import { ThemePicker } from "../components/ThemePicker";
import { PlainShell } from "../components/PageShell";
import { useTheme } from "../theme/ThemeProvider";

// One word each, and the word is what the step is *about* rather than what it
// does to a config file: "Provider", not "Connection"; "Look", not "Theme".
const STEPS = ["Storage", "Provider", "Models", "Look", "World"];

/** The adapters that have a model catalog to list: `llm.LISTABLE_KINDS`, as
 *  the provider editor spells it too. The Claude subscription has none, and
 *  the refresh route refuses it before anything is sent. */
const LISTABLE: LLMConnectionKind[] = ["openrouter", "openai_compatible", "anthropic"];

const BLANK_SELECTION: InferenceSelection = { provider: "", model: "", preset: "" };
const BLANK_EMBEDDING: EmbeddingSelection = { provider: "", model: "" };

/** A provider the wizard has in hand: the one it just created, or the one the
 *  library already writes through (`adopted`). */
type SavedProvider = { id: string; name: string; generatingCheck: boolean; adopted: boolean };

/** Fast or Decision: "Same as …" (`same`), or a model of its own. `sel` is
 *  kept either way, so its sampler preset survives a "Same as". */
type RoleChoice = { same: boolean; sel: InferenceSelection };

/** Mirrors the server's `inference.problem`: what a provider must have before
 *  it can send at all. A provider saved without it would say "Saved ✓" over
 *  something every turn refuses. */
function canSend(kind: LLMConnectionKind, key: string, baseUrl: string): boolean {
  if (kind === "openrouter" || kind === "anthropic") return key.trim() !== "";
  if (kind === "openai_compatible") return baseUrl.trim() !== "";
  return true;
}

/** One role on the Models step: a named region, so its controls are its own. */
function RoleSection({ title, hint, children }:
  { title: string; hint: ReactNode; children: ReactNode }) {
  return (
    <section className="wizard-role" aria-label={title}>
      <h4>{title}</h4>
      <p className="field-hint">{hint}</p>
      {children}
    </section>
  );
}

/** The first-run setup wizard (#194).
 *
 *  Five questions a fresh install otherwise expects the user to discover on
 *  their own, in the order the answers depend on each other: *where* the
 *  library lives comes first because every later answer is written into it,
 *  then a provider to write through and the models each role runs on (the one
 *  thing without which generation is impossible), then the theme, then the
 *  first world — which is the handoff into `CampaignWizard`, whose own first
 *  step needs a world to exist.
 *
 *  The provider and the roles are two steps because they are two records
 *  (spec 10): a provider is an account — a preset, a key, a billing — and
 *  which model chat runs on is the Primary role, written through
 *  `PUT /inference/settings`. Neither step writes until the library is at the
 *  current settings layout: a library still moving to it shows "Finishing the
 *  upgrade…" and asks again until the switch has landed.
 *
 *  Each step commits as it is answered rather than at the end: a wizard that
 *  banked its changes and applied them on Finish would have to re-implement
 *  every save path, and abandoning it halfway would silently discard work the
 *  user watched succeed. The consequence to keep in mind is that Back is
 *  navigation, not undo.
 *
 *  `onDone` is what actually retires the wizard for this session. App re-reads
 *  the server's verdict on every navigation, so this is not how it learns that
 *  setup is finished — it is the latch that makes leaving stick even when the
 *  verdict does not change, because the `setup_done` write below is
 *  best-effort and a store that cannot record it would otherwise answer
 *  first-run forever. */
export default function SetupWizard(
  { onDone }: { onDone: (store?: string) => void },
) {
  const navigate = useNavigate();
  // `mode`, not `name`: the control highlights the *choice*, so picking
  // System must not read back as whichever look the OS resolved it to.
  const { mode: theme, setTheme } = useTheme();
  const [step, setStep] = useState(1);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // The writes this component starts and then lets the user walk away from.
  // `store.write_config` serializes them now, so nothing is lost either way —
  // but two config saves in flight still make the *outcome* depend on response
  // order, and navigating away from one unmounts the only place its failure
  // would be reported.
  const [movingStore, setMovingStore] = useState(false);
  const [savingTheme, setSavingTheme] = useState(false);
  const [finishing, setFinishing] = useState(false);
  /** Any write this component has in flight. Every control that could start a
   *  second one, or navigate away from the first, is held while it is true. */
  const writing = busy || movingStore || savingTheme || finishing;
  /** Bumped by a move. An answer that arrives after one is about a store the
   *  wizard has stopped looking at, so it is dropped rather than shown. */
  const storeEpoch = useRef(0);

  // steps 2 and 3 — what the library's model settings look like now, and
  // whether it is at the layout this wizard writes (spec 11.2)
  const [inference, setInference] = useState<InferenceSettings | null>(null);
  const [inferenceFailed, setInferenceFailed] = useState<string | null>(null);
  /** Bumped each time a read of them settles, answered or not: what the
   *  upgrade poll re-arms on, so a failed read does not end the asking. */
  const [inferenceReads, setInferenceReads] = useState(0);

  // step 2 — the provider, unsaved until "Save provider"
  const [presets, setPresets] = useState<ProviderPresetOption[] | null>(null);
  const [presetsFailed, setPresetsFailed] = useState<string | null>(null);
  const [presetId, setPresetId] = useState("");
  const [providerName, setProviderName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [key, setKey] = useState("");
  const [billing, setBilling] = useState<"metered" | "subscription">("metered");
  const [provider, setProvider] = useState<SavedProvider | null>(null);
  /** What the provider said when the saved provider was checked (#146). Null
   *  while nothing has been checked — including the whole time before it is
   *  saved, since there is nothing to check yet. */
  const [health, setHealth] = useState<HealthCheckResult | null>(null);
  const [checking, setChecking] = useState(false);
  /** The new provider's catalog is being listed; the Models step's pickers
   *  ask again when it lands (`catalogs` keys them). */
  const [listing, setListing] = useState(false);
  const [catalogs, setCatalogs] = useState(0);

  // step 3 — the roles, read from the library once per store
  const [rolesRead, setRolesRead] = useState(false);
  const [primary, setPrimary] = useState<InferenceSelection>(BLANK_SELECTION);
  const [fast, setFast] = useState<RoleChoice>({ same: true, sel: BLANK_SELECTION });
  const [decision, setDecision] = useState<RoleChoice>({ same: true, sel: BLANK_SELECTION });
  const [embedding, setEmbedding] = useState<{ on: boolean; sel: EmbeddingSelection }>(
    { on: false, sel: BLANK_EMBEDDING });
  const [confirmingEmbedding, setConfirmingEmbedding] = useState(false);

  // step 5 — the first world
  const [worldName, setWorldName] = useState("");
  const [worldId, setWorldId] = useState<string | null>(null);
  // Step 1 can point this install at a folder that is already a full library —
  // the synced-folder case the storage step exists to support. The store the
  // rest of the wizard writes to is then not a first run at all, and offering
  // to create a "first world" in it would add a stray world to someone's
  // established collection.
  const [existingLibrary, setExistingLibrary] = useState(false);
  /** The store the wizard is currently working in, tracked so `finish()` can
   *  name it even when the write that would have reported it fails. */
  const [storeDir, setStoreDir] = useState<string | null>(null);

  /** Take from a config what the wizard should already consider answered for
   *  the store it describes, so a step that is done is not asked again.
   *
   *  Both the mount and the post-move path go through here. They used not to,
   *  and the move path silently kept asking for a provider the new library
   *  already had — saving that form creates a uniquely-suffixed duplicate.
   *
   *  Gated on `ready`, not merely on there being an active connection: at the
   *  current layout `active_connection` is what the Primary role resolves to,
   *  and `ready` is whether it can send — a library whose Primary names a
   *  keyless provider is exactly the state this step exists to fix.
   *
   *  `data_dir` is remembered even when nothing else is adopted, because
   *  `finish()` has to be able to name the store its answer belongs to on the
   *  path where the write it would have learned that from failed. */
  const adopt = useCallback((cfg: Config) => {
    setStoreDir(cfg.data_dir);
    if (!cfg.ready || !cfg.active_connection) return;
    setProvider({ id: cfg.active_connection.id, name: cfg.active_connection.name,
                  generatingCheck: false, adopted: true });
  }, []);

  /** Re-classify the store after step 1 has repointed at a different one, and
   *  drop everything the earlier steps recorded about the old one — a
   *  provider, its roles and a world live inside a store, so after a move they
   *  name records the active store does not have.
   *
   *  The question is "does this store have a world", not "is it a first run":
   *  an empty store whose setup was skipped before reports `first_run: false`
   *  too, and treating that as stocked would hide the create form and offer a
   *  campaign handoff into `CampaignWizard`, which cannot get past its first
   *  step with no world to pick. */
  const recheckStore = useCallback(async () => {
    storeEpoch.current += 1;
    setProvider(null);
    // With the verdict that belonged to it: a check is about one provider in
    // one store, and the new store's providers have not been tested at all.
    setHealth(null);
    // And a check still in flight belongs to that provider too: its answer is
    // dropped, so nothing else would ever clear the flag it set.
    setChecking(false);
    setListing(false);
    setInference(null);
    setInferenceFailed(null);
    setRolesRead(false);
    setConfirmingEmbedding(false);
    setWorldId(null);
    setWorldName("");
    try {
      // The theme is a property of the store too, so the new library's is now
      // the live one. Without this the Theme step marks the old store's card
      // active, and clicking that card would overwrite the new library's
      // preference with what the previous one happened to use.
      const [cfg, worlds] = await Promise.all([api.getConfig({ fresh: true }), api.listWorlds()]);
      setTheme(cfg.theme);
      adopt(cfg);
      setExistingLibrary(worlds.length > 0);
    } catch (err: unknown) {
      // Not a guess in either direction: say so, and leave the step showing the
      // form, which is recoverable. Silently claiming "already stocked" would
      // strand a fresh user with no way to make a world.
      setExistingLibrary(false);
      const detail = (err as { detail?: unknown } | null)?.detail;
      setError(typeof detail === "string" && detail
        ? detail : "Moved, but the new library could not be read.");
    }
  }, []);

  useEffect(() => {
    let alive = true;
    api.getConfig().then((c) => alive && adopt(c))
      .catch(() => { /* the form is the safe default */ });
    return () => { alive = false; };
  }, []);

  /** Choose a provider preset: it prefills the name, the address and the
   *  billing, which is the whole of what a preset is for (spec 6.1). */
  const choosePreset = useCallback((preset: ProviderPresetOption) => {
    setPresetId(preset.id);
    setProviderName(preset.label);
    setBaseUrl(preset.base_url);
    setBilling(preset.billing);
  }, []);

  // Bumped by Try again: a failed list is a reason and a way on, not a dead end.
  const [presetsAsked, setPresetsAsked] = useState(0);
  useEffect(() => {
    let alive = true;
    setPresets(null);
    setPresetsFailed(null);
    api.listProviderPresets()
      .then((list) => {
        if (!alive) return;
        setPresets(list);
        if (list[0]) choosePreset(list[0]);
      })
      .catch((err: unknown) => { if (alive) setPresetsFailed(errorText(err)); });
    return () => { alive = false; };
  }, [choosePreset, presetsAsked]);

  const loadInference = useCallback(async () => {
    const epoch = storeEpoch.current;
    try {
      const s = await api.getInferenceSettings();
      if (epoch !== storeEpoch.current) return;
      setInference(s);
      setInferenceFailed(null);
    } catch (err: unknown) {
      if (epoch === storeEpoch.current) setInferenceFailed(errorText(err));
    }
    if (epoch === storeEpoch.current) setInferenceReads((n) => n + 1);
  }, []);

  // Read on entering either model step: the Models step has to see the
  // provider the step before it just created.
  useEffect(() => {
    if (step === 2 || step === 3) void loadInference();
  }, [step, loadInference]);

  /** Whether these steps may write: the store's global layout is the one they
   *  write in, which is exactly what the server's `refuse_unmigrated` asks.
   *  Not "the upgrade is done": that also waits on every campaign being
   *  marked, and a campaign skipped as busy is finished on the next start —
   *  meanwhile the server takes these writes, so refusing them here would
   *  hold the step shut over something it does not depend on. */
  const settingsCurrent = !!inference && !inference.newer
    && inference.migration.state !== "newer" && inference.format === "2";
  // A library whose global switch has not landed yet is asked again until it
  // has. Re-armed whenever a read settles, answered or not: keyed on the
  // answer alone, one failed read left nothing changed and the asking stopped.
  const upgrading = !!inference && !settingsCurrent && !inference.newer
    && (inference.migration.state === "pending" || inference.migration.state === "running");
  useEffect(() => {
    if (!upgrading || (step !== 2 && step !== 3)) return;
    const timer = setTimeout(() => { void loadInference(); }, upgradePollDelay(inferenceReads));
    return () => clearTimeout(timer);
  }, [upgrading, step, inferenceReads, loadInference]);

  // The roles as the library has them, once per store and only once it is at
  // the layout they are written in. Primary with no provider of its own starts
  // on the provider step 2 saved, which is the one this wizard is setting up --
  // and so does a Primary stored on a provider that cannot send (a library
  // whose Primary names a keyless provider, the case `adopt` describes), once
  // step 2 has made one that can. Its model was that provider's, so it is not
  // carried over; its preset is.
  useEffect(() => {
    if (step !== 3 || rolesRead || !inference || !settingsCurrent) return;
    const { roles } = inference;
    const stored = roles.primary.stored;
    const storedCannotSend = !!stored.provider && !!provider && !provider.adopted
      && inference.providers.find((p) => p.id === stored.provider)?.usable === false;
    setPrimary(!provider ? stored
      : storedCannotSend ? { provider: provider.id, model: "", preset: stored.preset }
      : stored.provider ? stored : { ...stored, provider: provider.id });
    setFast({ same: !roles.fast.stored.provider && !roles.fast.stored.model,
              sel: roles.fast.stored });
    setDecision({ same: !roles.decision.stored.provider && !roles.decision.stored.model,
                  sel: roles.decision.stored });
    const emb = roles.embedding?.stored ?? BLANK_EMBEDDING;
    setEmbedding({ on: !!(emb.provider && emb.model), sel: emb });
    setRolesRead(true);
  }, [step, rolesRead, inference, settingsCurrent, provider]);

  /** Record that setup has been answered, then hand control back. Marking done
   *  is deliberately best-effort: failing to write a preference must not strand
   *  someone on the wizard, and the worst case is being offered it once more.
   *
   *  It takes the wizard down with it while it runs. This is a config write
   *  like the theme's, so a Back-then-pick-a-theme during a slow one is two
   *  unlocked writes racing; and clicking both final destinations would make
   *  the landing page depend on which response returned first. */
  async function finish(to: string) {
    if (finishing) return;
    setFinishing(true);
    let store: string | undefined;
    try {
      // The response names the store this answer belongs to, which is how the
      // caller's latch stays scoped to it — step 1 may have repointed at a
      // different library since the caller last looked.
      store = (await api.putConfig({ setup_done: "on" })).data_dir;
    } catch {
      /* the flag is a convenience, not a gate */
    }
    // `storeDir` is the fallback rather than the caller's own idea of the
    // store: step 1 may have repointed at a different library since the caller
    // last read the config, and letting it key its latch on the pre-move path
    // sends the user straight back into the wizard — the exact trap the latch
    // exists to prevent, on the one path where the flag write also failed.
    onDone(store ?? storeDir ?? undefined);
    navigate(to, { replace: true });
  }

  const preset = presets?.find((p) => p.id === presetId) ?? null;
  const providerReady = !!preset && providerName.trim() !== ""
    && canSend(preset.kind, key, baseUrl);

  /** Ask the saved provider whether it works (#146), and drop the answer if
   *  the wizard has moved to another store meanwhile. `confirm` only for a
   *  check that generates, and only from the button that says so. */
  function check(id: string, confirm: boolean) {
    const epoch = storeEpoch.current;
    setChecking(true);
    (confirm ? api.checkConnection(id, { confirm: true }) : api.checkConnection(id))
      .then((h) => { if (epoch === storeEpoch.current) setHealth(h); })
      .catch(() => { /* the check is a courtesy; a provider that saved is still saved */ })
      .finally(() => { if (epoch === storeEpoch.current) setChecking(false); });
  }

  async function saveProvider() {
    if (writing || provider || !preset || !providerReady || !settingsCurrent) return;
    setError(null);
    setBusy(true);
    try {
      const name = providerName.trim();
      // No model: at the current layout that is the Primary role's, and the
      // server refuses one here ("set this on the model, not the provider").
      const { id } = await api.createConnection({
        kind: preset.kind, name, preset: preset.id, billing,
        base_url: baseUrl.trim(), api_key: key,
      });
      setKey("");   // the key is on the server now; keeping a copy buys nothing
      setProvider({ id, name, generatingCheck: preset.generating_check, adopted: false });
      // The Models step's pickers list the providers this view read before the
      // create, so the new one would show as "(missing provider)" until the
      // step's own re-read lands. It can send: `providerReady` said so.
      setInference((s) => (!s || s.providers.some((p) => p.id === id) ? s : {
        ...s,
        providers: [...s.providers,
                    { id, name, kind: preset.kind, preset: preset.id, usable: true, problem: null }],
      }));
      setPrimary((p) => (p.provider ? p : { ...p, provider: id }));
      // Then ask the provider whether the thing just saved actually works, and
      // deliberately NOT awaited: the provider IS saved either way, so a
      // failed check is a warning beside the tick, not a gate — a wizard that
      // refused to move on because a key was rejected would trap someone whose
      // provider is merely down. A check that GENERATES (the Claude
      // subscription) is never sent unasked: it waits for its own button.
      if (!preset.generating_check) check(id, false);
      // The Models step lists this provider's catalog, which nothing has
      // fetched yet. Not awaited either, for the same reason.
      if (LISTABLE.includes(preset.kind)) {
        const epoch = storeEpoch.current;
        setListing(true);
        api.refreshConnectionModels(id)
          .catch(() => { /* the picker still takes a typed id */ })
          .finally(() => {
            if (epoch !== storeEpoch.current) return;
            setListing(false);
            setCatalogs((n) => n + 1);
          });
      }
    } catch (err: unknown) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  // ---- step 3: the roles ----
  const own = (c: RoleChoice) => c.same || (!!c.sel.provider && !!c.sel.model.trim());
  const rolesReady = !!primary.provider && !!primary.model.trim() && own(fast) && own(decision)
    && (!embedding.on || (!!embedding.sel.provider && !!embedding.sel.model.trim()));
  const storedEmbedding = inference?.roles.embedding?.stored ?? BLANK_EMBEDDING;
  const embeddingSent = embedding.on ? embedding.sel : BLANK_EMBEDDING;
  /** Whether this save would re-embed the library: a model that embeds and is
   *  not the one it already has. Clearing it, or rewriting what is there,
   *  re-embeds nothing (spec 10) — the server holds the same line. */
  const reEmbeds = !!embeddingSent.provider && !!embeddingSent.model
    && (embeddingSent.provider !== storedEmbedding.provider
        || embeddingSent.model !== storedEmbedding.model);

  async function saveRoles(confirmed: boolean) {
    if (writing || !rolesReady || !settingsCurrent) return;
    if (reEmbeds && !confirmed) { setConfirmingEmbedding(true); return; }
    setConfirmingEmbedding(false);
    setError(null);
    setBusy(true);
    // "Same as …" is a role with no model of its own. Whatever preset it has
    // stored is sent back as it was, since the reader said nothing about it
    // here -- but it is not used while the role names no provider (the
    // cascade reads a role only when it names one), which the Models page
    // says beside it.
    const roleOf = (c: RoleChoice) => ({
      selection: c.same ? { ...BLANK_SELECTION, preset: c.sel.preset } : c.sel,
    });
    try {
      const saved = await api.putInferenceSettings({
        roles: {
          primary: { selection: primary },
          fast: roleOf(fast),
          decision: roleOf(decision),
          embedding: { selection: embeddingSent },
        },
      }, confirmed ? { confirmEmbedding: true } : undefined);
      setInference(saved);
      setStep(4);
    } catch (err: unknown) {
      // The server's verdict outranks this one: the stored Embedding role can
      // have changed since it was read (another device), and printing the
      // refusal with no way to agree would leave nothing to do but Skip.
      if ((err as { kind?: unknown } | null)?.kind === "confirm_embedding") {
        setConfirmingEmbedding(true);
      } else {
        setError(errorText(err));
      }
    } finally {
      setBusy(false);
    }
  }

  async function pickTheme(next: string) {
    const previous = theme;
    setTheme(next);            // apply immediately; the wizard is the preview
    setError(null);
    setSavingTheme(true);
    try {
      await api.putConfig({ theme: next });
    } catch (err: unknown) {
      // Put the preview back. Left applied, an unsaved theme looks chosen for
      // the rest of the session and then vanishes on the next reload, which
      // reads as the app losing the setting rather than never taking it.
      setTheme(previous);
      setError(errorText(err));
    } finally {
      setSavingTheme(false);
    }
  }

  async function createWorld() {
    const trimmed = worldName.trim();
    if (!trimmed || writing) return;
    setError(null);
    setBusy(true);
    try {
      const { id } = await api.createWorld(trimmed);
      setWorldId(id);
    } catch (err: unknown) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  /** What stands in for steps 2 and 3 while the library cannot take their
   *  writes: still reading, still upgrading, an upgrade that stopped, or a
   *  layout a newer build wrote. Null once they may write. */
  function upgradeGate(): ReactNode {
    if (inferenceFailed !== null && !inference) {
      return (
        <p className="field-hint">
          Couldn't read this library's model settings: {inferenceFailed}{" "}
          <button className="link" onClick={() => { void loadInference(); }}>Try again</button>
        </p>
      );
    }
    if (!inference) return <p className="field-hint">Reading this library's model settings…</p>;
    if (inference.newer || inference.migration.state === "newer") {
      return (
        <p className="field-hint">
          A newer version of Grimoire has written this library's model settings, so this one
          will not change them. Skip this step, or set it up from the newer version.
        </p>
      );
    }
    if (settingsCurrent) return null;
    if (inference.migration.state === "failed") {
      return (
        <p className="field-hint">
          The upgrade to the new model settings stopped
          {inference.migration.reason ? `: ${inference.migration.reason}` : "."} Skip this step
          for now — Settings shows how the upgrade stands.
        </p>
      );
    }
    if (upgrading && upgradeStalled(inferenceReads)) {
      // Still `pending` or `running` after a long while: the switch may be
      // turned off, or a run elsewhere died and left its note. Nothing here
      // can finish it, so the step stops promising that it will open.
      return (
        <p className="field-hint" role="status">
          The upgrade to the new model settings is not finishing — it may not be running, and
          it resumes the next time Grimoire starts. Skip this step for now — Settings shows how
          the upgrade stands.
        </p>
      );
    }
    if (upgrading) {
      return (
        <p className="field-hint" role="status">
          Finishing the upgrade… This library's model settings are moving to a new layout,
          and this step opens as soon as that is done.
        </p>
      );
    }
    // Neither at the layout nor moving to it (the automatic switch is turned
    // off): nothing will open this step, and the server refuses its writes.
    return (
      <p className="field-hint">
        This library's model settings are still in the old layout, which this step cannot
        write. Skip it for now — Settings shows how the upgrade stands.
      </p>
    );
  }

  /** One quiet line on a step that is open while the upgrade still has work
   *  left: what it skipped (a busy campaign, finished on the next start) is
   *  named as the server names it, and nothing on this step waits on it. */
  function upgradeNote(): ReactNode {
    const line = settingsCurrent ? migrationLine(inference) : null;
    if (!line) return null;
    return (
      <p className="field-hint">
        {line}{migrationUnfinished(inference) ? " What you set here is saved meanwhile." : ""}
      </p>
    );
  }

  const gate = step === 2 || step === 3 ? upgradeGate() : null;
  const note = step === 2 || step === 3 ? upgradeNote() : null;
  const pickerProviders = inference?.providers ?? [];

  function sameAs(title: string, of: string, choice: RoleChoice,
                  set: (c: RoleChoice) => void, needs: ["generate"] | ["decide"]) {
    return (
      <>
        <select aria-label={title} value={choice.same ? "same" : "own"} disabled={writing}
                onChange={(e) => set({ ...choice, same: e.target.value === "same" })}>
          <option value="same">Same as {of}</option>
          <option value="own">A model of its own</option>
        </select>
        {!choice.same && (
          <ProviderModelPicker key={catalogs} needs={needs} providers={pickerProviders}
                               value={choice.sel} disabled={writing}
                               onChange={(v) => set({ ...choice, sel: { ...choice.sel, ...v } })} />
        )}
      </>
    );
  }

  return (
    <PlainShell>
      <div className="first-run view-anim">
        <img className="wizard-mark" src="/grimoire-128.png" alt="" width={56} height={56} />
        <h1 className="wizard-title">Grimoire</h1>
        {/* The promise the app is making, said before anything is asked. It is
            also the answer to the first question, which is why it comes first. */}
        <p className="wizard-promise">
          Everything you make stays yours, as plain files on this machine.
          Five questions and you're playing.
        </p>

        {/* Every step is named, not only the one you are on. Five questions is
            short enough to show whole, and seeing the whole of it is what makes
            it read as short. */}
        <ol className="wizard-steps">
          {STEPS.map((label, i) => {
            const n = i + 1;
            const state = step === n ? "on" : step > n ? "done" : "";
            return (
              <li key={label} className={`wizard-step ${state}`}>
                <span className="num">{step > n ? "✓" : n}</span>
                <span className="label">{label}</span>
              </li>
            );
          })}
        </ol>

        {error && <div className="banner error-banner">{error}</div>}

        {step === 1 && (
          <div className="wizard-body">
            <h3>Where should your library live?</h3>
            {/* The "plain files" half of this moved up to the page's own
                promise, where it is the first thing said rather than the third.
                What is left is the only part that asks for a decision. */}
            <p className="wizard-intro">
              The default is fine — change it now only if you would rather your
              library lived elsewhere.
            </p>
            <StorageLocation onPending={setMovingStore} onMoved={recheckStore} />
            <div className="wizard-footer">
              <span />
              {/* Label stays "Next" — the Move button is already saying
                  "Moving…", and two controls with one name is a worse hint. */}
              <button className="btn-accent" onClick={() => setStep(2)} disabled={writing}>Next ▸</button>
            </div>
          </div>
        )}

        {step === 2 && (
          <div className="wizard-body">
            <h3>Add a provider</h3>
            <p className="wizard-intro">
              Grimoire writes through a provider — an account with a model service, or a
              server of your own. Add one now, or skip: you can play by hand and add one
              later on the Providers page.
            </p>
            {note}
            {gate ?? (provider ? (
              <>
                <p className="config-msg save-flash">
                  {provider.adopted
                    ? `This library already writes through ${provider.name} ✓`
                    : `Saved ${provider.name} ✓`}
                </p>
                {/* "Saved" is not "it works" — the whole of #146. When the
                    check disagrees, say so here rather than letting the first
                    scene be where they find out. */}
                {health && !health.ok && (
                  <p className="field-hint">
                    Saved, but the provider refused: {health.detail || health.kind}. You can
                    carry on and fix it later on the Providers page.
                  </p>
                )}
                {health?.ok && provider.generatingCheck && (
                  <p className="field-hint">It answered ✓</p>
                )}
                {/* The only honest check of a Claude subscription is a real
                    (tiny) generation, so it is the reader's to ask for — the
                    server refuses it otherwise (spec 6.5). */}
                {provider.generatingCheck && !health && (
                  <p className="field-hint">
                    Checking this provider sends it one short message, so it is not done
                    automatically.{" "}
                    <button className="link" disabled={checking}
                            onClick={() => check(provider.id, true)}>
                      {checking ? "Checking…" : "Check (sends one short message)"}
                    </button>
                  </p>
                )}
              </>
            ) : presetsFailed !== null ? (
              <p className="field-hint">
                Couldn&apos;t list the kinds of provider: {presetsFailed}{" "}
                <button className="link" onClick={() => setPresetsAsked((n) => n + 1)}>
                  Try again
                </button>
              </p>
            ) : !presets ? (
              <p className="field-hint">Listing the kinds of provider…</p>
            ) : (
              <>
                <Field label="Provider">
                  <select value={presetId} disabled={writing}
                          onChange={(e) => {
                            const next = presets.find((p) => p.id === e.target.value);
                            if (next) choosePreset(next);
                          }}>
                    {presets.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
                  </select>
                </Field>
                <Field label="Name">
                  <input value={providerName} disabled={writing}
                         onChange={(e) => setProviderName(e.target.value)} />
                </Field>
                {preset && preset.kind !== "claude" && !preset.url_locked && (
                  <Field label="Base URL">
                    <input value={baseUrl} disabled={writing} placeholder="http://localhost:1234/v1"
                           onChange={(e) => setBaseUrl(e.target.value)} />
                  </Field>
                )}
                {preset && preset.kind !== "claude" && (
                  <Field label="API key"
                         hint={preset.kind === "openai_compatible" && !preset.url_locked
                           ? "Optional — leave blank for servers that don't require auth."
                           : undefined}>
                    <input type="password" autoComplete="off" value={key} disabled={writing}
                           onChange={(e) => setKey(e.target.value)} />
                  </Field>
                )}
                {/* Every other preset knows how it bills (spec 6.1); an
                    endpoint nobody has described is the reader's to say. */}
                {preset?.id === "custom" && (
                  <Field label="Billing"
                         hint="Subscription: calls are covered by a plan rather than charged per token.">
                    <select value={billing} disabled={writing}
                            onChange={(e) => setBilling(
                              e.target.value === "subscription" ? "subscription" : "metered")}>
                      <option value="metered">Metered</option>
                      <option value="subscription">Subscription</option>
                    </select>
                  </Field>
                )}
              </>
            ))}
            <div className="wizard-footer">
              <button className="subtle" onClick={() => setStep(1)} disabled={writing}>Back</button>
              {provider
                ? <button className="btn-accent" onClick={() => setStep(3)} disabled={writing}>Next ▸</button>
                : (
                  <span className="wizard-actions">
                    <button className="subtle" onClick={() => setStep(3)} disabled={writing}>Skip</button>
                    {!gate && presets && (
                      <button className="btn-accent" onClick={() => { void saveProvider(); }}
                              disabled={writing || !providerReady}>
                        {busy ? "Saving…" : "Save provider"}
                      </button>
                    )}
                  </span>
                )}
            </div>
          </div>
        )}

        {step === 3 && (
          <div className="wizard-body">
            <h3>Choose your models</h3>
            <p className="wizard-intro">
              Primary writes your story. The others can follow it until you have a reason to
              split them, and every one of them is changeable later on the Models page.
            </p>
            {note}
            {gate ?? (
              <>
                {listing && <p className="field-hint">Listing the new provider's models…</p>}
                <RoleSection title="Primary" hint="Writes every turn. Required.">
                  <ProviderModelPicker key={catalogs} needs={["generate"]}
                                       providers={pickerProviders}
                                       value={primary} disabled={writing}
                                       onChange={(v) => setPrimary((p) => ({ ...p, ...v }))} />
                  {/* It has no catalog to list (`LISTABLE`), so the picker can
                      only take a typed id — say which ones it understands. */}
                  {pickerProviders.find((p) => p.id === primary.provider)?.kind === "claude" && (
                    <p className="field-hint">
                      A Claude subscription has no model list to show: type one, such as
                      sonnet or opus.
                    </p>
                  )}
                </RoleSection>
                <RoleSection title="Fast" hint="Short background work: summaries, suggestions.">
                  {sameAs("Fast", "Primary", fast, setFast, ["generate"])}
                </RoleSection>
                <RoleSection title="Decision" hint="Yes-or-no questions the app asks itself.">
                  {sameAs("Decision", "Fast", decision, setDecision, ["decide"])}
                </RoleSection>
                <RoleSection title="Embedding"
                             hint="Optional. Lets recall find earlier scenes and lore by meaning, not only by matching words.">
                  <select aria-label="Embedding" value={embedding.on ? "on" : "off"}
                          disabled={writing}
                          onChange={(e) => {
                            setConfirmingEmbedding(false);
                            setEmbedding({ ...embedding, on: e.target.value === "on" });
                          }}>
                    <option value="off">Not now</option>
                    <option value="on">Choose a model</option>
                  </select>
                  {embedding.on && (
                    <ProviderModelPicker key={catalogs} needs={["embed"]}
                                         providers={pickerProviders}
                                         value={embedding.sel} disabled={writing}
                                         onChange={(v) => {
                                           setConfirmingEmbedding(false);
                                           setEmbedding({ on: true, sel: v });
                                         }} />
                  )}
                </RoleSection>
                {/* Changing the Embedding role re-embeds the library through
                    the chosen provider, which may cost money, so it waits for a
                    yes — and the server refuses the write without one. */}
                {confirmingEmbedding && (
                  <div className="banner" role="group" aria-label="Confirm embedding">
                    <p>
                      Embedding with {embeddingSent.model} embeds your whole library through{" "}
                      {pickerProviders.find((p) => p.id === embeddingSent.provider)?.name
                        ?? embeddingSent.provider}, which may cost money.
                    </p>
                    <span className="wizard-actions">
                      <button className="subtle" onClick={() => setConfirmingEmbedding(false)}
                              disabled={writing}>
                        Cancel
                      </button>
                      <button className="btn-accent" onClick={() => { void saveRoles(true); }}
                              disabled={writing}>
                        Embed and save
                      </button>
                    </span>
                  </div>
                )}
              </>
            )}
            <div className="wizard-footer">
              <button className="subtle" onClick={() => setStep(2)} disabled={writing}>Back</button>
              <span className="wizard-actions">
                <button className="subtle" onClick={() => setStep(4)} disabled={writing}>Skip</button>
                {!gate && (
                  <button className="btn-accent" onClick={() => { void saveRoles(false); }}
                          disabled={writing || !rolesReady || confirmingEmbedding}>
                    {busy ? "Saving…" : "Save and continue"}
                  </button>
                )}
              </span>
            </div>
          </div>
        )}

        {step === 4 && (
          <div className="wizard-body">
            <h3>Pick a look</h3>
            <p className="wizard-intro">
              Applies as you click, and is changeable any time from Config.
            </p>
            <ThemePicker value={theme} onPick={(next) => { void pickTheme(next); }}
                         disabled={writing} />
            <div className="wizard-footer">
              <button className="subtle" onClick={() => setStep(3)} disabled={writing}>Back</button>
              <button className="btn-accent" onClick={() => setStep(5)} disabled={writing}>
                {savingTheme ? "Saving…" : "Next ▸"}
              </button>
            </div>
          </div>
        )}

        {step === 5 && (
          <div className="wizard-body">
            <h3>{existingLibrary ? "This library is already stocked" : "Create your first world"}</h3>
            <p className="wizard-intro">
              {existingLibrary
                ? "The folder you chose in step one already holds worlds, so there is nothing to create here — open one from Worlds, or start a campaign from it."
                : "A world holds the places, people and lore your campaigns draw on. Every campaign starts from one, so this is the last thing standing between you and play."}
            </p>
            {worldId
              ? <p className="config-msg save-flash">Created {worldName.trim()} ✓</p>
              : existingLibrary ? null : (
                <div className="joined">
                  <input
                    placeholder="World name…" aria-label="World name"
                    value={worldName} onChange={(e) => setWorldName(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter") void createWorld(); }}
                  />
                  <button className="btn-accent" onClick={() => { void createWorld(); }}
                          disabled={writing || !worldName.trim()}>
                    {busy ? "Creating…" : "Create"}
                  </button>
                </div>
              )}
            <div className="wizard-footer">
              <button className="subtle" onClick={() => setStep(4)} disabled={writing}>Back</button>
              {worldId || existingLibrary
                ? (
                  <span className="wizard-actions">
                    <button className="subtle" onClick={() => { void finish("/"); }} disabled={writing}>Finish</button>
                    <button className="btn-accent" onClick={() => { void finish("/campaigns/new"); }} disabled={writing}>
                      Start a campaign ▸
                    </button>
                  </span>
                )
                /* Disabled while a world is being created: leaving now unmounts
                   the only place that would report the result, and dismisses
                   setup for good whether or not the world landed. */
                : <button className="subtle" onClick={() => { void finish("/"); }} disabled={writing}>
                    Finish later
                  </button>}
            </div>
          </div>
        )}

        {/* A standing way out, for the steps whose own footer only moves
            forward. The World step's footer always offers one, so it does not
            need this too. It sits beside Next rather than under the card as a
            sentence: leaving is a real answer to the wizard's questions, and
            burying it in prose made it read as a warning about giving up. */}
        {step !== 5 && (
          <p className="wizard-skip">
            {/* Disabled for the same reason the step's own Next is: leaving
                mid-write races this step's write against finish()'s config
                write, and unmounts the only place its failure would show. */}
            <button className="link" onClick={() => { void finish("/"); }} disabled={writing}>
              Skip setup
            </button>
            {" — you can do all of this later from Config."}
          </p>
        )}
      </div>
    </PlainShell>
  );
}
