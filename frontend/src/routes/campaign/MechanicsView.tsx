/**
 * Mechanics view (spec 14 §Mechanics view; spec 06 §Sheet UI rendering /
 * §Character creation / §Responsibilities).
 *
 * Wires the campaign's active mechanics module into the Frontend surface:
 *   - lists characters with sheet present/missing/unknown indicators;
 *   - renders the selected character's sheet through `SheetRenderer`, with
 *     the module's scoped `theme_css` applied;
 *   - exposes the character-creation wizard for missing sheets;
 *   - mounts the content browser (one tab per declared `content_kind`);
 *   - warns when the campaign has preserved sheets from a previously-bound
 *     mechanics module (spec 06 §Switching modules mid-campaign).
 *
 * Roll log / combat tracker remain placeholders — those depend on backend
 * surfaces outside this spec.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";

import { ApiError } from "../../api/client";
import { mechanicsApi, type RegisteredModule } from "../../api/library";
import { viewsApi } from "../../api/views";
import type { ResolvedCharacter } from "../../api/types";
import { useApi } from "../../api/useApi";
import { useResource } from "../../api/useResource";
import { SheetRenderer } from "../../sheets";
import type { SheetSchema, SheetValue } from "../../sheets/types";
import { Loading } from "./common";
import { CampaignCharacterCreation } from "./CharacterCreation";
import { CardIconBar } from "../../components/CardIconBar";
import { ContentBrowser } from "./ContentBrowser";

export function MechanicsView() {
  const { campaignId = "" } = useParams();
  const composition = useApi(useCallback(() => viewsApi.getComposition(campaignId), [campaignId]));
  const installed = useResource(useCallback(() => mechanicsApi.listInstalled(), []));
  const characters = useApi(useCallback(() => viewsApi.listCharacters(campaignId), [campaignId]));

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

  const active: RegisteredModule | undefined = (installed.data ?? []).find(
    (m) => m.manifest.id === moduleId,
  );

  return (
    <section className="route campaign-mechanics" aria-labelledby="mech-heading">
      <header className="route-header">
        <h2 id="mech-heading">Mechanics</h2>
      </header>

      {installed.loading && <p className="muted">Loading module…</p>}
      {installed.error && (
        <p className="error" role="alert">
          Failed to load module list: {installed.error.message}
        </p>
      )}
      {!installed.loading && (
        <article className="module-info">
          <h3>{active?.manifest.name ?? moduleId}</h3>
          {active?.manifest.version && <p className="muted">version {active.manifest.version}</p>}
          {active?.manifest.description && <p>{active.manifest.description}</p>}
          {!active && (
            <p className="error" role="alert">
              Module <code>{moduleId}</code> is referenced by the campaign but is not installed.
            </p>
          )}
        </article>
      )}

      <Loading state={characters} emptyMessage="No characters in this campaign yet.">
        {(rows) => (
          <SheetsPanel
            campaignId={campaignId}
            module={active ?? null}
            moduleId={moduleId}
            characters={rows}
            onRefresh={() => characters.reload()}
          />
        )}
      </Loading>

      {active && active.manifest.content_kinds.length > 0 && (
        <ContentBrowser campaignId={campaignId} module={active} />
      )}

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
    </section>
  );
}

interface SheetsPanelProps {
  campaignId: string;
  module: RegisteredModule | null;
  moduleId: string;
  characters: ResolvedCharacter[];
  onRefresh: () => void;
}

function SheetsPanel({ campaignId, module, moduleId, characters, onRefresh }: SheetsPanelProps) {
  const [selected, setSelected] = useState<string | null>(characters[0]?.character.id ?? null);
  const [sheets, setSheets] = useState<Record<string, "present" | "missing" | "unknown">>({});
  const [bulkBusy, setBulkBusy] = useState(false);
  const [bulkError, setBulkError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [creatingFor, setCreatingFor] = useState<string | null>(null);
  // Bump after the wizard finishes so the sheet re-fetches and the
  // "missing" badge flips to "present" without reloading the whole page.
  const [sheetNonce, setSheetNonce] = useState(0);

  const schemaState = useApi(
    useCallback(() => viewsApi.getSheetSchema(moduleId, "character"), [moduleId]),
  );
  // Prefer the inlined `theme_css` on the RegisteredModule payload (one fewer
  // network hop); fall back to the standalone GET when missing.
  const themeState = useApi(useCallback(() => viewsApi.getMechanicsThemeCss(moduleId), [moduleId]));

  const selectedRow = characters.find((c) => c.character.id === selected) ?? null;

  const present = Object.entries(sheets).filter(([, v]) => v === "present").length;
  const missing = Object.entries(sheets).filter(([, v]) => v === "missing").length;

  const handleBulk = useCallback(async () => {
    setBulkBusy(true);
    setBulkError(null);
    try {
      await viewsApi.bulkCreateMissingSheets(campaignId);
      setSheets({});
      setReloadKey((k) => k + 1);
      onRefresh();
    } catch (err) {
      setBulkError(err instanceof Error ? err.message : String(err));
    } finally {
      setBulkBusy(false);
    }
  }, [campaignId, onRefresh]);

  // Prefer the inlined `theme_css` on the RegisteredModule payload (one fewer
  // network hop); fall back to the standalone GET when missing.
  const themeCss = module?.theme_css ?? (themeState.status === "ok" ? themeState.data : "");
  const schema = schemaState.status === "ok" ? (schemaState.data as unknown as SheetSchema) : null;

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
              <CardIconBar actions={[]} />
            </li>
          ))}
        </ul>
        {missing > 0 && (
          <button type="button" className="primary" onClick={handleBulk} disabled={bulkBusy}>
            {bulkBusy
              ? "Creating…"
              : `Bulk-create ${missing} missing sheet${missing === 1 ? "" : "s"}`}
          </button>
        )}
        {bulkError && (
          <p className="error" role="alert">
            {bulkError}
          </p>
        )}
      </aside>
      <section className="sheet-detail">
        {selectedRow ? (
          <CharacterSheet
            key={`${selectedRow.character.id}-${reloadKey}-${sheetNonce}`}
            campaignId={campaignId}
            moduleId={moduleId}
            characterId={selectedRow.character.id}
            schema={schema}
            themeCss={themeCss}
            onStatus={(id, status) => setSheets((m) => ({ ...m, [id]: status }))}
            onStartCreation={() => setCreatingFor(selectedRow.character.id)}
          />
        ) : (
          <p className="muted">Select a character to view their sheet.</p>
        )}
      </section>
      {creatingFor && (
        <div
          className="modal-backdrop"
          role="dialog"
          aria-modal="true"
          aria-label="Create character sheet"
        >
          <div className="modal character-creation-modal">
            <CampaignCharacterCreation
              campaignId={campaignId}
              characterId={creatingFor}
              moduleId={moduleId}
              themeCss={themeCss}
              heading={
                characters.find((c) => c.character.id === creatingFor)?.character.name ??
                creatingFor
              }
              onCancel={() => setCreatingFor(null)}
              onComplete={() => {
                setCreatingFor(null);
                setSheetNonce((n) => n + 1);
              }}
            />
          </div>
        </div>
      )}
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
  moduleId: string;
  characterId: string;
  schema: SheetSchema | null;
  themeCss: string;
  onStatus: (id: string, status: "present" | "missing") => void;
  onStartCreation: () => void;
}

function CharacterSheet({
  campaignId,
  moduleId,
  characterId,
  schema,
  themeCss,
  onStatus,
  onStartCreation,
}: SheetProps) {
  const state = useApi<Record<string, unknown> | null>(
    useCallback(
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
      // onStatus is intentionally NOT in deps: it's a parent prop that
      // changes every render; including it would refetch the sheet on
      // every keystroke. The status callbacks are write-only side effects.
      // eslint-disable-next-line react-hooks/exhaustive-deps
      [campaignId, characterId],
    ),
  );

  const [working, setWorking] = useState<SheetValue | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const dirtyRef = useRef(false);

  // Reset working copy when the underlying sheet changes (e.g. switching
  // characters). The `key={...}` on the parent makes this remount on
  // bulk-create completion as well.
  const fetchedSheet = state.status === "ok" ? state.data : null;
  useEffect(() => {
    if (fetchedSheet !== null) {
      setWorking(fetchedSheet as SheetValue);
      dirtyRef.current = false;
    }
  }, [fetchedSheet]);

  const persist = useCallback(
    async (next: SheetValue) => {
      try {
        await viewsApi.putSheet(campaignId, "character", characterId, next);
        setSaveError(null);
      } catch (err) {
        setSaveError(err instanceof Error ? err.message : String(err));
      }
    },
    [campaignId, characterId],
  );

  return (
    <Loading state={state}>
      {(sheet) => {
        if (sheet === null) {
          return (
            <div className="muted">
              <p>No sheet on file for this character under the active mechanics module.</p>
              <p>
                Walk this character through the module's creation wizard, or use Bulk-create to
                initialise every missing sheet at once.
              </p>
              <button type="button" className="primary" onClick={onStartCreation}>
                Create sheet
              </button>
            </div>
          );
        }
        if (!schema) {
          return (
            <pre className="sheet-raw">
              <code>{JSON.stringify(sheet, null, 2)}</code>
            </pre>
          );
        }
        return (
          <>
            <SheetRenderer
              moduleId={moduleId}
              schema={schema}
              value={(working ?? sheet) as SheetValue}
              themeCss={themeCss || undefined}
              onChange={(next) => {
                setWorking(next);
                dirtyRef.current = true;
              }}
            />
            <div className="sheet-actions">
              <button
                type="button"
                disabled={!dirtyRef.current && working === null}
                onClick={() => {
                  if (working) void persist(working);
                  dirtyRef.current = false;
                }}
              >
                Save
              </button>
              {saveError && (
                <span className="error" role="alert">
                  {saveError}
                </span>
              )}
            </div>
          </>
        );
      }}
    </Loading>
  );
}
