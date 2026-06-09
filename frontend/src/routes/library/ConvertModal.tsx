import { useEffect, useMemo, useState } from "react";
import { Dialog, DialogClose } from "../../components/Dialog";

import {
  ApiError,
  type EntityKind,
  libraryApi,
  type ReclassificationPreview,
} from "../../api/library";

interface Props {
  worldId: string;
  sourceId: string;
  /** Optional initial target — defaults to the heuristic suggestion. */
  initialTargetKind?: EntityKind;
  onClose: () => void;
  onConverted: (targetKind: EntityKind, targetId: string) => void;
}

const TARGETS: EntityKind[] = ["character", "location", "faction", "item"];

export function ConvertModal({
  worldId,
  sourceId,
  initialTargetKind,
  onClose,
  onConverted,
}: Props) {
  const [targetKind, setTargetKind] = useState<EntityKind>(initialTargetKind ?? "character");
  const [preview, setPreview] = useState<ReclassificationPreview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [overrides, setOverrides] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    libraryApi
      .previewReclassify(worldId, sourceId, targetKind)
      .then((p) => {
        if (cancelled) return;
        setPreview(p);
        // Seed the dropdown from the heuristic the first time only.
        if (
          !initialTargetKind &&
          p.suggestion.kind !== "lore" &&
          p.suggestion.kind !== targetKind
        ) {
          setTargetKind(p.suggestion.kind as EntityKind);
        }
        setOverrides({});
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof ApiError ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [worldId, sourceId, targetKind]);

  const requiredFilled = useMemo(() => {
    if (!preview) return false;
    return preview.required_overrides.every((k) => (overrides[k] ?? "").trim() !== "");
  }, [preview, overrides]);

  async function submit() {
    if (!preview) return;
    setBusy(true);
    setError(null);
    try {
      const cleaned: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(overrides)) {
        if (v.trim() !== "") cleaned[k] = v.trim();
      }
      const result = await libraryApi.commitReclassify(worldId, sourceId, {
        target_kind: targetKind,
        overrides: cleaned,
      });
      onConverted(targetKind, result.target_id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog
      open
      onClose={onClose}
      title={`Convert ${sourceId}`}
      panelClassName="library-convert-modal"
    >
      <DialogClose />

      <label>
        <span>Target kind</span>
        <select value={targetKind} onChange={(e) => setTargetKind(e.target.value as EntityKind)}>
          {TARGETS.map((k) => (
            <option key={k} value={k}>
              {k}
            </option>
          ))}
        </select>
      </label>

      {loading && <p>Loading preview…</p>}
      {error && <p role="alert">{error}</p>}

      {preview && !loading && (
        <>
          {preview.suggestion.kind !== "lore" && (
            <p className="convert-suggestion">
              Heuristic suggests <strong>{preview.suggestion.kind}</strong>:{" "}
              {preview.suggestion.reason} ({(preview.suggestion.confidence * 100).toFixed(0)}%)
            </p>
          )}

          <section aria-label="Mapping preview">
            <h4>Will write</h4>
            <pre>{JSON.stringify(preview.frontmatter, null, 2)}</pre>
            <h4>Body</h4>
            <pre>{preview.body}</pre>
          </section>

          {preview.required_overrides.length > 0 && (
            <section aria-label="Required fields">
              <h4>Required</h4>
              {preview.required_overrides.map((key) => (
                <label key={key}>
                  <span>{key}</span>
                  <input
                    value={overrides[key] ?? ""}
                    onChange={(e) =>
                      setOverrides((prev) => ({
                        ...prev,
                        [key]: e.target.value,
                      }))
                    }
                    required
                  />
                </label>
              ))}
            </section>
          )}

          {preview.dropped.length > 0 && (
            <section aria-label="Discarded fields">
              <h4>Dropped</h4>
              <ul>
                {preview.dropped.map((k) => (
                  <li key={k}>{k}</li>
                ))}
              </ul>
              {preview.warnings.map((w, i) => (
                <p key={i} className="convert-warning">
                  {w}
                </p>
              ))}
            </section>
          )}

          <footer>
            <button type="button" onClick={onClose} disabled={busy}>
              Cancel
            </button>
            <button type="button" onClick={submit} disabled={busy || !requiredFilled}>
              Convert
            </button>
          </footer>
        </>
      )}
    </Dialog>
  );
}
