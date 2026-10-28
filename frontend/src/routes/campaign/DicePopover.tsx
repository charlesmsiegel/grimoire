import type { Dispatch, SetStateAction } from "react";
import { useEffect, useRef } from "react";
import {
  type SceneCheckActor
} from "../../api/client";

export function DicePopover({
  rollForm, rolling, setRollForm, enterCheckMode, checkActors, doCheck, fail, rollFromForm, showRollSyntax, setShowRollSyntax
}: {
  rollForm: { mode: "dice" | "check"; notation: string; label: string; error: string | null; checkActor: string; checkId: string; difficulty: number | ""; modifier: number; };
  rolling: boolean;
  setRollForm: Dispatch<SetStateAction<{ mode: "dice" | "check"; notation: string; label: string; error: string | null; checkActor: string; checkId: string; difficulty: number | ""; modifier: number; } | null>>;
  enterCheckMode: () => void;
  checkActors: SceneCheckActor[];
  doCheck: () => Promise<void>;
  fail: (e: unknown, retryable?: boolean, from?: string) => void;
  rollFromForm: () => Promise<void>;
  showRollSyntax: boolean;
  setShowRollSyntax: Dispatch<SetStateAction<boolean>>;
}) {
  const notationInput = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (rollForm.mode === "dice") notationInput.current?.focus();
  }, [rollForm.mode]);

  return (
    <div className="roll-pop">
      <div className="roll-mode-toggle">
        <button type="button" className={rollForm.mode === "dice" ? "active" : ""}
          disabled={rolling}
          onClick={() => setRollForm({ ...rollForm, mode: "dice" })}>
          Dice
        </button>
        <button type="button" className={rollForm.mode === "check" ? "active" : ""}
          disabled={rolling}
          onClick={enterCheckMode}>
          Check
        </button>
      </div>
      {rollForm.mode === "check" ? (
        <div className="check-pop">
          <select aria-label="Check actor" value={rollForm.checkActor} disabled={rolling}
            onChange={(e) => setRollForm({ ...rollForm, checkActor: e.target.value, checkId: "" })}>
            <option value="">Choose an actor…</option>
            {checkActors.map((a) => <option key={a.ref} value={a.ref}>{a.label}</option>)}
          </select>
          <select aria-label="Check" value={rollForm.checkId} disabled={rolling}
            onChange={(e) => setRollForm({ ...rollForm, checkId: e.target.value })}>
            <option value="">Choose a check…</option>
            {(checkActors.find((a) => a.ref === rollForm.checkActor)?.checks ?? [])
              .map(([key, label]) => <option key={key} value={key}>{label}</option>)}
          </select>
          <input type="number" aria-label="Difficulty" value={rollForm.difficulty} disabled={rolling}
            placeholder="default"
            onChange={(e) => setRollForm({
              ...rollForm,
              difficulty: e.target.value === "" ? "" : Number(e.target.value)
            })} />
          <input type="number" aria-label="Modifier" value={rollForm.modifier} disabled={rolling}
            onChange={(e) => setRollForm({ ...rollForm, modifier: Number(e.target.value) })} />
          <button className="btn-chrome" onClick={() => { doCheck().catch((err: unknown) => fail(err, false)); }}
            disabled={rolling || !rollForm.checkActor || !rollForm.checkId}>
            Roll ▸
          </button>
          {rollForm.error && <span className="roll-error">{rollForm.error}</span>}
        </div>
      ) : (
        <>
          <input
            ref={notationInput}
            placeholder="2d6+3, 4d6kh3, 7d10t6…"
            aria-label="Dice notation"
            value={rollForm.notation}
            disabled={rolling}
            onChange={(e) => setRollForm({ ...rollForm, notation: e.target.value })}
            onKeyDown={(e) => {
              // See the reroll box: an inline Enter is claimed, not shared.
              if (e.key === "Enter") { e.preventDefault(); rollFromForm().catch((err: unknown) => fail(err, false)); }
              if (e.key === "Escape") setRollForm(null);
            }}
          />
          <input
            placeholder="Label (optional)"
            aria-label="Roll label"
            value={rollForm.label}
            disabled={rolling}
            onChange={(e) => setRollForm({ ...rollForm, label: e.target.value })}
            onKeyDown={(e) => {
              // See the reroll box: an inline Enter is claimed, not shared.
              if (e.key === "Enter") { e.preventDefault(); rollFromForm().catch((err: unknown) => fail(err, false)); }
              if (e.key === "Escape") setRollForm(null);
            }}
          />
          <button className="btn-chrome" onClick={() => { rollFromForm().catch((err: unknown) => fail(err, false)); }} disabled={rolling}>Roll ▸</button>
          <button type="button" className="roll-syntax-help" aria-label="Dice notation syntax"
            aria-expanded={showRollSyntax}
            onClick={() => setShowRollSyntax((v) => !v)}>syntax {showRollSyntax ? "▾" : "▸"}</button>
          {rollForm.error && <span className="roll-error">{rollForm.error}</span>}
          {showRollSyntax && (
            <div className="roll-syntax">
              <div><code>NdM</code> — roll N dice with M sides (default N = 1), e.g. <code>2d6</code></div>
              <div><code>khN</code> / <code>klN</code> — keep highest/lowest N, e.g. <code>4d6kh3</code></div>
              <div><code>dhN</code> / <code>dlN</code> — drop highest/lowest N instead of keeping</div>
              <div><code>!</code> — exploding dice: max face rolls again, e.g. <code>5d6!</code></div>
              <div><code>+K</code> / <code>-K</code> — flat modifier on the total, e.g. <code>2d6+3</code></div>
              <div><code>tN</code> — pool mode: count dice ≥ N as successes, e.g. <code>7d10t6</code></div>
              <div><code>vs N</code> — grade the total success/failure vs a target, e.g. <code>1d20+5 vs 15</code></div>
              <div>Clauses combine freely (e.g. <code>4d6kh3!+2</code>); <code>tN</code> and <code>vs N</code> are mutually exclusive.</div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
