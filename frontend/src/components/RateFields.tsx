import type { Ref } from "react";
import type { PricingEntry } from "../api/client";
import { perMillionRate } from "./cost";

/** The four rate boxes one pricing entry is typed into, shared by the pricing
 *  table (`PricingEditor`, one row per model) and a model's own facts on its
 *  provider's page (`ProvidersView`'s `ModelFactsPanel`). Both write the same
 *  entry shape through the same strict check (`pricing.check_entry`), so they
 *  ask for it the same way. */

/** The four rates an entry can carry, in the order they are asked for. The
 *  cache pair is optional, and its absence is NOT zero: cache counts are slices
 *  of the prompt, so a row naming no cache rate has already priced them at the
 *  prompt rate — which is right for a provider that does not discount them. */
export const RATE_FIELDS: { key: keyof PricingEntry; label: string; required?: boolean }[] = [
  { key: "prompt_usd_per_1k", label: "Input", required: true },
  { key: "completion_usd_per_1k", label: "Output", required: true },
  { key: "cache_read_usd_per_1k", label: "Cache read" },
  { key: "cache_write_usd_per_1k", label: "Cache write" },
];

/** An entry as a form holds it: rates as the strings that were typed, so a
 *  half-entered "0." survives a re-render and an empty box stays empty rather
 *  than becoming a 0 nobody meant. */
export type RateForm = Record<keyof PricingEntry, string>;

export function emptyRates(): RateForm {
  return { prompt_usd_per_1k: "", completion_usd_per_1k: "",
           cache_read_usd_per_1k: "", cache_write_usd_per_1k: "" };
}

/** A stored entry as boxes. A stated `0` is the string "0" — a price — and a
 *  rate the entry does not carry is an empty box. */
export function formOf(entry: PricingEntry | null | undefined): RateForm {
  const form = emptyRates();
  for (const { key } of RATE_FIELDS) {
    const value = entry?.[key];
    if (typeof value === "number") form[key] = String(value);
  }
  return form;
}

/** Whether a box holds a usable rate: a finite, non-negative number. */
export function typed(value: string | undefined): boolean {
  const raw = (value ?? "").trim();
  return raw !== "" && Number.isFinite(Number(raw)) && Number(raw) >= 0;
}

/** Whether anything at all has been typed into any box. */
export function filled(form: RateForm): boolean {
  return RATE_FIELDS.some(({ key }) => (form[key] ?? "").trim() !== "");
}

/** Whether the entry would be kept: BOTH base rates, mirroring the store. An
 *  entry with one prices half a call and values the other half at nothing,
 *  which on a reply that generated nothing renders `$0.00` for a call nobody
 *  priced at all. Cache rates alone are the same case. */
export function complete(form: Partial<RateForm>): boolean {
  return typed(form.prompt_usd_per_1k) && typed(form.completion_usd_per_1k);
}

/** Whether both base boxes have anything in them at all. The strict writer's
 *  gate: a box left empty is a missing rate, which nothing but the form can
 *  say; a box holding a negative is a rate the store refuses and names, so it
 *  is sent (`entryOf`) rather than reported here as missing. */
export function hasBase(form: Partial<RateForm>): boolean {
  return (form.prompt_usd_per_1k ?? "").trim() !== ""
    && (form.completion_usd_per_1k ?? "").trim() !== "";
}

/** The boxes as an entry, for a strict writer (`PUT …/facts`): every filled
 *  box as the number it says, and the empty ones LEFT OUT. Never `null` for an
 *  empty box — the store refuses a present null ("must be a non-negative
 *  number") — and never dropped for a zero, which is a price. A filled box that
 *  is not a usable rate is sent as typed rather than silently dropped, so the
 *  store's refusal names it instead of the rate vanishing on the way in. */
export function entryOf(form: Partial<RateForm>): PricingEntry {
  const entry: PricingEntry = {};
  for (const { key } of RATE_FIELDS) {
    const raw = (form[key] ?? "").trim();
    if (raw !== "") entry[key] = Number(raw);
  }
  return entry;
}

/** The same rate as providers publish it. Every price sheet quotes dollars per
 *  million tokens; the file's unit is per 1,000 (#158's shape), and showing
 *  both is what stops a rate being typed a thousandfold off. "" for a box with
 *  no usable rate in it. */
export function perMillion(value: string): string {
  return typed(value) ? perMillionRate(Number(value.trim())) : "";
}

export function RateFields({ value, onChange, idPrefix, subject, disabled, inputRef }: {
  value: RateForm; onChange: (next: RateForm) => void; idPrefix: string;
  /** What the rates are for, in each box's accessible name ("Input rate for
   *  vendor/m"): a page can hold several sets of these boxes at once. */
  subject?: string;
  disabled?: boolean;
  /** The Input box, for a page that puts the caret there. */
  inputRef?: Ref<HTMLInputElement>;
}) {
  return (
    <div className="pricing-rates">
      {RATE_FIELDS.map((field) => (
        <label className="pricing-rate" key={field.key}>
          <span className="pricing-rate-label">
            {field.label}{field.required ? " *" : ""}
          </span>
          <input type="number" step="0.0001" min="0" inputMode="decimal"
                 id={`${idPrefix}-${field.key}`} disabled={disabled}
                 ref={field.key === "prompt_usd_per_1k" ? inputRef : undefined}
                 aria-label={`${field.label} rate${subject ? ` for ${subject}` : ""}`}
                 value={value[field.key] ?? ""}
                 onChange={(e) => onChange({ ...value, [field.key]: e.target.value })} />
          <span className="pricing-rate-hint">
            {perMillion(value[field.key] ?? "") || "$ per 1K"}
          </span>
        </label>
      ))}
    </div>
  );
}
