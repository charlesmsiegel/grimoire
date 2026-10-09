import type { SamplerParamSpec, SamplerPreset } from "../../api/client";

/** A stop string as the form shows it: control characters spelled as escapes,
 *  so `\nYou:` — the most common stop string there is — survives a one-per-line
 *  textarea. Backslash first, or the escapes it introduces would be doubled. */
export function encodeStop(s: string): string {
  return s.replace(/\\/g, "\\\\").replace(/\n/g, "\\n").replace(/\t/g, "\\t");
}

export function decodeStop(s: string): string {
  return s.replace(/\\(\\|n|t)/g, (_m, c: string) => (c === "n" ? "\n" : c === "t" ? "\t" : "\\"));
}

/** The form's state: every number as the string typed, so an empty box stays
 *  empty (unset) instead of becoming a 0 nobody meant. */
export type Draft = { name: string; notes: string; values: Record<string, string> };

export const BLANK: Draft = { name: "", notes: "", values: {} };

export function toDraft(p: SamplerPreset): Draft {
  const values: Record<string, string> = {};
  for (const [k, v] of Object.entries(p.params)) {
    values[k] = Array.isArray(v) ? v.map(encodeStop).join("\n") : String(v);
  }
  return { name: p.name, notes: p.notes, values };
}

/** The draft as the params the server stores. Blank means unset. A number that
 *  is not one is sent as typed, so the server's 400 names the parameter; a
 *  choice is sent as chosen. */
export function toParams(d: Draft, table: SamplerParamSpec[]): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const spec of table) {
    const typed = (d.values[spec.name] ?? "").trim();
    if (!typed) continue;
    if (spec.kind === "choice") {
      out[spec.name] = typed;
    } else if (spec.kind === "stop") {
      const lines = (d.values[spec.name] ?? "").split("\n").filter((l) => l !== "");
      if (lines.length) out[spec.name] = lines.map(decodeStop);
    } else {
      const n = Number(typed);
      out[spec.name] = Number.isFinite(n) ? n : typed;
    }
  }
  return out;
}

export function shown(v: unknown): string {
  return Array.isArray(v) ? v.map((s) => JSON.stringify(s)).join(", ") : String(v);
}
