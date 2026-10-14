/** "Ask the history" (roadmap 12, E3): a reader's question, answered by a
 *  model that reads the campaign's history with tools.
 *
 *  The answer is a draft -- held on its run, never stored -- and it cites
 *  only scenes the run actually read; an answer citing nothing it read is
 *  "not enough to answer" rather than a claim. What the run looked at is shown
 *  under the answer, so a reader can check it. Nothing here writes. */
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api/client";
import { errorText } from "../../api/errors";
import { isAbortError, newAttemptId } from "../../api/stream";
import type { HistoryAnswer } from "../../api/types";
import { encodeSegment } from "../../urlSegment";
import { InvestigationTrace, outcomeSentence } from "./InvestigationTrace";

/** The longest question the server takes. */
export const QUESTION_CHARS = 500;

/** A question still being answered, kept past this panel: the run is
 *  detached on the server and spends whether or not anyone is watching, so
 *  leaving the page must not lose its answer or let a second one start. A
 *  remount re-attaches by the same attempt id, which the server adopts. */
type Pending = { attempt: string; question: string };

const pendingKey = (cid: string) => `grimoire.askHistory.${cid}`;

/** Every storage touch is wrapped: storage can throw (a private window,
 *  blocked site data), and a panel that cannot remember still asks. */
function readPending(cid: string): Pending | null {
  try {
    const raw = localStorage.getItem(pendingKey(cid));
    const v: unknown = raw ? JSON.parse(raw) : null;
    if (v && typeof v === "object" && typeof (v as Pending).attempt === "string"
        && typeof (v as Pending).question === "string") return v as Pending;
  } catch { /* nothing remembered */ }
  return null;
}

function writePending(cid: string, p: Pending | null): void {
  try {
    if (p) localStorage.setItem(pendingKey(cid), JSON.stringify(p));
    else localStorage.removeItem(pendingKey(cid));
  } catch { /* in memory only */ }
}

export function AskHistory({ cid, onClose }: { cid: string; onClose: () => void }) {
  const [question, setQuestion] = useState(() => readPending(cid)?.question ?? "");
  const [asking, setAsking] = useState(false);
  const [answer, setAnswer] = useState<HistoryAnswer | null>(null);
  const [error, setError] = useState<string | null>(null);
  const live = useRef<AbortController | null>(null);
  const [titles, setTitles] = useState<Record<string, string>>({});

  useEffect(() => {
    let current = true;
    api.listScenes(cid)
      .then((list) => {
        if (current) setTitles(Object.fromEntries(list.map((s) => [s.id, s.title])));
      })
      .catch(() => { /* links fall back to "a scene" */ });
    return () => { current = false; };
  }, [cid]);

  async function follow(p: Pending) {
    live.current?.abort();
    const ctl = new AbortController();
    live.current = ctl;
    setAsking(true);
    setError(null);
    setAnswer(null);
    try {
      const got = await api.askHistory(cid, p.question, ctl.signal, p.attempt);
      writePending(cid, null);
      setAnswer(got);
    } catch (err: unknown) {
      // An unmount stops only the watching; the run, and its record here,
      // stay for the next mount to pick up.
      if (isAbortError(err)) return;
      writePending(cid, null);
      setError(errorText(err));
    } finally {
      if (!ctl.signal.aborted) setAsking(false);
    }
  }

  /** A question left running by an earlier mount is followed, never asked
   *  again: the server must still hold its run. A run it has reaped (its
   *  answer was produced and nobody collected it in time) or lost to a
   *  restart is reported, and the question handed back to ask anew. */
  async function resume(p: Pending) {
    const ctl = new AbortController();
    live.current = ctl;
    setAsking(true);
    try {
      const held = await api.findHistoryQuestion(cid, p.attempt, ctl.signal);
      if (held) {
        // Followed as it stands, never re-posted: a re-post would face the
        // start's checks (the switch, the model) over an answer the run
        // already holds, and lose it if either had changed meanwhile.
        const got = await api.followHistoryQuestion(cid, held, p.attempt, ctl.signal);
        writePending(cid, null);
        setAnswer(got);
      } else {
        writePending(cid, null);
        setError("The earlier question's answer is no longer held. Ask it again to rerun it.");
      }
    } catch (err: unknown) {
      if (isAbortError(err)) return;
      writePending(cid, null);
      setError(errorText(err));
    }
    if (!ctl.signal.aborted) setAsking(false);
  }

  useEffect(() => {
    const p = readPending(cid);
    if (p) void resume(p);
    return () => live.current?.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once per campaign
  }, [cid]);

  function ask() {
    const q = question.trim();
    if (!q || asking) return;
    const p = { attempt: newAttemptId(), question: q };
    writePending(cid, p);
    void follow(p);
  }

  // A scene is named by its title, never its id; until the titles arrive (or
  // for a scene the list no longer holds) it is simply "a scene".
  const sceneLink = (sid: string) => (
    <Link to={`/campaigns/${encodeSegment(cid)}/scenes/${encodeSegment(sid)}`}>
      {titles[sid] || "a scene"}
    </Link>
  );
  const outcome = answer?.outcome;
  const result = outcome?.state === "completed" ? outcome.result : null;

  return (
    <section className="ask-history" aria-label="Ask the history">
      <div className="ask-history-head">
        <h3>Ask the history</h3>
        {/* Closing while asking would leave a run spending with no one
            watching; navigation is covered by the pending record, the
            Close button simply waits for the answer. */}
        <button type="button" className="subtle" disabled={asking} onClick={onClose}
                title={asking ? "Wait for the answer before closing" : undefined}>Close</button>
      </div>
      <p className="field-hint">
        A model reads this campaign's scenes and ledger to answer. Experimental: it may
        miss things, and the answer is not saved.
      </p>
      <textarea aria-label="Your question" rows={3} value={question} disabled={asking}
                maxLength={QUESTION_CHARS} onChange={(e) => setQuestion(e.target.value)} />
      <div className="form-actions">
        <button type="button" disabled={asking || !question.trim()} onClick={ask}>
          {asking ? "Reading the history…" : "Ask"}
        </button>
      </div>
      {error && <p className="continuity-error" role="alert">{error}</p>}
      {outcome && (
        <div className="ask-history-answer" role="status">
          {result ? (
            <>
              <p>{result.text}</p>
              <p className="field-hint">
                From: {result.citations.map((ref, i) => (
                  <span key={ref}>{i > 0 && ", "}{sceneLink(ref.replace(/^scene:/, ""))}</span>
                ))}
              </p>
            </>
          ) : <p>{outcomeSentence(outcome)}</p>}
          <InvestigationTrace trace={outcome.trace} sceneLink={sceneLink} />
        </div>
      )}
    </section>
  );
}

export default AskHistory;
