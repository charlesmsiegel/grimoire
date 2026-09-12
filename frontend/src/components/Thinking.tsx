import { useState } from "react";
import { api } from "../api/client";
import { ErrorNote } from "./ErrorNote";

/** Reasoning is display-only text. Never parse its HTML or scene controls. */
function ThinkingText({ content }: { content: string }) {
  return <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", maxHeight: "24rem", overflowY: "auto" }}>
    {`<thinking>\n${content}\n</thinking>`}
  </pre>;
}

export function Thinking({ content }: { content: string }) {
  if (!content) return null;
  return <details className="thinking"><summary>Thinking</summary><ThinkingText content={content} /></details>;
}

/** Fetch only on expansion: collapsed history need not transfer every thought.
 *  The transcript's variant pointer keeps this tied to the displayed reply. */
export function SavedThinking({ cid, sid, responseId, variantId }: {
  cid: string; sid: string; responseId: string; variantId: string;
}) {
  const [content, setContent] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);
  async function load() {
    if (content !== null || loading) return;
    setLoading(true); setError(null);
    try {
      const record = await api.getResponse(cid, sid, responseId);
      setContent(record.variants.find((variant) => variant.id === variantId)?.reasoning ?? "");
    } catch (err) { setError(err); }
    finally { setLoading(false); }
  }
  return <details className="thinking" onToggle={(event) => { if (event.currentTarget.open) void load(); }}>
    <summary>Thinking</summary>
    {loading && <p role="status">Loading thinking…</p>}
    {error !== null && <p role="alert"><ErrorNote err={error} /></p>}
    {content !== null && (content ? <ThinkingText content={content} /> : <p>No thinking was saved for this variant.</p>)}
  </details>;
}
