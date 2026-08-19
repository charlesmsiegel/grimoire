import { api, type Actor, type RosterEntry } from "../api/client";

/** "In this scene": who is seated, at the version this campaign locked for
 *  them. Split out of `CastPanel`. */
export function SceneCastList({ cid, cast, roster }: {
  cid: string;
  cast: Actor[];
  /** The campaign's appearance record, which is where an actor's locked
   *  version lives — the cast row itself does not carry one. PCs are in it
   *  too, so they get a portrait the same way characters do. */
  roster: RosterEntry[];
}) {
  return (
    <div>
      <div className="role">In this scene</div>
      {cast.length === 0 && <div className="field-hint">No one cast yet.</div>}
      {cast.map((a) => {
        const ver = roster.find((r) => r.kind === a.kind && r.id === a.id)?.version;
        return (
          <div className="cast-row" key={`${a.kind}/${a.id}`}>
            {ver
              ? <img className="row-avatar" alt={`${a.id} avatar`}
                     src={api.actorImageUrl({ kind: "campaign", id: cid },
                                            a.kind, a.id, ver, "avatar")}
                     onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = "none"; }} />
              : null}
            <span>{a.id}</span>
            <span className="role">{a.kind === "pcs" ? "PC" : "character"} · {a.role}</span>
          </div>
        );
      })}
    </div>
  );
}
