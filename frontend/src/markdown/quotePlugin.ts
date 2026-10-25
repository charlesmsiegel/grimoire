import type { Nodes, Root, Text } from 'mdast';

// rehype plugin: wrap double-quoted runs (straight or curly) in <span class="quoted">.
// The scan is stateful: whether we are "inside" a quote carries across a block's
// inline children, so a quote that contains emphasis (e.g. "You are *very* good")
// stays highlighted across the markdown node boundary instead of mis-pairing its
// closer with the next line's opener. Each element's children are scanned with a
// fresh state, so quotes never bleed across block boundaries (paragraphs, list
// items, …).

// Tags whose content flows inline within a quote — an unclosed quote continues
// through these. Anything else (a nested block) ends the current quote run.
const INLINE = new Set([
  "em", "strong", "i", "b", "u", "s", "del", "ins", "mark",
  "sub", "sup", "small", "a", "code", "abbr", "span", "q",
]);

function isQuoteChar(ch: string): boolean {
  return ch === '"' || ch === "“" || ch === "”";
}

function quotedSpan(children: unknown[]) {
  return { type: "element", tagName: "span", properties: { className: ["quoted"] }, children };
}

// Split a text value into text / span.quoted nodes, carrying the open-quote state
// in and back out so it threads through sibling nodes.
function splitText(value: string, inQuote: boolean) {
  const runs: { value: string; quoted: boolean }[] = [];
  let buf = "";
  const flush = (quoted: boolean) => {
    if (!buf) return;
    runs.push({ value: buf, quoted });
    buf = "";
  };
  for (const ch of value) {
    if (isQuoteChar(ch)) {
      if (inQuote) {
        buf += ch;      // closing delimiter belongs to the quoted run
        flush(true);
        inQuote = false;
      } else {
        flush(false);   // emit the preceding narration
        buf = ch;       // opening delimiter starts the quoted run
        inQuote = true;
      }
    } else {
      buf += ch;
    }
  }
  flush(inQuote);        // trailing run: quoted if still open, else plain
  return { runs, inQuote };
}

function walk(node: unknown): void {
  if (typeof node !== "object" || node === null || !("children" in node) || !Array.isArray(node.children)) return;
  const children: unknown[] = node.children;
  const next: unknown[] = [];
  let inQuote = false;
  for (const child of children) {
    if (typeof child === "object" && child !== null && "type" in child
        && child.type === "text" && "value" in child && typeof child.value === "string") {
      const r = splitText(child.value, inQuote);
      next.push(...r.runs.map(run => run.quoted
        ? quotedSpan([{ type: 'text', value: run.value }]) : { type: 'text', value: run.value }));
      inQuote = r.inQuote;
    } else if (typeof child === "object" && child !== null && "type" in child && child.type === "element") {
      walk(child);
      if (inQuote && "tagName" in child && typeof child.tagName === "string" && INLINE.has(child.tagName)) {
        next.push(quotedSpan([child]));   // emphasis inside an open quote
      } else {
        inQuote = false;                  // a block boundary ends the quote
        next.push(child);
      }
    } else {
      next.push(child);
    }
  }
  node.children = next;
}

export function quotePlugin() {
  return (tree: unknown) => walk(tree);
}

const MARKDOWN_INLINE = new Set(['emphasis', 'strong', 'delete', 'link', 'linkReference']);

/** VN clips paragraphs into sentence pages. Mark the complete paragraph first:
 * a middle page may have neither delimiter, and a closing delimiter must not
 * become a new opener when that page is rendered on its own. Text-node data
 * survives clipping and becomes a safe span in the normal Markdown renderer. */
export function remarkQuotePlugin() {
  function annotate(node: Nodes, inQuote = false): boolean {
    if (!('children' in node)) return inQuote;
    const children = node.children.flatMap<Nodes>(child => {
      if (child.type === 'text') {
        const result = splitText(child.value, inQuote);
        inQuote = result.inQuote;
        return result.runs.map((run): Text => ({ ...child, value: run.value,
          ...(run.quoted ? { data: { ...child.data, hName: 'span', hProperties: { className: ['quoted'] } } } : {}),
        }));
      }
      if (MARKDOWN_INLINE.has(child.type)) inQuote = annotate(child, inQuote);
      else if (child.type === 'inlineCode' && inQuote) {
        return [{ ...child, data: { ...child.data, hProperties: { className: ['quoted'] } } }];
      } else if ('children' in child) {
        annotate(child);
        inQuote = false;
      }
      return [child];
    });
    // Each parent's child union is narrower than Nodes, but annotation only
    // replaces text with text and retains every other child's original type.
    node.children = children as typeof node.children;
    return inQuote;
  }
  return (tree: Root) => { annotate(tree); };
}
