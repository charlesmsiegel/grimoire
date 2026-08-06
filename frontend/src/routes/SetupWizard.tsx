import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { getModels, type Model } from "../api/models";
import {
  BLANK_CONNECTION, ConnectionForm, type ConnectionFormValue,
} from "../components/ConnectionForm";
import { StorageLocation } from "../components/StorageLocation";
import { ThemePicker } from "../components/ThemePicker";
import { useTheme } from "../theme/ThemeProvider";

const STEPS = ["Storage", "Connection", "Theme", "World"];

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
 *  `onDone` is what actually retires the wizard for this session. It is called
 *  alongside the `setup_done` write rather than left to a config refetch,
 *  because App decides between `/` and this wizard on its own state: leaving
 *  that to a re-read would race the navigation and bounce the user straight
 *  back in. */
export default function SetupWizard({ onDone }: { onDone: () => void }) {
  const navigate = useNavigate();
  const { name: theme, setTheme } = useTheme();
  const [step, setStep] = useState(1);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Two writes this component starts and then lets the user walk away from.
  // `PUT /api/config` is a read-modify-write of one file, so an in-flight theme
  // save overlapping the `setup_done` save can lose whichever landed first —
  // holding the step until it settles is what keeps them ordered.
  const [movingStore, setMovingStore] = useState(false);
  const [savingTheme, setSavingTheme] = useState(false);

  // step 2 — the connection, unsaved until "Save connection"
  const [form, setForm] = useState<ConnectionFormValue>(BLANK_CONNECTION);
  const [key, setKey] = useState("");
  const [orModels, setOrModels] = useState<Model[]>([]);
  const [orError, setOrError] = useState(false);
  const [connected, setConnected] = useState<string | null>(null);
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
    setCreatedId(null);
    setWorldId(null);
    setWorldName("");
    try {
      setExistingLibrary((await api.listWorlds()).length > 0);
    } catch (err: any) {
      // Not a guess in either direction: say so, and leave the step showing the
      // form, which is recoverable. Silently claiming "already stocked" would
      // strand a fresh user with no way to make a world.
      setExistingLibrary(false);
      setError(err.detail ?? "Moved, but the new library could not be read.");
    }
  }, []);

  useEffect(() => {
    let alive = true;
    getModels().then((m) => alive && setOrModels(m)).catch(() => alive && setOrError(true));
    return () => { alive = false; };
  }, []);

  /** Record that setup has been answered, then hand control back. Marking done
   *  is deliberately best-effort: failing to write a preference must not strand
   *  someone on the wizard, and the worst case is being offered it once more. */
  async function finish(to: string) {
    try {
      await api.putConfig({ setup_done: "on" });
    } catch {
      /* the flag is a convenience, not a gate */
    }
    onDone();
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
    if (busy || (!createdId && !connectionUsable)) return;
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
    } catch (err: any) {
      setError(err.detail ?? String(err));
    } finally {
      setBusy(false);
    }
  }

  async function pickTheme(next: string) {
    setTheme(next);            // apply immediately; the wizard is the preview
    setError(null);
    setSavingTheme(true);
    try {
      await api.putConfig({ theme: next });
    } catch (err: any) {
      setError(err.detail ?? String(err));
    } finally {
      setSavingTheme(false);
    }
  }

  async function createWorld() {
    const trimmed = worldName.trim();
    if (!trimmed || busy) return;
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
    <div className="page page-narrow view-anim wizard">
      <h1 className="page-h1">Welcome to Grimoire</h1>

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

      {error && <div className="banner error-banner">{error}</div>}

      {step === 1 && (
        <div className="wizard-body">
          <h3>Where should your library live?</h3>
          <p className="wizard-intro">
            Everything you make stays yours, as plain files. The default is fine —
            change it now only if you would rather it lived elsewhere.
          </p>
          <StorageLocation onPending={setMovingStore} onMoved={recheckStore} />
          <div className="wizard-footer">
            <span />
            {/* Label stays "Next" — the Move button is already saying
                "Moving…", and two controls with one name is a worse hint. */}
            <button className="btn-accent" onClick={() => setStep(2)} disabled={movingStore}>Next ▸</button>
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
              orModels={orModels} orError={orError}
            />
          )}
          <div className="wizard-footer">
            <button className="subtle" onClick={() => setStep(1)} disabled={busy}>Back</button>
            {connected
              ? <button className="btn-accent" onClick={() => setStep(3)}>Next ▸</button>
              : (
                <span className="wizard-actions">
                  <button className="subtle" onClick={() => setStep(3)} disabled={busy}>Skip</button>
                  <button className="btn-accent" onClick={saveConnection}
                          disabled={busy || (!createdId && !connectionUsable)}>
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
          <ThemePicker value={theme} onPick={pickTheme} disabled={savingTheme} />
          <div className="wizard-footer">
            <button className="subtle" onClick={() => setStep(2)} disabled={savingTheme}>Back</button>
            <button className="btn-accent" onClick={() => setStep(4)} disabled={savingTheme}>
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
                        disabled={busy || !worldName.trim()}>
                  {busy ? "Creating…" : "Create"}
                </button>
              </div>
            )}
          <div className="wizard-footer">
            <button className="subtle" onClick={() => setStep(3)} disabled={busy}>Back</button>
            {worldId || existingLibrary
              ? (
                <span className="wizard-actions">
                  <button className="subtle" onClick={() => finish("/")}>Finish</button>
                  <button className="btn-accent" onClick={() => finish("/campaigns/new")}>
                    Start a campaign ▸
                  </button>
                </span>
              )
              /* Disabled while a world is being created: leaving now unmounts
                 the only place that would report the result, and dismisses
                 setup for good whether or not the world landed. */
              : <button className="subtle" onClick={() => finish("/")} disabled={busy}>
                  Finish later
                </button>}
          </div>
        </div>
      )}

      {/* A standing way out, for the steps whose own footer only moves forward.
          Step 4's footer always offers one, so it does not need this too. */}
      {step !== 4 && (
        <p className="wizard-skip">
          {/* Disabled for the same reason the step's own Next is: leaving
              mid-write races this step's config write against finish()'s.
              `busy` covers the connection step's activation, which is a config
              write like the theme's and was missed the first time round. */}
          <button className="link" onClick={() => finish("/")}
                  disabled={busy || movingStore || savingTheme}>
            Skip setup
          </button>
          {" — you can do all of this later from Config."}
        </p>
      )}
    </div>
  );
}
