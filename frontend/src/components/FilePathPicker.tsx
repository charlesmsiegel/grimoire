import { useCallback, useEffect, useState } from "react";

import { type BrowseEntry, type GGUFInfo, configApi } from "../api/config";

interface Props {
  label: string;
  description?: string;
  required?: boolean;
  value: string;
  glob?: string;
  onChange: (path: string) => void;
  onIntrospect?: (info: GGUFInfo) => void;
}

export type { GGUFInfo };

export function FilePathPicker({
  label,
  description,
  required,
  value,
  glob,
  onChange,
  onIntrospect,
}: Props) {
  const [open, setOpen] = useState(false);
  const [directory, setDirectory] = useState<string | undefined>(undefined);
  const [parent, setParent] = useState<string | undefined>(undefined);
  const [entries, setEntries] = useState<BrowseEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [introspecting, setIntrospecting] = useState(false);
  const [ggufInfo, setGgufInfo] = useState<GGUFInfo | null>(null);

  const browse = useCallback(
    async (dir?: string) => {
      setLoading(true);
      setError(null);
      try {
        const res = await configApi.browseFiles(dir, glob);
        setDirectory(res.directory);
        setParent(res.parent);
        setEntries(res.entries);
      } catch (err) {
        setError(String(err));
      } finally {
        setLoading(false);
      }
    },
    [glob],
  );

  const introspect = useCallback(
    async (path: string) => {
      if (!path.toLowerCase().endsWith(".gguf")) return;
      setIntrospecting(true);
      try {
        const info = await configApi.ggufIntrospect(path);
        setGgufInfo(info);
        onIntrospect?.(info);
      } catch {
        setGgufInfo(null);
      } finally {
        setIntrospecting(false);
      }
    },
    [onIntrospect],
  );

  useEffect(() => {
    if (open) void browse(directory);
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleOpen = () => setOpen(true);
  const handleClose = () => setOpen(false);

  const handleSelect = (entry: BrowseEntry) => {
    if (entry.is_dir) {
      void browse(entry.path);
    } else {
      onChange(entry.path);
      void introspect(entry.path);
      setOpen(false);
    }
  };

  return (
    <label>
      <span>
        {label} {required && <em>*</em>}
      </span>
      <div className="file-path-picker">
        <input
          type="text"
          value={value}
          placeholder={description}
          onChange={(e) => onChange(e.target.value)}
          onBlur={(e) => {
            if (e.target.value) void introspect(e.target.value);
          }}
        />
        <button type="button" className="browse-btn" onClick={handleOpen}>
          Browse
        </button>
      </div>
      {description && <small>{description}</small>}
      {introspecting && <small className="gguf-status">Reading model metadata…</small>}
      {ggufInfo && !introspecting && (
        <small className="gguf-info">
          {[
            ggufInfo.name,
            ggufInfo.architecture,
            ggufInfo.context_length && `${ggufInfo.context_length.toLocaleString()} ctx`,
            ggufInfo.embedding_length && `${ggufInfo.embedding_length.toLocaleString()} dims`,
            ggufInfo.has_chat_template && "has chat template",
          ]
            .filter(Boolean)
            .join(" · ")}
        </small>
      )}

      {open && (
        <div className="file-browser-overlay" onClick={handleClose}>
          <div className="file-browser" onClick={(e) => e.stopPropagation()}>
            <div className="file-browser-header">
              <span className="file-browser-path" title={directory}>
                {directory}
              </span>
              <button type="button" onClick={handleClose}>
                &times;
              </button>
            </div>

            {loading && <div className="file-browser-loading">Loading…</div>}
            {error && <div className="file-browser-error">{error}</div>}

            {!loading && (
              <ul className="file-browser-list">
                {parent && (
                  <li>
                    <button type="button" className="fb-entry fb-dir" onClick={() => browse(parent)}>
                      ..
                    </button>
                  </li>
                )}
                {entries.map((e) => (
                  <li key={e.path}>
                    <button
                      type="button"
                      className={`fb-entry ${e.is_dir ? "fb-dir" : "fb-file"}`}
                      onClick={() => handleSelect(e)}
                    >
                      {e.is_dir ? `📁 ${e.name}` : `📄 ${e.name}`}
                    </button>
                  </li>
                ))}
                {entries.length === 0 && !parent && (
                  <li className="fb-empty">No matching files</li>
                )}
              </ul>
            )}
          </div>
        </div>
      )}
    </label>
  );
}
