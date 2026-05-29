/** Dropdown over a fixed option set. Tolerates an out-of-set current value. */
export function EnumSelect({
  value,
  options,
  onChange,
}: {
  value: string;
  options: { value: string; label: string }[];
  onChange: (next: string) => void;
}) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)}>
      {!options.some((o) => o.value === value) && value !== "" && (
        <option value={value}>{value}</option>
      )}
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  );
}
