import { Field } from "./Field";
import { collapseSceneFocusBreaks } from "./sceneDraft";

/** A soft-wrapping paragraph, not Markdown. Keep just-typed edge spaces until
 *  submission; otherwise normal multiword typing silently joins the words. */
export function SceneFocusInput({ value, onChange, disabled = false }: {
  value: string; onChange: (value: string) => void; disabled?: boolean;
}) {
  return <Field label="Scene focus" hint="Pasted line breaks become spaces. Leave blank for open-ended play.">
    <textarea aria-label="Scene focus" className="scene-focus-paragraph" rows={3} wrap="soft"
      value={value} disabled={disabled} onChange={(e) => onChange(collapseSceneFocusBreaks(e.target.value))} />
  </Field>;
}
