/** Frontmatter helpers shared between editor components. */

export type FrontmatterValue =
  | string
  | number
  | boolean
  | null
  | FrontmatterValue[]
  | { [key: string]: FrontmatterValue };

export type Frontmatter = Record<string, FrontmatterValue>;

export function isFrontmatter(v: unknown): v is Frontmatter {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

export function ensureFrontmatter(v: unknown): Frontmatter {
  return isFrontmatter(v) ? (v as Frontmatter) : {};
}
