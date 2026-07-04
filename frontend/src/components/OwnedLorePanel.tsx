import { useEffect, useState } from "react";
import { api, type EntityScope, type EntitySummary } from "../api/client";

/** Lists the world lore entries owned by `ownerRef`, with a shortcut to create a new one.
 *  Editing happens in the Lore tab — the callbacks route there. */
export function OwnedLorePanel({ scope, ownerRef, onOpenEntry, onNewEntry }: {
  scope: EntityScope; ownerRef: string;
  onOpenEntry: (id: string) => void; onNewEntry: () => void;
}) {
  const [owned, setOwned] = useState<EntitySummary[]>([]);
  useEffect(() => {
    api.listEntities(scope, "lore").then((items) =>
      setOwned(items.filter((e) =>
        (e.owners ?? "").split(",").map((o) => o.trim()).includes(ownerRef))),
    );
  }, [scope.kind, scope.id, ownerRef]);

  return (
    <div className="side-section owned-lore">
      <h4>Lore</h4>
      {owned.length > 0 ? (
        <div className="chips">
          {owned.map((e) => (
            <button key={e.id} className="chip" onClick={() => onOpenEntry(e.id)}>{e.name}</button>
          ))}
        </div>
      ) : (
        <div className="field-hint">No lore yet.</div>
      )}
      <button className="subtle" onClick={onNewEntry}>+ New lore</button>
    </div>
  );
}
