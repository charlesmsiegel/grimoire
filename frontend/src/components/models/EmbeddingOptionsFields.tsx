import type { EmbeddingInputMode } from "../../api/types";
import { Field } from "../Field";
import { dimensionsTyped, suggestFor, type EmbeddingForm } from "./embeddingOptions";

/** The facts form's "Embedding options" section (roadmap 01h): how the model
 *  is told a text is a query or a document, and a requested width. Suggest
 *  fills the boxes from the conventions its authors publish; it saves
 *  nothing, and the save that follows is what asks to re-embed. */
export function EmbeddingOptionsFields({ model, value, onChange, disabled }: {
  model: string; value: EmbeddingForm; onChange: (next: EmbeddingForm) => void;
  disabled?: boolean;
}) {
  const set = (patch: Partial<EmbeddingForm>) => onChange({ ...value, ...patch });
  const suggestion = suggestFor(model);
  return (
    <>
      <h4>Embedding options</h4>
      <div className="field-hint">
        How this model is told whether a text is a search or a passage, and the width of the
        vectors it returns. Changing what documents are sent with re-embeds the library.
      </div>
      <div className="form-actions">
        <button type="button" className="subtle" disabled={disabled || !suggestion}
                onClick={() => suggestion && set(suggestion.fill)}>
          Suggest
        </button>
        <span className="field-hint">
          {suggestion
            ? `Fills in what ${suggestion.family} models are published to expect. Nothing is saved until you save.`
            : "No published convention is known for this model."}
        </span>
      </div>
      <Field label="Input type">
        <select value={value.input} disabled={disabled}
                onChange={(e) => set({ input: e.target.value as EmbeddingInputMode })}>
          <option value="none">None — texts are sent as they are</option>
          <option value="prefix">Prefix — a text before each input</option>
          <option value="param">Request field — one type per request</option>
        </select>
      </Field>
      {value.input === "prefix" && (
        <>
          <Field label="Query prefix">
            <textarea rows={2} value={value.query_prefix} disabled={disabled}
                      onChange={(e) => set({ query_prefix: e.target.value })} />
          </Field>
          <Field label="Document prefix">
            <textarea rows={2} value={value.document_prefix} disabled={disabled}
                      onChange={(e) => set({ document_prefix: e.target.value })} />
          </Field>
        </>
      )}
      {value.input === "param" && (
        <>
          <Field label="Request field" hint="A top-level field of the request, such as input_type.">
            <input value={value.param_field} disabled={disabled}
                   onChange={(e) => set({ param_field: e.target.value })} />
          </Field>
          <Field label="Query value">
            <input value={value.query_value} disabled={disabled}
                   onChange={(e) => set({ query_value: e.target.value })} />
          </Field>
          <Field label="Document value"
                 hint="Queries and documents go in separate requests, so a recall costs two.">
            <input value={value.document_value} disabled={disabled}
                   onChange={(e) => set({ document_value: e.target.value })} />
          </Field>
        </>
      )}
      <Field label="Dimensions"
             hint="Empty for the model's own width. An endpoint that ignores the field fails the call.">
        <input type="number" min={1} step={1} inputMode="numeric" placeholder="Model default"
               value={value.dimensions} disabled={disabled}
               onChange={(e) => set({ dimensions: e.target.value })} />
      </Field>
      {value.dimensions.trim() !== "" && (
        <Field label="Dimensions field">
          <input value={value.dimensions_field} disabled={disabled}
                 onChange={(e) => set({ dimensions_field: e.target.value })} />
        </Field>
      )}
      {!dimensionsTyped(value) && (
        <div className="field-hint error">Dimensions are a whole number.</div>
      )}
    </>
  );
}
