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
 * Every specifier that survives to the emitted JavaScript, via the TypeScript
 * AST. Static imports, `export … from`, side-effect imports and dynamic
 * `import()` all count; unlike a regex this never mistakes a
 * specifier-shaped comment or string for an import.
 *
 * Type-only edges are excluded deliberately. `import type { X }`, `export
 * type { X } from`, and a clause whose every named binding is `type X` are
 * erased at emit, so they cannot participate in an initialization cycle —
 * counting them would reject a valid change (a section needing only a *type*
 * from ModuleEditor) as a runtime cycle that does not exist.
 */
function specifiers(code: string): string[] {
  const src = ts.createSourceFile("f.tsx", code, ts.ScriptTarget.Latest,
                                  true, ts.ScriptKind.TSX);
  const found: string[] = [];
  const literal = (n: ts.Node | undefined) =>
    n && ts.isStringLiteral(n) ? n.text : undefined;

  for (const st of src.statements) {
    if (ts.isImportDeclaration(st)) {
      const clause = st.importClause;
      if (clause?.isTypeOnly) continue;                    // import type { … }
      const named = clause?.namedBindings;
      // A clause with only `{ type A, type B }` and no default/namespace
      // binding emits nothing either.
      if (named && ts.isNamedImports(named) && !clause?.name &&
          named.elements.length > 0 &&
          named.elements.every((e) => e.isTypeOnly)) continue;
      const spec = literal(st.moduleSpecifier);
      if (spec) found.push(spec);
    } else if (ts.isExportDeclaration(st)) {
      if (st.isTypeOnly) continue;                         // export type … from
      const clause = st.exportClause;
      if (clause && ts.isNamedExports(clause) && clause.elements.length > 0 &&
          clause.elements.every((e) => e.isTypeOnly)) continue;
      const spec = literal(st.moduleSpecifier);
      if (spec) found.push(spec);
    }
  }

  // Dynamic import() can appear anywhere, and is always a runtime edge.
  const walk = (node: ts.Node): void => {
    if (ts.isCallExpression(node) &&
        node.expression.kind === ts.SyntaxKind.ImportKeyword) {
      const spec = literal(node.arguments[0]);
      if (spec) found.push(spec);
    }
    ts.forEachChild(node, walk);
  };
  ts.forEachChild(src, walk);
  return found;
}

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

/**
 * Vite queries that turn the target into data rather than executing it:
 * `./Foo.tsx?raw` yields Foo's *source text* as a string and never evaluates
 * Foo. Such a request is an asset edge, not an initialization edge — resolving
 * it to the underlying module would invent a cycle that the build never has.
 * (This file's own `import.meta.glob(..., "?raw")` is exactly that case.)
 */
const NON_EXECUTING_QUERY = /[?&](raw|url|inline)(&|$)/;

/** Resolve a relative specifier to a src-relative source path, or null. */
function resolve(from: string, spec: string): string | null {
  if (NON_EXECUTING_QUERY.test(spec)) return null;
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
  // `./Foo.tsx?raw` names a module file but imports its text, so `resolve`
  // deliberately returns null for it — it must not count as a lost edge.
  if (NON_EXECUTING_QUERY.test(spec)) return false;
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

  it("counts only edges that survive to the emitted JavaScript", () => {
    // Type-only edges are erased at emit and cannot cause an initialization
    // cycle. Counting them would fail a valid change — a section importing
    // only a *type* from ModuleEditor — as a runtime cycle that isn't there.
    expect(specifiers(`import { A } from "./v";`)).toEqual(["./v"]);
    expect(specifiers(`import type { A } from "./t";`)).toEqual([]);
    expect(specifiers(`import { type A, type B } from "./t";`)).toEqual([]);
    expect(specifiers(`import { type A, B } from "./v";`)).toEqual(["./v"]);
    expect(specifiers(`import D, { type A } from "./v";`)).toEqual(["./v"]);
    expect(specifiers(`export type { A } from "./t";`)).toEqual([]);
    expect(specifiers(`export { A } from "./v";`)).toEqual(["./v"]);
    expect(specifiers(`import "./side-effect";`)).toEqual(["./side-effect"]);
    expect(specifiers(`const f = () => import("./dyn");`)).toEqual(["./dyn"]);
    // …and no false positives from comments or ordinary strings.
    expect(specifiers(`// import { A } from "./c";\nconst s = 'from "./s"';`))
      .toEqual([]);
  });

  it("treats non-executing Vite queries as asset edges", () => {
    // `?raw` hands over the file's text; the target module never evaluates,
    // so it cannot be part of an initialization cycle.
    const from = "components/ModuleEditor.tsx";
    expect(resolve(from, "./ModuleSchemaEditor.tsx?raw")).toBeNull();
    expect(resolve(from, "./ModuleSchemaEditor.tsx?url")).toBeNull();
    expect(resolve(from, "./ModuleSchemaEditor.tsx")).toBe(
      "components/ModuleSchemaEditor.tsx");
    // …and the completeness assertion must agree, or a `?raw` import would
    // register as an edge the resolver lost.
    expect(isCodeSpecifier("./ModuleSchemaEditor.tsx?raw")).toBe(false);
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
