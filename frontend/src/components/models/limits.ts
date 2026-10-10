import type { ModelLimit } from "../../api/client";

/** A token count as the summary says it: `128k` for 131,072 and `200k` for
 *  200,000 -- the two ways providers round a window -- `1M` for a million,
 *  and the plain figure below a thousand. The exact count goes in a `title`. */
export function compactTokens(n: number): string {
  if (n >= 1_000_000 && n % 1_000_000 === 0) return `${n / 1_000_000}M`;
  if (n >= 1_048_576 && n % 1_048_576 === 0) return `${n / 1_048_576}M`;
  if (n >= 1024 && n % 1024 === 0) return `${n / 1024}k`;
  if (n >= 1000) return `${Math.round(n / 1000)}k`;
  return n.toLocaleString("en-US");
}

/** `200k window`, or `window unknown` (01i): what a role's model holds, prompt
 *  and reply together, read off the server's resolved limit. */
export function windowWords(limit: ModelLimit | null | undefined): string {
  return limit?.value != null ? `${compactTokens(limit.value)} window` : "window unknown";
}
