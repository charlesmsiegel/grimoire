/** "Ask the history" (roadmap 12, E3): a reader's question, answered by a
 *  model that reads the campaign's history with tools.
 *
 *  The answer is a draft -- held on its run, never stored -- and it cites
 *  only scenes the run actually read; an answer citing nothing it read is
 *  "not enough to answer" rather than a claim. What the run looked at is shown
 *  under the answer, so a reader can check it. Nothing here writes. */
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api/client";
import { errorText } from "../../api/errors";
import type { HistoryAnswer } from "../../api/types";
import { encodeSegment } from "../../urlSegment";
import { InvestigationTrace, outcomeSentence } from "./InvestigationTrace";

/** The longest question the server takes. */
export const QUESTION_CHARS = 500;

export function AskHistory({ cid, onClose }: { cid: string; onClose: () => void }) {
  const [question, setQuestion] = useState("");
  const [asking, setAsking] = useState(false);
  const [answer, setAnswer] = useState<HistoryAnswer | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function ask() {
    const q = question.trim();
    if (!q || asking) return;
    setAsking(true);
    setError(null);
    setAnswer(null);
    try {
      setAnswer(await api.askHistory(cid, q));
    } catch (err: unknown) {
      setError(errorText(err));
    } finally {
      setAsking(false);
    }
  }

  const sceneLink = (sid: string) => (
    <Link to={`/campaigns/${encodeSegment(cid)}/scenes/${encodeSegment(sid)}`}>{sid}</Link>
  );
  const outcome = answer?.outcome;
  const result = outcome?.state === "completed" ? outcome.result : null;

  return (
    <section className="ask-history" aria-label="Ask the history">
      <div className="ask-history-head">
        <h3>Ask the history</h3>
        <button type="button" className="subtle" onClick={onClose}>Close</button>
      </div>
      <p className="field-hint">
        A model reads this campaign's scenes and ledger to answer. Experimental: it may
        miss things, and the answer is not saved.
      </p>
      <textarea aria-label="Your question" rows={3} value={question} disabled={asking}
                maxLength={QUESTION_CHARS} onChange={(e) => setQuestion(e.target.value)} />
      <div className="form-actions">
        <button type="button" disabled={asking || !question.trim()} onClick={() => { void ask(); }}>
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
