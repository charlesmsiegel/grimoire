import { useRef, useState } from "react";
import { priceLabel, contextLabel, type Model } from "../api/models";

export default function ModelCombobox({
  value,
  onChange,
  models,
  error = false,
  ariaLabel,
  placeholder,
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
      />
      {error && (
        <div className="combobox-note">couldn’t load model list — type a model id</div>
      )}
      {open && !error && matches.length > 0 && (
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
