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

function quotedSpan(children: any[]): any {
  return { type: "element", tagName: "span", properties: { className: ["quoted"] }, children };
}

// Split a text value into text / span.quoted nodes, carrying the open-quote state
// in and back out so it threads through sibling nodes.
function splitText(value: string, inQuote: boolean): { nodes: any[]; inQuote: boolean } {
  const nodes: any[] = [];
  let buf = "";
  const flush = (quoted: boolean) => {
    if (!buf) return;
    nodes.push(quoted ? quotedSpan([{ type: "text", value: buf }]) : { type: "text", value: buf });
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
  return { nodes, inQuote };
}

function walk(node: any): void {
  if (!node || !Array.isArray(node.children)) return;
  const next: any[] = [];
  let inQuote = false;
  for (const child of node.children) {
    if (child.type === "text") {
      const r = splitText(child.value, inQuote);
      next.push(...r.nodes);
      inQuote = r.inQuote;
    } else if (child.type === "element") {
      walk(child);
      if (inQuote && INLINE.has(child.tagName)) {
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
  return (tree: any) => walk(tree);
}
