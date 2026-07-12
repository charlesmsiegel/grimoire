import { useEffect, useState } from "react";
import { api } from "../api/client";
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

type Check = { label: string; ok: boolean; tab: string };

export function WorldOverview({ wid, onNavigate }: { wid: string; onNavigate: (tab: string) => void }) {
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [checks, setChecks] = useState<Check[]>([]);

  useEffect(() => {
    let live = true;
    (async () => {
      const scope = { kind: "world" as const, id: wid };
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
  }, [wid]);

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
          {checks.map((c) => (
            <li key={c.label}>
              <button className={"check-row" + (c.ok ? " ok" : "")} onClick={() => onNavigate(c.tab)}>
                {c.ok ? "✓" : "○"} {c.label}
              </button>
            </li>
          ))}
        </ul>
      </div>
      <WorldMechanics wid={wid} />
    </div>
  );
}
