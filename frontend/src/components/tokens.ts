/**
 * Token-count estimates for library entities. Uses a lazily-loaded
 * `js-tiktoken` cl100k encoder; before it resolves (or if loading fails) we
 * fall back to the backend's offline heuristic of len/4. The result is an
 * ESTIMATE — production models are Claude, whose tokenizer differs slightly.
 */

interface Encoder {
  encode(text: string): number[];
}

let encoder: Encoder | null = null;
let loading: Promise<void> | null = null;

/** Kick off (or await) lazy load of the cl100k encoder. */
export function ensureTokenizer(): Promise<void> {
  if (encoder) return Promise.resolve();
  if (!loading) {
    loading = import("js-tiktoken")
      .then(({ getEncoding }) => {
        encoder = getEncoding("cl100k_base") as Encoder;
      })
      .catch(() => {
        // Leave encoder null; estimateTokens keeps using the fallback.
      });
  }
  return loading;
}

/** Synchronous estimate: exact once the encoder is loaded, else len/4. */
export function estimateTokens(text: string): number {
  if (!text) return 0;
  if (encoder) return encoder.encode(text).length;
  return Math.ceil(text.length / 4);
}

/** Estimate the on-disk cost of an entity (frontmatter + markdown body). */
export function estimateEntityTokens(frontmatter: Record<string, unknown>, body: string): number {
  return estimateTokens(`${JSON.stringify(frontmatter)}\n${body}`);
}
