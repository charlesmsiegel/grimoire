import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type EntityScope, type EntitySummary } from "../api/client";

/** Lists the world lore entries owned by `ownerRef`, with a shortcut to create a new one.
 *  Editing happens in the Lore tab — these are real addresses now, so both
 *  open in a new tab like any other record link. */
export function OwnedLorePanel({ scope, ownerRef, hrefFor, newHref }: {
  scope: EntityScope; ownerRef: string;
  hrefFor: (id: string) => string; newHref: string;
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
            <Link key={e.id} className="chip" to={hrefFor(e.id)}>{e.name}</Link>
          ))}
        </div>
      ) : (
        <div className="field-hint">No lore yet.</div>
      )}
      <Link className="subtle" to={newHref}>+ New lore</Link>
    </div>
  );
}
