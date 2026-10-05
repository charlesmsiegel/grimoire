/** Whether anything in the app embeds text — the Settings "Embeddings" chip.
 *
 *  A mirror of `store/embed_space.resolve()` returning non-None, which is the
 *  one gate every embedder shares: recall, the art catalogue, search by
 *  meaning, and the continuity checks after a wrap-up. Recall depth is not part
 *  of it — depth `0` turns recall off and nothing else — so it is reported
 *  separately, and conflating the two is what let "depth 0" read as "nothing is
 *  sent" while the other embedders still ran.
 *
 *  The conditions are resolve()'s, in its order: a connection id, a connection
 *  it names, an `openai_compatible` kind (the only one serving `/embeddings`),
 *  a base URL, and a non-blank model. Keep the two in step.
 *
 *  It reads the DRAFT. The row chip only renders when its section is not
 *  dirty — an unsaved edit shows the dot in the value's slot — so whenever this
 *  value is visible the draft equals the saved config. */
export function embeddingsOn(
  draft: { embeddings_connection_id: string; embeddings_model: string },
  connections: readonly { id: string; kind: string; base_url: string }[],
): boolean {
  const id = (draft.embeddings_connection_id || "").trim();
  if (!id || !(draft.embeddings_model || "").trim()) return false;
  const conn = connections.find((c) => c.id === id);
  return !!conn && conn.kind === "openai_compatible" && !!conn.base_url;
}

/** The capstone spec's §9.5 sentence, verbatim. It is the whole text of its
 *  own paragraph on the Settings page, so a test can hold it to the spec by
 *  exact match. */
export const EMBEDDINGS_COPY =
  "With a connection and model set, Grimoire also embeds plot-thread and commitment " +
  "summaries after each wrap-up to find possible overlaps. Recall depth does not control " +
  "this. Set the connection to Off to stop all embedding.";
