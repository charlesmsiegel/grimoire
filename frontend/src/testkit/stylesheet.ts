import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

/** `index.css` as a test reads it, and the tools for reading it.
 *
 *  Off disk rather than imported, for `pickerLayout.test.ts`'s reason: vitest
 *  stubs CSS imports to an empty string, so an import would make every case
 *  built on this pass vacuously. Comments come out first: these suites argue
 *  with themselves in prose, and a rule whose comment NAMES the selector it
 *  is explaining would otherwise read as a rule that selects it. Every case in
 *  `focus.test.tsx` was wrong in exactly that way once.
 *
 *  Shared between the focus-mode suite and the composer suite, which is why
 *  it is here and not in either: a stylesheet reader that two suites each
 *  carried a copy of would be two readers that can disagree about what a
 *  rule is. */
export function stylesheet() {
  const css = readFileSync(
    join(dirname(fileURLToPath(import.meta.url)), "..", "index.css"), "utf8")
    .replace(/\/\*[\s\S]*?\*\//g, "");

  /** The concatenated bodies of every `@media (max-width: <px>px)` block.
   *  Braces are counted rather than matched with a regex, because these
   *  blocks nest. */
  function atWidth(px: number): string {
    const query = `@media (max-width: ${px}px)`;
    const out: string[] = [];
    for (let i = 0; ; ) {
      const at = css.indexOf(query, i);
      if (at === -1) break;
      const open = css.indexOf("{", at);
      let depth = 0;
      let j = open;
      for (; j < css.length; j++) {
        if (css[j] === "{") depth++;
        else if (css[j] === "}" && --depth === 0) break;
      }
      out.push(css.slice(open + 1, j));
      i = j + 1;
    }
    if (!out.length) throw new Error(`no ${query} block in index.css`);
    return out.join("\n");
  }

  return { css, atWidth, bodiesNaming, bodiesOf, declares };
}

const rule = () => /([^{}]*)\{([^{}]*)\}/g;

/** Every rule in `text` whose selector list names the class `cls`, as bodies.
 *
 *  Whole-token, not substring: `.header-focus` must not match
 *  `.header-focus-word`, which is a different control-half with the opposite
 *  `display` — reading one as the other is how the first draft of the focus
 *  cases reported the fixed stylesheet as still broken.
 *
 *  Both ends are guarded. The dot is escaped as `\\.` and not as `\\${cls}`,
 *  which is an identity escape emitting `\\header-focus` — a pattern that
 *  matches the bare word anywhere and so anchors nothing on the left. */
export function bodiesNaming(text: string, cls: string): string[] {
  const named = new RegExp(`\\.${cls}(?![-\\w])`);
  const bodies: string[] = [];
  const re = rule();
  for (let m = re.exec(text); m; m = re.exec(text)) {
    if (named.test(m[1])) bodies.push(m[2]);
  }
  return bodies;
}

/** Every rule in `text` whose whole selector list is `selector`, as bodies.
 *
 *  The exact form of `bodiesNaming`: `.inputbar textarea` and `.inputbar`
 *  both name the class, and a case about the box must not pass on a
 *  declaration made about the bar. Whitespace is normalised on both sides,
 *  so a selector wrapped across lines in the stylesheet still matches. */
export function bodiesOf(text: string, selector: string): string[] {
  const want = selector.trim().replace(/\s+/g, " ");
  const bodies: string[] = [];
  const re = rule();
  for (let m = re.exec(text); m; m = re.exec(text)) {
    if (m[1].trim().replace(/\s+/g, " ") === want) bodies.push(m[2]);
  }
  return bodies;
}

/** The value `body` gives `prop`, or null. The first declaration when there
 *  are several — a `vh` fallback ahead of its `dvh` reads as the `vh`. */
export const declares = (body: string, prop: string): string | null => {
  const m = new RegExp(`(?:^|;)\\s*${prop}\\s*:\\s*([^;]+)`, "i").exec(body);
  return m ? m[1].trim() : null;
};
