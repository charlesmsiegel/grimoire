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
  // scene mode is picked first; nothing is fetched until then
  const [mode, setMode] = useState<"pc" | "offscreen" | null>(null);
  const [greetings, setGreetings] = useState<Availability[]>([]);
  // null = still generating; [] = nothing to offer (no key, empty, or failed)
  const [suggestions, setSuggestions] = useState<SceneSuggestion[] | null>(keySet ? null : []);
  // the same LLM call ranks greetings when >2 are available; null = pending
  const [picks, setPicks] = useState<string[] | null>(keySet ? null : []);
  // the same call estimates when the next scene opens; undefined until it answers
  const [nextDate, setNextDate] = useState<string | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!mode) return;
    api.availableGreetings(cid, afterSid ?? undefined)
      .then((all) => setGreetings(all.filter(
        (g) => g.available && !!g.pcless === (mode === "offscreen"))))
      .catch((err) => { setGreetings([]); setError(errMsg(err)); });
  }, [cid, afterSid, mode]);

  useEffect(() => {
    if (!keySet || !mode) return;
    api.sceneSuggestions(cid, afterSid ?? undefined, mode === "offscreen")
      .then((r) => { setSuggestions(r.suggestions); setPicks(r.greeting_picks ?? []); setNextDate(r.next_date || undefined); })
      .catch((err) => { setSuggestions([]); setPicks([]); setError(errMsg(err)); });
  }, [cid, afterSid, keySet, mode]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape" && !busy) onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, busy]);

  // 4 slots: 2 greetings + 2 generated; greetings grow to 4 when nothing will generate
  const wantGenerated = keySet && (suggestions === null || suggestions.length > 0);
  // with >2 available the LLM chooses; until it answers, show nothing rather than
  // cards that would shuffle. Empty/failed picks fall back to today's order.
  const rankPending = keySet && greetings.length > 2 && picks === null;
  const picked = (picks ?? [])
    .map((id) => greetings.find((g) => g.id === id))
    .filter((g): g is Availability => g !== undefined);
  const orderedGreetings = picked.length ? picked : greetings;
  const greetingCards = rankPending ? [] : orderedGreetings.slice(0, wantGenerated ? 2 : 4);
  const generatedCards = (suggestions ?? []).slice(0, 4 - (rankPending ? 2 : greetingCards.length));

  async function create(seed: (sid: string) => Promise<{ id?: string; prompt?: string }>,
                        title?: string, date?: string) {
    setBusy(true);
    setError(null);
    let created: string | null = null;
    try {
      const { id } = await api.createScene(cid, title, date, mode === "offscreen");
      created = id;
      const r = await seed(id);
      if (r.prompt !== undefined) onCreated(r.id ?? id, r.prompt);
      else onCreated(r.id ?? id);
    } catch (err: any) {
      // a half-seeded scene would be a stray — remove it before surfacing the error
      if (created) await api.deleteScene(cid, created).catch(() => {});
      setError(errMsg(err));
    } finally {
      setBusy(false);
    }
  }

  const pickManual = () => create(async () => ({}), undefined, nextDate);
  const pickGreeting = (gid: string) => create(async (sid) => {
    // the backend retitles the scene to the greeting's name; adopt the new id
    const { id } = await api.startFromGreeting(cid, sid, gid);
    return { id };
  }, undefined, nextDate);
  const pickSuggestion = (s: SceneSuggestion) => create(async (sid) => {
    if (s.cast.length) {
      // one request; members already cast come back in `skipped`, which is fine
      await api.addCastBatch(cid, sid, s.cast.map((c) => ({ kind: c.kind, id: c.id })));
    }
    if (s.location) await api.setSceneLocation(cid, sid, s.location.id);
    return { prompt: s.premise };
  }, s.title, s.date || nextDate);

  return (
    <div className="chooser-backdrop" role="dialog" aria-label="New scene"
         onClick={() => { if (!busy) onClose(); }}>
      <div className="chooser" onClick={(e) => e.stopPropagation()}>
        <h3>New scene</h3>
        {error && <div className="banner">{error}</div>}

        {mode === null ? (
          <>
            <div className="role">What kind of scene?</div>
            <button className="chooser-card" onClick={() => setMode("pc")}>
              <span className="chooser-card-title">With your PC</span>
              <span className="chooser-card-premise">Your player character takes part.</span>
            </button>
            <button className="chooser-card" onClick={() => setMode("offscreen")}>
              <span className="chooser-card-title">Offscreen (NPCs only)</span>
              <span className="chooser-card-premise">
                What happens away from your PC — NPC plans, motivations, and events you don't witness.
              </span>
            </button>
            <div className="form-actions">
              <button className="subtle" onClick={onClose}>Cancel</button>
            </div>
          </>
        ) : (
        <>
        <div className="role">From a greeting</div>
        {rankPending && <div className="field-hint">Choosing…</div>}
        {!rankPending && greetingCards.length === 0 && <div className="field-hint">No available greetings.</div>}
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
        </>
        )}
      </div>
    </div>
  );
}
