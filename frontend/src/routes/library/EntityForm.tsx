/**
 * Renders an entity descriptor (entitySchemas.ts) as a structured form over a
 * frontmatter dict. Known keys are owned by typed widgets; everything else
 * round-trips through the generic FrontmatterEditor under an "Advanced" section
 * so no field is ever lost. The markdown body is edited in its own panel.
 */
import type { Frontmatter, FrontmatterValue } from "./frontmatter";
import { FrontmatterEditor } from "./FrontmatterEditor";
import {
  type EntityDescriptor,
  type FieldDescriptor,
  createDefaultFields,
  managedKeys,
} from "./entitySchemas";
import { EnumSelect } from "./widgets/EnumSelect";
import { MapEditor } from "./widgets/MapEditor";
import { ObjectListEditor } from "./widgets/ObjectListEditor";
import { RefPicker } from "./widgets/RefPicker";
import { StringListEditor } from "./widgets/StringListEditor";
import { TagsInput } from "./widgets/TagsInput";

interface Props {
  descriptor: EntityDescriptor;
  worldId: string;
  frontmatter: Frontmatter;
  body: string;
  onFrontmatterChange: (next: Frontmatter) => void;
  onBodyChange: (next: string) => void;
  /** "create" renders only createDefault fields (no Advanced/body). */
  mode?: "edit" | "create";
}

function asString(v: FrontmatterValue | undefined): string {
  return typeof v === "string" ? v : v == null ? "" : String(v);
}
function asStringArray(v: FrontmatterValue | undefined): string[] {
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : [];
}
function asObject(v: FrontmatterValue | undefined): Record<string, FrontmatterValue> {
  return v && typeof v === "object" && !Array.isArray(v)
    ? (v as Record<string, FrontmatterValue>)
    : {};
}
function asRows(v: FrontmatterValue | undefined): Record<string, unknown>[] {
  return Array.isArray(v)
    ? (v.filter((x) => x && typeof x === "object" && !Array.isArray(x)) as Record<
        string,
        unknown
      >[])
    : [];
}

export function EntityForm({
  descriptor,
  worldId,
  frontmatter,
  body,
  onFrontmatterChange,
  onBodyChange,
  mode = "edit",
}: Props) {
  function setKey(key: string, value: FrontmatterValue) {
    onFrontmatterChange({ ...frontmatter, [key]: value });
  }

  function renderField(
    field: FieldDescriptor,
    value: FrontmatterValue | undefined,
    onChange: (next: FrontmatterValue) => void,
  ) {
    switch (field.widget) {
      case "text":
        return (
          <input
            type="text"
            value={asString(value)}
            readOnly={field.readOnly}
            onChange={(e) => onChange(e.target.value)}
          />
        );
      case "textarea":
        return (
          <textarea
            rows={field.rows ?? 3}
            value={asString(value)}
            onChange={(e) => onChange(e.target.value)}
          />
        );
      case "number":
        return (
          <input
            type="number"
            value={typeof value === "number" ? value : 0}
            onChange={(e) => onChange(Number(e.target.value))}
          />
        );
      case "bool":
        return (
          <input
            type="checkbox"
            checked={Boolean(value)}
            onChange={(e) => onChange(e.target.checked)}
          />
        );
      case "enum":
        return (
          <EnumSelect value={asString(value)} options={field.options ?? []} onChange={onChange} />
        );
      case "tags":
        return <TagsInput value={asStringArray(value)} onChange={onChange} />;
      case "stringList":
      case "refList":
        return (
          <StringListEditor label={field.label} value={asStringArray(value)} onChange={onChange} />
        );
      case "map":
        return (
          <MapEditor
            value={asObject(value) as Record<string, string>}
            onChange={(next) => onChange(next as FrontmatterValue)}
          />
        );
      case "ref":
        return (
          <RefPicker
            worldId={worldId}
            refKinds={field.refKinds ?? []}
            value={asString(value)}
            onChange={onChange}
          />
        );
      case "object": {
        const obj = asObject(value);
        return (
          <fieldset className="entity-form-object">
            {(field.fields ?? []).map((child) => (
              <label key={child.key} className="entity-form-field">
                <span>{child.label}</span>
                {renderField(child, obj[child.key], (next) =>
                  onChange({ ...obj, [child.key]: next }),
                )}
              </label>
            ))}
          </fieldset>
        );
      }
      case "objectList":
        return (
          <ObjectListEditor
            value={asRows(value)}
            fieldKeys={(field.fields ?? []).map((f) => f.key)}
            onChange={(rows) => onChange(rows as FrontmatterValue)}
            renderRow={(row, patch) => (
              <div className="object-list-fields">
                {(field.fields ?? []).map((child) => (
                  <label key={child.key} className="entity-form-field">
                    <span>{child.label}</span>
                    {renderField(child, row[child.key] as FrontmatterValue, (next) =>
                      patch({ ...row, [child.key]: next }),
                    )}
                  </label>
                ))}
              </div>
            )}
          />
        );
      default:
        return null;
    }
  }

  const hidden = managedKeys(descriptor);

  if (mode === "create") {
    return (
      <div className="entity-form entity-form-create">
        {createDefaultFields(descriptor).map((field) => (
          <label key={field.key} className="entity-form-field">
            <span>{field.label}</span>
            {renderField(field, frontmatter[field.key], (next) => setKey(field.key, next))}
          </label>
        ))}
      </div>
    );
  }

  return (
    <div className="entity-form">
      {descriptor.sections.map((section) => {
        const sectionFields = section.fields.map((field) => (
          <label key={field.key} className="entity-form-field">
            <span>{field.label}</span>
            {renderField(field, frontmatter[field.key], (next) => setKey(field.key, next))}
          </label>
        ));
        return section.collapsed ? (
          <details key={section.title} className="entity-form-section">
            <summary>{section.title}</summary>
            {sectionFields}
          </details>
        ) : (
          <fieldset key={section.title} className="entity-form-section">
            <legend>{section.title}</legend>
            {sectionFields}
          </fieldset>
        );
      })}

      <details className="entity-form-advanced">
        <summary>Advanced / raw fields</summary>
        <FrontmatterEditor value={frontmatter} onChange={onFrontmatterChange} hiddenKeys={hidden} />
      </details>

      <section className="entity-editor-panel" aria-labelledby="body-heading">
        <h4 id="body-heading">Markdown body</h4>
        <textarea
          className="entity-body-editor"
          value={body}
          rows={24}
          onChange={(e) => onBodyChange(e.target.value)}
        />
      </section>
    </div>
  );
}
