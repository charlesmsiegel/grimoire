import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type Config, type HealthCheckResult } from "../api/client";
import { type Model } from "../api/models";
import {
  BLANK_CONNECTION, ConnectionForm, type ConnectionFormValue,
} from "../components/ConnectionForm";
import { StorageLocation } from "../components/StorageLocation";
import { ThemePicker } from "../components/ThemePicker";
import { PlainShell } from "../components/PageShell";
import { useTheme } from "../theme/ThemeProvider";

// One word each, and the word is what the step is *about* rather than what it
// does to a config file: "Model", not "Connection"; "Look", not "Theme".
const STEPS = ["Storage", "Model", "Look", "World"];

/** The first-run setup wizard (#194).
 *
 *  Four questions a fresh install otherwise expects the user to discover on
 *  their own, in the order the answers depend on each other: *where* the
 *  library lives comes first because every later answer is written into it,
 *  then the LLM connection (the one thing without which generation is
 *  impossible), then the theme, then the first world — which is the handoff
 *  into `CampaignWizard`, whose own first step needs a world to exist.
 *
 *  Each step commits as it is answered rather than at the end: a wizard that
 *  banked four changes and applied them on Finish would have to re-implement
 *  four save paths, and abandoning it halfway would silently discard work the
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

  // step 2 — the connection, unsaved until "Save connection"
  const [form, setForm] = useState<ConnectionFormValue>(BLANK_CONNECTION);
  const [key, setKey] = useState("");
  const [models, setModels] = useState<Model[]>([]);
  const [modelsError, setModelsError] = useState(false);
  const [connected, setConnected] = useState<string | null>(null);
  /** What the provider said when the finished connection was tested (#146).
   *  Null while nothing has been checked — including the whole time before the
   *  connection is saved, since there is nothing to check yet. */
  const [health, setHealth] = useState<HealthCheckResult | null>(null);
  // Creating the connection and activating it are two requests, and the second
  // can fail on its own. Holding the id from a successful create is what makes
  // the retry activate that connection rather than create a second one —
  // `create_connection` uniquifies the slug, so a retry loop leaves a trail of
  // `-2`, `-3` connections behind.
  const [createdId, setCreatedId] = useState<string | null>(null);

  // step 4 — the first world
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
   *  and the move path silently kept asking for a connection the new library
   *  already had — saving that form creates a uniquely-suffixed duplicate of
   *  the connection that is already active.
   *
   *  Gated on `ready`, not merely on there being an active connection: a fresh
   *  store ships with an OpenRouter connection selected and no key, which is
   *  exactly the state this step exists to fix.
   *
   *  `data_dir` is remembered even when nothing else is adopted, because
   *  `finish()` has to be able to name the store its answer belongs to on the
   *  path where the write it would have learned that from failed. */
  const adopt = useCallback((cfg: Config) => {
    setStoreDir(cfg.data_dir);
    if (!cfg.ready || !cfg.active_connection) return;
    setConnected(cfg.active_connection.name);
    setCreatedId(cfg.active_connection.id);
  }, []);

  /** Re-classify the store after step 1 has repointed at a different one, and
   *  drop everything the earlier steps recorded about the old one — a
   *  connection and a world live inside a store, so after a move they name
   *  records the active store does not have.
   *
   *  The question is "does this store have a world", not "is it a first run":
   *  an empty store whose setup was skipped before reports `first_run: false`
   *  too, and treating that as stocked would hide the create form and offer a
   *  campaign handoff into `CampaignWizard`, which cannot get past its first
   *  step with no world to pick. */
  const recheckStore = useCallback(async () => {
    setConnected(null);
    // With the verdict that belonged to it: a check is about one connection in
    // one store, and the new store's connections have not been tested at all.
    setHealth(null);
    setCreatedId(null);
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
    } catch (err: any) {
      // Not a guess in either direction: say so, and leave the step showing the
      // form, which is recoverable. Silently claiming "already stocked" would
      // strand a fresh user with no way to make a world.
      setExistingLibrary(false);
      setError(err.detail ?? "Moved, but the new library could not be read.");
    }
  }, []);

  // The catalog for a connection that does not exist yet (#149): the wizard's
  // whole job on this step is picking a model for one. OpenRouter's list is
  // public, so it fills in before a key is typed; a custom endpoint has nothing
  // to list until its base URL is, and falls back to free-text entry until the
  // connection is saved and the Connections page can fetch it.
  useEffect(() => {
    let alive = true;
    setModelsError(false);
    if (form.kind !== "openrouter") { setModels([]); return; }
    api.previewModels({ kind: "openrouter" })
      .then((r) => alive && setModels(r.models))
      .catch(() => alive && setModelsError(true));
    return () => { alive = false; };
  }, [form.kind]);

  useEffect(() => {
    let alive = true;
    api.getConfig().then((c) => alive && adopt(c))
      .catch(() => { /* the form is the safe default */ });
    return () => { alive = false; };
  }, []);

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

  // Mirrors the backend's `_connection_ready`: a connection missing the field
  // its kind needs saves fine and then reports `ready: false`, so accepting one
  // here would put "Connected ✓" on a connection that cannot generate.
  const connectionUsable = form.name.trim() !== "" && (
    form.kind === "claude" ? true
      : form.kind === "openrouter" ? key.trim() !== ""
      : form.base_url.trim() !== "");

  async function saveConnection() {
    if (writing || (!createdId && !connectionUsable)) return;
    setError(null);
    setBusy(true);
    try {
      let id = createdId;
      if (!id) {
        ({ id } = await api.createConnection({ ...form, api_key: key }));
        setCreatedId(id);
        setKey("");   // the key is on the server now; keeping a copy buys nothing
      }
      await api.putConfig({ active_connection_id: id });
      setConnected(form.name.trim());
      // Then ask the provider whether the thing just saved actually works
      // (#146). Deliberately after `setConnected`, deliberately unable to undo
      // it, and deliberately NOT awaited.
      //
      // The connection IS saved and active either way, so a wizard that
      // refused to move on because a key was rejected would trap someone whose
      // provider is merely down — a failed check is a warning beside the tick,
      // not a gate. And awaiting it would hold `busy`, which every control on
      // this step is disabled by: the Claude path's only honest probe is a
      // real (tiny) generation, so "Saving…" would sit there for seconds after
      // the save it names had finished.
      void api.checkConnection(id).then(setHealth).catch(() => {
        /* the check is a courtesy; a connection that saved is still saved */
      });
    } catch (err: any) {
      setError(err.detail ?? String(err));
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
    } catch (err: any) {
      // Put the preview back. Left applied, an unsaved theme looks chosen for
      // the rest of the session and then vanishes on the next reload, which
      // reads as the app losing the setting rather than never taking it.
      setTheme(previous);
      setError(err.detail ?? String(err));
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
    } catch (err: any) {
      setError(err.detail ?? String(err));
    } finally {
      setBusy(false);
    }
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
          Four questions and you're playing.
        </p>

        {/* Every step is named, not only the one you are on. Four questions is
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
            <h3>Connect a model</h3>
            <p className="wizard-intro">
              Grimoire writes through whichever model you point it at. Add one now, or
              skip — you can play by hand and set this up later on the Connections page.
            </p>
            {connected && <p className="config-msg save-flash">Connected to {connected} ✓</p>}
            {/* "Connected" has meant "saved and made active" since this wizard
                was written, which is not the same as "it works" — the whole of
                #146. When the check disagrees, say so here rather than letting
                the first scene be where they find out. */}
            {connected && health && !health.ok && (
              <p className="field-hint">
                Saved, but the provider refused: {health.detail || health.kind}. You can
                carry on and fix it later on the Connections page.
              </p>
            )}
            {/* Created but not active: the form is gone because re-submitting it
                would create a second connection, and what is left to do is the
                activation that failed. */}
            {!connected && createdId && (
              <p className="field-hint">
                {form.name.trim()} was created but could not be made active.
              </p>
            )}
            {!connected && !createdId && (
              <ConnectionForm
                value={form} onChange={setForm}
                apiKey={key} onApiKey={setKey}
                models={models} modelsError={modelsError}
              />
            )}
            <div className="wizard-footer">
              <button className="subtle" onClick={() => setStep(1)} disabled={writing}>Back</button>
              {connected
                ? <button className="btn-accent" onClick={() => setStep(3)} disabled={writing}>Next ▸</button>
                : (
                  <span className="wizard-actions">
                    <button className="subtle" onClick={() => setStep(3)} disabled={writing}>Skip</button>
                    <button className="btn-accent" onClick={saveConnection}
                            disabled={writing || (!createdId && !connectionUsable)}>
                      {busy ? "Saving…" : createdId ? "Retry activation" : "Save connection"}
                    </button>
                  </span>
                )}
            </div>
          </div>
        )}

        {step === 3 && (
          <div className="wizard-body">
            <h3>Pick a look</h3>
            <p className="wizard-intro">
              Applies as you click, and is changeable any time from Config.
            </p>
            <ThemePicker value={theme} onPick={pickTheme} disabled={writing} />
            <div className="wizard-footer">
              <button className="subtle" onClick={() => setStep(2)} disabled={writing}>Back</button>
              <button className="btn-accent" onClick={() => setStep(4)} disabled={writing}>
                {savingTheme ? "Saving…" : "Next ▸"}
              </button>
            </div>
          </div>
        )}

        {step === 4 && (
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
                    onKeyDown={(e) => { if (e.key === "Enter") createWorld(); }}
                  />
                  <button className="btn-accent" onClick={createWorld}
                          disabled={writing || !worldName.trim()}>
                    {busy ? "Creating…" : "Create"}
                  </button>
                </div>
              )}
            <div className="wizard-footer">
              <button className="subtle" onClick={() => setStep(3)} disabled={writing}>Back</button>
              {worldId || existingLibrary
                ? (
                  <span className="wizard-actions">
                    <button className="subtle" onClick={() => finish("/")} disabled={writing}>Finish</button>
                    <button className="btn-accent" onClick={() => finish("/campaigns/new")} disabled={writing}>
                      Start a campaign ▸
                    </button>
                  </span>
                )
                /* Disabled while a world is being created: leaving now unmounts
                   the only place that would report the result, and dismisses
                   setup for good whether or not the world landed. */
                : <button className="subtle" onClick={() => finish("/")} disabled={writing}>
                    Finish later
                  </button>}
            </div>
          </div>
        )}

        {/* A standing way out, for the steps whose own footer only moves
            forward. Step 4's footer always offers one, so it does not need this
            too. It sits beside Next rather than under the card as a sentence:
            leaving is a real answer to "four questions", and burying it in prose
            made it read as a warning about giving up. */}
        {step !== 4 && (
          <p className="wizard-skip">
            {/* Disabled for the same reason the step's own Next is: leaving
                mid-write races this step's config write against finish()'s.
                `busy` covers the connection step's activation, which is a config
                write like the theme's and was missed the first time round. */}
            <button className="link" onClick={() => finish("/")} disabled={writing}>
              Skip setup
            </button>
            {" — you can do all of this later from Config."}
          </p>
        )}
      </div>
    </PlainShell>
  );
}
