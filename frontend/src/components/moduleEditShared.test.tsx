// The module-editor files once formed a five-file import cycle: ModuleEditor
// imported all four section components, and every section imported the shared
// save/dry-run helpers back out of ModuleEditor. The helpers now live here in
// moduleEditShared, so the arrows run one way.
//
// This guards that by building the real import graph — every relative import
// in every source file reachable from the module-editor entry points — and
// looking for a cycle, rather than grepping for one import spelling. A new
// section file, a transitive cycle through a third module, or a dynamic
// `import()` back into ModuleEditor all fail this test.

const RAW = import.meta.glob("../**/*.{ts,tsx}", {
  query: "?raw", import: "default", eager: true,
}) as Record<string, string>;

/** Collapse `.`/`..` segments; paths are src-relative, e.g. `components/Field.tsx`. */
function normalize(path: string): string {
  const out: string[] = [];
  for (const seg of path.split("/")) {
    if (seg === "" || seg === ".") continue;
    else if (seg === "..") out.pop();
    else out.push(seg);
  }
  return out.join("/");
}

// import.meta.glob keys are relative to this file (src/components/).
const SOURCES = new Map(
  Object.entries(RAW)
    .map(([key, code]) => [normalize("components/" + key), code] as const)
    .filter(([path]) => !path.includes(".test.")),
);

const dirOf = (path: string) => path.slice(0, path.lastIndexOf("/"));

/** Every `from "…"`, bare `import "…"` and dynamic `import("…")` specifier. */
function specifiers(code: string): string[] {
  const found: string[] = [];
  const patterns = [
    /\bfrom\s*["']([^"']+)["']/g,
    /\bimport\s*["']([^"']+)["']/g,
    /\bimport\s*\(\s*["']([^"']+)["']\s*\)/g,
  ];
  for (const re of patterns) {
    for (const m of code.matchAll(re)) found.push(m[1]);
  }
  return found;
}

/** Resolve a relative specifier to a src-relative source path, or null. */
function resolve(from: string, spec: string): string | null {
  if (!spec.startsWith(".")) return null; // bare package import
  const base = normalize(dirOf(from) + "/" + spec.split("?")[0]);
  for (const candidate of [base, `${base}.ts`, `${base}.tsx`,
                           `${base}/index.ts`, `${base}/index.tsx`]) {
    if (SOURCES.has(candidate)) return candidate;
  }
  return null;
}

const ENTRY_POINTS = [...SOURCES.keys()].filter((p) =>
  /^components\/[Mm]odule\w*\.tsx$/.test(p));

/** Depth-first search for a cycle; returns the offending path, or null. */
function findCycle(): string[] | null {
  const done = new Set<string>();
  const stack: string[] = [];
  const onStack = new Set<string>();

  const visit = (path: string): string[] | null => {
    if (onStack.has(path)) return [...stack.slice(stack.indexOf(path)), path];
    if (done.has(path)) return null;
    stack.push(path);
    onStack.add(path);
    for (const spec of specifiers(SOURCES.get(path)!)) {
      const next = resolve(path, spec);
      if (!next) continue;
      const cycle = visit(next);
      if (cycle) return cycle;
    }
    stack.pop();
    onStack.delete(path);
    done.add(path);
    return null;
  };

  for (const entry of ENTRY_POINTS) {
    const cycle = visit(entry);
    if (cycle) return cycle;
  }
  return null;
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

  it("has no import cycle", () => {
    const cycle = findCycle();
    expect(cycle, cycle ? `cycle: ${cycle.join(" -> ")}` : "").toBeNull();
  });
});
