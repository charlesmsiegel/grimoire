/**
 * Character-card import dialog (spec 2026-05-19-card-imports §REST/§UI).
 *
 * Two-step flow: upload → preview (parsed character + greetings + lore
 * + warnings) → toggles → commit. The component is self-contained;
 * callers render it as a modal under WorldDetailView and pass an
 * ``onClose`` callback that closes the dialog and triggers a reload of
 * the library list.
 */

import { useState } from "react";

import {
  type CommitResponse,
  type IngestOptionsPayload,
  type PreviewResponse,
  commitSillyTavernImport,
  previewSillyTavernImport,
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

export function ImportDialog({ worldId, onClose }: Props) {
  const [mode, setMode] = useState<Mode>("pick");
  const [errorMsg, setErrorMsg] = useState<string>("");
  const [preview, setPreview] = useState<PreviewResponse | null>(null);
  const [commitResult, setCommitResult] = useState<CommitResponse | null>(null);
  const [options, setOptions] = useState<Required<IngestOptionsPayload>>(
    DEFAULT_OPTIONS,
  );

  async function handleFile(file: File) {
    setMode("previewing");
    setErrorMsg("");
    try {
      const response = await previewSillyTavernImport(worldId, file);
      setPreview(response);
      setMode("preview");
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : String(err));
      setMode("error");
    }
  }

  async function handleCommit() {
    if (!preview) return;
    setMode("committing");
    setErrorMsg("");
    try {
      const response = await commitSillyTavernImport(
        worldId,
        preview.preview_id,
        options,
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

      {mode === "preview" && preview && (
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
              Import character_book entries as setting lore (
              {preview.ingested.lore_entries.length})
            </label>
          </fieldset>

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

          <details>
            <summary>
              Lore entries ({preview.ingested.lore_entries.length})
            </summary>
            <ul>
              {preview.ingested.lore_entries.map((entry) => (
                <li key={entry.source_index}>
                  <strong>{entry.name || entry.keys[0] || `entry-${entry.source_index}`}</strong>{" "}
                  — keys: {entry.keys.join(", ")}
                </li>
              ))}
            </ul>
          </details>

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

      {mode === "error" && (
        <p className="import-dialog-error" role="alert">
          {errorMsg}
        </p>
      )}
    </div>
  );
}
