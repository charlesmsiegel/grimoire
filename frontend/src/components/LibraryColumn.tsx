import { useEffect, useState } from "react";
import { NavLink } from "react-router-dom";
import { LIBRARY_SECTIONS } from "../librarySections";
import { ColumnSection } from "./PageShell";

/** The library's context column: its six sections, each with a live count,
 *  one of them lit.
 *
 *  It replaces the card hub that used to sit in front of these routes. A hub
 *  is a page you pass through — it answers "what is in the library" once and
 *  then costs a click on every visit after that. The same six labels in the
 *  column answer it permanently and switch between them in one click, which is
 *  the dominant action here.
 *
 *  Counts are one request per section, unchanged from the hub: every one of
 *  these list routes already exists and is already cheap, they settle
 *  independently, and a section whose request fails costs only its own number
 *  instead of blanking the column. */
export default function LibraryColumn() {
  const [counts, setCounts] = useState<Record<string, number | null>>({});

  useEffect(() => {
    let live = true;
    for (const s of LIBRARY_SECTIONS) {
      // Started inside a promise so a `count` that throws *synchronously* —
      // an endpoint that has gone away, a stub that never got wired up — is
      // the same "unknown, show a dash" case as one that rejects, rather than
      // an exception out of an effect that takes the column down with it.
      Promise.resolve()
        .then(() => s.count())
        .then((n) => { if (live) setCounts((c) => ({ ...c, [s.to]: n })); })
        .catch(() => { if (live) setCounts((c) => ({ ...c, [s.to]: null })); });
    }
    return () => { live = false; };
  }, []);

  return (
    <ColumnSection label="The Library">
      {LIBRARY_SECTIONS.map((s) => (
        // NavLink is already prefix-aware, which is what a section wants:
        // /worlds/saltmarch is still Worlds.
        <NavLink key={s.to} to={s.to}
                 className={({ isActive }) => "column-row" + (isActive ? " active" : "")}>
          <span className="column-row-label">{s.label}</span>
          {/* Undefined is "still loading", null is "that request failed" —
              both genuinely unknown, and a dash says so where a 0 would claim
              the section is empty. */}
          <span className="column-row-count">
            {counts[s.to] === null || counts[s.to] === undefined ? "—" : counts[s.to]}
          </span>
        </NavLink>
      ))}
    </ColumnSection>
  );
}
