/** What embedding the continuity checks do, and how to stop it. The whole
 *  text of its own paragraph on the Settings page, so a test can hold it by
 *  exact match.
 *
 *  It began as the capstone spec's §9.5 sentence, verbatim; that sentence
 *  predates the role vocabulary ("Set the connection to Off"), and slice C
 *  removed the connection control it named, so it now names the control
 *  that exists -- the Embedding role on Models -- with the same facts. */
export const EMBEDDINGS_COPY =
  "With the Embedding role set, Grimoire also embeds plot-thread and commitment " +
  "summaries after each wrap-up to find possible overlaps. Recall depth does not control " +
  "this. Clear the Embedding role on the Models page to stop all embedding.";
