import type { TrackerActor, TrackerAware, TrackerField } from "../../api/client";

/** Who knows a value, as the tag beside it: nothing for "everyone present". */
export function awareTag(aware: TrackerAware, names: Record<string, string>): string {
  if (aware === "present") return "";
  if (aware.length === 0) return "private";
  return `known to: ${aware.map((ref) => names[ref] ?? ref).join(", ")}`;
}

export function valueText(value: string | string[]): string {
  return Array.isArray(value) ? value.join(", ") : value;
}

/** One actor's tracked values, read-only. A field this post's turn moved is
 *  marked `changed`; a stored value whose field was since removed from the
 *  definitions still prints, under its key. */
export function TrackerValues({ actorRef, actor, fields, names, changed }: {
  actorRef: string; actor: TrackerActor; fields: TrackerField[];
  names: Record<string, string>;
  /** Field keys this post changed on this actor. */
  changed: ReadonlySet<string>;
}) {
  const known = new Set(fields.map((f) => f.key));
  const rows = [
    ...fields.map((f) => ({ key: f.key, label: f.label })),
    ...Object.keys(actor.fields).filter((k) => !known.has(k)).map((k) => ({ key: k, label: k })),
  ].filter((r) => actor.fields[r.key] !== undefined);
  return (
    <div className="tracker-actor">
      <h5>{names[actorRef] ?? actorRef}{actor.present ? "" : " (not present)"}</h5>
      {rows.length === 0 && <p className="field-hint">Nothing tracked.</p>}
      <ul>
        {rows.map(({ key, label }) => {
          const v = actor.fields[key];
          const tag = awareTag(v.aware, names);
          const text = valueText(v.value);
          return (
            <li key={key} className={changed.has(key) ? "changed" : undefined}>
              <span className="tracker-label">{label}</span>
              {": "}{text === "" ? "—" : text}
              {tag && <span className="chip on">{tag}</span>}
              {v.set_by === "user" && <span className="field-hint"> (edited)</span>}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
