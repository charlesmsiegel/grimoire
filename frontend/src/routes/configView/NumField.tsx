/** One numeric setting. A component rather than nineteen copies of the same
 *  five lines — and declared at module scope, not inside `ConfigView`, because
 *  a component defined during render is a new type every render and React
 *  would remount the input on each keystroke, taking the caret with it. */
export function NumField(
  { id, label, value, placeholder, unit, caption, decimal = false, disabled = false, onChange }: {
    id: string; label: string; value: string; placeholder?: string;
    unit?: string; caption?: string; decimal?: boolean; disabled?: boolean;
    onChange: (next: string) => void;
  },
) {
  return (
    <div className="config-field">
      <label htmlFor={id}>{label}</label>
      <div className="config-input">
        <input id={id} type="text" inputMode={decimal ? "decimal" : "numeric"}
               value={value} placeholder={placeholder} disabled={disabled}
               onChange={(e) => onChange(e.target.value)} />
        {unit && <span className="config-unit" aria-hidden>{unit}</span>}
      </div>
      {caption && <p className="config-caption">{caption}</p>}
    </div>
  );
}

