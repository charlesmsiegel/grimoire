// rehype plugin: drop HTML comments from the rendered transcript.
//
// `<!-- ... -->` is how an author leaves a note to themselves in a scene — a
// reminder that a character is lying, a marker for a beat to come back to. It
// belongs in the stored text, and the edit box hands back the message verbatim,
// so nothing here touches what is saved. It just must not appear in the prose
// the reader is looking at, which is what it did.
//
// Done on the TREE rather than by stripping `<!--.*?-->` from the source string,
// for one reason worth stating: a comment inside a fenced code block is content,
// not a note, and a regex over the raw markdown cannot tell the two apart. In
// the tree that content is a `text` node inside <pre><code> and is never a
// comment node, so it survives untouched.
//
// Both shapes are handled because which one appears depends on the pipeline:
// remark-rehype turns an mdast `html` node into a hast `raw` node (a string of
// unparsed HTML), while a tree that has been through rehype-raw or an HTML
// parser carries a real `comment` node. Matching only one would work until the
// plugin list changed underneath it.
//
// Two known limits, both from working on raw STRINGS rather than a parsed HTML
// tree, and both judged not worth a tokenizer here (Codex review):
//
// - Prose left over on a comment's own line stays raw, so markdown in it is not
//   re-parsed: `<!-- note -->[map](url)` shows the link syntax literally. That
//   is unchanged from before this plugin existed -- CommonMark already groups
//   the whole line into one HTML block, so the link was never parsed -- and
//   fixing it means stripping comments from the SOURCE, which would then strip
//   them inside fenced code blocks too. The trade was taken the other way.
// - Comment delimiters that are content of a raw-text element are cut anyway:
//   `<script>const m = "<!-- x -->";</script>` loses the middle. Telling that
//   apart needs HTML-context-aware tokenization. react-markdown does not render
//   raw HTML at all (it is shown as text), so the cost is a mangled literal in
//   prose that was already being displayed as markup.

// Every comment in a value, not the value as a whole. CommonMark groups
// block-start HTML together with the text that follows it, so a comment is NOT
// reliably a node of its own: `<!-- beat -->She looks away.` arrives as one raw
// node. Testing the whole value failed in both directions at once (Codex
// review) -- that node did not match, so the note rendered as visible text,
// while `<!--a-->visible<!--b-->` DID match end to end and took `visible` with
// it. So comments are cut out of the value and whatever is left stays.
/** What survives a raw value once its comments are cut out; "" if nothing does.
 *
 *  Scanned with `indexOf` rather than a `/<!--[\s\S]*?-->/g` replace. The regex
 *  is quadratic on a value full of unterminated openers -- each one sends the
 *  lazy quantifier scanning to the end of the string looking for a closer that
 *  is not there -- and a stored message has no length limit, so a pathological
 *  one froze the render thread for seconds (Codex review, measured at ~9s for
 *  100k openers). This resumes from where the last one ended, so it is linear.
 *
 *  An unterminated opener ends the scan and keeps everything from it onward:
 *  half a comment is not a comment, and dropping the rest of the message on
 *  account of a typo would be the worst possible reading of it. */
function withoutComments(value: string): string {
  let out = "";
  let i = 0;
  for (;;) {
    const start = value.indexOf("<!--", i);
    if (start === -1) { out += value.slice(i); break; }
    const end = value.indexOf("-->", start + 4);
    if (end === -1) { out += value.slice(i); break; }
    out += value.slice(i, start);
    i = end + 3;
  }
  return out.trim() ? out : "";
}

function walk(node: any): void {
  if (!node || !Array.isArray(node.children)) return;
  const next: any[] = [];
  for (const child of node.children) {
    if (child?.type === "comment") continue;              // a parsed comment: gone
    if (child?.type === "raw" && typeof child.value === "string") {
      const rest = withoutComments(child.value);
      if (!rest) continue;                                // nothing but notes
      if (rest !== child.value) child.value = rest;       // notes cut, prose kept
    }
    walk(child);
    next.push(child);
  }
  node.children = next;
}

export function commentPlugin() {
  return (tree: any) => walk(tree);
}
