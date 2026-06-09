/**
 * World view (spec 14 §World view).
 *
 * Tabbed listing of the campaign's resolved world contents, one tab per
 * entity kind in the same order as the library world detail. Characters here
 * are the full composition (the Cast view is the dramatis personae — PCs,
 * emergent characters, and anyone who has appeared in a scene). Locations get
 * a hierarchy view by parent; items show current-holder when the resolved
 * frontmatter records one; lore is grouped by keyword.
 */

import { useCallback, useMemo } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";

import { characterRefFor } from "../../api/campaign";
import { viewsApi } from "../../api/views";
import type { Composition, Greeting, ResolvedCharacter, ResolvedEntity } from "../../api/types";
import { useResource } from "../../api/useResource";
import { CardFilters } from "../../components/CardFilters";
import { CardIconBar } from "../../components/CardIconBar";
import { useCardFilters } from "../../hooks/useCardFilters";
import { Markdown } from "../../components/Markdown";
import { AsyncSection } from "../../components/AsyncSection";
import { ChainBadge, Tabs } from "./common";

type WorldTab =
  | "characters"
  | "monsters"
  | "items"
  | "locations"
  | "lore"
  | "factions"
  | "greetings";

const TABS: { key: WorldTab; label: string }[] = [
  { key: "characters", label: "Characters" },
  { key: "monsters", label: "Monsters" },
  { key: "items", label: "Items" },
  { key: "locations", label: "Locations" },
  { key: "lore", label: "Lore" },
  { key: "factions", label: "Factions" },
  { key: "greetings", label: "Greetings" },
];

export function WorldView() {
  const { campaignId = "" } = useParams();
  // Tab selection lives in the URL (?tab=…) so world tabs are deep-linkable
  // and survive refresh, matching the library's URL-per-kind routes.
  const [searchParams, setSearchParams] = useSearchParams();
  const rawTab = searchParams.get("tab");
  const tab: WorldTab = TABS.some((t) => t.key === rawTab) ? (rawTab as WorldTab) : "characters";
  const selectTab = (key: WorldTab) => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.set("tab", key);
      return next;
    });
  };

  return (
    <section className="route campaign-world" aria-labelledby="world-heading">
      <header className="route-header">
        <h2 id="world-heading">World</h2>
        <Tabs tabs={TABS} active={tab} onSelect={selectTab} ariaLabel="World tabs" />
      </header>
      {tab === "characters" && <CharactersTab campaignId={campaignId} />}
      {tab === "monsters" && <EntityTab campaignId={campaignId} kind="monsters" />}
      {tab === "items" && <EntityTab campaignId={campaignId} kind="items" />}
      {tab === "locations" && <LocationsTab campaignId={campaignId} />}
      {tab === "lore" && <LoreTab campaignId={campaignId} />}
      {tab === "factions" && <EntityTab campaignId={campaignId} kind="factions" />}
      {tab === "greetings" && <GreetingsTab campaignId={campaignId} />}
    </section>
  );
}

function fetcherFor(campaignId: string, kind: Exclude<WorldTab, "greetings" | "characters">) {
  switch (kind) {
    case "monsters":
      return () => viewsApi.listMonsters(campaignId);
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
  kind: Exclude<WorldTab, "greetings" | "locations" | "lore" | "characters">;
}) {
  const state = useResource(useCallback(() => fetcherFor(campaignId, kind)(), [campaignId, kind]));
  return (
    <AsyncSection state={state} emptyMessage={`No ${kind} resolved for this campaign yet.`}>
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
    </AsyncSection>
  );
}

function CharactersTab({ campaignId }: { campaignId: string }) {
  const state = useResource(useCallback(() => viewsApi.listCharacters(campaignId), [campaignId]));
  const cast = useResource(useCallback(() => viewsApi.listCast(campaignId), [campaignId]));
  // Key membership by canonical ref, not bare id — cross-world variants
  // share asset ids (#517).
  const castRefs = new Set(cast.data?.map((r) => characterRefFor(r.character)) ?? []);
  return (
    <AsyncSection state={state} emptyMessage="No characters resolved for this campaign yet.">
      {(rows) => <FilteredCharacters rows={rows} campaignId={campaignId} castRefs={castRefs} />}
    </AsyncSection>
  );
}

interface FilteredCharactersProps {
  rows: ResolvedCharacter[];
  campaignId: string;
  castRefs: Set<string>;
}

function FilteredCharacters({ rows, campaignId, castRefs }: FilteredCharactersProps) {
  const { filtered, search, setSearch, selectedTags, toggleTag, clearTags, availableTags } =
    useCardFilters(rows, {
      text: (r) => [
        r.character.name,
        r.character.id,
        r.character.world_id,
        r.character.description,
        r.character.body,
      ],
      tags: (r) => r.character.tags,
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
        searchPlaceholder="Search characters by name, id, or text…"
        searchLabel="Search characters"
        resultSummary={
          filtered.length === rows.length
            ? `${rows.length} character${rows.length === 1 ? "" : "s"}`
            : `${filtered.length} of ${rows.length}`
        }
      />
      {filtered.length === 0 ? (
        <p className="muted">No characters match the current filters.</p>
      ) : (
        <ul className="grid-cards entity-grid">
          {filtered.map((r) => {
            const c = r.character;
            return (
              <li key={`${c.world_id ?? "campaign"}:${c.id}`} className="card entity-card-static">
                <header>
                  <h4>{c.name || c.id}</h4>
                  <ChainBadge chain={r.source_chain} overrides={r.overrides_applied} />
                </header>
                <p className="muted">
                  {c.role}
                  {c.world_id && ` · ${c.world_id}`}
                </p>
                {c.description && <p>{c.description}</p>}
                {c.body && (
                  <details>
                    <summary>Preview</summary>
                    <Markdown>{c.body}</Markdown>
                  </details>
                )}
                {castRefs.has(characterRefFor(c)) && (
                  <p>
                    <Link
                      to={`/campaigns/${encodeURIComponent(campaignId)}/cast?ref=${encodeURIComponent(characterRefFor(c))}`}
                    >
                      In cast — open
                    </Link>
                  </p>
                )}
                <CardIconBar actions={[]} />
              </li>
            );
          })}
        </ul>
      )}
    </>
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
        <ul className="grid-cards entity-grid">
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
  const state = useResource(useCallback(() => viewsApi.listLocations(campaignId), [campaignId]));
  return (
    <AsyncSection state={state} emptyMessage="No locations resolved for this campaign yet.">
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
    </AsyncSection>
  );
}

function LoreTab({ campaignId }: { campaignId: string }) {
  const state = useResource(useCallback(() => viewsApi.listLore(campaignId), [campaignId]));
  return (
    <AsyncSection state={state} emptyMessage="No lore entries resolved for this campaign yet.">
      {(rows) => <FilteredLore rows={rows} />}
    </AsyncSection>
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
              <ul className="grid-cards entity-grid">
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
  const composition = useResource<Composition>(
    useCallback(() => viewsApi.getComposition(campaignId), [campaignId]),
  );

  return (
    <AsyncSection state={composition}>
      {(comp) => {
        // Honor each ref's include filter: null/missing means "every kind";
        // an explicit list (even []) is literal.
        const worldIds = comp.worlds
          .filter((ref) => ref.include === null || ref.include.includes("greetings"))
          .map((ref) => ref.world_id);
        return <GreetingsAcrossWorlds worldIds={worldIds} />;
      }}
    </AsyncSection>
  );
}

interface GreetingsFanout {
  greetings: Greeting[];
  /** World ids whose greeting fetch failed; surfaced, not swallowed. */
  failures: string[];
}

function GreetingsAcrossWorlds({ worldIds }: { worldIds: string[] }) {
  const idsKey = useMemo(() => worldIds.join("|"), [worldIds]);
  const state = useResource<GreetingsFanout>(
    useCallback(
      async () => {
        const settled = await Promise.allSettled(
          worldIds.map((id) => viewsApi.listGreetingsForWorld(id)),
        );
        const greetings: Greeting[] = [];
        const failures: string[] = [];
        settled.forEach((res, i) => {
          if (res.status === "fulfilled") greetings.push(...res.value);
          else failures.push(worldIds[i] ?? "?");
        });
        return { greetings, failures };
      },
      // worldIds identity is unstable; collapse to a string key.
      // eslint-disable-next-line react-hooks/exhaustive-deps
      [idsKey],
    ),
  );
  if (worldIds.length === 0) {
    return <p className="muted">No world refs in the composition include greetings.</p>;
  }
  return (
    <AsyncSection state={state}>
      {({ greetings, failures }) => (
        <>
          {failures.length > 0 && (
            <p className="error" role="alert">
              Failed to load greetings from: {failures.join(", ")}
            </p>
          )}
          {greetings.length === 0 ? (
            failures.length === 0 && (
              <p className="muted">No greetings declared in the composed worlds.</p>
            )
          ) : (
            <FilteredGreetings rows={greetings} />
          )}
        </>
      )}
    </AsyncSection>
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
        <ul className="grid-cards entity-grid">
          {filtered.map((g) => (
            <li key={`${g.world_id}:${g.id}`} className="card entity-card-static">
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
              <CardIconBar actions={[]} />
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

function EntityCard({ row, children }: { row: ResolvedEntity; children?: React.ReactNode }) {
  return (
    <li className="card entity-card-static">
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
      <CardIconBar actions={[]} />
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
