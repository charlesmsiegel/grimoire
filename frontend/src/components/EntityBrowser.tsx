/**
 * Shared world-contents browser (issue #601).
 *
 * One grid + CardFilters + per-kind decorators (location hierarchy tree, lore
 * keyword grouping) consuming the common ResolvedEntity row shape, used by
 * both the library's EntityListView and the campaign WorldView. Scope-specific
 * affordances arrive through props: the library passes editor links, token
 * badges, and delete/convert actions; the campaign passes the source-chain
 * badge and override/promote actions.
 */

import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import type { ResolvedEntity } from "../api/types";
import { useCardFilters } from "../hooks/useCardFilters";
import { CardFilters } from "./CardFilters";
import { CardIconBar, type CardIconAction } from "./CardIconBar";
import { Markdown } from "./Markdown";
import { TokenBadge } from "./TokenBadge";

export interface EntityBrowserProps {
  rows: ResolvedEntity[];
  /** Plural kind for copy and per-kind decorators ("items", "locations", …). */
  kindPlural: string;
  /**
   * "library" renders editor links + token badges and searches within one
   * world; "campaign" shows per-card world provenance and includes it in the
   * search haystack.
   */
  scope: "library" | "campaign";
  /** Per-card icon actions (delete/convert in library; override/promote in campaign). */
  actionsFor?: (row: ResolvedEntity) => CardIconAction[];
  /** Card title link target (library editor page); null renders plain text. */
  linkFor?: (row: ResolvedEntity) => string | null;
  /** Scope-specific badge slot in the card header (e.g. the campaign ChainBadge). */
  renderBadge?: (row: ResolvedEntity) => ReactNode;
  /** Extra inline content under the card meta (holder, parent, greeting fields…). */
  renderExtras?: (row: ResolvedEntity) => ReactNode;
}

function rowTags(row: ResolvedEntity): string[] {
  const raw = row.frontmatter["tags"] ?? row.frontmatter["keywords"];
  if (Array.isArray(raw)) return raw.filter((v): v is string => typeof v === "string");
  return [];
}

function rowKeywords(row: ResolvedEntity): string[] {
  const raw = row.frontmatter["keywords"] ?? row.frontmatter["tags"];
  if (Array.isArray(raw)) return raw.filter((v): v is string => typeof v === "string");
  return [];
}

function fmString(row: ResolvedEntity, key: string): string | null {
  const v = row.frontmatter[key];
  return typeof v === "string" ? v : null;
}

export function EntityBrowser({
  rows,
  kindPlural,
  scope,
  actionsFor,
  linkFor,
  renderBadge,
  renderExtras,
}: EntityBrowserProps) {
  const { filtered, search, setSearch, selectedTags, toggleTag, clearTags, availableTags } =
    useCardFilters(rows, {
      text: (row) => [
        row.name,
        row.asset_id,
        scope === "campaign" ? row.world_id : null,
        row.body,
        fmString(row, "description"),
        fmString(row, "starting_location"),
        fmString(row, "mood"),
      ],
      tags: rowTags,
    });

  const filters = (
    <CardFilters
      search={search}
      onSearch={setSearch}
      availableTags={availableTags}
      selectedTags={selectedTags}
      onToggleTag={toggleTag}
      onClearTags={clearTags}
      searchPlaceholder={`Search ${kindPlural} by name, id, or text…`}
      searchLabel={`Search ${kindPlural}`}
      resultSummary={
        filtered.length === rows.length
          ? `${rows.length} ${kindPlural}`
          : `${filtered.length} of ${rows.length}`
      }
    />
  );

  const card = (row: ResolvedEntity) => (
    <BrowserCard
      key={`${row.world_id ?? "campaign"}:${row.asset_id}`}
      row={row}
      scope={scope}
      link={linkFor?.(row) ?? null}
      badge={renderBadge?.(row)}
      extras={renderExtras?.(row)}
      actions={actionsFor?.(row) ?? []}
    />
  );

  let body: ReactNode;
  if (filtered.length === 0) {
    body = <p className="muted">No {kindPlural} match the current filters.</p>;
  } else if (kindPlural === "lore") {
    body = (
      <div className="lore-grid">
        {[...groupByKeyword(filtered).entries()].map(([kw, entries]) => (
          <section key={kw} className="lore-group">
            <h3>{kw}</h3>
            <ul className="grid-cards entity-grid">{entries.map(card)}</ul>
          </section>
        ))}
      </div>
    );
  } else {
    body = <ul className="grid-cards entity-grid">{filtered.map(card)}</ul>;
  }

  if (kindPlural === "locations") {
    // Hierarchy is built from the full row set so filtering the grid never
    // orphans the tree.
    return (
      <>
        {filters}
        <div className="locations-layout">
          <aside className="location-tree">
            <h3>Hierarchy</h3>
            <LocationTree nodes={buildLocationTree(rows)} />
          </aside>
          <div>{body}</div>
        </div>
      </>
    );
  }

  return (
    <>
      {filters}
      {body}
    </>
  );
}

interface BrowserCardProps {
  row: ResolvedEntity;
  scope: "library" | "campaign";
  link: string | null;
  badge: ReactNode;
  extras: ReactNode;
  actions: CardIconAction[];
}

function BrowserCard({ row, scope, link, badge, extras, actions }: BrowserCardProps) {
  const title = row.name || row.asset_id;
  const tags = rowTags(row);
  return (
    <li className="card entity-browser-card">
      <header>
        <h4>{link ? <Link to={link}>{title}</Link> : title}</h4>
        {badge}
      </header>
      <small>
        {row.asset_id}
        {scope === "campaign" && row.world_id ? ` · ${row.world_id}` : ""}
      </small>
      {tags.length > 0 && <p className="entity-browser-card-meta">{tags.join(" · ")}</p>}
      {extras}
      {row.body && (
        <details>
          <summary>Preview</summary>
          <Markdown>{row.body}</Markdown>
        </details>
      )}
      {scope === "library" && (
        <p className="entity-browser-card-meta">
          <TokenBadge text={`${JSON.stringify(row.frontmatter)}\n${row.body}`} />
        </p>
      )}
      <CardIconBar actions={actions} />
    </li>
  );
}

// ---------- per-kind decorators ----------

interface LocationNode {
  row: ResolvedEntity;
  children: LocationNode[];
}

function extractParent(row: ResolvedEntity): string | null {
  return fmString(row, "parent_id") ?? fmString(row, "parent");
}

function buildLocationTree(rows: ResolvedEntity[]): LocationNode[] {
  const byId = new Map<string, LocationNode>();
  for (const row of rows) byId.set(row.asset_id, { row, children: [] });
  const roots: LocationNode[] = [];
  for (const node of byId.values()) {
    const parent = extractParent(node.row);
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
    const kw = rowKeywords(row);
    const keys = kw.length > 0 ? kw : ["(unkeyed)"];
    for (const k of keys) {
      const list = groups.get(k) ?? [];
      list.push(row);
      groups.set(k, list);
    }
  }
  return groups;
}
