import { type HeldEntryRow, type LoreReason, type OwnerPresence } from "../api/types";

/** A reason in words, for the context inspector (spec §10).
 *
 *  Describes a reason dict the backend already built (`store/context/
 *  activation.py`), so the live composition and a sent turn's capture read the
 *  same way. `names` is the row's own map of ref -> display name; a ref it
 *  does not carry names itself, so a record deleted since the capture still
 *  reads as something.
 *
 *  An owner is appended after the main text. A keyless owned entry shows only
 *  the owner: "always on" would hide why it is in, and the owner is why.
 */
export function describeReason(reason: LoreReason, names: Record<string, string>): string {
  const name = (ref: string) => names[ref] ?? ref;
  const owner = describeOwner(reason, name);
  if (reason.type === "keyless" && owner) return owner;
  const main = describeMain(reason, name);
  return owner ? `${main} · ${owner}` : main;
}

/** What an entry the packer held back says: today only a cooldown. */
export function describeHeld(reason: HeldEntryRow["reason"]): string {
  return `on cooldown — ${posts(reason.remaining)}`;
}

function posts(n: number): string {
  return `${n} ${n === 1 ? "post" : "posts"}`;
}

function describeMain(reason: LoreReason, name: (ref: string) => string): string {
  switch (reason.type) {
    case "key": {
      const keys = reason.secondary ? `'${reason.key}' + '${reason.secondary}'` : `'${reason.key}'`;
      if (reason.post != null) return `key ${keys} in post #${reason.post}`;
      if (reason.seed) return `key ${keys} in this turn's note`;
      return `key ${keys}`;
    }
    case "keyless":
      return "always on";
    case "pinned":
      return "pinned";
    case "sticky": {
      const left = `sticky — ${posts(reason.remaining)} left`;
      return reason.from_post != null ? `${left} (from post #${reason.from_post})` : left;
    }
    case "recursion":
      return `pulled in by ${name(reason.via)}`;
    case "recall":
      return `recalled (similarity ${reason.score.toFixed(2)})`;
    default:
      // A reason type a newer backend grew; say what it is rather than nothing.
      return typeof (reason as { type?: unknown }).type === "string"
        ? (reason as { type: string }).type : "unknown";
  }
}

function describeOwner(reason: LoreReason, name: (ref: string) => string): string | null {
  if (!reason.owner) return null;
  const how = describePresence(reason.owner_presence, name);
  return `owner present: ${name(reason.owner)}${how ? ` (${how})` : ""}`;
}

function describePresence(p: OwnerPresence | undefined, name: (ref: string) => string): string | null {
  if (!p) return null;
  const via = p.via ? name(p.via) : null;
  switch (p.type) {
    case "held_by": return via ? `held by ${via}` : "held";
    case "led_by": return via ? `led by ${via}` : "led";
    case "headquarters": return "headquartered here";
    case "habitat": return "lives here";
    case "current_location": return "here";
    case "activated": return "mentioned";
    default: return null; // cast: the owner's name says it
  }
}
