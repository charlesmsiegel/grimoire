import { Fragment, memo } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { hideArtHandles } from "../../artHandles";
import { commentPlugin } from "../../markdown/commentPlugin";
import { quotePlugin } from "../../markdown/quotePlugin";

/** One post's markdown, parsed once per distinct text.
 *
 *  Memoized on the string, so a re-render of whatever holds it (the composer
 *  re-renders the play view on every keystroke, a stream on every delta) costs
 *  nothing unless the words changed. A parse is a full unified pipeline and a
 *  hast-to-React pass, which is the most expensive thing on this screen per
 *  byte. */
export const RenderedMarkdown = memo(function RenderedMarkdown({ content }: { content: string }) {
  // commentPlugin runs first, though nothing depends on it doing so: quotePlugin
  // scans `text` nodes only and steps over `raw`/`comment` ones without reading
  // their values, so a quote mark inside a note could never have opened a run
  // either way. The order is for reading, not for correctness.
  return (
    <Markdown remarkPlugins={[remarkGfm]} rehypePlugins={[commentPlugin, quotePlugin]}>{content}</Markdown>
  );
});

// A fence line, opening or closing: up to three spaces, then three or more of
// one fence character. Backtick info strings may not contain a backtick.
const FENCE = /^ {0,3}(`{3,}|~{3,})(.*)$/;
// The start of a list item, as far as the first line of a block can say:
// a bullet or an ordinal and then whitespace or the end of the line. A block
// that starts like this may be the next item of the list above it, and a list
// split across two parses comes back as two lists (and tight where the whole
// would be loose).
const LIST_ITEM = /^(?:[-*+]|\d{1,9}[.)])(?:[ \t]|$)/;
// A link reference or footnote definition. Either one changes how an EARLIER
// block parses — `[x]` is a link only once `[x]: url` exists somewhere — so a
// reply carrying one is parsed whole.
const DEFINITION = /^ {0,3}\[[^\]]+\]:/m;
// Enough of a line to answer LIST_ITEM for it: nine digits, the delimiter and
// the space after it, plus a character of slack.
const DECIDABLE = 12;

/** Where `text` can be cut into blocks that parse the same apart as together.
 *
 *  A boundary is the start of the first line after a blank one, and only where
 *  nothing in front of it is still open — a fenced block carries its blank
 *  lines inside it, and so does an HTML comment. The line it starts must also
 *  be known not to continue the block above: not indented (a list item's
 *  continuation, or indented code) and not a list item (the next item of a
 *  list, which would split one loose list into two tight ones). A line still
 *  too short to tell waits, which is what keeps every boundary stable as the
 *  text grows: once one is found, nothing appended can move it.
 *
 *  Conservative in every direction, because a boundary missed costs a slightly
 *  longer tail and a boundary taken wrongly costs a picture that reflows when
 *  the stored post replaces it.
 *
 *  Exported for its own test. */
export function blockBoundaries(text: string): number[] {
  if (DEFINITION.test(text)) return [];
  const out: number[] = [];
  let fence: { char: string; len: number } | null = null;
  let inComment = false;
  let sawBlank = false;
  let at = 0;
  while (at < text.length) {
    const nl = text.indexOf("\n", at);
    const end = nl === -1 ? text.length : nl;
    const line = text.slice(at, end);
    if (fence) {
      const m = FENCE.exec(line);
      if (m && m[1][0] === fence.char && m[1].length >= fence.len && !m[2].trim()) fence = null;
    } else if (inComment) {
      inComment = !closesComment(line);
    } else if (!line.trim()) {
      sawBlank = true;
    } else {
      if (sawBlank && !/^[ \t]/.test(line) && !LIST_ITEM.test(line)
          && (nl !== -1 || line.length >= DECIDABLE)) {
        out.push(at);
      }
      sawBlank = false;
      const m = FENCE.exec(line);
      if (m && !(m[1][0] === "`" && m[2].includes("`"))) {
        fence = { char: m[1][0], len: m[1].length };
      } else {
        inComment = opensComment(line);
      }
    }
    if (nl === -1) break;
    at = nl + 1;
  }
  return out;
}

/** Whether a line leaves an HTML comment open, read left to right. */
function opensComment(line: string): boolean {
  let i = 0;
  for (;;) {
    const open = line.indexOf("<!--", i);
    if (open === -1) return false;
    const close = line.indexOf("-->", open + 4);
    if (close === -1) return true;
    i = close + 3;
  }
}

/** Whether a line inside an open comment closes it and opens no other. */
function closesComment(line: string): boolean {
  const close = line.indexOf("-->");
  return close !== -1 && !opensComment(line.slice(close + 3));
}

/** A reply that is still arriving, parsed a block at a time.
 *
 *  Rendering the whole accumulated reply on every delta re-parses everything
 *  above the cursor each time, so a reply costs the square of its length to
 *  watch arrive. Here the blocks `blockBoundaries` has closed each render as
 *  their own `RenderedMarkdown`, which is memoized on its text — once a block
 *  is closed its string never changes, so it is parsed once — and only the
 *  open tail is parsed again per delta.
 *
 *  The seam between two blocks is a "\n", which is exactly the text node the
 *  parser puts between two top-level blocks of one document, so the DOM is the
 *  one a single parse of the same text would build (the test holds it to
 *  that). Display only, like `hideArtHandles` beside it: the stored post
 *  replaces all of this the moment the turn lands.
 *
 *  Art handles are hidden per block. A handle never spans a blank line, and a
 *  partial one can only be at the very end, which is the tail's. A block that
 *  hiding leaves blank (a handle on a line of its own) parses to nothing, and
 *  gets no seam either, since the whole text has no block there to separate. */
export const StreamingMarkdown = memo(function StreamingMarkdown({ text }: { text: string }) {
  const starts = [0, ...blockBoundaries(text)];
  const blocks = starts.map((start, i) => ({
    start, content: hideArtHandles(text.slice(start, starts[i + 1])),
  }));
  let seen = false;
  return (
    <>
      {blocks.map(({ start, content }) => {
        const seam = seen && content.trim() !== "";
        seen ||= content.trim() !== "";
        return (
          // Keyed by where the block starts, which never moves as the text
          // grows, so a closed block keeps its element and its memoized parse.
          <Fragment key={start}>
            {seam && "\n"}
            <RenderedMarkdown content={content} />
          </Fragment>
        );
      })}
    </>
  );
});
