import { useEffect, useState } from "react";
import { api, type Availability, type SceneSuggestion } from "../api/client";

// LLM-backed endpoints 502 with an object detail; coerce so it renders as text.
function errMsg(err: any): string {
  const d = err?.detail;
  return typeof d === "string" ? d : (d?.detail ?? String(err));
}

export function NewSceneChooser({ cid, afterSid, keySet, onClose, onCreated }: {
  cid: string;
  afterSid: string | null;          // ranking reference: the selected (or latest) scene
  keySet: boolean;
  onClose: () => void;
  onCreated: (sid: string, initialPrompt?: string) => void;
}) {
  const [greetings, setGreetings] = useState<Availability[]>([]);
  // null = still generating; [] = nothing to offer (no key, empty, or failed)
  const [suggestions, setSuggestions] = useState<SceneSuggestion[] | null>(keySet ? null : []);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.availableGreetings(cid, afterSid ?? undefined)
      .then((all) => setGreetings(all.filter((g) => g.available)))
      .catch((err) => { setGreetings([]); setError(errMsg(err)); });
  }, [cid, afterSid]);

  useEffect(() => {
    if (!keySet) return;
    api.sceneSuggestions(cid)
      .then((r) => setSuggestions(r.suggestions))
      .catch((err) => { setSuggestions([]); setError(errMsg(err)); });
  }, [cid, keySet]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape" && !busy) onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, busy]);

  // 4 slots: 2 greetings + 2 generated; greetings grow to 4 when nothing will generate
  const wantGenerated = keySet && (suggestions === null || suggestions.length > 0);
  const greetingCards = greetings.slice(0, wantGenerated ? 2 : 4);
  const generatedCards = (suggestions ?? []).slice(0, 4 - greetingCards.length);

  async function create(seed: (sid: string) => Promise<string | undefined>) {
    setBusy(true);
    setError(null);
    let created: string | null = null;
    try {
      const { id } = await api.createScene(cid);
      created = id;
      const prompt = await seed(id);
      if (prompt !== undefined) onCreated(id, prompt);
      else onCreated(id);
    } catch (err: any) {
      // a half-seeded scene would be a stray — remove it before surfacing the error
      if (created) await api.deleteScene(cid, created).catch(() => {});
      setError(errMsg(err));
    } finally {
      setBusy(false);
    }
  }

  const pickManual = () => create(async () => undefined);
  const pickGreeting = (gid: string) => create(async (sid) => {
    await api.startFromGreeting(cid, sid, gid);
    return undefined;
  });
  const pickSuggestion = (s: SceneSuggestion) => create(async (sid) => {
    if (s.cast.length) {
      // one request; members already cast come back in `skipped`, which is fine
      await api.addCastBatch(cid, sid, s.cast.map((c) => ({ kind: c.kind, id: c.id })));
    }
    if (s.location) await api.setSceneLocation(cid, sid, s.location.id);
    return s.premise;
  });

  return (
    <div className="chooser-backdrop" role="dialog" aria-label="New scene"
         onClick={() => { if (!busy) onClose(); }}>
      <div className="chooser" onClick={(e) => e.stopPropagation()}>
        <h3>New scene</h3>
        {error && <div className="banner">{error}</div>}

        <div className="role">From a greeting</div>
        {greetingCards.length === 0 && <div className="field-hint">No available greetings.</div>}
        {greetingCards.map((g) => (
          <button className="chooser-card" key={g.id} disabled={busy} onClick={() => pickGreeting(g.id)}>
            <span className="chooser-card-title">{g.name}</span>
            {g.unlocked && <span className="chip on">unlocked</span>}
          </button>
        ))}

        <div className="role">Generated</div>
        {!keySet && <div className="field-hint">Set an OpenRouter key in Config to generate.</div>}
        {keySet && suggestions === null && <div className="field-hint">Generating…</div>}
        {generatedCards.map((s, i) => (
          <button className="chooser-card" key={i} disabled={busy} onClick={() => pickSuggestion(s)}>
            <span className="chooser-card-title">{s.title}</span>
            <span className="chooser-card-premise">{s.premise}</span>
            <span className="field-hint">
              {s.cast.map((c) => c.name).join(", ")}{s.location ? ` · ${s.location.name}` : ""}
            </span>
          </button>
        ))}

        <div className="form-actions">
          <button className="subtle" disabled={busy} onClick={onClose}>Cancel</button>
          <button className="primary" disabled={busy} onClick={pickManual}>Create manually</button>
        </div>
      </div>
    </div>
  );
}
