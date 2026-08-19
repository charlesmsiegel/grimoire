import { api, type Actor, type Briefing, type RosterEntry } from "../../api/client";
import { Portrait } from "../Portrait";
import CastChanges from "./CastChanges";

/** Where an actor stands in the scene in front of you. */
export type CastState = "PLAYER" | "IN SCENE";

export type CastTile = {
  kind: string;
  id: string;
  name: string;
  version: string;
  state: CastState;
};

/** The scene's cast, in cast order, as one tile each.
 *
 *  The grid is the room, and only the room. The campaign's wider roster is a
 *  browsable list of records and belongs on the Characters page; folding it in
 *  here buried the two or three people actually on stage under everyone the
 *  campaign has ever met, which is the opposite of what a column beside the
 *  transcript is for.
 *
 *  `roster` is still read, for one thing: an actor's portrait resolves off the
 *  version this campaign *locked*, which the scene's cast record does not
 *  carry.
 *
 *  Exported for its own test — it is much easier to state as a function than to
 *  reach through a rendered grid of portraits. */
export function tiers(cast: Actor[], roster: RosterEntry[]): CastTile[] {
  return cast.map((a) => ({
    kind: a.kind, id: a.id, name: a.name,
    version: roster.find((r) => r.kind === a.kind && r.id === a.id)?.version ?? "",
    state: a.role === "player" ? "PLAYER" : "IN SCENE",
  }));
}

function Tile(
  { cid, tile, onOpen }: { cid: string; tile: CastTile; onOpen: () => void },
) {
  const src = tile.kind === "characters" && tile.version
    ? api.campaignImageUrl(cid, tile.id, tile.version, "avatar")
    : null;
  return (
    <div className="cast-tile">
      {/* The portrait and the name are one control with two halves rather than
          two controls: a portrait you can click and a name you cannot is a
          hit target that changes shape depending on where the art ends. */}
      <button className="cast-tile-art" onClick={onOpen} tabIndex={-1} aria-hidden>
        <Portrait src={src} name={tile.name} />
      </button>
      <button className="cast-tile-name" onClick={onOpen}>
        <span className="cast-name">{tile.name}</span>
        <span className="cast-state">{tile.state}</span>
      </button>
    </div>
  );
}

/** The context column's default state: who is here, what is open, what is owed.
 *
 *  All three used to be behind a toggle — the cast in an inspector panel, the
 *  threads and commitments in a briefing you opened before the scene and then
 *  closed. Continuity is the thing this app is *for*, so it sits beside the
 *  transcript permanently and nothing has to be reopened to check it. */
export default function CastColumn(
  { cid, sid, hasPosts, refreshKey, cast, roster, briefing, onOpen, onCastChanged }: {
    cid: string;
    /** The open scene, or "" when none is. The cast-change scan is per scene. */
    sid: string;
    /** Whether the open scene has any posts, and the parent's scene-read
     *  counter: between them, whether the cast-change scan runs and when it
     *  re-runs. */
    hasPosts: boolean;
    refreshKey: number;
    cast: Actor[];
    roster: RosterEntry[];
    briefing: Briefing | null;
    onOpen: (kind: string, id: string) => void;
    onCastChanged: () => void;
  },
) {
  const tiles = tiers(cast, roster);
  const threads = briefing?.plot ?? [];
  const owed = briefing?.commitments ?? [];

  return (
    <>
      <div className="cast-grid">
        {tiles.map((t) => (
          <Tile key={`${t.kind}/${t.id}`} cid={cid} tile={t}
                onOpen={() => onOpen(t.kind, t.id)} />
        ))}
      </div>
      {tiles.length === 0 && (
        <p className="column-empty">
          Nobody is in this scene yet. Cast someone to begin.
        </p>
      )}

      {/* Directly under the grid: what the last turn says the grid should be. */}
      {sid && <CastChanges cid={cid} sid={sid} hasPosts={hasPosts} refreshKey={refreshKey}
                           onChanged={onCastChanged} />}

      <div className="column-section">
        <div className="column-section-head">
          <span className="section-label">Threads</span>
          <span className="column-count">plot.json</span>
        </div>
        {threads.length === 0 && <p className="column-empty">Nothing open.</p>}
        {threads.map((t) => (
          <div className="brief-row" key={t.id}>
            <div className="brief-title">{t.title}</div>
            <div className="brief-meta">
              <span className="brief-status">{t.status.toUpperCase()}</span>
              {t.last_scene && <span> · {t.last_scene}</span>}
            </div>
          </div>
        ))}
      </div>

      <div className="column-section">
        <div className="column-section-head">
          <span className="section-label">Owed</span>
          <span className="column-count">commitments.json</span>
        </div>
        {owed.length === 0 && <p className="column-empty">Nothing owed.</p>}
        {owed.map((c) => (
          <div className="brief-row" key={c.id}>
            <div className="brief-title">{c.title}</div>
            <div className="brief-meta">
              {/* A threat is the one kind of commitment that is about to
                  happen *to* you, so it is the one that reads in --alert. */}
              <span className={"brief-status" + (c.kind === "threat" ? " alert" : "")}>
                {c.kind.toUpperCase()}
              </span>
              <span> · {c.due ? c.due : "NO DEADLINE"}</span>
            </div>
          </div>
        ))}
      </div>
    </>
  );
}
