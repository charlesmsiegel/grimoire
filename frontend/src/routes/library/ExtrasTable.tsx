/**
 * Entity-detail extras editor (narrative-extras-design §Entity-detail UI).
 *
 * Renders the cascade-resolved key/value listing with source badges and
 * inline edit. The full design also includes pin/promote menus and a
 * type-aware add-field modal; this v1 component focuses on the table view
 * + add/delete so the routes are exercised end-to-end. Pin/promote can
 * plug into the existing menu surface incrementally.
 */

import { useCallback, useEffect, useState } from "react";

import { ApiError } from "../../api/client";
import {
  type ExtraValue,
  type ExtraValueShape,
  type ExtrasMap,
  type ExtrasScope,
  deleteLibraryExtra,
  listLibraryExtras,
  promoteToLibrary,
  putLibraryExtra,
} from "../../api/extras";
import { ConfirmDestructiveDialog } from "../../components/ConfirmDestructiveDialog";
import { useDestructiveConfirm } from "../../hooks/useDestructiveConfirm";

interface Props {
  worldId: string;
  kind: string; // singular: "character", "location", ...
  entityId: string;
  /** When supplied, render the campaign-resolved cascade with source
   * badges; promote/pin actions become available. */
  campaignId?: string;
}

const SCOPE_BADGE: Record<ExtrasScope, string> = {
  library: "📚",
  "campaign-local": "🌿",
  override: "✏️",
};

const SCOPE_TITLE: Record<ExtrasScope, string> = {
  library: "Library — travels across campaigns",
  "campaign-local": "Campaign-local — emergent value",
  override: "Override — replaces the library value in this campaign",
};

export function ExtrasTable({ worldId, kind, entityId, campaignId }: Props) {
  const [rows, setRows] = useState<ExtrasMap>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await listLibraryExtras(worldId, kind, entityId);
      setRows(data);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [worldId, kind, entityId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  if (loading) return <div className="extras-table extras-loading">Loading extras…</div>;
  if (error) return <div className="extras-table extras-error">Error: {error}</div>;

  const entries = Object.entries(rows);
  return (
    <section className="extras-table">
      <header>
        <h4>Narrative extras</h4>
        <button type="button" onClick={() => setAdding(true)} disabled={adding}>
          + Add field
        </button>
      </header>
      {entries.length === 0 && !adding ? (
        <p className="empty-state">
          No extras yet. Add a free-form detail like favorite_drink, scars, dialect_notes…
        </p>
      ) : null}
      <ul className="extras-rows">
        {entries.map(([key, extra]) => (
          <ExtrasRow
            key={key}
            entryKey={key}
            extra={extra}
            worldId={worldId}
            kind={kind}
            entityId={entityId}
            campaignId={campaignId}
            onChanged={reload}
          />
        ))}
        {adding ? (
          <ExtrasAddRow
            worldId={worldId}
            kind={kind}
            entityId={entityId}
            onSaved={() => {
              setAdding(false);
              void reload();
            }}
            onCancel={() => setAdding(false)}
          />
        ) : null}
      </ul>
    </section>
  );
}

interface RowProps {
  entryKey: string;
  extra: ExtraValue;
  worldId: string;
  kind: string;
  entityId: string;
  campaignId?: string;
  onChanged: () => void;
}

function ExtrasRow({ entryKey, extra, worldId, kind, entityId, campaignId, onChanged }: RowProps) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(() => renderEditableValue(extra.value));
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function save() {
    setSaving(true);
    setErr(null);
    try {
      await putLibraryExtra(worldId, kind, entityId, entryKey, parseEditableValue(draft));
      setEditing(false);
      onChanged();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  const del = useDestructiveConfirm<string>(async (key) => {
    setSaving(true);
    try {
      await deleteLibraryExtra(worldId, kind, entityId, key);
      onChanged();
    } finally {
      setSaving(false);
    }
  });

  async function promote() {
    if (!campaignId) return;
    setSaving(true);
    try {
      await promoteToLibrary(campaignId, kind, entityId, entryKey, worldId);
      onChanged();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <li className="extras-row">
      {del.target !== null && (
        <ConfirmDestructiveDialog
          open
          title={`Delete extras key "${del.target}"?`}
          body={<p>This cannot be undone.</p>}
          busy={del.busy}
          error={del.error}
          onConfirm={del.confirm}
          onCancel={del.cancel}
        />
      )}
      <span className="extras-badge" title={SCOPE_TITLE[extra.scope]} aria-label={extra.scope}>
        {SCOPE_BADGE[extra.scope]}
      </span>
      <span className="extras-key">{entryKey}</span>
      {editing ? (
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={() => void save()}
          autoFocus
          rows={2}
        />
      ) : (
        <span className="extras-value" onClick={() => setEditing(true)}>
          {renderDisplayValue(extra.value)}
        </span>
      )}
      <span className="extras-actions">
        {extra.scope !== "library" && campaignId ? (
          <button type="button" onClick={() => void promote()} disabled={saving}>
            Promote → library
          </button>
        ) : null}
        {/* eslint-disable-next-line local/no-bespoke-delete -- extras table row action, not a card */}
        <button
          type="button"
          onClick={() => del.request(entryKey)}
          disabled={saving}
          aria-label="Delete"
        >
          ×
        </button>
      </span>
      {err ? <span className="extras-error">{err}</span> : null}
    </li>
  );
}

function ExtrasAddRow({
  worldId,
  kind,
  entityId,
  onSaved,
  onCancel,
}: {
  worldId: string;
  kind: string;
  entityId: string;
  onSaved: () => void;
  onCancel: () => void;
}) {
  const [key, setKey] = useState("");
  const [value, setValue] = useState("");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function commit() {
    if (!key.trim()) {
      setErr("Key is required");
      return;
    }
    setSaving(true);
    setErr(null);
    try {
      await putLibraryExtra(worldId, kind, entityId, key.trim(), parseEditableValue(value));
      onSaved();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <li className="extras-row extras-row-add">
      <input
        type="text"
        placeholder="key (snake_case)"
        value={key}
        onChange={(e) => setKey(e.target.value)}
        disabled={saving}
        autoFocus
      />
      <textarea
        placeholder="value (newline-separated for a list; key=value for a dict)"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        disabled={saving}
        rows={2}
      />
      <span className="extras-actions">
        <button type="button" onClick={() => void commit()} disabled={saving}>
          Save
        </button>
        <button type="button" onClick={onCancel} disabled={saving}>
          Cancel
        </button>
      </span>
      {err ? <span className="extras-error">{err}</span> : null}
    </li>
  );
}

// --------------------------------------------------------------------------
// Value display / editing helpers
// --------------------------------------------------------------------------

function renderDisplayValue(value: ExtraValueShape): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (typeof value === "string" || typeof value === "number") return String(value);
  if (Array.isArray(value)) return value.map((v) => String(v ?? "")).join("; ");
  return Object.entries(value)
    .map(([k, v]) => `${k}: ${v ?? ""}`)
    .join(", ");
}

function renderEditableValue(value: ExtraValueShape): string {
  if (value === null || value === undefined) return "";
  if (Array.isArray(value)) return value.map((v) => String(v ?? "")).join("\n");
  if (typeof value === "object") {
    return Object.entries(value)
      .map(([k, v]) => `${k}=${v ?? ""}`)
      .join("\n");
  }
  return String(value);
}

function parseEditableValue(text: string): ExtraValueShape {
  const trimmed = text.trim();
  if (!trimmed) return null;
  // Multi-line: list-of-strings, unless every line matches key=value → dict.
  const lines = trimmed
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter(Boolean);
  if (lines.length > 1) {
    if (lines.every((line) => /^[^=]+=[^=]*$/.test(line))) {
      const obj: Record<string, string> = {};
      for (const line of lines) {
        const eq = line.indexOf("=");
        const k = line.slice(0, eq).trim();
        const v = line.slice(eq + 1).trim();
        if (k) obj[k] = v;
      }
      return obj;
    }
    return lines;
  }
  // Single line: scalar string. Numeric / boolean coercion is intentionally
  // not done here -- a user typing "yes" probably means the literal string.
  return trimmed;
}
