import type { Message } from "../../api/client";

/** Whether a citation's excerpt is in this post.
 *
 *  `needle` arrives trimmed and lower-cased. Both texts are searched because
 *  the server judges a citation against the PROMPT view, which a rule may have
 *  changed, and the client holds only the stored text and the DISPLAY view
 *  (`shown`). A rule that applies to both phases -- the common case -- makes
 *  `shown` carry the quoted words; one that applies to the prompt alone can
 *  still leave a quote that neither text contains, and that quote is not found.
 */
export function quotedIn(m: Pick<Message, "content" | "shown">, needle: string): boolean {
  if (!needle) return false;
  return m.content.toLowerCase().includes(needle)
    || (m.shown !== undefined && m.shown.toLowerCase().includes(needle));
}
