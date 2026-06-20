import { useEffect, useRef, useState } from "react";
import { fetchModels, priceLabel, type Model } from "../api/models";

export default function ModelCombobox({
  value,
  onChange,
}: {
  value: string;
  onChange: (id: string) => void;
}) {
  const [models, setModels] = useState<Model[]>([]);
  const [error, setError] = useState(false);
  const [open, setOpen] = useState(false);
  const [touched, setTouched] = useState(false);
  const blurTimer = useRef<number>();

  useEffect(() => {
    let alive = true;
    fetchModels()
      .then((m) => alive && setModels(m))
      .catch(() => alive && setError(true));
    return () => {
      alive = false;
    };
  }, []);

  // Show the full list on a fresh focus; narrow once the user types.
  const q = value.toLowerCase();
  const matches =
    touched && q
      ? models.filter((m) => m.id.toLowerCase().includes(q) || m.name.toLowerCase().includes(q))
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
                <span className="combobox-price">{priceLabel(m)}</span>
              </div>
              <div className="combobox-id">{m.id}</div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
