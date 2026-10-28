import { useEffect, useRef } from "react";

/** Mounted only after Rename is chosen. Typing or a background read must not steal focus. */
export function SceneRenameInput({ value, onChange, onCommit, onCancel }: {
  value: string;
  onChange: (title: string) => void;
  onCommit: () => void;
  onCancel: () => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => { input.current?.focus(); }, []);
  return <input ref={input} className="row-rename scene-rename" aria-label="Rename scene"
    value={value} onChange={(event) => onChange(event.target.value)}
    onKeyDown={(event) => {
      // An inline Enter is claimed, not shared with the scene's Send shortcut.
      if (event.key === "Enter") { event.preventDefault(); onCommit(); }
      if (event.key === "Escape") onCancel();
    }} />;
}
