/**
 * World view (spec 14 §World view).
 *
 * Tabbed listing of resolved items / locations / lore / factions / greetings
 * for the campaign. Locations get a hierarchy view by parent; items show
 * current-holder when the resolved frontmatter records one; lore is grouped
 * by keyword.
 */

import { useState } from "react";
import { useParams } from "react-router-dom";

import { viewsApi } from "../../api/views";
import type { Composition, Greeting, ResolvedEntity } from "../../api/types";
import { useApi } from "../../api/useApi";
import { Markdown } from "../../components/Markdown";
import { ChainBadge, Loading, Tabs } from "./common";

type WorldTab = "items" | "locations" | "lore" | "factions" | "greetings";

const TABS: { key: WorldTab; label: string }[] = [
  { key: "items", label: "Items" },
  { key: "locations", label: "Locations" },
  { key: "lore", label: "Lore" },
  { key: "factions", label: "Factions" },
  { key: "greetings", label: "Greetings" },
];

export function WorldView() {
  const { campaignId = "" } = useParams();
  const [tab, setTab] = useState<WorldTab>("items");

  return (
    <section className="route campaign-world" aria-labelledby="world-heading">
      <header className="route-header">
        <h2 id="world-heading">World</h2>
        <Tabs tabs={TABS} active={tab} onSelect={setTab} ariaLabel="World tabs" />
      </header>
      {tab === "items" && <EntityTab campaignId={campaignId} kind="items" />}
      {tab === "locations" && <LocationsTab campaignId={campaignId} />}
      {tab === "lore" && <LoreTab campaignId={campaignId} />}
      {tab === "factions" && <EntityTab campaignId={campaignId} kind="factions" />}
      {tab === "greetings" && <GreetingsTab campaignId={campaignId} />}
    </section>
  );
}

function fetcherFor(campaignId: string, kind: Exclude<WorldTab, "greetings">) {
  switch (kind) {
    case "items":
      return () => viewsApi.listItems(campaignId);
    case "locations":
      return () => viewsApi.listLocations(campaignId);
    case "lore":
      return () => viewsApi.listLore(campaignId);
    case "factions":
      return () => viewsApi.listFactions(campaignId);
  }
}

function EntityTab({
  campaignId,
  kind,
}: {
  campaignId: string;
  kind: Exclude<WorldTab, "greetings" | "locations" | "lore">;
}) {
  const state = useApi(fetcherFor(campaignId, kind), [campaignId, kind]);
  return (
    <Loading state={state} emptyMessage={`No ${kind} resolved for this campaign yet.`}>
      {(rows) => (
        <ul className="entity-grid">
          {rows.map((row) => (
            <EntityCard key={`${row.setting_id ?? "campaign"}:${row.asset_id}`} row={row}>
              {kind === "items" && row.extras && extractHolder(row.extras) && (
                <p className="muted">Holder: {extractHolder(row.extras)}</p>
              )}
            </EntityCard>
          ))}
        </ul>
      )}
    </Loading>
  );
}

function LocationsTab({ campaignId }: { campaignId: string }) {
  const state = useApi(() => viewsApi.listLocations(campaignId), [campaignId]);
  return (
    <Loading state={state} emptyMessage="No locations resolved for this campaign yet.">
      {(rows) => {
        const tree = buildLocationTree(rows);
        return (
          <div className="locations-layout">
            <aside className="location-tree">
              <h3>Hierarchy</h3>
              <LocationTree nodes={tree} />
            </aside>
            <ul className="entity-grid">
              {rows.map((row) => (
                <EntityCard key={`${row.setting_id ?? "campaign"}:${row.asset_id}`} row={row}>
                  {extractParent(row.frontmatter) && (
                    <p className="muted">Parent: {extractParent(row.frontmatter)}</p>
                  )}
                </EntityCard>
              ))}
            </ul>
          </div>
        );
      }}
    </Loading>
  );
}

function LoreTab({ campaignId }: { campaignId: string }) {
  const state = useApi(() => viewsApi.listLore(campaignId), [campaignId]);
  return (
    <Loading state={state} emptyMessage="No lore entries resolved for this campaign yet.">
      {(rows) => {
        const grouped = groupByKeyword(rows);
        return (
          <div className="lore-grid">
            {[...grouped.entries()].map(([kw, entries]) => (
              <section key={kw} className="lore-group">
                <h3>{kw}</h3>
                <ul className="entity-grid">
                  {entries.map((row) => (
                    <EntityCard key={`${row.setting_id ?? "campaign"}:${row.asset_id}`} row={row} />
                  ))}
                </ul>
              </section>
            ))}
          </div>
        );
      }}
    </Loading>
  );
}

function GreetingsTab({ campaignId }: { campaignId: string }) {
  const composition = useApi<Composition>(() => viewsApi.getComposition(campaignId), [campaignId]);

  if (composition.status !== "ok") {
    return (
      <Loading state={composition}>{() => <p className="muted">Loading composition…</p>}</Loading>
    );
  }

  return (
    <GreetingsAcrossSettings settingIds={composition.data.settings.map((s) => s.setting_id)} />
  );
}

function GreetingsAcrossSettings({ settingIds }: { settingIds: string[] }) {
  const state = useApi<Greeting[]>(
    () =>
      Promise.all(
        settingIds.map((id) => viewsApi.listGreetingsForSetting(id).catch(() => [])),
      ).then((lists) => lists.flat()),
    [settingIds.join("|")],
  );
  if (settingIds.length === 0) {
    return <p className="muted">No setting refs in the composition.</p>;
  }
  return (
    <Loading state={state} emptyMessage="No greetings declared in the composed settings.">
      {(rows) => (
        <ul className="entity-grid">
          {rows.map((g) => (
            <li key={`${g.setting_id}:${g.id}`} className="entity-card-static">
              <header>
                <h4>{g.name}</h4>
                <span className="muted">{g.setting_id}</span>
              </header>
              {g.starting_location && <p className="muted">Location: {g.starting_location}</p>}
              {g.starting_time && <p className="muted">Time: {g.starting_time}</p>}
              {g.mood && <p className="muted">Mood: {g.mood}</p>}
              {g.body && (
                <details>
                  <summary>Preview</summary>
                  <Markdown>{g.body}</Markdown>
                </details>
              )}
            </li>
          ))}
        </ul>
      )}
    </Loading>
  );
}

function EntityCard({ row, children }: { row: ResolvedEntity; children?: React.ReactNode }) {
  return (
    <li className="entity-card-static">
      <header>
        <h4>{row.name || row.asset_id}</h4>
        <ChainBadge chain={row.source_chain} overrides={row.overrides_applied} />
      </header>
      {row.setting_id && <p className="muted">setting: {row.setting_id}</p>}
      {children}
      {row.body && (
        <details>
          <summary>Preview</summary>
          <Markdown>{row.body}</Markdown>
        </details>
      )}
    </li>
  );
}

// ---------- helpers ----------

function extractParent(fm: Record<string, unknown>): string | null {
  const v = fm["parent"] ?? fm["parent_id"];
  return typeof v === "string" ? v : null;
}

function extractHolder(extras: Record<string, unknown>): string | null {
  const v = extras["holder"] ?? extras["current_holder"];
  return typeof v === "string" ? v : null;
}

interface LocationNode {
  row: ResolvedEntity;
  children: LocationNode[];
}

function buildLocationTree(rows: ResolvedEntity[]): LocationNode[] {
  const byId = new Map<string, LocationNode>();
  for (const row of rows) byId.set(row.asset_id, { row, children: [] });
  const roots: LocationNode[] = [];
  for (const node of byId.values()) {
    const parent = extractParent(node.row.frontmatter);
    const parentNode = parent ? byId.get(parent) : null;
    if (parentNode) parentNode.children.push(node);
    else roots.push(node);
  }
  return roots;
}

function LocationTree({ nodes }: { nodes: LocationNode[] }) {
  if (nodes.length === 0) return <p className="muted">No locations.</p>;
  return (
    <ul className="tree">
      {nodes.map((n) => (
        <li key={n.row.asset_id}>
          <span>{n.row.name || n.row.asset_id}</span>
          {n.children.length > 0 && <LocationTree nodes={n.children} />}
        </li>
      ))}
    </ul>
  );
}

function groupByKeyword(rows: ResolvedEntity[]): Map<string, ResolvedEntity[]> {
  const groups = new Map<string, ResolvedEntity[]>();
  for (const row of rows) {
    const kw = readKeywords(row.frontmatter);
    const tags = kw.length > 0 ? kw : ["(unkeyed)"];
    for (const t of tags) {
      const list = groups.get(t) ?? [];
      list.push(row);
      groups.set(t, list);
    }
  }
  return groups;
}

function readKeywords(fm: Record<string, unknown>): string[] {
  const raw = fm["keywords"] ?? fm["tags"];
  if (Array.isArray(raw)) return raw.filter((v): v is string => typeof v === "string");
  return [];
}
