import { useEffect, useState } from "react";
import { ApiError, api, type PricingEntry } from "../api/client";

/** Per-model token rates, for the providers that report no price (#158).
 *
 *  Grimoire records what a provider says a call cost. OpenRouter says; every
 *  OpenAI-compatible endpoint says nothing at all, and those calls sit in every
 *  rollup as "unpriced" — honest, and useless for answering what a campaign has
 *  cost. This table is how a reader closes that gap themselves.
 *
 *  **What comes out of it is an estimate and is labelled as one everywhere.**
 *  A modelled figure lands in its own column, is never added to what was
 *  billed, and is never charged against a campaign's budget. The whole reason
 *  the ledger stores an absent price rather than a zero one is so this can be
 *  layered on top without destroying the distinction.
 *
 *  Its own save button rather than the page's, like `StorageLocation` above it:
 *  this writes a different file through a different route, and folding it into
 *  the config draft would make one Save mean two writes either of which could
 *  fail on its own.
 */

/** The four rates an entry can carry, in the order they are asked for. The
 *  cache pair is optional, and its absence is NOT zero: cache counts are slices
 *  of the prompt, so a row naming no cache rate has already priced them at the
 *  prompt rate — which is right for a provider that does not discount them. */
const FIELDS: { key: keyof PricingEntry; label: string; required?: boolean }[] = [
  { key: "prompt_usd_per_1k", label: "Input", required: true },
  { key: "completion_usd_per_1k", label: "Output", required: true },
  { key: "cache_read_usd_per_1k", label: "Cache read" },
  { key: "cache_write_usd_per_1k", label: "Cache write" },
];

/** A row as the form holds it: rates as the strings that were typed, so a
 *  half-entered "0." survives a re-render and an empty box stays empty rather
 *  than becoming a 0 nobody meant.
 *
 *  `isDefault` is a flag rather than "the id is empty", and that is not a
 *  stylistic choice: a freshly added row also has an empty id — nobody has
 *  typed one yet — and inferring the catch-all from emptiness would turn every
 *  new row into a second rate claiming to price everything. */
type Row = { key: number; id: string; isDefault: boolean;
             rates: Record<string, string> };

/** Row keys, so React reconciles by identity rather than by position. An index
 *  key would make removing the first of three rows reuse its inputs for the
 *  second — carrying a half-typed rate onto a different model. Module-scoped
 *  and monotonic: the value only has to be unique within one list. */
let nextKey = 0;

/** The key that prices everything with no entry of its own. Empty on the wire;
 *  the form gives it a name and pins it to the bottom of the list. */
const DEFAULT_KEY = "";

function toRows(table: Record<string, PricingEntry>): Row[] {
  const rows = Object.entries(table).map(([id, entry]) => ({
    key: nextKey++, id, isDefault: id === DEFAULT_KEY,
    rates: Object.fromEntries(FIELDS.map((f) => [f.key,
      entry[f.key] === undefined ? "" : String(entry[f.key])])),
  }));
  // Named entries first, the catch-all last: it is what applies when nothing
  // above it matched, and reading it in that position says so.
  rows.sort((a, b) => (a.isDefault ? 1 : b.isDefault ? -1 : 0)
                      || a.id.localeCompare(b.id));
  return rows;
}

function toTable(rows: Row[]): Record<string, PricingEntry> {
  const table: Record<string, PricingEntry> = {};
  for (const row of rows) {
    const key = row.isDefault ? DEFAULT_KEY : row.id.trim();
    // A named row nobody has named yet is not sent: it would land on the
    // catch-all key and quietly become the rate for every model in the library.
    if (!row.isDefault && !key) continue;
    const entry: Record<string, number> = {};
    for (const field of FIELDS) {
      const typed = (row.rates[field.key] ?? "").trim();
      if (typed === "") continue;
      const value = Number(typed);
      // Left out rather than sent as NaN: the server drops an unusable rate
      // anyway, and sending one would make the answer disagree with the form
      // for reasons the form never explained.
      if (Number.isFinite(value) && value >= 0) entry[field.key] = value;
    }
    // BOTH base rates, mirroring the store: an entry with one prices half a
    // call and values the other half at nothing, which on a reply that
    // generated nothing renders `$0.00` for a call nobody priced at all. The
    // server drops such a row, and mirroring it here is what keeps the form
    // from appearing to have saved something it did not.
    if (entry.prompt_usd_per_1k !== undefined && entry.completion_usd_per_1k !== undefined) {
      table[key] = entry;
    }
  }
  return table;
}

/** The normalized key a row will be saved under — what `toTable` uses, so the
 *  duplicate check below asks the same question the save does. */
function keyOf(row: Row): string {
  return row.isDefault ? DEFAULT_KEY : row.id.trim();
}

/** The keys claimed by more than one row.
 *
 *  Two rows with the same id — or ids that become equal once trimmed — collapse
 *  in `toTable`, and the whole-table PUT then removes one of them while
 *  reporting a successful save. Detected rather than resolved: which of the two
 *  the user meant is not something this component can know. */
function duplicates(rows: Row[]): Set<string> {
  const seen = new Set<string>();
  const twice = new Set<string>();
  for (const row of rows) {
    const key = keyOf(row);
    // A row nobody has named yet is not a duplicate of the next unnamed one —
    // neither is saved at all (`toTable`), so neither can displace anything.
    if (!row.isDefault && !key) continue;
    if (seen.has(key)) twice.add(key);
    seen.add(key);
  }
  return twice;
}

function typed(row: Row, key: string): boolean {
  const raw = (row.rates[key] ?? "").trim();
  return raw !== "" && Number.isFinite(Number(raw)) && Number(raw) >= 0;
}

/** Whether this row will survive a save. Both base rates, per `toTable`. */
function complete(row: Row): boolean {
  return ["prompt_usd_per_1k", "completion_usd_per_1k"].every((k) => typed(row, k));
}

/** A row somebody has started filling in. Anything typed counts — the point is
 *  to tell "I have not touched this yet" from "I filled this in and it is
 *  about to be thrown away". */
function started(row: Row): boolean {
  return row.isDefault || row.id.trim() !== ""
    || Object.keys(row.rates).some((k) => typed(row, k));
}

/** A row that has been filled in but never named. `toTable` drops it — an
 *  unnamed row must not become the catch-all — and without this it did so
 *  silently, under a "Rates saved" that discarded everything typed into it. */
function unnamed(row: Row): boolean {
  return !row.isDefault && row.id.trim() === "" && started(row);
}

/** The same rate as providers publish it. Every price sheet quotes dollars per
 *  million tokens; the file's unit is per 1,000 (#158's shape), and showing
 *  both is what stops a rate being typed a thousandfold off. */
function perMillion(typed: string): string {
  const value = Number((typed ?? "").trim());
  if (!(typed ?? "").trim() || !Number.isFinite(value) || value < 0) return "";
  return `$${(value * 1000).toLocaleString(undefined, { maximumFractionDigits: 2 })}/M`;
}

export function PricingEditor() {
  const [rows, setRows] = useState<Row[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  /** The read failed, so what is on screen is not the stored table.
   *
   *  Held as its own state rather than degraded to an empty form, which is the
   *  policy everywhere else here and is wrong for exactly this panel: a
   *  transient GET failure would leave an editable blank table, and one click
   *  of Save would then send `{}` and delete every rate the user has — rates
   *  they never loaded, never saw and never removed. A read that failed has to
   *  say so and refuse to write over what it could not read. */
  const [unread, setUnread] = useState(false);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let live = true;
    api.getPricing()
      .then((t) => {
        if (!live) return;
        // `unreadable` is a 200 carrying no rates: the file is there and could
        // not be parsed. Same refusal as a rejected request, because saving an
        // empty form would replace what is in that file with nothing.
        setRows(t.unreadable ? [] : toRows(t.rates));
        setUnread(Boolean(t.unreadable));
      })
      .catch(() => { if (!live) return; setRows([]); setUnread(true); });
    return () => { live = false; };
  }, [reload]);

  function edit(index: number, key: string, value: string) {
    setSaved(false);
    setRows((old) => (old ?? []).map((row, i) =>
      i === index ? { ...row, rates: { ...row.rates, [key]: value } } : row));
  }

  async function save() {
    setError(null);
    setBusy(true);
    try {
      const answer = await api.setPricing(toTable(rows ?? []));
      // Seeded from the SERVER's answer, not from what was typed: an entry it
      // dropped has to disappear from the form too, or the next save would send
      // it back and the two would disagree forever.
      setRows(toRows(answer.rates));
      setSaved(true);
    } catch (err) {
      // `ApiError` carries the server's own `detail`; anything else is a
      // transport failure with nothing better to show than its message.
      setError(err instanceof ApiError ? err.detail : String(err));
    } finally {
      setBusy(false);
    }
  }

  const clashes = rows === null ? new Set<string>() : duplicates(rows);
  const nameless = (rows ?? []).some(unnamed);

  if (rows === null) return <p className="field-hint">Reading rates…</p>;

  if (unread) {
    return (
      <div className="pricing-editor">
        <div className="field-hint error">
          Could not read the rate table. Nothing is shown and nothing can be
          saved from here — an empty form saved over rates that failed to load
          would delete them.
        </div>
        <div className="picker">
          <button onClick={() => { setRows(null); setReload((n) => n + 1); }}>
            Try again
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="pricing-editor">
      {rows.length === 0 && (
        <p className="field-hint">
          No rates set. Calls whose provider reports no price stay "unpriced" in
          every cost view — which is the honest answer, and not a useful one.
        </p>
      )}

      {rows.map((row, i) => (
        <div className="pricing-row" key={row.key}>
          <div className="pricing-model">
            {row.isDefault ? (
              <span className="chip on">Every other model</span>
            ) : (
              <input aria-label={`Model id for row ${i + 1}`} value={row.id}
                     disabled={busy}
                     placeholder="provider/model, or provider/*"
                     onChange={(e) => {
                       setSaved(false);
                       const id = e.target.value;
                       setRows((old) => (old ?? []).map((r, n) => n === i ? { ...r, id } : r));
                     }} />
            )}
            <button className="subtle" aria-label={`Remove ${row.isDefault ? "the catch-all rate" : row.id || "this rate"}`}
                    disabled={busy}
                    onClick={() => { setSaved(false);
                                     setRows((old) => (old ?? []).filter((_, n) => n !== i)); }}>
              ✕
            </button>
          </div>
          {unnamed(row) && (
            <div className="field-hint error">
              This row needs a model id — give it one, or remove it. Saving as
              it stands would throw away what you have typed here.
            </div>
          )}
          {clashes.has(keyOf(row)) && (
            <div className="field-hint error">
              Two rows claim this model. Saving would keep only one of them —
              rename or remove one first.
            </div>
          )}
          {!complete(row) && (
            <div className="field-hint">
              Input and output are both needed — a rate for one prices the other
              half of every call at nothing. This row will not be saved.
            </div>
          )}
          <div className="pricing-rates">
            {FIELDS.map((field) => (
              <label className="pricing-rate" key={field.key}>
                <span className="pricing-rate-label">
                  {field.label}{field.required ? " *" : ""}
                </span>
                {/* Frozen while the PUT is in flight, like the buttons beside
                    it: the answer re-seeds every row from what the server kept,
                    so anything typed after Save was clicked would be discarded
                    by a response that never saw it. */}
                <input type="number" step="0.0001" min="0" inputMode="decimal"
                       disabled={busy}
                       aria-label={`${field.label} rate for ${row.isDefault ? "every other model" : row.id || "a new model"}`}
                       value={row.rates[field.key] ?? ""}
                       onChange={(e) => edit(i, field.key, e.target.value)} />
                <span className="pricing-rate-hint">
                  {perMillion(row.rates[field.key] ?? "") || "$ per 1K"}
                </span>
              </label>
            ))}
          </div>
        </div>
      ))}

      <div className="picker">
        <button disabled={busy}
                onClick={() => { setSaved(false);
                                 setRows([...rows, { key: nextKey++, id: "",
                                                     isDefault: false, rates: {} }]); }}>
          + Add a model
        </button>
        {/* Offered only while nothing holds the catch-all key, so the list
            cannot end up with two rows both claiming to price everything. */}
        {!rows.some((r) => r.isDefault) && (
          <button disabled={busy}
                  onClick={() => { setSaved(false);
                                   setRows([...rows, { key: nextKey++, id: DEFAULT_KEY,
                                                       isDefault: true, rates: {} }]); }}>
            + Add a catch-all rate
          </button>
        )}
        {/* `void`: an async handler returns a promise, and a click handler
            that returns one is a floating promise nothing awaits. */}
        <button className="primary" onClick={() => void save()}
                disabled={busy || clashes.size > 0 || nameless}
                title={clashes.size > 0
                  ? "Two rows claim the same model; saving would drop one"
                  : nameless
                    ? "A row has rates but no model id; saving would drop it"
                    : undefined}>
          Save rates
        </button>
      </div>

      {saved && <div className="field-hint">Rates saved.</div>}
      {error && <div className="field-hint error">{error}</div>}
    </div>
  );
}

export default PricingEditor;
