import { commentPlugin } from "./commentPlugin";

const p = (...children: any[]) => ({ type: "element", tagName: "p", children });
const text = (value: string) => ({ type: "text", value });
const raw = (value: string) => ({ type: "raw", value });
const root = (...children: any[]) => ({ type: "root", children });

test("drops a raw node holding an HTML comment", () => {
  const tree: any = root(p(text("before")), raw("<!-- a note -->"), p(text("after")));
  commentPlugin()(tree);
  expect(tree.children).toHaveLength(2);
  expect(tree.children.map((c: any) => c.children[0].value)).toEqual(["before", "after"]);
});

test("drops a real comment node too", () => {
  // Which shape appears depends on the plugin list: remark-rehype emits `raw`,
  // an HTML parser emits `comment`. Matching one only works until that changes.
  const tree: any = root(p(text("before")), { type: "comment", value: " a note " });
  commentPlugin()(tree);
  expect(tree.children).toHaveLength(1);
});

test("drops a comment nested inside a block", () => {
  const tree: any = root(p(text("She smiles "), raw("<!-- beat -->"), text(" and turns away.")));
  commentPlugin()(tree);
  expect(tree.children[0].children.map((c: any) => c.value))
    .toEqual(["She smiles ", " and turns away."]);
});

test("multi-line comments go, and comments spanning several lines of note", () => {
  const tree: any = root(raw("<!--\n  she is lying\n  come back to this\n-->"), p(text("kept")));
  commentPlugin()(tree);
  expect(tree.children).toHaveLength(1);
  expect(tree.children[0].children[0].value).toBe("kept");
});

// The reason this is a tree pass rather than a regex over the markdown source:
// inside a code fence the same characters are content the reader asked to see.
test("a comment inside a code block is content and survives", () => {
  const tree: any = root({
    type: "element", tagName: "pre",
    children: [{ type: "element", tagName: "code", children: [text("<!-- not a note -->")] }],
  });
  commentPlugin()(tree);
  const code = tree.children[0].children[0];
  expect(code.children[0].value).toBe("<!-- not a note -->");
});

// A raw node is not automatically a comment -- other raw HTML must not be
// swept up by a rule that was only ever meant to remove notes.
test("raw HTML that is not a comment is left alone", () => {
  const tree: any = root(raw("<br>"), raw("<!-- gone -->"));
  commentPlugin()(tree);
  expect(tree.children).toHaveLength(1);
  expect(tree.children[0].value).toBe("<br>");
});

test("text that merely mentions the delimiters is untouched", () => {
  const tree: any = root(p(text("write <!-- like this --> to leave a note")));
  commentPlugin()(tree);
  expect(tree.children[0].children[0].value).toBe("write <!-- like this --> to leave a note");
});

// Codex review. CommonMark groups block-start HTML with the text that follows
// it into ONE raw node, so a comment is not always a node of its own. Matching
// whole nodes therefore failed in both directions at once.
test("a comment sharing its node with prose is removed without taking the prose", () => {
  const tree: any = root(raw("<!-- beat -->She looks away."));
  commentPlugin()(tree);
  expect(tree.children).toHaveLength(1);
  expect(tree.children[0].value).toBe("She looks away.");
});

test("two comments around prose do not swallow what sits between them", () => {
  const tree: any = root(raw("<!--a-->visible<!--b-->"));
  commentPlugin()(tree);
  expect(tree.children).toHaveLength(1);
  expect(tree.children[0].value).toBe("visible");
});

test("a raw node that is nothing but comments goes entirely", () => {
  const tree: any = root(raw("<!--a--> <!--b-->"), p(text("kept")));
  commentPlugin()(tree);
  expect(tree.children).toHaveLength(1);
  expect(tree.children[0].children[0].value).toBe("kept");
});

// Codex review. A lazy-quantifier regex rescans to the end of the string for
// every unterminated opener, which is quadratic; a stored message has no length
// limit, so a pathological one froze the render thread for seconds.
test("a value full of unterminated openers scans in linear time", () => {
  const tree: any = root(raw("<!--".repeat(100_000) + "tail"));
  const started = performance.now();
  commentPlugin()(tree);
  expect(performance.now() - started).toBeLessThan(1000);   // was ~9s
  // ...and an unterminated opener is not a comment, so nothing is thrown away
  expect(tree.children[0].value).toContain("tail");
});

test("an unterminated opener keeps everything from it onward", () => {
  const tree: any = root(raw("<!-- closed --> kept <!-- never closed"));
  commentPlugin()(tree);
  expect(tree.children[0].value).toBe(" kept <!-- never closed");
});

test("a tree with no comments is unchanged", () => {
  const tree: any = root(p(text("nothing to do here")));
  commentPlugin()(tree);
  expect(tree.children[0].children[0].value).toBe("nothing to do here");
});
