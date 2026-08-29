import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Availability, type SceneIdea, type SceneIdeaDraft,
         type SceneSuggestion } from "../api/client";
import { errorText } from "../api/errors";
import { ErrorNote } from "./ErrorNote";
import { customDraft, greetingDraft, savedDraft, suggestionDraft,
         type SceneDraft } from "./sceneDraft";
import type { SceneSuggestionsState } from "./useSceneSuggestions";

/** How many saved ideas the picker shows before the "show all" toggle — the
 *  same 4-slot budget the greeting and generated groups share between them. */
const SAVED_SLOTS = 4;

/** The generated half of the picker (suggestions/picks/nextDate/busy/error/
 *  suggest) and the typed `direction` both live in `NewSceneChooser` now, not
 *  here — this component only renders them. That is what makes **Back**
 *  cheap: it unmounts this pane, and since the state it used to own now lives
 *  one level up, unmounting costs nothing (issue #319). Before this, Back
 *  cleared `draft`, which unmounted the picker and, with it, the
 *  `useSceneSuggestions` instance; remounting re-ran the hook's mount effect
 *  at `rank=true` — a fresh, expensive, re-shufflable LLM call for what the
 *  user experiences as "go back", and it also discarded whatever direction
 *  they had typed. The greeting fetch below is the one piece that stays
 *  local: a greeting another client played meanwhile disappearing on Back is
 *  wanted, and it is cheap enough not to matter. The ledger read beside it
 *  stays local for the same reason and one more: this pane is what writes to
 *  it, so it is also what has to re-read it.
 *
 *  Nothing in the generated group happens until it is asked for: `asked` is
 *  false until the reader presses **Suggest ideas**, and that press is the
 *  only thing in this pane that spends a generation. Everything else on the
 *  screen -- greetings, the saved ledger, a blank scene, their own typed
 *  premise -- works without one, which is what the picker showed anyone with
 *  no model configured all along. */
export function SceneIdeaPicker({ cid, afterSid, ready, pcless, direction, onDirectionChange,
                                  asked, suggestions, picks, nextDate, busy, error: genError,
                                  suggest, onPicked, onCancel }: {
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
  // The saved half of the ledger (#88). Greeting-sourced rows are dropped
  // here rather than server-side: they have their own group below, ordered by
  // the LLM's ranking and carrying an `unlocked` chip this projection has no
  // room for, so showing them twice would be strictly worse than once.
  const [saved, setSaved] = useState<SceneIdea[]>([]);
  const [showDismissed, setShowDismissed] = useState(false);
  const [showAll, setShowAll] = useState(false);
  // Which generated cards this session has already saved. The saved copy also
  // appears under Saved on the next read, so without this the same idea can be
  // filed twice with two ids and no way to tell them apart.
  //
  // Keyed by the suggestion OBJECT, not by its index. An index is a position
  // in a list that a regenerate replaces wholesale, and a save is in flight
  // across that replacement: the reply lands, and the save's callback then
  // files index 0 against whatever idea now sits at index 0, labelling a
  // brand-new card "Saved" and refusing to file it (Codex, review). Every
  // reply is freshly parsed, so its objects are new ones and an entry made
  // against a replaced card simply never matches again -- which is also why
  // this needs no reset when `suggestions` changes.
  const [kept, setKept] = useState<Set<SceneSuggestion>>(new Set());
  // A save in flight. Save is idempotent server-side (an identical standing
  // idea returns its existing id rather than a second one), so this is about
  // the reader seeing that their click landed rather than about correctness --
  // but both halves are wanted: the button must not look inert, and two
  // sessions racing must not file two copies either.
  const [saving, setSaving] = useState(false);
  const [typed, setTyped] = useState("");
  const [inferring, setInferring] = useState(false);
  // Raw, so a generation the model could not be reached for reads as the
  // offline recovery rather than as a socket error (#210).
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    api.availableGreetings(cid, afterSid ?? undefined)
      .then((all) => setGreetings(all.filter((g) => g.available && !!g.pcless === pcless)))
      .catch((err) => { setGreetings([]); setError(err); });
  }, [cid, afterSid, pcless]);

  // `used` entries are deliberately not shown: they became scenes, and those
  // are in the rail behind this modal. Mode matters as much as status -- an
  // idea saved for an offscreen scene casts nobody the player can be, so
  // offering it in a PC scene would seat the wrong people.
  const loadSaved = useCallback(() => {
    // `false`: no greeting rows. This pane renders greetings from the ranked
    // `availableGreetings` read above and filters every composed one out, so
    // asking for them costs a second full sweep of the campaign's greeting
    // frontmatter, on the hottest path in the app, to render nothing.
    api.listSceneIdeas(cid, false)
      // `source !== "greeting"` still, belt and braces: the query param is a
      // request, and a row that arrived anyway must not reach the Saved group.
      .then((all) => setSaved(all.filter((i) => i.source !== "greeting"
                                                && i.pcless === pcless
                                                && i.status !== "used")))
      // A ledger that will not load must not cost the reader the rest of the
      // picker: greetings, generated cards and their own typed idea all still
      // work without it.
      .catch((err) => { setSaved([]); setError(err); });
  }, [cid, pcless]);
  useEffect(loadSaved, [loadSaved]);

  const active = saved.filter((i) => i.status === "active");
  const dismissed = saved.filter((i) => i.status === "dismissed");
  // The ledger is unbounded — nothing prunes it, and a long campaign
  // accumulates — so it gets a slot budget like the other groups rather than
  // pushing greetings and generated cards off the bottom of the modal. The
  // server orders newest first, so what shows is what was saved most recently.
  const shownActive = showAll ? active : active.slice(0, SAVED_SLOTS);

  // `pcless` LAST, so an `idea` cannot carry a mode that contradicts the one
  // this picker is running in -- the mode decides which cast tokens the server
  // will keep, so a wrong one silently empties the cast.
  function save(idea: SceneIdeaDraft, done?: () => void) {
    setError(null);
    setSaving(true);
    api.saveSceneIdea(cid, { ...idea, pcless })
      .then(() => { done?.(); loadSaved(); })
      .catch((err) => setError(err))
      .finally(() => setSaving(false));
  }

  function setStatus(lid: string, status: "active" | "dismissed") {
    setError(null);
    api.setSceneIdeaStatus(cid, lid, status)
      .then(loadSaved)
      .catch((err) => setError(err));
  }

  // 4 slots: 2 greetings + 2 generated; greetings grow to 4 when nothing will
  // generate -- no LLM connection, or a reply that came back empty. With one
  // configured the ideas are fetched on open, so the ordinary picker is the
  // 2 + 2 split and the greetings in it are the two the ranking chose.
  const wantGenerated = ready && asked && (suggestions === null || suggestions.length > 0);
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
      //
      // Text, not the rejection, and so no offline note (#210): this is the
      // one LLM call here whose failure does not stop the reader. They get the
      // form they asked for either way, and telling someone mid-flow to go to
      // another page would be advice against what they are already doing. The
      // banner above says it for every call that DOES stop.
      onPicked(customDraft(text, null, latestDate.current, pcless),
               `${errorText(err)} — continuing without inferred details.`);
    } finally {
      setInferring(false);
    }
  }

  /** Cast/location/date as the ledger stores them: ids, not the resolved names
   *  the card was rendered from. The server re-validates them anyway, and will
   *  again on every read. */
  function asDraft(s: SceneSuggestion): SceneIdeaDraft {
    return { title: s.title, premise: s.premise, date: s.date ?? "",
             cast: s.cast.map((c) => `${c.kind}:${c.id}`),
             location: s.location?.id ?? "", source: "llm" };
  }

  const shown = error ?? genError;
  return (
    <>
      {shown != null && <div className="banner"><ErrorNote err={shown} /></div>}

      <div className="role">Saved</div>
      {active.length === 0 && (
        <div className="field-hint">Nothing saved yet — Save keeps an idea for another day.</div>
      )}
      {shownActive.map((i) => (
        <div className="chooser-row" key={i.id}>
          <button className="chooser-card" disabled={inferring}
                  onClick={() => onPicked(savedDraft(i, latestDate.current, pcless))}>
            <span className="chooser-card-title">{i.title}</span>
            <span className="chooser-card-premise">{i.premise}</span>
            <span className="field-hint">
              {i.cast.map((c) => c.name).join(", ")}{i.location ? ` · ${i.location.name}` : ""}
            </span>
          </button>
          <button className="subtle" aria-label={`Dismiss ${i.title}`}
                  onClick={() => setStatus(i.id, "dismissed")}>×</button>
        </div>
      ))}
      {active.length > SAVED_SLOTS && (
        <button className="subtle" onClick={() => setShowAll((v) => !v)}>
          {showAll ? "Show fewer" : `Show all ${active.length} saved`}
        </button>
      )}
      {dismissed.length > 0 && (
        <button className="subtle" onClick={() => setShowDismissed((v) => !v)}>
          {showDismissed ? "Hide dismissed" : `Show dismissed (${dismissed.length})`}
        </button>
      )}
      {showDismissed && dismissed.map((i) => (
        <div className="chooser-row" key={i.id}>
          {/* not pickable: a dismissed idea comes back to the list first, so
              restoring is a deliberate step rather than a side effect of a click */}
          <span className="field-hint grow">{i.title}</span>
          {/* the title is in the label, not just the row beside it: several of
              these stack, and "Restore, Restore, Restore" is what a screen
              reader would otherwise read out */}
          <button className="subtle" aria-label={`Restore ${i.title}`}
                  onClick={() => setStatus(i.id, "active")}>Restore</button>
        </div>
      ))}

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
      {/* The direction box and its button sit in this group rather than at the
          top of the modal, where they read as steering the whole picker. They
          steer one group, and that group is the one that costs money: the
          button below is the only thing in the picker that spends a
          generation, and it is next to what it pays for. */}
      <div className="picker idea-direction">
        <input type="text" aria-label="Direction" className="grow"
               placeholder="Steer the generated ideas — e.g. something at sea"
               value={direction} onChange={(e) => onDirectionChange(e.target.value)} />
        {/* One control and one call: `suggest` ranks until a ranking has
            landed and regenerates after that, which is a distinction this pane
            cannot make (it knows a press happened, not that a reply came
            back). The label is stable while `busy` so it stays the same
            control to look at -- and disabled, so it cannot be pressed twice. */}
        {/* `inferring` too, not just `busy`: an extraction in flight is a
            draft on its way to the confirm form, and this pane unmounts the
            moment it arrives. A generation started here would be paid for and
            then thrown away with the component -- which is the whole thing
            this button exists to stop. */}
        <button className="subtle" disabled={!ready || busy || inferring}
                onClick={() => { setError(null); suggest(direction); }}>
          {asked ? "↻ Regenerate" : "✨ Suggest ideas"}
        </button>
      </div>
      {!ready && <div className="field-hint">Set up an LLM connection in Config to generate.</div>}
      {ready && asked && suggestions === null && <div className="field-hint">Generating…</div>}
      {ready && asked && suggestions !== null && suggestions.length === 0 && !busy && genError == null && (
        <div className="field-hint">No ideas came back — Regenerate, or steer it and try again.</div>
      )}
      {generatedCards.map((s, i) => (
        <div className="chooser-row" key={i}>
          <button className="chooser-card" disabled={inferring}
                  onClick={() => onPicked(suggestionDraft(s, latestDate.current, pcless))}>
            <span className="chooser-card-title">{s.title}</span>
            <span className="chooser-card-premise">{s.premise}</span>
            <span className="field-hint">
              {s.cast.map((c) => c.name).join(", ")}{s.location ? ` · ${s.location.name}` : ""}
            </span>
          </button>
          {/* The whole point of the ledger: Regenerate used to be the only way
              past a card, and it threw away everything it replaced. The label
              carries the title as well as the state — several of these sit on
              one screen, and an aria-label overrides the text, so a fixed one
              would leave them indistinguishable. */}
          <button className="subtle" disabled={kept.has(s) || saving}
                  aria-label={`${kept.has(s) ? "Saved" : "Save"} ${s.title}`}
                  onClick={() => save(asDraft(s), () => setKept((k) => new Set(k).add(s)))}>
            {kept.has(s) ? "Saved" : "Save"}
          </button>
        </div>
      ))}

      <div className="role">Your own</div>
      <textarea aria-label="Your own scene" rows={3} value={typed}
                placeholder="Describe how the scene starts — the date and place are read from this."
                onChange={(e) => setTyped(e.target.value)} />

      <div className="form-actions">
        <button className="subtle" onClick={onCancel}>Cancel</button>
        {/* Saving skips the extraction deliberately: it is an LLM call whose
            result is only used to pre-fill the confirm form, and this path is
            not going there. The date and place are read from the text on the
            day the idea is actually picked. */}
        <button className="subtle" disabled={!typed.trim() || inferring || saving}
                onClick={() => save({ premise: typed.trim(), source: "user" },
                                    () => setTyped(""))}>
          Save for later
        </button>
        <button className="primary" disabled={inferring} onClick={useTyped}>
          {inferring ? "…" : typed.trim() ? "Use this →" : "Create blank scene"}
        </button>
      </div>
    </>
  );
}
