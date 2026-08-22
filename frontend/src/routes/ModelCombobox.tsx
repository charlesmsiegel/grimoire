import { useRef, useState } from "react";
import { priceLabel, contextLabel, type Model } from "../api/models";

export default function ModelCombobox({
  value,
  onChange,
  models,
  error = false,
  ariaLabel,
  placeholder,
  disabled = false,
}: {
  value: string;
  onChange: (id: string) => void;
  models: Model[];
  error?: boolean;
  /** Names the input where no `<Field>` label wraps it — the reroll popover
   *  (#77) is a gutter overlay with no room for one. */
  ariaLabel?: string;
  /** What an EMPTY value falls back to, shown in the box. Only a picker whose
   *  blank means something other than "unset" has one to offer. */
  placeholder?: string;
  /** Refuse input while the caller cannot yet say what an empty box means —
   *  the reroll picker's `placeholder` is the model its route will run, and
   *  until the route is known the control has nothing true to tell the reader
   *  and nothing to attribute what they type to (#77, Codex review). */
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [touched, setTouched] = useState(false);
  const blurTimer = useRef<number>();

  // Show the full list on a fresh focus; narrow once the user types.
  const q = value.toLowerCase();
  const matches =
    touched && q
      ? models.filter(
          (m) =>
            m.id.toLowerCase().includes(q) ||
            m.name.toLowerCase().includes(q) ||
            (m.prompt != null && m.completion != null && priceLabel(m).toLowerCase().includes(q)),
        )
      : models;

  // What is actually ON SCREEN, named once. `open` alone is not it: the list
  // also needs a model to show and no error, so a box focused against an empty
  // catalog is open-but-invisible. The key handler below gates on this rather
  // than on `open`, because review caught it swallowing Escape for a list that
  // was not there — leaving Escape a dead key for every custom endpoint with
  // nothing cached, which is most of them.
  const listShown = open && !disabled && !error && matches.length > 0;

  function select(id: string) {
    onChange(id);
    setOpen(false);
    setTouched(false);
  }

  return (
    <div className="combobox">
      <input
        type="text"
        aria-label={ariaLabel}
        placeholder={placeholder}
        disabled={disabled}
        value={value}
        onChange={(e) => {
          onChange(e.target.value);
          setTouched(true);
          setOpen(true);
        }}
        onFocus={() => {
          setTouched(false);
          setOpen(true);
        }}
        onBlur={() => {
          blurTimer.current = window.setTimeout(() => setOpen(false), 120);
        }}
        onKeyDown={(e) => {
          // Escape and Enter both act on the LIST first, and only while it is
          // on screen. Without this they bubble straight past — and the reroll
          // popover, which dismisses on Escape and commits on Enter (#77),
          // took the whole thing down, or sent the reroll with half-typed
          // text, while the reader was still choosing a model. A second press,
          // with the list shut, bubbles as before, so neither key is lost.
          if (!listShown) return;
          if (e.key === "Escape" || e.key === "Enter") {
            e.stopPropagation();
            setOpen(false);
          }
        }}
      />
      {error && (
        <div className="combobox-note">couldn’t load model list — type a model id</div>
      )}
      {listShown && (
        <ul className="combobox-list">
          {matches.map((m) => (
            <li
              key={m.id}
              className="combobox-row"
              onMouseDown={(e) => {
                e.preventDefault();
                select(m.id);
              }}
            >
              <div className="combobox-row-top">
                <span className="combobox-name">{m.name}</span>
                {m.prompt != null && m.completion != null && (
                  <span className="combobox-price">{priceLabel(m)}</span>
                )}
              </div>
              <div className="combobox-row-bottom">
                <span className="combobox-id">{m.id}</span>
                {m.context != null && m.context > 0 && (
                  <span className="combobox-ctx">{contextLabel(m.context)}</span>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
