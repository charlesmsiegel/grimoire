import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { api, type CastChanges as Changes } from "../../api/client";

const EMPTY: Changes = { enter: [], leave: [], unknown: [] };

const isEmpty = (c: Changes) => !c.enter.length && !c.leave.length && !c.unknown.length;

/** What the turn that just landed says about the cast (#97, #98).
 *
 *  Three kinds of chip, each a proposal the reader answers:
 *
 *  - **enter** — a campaign character the prose named who is not on stage. Add
 *    seats them.
 *  - **leave** — a cast member the prose walked off. Remove drops them from the
 *    scene (they stay in the campaign's roster).
 *  - **unknown** — a name no record answers to. Create makes a campaign-side
 *    character and seats it in one step.
 *
 *  Nothing here applies on its own, and that is a correctness rule rather than
 *  a courtesy: seating an actor LOCKS a version campaign-side and a departure
 *  writes a line into the transcript, so a wrong guess applied silently is a
 *  wrong guess that cannot be taken back cleanly. The detector is a heuristic
 *  over one turn's prose and is deliberately generous; the confirm step is what
 *  makes that safe.
 *
 *  Dismissal splits by bucket, deliberately. An enter or unknown chip dismisses
 *  through the scene's stored `dismissed` list, so it stays gone for this scene.
 *  A leave chip only hides locally: the cue came out of one turn's text, and the
 *  next turn's scan will not repeat it — whereas a stored dismissal is keyed by
 *  character id and would also silence that character's future *enter*
 *  suggestions, which is not what "no, they didn't leave" means. */
export default function CastChanges(
  { cid, sid, hasPosts, refreshKey, onChanged }: {
    cid: string;
    sid: string;
    /** Whether the scene has any posts at all. A boolean rather than a count,
     *  because a count would be a lie in both directions: the transcript fetch
     *  is windowed (#94), so it neither grows when a turn lands in a long scene
     *  nor stands still when the reader pages backwards through an old one. */
    hasPosts: boolean;
    /** Bumped every time the parent re-reads the scene, which is what a landed
     *  turn does. This, not the post count, is what makes a scan happen after a
     *  turn in a scene long enough to be windowed. */
    refreshKey: number;
    /** A transition was applied; the parent re-reads the scene's cast. */
    onChanged: () => void;
  },
) {
  const [changes, setChanges] = useState<Changes>(EMPTY);
  // Departure chips the reader answered "Not yet". Keyed by the SENTENCE as
  // well as the actor: the same actor genuinely leaving three turns later is a
  // different chip and must be offered again, while a re-scan of the turn that
  // was already answered must not bring the same one back.
  const [notLeaving, setNotLeaving] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Which scene this panel is showing NOW, readable from a callback created
  // under a previous one: a scan started in scene A must not install its
  // candidates over scene B's after a switch.
  //
  //  A LAYOUT effect, for the reason `CastPanel`'s is: passive effects are
  //  scheduled in their own task, so a fetch settling between the commit of
  //  scene B and its passive effects reads a ref that still says A.
  const live = useRef(`${cid}/${sid}`);
  useLayoutEffect(() => { live.current = `${cid}/${sid}`; }, [cid, sid]);

  // Which scan is the newest. Two can be in flight at once -- a confirm reloads,
  // and the parent's scene re-read bumps `refreshKey` into a second scan -- and
  // they can land out of order, which without this would let the pre-confirm
  // result install itself on top and re-offer the chip just applied.
  const scan = useRef(0);

  const reload = useCallback(() => {
    const asked = `${cid}/${sid}`;
    const mine = ++scan.current;
    // "Nothing changed" over "nothing changed" keeps the state object it already
    // has, so React bails out rather than re-rendering the column for a result
    // identical to the one on screen. That is most turns.
    const settle = (c: Changes) => {
      if (live.current !== asked || mine !== scan.current) return;
      // A fresh scan retires the last action's error with it. Left standing, a
      // failed confirm would keep its banner over chips read from a later turn.
      setError(null);
      setChanges((prev) => (isEmpty(prev) && isEmpty(c) ? prev : c));
    };
    api.castChanges(cid, sid).then(settle).catch(() => settle(EMPTY));
  }, [cid, sid]);

  // Both triggers are dependencies, `hasPosts` included: reading a guard while
  // leaving it out of the deps is how a guard goes stale.
  useEffect(() => {
    setChanges(EMPTY);
    if (hasPosts) reload();
  }, [cid, sid, hasPosts, refreshKey, reload]);

  // Answered departures are forgotten on a SCENE change, not on every re-scan:
  // confirming any other chip bumps `refreshKey`, and clearing here would bring
  // back a departure the reader had just waved off.
  useEffect(() => { setNotLeaving([]); }, [cid, sid]);

  async function act(what: () => Promise<unknown>) {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await what();
      onChanged();
      reload();
    } catch (err: any) {
      setError(err.detail ?? String(err));
    } finally {
      setBusy(false);
    }
  }

  // Only departures filter locally. An enter or unknown chip answered "Dismiss"
  // is gone server-side by the time the re-scan lands, so a second, client-side
  // list of them would be a copy of state the server already holds.
  const { enter, unknown } = changes;
  const leave = changes.leave.filter((d) => !notLeaving.includes(`${d.id}/${d.quote}`));
  if (!enter.length && !leave.length && !unknown.length && !error) return null;

  return (
    <div className="column-section cast-changes">
      <div className="column-section-head">
        <span className="section-label">This turn</span>
        <span className="column-count">suggested</span>
      </div>
      {error && <p className="column-empty">{error}</p>}

      {enter.map((e) => (
        <div className="brief-row" key={`enter/${e.id}`}>
          <div className="brief-title">{e.name} is named but not in the scene</div>
          <div className="brief-meta">Mentioned by {e.mentioned_by.join(", ")}</div>
          <div className="chips">
            <button className="chip" disabled={busy}
                    onClick={() => act(() => api.addToCast(cid, sid, { kind: e.kind, id: e.id }))}>
              Add {e.name}
            </button>
            <button className="chip" disabled={busy}
                    onClick={() => act(() => api.dismissSuggestion(cid, sid, e.id))}>
              Dismiss
            </button>
          </div>
        </div>
      ))}

      {leave.map((d) => (
        <div className="brief-row" key={`leave/${d.id}`}>
          <div className="brief-title">{d.name} seems to have left</div>
          <div className="cast-quote">“{d.quote}”</div>
          <div className="chips">
            <button className="chip" disabled={busy}
                    onClick={() => act(() => api.removeFromCast(cid, sid, d.kind, d.id))}>
              Remove {d.name}
            </button>
            <button className="chip" disabled={busy}
                    onClick={() => setNotLeaving((h) => [...h, `${d.id}/${d.quote}`])}>
              Not yet
            </button>
          </div>
        </div>
      ))}

      {unknown.map((u) => (
        <div className="brief-row" key={`unknown/${u.name}`}>
          <div className="brief-title">{u.name} is new to this campaign</div>
          <div className="brief-meta">Mentioned by {u.mentioned_by.join(", ")}</div>
          <div className="chips">
            <button className="chip" disabled={busy}
                    onClick={() => act(() => api.createEmergentCast(cid, sid, u.name))}>
              Create {u.name}
            </button>
            <button className="chip" disabled={busy}
                    onClick={() => act(() => api.dismissSuggestion(cid, sid, u.name))}>
              Dismiss
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}
