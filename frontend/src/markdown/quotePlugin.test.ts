import { quotePlugin } from "./quotePlugin";

test("wraps double-quoted text in span.quoted", () => {
  const tree: any = { type: "root", children: [
    { type: "element", tagName: "p", children: [
      { type: "text", value: 'She said "hello there" softly.' },
    ] },
  ] };
  quotePlugin()(tree);
  const p = tree.children[0];
  const span = p.children.find((c: any) => c.type === "element" && c.tagName === "span");
  expect(span).toBeTruthy();
  expect(span.properties.className).toContain("quoted");
  expect(span.children[0].value).toBe('"hello there"');
});

test("leaves unquoted text untouched", () => {
  const tree: any = { type: "root", children: [
    { type: "element", tagName: "p", children: [{ type: "text", value: "no quotes here" }] },
  ] };
  quotePlugin()(tree);
  expect(tree.children[0].children).toEqual([{ type: "text", value: "no quotes here" }]);
});

// A quote containing emphasis (e.g. italics) splits the paragraph across markdown
// nodes. The opener and closer land in different text nodes, so a naive per-node
// pass mis-pairs the closer of one line with the opener of the next — highlighting
// the narration between them instead of the dialogue.
test("quotes containing emphasis highlight the dialogue, not the narration", () => {
  const tree: any = { type: "root", children: [
    { type: "element", tagName: "p", children: [
      { type: "text", value: '"You are very ' },
      { type: "element", tagName: "em", children: [{ type: "text", value: "responsive" }] },
      { type: "text", value: ' in the morning," she observes. "Noted."' },
    ] },
  ] };
  quotePlugin()(tree);
  const p = tree.children[0];
  const text = (node: any): string =>
    node.type === "text" ? node.value : (node.children || []).map(text).join("");
  const highlighted = p.children
    .filter((c: any) => c.tagName === "span" && c.properties?.className?.includes("quoted"))
    .map(text)
    .join("|");
  // The narration between the two lines must NOT be highlighted.
  expect(highlighted).not.toContain("she observes");
  // Both spoken lines must be highlighted, including the italicised word.
  expect(highlighted).toContain("You are very");
  expect(highlighted).toContain("responsive");
  expect(highlighted).toContain("morning,");
  expect(highlighted).toContain("Noted.");
});
