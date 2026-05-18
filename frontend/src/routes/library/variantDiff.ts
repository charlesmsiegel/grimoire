/**
 * Helpers for computing a lightweight cross-world variant diff.
 *
 * The MVP renders a per-pair frontmatter diff (key/value comparison) and a
 * body length delta. We deliberately avoid a full text diff for now: the
 * backend already returns full body + frontmatter on each variant, so the
 * comparison runs entirely on the client.
 */

export interface FrontmatterDiffRow {
  key: string;
  a: unknown;
  b: unknown;
  changed: boolean;
}

export interface VariantDiff {
  /** Frontmatter rows where the values differ between A and B. */
  rows: FrontmatterDiffRow[];
  /** length(B.body) - length(A.body). Negative means B is shorter. */
  bodyLengthDelta: number;
  bodyLengthA: number;
  bodyLengthB: number;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function valuesEqual(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (a === null || b === null) return a === b;
  if (Array.isArray(a) && Array.isArray(b)) {
    if (a.length !== b.length) return false;
    return a.every((item, i) => valuesEqual(item, b[i]));
  }
  if (isObject(a) && isObject(b)) {
    const ka = Object.keys(a);
    const kb = Object.keys(b);
    if (ka.length !== kb.length) return false;
    return ka.every((k) => valuesEqual(a[k], b[k]));
  }
  return false;
}

export function diffFrontmatter(
  a: Record<string, unknown>,
  b: Record<string, unknown>,
): FrontmatterDiffRow[] {
  const keys = new Set<string>([...Object.keys(a), ...Object.keys(b)]);
  const rows: FrontmatterDiffRow[] = [];
  for (const key of Array.from(keys).sort()) {
    const av = a[key];
    const bv = b[key];
    if (!valuesEqual(av, bv)) {
      rows.push({ key, a: av, b: bv, changed: true });
    }
  }
  return rows;
}

export function diffVariants(
  a: { frontmatter: Record<string, unknown>; body: string },
  b: { frontmatter: Record<string, unknown>; body: string },
): VariantDiff {
  const rows = diffFrontmatter(a.frontmatter || {}, b.frontmatter || {});
  const bodyLengthA = (a.body || "").length;
  const bodyLengthB = (b.body || "").length;
  return {
    rows,
    bodyLengthDelta: bodyLengthB - bodyLengthA,
    bodyLengthA,
    bodyLengthB,
  };
}

export function formatValue(value: unknown): string {
  if (value === undefined) return "(missing)";
  if (value === null) return "null";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}
