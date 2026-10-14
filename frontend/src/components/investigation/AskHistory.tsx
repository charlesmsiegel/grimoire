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
import { BudgetLevel } from "./BudgetLevel";
import { pendingKey, readPending, settles, writePending } from "./pending";

/** The longest question the server takes. */
export const QUESTION_CHARS = 500;

/** A question still being answered, kept past this panel (`pending.ts`). */
type Pending = { attempt: string; question: string };

const keyOf = (root: string, cid: string) => pendingKey("askHistory", root, cid);
const readQuestion = (root: string, cid: string) =>
  readPending<Pending>(keyOf(root, cid), (v) => typeof v.question === "string");
const writeQuestion = (root: string, cid: string, p: Pending | null) =>
  writePending(keyOf(root, cid), p);

/** Whether a question asked here is still remembered for `cid` in library
 *  `root`: its panel stays reachable to collect the answer, whatever the
 *  switch now says. */
export const hasPendingQuestion = (root: string, cid: string) => readQuestion(root, cid) !== null;

/** `canAsk` is false once Settings switched the question off: a question
 *  already running is still followed to its answer, but none is started. */
export function AskHistory({ cid, root, onClose, canAsk = true }:
  { cid: string; root: string; onClose: () => void; canAsk?: boolean }) {
  const [question, setQuestion] = useState(() => readQuestion(root, cid)?.question ?? "");
  /** Bumped when a remembered question is forgotten, so the notice re-reads. */
  const [, setForgotten] = useState(0);
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
      writeQuestion(root, cid, null);
      setAnswer(got);
    } catch (err: unknown) {
      // An unmount stops only the watching; the run, and its record here,
      // stay for the next mount to pick up.
      if (isAbortError(err)) return;
      if (settles(err)) writeQuestion(root, cid, null);
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
        writeQuestion(root, cid, null);
        setAnswer(got);
      } else {
        writeQuestion(root, cid, null);
        setError("The earlier question's answer is no longer held. Ask it again to rerun it.");
      }
    } catch (err: unknown) {
      if (isAbortError(err)) return;
      if (settles(err)) writeQuestion(root, cid, null);
      setError(errorText(err));
    }
    if (!ctl.signal.aborted) setAsking(false);
  }

  useEffect(() => {
    const p = readQuestion(root, cid);
    if (p) void resume(p);
    return () => live.current?.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once per campaign and library
  }, [cid, root]);

  // A question whose failure proved nothing is still remembered: its run may
  // be going. Another is not started over it until it is checked or forgotten,
  // or the only handle to that run (and its answer) would be lost.
  const unresolved = asking ? null : readQuestion(root, cid);
  const blocked = unresolved !== null && unresolved.question !== question.trim();

  function forget() {
    writeQuestion(root, cid, null);
    setForgotten((n) => n + 1);
  }

  function ask() {
    const q = question.trim();
    if (!q || asking || !canAsk || blocked) return;
    // The same question as one still remembered re-asks under its attempt
    // id, which the server adopts: a failure that proved nothing must not
    // let a second run start and spend again.
    const held = readQuestion(root, cid);
    const p = held?.question === q ? held : { attempt: newAttemptId(), question: q };
    writeQuestion(root, cid, p);
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
        <button type="button" disabled={asking || !question.trim() || !canAsk || blocked}
                onClick={ask}>
          {asking ? "Reading the history…" : "Ask"}
        </button>
      </div>
      {blocked && unresolved && (
        <div className="field-hint" role="status">
          An earlier question may still be being answered: “{unresolved.question}”.{" "}
          <button type="button" className="subtle" onClick={() => { void resume(unresolved); }}>
            Check on it
          </button>{" "}
          <button type="button" className="subtle" onClick={forget}>Forget it</button>
        </div>
      )}
      {canAsk ? <BudgetLevel cid={cid} />
        : <p className="field-hint">Asking is switched off in Settings.</p>}
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
