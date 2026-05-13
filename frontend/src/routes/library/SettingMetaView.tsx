import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";

import { ApiError, libraryApi, type SettingMeta } from "../../api/library";
import { useResource } from "../../api/useResource";
import { AsyncBoundary } from "./AsyncBoundary";

const FIELDS: { key: keyof SettingMeta; label: string; type: "text" | "textarea" | "tags" }[] = [
  { key: "name", label: "Name", type: "text" },
  { key: "genre", label: "Genre", type: "text" },
  { key: "description", label: "Description", type: "textarea" },
  { key: "tags", label: "Tags (comma separated)", type: "tags" },
];

export function SettingMetaView() {
  const { settingId = "" } = useParams();
  const { data, loading, error, reload } = useResource(
    () => libraryApi.getSetting(settingId),
    [settingId],
  );

  const [draft, setDraft] = useState<Partial<SettingMeta>>({});
  const [calendar, setCalendar] = useState("");
  const [atmosphere, setAtmosphere] = useState("");
  const [defaults, setDefaults] = useState("");
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
    setCalendar(JSON.stringify(data.calendar ?? {}, null, 2));
    setAtmosphere(JSON.stringify(data.atmosphere ?? {}, null, 2));
    setDefaults(JSON.stringify(data.defaults ?? {}, null, 2));
    setDirty(false);
  }, [data]);

  function patch<K extends keyof SettingMeta>(key: K, value: SettingMeta[K]) {
    setDraft((d) => ({ ...d, [key]: value }));
    setDirty(true);
  }

  async function save() {
    setSaving(true);
    setSaveErr(null);
    try {
      const body: Record<string, unknown> = { ...draft };
      try {
        body.calendar = JSON.parse(calendar || "{}");
        body.atmosphere = JSON.parse(atmosphere || "{}");
        body.defaults = JSON.parse(defaults || "{}");
      } catch (parseErr) {
        throw new Error(
          `JSON parse error in calendar/atmosphere/defaults: ${(parseErr as Error).message}`,
        );
      }
      await libraryApi.updateSetting(settingId, body);
      setDirty(false);
      reload();
    } catch (err) {
      setSaveErr(err instanceof ApiError ? err.message : (err as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="setting-meta">
      <AsyncBoundary loading={loading} error={error} onRetry={reload}>
        <div className="library-form" aria-label="Setting metadata">
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

          <label>
            <span>Calendar (JSON)</span>
            <textarea
              rows={6}
              value={calendar}
              onChange={(e) => {
                setCalendar(e.target.value);
                setDirty(true);
              }}
            />
          </label>
          <label>
            <span>Atmosphere (JSON)</span>
            <textarea
              rows={4}
              value={atmosphere}
              onChange={(e) => {
                setAtmosphere(e.target.value);
                setDirty(true);
              }}
            />
          </label>
          <label>
            <span>Defaults (JSON)</span>
            <textarea
              rows={4}
              value={defaults}
              onChange={(e) => {
                setDefaults(e.target.value);
                setDirty(true);
              }}
            />
          </label>

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
