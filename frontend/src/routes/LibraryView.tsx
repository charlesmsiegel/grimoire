import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { LIBRARY_SECTIONS } from "../librarySections";

function countLabel(n: number | null | undefined, unit: string): string {
  // Undefined is "still loading", null is "that request failed" — both are
  // genuinely unknown, and a dash says so where a 0 would claim the library
  // is empty.
  if (n === null || n === undefined) return "—";
  return `${n} ${unit}${n === 1 ? "" : "s"}`;
}

export default function LibraryView() {
  const [counts, setCounts] = useState<Record<string, number | null>>({});

  // Deliberately one request per section rather than a new aggregate endpoint:
  // every one of these list routes already exists and is already cheap, and
  // they settle independently, so a library whose request fails costs only its
  // own count instead of blanking the whole page.
  useEffect(() => {
    let live = true;
    for (const s of LIBRARY_SECTIONS) {
      s.count()
        .then((n) => { if (live) setCounts((c) => ({ ...c, [s.to]: n })); })
        .catch(() => { if (live) setCounts((c) => ({ ...c, [s.to]: null })); });
    }
    return () => { live = false; };
  }, []);

  return (
    <div className="page view-anim">
      <div className="page-head">
        <h1 className="page-h1">Library</h1>
      </div>
      <div className="count-label">Everything a campaign is built from</div>
      <div className="library-grid">
        {LIBRARY_SECTIONS.map((s) => (
          <Link key={s.to} to={s.to} className="library-card">
            <h3>{s.label}</h3>
            <p className="library-blurb">{s.blurb}</p>
            <footer data-testid={`count-${s.to.slice(1)}`}>
              {countLabel(counts[s.to], s.unit)}
            </footer>
          </Link>
        ))}
      </div>
    </div>
  );
}
