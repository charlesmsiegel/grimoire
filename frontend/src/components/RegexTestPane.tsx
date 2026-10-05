import { useRef, useState } from "react";
import { api, type RegexRule, type RegexScope, type RegexStep } from "../api/client";
import { errorText } from "../api/errors";

type Role = "model" | "user";
type Phase = "display" | "prompt" | "store";

/** `after` split around what differs from `before`: the longest shared prefix and
 *  suffix are left plain and the middle is the change. Deliberately not a real
 *  diff -- a rule's effect is usually one substitution, and a trace that has to
 *  be read twice to find it is no clearer than one that marks a little too much. */
function changed(before: string, after: string): { head: string; mid: string; tail: string } {
  const room = Math.min(before.length, after.length);
  let p = 0;
  while (p < room && before[p] === after[p]) p++;
  let s = 0;
  while (s < room - p && before[before.length - 1 - s] === after[after.length - 1 - s]) s++;
  return { head: after.slice(0, p), mid: after.slice(p, after.length - s), tail: after.slice(after.length - s) };
}

const WHOLE = /^\d+$/;

/** Runs the rules a level would apply over pasted text and shows each one's
 *  turn: what it matched, what it left, or why it stood aside. The open form's
 *  rule, when there is one, is tested in place of its saved version, so a rule
 *  can be tried before it is saved. `connection` picks which connection's rules
 *  a global, world or campaign level runs first, as the one that produced a
 *  message would; a connection level is its own rules, so it offers no choice. */
export function RegexTestPane({ scope, draft, initial, connections = [] }: {
  scope: RegexScope;
  draft?: RegexRule;
  initial?: { text: string; role: Role; depth: number; connection?: string };
  connections?: { id: string; name: string }[];
}) {
  const [text, setText] = useState(initial?.text ?? "");
  const [role, setRole] = useState<Role>(initial?.role ?? "model");
  const [phase, setPhase] = useState<Phase>("display");
  const [depth, setDepth] = useState(String(initial?.depth ?? 0));
  const [connection, setConnection] = useState(initial?.connection ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The input a trace was run on: each step's "before" is the step above it, and
  // the first one's is this, not whatever the textarea has been edited to since.
  const [trace, setTrace] = useState<{ input: string; steps: RegexStep[]; result: string } | null>(null);
  const seq = useRef(0);

  const depthOk = WHOLE.test(depth.trim());

  async function run() {
    if (!depthOk) return;
    const mine = ++seq.current;
    const input = text;
    setBusy(true); setError(null);
    try {
      const out = await api.testRegex({
        scope, text: input, role, phase, depth: Number(depth.trim()),
        ...(draft ? { draft } : {}),
        ...(scope.kind !== "connection" && connection ? { connection } : {}),
      });
      if (seq.current === mine) setTrace({ input, steps: out.steps, result: out.result });
    } catch (err) {
      if (seq.current === mine) { setError(errorText(err)); setTrace(null); }
    } finally {
      if (seq.current === mine) setBusy(false);
    }
  }

  return (
    <div className="regex-test">
      <label className="field-hint" htmlFor="regex-test-text">Text to test</label>
      <textarea id="regex-test-text" aria-label="Text to test" rows={4} className="regex-code" value={text}
                onChange={(e) => setText(e.target.value)} />
      <div className="regex-test-controls">
        <label>Role
          <select aria-label="Role" value={role} onChange={(e) => setRole(e.target.value as Role)}>
            <option value="model">Model post</option>
            <option value="user">Player post</option>
          </select>
        </label>
        <label>Phase
          <select aria-label="Phase" value={phase} onChange={(e) => setPhase(e.target.value as Phase)}>
            <option value="display">Display</option>
            <option value="prompt">Prompt</option>
            <option value="store">Store</option>
          </select>
        </label>
        <label>Depth
          <input type="number" min={0} aria-label="Depth" value={depth}
                 onChange={(e) => setDepth(e.target.value)} />
        </label>
        {scope.kind !== "connection" && (
          <label>Connection
            <select aria-label="Connection" value={connection} onChange={(e) => setConnection(e.target.value)}>
              <option value="">None</option>
              {connections.map((c) => <option key={c.id} value={c.id}>{c.name || c.id}</option>)}
            </select>
          </label>
        )}
      </div>
      {!depthOk && <div className="field-hint">Depth must be a whole number, 0 or more.</div>}
      {draft && <div className="field-hint">Testing the rule in the open form in place of its saved version.</div>}
      <div className="form-actions">
        <button className="primary" disabled={busy || !depthOk} onClick={() => void run()}>Run</button>
      </div>
      {error && <div className="banner" role="alert">{error}</div>}
      {trace && (
        trace.steps.length === 0 ? (
          <p className="field-hint">No rules to run at this level.</p>
        ) : (
          <>
            <ol className="regex-trace">
              {trace.steps.map((s, i) => {
                const parts = changed(i === 0 ? trace.input : trace.steps[i - 1].text_after, s.text_after);
                return (
                  <li key={s.rule_id} className={s.applied ? undefined : "off"}>
                    <div>
                      <strong>{s.name || s.rule_id}</strong>{" "}
                      <span className="chip">{s.level}</span>{" "}
                      {s.applied
                        ? <span className="field-hint">{s.matches} {s.matches === 1 ? "match" : "matches"}</span>
                        : <span className="field-hint">{s.reason ?? "did not apply"}</span>}
                    </div>
                    {s.applied && (
                      <pre className="regex-code">{parts.head}{parts.mid && <mark>{parts.mid}</mark>}{parts.tail}</pre>
                    )}
                  </li>
                );
              })}
            </ol>
            <div className="field-hint">Result</div>
            <pre className="regex-code">{trace.result}</pre>
          </>
        )
      )}
    </div>
  );
}
