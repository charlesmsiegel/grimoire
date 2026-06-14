/**
 * World view (spec 14 §World view).
 *
 * Tabbed listing of the campaign's resolved world contents, one tab per
 * entity kind in the same order as the library world detail, all rendered
 * through the shared EntityBrowser (issue #601). Rows come from the
 * cascade-resolved list endpoints, so emergent entities and overrides are
 * visible with truthful source chains. Campaign-scope affordances per card:
 * edit override (library-backed rows) and promote to library (emergent rows).
 * Characters here are the full composition (the Cast view is the dramatis
 * personae); locations get a hierarchy tree; lore is grouped by keyword.
 */

import { useCallback, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";

import { characterRefFor } from "../../api/campaign";
import { ENTITY_KIND_SINGULAR } from "../../api/library";
import { viewsApi } from "../../api/views";
import type { ResolvedCharacter, ResolvedEntity } from "../../api/types";
import { useResource } from "../../api/useResource";
import { AsyncSection } from "../../components/AsyncSection";
import { type CardIconAction } from "../../components/CardIconBar";
import { PencilIcon, PromoteIcon } from "../../components/icons";
import { EntityBrowser } from "../../components/EntityBrowser";
import { ChainBadge, Tabs } from "./common";
import { characterCardToFrontmatter } from "./characterFrontmatter";
import { EditOverrideDialog } from "./EditOverrideDialog";
import { PromoteToLibraryDialog } from "./PromoteToLibraryDialog";

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
      {tab === "characters" ? (
        <CharactersTab campaignId={campaignId} />
      ) : (
        // Keyed by kind so the browser's filter state (search, tags) never
        // leaks from one tab into the next.
        <KindTab key={tab} campaignId={campaignId} kindPlural={tab} />
      )}
    </section>
  );
}

function fetcherFor(campaignId: string, kind: Exclude<WorldTab, "characters">) {
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
    case "greetings":
      return () => viewsApi.listGreetings(campaignId);
  }
}

function isEmergentRow(row: ResolvedEntity): boolean {
  const top = row.source_chain[0];
  return !top || top.layer === "emergent";
}

/** Campaign-scope card actions: edit override / promote to library (#601). */
function campaignActions(
  row: ResolvedEntity,
  handlers: {
    onEditOverride: (row: ResolvedEntity) => void;
    onPromote: (row: ResolvedEntity) => void;
  },
): CardIconAction[] {
  const name = row.name || row.asset_id;
  if (isEmergentRow(row)) {
    return [
      {
        key: "promote",
        icon: <PromoteIcon />,
        label: `Promote ${name} to library`,
        onClick: () => handlers.onPromote(row),
      },
    ];
  }
  if (!row.world_id) return [];
  return [
    {
      key: "override",
      icon: <PencilIcon />,
      label: `Edit override for ${name}`,
      onClick: () => handlers.onEditOverride(row),
    },
  ];
}

interface CampaignBrowserProps {
  campaignId: string;
  kindSingular: string;
  kindPlural: string;
  rows: ResolvedEntity[];
  onChanged: () => void;
  renderExtras?: (row: ResolvedEntity) => React.ReactNode;
}

/** EntityBrowser in campaign scope, wired to the override/promote dialogs. */
function CampaignBrowser({
  campaignId,
  kindSingular,
  kindPlural,
  rows,
  onChanged,
  renderExtras,
}: CampaignBrowserProps) {
  const [overriding, setOverriding] = useState<ResolvedEntity | null>(null);
  const [promoting, setPromoting] = useState<ResolvedEntity | null>(null);
  return (
    <>
      <EntityBrowser
        rows={rows}
        kindPlural={kindPlural}
        scope="campaign"
        renderBadge={(row) => (
          <ChainBadge chain={row.source_chain} overrides={row.overrides_applied} />
        )}
        renderExtras={renderExtras}
        actionsFor={(row) =>
          campaignActions(row, { onEditOverride: setOverriding, onPromote: setPromoting })
        }
      />
      {overriding && overriding.world_id && (
        <EditOverrideDialog
          campaignId={campaignId}
          kind={kindSingular}
          entityId={overriding.asset_id}
          worldId={overriding.world_id}
          name={overriding.name}
          initialFrontmatter={overriding.frontmatter}
          onClose={() => setOverriding(null)}
          onSaved={() => {
            setOverriding(null);
            onChanged();
          }}
        />
      )}
      {promoting && (
        <PromoteToLibraryDialog
          campaignId={campaignId}
          kind={kindSingular}
          entityId={promoting.asset_id}
          name={promoting.name}
          onClose={() => setPromoting(null)}
          onPromoted={() => {
            setPromoting(null);
            onChanged();
          }}
        />
      )}
    </>
  );
}

function KindTab({
  campaignId,
  kindPlural,
}: {
  campaignId: string;
  kindPlural: Exclude<WorldTab, "characters">;
}) {
  const state = useResource(
    useCallback(() => fetcherFor(campaignId, kindPlural)(), [campaignId, kindPlural]),
  );
  return (
    <AsyncSection state={state} emptyMessage={`No ${kindPlural} resolved for this campaign yet.`}>
      {(rows) => (
        <CampaignBrowser
          campaignId={campaignId}
          kindSingular={ENTITY_KIND_SINGULAR[kindPlural] ?? kindPlural}
          kindPlural={kindPlural}
          rows={rows}
          onChanged={() => state.reload()}
          renderExtras={extrasFor(kindPlural)}
        />
      )}
    </AsyncSection>
  );
}

function extrasFor(kindPlural: Exclude<WorldTab, "characters">) {
  switch (kindPlural) {
    case "items":
      return (row: ResolvedEntity) => {
        const holder = extractHolder(row);
        return holder ? <p className="muted">Holder: {holder}</p> : null;
      };
    case "locations":
      return (row: ResolvedEntity) => {
        const parent = fmString(row, "parent_id") ?? fmString(row, "parent");
        return parent ? <p className="muted">Parent: {parent}</p> : null;
      };
    case "greetings":
      return (row: ResolvedEntity) => (
        <>
          {fmString(row, "starting_location") && (
            <p className="muted">Location: {fmString(row, "starting_location")}</p>
          )}
          {fmString(row, "starting_time") && (
            <p className="muted">Time: {fmString(row, "starting_time")}</p>
          )}
          {fmString(row, "mood") && <p className="muted">Mood: {fmString(row, "mood")}</p>}
        </>
      );
    default:
      return undefined;
  }
}

function fmString(row: ResolvedEntity, key: string): string | null {
  const v = row.frontmatter[key];
  return typeof v === "string" && v ? v : null;
}

function extractHolder(row: ResolvedEntity): string | null {
  const v =
    row.extras["holder"] ?? row.extras["current_holder"] ?? row.frontmatter["current_holder"];
  return typeof v === "string" && v ? v : null;
}

// ---------- characters ----------

function characterToRow(r: ResolvedCharacter): ResolvedEntity {
  const c = r.character;
  return {
    kind: "character",
    asset_id: c.id,
    world_id: c.world_id,
    name: c.name || c.id,
    frontmatter: characterCardToFrontmatter(c),
    body: c.body,
    source_chain: r.source_chain,
    overrides_applied: r.overrides_applied,
    extras: {},
  };
}

function CharactersTab({ campaignId }: { campaignId: string }) {
  const state = useResource(useCallback(() => viewsApi.listCharacters(campaignId), [campaignId]));
  const cast = useResource(useCallback(() => viewsApi.listCast(campaignId), [campaignId]));
  // Key membership by canonical ref, not bare id — the same asset id can
  // exist in more than one world (#517).
  const castRefs = new Set(cast.data?.map((r) => characterRefFor(r.character)) ?? []);
  return (
    <AsyncSection state={state} emptyMessage="No characters resolved for this campaign yet.">
      {(resolved) => (
        <>
          {/* Cast membership only decorates the cards; a failed lookup must
              not pass for "nobody is in the cast". */}
          {cast.error && !cast.data && (
            <div className="async-status async-error" role="alert">
              <p>Cast lookup failed — “In cast” links unavailable: {cast.error.message}</p>
              <button type="button" onClick={cast.reload}>
                Retry
              </button>
            </div>
          )}
          <CampaignBrowser
            campaignId={campaignId}
            kindSingular="character"
            kindPlural="characters"
            rows={resolved.map(characterToRow)}
            onChanged={() => state.reload()}
            renderExtras={(row) => {
              const ref = characterRefFor({ world_id: row.world_id, id: row.asset_id });
              return (
                <>
                  {fmString(row, "role") && <p className="muted">{fmString(row, "role")}</p>}
                  {fmString(row, "description") && <p>{fmString(row, "description")}</p>}
                  {castRefs.has(ref) && (
                    <p>
                      <Link
                        to={`/campaigns/${encodeURIComponent(campaignId)}/cast?ref=${encodeURIComponent(ref)}`}
                      >
                        In cast — open
                      </Link>
                    </p>
                  )}
                </>
              );
            }}
          />
        </>
      )}
    </AsyncSection>
  );
}
