import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

// Read the stylesheet off disk rather than importing it: vitest stubs CSS
// modules to an empty string, and `?raw` does not opt out of that, so an
// import would parse nothing and every case below would pass vacuously.
// `fileURLToPath` rather than `new URL("./index.css", import.meta.url)` —
// vite rewrites that exact pattern into an asset reference before node sees it.
const css = readFileSync(join(dirname(fileURLToPath(import.meta.url)), "index.css"), "utf8");

// `.picker` is the "control + action button" row used across the app: a
// select (or input) followed by the button that acts on it — "Set location",
// "+ Add", "Move to", "Use this calendar".
//
// The bug this guards: `.picker` was a non-wrapping flex row, and a `<select>`
// never shrinks below its widest option (`min-width: auto` resolves to the
// content's intrinsic size). So in the 286px scene inspector, the cast row
// (kind + actor + role + "+ Add") laid out to 478px and the location row to
// 330px against 252px of content width — the trailing button was pushed
// clean past the column, which clips, and neither "+ Add" nor "Move to" was
// reachable while a scene was open. The wide pages hid it: a long location
// name still fits in the 700px-odd cast panel, so only the inspector broke.
//
// jsdom runs no layout engine, so a rendered-geometry assertion is not
// available here — these assert the declarations that make the overflow
// impossible instead. Removing any of them reopens the bug.

/** The body of the first rule whose selector list matches `selector`. */
function ruleBody(selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = new RegExp(`(^|\\})\\s*${escaped}\\s*\\{([^}]*)\\}`, "m").exec(css);
  if (!match) throw new Error(`no rule for selector "${selector}"`);
  return match[2];
}

/** Declared value of `prop` in `body`, or null when it is not declared. */
function decl(body: string, prop: string): string | null {
  const match = new RegExp(`(?:^|;)\\s*${prop}\\s*:\\s*([^;]+)`, "i").exec(body);
  return match ? match[1].trim() : null;
}

describe("the .picker control row", () => {
  it("wraps, so the action button can never be pushed out of a column", () => {
    expect(decl(ruleBody(".picker"), "flex-wrap")).toBe("wrap");
  });

  it("lets its select and input shrink and clamp to the container", () => {
    const body = ruleBody(".picker input, .picker select");
    // min-width: 0 overrides the flex default of `auto`, which pins a select
    // to its widest option; max-width keeps a lone over-wide control inside
    // the column once it has wrapped onto its own line.
    expect(decl(body, "min-width")).toBe("0");
    expect(decl(body, "max-width")).toBe("100%");
  });

  it("stacks the inspector's pickers instead of rationing 286px four ways", () => {
    // Wrapping alone leaves the inspector's cast row ragged — the kind select
    // sits alone on a short line because the actor select's intrinsic width
    // will not fit beside it. Growing the selects fills each wrapped line.
    expect(decl(ruleBody(".inspector .picker select"), "flex")).toBe("1 1 auto");
  });
});
