import { useState } from "react";
import { api, type EntityScope, type ModuleDetail } from "../api/client";
import { Field } from "./Field";
import { typeKind } from "./SheetEditor";

type Step = "form" | "type" | "budget";

const capitalize = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

// Mirrors backend/src/grimoire/store/sheets.py::_pool_floor -- dots/track
// pools always floor at 0, but number fields floor at their declared `min`
// (or 0 if none is declared). Getting this wrong misrepresents the server's
// actual spend baseline for number fields with a nonzero min.
function poolFloor(fdef: { type?: string; min?: number } | undefined): number {
  if (fdef?.type === "number" && typeof fdef.min === "number" && Number.isInteger(fdef.min)) {
    return fdef.min;
  }
  return 0;
}

export default function CreationWizard({ scope, kind, module, createRecord, deleteRecord, onDone, onCancel }: {
  scope: EntityScope; kind: string; module: ModuleDetail;
  createRecord: (name: string) => Promise<string>;
  deleteRecord?: (id: string) => Promise<void>;
  onDone: (id: string) => void; onCancel: () => void;
}) {
  const [step, setStep] = useState<Step>("form");
  const [name, setName] = useState("");
  const [sheetType, setSheetType] = useState("");
  const [spends, setSpends] = useState<Record<string, Record<string, number>>>({});
  const [error, setError] = useState<string | null>(null);

  // `kind` is the file/entity kind used for every API call (create, putSheetCreation) --
  // "pcs" is a real, distinct kind there. Sheet *types* are declared with the module
  // kind ("characters"), never "pcs" (mirrors backend sheets.sheet_kind()); typeKind()
  // is the same mapping SheetEditor already uses, reused here so PCEditor's
  // kind="pcs" wizard actually finds its "characters" sheet types instead of
  // filtering to an empty list (round-1 Codex finding on the plan).
  const types = Object.entries(module.sheets.sheet_types).filter(([, st]) => st.kind === typeKind(kind));
  const typeDef = sheetType ? module.sheets.sheet_types[sheetType] : undefined;
  const pools = typeDef?.creation?.pools ?? {};
  // EntityEditor passes the plural file/API kind ("items"); the wizard header wants
  // the singular reading ("item") to match the "+ New item with sheet…" trigger copy.
  const singularKind = kind.endsWith("s") ? kind.slice(0, -1) : kind;

  // Just advances to the type-pick step -- the record itself isn't created
  // until submit() actually commits, so Cancel on a later step leaves no
  // persistent trace.
  function nextFromName() {
    if (!name.trim()) return;
    setStep("type");
  }

  function pickType(tid: string) {
    setSheetType(tid);
    setSpends({});
    const def = module.sheets.sheet_types[tid];
    if (def?.creation) {
      setStep("budget");
    } else {
      void submit(tid, {});
    }
  }

  function setSpend(poolId: string, fieldKey: string, value: number) {
    setSpends({ ...spends, [poolId]: { ...(spends[poolId] ?? {}), [fieldKey]: value } });
  }

  function poolTotal(poolId: string): number {
    const pool = pools[poolId];
    if (!pool) return 0;
    let total = 0;
    for (const [fieldKey, cost] of Object.entries(pool.costs)) {
      const fdef = module.sheets.groups[poolId]?.fields.find((f) => f.key === fieldKey);
      const floor = poolFloor(fdef);
      const value = spends[poolId]?.[fieldKey] ?? floor;
      total += (value - floor) * cost;
    }
    return total;
  }

  const anyOverBudget = Object.keys(pools).some((pid) => {
    const budget = typeof pools[pid].budget === "number" ? pools[pid].budget as number : Infinity;
    return poolTotal(pid) > budget;
  });

  async function submit(tid: string, finalSpends: Record<string, Record<string, number>>) {
    setError(null);
    let id: string;
    try {
      id = await createRecord(name);
    } catch (err: any) {
      setError(err.detail ?? String(err));
      return;
    }
    try {
      await api.putSheetCreation(scope, module.id, kind, id, { sheet_type: tid, spends: finalSpends, expected: null });
      onDone(id);
    } catch (err: any) {
      // The record was already created; the sheet write is what failed. Best-effort
      // roll it back so a rejected budget/validation/transient failure doesn't leave
      // a durable, permanently orphaned, unsheeted record with no way for the user
      // to know it was left behind. A delete failure must not mask the original error.
      if (deleteRecord) {
        try {
          await deleteRecord(id);
        } catch {
          // ignore -- surfaced error below still reflects the original failure
        }
      }
      setError(err.detail ?? String(err));
    }
  }

  return (
    <div className="form">
      <h3>New {singularKind} (with sheet)</h3>
      {error && <div className="banner">{error}</div>}
      {step === "form" && (
        <>
          <Field label="Name">
            <input aria-label="Name" type="text" value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <div className="form-actions">
            <button className="subtle" onClick={onCancel}>Cancel</button>
            <button className="primary" onClick={nextFromName} disabled={!name.trim()}>Next</button>
          </div>
        </>
      )}
      {step === "type" && (
        <>
          <Field label="Sheet type">
            <select aria-label="Sheet type" value={sheetType} onChange={(e) => setSheetType(e.target.value)}>
              <option value="" disabled>Select type…</option>
              {types.map(([tid, st]) => <option key={tid} value={tid}>{st.label}</option>)}
            </select>
          </Field>
          <div className="form-actions">
            <button className="subtle" onClick={onCancel}>Cancel</button>
            <button className="primary" onClick={() => pickType(sheetType)} disabled={!sheetType}>
              {typeDef?.creation ? "Next" : "Create"}
            </button>
          </div>
        </>
      )}
      {step === "budget" && typeDef && (
        <>
          {Object.entries(pools).map(([poolId, pool]) => (
            <div className="side-section" key={poolId}>
              <h4>{module.sheets.groups[poolId]?.label ?? poolId} — {poolTotal(poolId)} / {String(pool.budget)}</h4>
              {Object.keys(pool.costs).map((fieldKey) => {
                const fdef = module.sheets.groups[poolId]?.fields.find((f) => f.key === fieldKey);
                const fieldLabel = fdef?.label ?? capitalize(fieldKey);
                const floor = poolFloor(fdef);
                return (
                  <Field key={fieldKey} label={fieldLabel}>
                    <input aria-label={fieldLabel} type="number" min={floor} max={fdef?.max}
                           value={spends[poolId]?.[fieldKey] ?? floor}
                           onChange={(e) => setSpend(poolId, fieldKey, Number(e.target.value))} />
                  </Field>
                );
              })}
            </div>
          ))}
          <div className="form-actions">
            <button className="subtle" onClick={onCancel}>Cancel</button>
            <button className="primary" onClick={() => submit(sheetType, spends)} disabled={anyOverBudget}>Create</button>
          </div>
        </>
      )}
    </div>
  );
}
