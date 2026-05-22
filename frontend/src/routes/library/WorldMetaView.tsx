import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router-dom";

import { ApiError, libraryApi, type WorldMeta } from "../../api/library";
import { useResource } from "../../api/useResource";
import { AsyncBoundary } from "./AsyncBoundary";
import { WorldAtmosphereForm } from "./WorldAtmosphereForm";
import { WorldCalendarForm } from "./WorldCalendarForm";
import { WorldDefaultsForm } from "./WorldDefaultsForm";
import { parseCalendar, serializeCalendar, type WorldCalendar } from "./world-calendar";

const FIELDS: { key: keyof WorldMeta; label: string; type: "text" | "textarea" | "tags" }[] = [
  { key: "name", label: "Name", type: "text" },
  { key: "genre", label: "Genre", type: "text" },
  { key: "description", label: "Description", type: "textarea" },
  { key: "tags", label: "Tags (comma separated)", type: "tags" },
];

export function WorldMetaView() {
  const { worldId = "" } = useParams();
  const { data, loading, error, reload } = useResource(
    useCallback(() => libraryApi.getWorld(worldId), [worldId]),
  );

  const [draft, setDraft] = useState<Partial<WorldMeta>>({});
  const [calendar, setCalendar] = useState<WorldCalendar>(parseCalendar({}));
  const [atmosphere, setAtmosphere] = useState<Record<string, unknown>>({});
  const [defaults, setDefaults] = useState<Record<string, unknown>>({});
  const [saving, setSaving] = useState(false);
  const [saveErr, setSaveErr] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    if (!data) return;
    setDraft({
      name: data.name,
      genre: data.genre,
      description: data.description,
      tags: data.tags,
    });
    setCalendar(parseCalendar(data.calendar ?? {}));
    setAtmosphere((data.atmosphere ?? {}) as Record<string, unknown>);
    setDefaults((data.defaults ?? {}) as Record<string, unknown>);
    setDirty(false);
  }, [data]);

  function patch<K extends keyof WorldMeta>(key: K, value: WorldMeta[K]) {
    setDraft((d) => ({ ...d, [key]: value }));
    setDirty(true);
  }

  async function save() {
    setSaving(true);
    setSaveErr(null);
    try {
      await libraryApi.updateWorld(worldId, {
        ...draft,
        calendar: serializeCalendar(calendar),
        atmosphere,
        defaults,
      });
      setDirty(false);
      reload();
    } catch (err) {
      setSaveErr(err instanceof ApiError ? err.message : (err as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="world-meta">
      <AsyncBoundary loading={loading} error={error} onRetry={reload}>
        <div className="library-form" aria-label="World metadata">
          {FIELDS.map((field) => (
            <label key={field.key}>
              <span>{field.label}</span>
              {field.type === "textarea" ? (
                <textarea
                  rows={3}
                  value={(draft[field.key] as string) ?? ""}
                  onChange={(e) => patch(field.key as "description", e.target.value)}
                />
              ) : field.type === "tags" ? (
                <input
                  type="text"
                  value={(draft.tags ?? []).join(", ")}
                  onChange={(e) =>
                    patch(
                      "tags",
                      e.target.value
                        .split(",")
                        .map((t) => t.trim())
                        .filter(Boolean),
                    )
                  }
                />
              ) : (
                <input
                  type="text"
                  value={(draft[field.key] as string) ?? ""}
                  onChange={(e) => patch(field.key as "name", e.target.value)}
                />
              )}
            </label>
          ))}

          <WorldCalendarForm
            value={calendar}
            onChange={(next) => {
              setCalendar(next);
              setDirty(true);
            }}
          />
          <WorldAtmosphereForm
            value={atmosphere}
            onChange={(next) => {
              setAtmosphere(next);
              setDirty(true);
            }}
          />
          <WorldDefaultsForm
            value={defaults}
            onChange={(next) => {
              setDefaults(next);
              setDirty(true);
            }}
          />

          {saveErr && (
            <p className="library-error" role="alert">
              {saveErr}
            </p>
          )}
          <button onClick={save} disabled={!dirty || saving}>
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </AsyncBoundary>
    </section>
  );
}
