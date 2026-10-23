import { api, type ExpressionMutation, type ExpressionOutfit, type ExpressionTarget } from "../../api/client";

export function ExpressionGrid({ target, id, outfit, labels, editing, busy, upload, mutate, preview }: {
  target: ExpressionTarget; id: string; outfit: ExpressionOutfit; labels: string[]; editing: boolean; busy: boolean;
  upload: (outfit: string, label: string, file: File) => Promise<void>;
  mutate: (mutations: ExpressionMutation[]) => Promise<void>; preview: (label: string) => void;
}) {
  return <div className="expression-grid">{labels.map(label => {
    const row = outfit.expressions[label];
    return <div className="expression-slot" key={label}>
      <button className="expression-image" disabled={!row?.available} aria-label={`Preview ${label}`} onClick={() => preview(label)}>
        {row?.available ? <img loading="lazy" src={api.expressionImageUrl(target, id, label, row.token)} alt={label} /> : <span>{row ? "Image missing" : "No sprite"}</span>}
      </button>
      <strong>{label}</strong><span className="field-hint">{row?.source ?? "Missing"}</span>
      {editing && <>
        <label className="expression-upload">{row ? "Replace" : "Upload"}<input aria-label={`Upload ${label}`} type="file" accept="image/png,image/webp,image/jpeg,image/gif"
          disabled={busy} onChange={e => { const file = e.target.files?.[0]; e.target.value = ""; if (file) void upload(id, label, file); }} /></label>
        {row && <div className="expression-actions">
          {row.source !== "inherited" && <button disabled={busy} onClick={() => void mutate([{ op: "remove-expression", outfit: id, expression: label }])}>Remove {label} override</button>}
          <button disabled={busy} onClick={() => void mutate([{ op: "hide-expression", outfit: id, expression: label }])}>Hide {label}</button>
        </div>}
      </>}
    </div>;
  })}</div>;
}
