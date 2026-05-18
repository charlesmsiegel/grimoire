/**
 * Lightweight performance instrumentation for spec 14 §Performance budgets.
 *
 * Usage:
 *   markStart("library:render")
 *   ...
 *   markEnd("library:render")
 *
 * `markEnd` calls `performance.measure` (so the spans show up in the devtools
 * Performance tab) and — when `VITE_PERF_LOG=true` — emits a console.debug
 * line so the manual checklist in `frontend/perf-checklist.md` can sanity-
 * check budgets without hand-typing `performance.now()` snippets.
 *
 * A single name may only be measured once between `markStart` and `markEnd`;
 * calling `markStart` again resets the start timestamp. The module never
 * throws, so it is safe to import in environments where `performance` is
 * missing (e.g. SSR).
 */

const starts = new Map<string, number>();

function perfNow(): number {
  if (typeof performance !== "undefined" && typeof performance.now === "function") {
    return performance.now();
  }
  return Date.now();
}

function loggingEnabled(): boolean {
  try {
    // Vite replaces import.meta.env at build time; guard for non-Vite envs.
    const env =
      typeof import.meta !== "undefined"
        ? (import.meta as { env?: Record<string, string | undefined> }).env
        : undefined;
    return env?.VITE_PERF_LOG === "true";
  } catch {
    return false;
  }
}

export function markStart(name: string): void {
  starts.set(name, perfNow());
  try {
    if (typeof performance !== "undefined" && typeof performance.mark === "function") {
      performance.mark(`${name}:start`);
    }
  } catch {
    // No-op: marks are best-effort.
  }
}

export function markEnd(name: string): number | null {
  const start = starts.get(name);
  if (start === undefined) return null;
  starts.delete(name);
  const end = perfNow();
  const duration = end - start;
  try {
    if (typeof performance !== "undefined" && typeof performance.mark === "function") {
      performance.mark(`${name}:end`);
      performance.measure(name, `${name}:start`, `${name}:end`);
    }
  } catch {
    // No-op: measures are best-effort.
  }
  if (loggingEnabled()) {
    console.debug(`[perf] ${name}: ${duration.toFixed(1)}ms`);
  }
  return duration;
}

/** Test-only: clear in-progress timers between cases. */
export function _resetForTests(): void {
  starts.clear();
}
