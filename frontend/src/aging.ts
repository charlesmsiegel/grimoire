import type { Aging } from "./api/client";

/** How a record's age reads on screen (#103) — one sentence fragment, or "".
 *
 *  Shared by the ledger's tables and the advance digest rather than written
 *  twice, because the two are the same claim about the same records and a
 *  reader moving between them should not have to reconcile "12 days overdue"
 *  with "overdue by 12". The wording is deliberately the badge only: where it
 *  goes (a note line, a chip, a column) is each view's decision.
 *
 *  Returns "" for `ok` and for a record with nothing to measure — no clock, no
 *  dated scene, a calendar that will not load. An unaged row shows no badge,
 *  which is the honest rendering of "cannot tell" and the same thing the ledger
 *  showed before aging existed.
 */
export function agingLabel(aging: Aging | undefined): string {
  if (!aging || aging.state === "ok") return "";
  if (aging.state === "overdue" && aging.days_over !== null)
    return `OVERDUE BY ${days(aging.days_over)}`;
  if (aging.state === "overdue") return "OVERDUE";
  if (aging.days_since !== null) return `STALE · ${days(aging.days_since)} UNTOUCHED`;
  return "STALE";
}

/** Singular days read as a mistake otherwise ("1 days overdue"). */
function days(n: number): string {
  return `${n} DAY${n === 1 ? "" : "S"}`;
}
