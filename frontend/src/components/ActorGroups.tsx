import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type ActorGroup, type EntityScope } from "../api/client";
import { sectionHref } from "../worldPaths";
import { ColumnSection } from "./PageShell";

/** Read-only affiliations. A request belongs to one scope AND actor: even
 * before effect cleanup, the previous answer cannot draw on the next record. */
export function ActorGroups({ scope, actor }: { scope: EntityScope; actor: string }) {
  const { kind, id } = scope;
  const key = JSON.stringify([kind, id, actor]);
  const [loaded, setLoaded] = useState<{ key: string; groups: ActorGroup[] } | null>(null);
  useEffect(() => {
    let live = true;
    void api.getActorMembership({ kind, id }, actor).then(
      (answer) => { if (live) setLoaded({ key, groups: answer.groups }); },
      () => { if (live) setLoaded({ key, groups: [] }); },
    );
    return () => { live = false; };
  }, [kind, id, actor, key]);
  if (loaded?.key !== key || loaded.groups.length === 0) return null;
  return <ColumnSection label="Groups"><div className="chips">
    {loaded.groups.map((group) => <Link key={group.ref} className="chip on"
      to={sectionHref(scope, { kind: "record", at: "groups", rid: group.ref.slice("groups:".length) })}>
      {group.name}{group.roles.includes("leader") ? " (leads)" : ""}
    </Link>)}
  </div></ColumnSection>;
}
