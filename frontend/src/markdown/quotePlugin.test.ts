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
