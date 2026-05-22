/**
 * World view (spec 14 §World view).
 *
 * Tabbed listing of resolved items / locations / lore / factions / greetings
 * for the campaign. Locations get a hierarchy view by parent; items show
 * current-holder when the resolved frontmatter records one; lore is grouped
 * by keyword.
 */

import { useCallback, useMemo, useState } from "react";
import { useParams } from "react-router-dom";

import { viewsApi } from "../../api/views";
import type { Composition, Greeting, ResolvedEntity } from "../../api/types";
import { useApi } from "../../api/useApi";
import { CardFilters } from "../../components/CardFilters";
import { useCardFilters } from "../../hooks/useCardFilters";
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
  const state = useApi(useCallback(() => fetcherFor(campaignId, kind)(), [campaignId, kind]));
  return (
    <Loading state={state} emptyMessage={`No ${kind} resolved for this campaign yet.`}>
      {(rows) => (
        <FilteredEntityGrid
          rows={rows}
          kind={kind}
          renderExtras={(row) =>
            kind === "items" && row.extras && extractHolder(row.extras) ? (
              <p className="muted">Holder: {extractHolder(row.extras)}</p>
            ) : null
          }
        />
      )}
    </Loading>
  );
}

interface FilteredEntityGridProps {
  rows: ResolvedEntity[];
  kind: string;
  renderExtras?: (row: ResolvedEntity) => React.ReactNode;
}

function FilteredEntityGrid({ rows, kind, renderExtras }: FilteredEntityGridProps) {
  const { filtered, search, setSearch, selectedTags, toggleTag, clearTags, availableTags } =
    useCardFilters(rows, {
      text: (row) => [row.name, row.asset_id, row.world_id, row.body],
      tags: (row) => entityTags(row),
    });
  return (
    <>
      <CardFilters
        search={search}
        onSearch={setSearch}
        availableTags={availableTags}
        selectedTags={selectedTags}
        onToggleTag={toggleTag}
        onClearTags={clearTags}
        searchPlaceholder={`Search ${kind} by name, id, or body…`}
        searchLabel={`Search ${kind}`}
        resultSummary={
          filtered.length === rows.length
            ? `${rows.length} ${kind}`
            : `${filtered.length} of ${rows.length}`
        }
      />
      {filtered.length === 0 ? (
        <p className="muted">No {kind} match the current filters.</p>
      ) : (
        <ul className="entity-grid">
          {filtered.map((row) => (
            <EntityCard key={`${row.world_id ?? "campaign"}:${row.asset_id}`} row={row}>
              {renderExtras?.(row)}
            </EntityCard>
          ))}
        </ul>
      )}
    </>
  );
}

function entityTags(row: ResolvedEntity): string[] {
  const fm = row.frontmatter;
  const raw = fm["tags"] ?? fm["keywords"];
  if (Array.isArray(raw)) return raw.filter((v): v is string => typeof v === "string");
  return [];
}

function LocationsTab({ campaignId }: { campaignId: string }) {
  const state = useApi(useCallback(() => viewsApi.listLocations(campaignId), [campaignId]));
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
            <FilteredEntityGrid
              rows={rows}
              kind="locations"
              renderExtras={(row) =>
                extractParent(row.frontmatter) ? (
                  <p className="muted">Parent: {extractParent(row.frontmatter)}</p>
                ) : null
              }
            />
          </div>
        );
      }}
    </Loading>
  );
}

function LoreTab({ campaignId }: { campaignId: string }) {
  const state = useApi(useCallback(() => viewsApi.listLore(campaignId), [campaignId]));
  return (
    <Loading state={state} emptyMessage="No lore entries resolved for this campaign yet.">
      {(rows) => <FilteredLore rows={rows} />}
    </Loading>
  );
}

function FilteredLore({ rows }: { rows: ResolvedEntity[] }) {
  const { filtered, search, setSearch, selectedTags, toggleTag, clearTags, availableTags } =
    useCardFilters(rows, {
      text: (row) => [row.name, row.asset_id, row.world_id, row.body],
      tags: (row) => readKeywords(row.frontmatter),
    });
  const grouped = groupByKeyword(filtered);
  return (
    <>
      <CardFilters
        search={search}
        onSearch={setSearch}
        availableTags={availableTags}
        selectedTags={selectedTags}
        onToggleTag={toggleTag}
        onClearTags={clearTags}
        searchPlaceholder="Search lore by name, id, or body…"
        searchLabel="Search lore"
        resultSummary={
          filtered.length === rows.length
            ? `${rows.length} lore entr${rows.length === 1 ? "y" : "ies"}`
            : `${filtered.length} of ${rows.length}`
        }
      />
      {filtered.length === 0 ? (
        <p className="muted">No lore entries match the current filters.</p>
      ) : (
        <div className="lore-grid">
          {[...grouped.entries()].map(([kw, entries]) => (
            <section key={kw} className="lore-group">
              <h3>{kw}</h3>
              <ul className="entity-grid">
                {entries.map((row) => (
                  <EntityCard key={`${row.world_id ?? "campaign"}:${row.asset_id}`} row={row} />
                ))}
              </ul>
            </section>
          ))}
        </div>
      )}
    </>
  );
}

function GreetingsTab({ campaignId }: { campaignId: string }) {
  const composition = useApi<Composition>(
    useCallback(() => viewsApi.getComposition(campaignId), [campaignId]),
  );

  if (composition.status !== "ok") {
    return (
      <Loading state={composition}>{() => <p className="muted">Loading composition…</p>}</Loading>
    );
  }

  return (
    <GreetingsAcrossWorlds worldIds={composition.data.worlds.map((s) => s.world_id)} />
  );
}

function GreetingsAcrossWorlds({ worldIds }: { worldIds: string[] }) {
  const idsKey = useMemo(() => worldIds.join("|"), [worldIds]);
  const state = useApi<Greeting[]>(
    useCallback(
      () =>
        Promise.all(
          worldIds.map((id) => viewsApi.listGreetingsForWorld(id).catch(() => [])),
        ).then((lists) => lists.flat()),
      // worldIds identity is unstable; collapse to a string key.
      // eslint-disable-next-line react-hooks/exhaustive-deps
      [idsKey],
    ),
  );
  if (worldIds.length === 0) {
    return <p className="muted">No world refs in the composition.</p>;
  }
  return (
    <Loading state={state} emptyMessage="No greetings declared in the composed worlds.">
      {(rows) => <FilteredGreetings rows={rows} />}
    </Loading>
  );
}

function FilteredGreetings({ rows }: { rows: Greeting[] }) {
  const { filtered, search, setSearch, selectedTags, toggleTag, clearTags, availableTags } =
    useCardFilters(rows, {
      text: (g) => [g.name, g.id, g.world_id, g.body, g.starting_location, g.mood],
      tags: (g) => g.tags ?? [],
    });
  return (
    <>
      <CardFilters
        search={search}
        onSearch={setSearch}
        availableTags={availableTags}
        selectedTags={selectedTags}
        onToggleTag={toggleTag}
        onClearTags={clearTags}
        searchPlaceholder="Search greetings by name, id, or body…"
        searchLabel="Search greetings"
        resultSummary={
          filtered.length === rows.length
            ? `${rows.length} greeting${rows.length === 1 ? "" : "s"}`
            : `${filtered.length} of ${rows.length}`
        }
      />
      {filtered.length === 0 ? (
        <p className="muted">No greetings match the current filters.</p>
      ) : (
        <ul className="entity-grid">
          {filtered.map((g) => (
            <li key={`${g.world_id}:${g.id}`} className="entity-card-static">
              <header>
                <h4>{g.name}</h4>
                <span className="muted">{g.world_id}</span>
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
    </>
  );
}

function EntityCard({ row, children }: { row: ResolvedEntity; children?: React.ReactNode }) {
  return (
    <li className="entity-card-static">
      <header>
        <h4>{row.name || row.asset_id}</h4>
        <ChainBadge chain={row.source_chain} overrides={row.overrides_applied} />
      </header>
      {row.world_id && <p className="muted">world: {row.world_id}</p>}
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
