import { useEffect, useRef, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { api, type CampaignMeta } from "../api/client";
import { LIBRARY_SECTIONS, inLibrary } from "../librarySections";
import { onCampaignsChanged } from "../appEvents";

const RECENT_LIMIT = 5;

/** The campaign currently being read, if the route names one. "new" is the
 *  wizard, not a campaign, and matches [^/]+ just as happily. */
function openCampaign(pathname: string): string | null {
  const m = /^\/campaigns\/([^/]+)/.exec(pathname);
  return m && m[1] !== "new" ? m[1] : null;
}

function NavItem({ to, label, glyph, rail, end, alwaysGlyph, className = "nav-link" }: {
  to: string; label: string; glyph: string; rail: boolean;
  end?: boolean; alwaysGlyph?: boolean; className?: string;
}) {
  return (
    <NavLink to={to} end={end} title={label}
             className={({ isActive }) => className + (isActive ? " active" : "")}>
      {/* Rendered, not merely CSS-hidden, only where it is actually shown:
          a glyph left in the DOM for an expanded sub-row would still land in
          the row's text, which is what a copy/paste or a caret-browsing user
          gets. It is decoration either way, so `aria-hidden` keeps it out of
          the accessible name and the visually-hidden label supplies one. */}
      {(rail || alwaysGlyph) && <span className="nav-glyph" aria-hidden>{glyph}</span>}
      <span className={rail ? "sr-only" : "nav-label"}>{label}</span>
    </NavLink>
  );
}

export default function NavSidebar({ rail, onToggleRail }: {
  rail: boolean; onToggleRail: () => void;
}) {
  const { pathname } = useLocation();
  // null is "we don't know yet" — still loading, or the request failed. It is
  // deliberately not [], because "No campaigns yet" is a claim about the
  // library, and a user with forty campaigns and a dead backend must not be
  // told they have none.
  const [campaigns, setCampaigns] = useState<CampaignMeta[] | null>(null);

  // Navigation alone is not enough: `CampaignsView` renames and deletes from
  // `/`, so the pathname never moves and the rail would keep a stale label --
  // or a link to a campaign that no longer exists. `mutations` bumps on every
  // campaign mutation wherever it was issued from.
  const [mutations, setMutations] = useState(0);
  useEffect(() => onCampaignsChanged(() => setMutations((n) => n + 1)), []);

  const openCid = openCampaign(pathname);

  // Which dependency woke this run, not how many times it has run. A refetch
  // caused by a mutation must not join a GET issued before it -- that read
  // predates the change and answers with exactly the list being replaced,
  // and unlike the old effect this one accepts what comes back.
  const seenMutations = useRef(0);

  useEffect(() => {
    const bySignal = mutations !== seenMutations.current;
    seenMutations.current = mutations;
    let live = true;
    api.listCampaigns(bySignal)
      .then((list) => { if (live) setCampaigns(list); })
      // A rail that throws would take the whole shell's render down with it,
      // on every route, for a list that is a convenience.
      .catch(() => { if (live) setCampaigns(null); });
    return () => { live = false; };
  }, [pathname, mutations]);

  // Sorted here rather than trusting the server's order: GET /campaigns sorts
  // by campaign.md's `updated`, which only metadata writes advance, so a
  // campaign played into last night ranks below one renamed months ago.
  // `activity` folds in the newest scene. Copy first -- sort mutates, and this
  // array is state.
  const ranked = [...(campaigns ?? [])]
    .sort((a, b) => (b.activity ?? b.updated).localeCompare(a.activity ?? a.updated));

  // The campaign you are reading IS the most recent one, by definition, and
  // saying so needs no request. Playing advances `activity` server-side on
  // every scene write, but refetching the list per transcript post would be a
  // bad trade: GET /campaigns parses the frontmatter head of every scene of
  // every campaign to count them, and it would run on the machine that is
  // streaming the reply. Deriving the top slot from the route instead is
  // exact, free, and covers the case that actually bites -- deep-linking to an
  // old campaign and finding it missing from Recent while sitting in it. The
  // next navigation refetches and the server's ordering takes over.
  const open = openCid ? ranked.find((c) => c.id === openCid) : undefined;
  const recent = (open ? [open, ...ranked.filter((c) => c.id !== open.id)] : ranked)
    .slice(0, RECENT_LIMIT);

  return (
    <nav className={"nav-sidebar" + (rail ? " rail" : "")} aria-label="Primary">
      <button type="button" className="nav-rail-toggle" onClick={onToggleRail}
              aria-expanded={!rail}
              aria-label={rail ? "Expand navigation" : "Collapse navigation"}>
        <span aria-hidden>{rail ? "»" : "«"}</span>
      </button>

      <NavItem to="/" label="Campaigns" glyph="◆" rail={rail} alwaysGlyph end />
      <NavItem to="/library" label="Library" glyph="▤" rail={rail} alwaysGlyph />
      {inLibrary(pathname) && (
        <div className="nav-sub">
          {LIBRARY_SECTIONS.map((s) => (
            <NavItem key={s.to} to={s.to} label={s.label} glyph={s.label[0]} rail={rail}
                     className="nav-link nav-link-sub" />
          ))}
        </div>
      )}
      <NavItem to="/connections" label="Connections" glyph="◈" rail={rail} alwaysGlyph />

      <h2 className="nav-heading">Recent</h2>
      <div className="nav-recent" data-testid="nav-recent">
        {recent.map((c) => (
          <NavItem key={c.id} to={`/campaigns/${c.id}`} label={c.name}
                   glyph={c.name.slice(0, 1).toUpperCase()} rail={rail}
                   className="nav-link nav-link-recent" />
        ))}
      </div>
      {campaigns?.length === 0 && <p className="nav-empty">No campaigns yet</p>}
    </nav>
  );
}
