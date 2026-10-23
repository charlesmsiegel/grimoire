import { useState } from "react";
import type { ExpressionMutation, ExpressionOutfit } from "../../api/client";

export function OutfitEditor({ id, outfit, busy, mutate }: { id: string; outfit: ExpressionOutfit; busy: boolean;
  mutate: (mutations: ExpressionMutation[]) => Promise<void> }) {
  const [label, setLabel] = useState(outfit.label);
  const [aliases, setAliases] = useState(outfit.clothing_aliases.join("\n"));
  const [fallback, setFallback] = useState(outfit.fallback_expression);
  const [presentation, setPresentation] = useState(outfit.presentation);
  const [copy, setCopy] = useState("");
  return <fieldset disabled={busy} className="expression-outfit-editor">
    <legend>Outfit settings</legend>
    <p className="field-hint">Outfit ID: {id}</p>
    <label>Outfit name<input value={label} onChange={e => setLabel(e.target.value)} /></label>
    <label>Clothing aliases (one per line)<textarea value={aliases} onChange={e => setAliases(e.target.value)} rows={2} /></label>
    <label>Fallback expression<select value={fallback} onChange={e => setFallback(e.target.value)}>
      {[...new Set(["neutral", fallback, ...Object.keys(outfit.expressions)])].map(value => <option key={value}>{value}</option>)}
    </select></label>
    {(["scale", "offset_x", "offset_y"] as const).map(field => <label key={field}>
      {field === "scale" ? "Sprite scale" : field === "offset_x" ? "Horizontal alignment" : "Vertical alignment"}
      <input type="number" min={field === "scale" ? 0.1 : -1} max={field === "scale" ? 4 : 1} step="0.05"
        value={presentation[field]} onChange={e => setPresentation({ ...presentation, [field]: Number(e.target.value) })} />
    </label>)}
    <button disabled={!label.trim()} onClick={() => void mutate([{ op: "patch-outfit", outfit: id,
      metadata: { label, clothing_aliases: aliases.split("\n").map(s => s.trim()).filter(Boolean), fallback_expression: fallback, presentation } }])}>Save outfit</button>
    <div className="expression-actions">
      <button onClick={() => void mutate([{ op: "set-default", outfit: id }])}>Use as default</button>
      <button onClick={() => void mutate([{ op: "hide-outfit", outfit: id }])}>Hide outfit</button>
      <button onClick={() => { if (window.confirm(`Remove the local outfit “${label}”? Inherited content will be revealed.`)) void mutate([{ op: "remove-outfit", outfit: id }]); }}>Remove local outfit</button>
    </div>
    <label>New outfit ID for duplicate<input value={copy} onChange={e => setCopy(e.target.value)} /></label>
    <button disabled={!/^[a-z][a-z0-9_-]{0,63}$/.test(copy)} onClick={() => void mutate([{ op: "duplicate-outfit", outfit: id, new_outfit: copy }])}>Duplicate outfit</button>
  </fieldset>;
}
