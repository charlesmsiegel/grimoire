import { render } from "@testing-library/react";
import { hideArtHandles } from "../../artHandles";

// Counts parses: every render of `react-markdown` is a whole unified pipeline,
// and the characters handed to it are what a streamed reply costs to watch.
const parsed = vi.hoisted(() => ({ calls: 0, chars: 0 }));
vi.mock("react-markdown", async () => {
  const actual = await vi.importActual<typeof import("react-markdown")>("react-markdown");
  const Counted = (props: Parameters<typeof actual.default>[0]) => {
    parsed.calls += 1;
    parsed.chars += String(props.children ?? "").length;
    return actual.default(props);
  };
  return { ...actual, default: Counted };
});

import { RenderedMarkdown, StreamingMarkdown, blockBoundaries } from "./StreamingMarkdown";

/** What one parse of the whole text builds — the picture the stored post will
 *  replace the stream with, and so the one the stream has to match. */
function whole(text: string): string {
  const { container, unmount } = render(<RenderedMarkdown content={hideArtHandles(text)} />);
  const html = container.innerHTML;
  unmount();
  return html;
}

/** Stream `text` in `step`-sized deltas, checking the DOM against a single
 *  parse of the same prefix after every one of them. */
function streamAndCompare(text: string, step = 3) {
  const { container, rerender, unmount } = render(<StreamingMarkdown text="" />);
  for (let n = step; n < text.length + step; n += step) {
    const prefix = text.slice(0, n);
    rerender(<StreamingMarkdown text={prefix} />);
    expect(container.innerHTML, `after ${JSON.stringify(prefix)}`).toBe(whole(prefix));
  }
  unmount();
}

const PROSE = "The lamps along the Saltmarch quay were lit when *Seraphine* stepped off.\n\n"
  + "\"You're late,\" Mara said, not looking up. \"The tide won't wait for **anyone**.\"\n\n"
  + "Winifred folded her arms. \"Then it can \"wait\" for me.\"\n\n"
  + "Nobody laughed.";

test("streamed prose and quotes build the DOM a single parse of the same text builds", () => {
  streamAndCompare(PROSE);
});

test("a list split by blank lines stays one list while it streams", () => {
  // Loose (blank lines between items) and ordered — the two shapes a naive
  // paragraph split breaks into several lists.
  streamAndCompare("Three things on the ledger:\n\n- the salt tithe\n\n- the lamp oil\n\n"
    + "- a debt to Mara\n\nAnd one more:\n\n1. a letter\n\n2. a key\n\nThat was all.");
});

test("a fenced block with blank lines inside it is never cut", () => {
  streamAndCompare("The ledger read:\n\n```\nSaltmarch  12\n\n\nRealm       4\n```\n\n"
    + "~~~~\ninside\n\n~~~\nstill inside\n~~~~\n\nAfter it.");
});

test("an HTML comment spanning a blank line is never cut", () => {
  streamAndCompare("Mara smiles.\n\n<!-- she is lying\n\nremember this -->\n\nWinifred nods.");
  streamAndCompare("Mara smiles.\n\n<!-- a note on its own -->\n\nWinifred nods.");
});

test("a reference definition arriving later re-parses the reply whole", () => {
  streamAndCompare("See the [harbour map] for the route.\n\nIt is old.\n\n[harbour map]: /maps/saltmarch\n");
});

test("art handles stay hidden, partial ones included, while the reply arrives", () => {
  streamAndCompare("Seraphine turns.\n\n[[art:characters:seraphine:portrait]]\n\nShe says nothing.");
});

test("a closed block is parsed once, so a long reply does not cost its square", () => {
  const para = "Winifred waited for the tide to turn, as it always did, at the worst moment. ";
  const reply = Array.from({ length: 20 }, (_, i) => `${para}${para}(${i})`).join("\n\n");
  parsed.calls = 0;
  parsed.chars = 0;
  const { rerender, unmount } = render(<StreamingMarkdown text="" />);
  const step = 8;
  let wholeChars = 0;
  for (let n = step; n < reply.length + step; n += step) {
    rerender(<StreamingMarkdown text={reply.slice(0, n)} />);
    wholeChars += Math.min(n, reply.length);
  }
  unmount();
  // Re-parsing the accumulated reply every delta would hand the parser
  // `wholeChars`; the tail alone is a paragraph at most.
  expect(parsed.chars).toBeLessThan(wholeChars / 10);
});

test("a boundary waits until the line after the blank one can be told apart", () => {
  // Too short to know whether it is `12. an item` or a sentence.
  expect(blockBoundaries("Mara.\n\n12")).toEqual([]);
  expect(blockBoundaries("Mara.\n\n12. the salt tithe")).toEqual([]);
  expect(blockBoundaries("Mara.\n\n1200 barrels came in.")).toEqual([7]);
  // Indented: a list item's continuation, or indented code.
  expect(blockBoundaries("- salt\n\n  more salt\n")).toEqual([]);
  // A finished line decides at once, however short.
  expect(blockBoundaries("Mara.\n\nNo.\n")).toEqual([7]);
});
