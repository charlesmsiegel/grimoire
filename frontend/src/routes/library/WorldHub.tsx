import { useCallback } from "react";
import { Link, useParams } from "react-router-dom";

import { libraryApi, type WorldSummary } from "../../api/library";
import { useResource } from "../../api/useResource";
import { AsyncBoundary } from "./AsyncBoundary";

const KIND_TABS: { plural: string; label: string; singular: string }[] = [
  { plural: "characters", label: "Characters", singular: "character" },
  { plural: "locations", label: "Locations", singular: "location" },
  { plural: "items", label: "Items", singular: "item" },
  { plural: "lore", label: "Lore", singular: "lore entry" },
  { plural: "factions", label: "Factions", singular: "faction" },
  { plural: "monsters", label: "Monsters", singular: "monster" },
  { plural: "greetings", label: "Greetings", singular: "greeting" },
];

/** Checklist that drives the setup-progress bar. Intentionally simple guidance. */
function checklist(summary: WorldSummary) {
  return [
    { ok: summary.has_description, label: "Add a description", to: "meta" },
    { ok: summary.has_genre, label: "Set a genre", to: "meta" },
    { ok: (summary.counts.characters ?? 0) > 0, label: "Add a character", to: "characters" },
    { ok: (summary.counts.locations ?? 0) > 0, label: "Add a location", to: "locations" },
    {
      ok: (summary.counts.greetings ?? 0) > 0,
      label: "Write an opening greeting",
      to: "greetings",
    },
  ];
}

export function WorldHub() {
  const { worldId = "" } = useParams();
  const { data, loading, error, reload } = useResource(
    useCallback(() => libraryApi.worldSummary(worldId), [worldId]),
  );

  return (
    <section className="world-hub">
      <AsyncBoundary loading={loading} error={error} onRetry={reload}>
        {data && <WorldHubBody summary={data} worldId={worldId} />}
      </AsyncBoundary>
    </section>
  );
}

function WorldHubBody({ summary, worldId }: { summary: WorldSummary; worldId: string }) {
  const base = `/library/worlds/${encodeURIComponent(worldId)}`;
  const items = checklist(summary);
  const done = items.filter((i) => i.ok).length;
  const percent = Math.round((done / items.length) * 100);
  const unmet = items.filter((i) => !i.ok);

  return (
    <>
      <div className="world-hub-progress">
        <h4>World setup · {percent}%</h4>
        <div className="world-hub-progress-bar" aria-hidden>
          <span style={{ width: `${percent}%` }} />
        </div>
        <ul className="world-hub-checklist">
          {items.map((i) => (
            <li key={i.label} className={i.ok ? "done" : "todo"}>
              {i.ok ? "✓" : "▢"} <Link to={`${base}/${i.to}`}>{i.label}</Link>
            </li>
          ))}
        </ul>
      </div>

      <h4>Contents</h4>
      <ul className="grid-cards world-hub-counts">
        {KIND_TABS.map((k) => {
          const n = summary.counts[k.plural] ?? 0;
          return (
            <li key={k.plural} className={n === 0 ? "empty" : ""}>
              <Link to={`${base}/${k.plural}`}>
                <span className="world-hub-count">{n}</span>
                <span className="world-hub-kind">{k.label}</span>
                {n === 0 && <span className="world-hub-add">Add a {k.singular}</span>}
              </Link>
            </li>
          );
        })}
      </ul>

      {unmet.length > 0 && (
        <>
          <h4>Suggested next</h4>
          <ul className="world-hub-suggestions">
            {unmet.map((i) => (
              <li key={i.label}>
                <Link to={`${base}/${i.to}`}>{i.label}</Link>
              </li>
            ))}
          </ul>
        </>
      )}
    </>
  );
}
