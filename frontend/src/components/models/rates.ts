import type { RateInfo } from "../../api/client";
import { perMillionRate } from "../cost";

/** What would price a role's or route's calls, in words (spec 3.5). Quoted
 *  per million through `perMillionRate`, the one formatter every cost surface
 *  uses. "Would be priced at", because it is the rate for a call answered
 *  under the configured name; a dated snapshot can match another entry.
 *
 *  A price nobody reported is never rendered as zero: `none`, and an entry
 *  missing a side (which pricing never uses), are words. A real 0 is `$0/M`. */
export function rateWords(rate: RateInfo | null, { promptOnly = false } = {}): string | null {
  if (!rate) return null;
  if (rate.source === "native") return "native decisions: priced only if the provider reports a cost";
  const input = rate.entry?.prompt_usd_per_1k;
  const output = rate.entry?.completion_usd_per_1k;
  if (rate.source === "none" || input === undefined || output === undefined) {
    return "no rate: calls unpriced";
  }
  const where = rate.source === "provider" ? "provider" : "your rates";
  const figure = promptOnly ? perMillionRate(input)
    : `${perMillionRate(input)} in · ${perMillionRate(output)} out`;
  return `would be priced at ${figure} (${where})`;
}

/** Where Set rate goes: the model rides the query, encoded, and the fragment
 *  only scrolls -- so an id holding `/`, `?` or `#` arrives whole. */
export function setRateHref(model: string): string {
  return `/models?add=${encodeURIComponent(model)}#rates`;
}

/** The model a Set rate link asked for, from a location's `search`. */
export function addedModel(search: string): string {
  return new URLSearchParams(search).get("add") ?? "";
}
