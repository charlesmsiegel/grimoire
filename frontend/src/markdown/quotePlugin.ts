// rehype plugin: wrap double-quoted runs (straight or curly) in <span class="quoted">.
// Operates within a single text node; quotes spanning markdown nodes are left alone.
const QUOTE = /["“][^"”]*["”]/g;

function splitQuotes(value: string): any[] {
  const out: any[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  QUOTE.lastIndex = 0;
  while ((m = QUOTE.exec(value))) {
    if (m.index > last) out.push({ type: "text", value: value.slice(last, m.index) });
    out.push({
      type: "element", tagName: "span",
      properties: { className: ["quoted"] },
      children: [{ type: "text", value: m[0] }],
    });
    last = m.index + m[0].length;
  }
  if (last < value.length) out.push({ type: "text", value: value.slice(last) });
  return out;
}

function walk(node: any): void {
  if (!node || !Array.isArray(node.children)) return;
  const next: any[] = [];
  for (const child of node.children) {
    if (child.type === "text" && QUOTE.test(child.value)) {
      next.push(...splitQuotes(child.value));
    } else {
      walk(child);
      next.push(child);
    }
  }
  node.children = next;
}

export function quotePlugin() {
  return (tree: any) => walk(tree);
}
