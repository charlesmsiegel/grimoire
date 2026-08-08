import { useEffect, useRef, useState } from "react";
import { api, type Availability } from "../api/client";
import { errMsg } from "./errMsg";
import { customDraft, greetingDraft, suggestionDraft, type SceneDraft } from "./sceneDraft";
import { useSceneSuggestions } from "./useSceneSuggestions";

export function SceneIdeaPicker({ cid, afterSid, ready, pcless, onPicked, onCancel }: {
  cid: string;
  afterSid: string | null;
  ready: boolean;
  pcless: boolean;
  /** `warning` is shown by the confirm pane: this component unmounts the
   *  instant a draft is emitted, so its own banner cannot carry one. */
  onPicked: (draft: SceneDraft, warning?: string) => void;
  onCancel: () => void;
}) {
  const [greetings, setGreetings] = useState<Availability[]>([]);
  const [direction, setDirection] = useState("");
  const [typed, setTyped] = useState("");
  const [inferring, setInferring] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { suggestions, picks, nextDate, busy, error: genError, refresh } =
    useSceneSuggestions(cid, afterSid, ready, pcless);

  useEffect(() => {
    api.availableGreetings(cid, afterSid ?? undefined)
      .then((all) => setGreetings(all.filter((g) => g.available && !!g.pcless === pcless)))
      .catch((err) => { setGreetings([]); setError(errMsg(err)); });
  }, [cid, afterSid, pcless]);

  // 4 slots: 2 greetings + 2 generated; greetings grow to 4 when nothing will generate
  const wantGenerated = ready && (suggestions === null || suggestions.length > 0);
  // with >2 available the LLM chooses; until it answers, show nothing rather than
  // cards that would shuffle. Empty/failed picks fall back to today's order.
  const rankPending = ready && greetings.length > 2 && picks === null;
  const picked = (picks ?? [])
    .map((id) => greetings.find((g) => g.id === id))
    .filter((g): g is Availability => g !== undefined);
  const orderedGreetings = picked.length ? picked : greetings;
  const greetingCards = rankPending ? [] : orderedGreetings.slice(0, wantGenerated ? 2 : 4);
  const generatedCards = (suggestions ?? []).slice(0, 4 - (rankPending ? 2 : greetingCards.length));

  // Read at EMIT time, not at click time: the date estimate arrives with the
  // suggestions, and an extraction call can easily outlast it. Without the ref
  // a draft built after the estimate landed would still carry the empty string
  // this render closed over.
  const latestDate = useRef(nextDate);
  useEffect(() => { latestDate.current = nextDate; }, [nextDate]);

  async function useTyped() {
    setError(null);         // clear a stale greetings-fetch banner on every path, not just this one
    const text = typed.trim();
    if (!text) {           // the blank path: no call, today's "Create manually"
      onPicked(customDraft("", null, latestDate.current, pcless));
      return;
    }
    if (!ready) {          // inference is an enhancement of this path, not a requirement
      onPicked(customDraft(text, null, latestDate.current, pcless));
      return;
    }
    setInferring(true);
    try {
      const intent = await api.sceneIntent(cid, text, pcless);
      onPicked(customDraft(text, intent, latestDate.current, pcless));
    } catch (err: any) {
      // A miss must leave a usable form, never a dead end — and the warning
      // travels with the draft, because this pane is about to unmount.
      onPicked(customDraft(text, null, latestDate.current, pcless),
               `${errMsg(err)} — continuing without inferred details.`);
    } finally {
      setInferring(false);
    }
  }

  const shown = error ?? genError;
  return (
    <>
      {shown && <div className="banner">{shown}</div>}

      <div className="picker">
        <input type="text" aria-label="Direction" className="grow"
               placeholder="Steer the generated ideas — e.g. something at sea"
               value={direction} onChange={(e) => setDirection(e.target.value)} />
        <button className="subtle" disabled={!ready || busy}
                onClick={() => refresh(direction)}>↻ Regenerate</button>
      </div>

      <div className="role">From a greeting</div>
      {rankPending && <div className="field-hint">Choosing…</div>}
      {!rankPending && greetingCards.length === 0 && <div className="field-hint">No available greetings.</div>}
      {greetingCards.map((g) => (
        <button className="chooser-card" key={g.id}
                onClick={() => onPicked(greetingDraft(g, latestDate.current, pcless))}>
          <span className="chooser-card-title">{g.name}</span>
          {g.unlocked && <span className="chip on">unlocked</span>}
        </button>
      ))}

      <div className="role">Generated</div>
      {!ready && <div className="field-hint">Set up an LLM connection in Config to generate.</div>}
      {ready && suggestions === null && <div className="field-hint">Generating…</div>}
      {generatedCards.map((s, i) => (
        <button className="chooser-card" key={i}
                onClick={() => onPicked(suggestionDraft(s, latestDate.current, pcless))}>
          <span className="chooser-card-title">{s.title}</span>
          <span className="chooser-card-premise">{s.premise}</span>
          <span className="field-hint">
            {s.cast.map((c) => c.name).join(", ")}{s.location ? ` · ${s.location.name}` : ""}
          </span>
        </button>
      ))}

      <div className="role">Your own</div>
      <textarea aria-label="Your own scene" rows={3} value={typed}
                placeholder="Describe how the scene starts — the date and place are read from this."
                onChange={(e) => setTyped(e.target.value)} />

      <div className="form-actions">
        <button className="subtle" onClick={onCancel}>Cancel</button>
        <button className="primary" disabled={inferring} onClick={useTyped}>
          {inferring ? "…" : typed.trim() ? "Use this →" : "Create blank scene"}
        </button>
      </div>
    </>
  );
}
