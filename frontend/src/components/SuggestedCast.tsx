import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { api, type Suggestion } from "../api/client";
import { errMsg } from "./errMsg";

/** One shared empty list, so a scan that finds nothing lands as the same value
 *  the state already holds: React bails out of the update instead of
 *  re-rendering every host of this strip on every refresh. */
const NONE: Suggestion[] = [];

/** "Who should appear" (#96): the characters the backend's mention-scan found
 *  named in the cards of whoever is already in the scene, who have not appeared
 *  in this campaign yet and were not dismissed here.
 *
 *  Advisory, so it fails quiet: a scan that errors renders nothing rather than
 *  putting a banner over the panel that hosts it. Acting on a chip is a write,
 *  though, and a write that fails says so.
 *
 *  Accept is deliberately the ordinary cast-add path (`api.addToCast` with no
 *  version), which is what locks the character's default version on first
 *  appearance — a suggestion must not become a second way to seat someone. */
export function SuggestedCast({ cid, sid, nameOf, refreshKey, onCast }: {
  cid: string;
  sid: string;
  /** Resolves the `mentioned_by` character ids the scan returns to names. The
   *  host already holds the campaign's character list; this component does not
   *  refetch it. Falls back to the id. */
  nameOf?: (id: string) => string;
  /** Bumped by the host when the scene's messages change: the scan reads the
   *  cards of who is *currently* cast, so it goes stale as the scene moves. */
  refreshKey?: number;
  /** A character was just seated — the host reloads its own cast list. */
  onCast?: () => void;
}) {
  const [suggestions, setSuggestions] = useState<Suggestion[]>(NONE);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<string | null>(null);

  // Which scene this strip is showing NOW, readable from a callback created
  // under a previous one: a scan that answers after the reader has moved on
  // would otherwise offer scene A's mentions as scene B's cast.
  //
  // A LAYOUT effect for the reason `OpenerComposer`'s `live` documents at
  // length: a passive effect is scheduled in its own task, and a promise
  // settling in that gap reads a ref that still names the scene just left.
  const live = useRef(`${cid}/${sid}`);
  useLayoutEffect(() => { live.current = `${cid}/${sid}`; }, [cid, sid]);

  const reload = useCallback(async () => {
    const asked = `${cid}/${sid}`;
    try {
      const found = await api.getSuggestions(cid, sid);
      if (live.current === asked) setSuggestions(found.length ? found : NONE);
    } catch {
      if (live.current === asked) setSuggestions(NONE);
    }
  }, [cid, sid]);

  useEffect(() => { setError(null); reload(); }, [reload, refreshKey]);

  async function act(s: Suggestion, run: () => Promise<unknown>) {
    if (pending) return;
    setError(null);
    setPending(s.character);
    try {
      await run();
      await reload();
    } catch (err: any) {
      setError(errMsg(err));
    } finally {
      setPending(null);
    }
  }

  const accept = (s: Suggestion) => act(s, async () => {
    await api.addToCast(cid, sid, { kind: "characters", id: s.character, role: "npc" });
    onCast?.();
  });

  const dismiss = (s: Suggestion) =>
    act(s, () => api.dismissSuggestion(cid, sid, s.character));

  if (!suggestions.length && !error) return null;

  return (
    <div className="suggested-cast">
      <div className="role">Suggested cast</div>
      {error && <div className="banner">{error}</div>}
      {suggestions.map((s) => (
        <div className="cast-row" key={s.character}>
          <span>{s.name}</span>
          <span className="role">
            mentioned by {s.mentioned_by.map((id) => nameOf?.(id) ?? id).join(", ")}
          </span>
          {/* Labelled, not just glyph-and-name: the panel above already has an
              "Add", so the accessible name has to say who each one seats. */}
          <span className="row-actions">
            <button className="primary" aria-label={`Add ${s.name} to the scene`}
                    disabled={!!pending} onClick={() => accept(s)}>Add</button>
            <button className="subtle" aria-label={`Dismiss ${s.name}`}
                    disabled={!!pending} onClick={() => dismiss(s)}>Dismiss</button>
          </span>
        </div>
      ))}
    </div>
  );
}
