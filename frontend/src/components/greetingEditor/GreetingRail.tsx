import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import type { CharacterSummary, EntityScope, Greeting, GreetingMark } from "../../api/client";

// Marks are campaign-only (a world has no play history), so the rail's mark
// chips are too; search works in both scopes.
const MARKS: Exclude<GreetingMark, null>[] = ["played", "completed", "skipped"];
const MARK_LABEL: Record<string, string> = { played: "played", completed: "done", skipped: "skip" };

// NFC on both sides before folding case. Names arrive from hand-written
// markdown and imported cards, so an accented one can be stored decomposed
// (e + combining acute) while the reader types it composed; the two render
// identically and would otherwise never match (Codex review). This normalizes
// form, not accents: "cafe" still does not find "café", which keeps the match
// rule something a reader can predict.
const fold = (s: string) => s.normalize("NFC").toLowerCase();

/** The greeting browse rail owns its filters. Scope changes reset them while
 * retaining the search input and its keyboard focus. */
export function GreetingRail({ scope, scopeKey, greetings, chars, gid, sectionPath, recordHref,
                               untaggedCount, listsReady, onTagImages }: {
  scope: EntityScope; scopeKey: string; greetings: Greeting[]; chars: CharacterSummary[];
  gid: string | null; sectionPath: string; recordHref: (id: string) => string;
  untaggedCount: number; listsReady: boolean; onTagImages: () => void;
}) {
  const worldScope = scope.kind === "world";
  const charById = useMemo(() => new Map(chars.map((c) => [c.id, c])), [chars]);
  const charName = useCallback((id: string) => charById.get(id)?.name ?? id, [charById]);
  // Rail filters. `query` matches names; `hiddenMarks` holds the marks the
  // reader has switched OFF, so the default (an empty set) hides nothing -- a
  // list that silently starts short is worse than one that starts long.
  const [query, setQuery] = useState("");
  const [hiddenMarks, setHiddenMarks] = useState<Set<Exclude<GreetingMark, null>>>(new Set());
  useEffect(() => {
    setQuery("");
    setHiddenMarks(new Set());
  }, [scopeKey]);

  // --- rail filtering -------------------------------------------------------
  const markCounts = useMemo(
    () => MARKS.map((m) => [m, greetings.filter((g) => g.mark === m).length] as const),
    [greetings]);

  function toggleMark(m: Exclude<GreetingMark, null>) {
    setHiddenMarks((prev) => {
      const next = new Set(prev);
      if (next.has(m)) next.delete(m); else next.add(m);
      return next;
    });
  }

  const shownGreetings = useMemo(() => {
    // Name, source character, and every present character -- all of which the
    // list payload already carries, so this costs no request. Bodies are NOT
    // searched: they only exist on the per-greeting read.
    const needle = fold(query.trim());
    const matchesQuery = (g: Greeting) => !needle
      || [g.name, charName(g.character), ...(g.present ?? []).map(charName)]
        .some((s) => fold(s).includes(needle));
    return greetings.filter((g) => {
      // The open greeting always stays listed. Its content is on screen either
      // way, and dropping its row would leave the body with no visible source --
      // the reader would see a record the list denies having.
      if (g.id === gid) return true;
      if (!worldScope && g.mark && hiddenMarks.has(g.mark)) return false;
      return matchesQuery(g);
    });
  }, [greetings, gid, worldScope, hiddenMarks, query, charName]);
  const hiddenCount = greetings.length - shownGreetings.length;
  const rail = useMemo(() => shownGreetings.map((g) => (
    <Link
      key={g.id}
      className={"row" + (gid === g.id ? " active" : "")}
      to={recordHref(g.id)}
    >
      {g.name}
      {!worldScope && g.mark && (
        <span className={`mark-badge ${g.mark}`}>
          {MARK_LABEL[g.mark]}
        </span>
      )}
    </Link>
  )), [shownGreetings, gid, worldScope, recordHref]);
  return (
    <div className="editor-list">
      <Link className="primary new" to={{ pathname: sectionPath, search: "?new=1" }}>+ New greeting</Link>
      {worldScope && untaggedCount > 0 && (
        <button className="subtle new" onClick={onTagImages}>
          ▶ Tag images ({untaggedCount})
        </button>
      )}
      <input className="rail-search" type="search" value={query} aria-label="Search greetings"
             placeholder="Search name or character…"
             onChange={(e) => setQuery(e.target.value)} />
      {!worldScope && markCounts.some(([, n]) => n > 0) && (
        <div className="rail-filters" role="group" aria-label="Filter by mark">
          {markCounts.filter(([, n]) => n > 0).map(([m, n]) => (
            <button key={m} className={"chip" + (hiddenMarks.has(m) ? "" : " on")}
                    aria-pressed={!hiddenMarks.has(m)}
                    title={hiddenMarks.has(m) ? `Show ${MARK_LABEL[m]}` : `Hide ${MARK_LABEL[m]}`}
                    onClick={() => toggleMark(m)}>
              {MARK_LABEL[m]} {n}
            </button>
          ))}
        </div>
      )}
      {rail}
      {/* One status line, always in the DOM rather than mounted on demand:
          filtering happens while focus is still in the search box, so a
          result count that only appears afterwards is never announced. Live
          regions have to exist before the text changes to be read out. */}
      <div className="field-hint rail-empty" role="status" aria-live="polite">
        {!listsReady
          ? ""                        /* silence, not a verdict, while loading */
          : shownGreetings.length === 0
            ? "No greetings match."
            : hiddenCount > 0 ? `${hiddenCount} hidden` : ""}
      </div>
    </div>
  );
}
