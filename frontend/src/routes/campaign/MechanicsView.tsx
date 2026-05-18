/**
 * Mechanics view (spec 14 §Mechanics view).
 *
 * Shows the active mechanics module manifest, lists characters in the
 * campaign with a "sheet present / missing" indicator (bulk-create hook), and
 * fetches the selected character's sheet via the Mechanics API. Roll log /
 * combat tracker / content browser are scaffolded as placeholders that wire
 * up once the mechanics modules expose those surfaces over REST.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";

import { ApiError } from "../../api/client";
import { viewsApi } from "../../api/views";
import type { ResolvedCharacter } from "../../api/types";
import { useApi } from "../../api/useApi";
import { SheetRenderer } from "../../sheets";
import type { SheetSchema, SheetValue } from "../../sheets/types";
import { Loading } from "./common";

export function MechanicsView() {
  const { campaignId = "" } = useParams();
  const composition = useApi(() => viewsApi.getComposition(campaignId), [campaignId]);
  const installed = useApi(() => viewsApi.installedMechanics(), []);
  const characters = useApi(() => viewsApi.listCharacters(campaignId), [campaignId]);

  if (composition.status !== "ok") {
    return (
      <section className="route campaign-mechanics" aria-labelledby="mech-heading">
        <header className="route-header">
          <h2 id="mech-heading">Mechanics</h2>
        </header>
        <Loading state={composition}>{() => <p className="muted">Loading composition…</p>}</Loading>
      </section>
    );
  }

  const moduleId = composition.data.mechanics;
  if (!moduleId) {
    return (
      <section className="route campaign-mechanics" aria-labelledby="mech-heading">
        <header className="route-header">
          <h2 id="mech-heading">Mechanics</h2>
        </header>
        <p className="muted">
          No mechanics module is selected for this campaign. Install one and pick it from the
          campaign composition to add rules.
        </p>
      </section>
    );
  }

  return (
    <section className="route campaign-mechanics" aria-labelledby="mech-heading">
      <header className="route-header">
        <h2 id="mech-heading">Mechanics</h2>
      </header>

      <Loading state={installed}>
        {(modules) => {
          const active = modules.find((m) => m.manifest.id === moduleId);
          return (
            <article className="module-info">
              <h3>{active?.manifest.name ?? moduleId}</h3>
              {active?.manifest.version && (
                <p className="muted">version {active.manifest.version}</p>
              )}
              {active?.manifest.description && <p>{active.manifest.description}</p>}
              {active?.load_error && (
                <p className="error" role="alert">
                  Load error: {active.load_error}
                </p>
              )}
              {!active && (
                <p className="error" role="alert">
                  Module <code>{moduleId}</code> is referenced by the campaign but is not installed.
                </p>
              )}
            </article>
          );
        }}
      </Loading>

      <Loading state={characters} emptyMessage="No characters in this campaign yet.">
        {(rows) => <SheetsPanel campaignId={campaignId} moduleId={moduleId} characters={rows} />}
      </Loading>

      <section className="placeholder-panel">
        <h3>Roll log</h3>
        <p className="muted">
          Mechanics roll outcomes from the orchestrator turn loop stream here once a campaign starts
          producing rolls.
        </p>
      </section>

      <section className="placeholder-panel">
        <h3>Combat tracker</h3>
        <p className="muted">
          Combat trackers are mechanics-defined. The active module did not declare an inline combat
          panel, so this slot is idle until combat starts.
        </p>
      </section>

      <section className="placeholder-panel">
        <h3>Content browser</h3>
        <p className="muted">
          Mechanics-defined content kinds (spells, magic items, …) appear here when the active
          module advertises them.
        </p>
      </section>
    </section>
  );
}

interface SheetsPanelProps {
  campaignId: string;
  moduleId: string;
  characters: ResolvedCharacter[];
}

function SheetsPanel({ campaignId, characters }: SheetsPanelProps) {
  const [selected, setSelected] = useState<string | null>(characters[0]?.character.id ?? null);
  const [sheets, setSheets] = useState<Record<string, "present" | "missing" | "unknown">>({});

  const selectedRow = characters.find((c) => c.character.id === selected) ?? null;

  const present = Object.entries(sheets).filter(([, v]) => v === "present").length;
  const missing = Object.entries(sheets).filter(([, v]) => v === "missing").length;

  return (
    <div className="mechanics-sheets-layout">
      <aside className="sheet-list">
        <h3>Sheets</h3>
        <p className="muted">
          {present} present · {missing} missing of {characters.length} characters
        </p>
        <ul className="entity-list">
          {characters.map((c) => (
            <li key={c.character.id}>
              <button
                type="button"
                className={selected === c.character.id ? "entity-card active" : "entity-card"}
                onClick={() => setSelected(c.character.id)}
              >
                <div className="entity-card-head">
                  <span className="entity-name">{c.character.name}</span>
                  <SheetStatus status={sheets[c.character.id] ?? "unknown"} />
                </div>
                <small className="entity-meta">{c.character.role}</small>
              </button>
            </li>
          ))}
        </ul>
        {missing > 0 && (
          <button type="button" className="primary" disabled title="Wired in a follow-up task.">
            Bulk-create {missing} missing sheet{missing === 1 ? "" : "s"}
          </button>
        )}
      </aside>
      <section className="sheet-detail">
        {selectedRow ? (
          <CharacterSheet
            campaignId={campaignId}
            characterId={selectedRow.character.id}
            onStatus={(id, status) => setSheets((m) => ({ ...m, [id]: status }))}
          />
        ) : (
          <p className="muted">Select a character to view their sheet.</p>
        )}
      </section>
    </div>
  );
}

function SheetStatus({ status }: { status: "present" | "missing" | "unknown" }) {
  if (status === "present") return <span className="badge badge-ok">sheet</span>;
  if (status === "missing") return <span className="badge badge-warn">missing</span>;
  return <span className="badge">?</span>;
}

interface SheetProps {
  campaignId: string;
  characterId: string;
  onStatus: (id: string, status: "present" | "missing") => void;
}

function CharacterSheet({ campaignId, characterId, onStatus }: SheetProps) {
  const state = useApi<Record<string, unknown> | null>(
    () =>
      viewsApi.getSheet(campaignId, "character", characterId).then(
        (sheet) => {
          onStatus(characterId, "present");
          return sheet;
        },
        (err: unknown) => {
          if (err instanceof ApiError && err.status === 404) {
            onStatus(characterId, "missing");
            return null;
          }
          throw err;
        },
      ),
    [campaignId, characterId],
  );

  return (
    <Loading state={state}>
      {(sheet) => {
        if (sheet === null) {
          return (
            <div className="muted">
              <p>No sheet on file for this character under the active mechanics module.</p>
              <button type="button" disabled title="Wired in a follow-up task.">
                Initialize sheet
              </button>
            </div>
          );
        }
        return (
          <pre className="sheet-raw">
            <code>{JSON.stringify(sheet, null, 2)}</code>
          </pre>
        );
      }}
    </Loading>
  );
}
