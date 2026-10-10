import type { ModelLimit } from "../../api/client";

/** A token count as the Models summary says it: `1M`, `200k`, `32k`, or the
 *  number itself below a thousand. Rounded DOWN: overstating a window is the
 *  unsafe direction (a prompt sized to it would not fit). */
export function tokensShort(value: number): string {
  if (value >= 1_000_000) return `${Math.floor(value / 100_000) / 10}M`;
  return value >= 1000 ? `${Math.floor(value / 1000)}k` : String(value);
}

/** A window in words (spec 01i 6.3): `200k window`, or `window unknown`. */
export function windowWords(limit: ModelLimit | undefined | null): string {
  return limit?.value != null ? `${tokensShort(limit.value)} window` : "window unknown";
}
