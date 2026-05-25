import { useCallback, useEffect, useState } from "react";

import { api } from "../../api/client";
import { ConfigSaveIndicator } from "./ConfigSaveIndicator";
import { errorMessage, useAppConfig } from "./shared";

interface LibrarySettings {
  embed_on_index: boolean;
  summarize_on_index: boolean;
}

function useLibrarySettings() {
  const [data, setData] = useState<LibrarySettings | null>(null);
  const [status, setStatus] = useState<"idle" | "loading" | "saving" | "saved" | "error">(
    "loading",
  );
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const result = await api.get<LibrarySettings>("/api/config/state-store/library");
        if (!cancelled) {
          setData(result);
          setStatus("idle");
        }
      } catch (err) {
        if (!cancelled) {
          setError(errorMessage(err));
          setStatus("error");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const patch = useCallback((next: Partial<LibrarySettings>) => {
    setData((prev) => (prev ? { ...prev, ...next } : prev));
    setStatus("saving");
    setError(null);
    void (async () => {
      try {
        const result = await api.patch<LibrarySettings>(
          "/api/config/state-store/library",
          next,
        );
        setData(result);
        setStatus("saved");
      } catch (err) {
        setError(errorMessage(err));
        setStatus("error");
      }
    })();
  }, []);

  return { data, patch, status, error };
}

export function LibraryTab() {
  const { data, patch, status, error } = useAppConfig();
  const lib = useLibrarySettings();
  return (
    <div className="settings-form">
      <label className="wizard-field">
        <span>Library path</span>
        <input
          type="text"
          value={data?.library_path ?? ""}
          onChange={(e) => patch({ library_path: e.target.value })}
          disabled={!data}
        />
        <small>Filesystem directory scanned for settings, style guides, presets.</small>
      </label>
      <label className="wizard-toggle">
        <input
          type="checkbox"
          checked={lib.data?.embed_on_index ?? true}
          onChange={(e) => lib.patch({ embed_on_index: e.target.checked })}
          disabled={!lib.data}
        />
        <span>Enable embeddings</span>
        <small>
          Generate vector embeddings when indexing library content. Disable to stop
          embedding API calls. Takes effect on next restart.
        </small>
      </label>
      <label className="wizard-toggle">
        <input
          type="checkbox"
          checked={lib.data?.summarize_on_index ?? true}
          onChange={(e) => lib.patch({ summarize_on_index: e.target.checked })}
          disabled={!lib.data}
        />
        <span>Enable auto-summarization</span>
        <small>
          Summarize library entities via LLM for background-tier context injection.
          Disable to stop LLM calls for summarization. Takes effect on next restart.
        </small>
      </label>
      <p className="wizard-meta">Changes save automatically and take effect on next restart.</p>
      <ConfigSaveIndicator status={status} error={error ?? lib.error} />
    </div>
  );
}
