import ts from "typescript";

// The module-editor files once formed a five-file import cycle: ModuleEditor
// imported all four section components, and every section imported the shared
// save/dry-run helpers back out of ModuleEditor. The helpers now live here in
// moduleEditShared, so the arrows run one way.
//
// This guards that by building the real import graph — every source file under
// src/, scanned with TypeScript's own preprocessor — and asking, for each
// module-editor file, whether a path leads from it back to itself. A new
// section file, a transitive cycle through a third module, a re-export or a
// dynamic `import()` back into ModuleEditor all fail this test.

const RAW = import.meta.glob("../**/*.{ts,tsx}", {
  query: "?raw", import: "default", eager: true,
}) as Record<string, string>;

/**
 * Collapse `.`/`..` segments. Paths are src-relative (`components/Field.tsx`);
 * a path that escapes src has no src-relative form, so it returns null rather
 * than silently clamping to a different file.
 */
function normalize(path: string): string | null {
  const out: string[] = [];
  for (const seg of path.split("/")) {
    if (seg === "" || seg === ".") continue;
    else if (seg === "..") { if (!out.pop()) return null; }
    else out.push(seg);
  }
  return out.join("/");
}

// import.meta.glob keys are relative to this file (src/components/). Test
// files stay in the graph — excluding them would silently drop any edge that
// pointed at one — they just aren't cycle roots.
const SOURCES = new Map(
  Object.entries(RAW).flatMap(([key, code]) => {
    const path = normalize("components/" + key);
    return path ? [[path, code] as const] : [];
  }),
);

/** Directory of a src-relative path; `""` for a file at the src root. */
function dirOf(path: string): string {
  const cut = path.lastIndexOf("/");
  return cut < 0 ? "" : path.slice(0, cut);
}

/**
 * Every module specifier in a file, via TypeScript's own preprocessor: it
 * covers static imports, `export … from`, `require` and dynamic `import()`
 * (including template-literal and comment-interrupted forms), and — unlike a
 * regex — never mistakes a specifier-shaped comment or string for an import.
 */
const specifiers = (code: string) =>
  ts.preProcessFile(code, true, true).importedFiles.map((f) => f.fileName);

/** Resolve a relative specifier to a src-relative source path, or null. */
function resolve(from: string, spec: string): string | null {
  const base = normalize(dirOf(from) + "/" + spec.split("?")[0]);
  if (base === null) return null;
  // `base === ""` is the src root itself — a bare `".."` from a component —
  // where the only candidates are its index files, with no path prefix.
  const candidates = base
    ? [base, `${base}.ts`, `${base}.tsx`, `${base}/index.ts`, `${base}/index.tsx`]
    : ["index.ts", "index.tsx"];
  for (const candidate of candidates) {
    if (SOURCES.has(candidate)) return candidate;
  }
  return null;
}

const isRelative = (spec: string) => spec.startsWith(".");

/** path -> the source files it imports. */
const GRAPH = new Map(
  [...SOURCES].map(([path, code]) => [
    path,
    specifiers(code).filter(isRelative)
      .map((spec) => resolve(path, spec))
      .filter((dep): dep is string => dep !== null),
  ]),
);

/**
 * A specifier that should have resolved to one of our source files: no
 * extension, or a JS/TS one. Anything else (`./index.css`, `./logo.svg?url`)
 * is an asset, so failing to resolve it is expected rather than a lost edge.
 * Testing what a module looks like, rather than listing every asset type,
 * keeps a new asset extension from tripping the assertion below.
 */
function isCodeSpecifier(spec: string): boolean {
  const name = spec.split("?")[0].split("/").pop() ?? "";
  if (/^\.*$/.test(name)) return true; // a bare `.` or `..` directory target
  const dot = name.lastIndexOf(".");
  return dot <= 0 || /^\.[mc]?[jt]sx?$/.test(name.slice(dot));
}

/** Relative specifiers that resolved to nothing — assets, or a dropped edge. */
const UNRESOLVED = [...SOURCES].flatMap(([path, code]) =>
  specifiers(code).filter(isRelative)
    .filter((spec) => resolve(path, spec) === null)
    .map((spec) => `${path} -> ${spec}`));

// Every section file plus the shared module. Deliberately narrower than
// `Module\w*` so an unrelated future `ModulePicker.tsx` isn't dragged in,
// but open enough that a new section editor is covered automatically.
const MODULE_EDITOR = /^components\/(Module\w*Editor|moduleEditShared)\.tsx$/;
const ENTRY_POINTS = [...SOURCES.keys()]
  .filter((p) => MODULE_EDITOR.test(p) && !p.includes(".test."));

/**
 * A path from `entry` back to `entry`, or null. Memoizing fully-explored
 * nodes is sound because the target is fixed: a node explored without
 * reaching `entry` can never reach it.
 */
function cycleThrough(entry: string): string[] | null {
  const exhausted = new Set<string>();
  const walk = (path: string, trail: string[]): string[] | null => {
    for (const dep of GRAPH.get(path) ?? []) {
      if (dep === entry) return [...trail, dep];
      if (exhausted.has(dep)) continue;
      exhausted.add(dep);
      const found = walk(dep, [...trail, dep]);
      if (found) return found;
    }
    return null;
  };
  return walk(entry, [entry]);
}

describe("module-editor import graph", () => {
  it("finds every module-editor file", () => {
    // Guards the glob itself: if this went empty or lost the section files,
    // the cycle check below would pass vacuously.
    expect(ENTRY_POINTS).toEqual(expect.arrayContaining([
      "components/ModuleEditor.tsx",
      "components/ModuleSchemaEditor.tsx",
      "components/ModuleRulesEditor.tsx",
      "components/ModuleContentEditor.tsx",
      "components/ModuleDisplayEditor.tsx",
      "components/moduleEditShared.tsx",
    ]));
  });

  it("drops no edge it could not resolve", () => {
    // Every unresolved relative specifier must be an asset, not a module the
    // resolver failed on — otherwise the graph has invisible holes.
    expect(UNRESOLVED.filter((u) => isCodeSpecifier(u.split(" -> ")[1])))
      .toEqual([]);
  });

  it.each(ENTRY_POINTS)("%s is in no import cycle", (entry) => {
    const cycle = cycleThrough(entry);
    expect(cycle, cycle ? `cycle: ${cycle.join(" -> ")}` : "").toBeNull();
  });
});
