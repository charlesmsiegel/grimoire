import { useState } from "react";
import { GreetingMarkdown } from "../GreetingMarkdown";
import { EditableField } from "./EditableField";

/** The card's own greetings: the first message and the alternates.
 *
 *  World greetings that merely *feature* this character are separate records
 *  and live on the Card tab under their own label — they are not on the card,
 *  so they are not counted by this tab either.
 */
export function GreetingsTab(
  { name, firstMes, greetings, editing, onEditingChange, busy, onSaveFirstMes, onSaveGreetings }: {
    name: string;
    firstMes: string;
    greetings: string[];
    editing: string | null;
    onEditingChange: (key: string | null) => void;
    busy: boolean;
    onSaveFirstMes: (next: string) => Promise<boolean>;
    onSaveGreetings: (next: string[]) => Promise<boolean>;
  },
) {
  /** A greeting being written that the card does not have yet.
   *
   *  It cannot be added by saving a placeholder first: `buildCard` drops blank
   *  greetings on the way out — deliberately, so an emptied one is removed —
   *  so a blank saved now would not survive the round trip that created it,
   *  and the row would vanish under whatever had been typed into it. The row
   *  lives here until it has content worth storing. */
  const [adding, setAdding] = useState(false);
  const empty = !firstMes.trim() && greetings.length === 0 && !adding;

  return <>
    <EditableField
      label="First message"
      value={firstMes}
      placeholder="No first message."
      rendered={<GreetingMarkdown>{firstMes}</GreetingMarkdown>}
      editing={editing === "greetings:first"}
      onEditingChange={(open) => onEditingChange(open ? "greetings:first" : null)}
      disabled={busy}
      onSave={onSaveFirstMes}
    />

    {greetings.map((g, i) => (
      <EditableField
        // The position IS a greeting's identity — the card stores a bare list
        // with no ids, and this UI only edits in place, appends and drops the
        // last, so an index can never point at a different greeting than the
        // one it pointed at before.
        // eslint-disable-next-line react/no-array-index-key
        key={i}
        label={`Alternate greeting ${i + 1}`}
        value={g}
        rendered={<blockquote className="greeting-quote"><GreetingMarkdown>{g}</GreetingMarkdown></blockquote>}
        editing={editing === `greetings:${i}`}
        onEditingChange={(open) => onEditingChange(open ? `greetings:${i}` : null)}
        disabled={busy}
        onSave={(next) => onSaveGreetings(greetings.map((x, j) => (j === i ? next : x)))}
      />
    ))}

    {adding && (
      <EditableField
        label={`Alternate greeting ${greetings.length + 1}`}
        value=""
        placeholder="Nothing written yet."
        editing={editing === "greetings:new"}
        onEditingChange={(open) => {
          onEditingChange(open ? "greetings:new" : null);
          if (!open) setAdding(false);      // cancelling drops the row entirely
        }}
        disabled={busy}
        onSave={async (next) => {
          if (!next.trim()) { setAdding(false); return true; }
          const ok = await onSaveGreetings([...greetings, next]);
          if (ok) setAdding(false);
          return ok;
        }}
      />
    )}

    <div className="form-actions">
      <button className="subtle" type="button" disabled={busy || adding}
              onClick={() => { setAdding(true); onEditingChange("greetings:new"); }}>
        + Add greeting
      </button>
      {greetings.length > 0 && (
        <button className="subtle" type="button" disabled={busy}
                onClick={() => {
                  if (!window.confirm(`Remove alternate greeting ${greetings.length}?`)) return;
                  void onSaveGreetings(greetings.slice(0, -1));
                }}>
          Remove last
        </button>
      )}
    </div>

    {empty && (
      <p className="empty-state">
        No greetings on this card. A greeting is the <span className="empty-what">opening
        a scene can start from</span> — add one here, or import a card that carries
        some. World greetings that merely feature {name} are listed on the Card tab.
      </p>
    )}
  </>;
}

export default GreetingsTab;
