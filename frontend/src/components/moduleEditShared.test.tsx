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

/**
 * Every extension a module source can have. Three things must agree on this
 * set — the glob that loads the sources, the candidates `resolve()` probes,
 * and the JS→TS substitution below — because a specifier that resolves to
 * nothing drops its edge from the graph. `GLOB` repeats the list because
 * `import.meta.glob` needs a literal pattern; the last case in this file
 * asserts the two never drift apart.
 */
const MODULE_EXTS = [".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs"];
const GLOB = "../**/*.{ts,tsx,mts,cts,js,jsx,mjs,cjs}";

const RAW = import.meta.glob("../**/*.{ts,tsx,mts,cts,js,jsx,mjs,cjs}", {
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

/**
 * TypeScript lets a specifier name the file that will be *emitted*, so
 * `./foo.js` on disk is `foo.ts`. Probing only the literal name would leave
 * such an import unresolved — dropping a real edge, and tripping the
 * completeness assertion below on a perfectly valid import.
 */
const JS_TO_TS: Record<string, string[]> = {
  ".js": [".ts", ".tsx"],
  ".jsx": [".tsx"],
  ".mjs": [".mts"],
  ".cjs": [".cts"],
};

/** Resolve a relative specifier to a src-relative source path, or null. */
function resolve(from: string, spec: string): string | null {
  const base = normalize(dirOf(from) + "/" + spec.split("?")[0]);
  if (base === null) return null;
  // `base === ""` is the src root itself — a bare `".."` from a component —
  // where the only candidates are its index files, with no path prefix.
  const candidates = base
    ? [base, ...MODULE_EXTS.map((e) => base + e),
       ...MODULE_EXTS.map((e) => `${base}/index${e}`)]
    : MODULE_EXTS.map((e) => `index${e}`);
  const js = Object.keys(JS_TO_TS).find((ext) => base.endsWith(ext));
  if (js) {
    const stem = base.slice(0, -js.length);
    candidates.push(...JS_TO_TS[js].map((ext) => stem + ext));
  }
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

  it("resolves the specifier forms the bundler accepts", () => {
    // Extensionless, explicit, and the TypeScript `.js`-means-`.ts` form.
    // The last one is why JS_TO_TS exists: without it a valid `./client.js`
    // import would drop its edge and fail the completeness case below.
    const from = "components/ModuleEditor.tsx";
    expect(resolve(from, "../api/client")).toBe("api/client.ts");
    expect(resolve(from, "../api/client.ts")).toBe("api/client.ts");
    expect(resolve(from, "../api/client.js")).toBe("api/client.ts");
    expect(resolve(from, "./moduleEditShared")).toBe("components/moduleEditShared.tsx");
    expect(resolve(from, "./moduleEditShared.js")).toBe("components/moduleEditShared.tsx");
    expect(resolve(from, "../index.css")).toBeNull(); // asset, not a module edge
  });

  it("loads every extension it claims to resolve", () => {
    // The bug this catches: `.mjs`→`.mts` was added to JS_TO_TS while the glob
    // still only loaded {ts,tsx}, so the mapping could never hit and a valid
    // `./foo.mjs` import would fail the completeness case below.
    const globbed = GLOB.slice(GLOB.indexOf("{") + 1, GLOB.indexOf("}"))
      .split(",").map((e) => `.${e}`);
    expect(globbed).toEqual(MODULE_EXTS);
    for (const targets of Object.values(JS_TO_TS)) {
      expect(MODULE_EXTS).toEqual(expect.arrayContaining(targets));
    }
    // …and the literal passed to import.meta.glob really is GLOB: if it were
    // not, SOURCES would be missing files this suite depends on.
    expect(SOURCES.has("components/moduleEditShared.tsx")).toBe(true);
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
