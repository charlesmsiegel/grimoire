import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { CalendarConfig } from "./CalendarConfig";
import WorldMechanics from "./WorldMechanics";

const TILES = [
  { key: "characters", label: "Characters", tab: "characters" },
  { key: "pcs", label: "PCs", tab: "pcs" },
  { key: "locations", label: "Locations", tab: "locations" },
  { key: "lore", label: "Lore", tab: "lore" },
  { key: "items", label: "Items", tab: "items" },
  { key: "groups", label: "Groups", tab: "groups" },
  { key: "creatures", label: "Creatures", tab: "creatures" },
  { key: "greetings", label: "Greetings", tab: "greetings" },
] as const;

/** One checklist row. `tab` is the next action — the tab that fixes it — and is
 *  absent for a row whose fix is a section of the Overview itself, which renders
 *  as a statement rather than a button that would click through to where you
 *  already are. */
type Check = { label: string; ok: boolean; tab?: string };

export function WorldOverview({
  wid, onNavigate, worldMid = "", onPickMid = () => {},
}: { wid: string; onNavigate: (tab: string) => void; worldMid?: string; onPickMid?: (mid: string) => void }) {
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [checks, setChecks] = useState<Check[]>([]);
  // Reported by the Calendar section below rather than read here: it fetches
  // the same config anyway, and the rest of this list costs a read of every
  // greeting, which a save on one checkbox must not re-run. `null` is "not
  // reported yet, or that read failed" — genuinely unknown, and an unknown flag
  // is not a chore, so it contributes no row at all.
  const [confirmed, setConfirmed] = useState<boolean | null>(null);
  const scope = useMemo(() => ({ kind: "world" as const, id: wid }), [wid]);

  useEffect(() => {
    let live = true;
    (async () => {
      const [w, greetings, chars, untagged] = await Promise.all([
        api.getWorld(wid), api.listGreetings(scope), api.listCharacters(scope), api.listUntaggedImages(wid),
      ]);
      const details = await Promise.all(greetings.map((g) => api.readGreeting(scope, g.id)));
      if (!live) return;
      const c = w.counts ?? {};
      const hasEdges = details.some((d) => d.edges.leads_to.length > 0 || d.edges.excludes.length > 0);
      const noTagline = chars.filter((ch) => !ch.tagline).length;
      setCounts(c);
      setChecks([
        { label: "Has a player character", ok: (c.pcs ?? 0) > 0, tab: "pcs" },
        { label: "Has a location", ok: (c.locations ?? 0) > 0, tab: "locations" },
        { label: "Has a greeting", ok: greetings.length > 0, tab: "greetings" },
        { label: "Plot map has connections", ok: hasEdges, tab: "greetings" },
        { label: noTagline
            ? `${noTagline} character${noTagline === 1 ? "" : "s"} missing a tagline`
            : "All characters have taglines", ok: noTagline === 0, tab: "characters" },
        { label: untagged.length
            ? `${untagged.length} untagged greeting image${untagged.length === 1 ? "" : "s"}`
            : "All greeting images tagged", ok: untagged.length === 0, tab: "greetings" },
      ]);
    })();
    return () => { live = false; };
  }, [wid, scope]);

  // A different world's answer is not this one's. Without the reset the row
  // would keep showing the last world's flag until the section below reloads.
  useEffect(() => { setConfirmed(null); }, [wid]);

  // First: it is the one item on this list that is a decision rather than a
  // count, and appending it would make the list grow upwards as the slower
  // reads land.
  const rows: Check[] = confirmed === null
    ? checks
    : [{ label: "Calendar confirmed", ok: confirmed }, ...checks];

  return (
    <div className="world-overview">
      <div className="overview-tiles">
        {TILES.map((t) => (
          <button key={t.key} className="overview-tile" onClick={() => onNavigate(t.tab)}>
            <span className="overview-count">{counts[t.key] ?? 0}</span>
            <span className="overview-label">{t.label}</span>
          </button>
        ))}
      </div>
      <div className="side-section">
        <h4>Setup checklist</h4>
        <ul className="overview-checklist">
          {rows.map((c) => (
            <li key={c.label}>
              {c.tab ? (
                <button className={"check-row" + (c.ok ? " ok" : "")} onClick={() => onNavigate(c.tab!)}>
                  {c.ok ? "✓" : "○"} {c.label}
                </button>
              ) : (
                <span className={"check-row static" + (c.ok ? " ok" : "")}>
                  {c.ok ? "✓" : "○"} {c.label}
                </span>
              )}
            </li>
          ))}
        </ul>
      </div>
      {/* The world's two defaults, side by side: what campaigns started from it
          reckon time by, and what they roll dice with. Both are copied into a
          campaign at creation, which is why they live on the world's setup
          screen rather than in any one campaign. */}
      <div className="side-section">
        <h4>Calendar</h4>
        <CalendarConfig scope={scope} onConfig={(c) => setConfirmed(c.confirmed)} />
      </div>
      <WorldMechanics wid={wid} worldMid={worldMid} onPickMid={onPickMid} />
    </div>
  );
}
