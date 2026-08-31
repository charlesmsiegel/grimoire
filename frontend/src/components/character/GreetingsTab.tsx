import { GreetingMarkdown } from "../GreetingMarkdown";
import { EditableField } from "./EditableField";

/** The card's own greetings: the first message and the alternates.
 *
 *  World greetings that merely *feature* this character are separate records
 *  and live on the Card tab under their own label — they are not on the card,
 *  so they are not counted by this tab either.
 */
export function GreetingsTab(
  { name, firstMes, greetings, editing, onEditingChange, onSaveFirstMes, onSaveGreetings }: {
    name: string;
    firstMes: string;
    greetings: string[];
    editing: string | null;
    onEditingChange: (key: string | null) => void;
    onSaveFirstMes: (next: string) => Promise<boolean>;
    onSaveGreetings: (next: string[]) => Promise<boolean>;
  },
) {
  const empty = !firstMes.trim() && greetings.length === 0;

  return <>
    <EditableField
      label="First message"
      value={firstMes}
      placeholder="No first message."
      rendered={<GreetingMarkdown>{firstMes}</GreetingMarkdown>}
      editing={editing === "greetings:first"}
      onEditingChange={(open) => onEditingChange(open ? "greetings:first" : null)}
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
        onSave={(next) => onSaveGreetings(greetings.map((x, j) => (j === i ? next : x)))}
      />
    ))}

    <div className="form-actions">
      <button className="subtle" type="button"
              onClick={() => {
                // Appended empty and opened for editing straight away, rather
                // than saved blank: `buildCard` drops empty greetings on the
                // way out, so a blank one saved now would not survive the round
                // trip it was created by.
                onEditingChange(`greetings:${greetings.length}`);
                void onSaveGreetings([...greetings, " "]);
              }}>
        + Add greeting
      </button>
      {greetings.length > 0 && (
        <button className="subtle" type="button"
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
