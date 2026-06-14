import { useCallback } from "react";

import { useResource } from "../api/useResource";
import { ensureTokenizer, estimateTokens } from "./tokens";

/** Badge showing an approximate token cost for a block of text. */
export function TokenBadge({ text, className }: { text: string; className?: string }) {
  // `data` flips to non-null once the real encoder is loaded, forcing a
  // recompute from the len/4 fallback to the exact count.
  const loadTokenizer = useCallback(() => ensureTokenizer(), []);
  const { data } = useResource(loadTokenizer);

  // `data` is read so the value recomputes when the encoder arrives.
  void data;
  const count = estimateTokens(text);
  return (
    <span
      className={className ? `token-badge ${className}` : "token-badge"}
      title="Approximate token count (cl100k estimate; the live model's tokenizer differs)"
    >
      ~{count.toLocaleString("en-US")} tokens
    </span>
  );
}
