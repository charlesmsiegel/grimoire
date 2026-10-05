import type { Actor } from "../../api/client";

type Member = Pick<Actor, "kind" | "id" | "name" | "role">;

/** One-tap "reply as": Grimoire, plus each NPC in the scene.
 *
 *  A chip replies immediately rather than selecting a speaker for a later
 *  button, so the composer stays a single gesture. A sitting-out NPC is dimmed
 *  but never disabled: sitting out only removes them from the automatic order,
 *  and the player tapping their name is the explicit override. */
export default function ReplyChips(
  { cast, sittingOut, disabled, onReply }: {
    cast: Member[];
    sittingOut: string[];
    disabled: boolean;
    onReply: (ref: string) => void;
  },
) {
  return (
    <div className="chips reply-chips" role="group" aria-label="Reply as">
      <button type="button" className="chip reply-chip" disabled={disabled}
        onClick={() => onReply("grimoire")}>Grimoire</button>
      {cast.filter((a) => a.role === "npc").map((a) => {
        const ref = `${a.kind}:${a.id}`;
        const out = sittingOut.includes(ref);
        return (
          <button key={ref} type="button" disabled={disabled}
            className={`chip reply-chip${out ? " sitting-out" : ""}`}
            title={out ? "Sitting out — tap to have them reply anyway" : undefined}
            onClick={() => onReply(ref)}>{a.name}</button>
        );
      })}
    </div>
  );
}
