import { useCallback, useId } from "react";

import { ENTITY_KIND_PLURAL, type EntityKind, libraryApi } from "../../../api/library";
import { useResource } from "../../../api/useResource";

interface Suggestion {
  id: string;
  label: string;
}

/**
 * Free-text input backed by a datalist of the world's entities of the target
 * kind(s). Stores the chosen `asset_id`; free text is allowed so refs may
 * point at not-yet-created entities.
 */
export function RefPicker({
  worldId,
  refKinds,
  value,
  onChange,
}: {
  worldId: string;
  refKinds: EntityKind[];
  value: string;
  onChange: (next: string) => void;
}) {
  const listId = useId();
  const { data: suggestions } = useResource(
    useCallback(async () => {
      const results: Suggestion[] = [];
      for (const kind of refKinds) {
        try {
          const entities = await libraryApi.listEntities(worldId, ENTITY_KIND_PLURAL[kind]);
          for (const e of entities) {
            const id = "asset_id" in e ? (e.asset_id as string) : (e.id as string);
            results.push({ id, label: e.name || id });
          }
        } catch {
          // Ref pickers are advisory — failure leaves the input as free text.
        }
      }
      return results;
    }, [worldId, refKinds]),
  );

  return (
    <>
      <input type="text" list={listId} value={value} onChange={(e) => onChange(e.target.value)} />
      <datalist id={listId}>
        {(suggestions ?? []).map((s) => (
          <option key={s.id} value={s.id}>
            {s.label}
          </option>
        ))}
      </datalist>
    </>
  );
}
