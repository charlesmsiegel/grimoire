/**
 * Character-card import dialog (specs 2026-05-19-card-imports §REST/§UI
 * and 2026-05-20-import-dialog-reclassify §4).
 *
 * Two-step flow: upload → preview (parsed character + greetings + lore
 * + warnings) → toggles + per-row category dropdowns → commit. The
 * component is self-contained; callers render it as a modal under
 * WorldDetailView and pass an ``onClose`` callback that closes the
 * dialog and triggers a reload of the library list.
 */

import { useMemo, useState } from "react";

import {
  type CommitResponse,
  type IngestOptionsPayload,
  type LoreOverrideKind,
  type LoreOverridePayload,
  type LoreSuggestion,
  type PreviewResponse,
  commitSillyTavernImport,
  previewSillyTavernImport,
  requiredOverridesFor,
} from "../../api/imports";

interface Props {
  worldId: string;
  onClose: (committed: boolean) => void;
}

type Mode = "pick" | "previewing" | "preview" | "committing" | "done" | "error";

const DEFAULT_OPTIONS: Required<IngestOptionsPayload> = {
  expand_macros: true,
  import_character_book: true,
  import_alternate_greetings: true,
  import_primary_greeting: true,
  keep_embedded_avatar: true,
  extract_relationships: true,
  derive_image_prompt: true,
};

const KIND_OPTIONS: { value: LoreOverrideKind; label: string }[] = [
  { value: "lore", label: "Lore" },
  { value: "character", label: "Character" },
  { value: "location", label: "Location" },
  { value: "faction", label: "Faction" },
  { value: "item", label: "Item" },
  { value: "skip", label: "Skip" },
];

interface LoreRowState {
  source_index: number;
  kind: LoreOverrideKind;
  overrides: Record<string, string>;
}

function initialRows(preview: PreviewResponse): LoreRowState[] {
  const byIndex = new Map<number, LoreSuggestion>();
  for (const s of preview.lore_suggestions) byIndex.set(s.source_index, s);
  return preview.ingested.lore_entries.map((entry) => {
    const suggestion = byIndex.get(entry.source_index);
    return {
      source_index: entry.source_index,
      kind: (suggestion?.kind ?? "lore") as LoreOverrideKind,
      overrides: {},
    };
  });
}

function kindLabel(kind: LoreOverrideKind): string {
  return kind.charAt(0).toUpperCase() + kind.slice(1);
}

export function ImportDialog({ worldId, onClose }: Props) {
  const [mode, setMode] = useState<Mode>("pick");
  const [errorMsg, setErrorMsg] = useState<string>("");
  const [preview, setPreview] = useState<PreviewResponse | null>(null);
  const [commitResult, setCommitResult] = useState<CommitResponse | null>(null);
  const [options, setOptions] = useState<Required<IngestOptionsPayload>>(
    DEFAULT_OPTIONS,
  );
  const [loreRows, setLoreRows] = useState<LoreRowState[]>([]);

  const suggestionsByIndex = useMemo(() => {
    const m = new Map<number, LoreSuggestion>();
    if (preview) for (const s of preview.lore_suggestions) m.set(s.source_index, s);
    return m;
  }, [preview]);

  async function handleFile(file: File) {
    setMode("previewing");
    setErrorMsg("");
    try {
      const response = await previewSillyTavernImport(worldId, file);
      setPreview(response);
      setLoreRows(initialRows(response));
      setMode("preview");
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : String(err));
      setMode("error");
    }
  }

  function setRowKind(sourceIndex: number, kind: LoreOverrideKind) {
    setLoreRows((rows) =>
      rows.map((row) =>
        row.source_index === sourceIndex
          ? { ...row, kind, overrides: {} }
          : row,
      ),
    );
  }

  function setRowOverride(sourceIndex: number, key: string, value: string) {
    setLoreRows((rows) =>
      rows.map((row) =>
        row.source_index === sourceIndex
          ? { ...row, overrides: { ...row.overrides, [key]: value } }
          : row,
      ),
    );
  }

  async function handleCommit() {
    if (!preview) return;
    // Validate required overrides per row.
    for (const row of loreRows) {
      const required = requiredOverridesFor(row.kind);
      const missing = required.filter((k) => !row.overrides[k]?.trim());
      if (missing.length > 0) {
        setErrorMsg(
          `${kindLabel(row.kind)} row ${row.source_index} requires ${missing.join(", ")}`,
        );
        setMode("preview");
        return;
      }
    }
    const overridesPayload: LoreOverridePayload[] = loreRows
      .filter((row) => row.kind !== "lore")
      .map((row) => ({
        source_index: row.source_index,
        kind: row.kind,
        overrides: row.overrides,
      }));
    setMode("committing");
    setErrorMsg("");
    try {
      const response = await commitSillyTavernImport(
        worldId,
        preview.preview_id,
        options,
        overridesPayload,
      );
      setCommitResult(response);
      setMode("done");
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : String(err));
      setMode("error");
    }
  }

  function toggle<K extends keyof Required<IngestOptionsPayload>>(key: K) {
    setOptions((prev) => ({ ...prev, [key]: !prev[key] }));
  }

  return (
    <div className="import-dialog" role="dialog" aria-label="Import character card">
      <header className="import-dialog-header">
        <h3>Import character card</h3>
        <button type="button" onClick={() => onClose(mode === "done")}>
          {mode === "done" ? "Close" : "Cancel"}
        </button>
      </header>

      {mode === "pick" && (
        <label className="import-dialog-pick">
          <span>Select a SillyTavern card (PNG / charx / JSON):</span>
          <input
            type="file"
            accept=".png,.json,.charx,application/json,image/png,application/zip"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) void handleFile(file);
            }}
          />
        </label>
      )}

      {mode === "previewing" && <p>Parsing card…</p>}
      {mode === "committing" && <p>Writing to library…</p>}

      {(mode === "preview" || mode === "error") && preview && (
        <section className="import-dialog-preview">
          <h4>{preview.ingested.data.name}</h4>
          {preview.ingested.data.description && (
            <p className="import-dialog-description">
              {preview.ingested.data.description}
            </p>
          )}

          <fieldset className="import-dialog-options">
            <legend>What to import</legend>
            <label>
              <input
                type="checkbox"
                checked={options.expand_macros}
                onChange={() => toggle("expand_macros")}
              />
              Expand {"{{char}}/{{random}}/{{roll}}"} macros at ingest
            </label>
            <label>
              <input
                type="checkbox"
                checked={options.import_primary_greeting}
                onChange={() => toggle("import_primary_greeting")}
              />
              Import primary greeting (first_mes)
            </label>
            <label>
              <input
                type="checkbox"
                checked={options.import_alternate_greetings}
                onChange={() => toggle("import_alternate_greetings")}
              />
              Import alternate greetings ({preview.ingested.alternate_greetings.length})
            </label>
            <label>
              <input
                type="checkbox"
                checked={options.import_character_book}
                onChange={() => toggle("import_character_book")}
              />
              Import character_book entries (
              {preview.ingested.lore_entries.length})
            </label>
          </fieldset>

          {preview.ingested.greetings.length > 0 && (
            <details>
              <summary>
                Greetings ({preview.ingested.greetings.length})
              </summary>
              <ul>
                {preview.ingested.greetings.map((g) => (
                  <li key={g.source_index}>
                    <strong>{g.is_primary ? "Primary" : `Alt ${g.source_index}`}:</strong>{" "}
                    {g.body.slice(0, 120)}
                    {g.body.length > 120 ? "…" : ""}
                  </li>
                ))}
              </ul>
            </details>
          )}

          {preview.ingested.lore_entries.length > 0 && options.import_character_book && (
            <fieldset className="import-dialog-lore-rows">
              <legend>
                Lore entries ({preview.ingested.lore_entries.length}) — pick a category per row
              </legend>
              <ul>
                {preview.ingested.lore_entries.map((entry) => {
                  const row = loreRows.find(
                    (r) => r.source_index === entry.source_index,
                  );
                  if (!row) return null;
                  const suggestion = suggestionsByIndex.get(entry.source_index);
                  const required = requiredOverridesFor(row.kind);
                  const label =
                    entry.name || entry.keys[0] || `entry-${entry.source_index}`;
                  return (
                    <li key={entry.source_index} className="import-dialog-lore-row">
                      <span className="import-dialog-lore-row-name">
                        <strong>{label}</strong>
                        {entry.keys.length > 0 && (
                          <> — keys: {entry.keys.join(", ")}</>
                        )}
                      </span>
                      <label>
                        <span className="visually-hidden">
                          Category for row {entry.source_index}
                        </span>
                        <select
                          aria-label={`Category for row ${entry.source_index}`}
                          value={row.kind}
                          onChange={(e) =>
                            setRowKind(
                              entry.source_index,
                              e.target.value as LoreOverrideKind,
                            )
                          }
                        >
                          {KIND_OPTIONS.map((opt) => (
                            <option key={opt.value} value={opt.value}>
                              {opt.label}
                            </option>
                          ))}
                        </select>
                      </label>
                      {suggestion && suggestion.kind !== "lore" && suggestion.reason && (
                        <span
                          className="import-dialog-lore-row-why"
                          title={suggestion.reason}
                        >
                          Why?
                        </span>
                      )}
                      {required.map((key) => (
                        <label key={key} className="import-dialog-lore-row-override">
                          <span>
                            {kindLabel(row.kind)} {key}
                          </span>
                          <input
                            type="text"
                            aria-label={`${kindLabel(row.kind)} ${key} (row ${entry.source_index})`}
                            value={row.overrides[key] ?? ""}
                            onChange={(e) =>
                              setRowOverride(
                                entry.source_index,
                                key,
                                e.target.value,
                              )
                            }
                          />
                        </label>
                      ))}
                    </li>
                  );
                })}
              </ul>
            </fieldset>
          )}

          {preview.ingested.warnings.length > 0 && (
            <details>
              <summary>Warnings ({preview.ingested.warnings.length})</summary>
              <ul>
                {preview.ingested.warnings.map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            </details>
          )}

          <p className="import-dialog-note">
            Character-scoped lore coming in a future release; for now lore
            lands at world scope.
          </p>

          {errorMsg && (
            <p className="import-dialog-error" role="alert">
              {errorMsg}
            </p>
          )}

          <div className="import-dialog-actions">
            <button type="button" onClick={() => void handleCommit()}>
              Commit
            </button>
          </div>
        </section>
      )}

      {mode === "done" && commitResult && (
        <section className="import-dialog-done">
          <h4>Import complete</h4>
          <p>
            Created {commitResult.result.created.length} entries
            {commitResult.result.errors.length > 0
              ? `, ${commitResult.result.errors.length} errors`
              : ""}
            .
          </p>
          <details>
            <summary>Files created</summary>
            <ul>
              {commitResult.result.created.map((c) => (
                <li key={c}>
                  <code>{c}</code>
                </li>
              ))}
            </ul>
          </details>
          {commitResult.result.errors.length > 0 && (
            <details open>
              <summary>Errors</summary>
              <ul>
                {commitResult.result.errors.map((e, i) => (
                  <li key={i}>{e}</li>
                ))}
              </ul>
            </details>
          )}
        </section>
      )}

      {mode === "error" && !preview && (
        <p className="import-dialog-error" role="alert">
          {errorMsg}
        </p>
      )}
    </div>
  );
}
