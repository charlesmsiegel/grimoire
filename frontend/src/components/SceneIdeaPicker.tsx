import { useEffect, useRef, useState } from "react";
import { api, type Availability } from "../api/client";
import { errMsg } from "./errMsg";
import { customDraft, greetingDraft, suggestionDraft, type SceneDraft } from "./sceneDraft";
import type { SceneSuggestionsState } from "./useSceneSuggestions";

/** The generated half of the picker (suggestions/picks/nextDate/busy/error/
 *  refresh) and the typed `direction` both live in `NewSceneChooser` now, not
 *  here — this component only renders them. That is what makes **Back**
 *  cheap: it unmounts this pane, and since the state it used to own now lives
 *  one level up, unmounting costs nothing (issue #319). Before this, Back
 *  cleared `draft`, which unmounted the picker and, with it, the
 *  `useSceneSuggestions` instance; remounting re-ran the hook's mount effect
 *  at `rank=true` — a fresh, expensive, re-shufflable LLM call for what the
 *  user experiences as "go back", and it also discarded whatever direction
 *  they had typed. The greeting fetch below is the one piece that stays
 *  local: a greeting another client played meanwhile disappearing on Back is
 *  wanted, and it is cheap enough not to matter. */
export function SceneIdeaPicker({ cid, afterSid, ready, pcless, direction, onDirectionChange,
                                  suggestions, picks, nextDate, busy, error: genError, refresh,
                                  onPicked, onCancel }: {
  cid: string;
  afterSid: string | null;
  ready: boolean;
  pcless: boolean;
  direction: string;
  onDirectionChange: (direction: string) => void;
  /** `warning` is shown by the confirm pane: this component unmounts the
   *  instant a draft is emitted, so its own banner cannot carry one. */
  onPicked: (draft: SceneDraft, warning?: string) => void;
  onCancel: () => void;
} & SceneSuggestionsState) {
  const [greetings, setGreetings] = useState<Availability[]>([]);
  const [typed, setTyped] = useState("");
  const [inferring, setInferring] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
      const draft = customDraft(text, intent, latestDate.current, pcless);
      // fails or returns nothing -> a hint that metadata could not be inferred
      const empty = intent !== null && !intent.title && !intent.date
        && !intent.location && intent.cast.length === 0;
      if (empty) onPicked(draft, "Nothing could be inferred from that — fill in the details below.");
      else onPicked(draft);
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
               value={direction} onChange={(e) => onDirectionChange(e.target.value)} />
        <button className="subtle" disabled={!ready || busy}
                onClick={() => { setError(null); refresh(direction); }}>↻ Regenerate</button>
      </div>

      <div className="role">From a greeting</div>
      {rankPending && <div className="field-hint">Choosing…</div>}
      {!rankPending && greetingCards.length === 0 && <div className="field-hint">No available greetings.</div>}
      {greetingCards.map((g) => (
        <button className="chooser-card" key={g.id} disabled={inferring}
                onClick={() => onPicked(greetingDraft(g, latestDate.current, pcless))}>
          <span className="chooser-card-title">{g.name}</span>
          {g.unlocked && <span className="chip on">unlocked</span>}
        </button>
      ))}

      <div className="role">Generated</div>
      {!ready && <div className="field-hint">Set up an LLM connection in Config to generate.</div>}
      {ready && suggestions === null && <div className="field-hint">Generating…</div>}
      {generatedCards.map((s, i) => (
        <button className="chooser-card" key={i} disabled={inferring}
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
