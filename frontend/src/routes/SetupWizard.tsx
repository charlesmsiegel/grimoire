import { useEffect, useState } from "react";
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

  // step 2 — the connection, unsaved until "Save connection"
  const [form, setForm] = useState<ConnectionFormValue>(BLANK_CONNECTION);
  const [key, setKey] = useState("");
  const [orModels, setOrModels] = useState<Model[]>([]);
  const [orError, setOrError] = useState(false);
  const [connected, setConnected] = useState<string | null>(null);

  // step 4 — the first world
  const [worldName, setWorldName] = useState("");
  const [worldId, setWorldId] = useState<string | null>(null);

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

  async function saveConnection() {
    if (!form.name.trim() || busy) return;
    setError(null);
    setBusy(true);
    try {
      const { id } = await api.createConnection({ ...form, api_key: key });
      await api.putConfig({ active_connection_id: id });
      setConnected(form.name.trim());
      setKey("");
    } catch (err: any) {
      setError(err.detail ?? String(err));
    } finally {
      setBusy(false);
    }
  }

  async function pickTheme(next: string) {
    setTheme(next);            // apply immediately; the wizard is the preview
    setError(null);
    try {
      await api.putConfig({ theme: next });
    } catch (err: any) {
      setError(err.detail ?? String(err));
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
          <StorageLocation />
          <div className="wizard-footer">
            <span />
            <button className="btn-accent" onClick={() => setStep(2)}>Next ▸</button>
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
          {connected
            ? <p className="config-msg save-flash">Connected to {connected} ✓</p>
            : (
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
                          disabled={busy || !form.name.trim()}>
                    {busy ? "Saving…" : "Save connection"}
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
          <ThemePicker value={theme} onPick={pickTheme} />
          <div className="wizard-footer">
            <button className="subtle" onClick={() => setStep(2)}>Back</button>
            <button className="btn-accent" onClick={() => setStep(4)}>Next ▸</button>
          </div>
        </div>
      )}

      {step === 4 && (
        <div className="wizard-body">
          <h3>Create your first world</h3>
          <p className="wizard-intro">
            A world holds the places, people and lore your campaigns draw on. Every
            campaign starts from one, so this is the last thing standing between you
            and play.
          </p>
          {worldId
            ? <p className="config-msg save-flash">Created {worldName.trim()} ✓</p>
            : (
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
            {worldId
              ? (
                <span className="wizard-actions">
                  <button className="subtle" onClick={() => finish("/")}>Finish</button>
                  <button className="btn-accent" onClick={() => finish("/campaigns/new")}>
                    Start a campaign ▸
                  </button>
                </span>
              )
              : <button className="subtle" onClick={() => finish("/")}>Finish later</button>}
          </div>
        </div>
      )}

      {/* A standing way out, for the steps whose own footer only moves forward.
          Step 4's footer always offers one, so it does not need this too. */}
      {step !== 4 && (
        <p className="wizard-skip">
          <button className="link" onClick={() => finish("/")}>Skip setup</button>
          {" — you can do all of this later from Config."}
        </p>
      )}
    </div>
  );
}
