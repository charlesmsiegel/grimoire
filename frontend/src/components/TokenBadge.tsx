import { useEffect, useState } from "react";

import { ensureTokenizer, estimateTokens } from "./tokens";

/** Badge showing an approximate token cost for a block of text. */
export function TokenBadge({ text, className }: { text: string; className?: string }) {
  // `ready` flips once the real encoder is loaded, forcing a recompute from
  // the len/4 fallback to the exact count.
  const [ready, setReady] = useState(false);
  useEffect(() => {
    let active = true;
    void ensureTokenizer().then(() => active && setReady(true));
    return () => {
      active = false;
    };
  }, []);

  // `ready` is read so the value recomputes when the encoder arrives.
  void ready;
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
