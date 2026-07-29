import ts from "typescript";

// The module-editor files once formed a five-file import cycle: ModuleEditor
// imported all four section components, and every section imported the shared
// save/dry-run helpers back out of ModuleEditor. The helpers now live here in
// moduleEditShared, so the arrows run one way.
//
// This guards that by building the real import graph — every source file under
// src/, scanned with the TypeScript AST — and asking, for each
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
const MODULE_EXTS = [".mjs", ".js", ".mts", ".ts", ".jsx", ".tsx", ".cjs", ".cts"];
const GLOB = "../**/*.{mjs,js,mts,ts,jsx,tsx,cjs,cts}";

const RAW = import.meta.glob("../**/*.{mjs,js,mts,ts,jsx,tsx,cjs,cts}", {
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
 * Glob syntax this matcher does not implement. fast-glob (what Vite uses)
 * supports extglobs — `+(a|b)`, `@(a|b)`, `?(a)`, `!(a)`. Rather than match
 * them wrongly and drop the real edges silently, a pattern containing one is
 * reported as unanalyzable so the suite fails.
 */
const UNSUPPORTED_GLOB = /[?*+@!]\(/;

/** A Vite glob pattern as a matcher over src-relative paths. */
function globToRegExp(pattern: string): RegExp {
  let out = "";
  for (let i = 0; i < pattern.length; i++) {
    const c = pattern[i];
    if (c === "*") {
      if (pattern[i + 1] === "*") { out += ".*"; i++; if (pattern[i + 1] === "/") i++; }
      else out += "[^/]*";
    } else if (c === "[") {
      // A character class passes through, with fast-glob's `!` negation
      // rewritten to regex form. `./Module[SR]*Editor.tsx` is a valid pattern.
      const close = pattern.indexOf("]", i + 1);
      if (close < 0) { out += "\\["; continue; }
      const body = pattern.slice(i + 1, close);
      out += "[" + (body.startsWith("!") ? "^" + body.slice(1) : body) + "]";
      i = close;
    } else if (c === "{") out += "(";
    else if (c === "}") out += ")";
    else if (c === ",") out += "|";
    else if (c === "?") out += "[^/]";
    else out += c.replace(/[.+^$()|[\]\\]/g, "\\$&");
  }
  return new RegExp(`^${out}$`);
}

/**
 * Vite glob options that hand over data instead of evaluating the match.
 * Matched as a whole parameter rather than a substring: `{ query: "?draw=1" }`
 * is an ordinary custom query whose modules *are* imported, so reading it as
 * raw would drop every edge the glob creates.
 */
const NON_EXECUTING_MODE = /^(raw|url|inline)$/;

function globIsRaw(opts: ts.Node | undefined): boolean {
  if (!opts || !ts.isObjectLiteralExpression(opts)) return false;
  return opts.properties.some((p) => {
    if (!ts.isPropertyAssignment(p) || !ts.isIdentifier(p.name)) return false;
    if (!ts.isStringLiteralLike(p.initializer)) return false;
    const value = p.initializer.text;
    if (p.name.text === "as") return NON_EXECUTING_MODE.test(value);
    if (p.name.text !== "query") return false;
    // `query` is a query string: "?raw", "raw", or "?foo=1&raw".
    return value.replace(/^\?/, "").split("&")
      .some((param) => NON_EXECUTING_MODE.test(param.split("=")[0]));
  });
}

/** Parse each file as what it actually is: `<Foo>x` is a cast in .ts, JSX in .tsx. */
function scriptKind(path: string): ts.ScriptKind {
  if (path.endsWith(".tsx")) return ts.ScriptKind.TSX;
  if (path.endsWith(".jsx")) return ts.ScriptKind.JSX;
  if (/\.[mc]?js$/.test(path)) return ts.ScriptKind.JS;
  return ts.ScriptKind.TS;
}

/**
 * Names used somewhere other than a type position. TypeScript also elides an
 * *unmarked* import whose bindings are only ever used as types, so
 * `import { Props } from "./x"` with `Props` used solely in type position
 * emits nothing — checking `isTypeOnly` markers alone would keep that edge.
 *
 * This is a syntactic approximation of the checker's elision, and it is
 * deliberately biased: anything ambiguous counts as a value use, which keeps
 * the edge. A spurious edge fails loudly and is diagnosable; a dropped one is
 * a silent hole, which is the failure this whole file exists to prevent.
 */
function valueNames(src: ts.SourceFile): Set<string> {
  const used = new Set<string>();
  const walk = (node: ts.Node, inType: boolean): void => {
    // Import clauses bind names, they don't use them.
    if (ts.isImportDeclaration(node)) return;
    const typeCtx = inType || ts.isTypeNode(node);
    // `class A extends B` is a value use even though the node is a TypeNode.
    const heritageValue = ts.isExpressionWithTypeArguments(node) &&
      ts.isHeritageClause(node.parent) &&
      node.parent.token === ts.SyntaxKind.ExtendsKeyword &&
      ts.isClassLike(node.parent.parent);
    if (!typeCtx || heritageValue) {
      if (ts.isIdentifier(node)) used.add(node.text);
    }
    ts.forEachChild(node, (c) => walk(c, typeCtx && !heritageValue));
  };
  ts.forEachChild(src, (n) => walk(n, false));
  return used;
}

/**
 * Every specifier that survives to the emitted JavaScript, via the TypeScript
 * AST. Static imports, `export … from`, side-effect imports and dynamic
 * `import()` all count; unlike a regex this never mistakes a
 * specifier-shaped comment or string for an import.
 *
 * Type-only edges are excluded deliberately — `import type { X }`, `export
 * type { X } from`, a clause whose every binding is `type X`, and a clause
 * whose bindings are only ever used in type position are all erased at emit,
 * so they cannot participate in an initialization cycle. Counting them would
 * reject a valid change (a section needing only a *type* from ModuleEditor)
 * as a runtime cycle that does not exist.
 */
function specifiers(code: string, path = "f.tsx"): string[] {
  const src = ts.createSourceFile(path, code, ts.ScriptTarget.Latest, true,
                                  scriptKind(path));
  const found: string[] = [];
  // `import("./x")` and ``import(`./x`)`` are both valid and both emit.
  const literal = (n: ts.Node | undefined) =>
    n && ts.isStringLiteralLike(n) ? n.text : undefined;
  let values: Set<string> | null = null;   // computed lazily; only some files need it
  // Type erasure is a TypeScript compile step. In a .js/.mjs/.jsx file native
  // ESM evaluates the target for its side effects whether or not the binding
  // is used, so eliding an "unused" import there would drop a real edge.
  const kind = scriptKind(path);
  const erasesTypes = kind === ts.ScriptKind.TS || kind === ts.ScriptKind.TSX;

  for (const st of src.statements) {
    if (ts.isImportDeclaration(st)) {
      const clause = st.importClause;
      if (clause?.isTypeOnly) continue;                    // import type { … }
      // A side-effect import (`import "./x"`) has no clause and always emits.
      if (clause && erasesTypes) {
        const named = clause.namedBindings;
        const bindings = named && ts.isNamedImports(named) ? named.elements : [];
        // Explicitly-marked: `{ type A, type B }` with no default/namespace.
        if (!clause.name && bindings.length > 0 &&
            bindings.every((e) => e.isTypeOnly)) continue;
        // Usage-elided: every binding is only ever referenced as a type. This
        // covers default, named and namespace forms — `import * as E from "./x"`
        // used only as `E.Props` is erased just like the others.
        values ??= valueNames(src);
        const names = [
          clause.name,
          ...(named && ts.isNamespaceImport(named) ? [named.name] : []),
          ...bindings.map((e) => e.name),
        ].filter((n): n is ts.Identifier => !!n);
        if (names.length > 0 && !names.some((n) => values!.has(n.text))) continue;
      }
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

  // Dynamic import() can appear anywhere and is always a runtime edge — as is
  // `require()` and `import x = require()`, which the glob admits because it
  // covers .cjs/.cts. A template *expression* specifier is handled by
  // `patternEdges` below; anything else non-literal is unanalyzable and is
  // reported by OPAQUE_IMPORTS rather than dropped.
  const walk = (node: ts.Node): void => {
    if (ts.isCallExpression(node) &&
        (node.expression.kind === ts.SyntaxKind.ImportKeyword ||
         (ts.isIdentifier(node.expression) && node.expression.text === "require"))) {
      const spec = literal(node.arguments[0]);
      if (spec) found.push(spec);
    } else if (ts.isImportEqualsDeclaration(node) && !node.isTypeOnly &&
               ts.isExternalModuleReference(node.moduleReference)) {
      // `import type E = require("./x")` is erased like the other type-only
      // forms, so counting it would report a cycle the runtime does not have.
      const spec = literal(node.moduleReference.expression);
      if (spec) found.push(spec);
    }
    ts.forEachChild(node, walk);
  };
  ts.forEachChild(src, walk);
  return found;
}

/** Executable module requests whose specifier is a node we can inspect. */
function dynamicArgs(code: string, path: string): ts.Expression[] {
  const src = ts.createSourceFile(path, code, ts.ScriptTarget.Latest, true,
                                  scriptKind(path));
  const args: ts.Expression[] = [];
  const walk = (node: ts.Node): void => {
    if (ts.isCallExpression(node) &&
        (node.expression.kind === ts.SyntaxKind.ImportKeyword ||
         (ts.isIdentifier(node.expression) && node.expression.text === "require")) &&
        node.arguments[0]) {
      args.push(node.arguments[0]);
    }
    ts.forEachChild(node, walk);
  };
  ts.forEachChild(src, walk);
  return args;
}

/**
 * A template-expression specifier as a glob. Vite's dynamic-import-vars
 * rewrites ``import(`./Module${kind}Editor.tsx`)`` into a glob whose variable
 * part cannot cross a path separator, so `${…}` becomes a single-segment
 * wildcard. Expanding it keeps those edges in the graph; the alternative is
 * the silent hole of a request that reaches neither the graph nor
 * `UNRESOLVED`.
 */
function templatePattern(node: ts.Expression): string | null {
  if (!ts.isTemplateExpression(node)) return null;
  let out = node.head.text;
  for (const span of node.templateSpans) out += "*" + span.literal.text;
  return out;
}

/** `import.meta.glob(...)` calls in a file, as {patterns, raw} records. */
function globCalls(code: string, path: string) {
  const src = ts.createSourceFile(path, code, ts.ScriptTarget.Latest, true,
                                  scriptKind(path));
  const calls: { patterns: string[]; raw: boolean; analyzable: boolean }[] = [];
  const walk = (node: ts.Node): void => {
    // `new.target` is a MetaProperty too, so a class with a static `glob`
    // method would otherwise read as a Vite glob and invent an edge.
    const meta = ts.isCallExpression(node) &&
      ts.isPropertyAccessExpression(node.expression) &&
      node.expression.name.text === "glob" &&
      ts.isMetaProperty(node.expression.expression)
        ? node.expression.expression : null;
    if (ts.isCallExpression(node) && meta &&
        meta.keywordToken === ts.SyntaxKind.ImportKeyword &&
        meta.name.text === "meta") {
      const arg = node.arguments[0];
      const list = !arg ? []
        : ts.isStringLiteralLike(arg) ? [arg.text]
        : ts.isArrayLiteralExpression(arg)
          ? arg.elements.filter(ts.isStringLiteralLike).map((e) => e.text)
          : [];
      const literalArg = !!arg && (ts.isStringLiteralLike(arg) ||
        (ts.isArrayLiteralExpression(arg) &&
         arg.elements.every((e) => ts.isStringLiteralLike(e))));
      calls.push({
        patterns: list,
        raw: globIsRaw(node.arguments[1]),
        analyzable: literalArg && !list.some((g) => UNSUPPORTED_GLOB.test(g)),
      });
    }
    ts.forEachChild(node, walk);
  };
  ts.forEachChild(src, walk);
  return calls;
}

/**
 * Source files a `import.meta.glob` pulls in as *modules*. Vite rewrites an
 * eager glob into static imports and a lazy one into dynamic imports, so its
 * matches are real initialization edges — invisible to `specifiers()`, which
 * sees a method call rather than an import, and invisible to `UNRESOLVED`,
 * which never receives a specifier for them. A `?raw`/`?url` glob is excluded
 * for the same reason the equivalent static import is: it yields data, not a
 * module. (This file's own glob is that case.)
 */
/** Expand one pattern set against SOURCES, honouring Vite's `!` exclusions. */
function expandPatterns(patterns: string[], from: string): string[] {
  const toRe = (p: string) => {
    const bare = p.split("?")[0];
    // A glob pattern can be root-absolute too. Always prefixing dirOf(from)
    // would build `components/src/components/…`, matching nothing — and
    // because the pattern is still a literal it would not trip OPAQUE_GLOBS.
    const base = ROOT_ABSOLUTE.test(bare)
      ? normalize(bare.replace(ROOT_ABSOLUTE, ""))
      : normalize(dirOf(from) + "/" + bare);
    return base === null ? null : globToRegExp(base);
  };
  const include = patterns.filter((p) => !p.startsWith("!"))
    .map(toRe).filter((r): r is RegExp => !!r);
  // Vite subtracts `!`-prefixed patterns from the matches. Treating them as
  // ordinary patterns that simply match nothing would keep a file the build
  // excludes, inventing a cycle the generated imports do not contain.
  const exclude = patterns.filter((p) => p.startsWith("!"))
    .map((p) => toRe(p.slice(1))).filter((r): r is RegExp => !!r);
  const out: string[] = [];
  for (const candidate of SOURCES.keys()) {
    if (candidate === from) continue;                 // a glob never self-matches
    if (!include.some((r) => r.test(candidate))) continue;
    if (exclude.some((r) => r.test(candidate))) continue;
    out.push(candidate);
  }
  return out;
}

function globEdges(code: string, path: string): string[] {
  const out: string[] = [];
  for (const call of globCalls(code, path)) {
    if (call.raw) continue;
    out.push(...expandPatterns(call.patterns, path));
  }
  // ``import(`./Module${kind}Editor.tsx`)`` is a module request too.
  for (const arg of dynamicArgs(code, path)) {
    const pattern = templatePattern(arg);
    if (pattern && !NON_EXECUTING_QUERY.test(pattern)) {
      out.push(...expandPatterns([pattern], path));
    }
  }
  return out;
}

/** Globs whose pattern isn't a literal, so their edges can't be expanded. */
const OPAQUE_GLOBS = [...SOURCES].flatMap(([path, code]) =>
  globCalls(code, path)
    .filter((c) => !c.raw && !c.analyzable)
    .map(() => path));

/**
 * Dynamic `import()`/`require()` whose specifier is neither a literal nor a
 * template we can turn into a glob — `import(someVariable)`. Its edges cannot
 * be recovered, so it fails the suite instead of vanishing.
 */
const OPAQUE_IMPORTS = [...SOURCES].flatMap(([path, code]) =>
  dynamicArgs(code, path)
    .filter((a) => !ts.isStringLiteralLike(a) && templatePattern(a) === null)
    .map(() => path));

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

/**
 * Vite also resolves root-absolute specifiers against the project root, so
 * `/src/components/ModuleEditor.tsx` is the same module as a relative import
 * of it. Dropping those for "not starting with a dot" let such an import close
 * a cycle while missing from both the graph and the unresolved-edge check.
 */
const ROOT_ABSOLUTE = /^\/src\//;

/** A specifier that names a file in this project rather than a package. */
const isLocal = (spec: string) =>
  spec.startsWith(".") || ROOT_ABSOLUTE.test(spec);

/** Resolve a local specifier to a src-relative source path, or null. */
function resolve(from: string, spec: string): string | null {
  if (NON_EXECUTING_QUERY.test(spec)) return null;
  const bare = spec.split("?")[0];
  const base = ROOT_ABSOLUTE.test(bare)
    ? normalize(bare.replace(ROOT_ABSOLUTE, ""))
    : normalize(dirOf(from) + "/" + bare);
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

/** path -> the source files it imports. */
const GRAPH = new Map(
  [...SOURCES].map(([path, code]) => [
    path,
    [...specifiers(code, path).filter(isLocal)
        .map((spec) => resolve(path, spec))
        .filter((dep): dep is string => dep !== null),
     ...globEdges(code, path)],
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
  specifiers(code, path).filter(isLocal)
    .filter((spec) => resolve(path, spec) === null)
    .map((spec) => `${path} -> ${spec}`));

// Every section file plus the shared module. Deliberately narrower than
// `Module\w*` so an unrelated future `ModulePicker.tsx` isn't dragged in,
// but open enough that a new section editor is covered automatically.
// Any admitted extension, not just .tsx: a future `ModuleDataEditor.ts` must
// be a cycle root in its own right. Walking only the existing roots would miss
// a cycle between a new editor and its own helper — one that never leads back
// to ModuleEditor, and so is never reached from any current entry point.
const MODULE_EDITOR = new RegExp(
  `^components/(Module\\w*Editor|moduleEditShared)(${
    MODULE_EXTS.map((e) => e.replace(".", "\\.")).join("|")})$`);
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
    // Each snippet must actually *use* what it imports: an unused import is
    // itself elided, so a snippet that only declares one would pass for the
    // wrong reason.
    expect(specifiers(`import { A } from "./v"; A();`)).toEqual(["./v"]);
    expect(specifiers(`import type { A } from "./t"; let x: A;`)).toEqual([]);
    expect(specifiers(`import { type A, type B } from "./t"; let x: A; let y: B;`))
      .toEqual([]);
    expect(specifiers(`import { type A, B } from "./v"; let x: A; B();`))
      .toEqual(["./v"]);
    expect(specifiers(`import D, { type A } from "./v"; D(); let x: A;`))
      .toEqual(["./v"]);
    expect(specifiers(`export type { A } from "./t";`)).toEqual([]);
    expect(specifiers(`export { A } from "./v";`)).toEqual(["./v"]);
    expect(specifiers(`import "./side-effect";`)).toEqual(["./side-effect"]);
    expect(specifiers(`const f = () => import("./dyn");`)).toEqual(["./dyn"]);
    // A template-literal specifier emits exactly like a quoted one.
    expect(specifiers("const f = () => import(`./tmpl`);")).toEqual(["./tmpl"]);
    // Unmarked, but used only as a type — TypeScript erases the whole import.
    expect(specifiers(`import { P } from "./t"; let x: P;`)).toEqual([]);
    expect(specifiers(`import { P } from "./v"; let x: P; P();`)).toEqual(["./v"]);
    // …and no false positives from comments or ordinary strings.
    expect(specifiers(`// import { A } from "./c";\nconst s = 'from "./s"';`))
      .toEqual([]);
  });

  it("does not elide unused imports in native-ESM files", () => {
    // Type erasure is a TypeScript step. A .js/.mjs module is evaluated for
    // its side effects whether or not the binding is used, so eliding here
    // would drop a real runtime edge — and `UNRESOLVED` could not catch it,
    // because no specifier ever reaches the resolver.
    const unused = `import { A } from "./ModuleEditor";`;
    expect(specifiers(unused, "api/thing.js")).toEqual(["./ModuleEditor"]);
    expect(specifiers(unused, "api/thing.mjs")).toEqual(["./ModuleEditor"]);
    expect(specifiers(unused, "api/thing.jsx")).toEqual(["./ModuleEditor"]);
    expect(specifiers(unused, "api/thing.ts")).toEqual([]);   // TS erases it
  });

  it("applies type-use elision to namespace imports", () => {
    expect(specifiers(`import * as E from "./t"; let p: E.Props;`)).toEqual([]);
    expect(specifiers(`import * as E from "./v"; E.go();`)).toEqual(["./v"]);
  });

  it("follows Vite's extension resolution order", () => {
    // Vite's default resolve.extensions probes .mjs/.js before .mts/.ts, so an
    // extensionless specifier with both a .js and a .ts sibling loads the .js.
    // Ordering MODULE_EXTS differently would silently graph the wrong file.
    expect(MODULE_EXTS.slice(0, 6))
      .toEqual([".mjs", ".js", ".mts", ".ts", ".jsx", ".tsx"]);
  });

  it("counts modules pulled in by import.meta.glob", () => {
    // Vite rewrites an eager glob into static imports, so its matches are real
    // initialization edges — invisible to specifiers(), which sees a method
    // call, and to UNRESOLVED, which never gets a specifier for them.
    const eager =
      `const m = import.meta.glob("./Module*Editor.tsx", { eager: true });`;
    expect(globEdges(eager, "components/X.tsx")).toEqual(
      expect.arrayContaining([
        "components/ModuleEditor.tsx", "components/ModuleSchemaEditor.tsx",
      ]));
    // A ?raw glob hands over text and never evaluates the match — same rule as
    // a `?raw` static import. This file's own glob is that case.
    const raw =
      `const m = import.meta.glob("./Module*Editor.tsx", { query: "?raw" });`;
    expect(globEdges(raw, "components/X.tsx")).toEqual([]);
    // A glob never matches the file it sits in.
    expect(globEdges(eager, "components/ModuleEditor.tsx"))
      .not.toContain("components/ModuleEditor.tsx");
  });

  it("has no glob or dynamic import it cannot expand", () => {
    // A computed pattern can't be matched against SOURCES, so its edges would
    // vanish silently. Fail loudly instead of guessing.
    expect(OPAQUE_GLOBS).toEqual([]);
    expect(OPAQUE_IMPORTS).toEqual([]);
  });

  it("counts CommonJS requires and import-equals", () => {
    // The glob admits .cjs/.cts, so a transitive dependency can reach an
    // editor through `require()` — a runtime edge invisible to an
    // import-keyword-only walk, and to UNRESOLVED, which gets no specifier.
    expect(specifiers(`const m = require("./ModuleEditor");`, "api/a.cjs"))
      .toEqual(["./ModuleEditor"]);
    expect(specifiers(`import m = require("./ModuleEditor");`, "api/a.ts"))
      .toEqual(["./ModuleEditor"]);
  });

  it("expands template-expression dynamic imports", () => {
    // Vite's dynamic-import-vars rewrites this into a glob whose variable part
    // cannot cross a path separator. Without expansion the request reaches
    // neither the graph nor UNRESOLVED.
    const code = "const f = (k) => import(`./Module${k}Editor.tsx`);";
    expect(globEdges(code, "components/X.tsx")).toEqual(
      expect.arrayContaining([
        "components/ModuleSchemaEditor.tsx", "components/ModuleRulesEditor.tsx",
      ]));
    // A specifier that is neither literal nor template can't be expanded at
    // all, so it must surface rather than disappear.
    expect(dynamicArgs("const f = (p) => import(p);", "components/X.tsx")
      .filter((a) => !ts.isStringLiteralLike(a) && templatePattern(a) === null))
      .toHaveLength(1);
  });

  it("skips type-only import-equals", () => {
    expect(specifiers(`import E = require("./ModuleEditor"); E.go();`, "a/b.ts"))
      .toEqual(["./ModuleEditor"]);
    expect(specifiers(`import type E = require("./ModuleEditor"); let x: E.T;`,
                      "a/b.ts")).toEqual([]);
  });

  it("resolves root-absolute glob patterns from the project root", () => {
    // Prefixing dirOf(from) would build `components/src/components/…`, which
    // matches nothing — and the pattern is still a literal, so OPAQUE_GLOBS
    // would not catch the loss either.
    const code = 'const m = import.meta.glob("/src/components/Module*Editor.tsx",' +
      ' { eager: true });';
    expect(globEdges(code, "routes/Deep.tsx")).toEqual(
      expect.arrayContaining(["components/ModuleEditor.tsx"]));
  });

  it("matches glob character classes", () => {
    const code = 'const m = import.meta.glob("./Module[SR]*Editor.tsx",' +
      ' { eager: true });';
    const edges = globEdges(code, "components/X.tsx");
    expect(edges).toEqual(expect.arrayContaining([
      "components/ModuleSchemaEditor.tsx", "components/ModuleRulesEditor.tsx"]));
    expect(edges).not.toContain("components/ModuleContentEditor.tsx");
    // Extglob syntax is not implemented, so it must be reported rather than
    // matched wrongly — `analyzable: false` routes it to OPAQUE_GLOBS.
    expect(UNSUPPORTED_GLOB.test("./+(a|b).tsx")).toBe(true);
    expect(UNSUPPORTED_GLOB.test("./Module[SR]*Editor.tsx")).toBe(false);
  });

  it("treats a raw glob query as a whole parameter", () => {
    const raw = 'const m = import.meta.glob("./Module*Editor.tsx", { query: "?raw" });';
    expect(globEdges(raw, "components/X.tsx")).toEqual([]);
    // "?draw=1" merely contains "raw" — Vite still imports these modules.
    const draw = 'const m = import.meta.glob("./Module*Editor.tsx", ' +
      '{ query: "?draw=1", eager: true });';
    expect(globEdges(draw, "components/X.tsx")).toEqual(
      expect.arrayContaining(["components/ModuleEditor.tsx"]));
  });

  it("does not mistake new.target.glob for a Vite glob", () => {
    // `new.target` is a MetaProperty as well, so an unguarded check invents an
    // edge from an ordinary method call.
    const code = 'class C { constructor() { new.target.glob("./ModuleEditor.tsx"); } }';
    expect(globEdges(code, "components/X.tsx")).toEqual([]);
  });

  it("treats every admitted extension as a possible cycle root", () => {
    expect(MODULE_EDITOR.test("components/ModuleDataEditor.ts")).toBe(true);
    expect(MODULE_EDITOR.test("components/ModuleLegacyEditor.jsx")).toBe(true);
    expect(MODULE_EDITOR.test("components/moduleEditShared.tsx")).toBe(true);
    expect(MODULE_EDITOR.test("components/ModulePicker.tsx")).toBe(false);
    expect(MODULE_EDITOR.test("routes/ModulesView.tsx")).toBe(false);
  });

  it("subtracts negated glob patterns", () => {
    // Vite excludes `!`-prefixed patterns. Treating one as a positive pattern
    // that matches nothing keeps a file the build leaves out — a cycle the
    // generated imports do not contain.
    const code = 'const m = import.meta.glob(["./Module*Editor.tsx", ' +
      '"!./ModuleEditor.tsx"], { eager: true });';
    const edges = globEdges(code, "components/X.tsx");
    expect(edges).toContain("components/ModuleSchemaEditor.tsx");
    expect(edges).not.toContain("components/ModuleEditor.tsx");
  });

  it("resolves Vite root-absolute specifiers", () => {
    const from = "components/X.tsx";
    expect(isLocal("/src/components/ModuleEditor.tsx")).toBe(true);
    expect(isLocal("react")).toBe(false);
    expect(resolve(from, "/src/components/ModuleEditor.tsx"))
      .toBe("components/ModuleEditor.tsx");
    expect(resolve(from, "/src/api/client")).toBe("api/client.ts");
  });

  it("parses each file as its own script kind", () => {
    // `<Foo>raw` is a type assertion in .ts but an unterminated JSX tag in
    // .tsx, and TSX error recovery swallows what follows — including a
    // dynamic import back to an editor, which would vanish from the graph
    // without ever showing up as an unresolved edge.
    const code = 'const v = <Foo>raw;\nconst f = () => import("./ModuleEditor");';
    expect(specifiers(code, "api/thing.ts")).toEqual(["./ModuleEditor"]);
    expect(specifiers(code, "api/thing.mts")).toEqual(["./ModuleEditor"]);
    // Same text in a .tsx file really is malformed JSX; the point is that the
    // extension decides, not a hardcoded default.
    expect(scriptKind("a/b.tsx")).toBe(ts.ScriptKind.TSX);
    expect(scriptKind("a/b.ts")).toBe(ts.ScriptKind.TS);
    expect(scriptKind("a/b.mts")).toBe(ts.ScriptKind.TS);
    expect(scriptKind("a/b.mjs")).toBe(ts.ScriptKind.JS);
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
