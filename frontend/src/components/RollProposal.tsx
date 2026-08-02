import { useState } from "react";
import type { ProposalRecord } from "../api/client";

export type ResolveBody = {
  proposal: string;
  action: "accept" | "decline";
  check?: string;
  actor?: string;
  difficulty?: number;
  modifier?: number;
};

// dedupe checks across every actor's option list, keeping first-seen order —
// used when the proposal's actor couldn't be resolved and the picker has to
// offer the union of everyone's checks.
function allChecks(available: Record<string, [string, string][]>): [string, string][] {
  const seen = new Set<string>();
  const out: [string, string][] = [];
  for (const list of Object.values(available)) {
    for (const [key, label] of list) {
      if (!seen.has(key)) {
        seen.add(key);
        out.push([key, label]);
      }
    }
  }
  return out;
}

export function RollProposal({ record, busy, onResolve }:
  { record: ProposalRecord; busy: boolean; onResolve: (body: ResolveBody) => void }) {
  const { payload } = record;
  const hasProblems = payload.problems.length > 0;
  const [modifying, setModifying] = useState(hasProblems);

  const available = payload.available ?? {};
  const actorOptions = Object.keys(available);
  const actorResolved = Boolean(payload.actor);

  const [actor, setActor] = useState(payload.actor ?? actorOptions[0] ?? "");
  const [check, setCheck] = useState(payload.check ?? "");
  const [difficulty, setDifficulty] = useState<number | "">(payload.difficulty ?? "");
  const [modifier, setModifier] = useState(payload.modifier ?? 0);

  // Both states mean the same thing: the decision is made and only its
  // narration is outstanding, because the continuation stream failed or was
  // stopped. The backend re-streams either one on request, keyed to the state
  // the record is already in — so the action here restates that state rather
  // than making a new decision, and re-declining a declined record is not a
  // second decline.
  if (record.status === "resolved" || record.status === "declined") {
    const declined = record.status === "declined";
    return (
      <div className="roll-proposal">
        <p className="field-hint">
          {declined ? "Roll declined, narration pending." : "Roll made, narration pending."}
        </p>
        <div className="form-actions">
          <button className="primary" type="button" disabled={busy}
                  onClick={() => onResolve({ proposal: record.id,
                                             action: declined ? "decline" : "accept" })}>
            Continue narration
          </button>
        </div>
      </div>
    );
  }

  const checkOptions = actorResolved ? (available[payload.actor as string] ?? []) : allChecks(available);
  const checkLabel = payload.check_label ?? payload.check ?? "check";
  const actorLabel = payload.actor_label ?? payload.actor ?? "unresolved actor";
  const open = modifying || hasProblems;

  function rollIt() {
    const body: ResolveBody = { proposal: record.id, action: "accept", check, actor, modifier };
    if (difficulty !== "") body.difficulty = difficulty;
    onResolve(body);
  }

  function decline() {
    onResolve({ proposal: record.id, action: "decline" });
  }

  return (
    <div className="roll-proposal">
      <div className="roll-proposal-chip">
        🎲 {checkLabel} — {actorLabel}
        {payload.difficulty != null && <span> · diff {payload.difficulty}</span>}
      </div>
      {payload.reason && <div className="field-hint">{payload.reason}</div>}
      {open && (
        <div className="roll-proposal-form">
          {!actorResolved && (
            <label>
              Actor
              <select aria-label="Actor" value={actor} disabled={busy}
                      onChange={(e) => setActor(e.target.value)}>
                {actorOptions.map((a) => <option key={a} value={a}>{a}</option>)}
              </select>
            </label>
          )}
          <label>
            Check
            <select aria-label="Check" value={check} disabled={busy}
                    onChange={(e) => setCheck(e.target.value)}>
              {checkOptions.map(([key, label]) => <option key={key} value={key}>{label}</option>)}
            </select>
          </label>
          <label>
            Difficulty
            <input type="number" aria-label="Difficulty" value={difficulty} disabled={busy}
                   placeholder="default"
                   onChange={(e) => setDifficulty(e.target.value === "" ? "" : Number(e.target.value))} />
          </label>
          <label>
            Modifier
            <input type="number" aria-label="Modifier" value={modifier} disabled={busy}
                   onChange={(e) => setModifier(Number(e.target.value))} />
          </label>
          {payload.problems.map((p, i) => (
            <div key={i} className="field-hint">{p}</div>
          ))}
        </div>
      )}
      <div className="form-actions">
        <button className="primary" type="button" disabled={busy} onClick={rollIt}>Roll it</button>
        <button className="subtle" type="button" disabled={busy}
                onClick={() => setModifying((m) => !m)}>Modify</button>
        <button className="subtle" type="button" disabled={busy} onClick={decline}>Decline</button>
      </div>
    </div>
  );
}
