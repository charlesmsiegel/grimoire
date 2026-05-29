/**
 * Declarative field descriptors that drive the structured entity forms
 * (issue #441). Each descriptor mirrors a backend library model
 * (`types/characters.py`, `types/world.py`); a field's `key` is the
 * frontmatter key it reads and writes. Keys not covered by a descriptor fall
 * through to the generic FrontmatterEditor under EntityForm's "Advanced"
 * section. A cross-language drift test guards the keys against the schema.
 */

import type { EntityKind } from "../../api/library";

export type Widget =
  | "text"
  | "textarea"
  | "number"
  | "bool"
  | "enum"
  | "tags"
  | "stringList"
  | "ref"
  | "refList"
  | "object"
  | "objectList"
  | "map";

export interface FieldDescriptor {
  key: string;
  label: string;
  widget: Widget;
  help?: string;
  readOnly?: boolean;
  rows?: number;
  options?: { value: string; label: string }[];
  refKinds?: EntityKind[];
  /** Children for `object` / `objectList` widgets. */
  fields?: FieldDescriptor[];
}

export interface EntitySectionDescriptor {
  title: string;
  collapsed?: boolean;
  fields: FieldDescriptor[];
}

export interface EntityDescriptor {
  kind: EntityKind;
  sections: EntitySectionDescriptor[];
}

const CHARACTER: EntityDescriptor = {
  kind: "character",
  sections: [
    {
      title: "Identity",
      fields: [
        { key: "name", label: "Name", widget: "text" },
        { key: "id", label: "ID", widget: "text", readOnly: true },
        {
          key: "role",
          label: "Role",
          widget: "enum",
          options: [
            { value: "pc", label: "PC" },
            { value: "major_npc", label: "Major NPC" },
            { value: "minor_npc", label: "Minor NPC" },
            { value: "ensemble", label: "Ensemble" },
            { value: "named_flavor", label: "Named flavor" },
          ],
        },
        { key: "aliases", label: "Aliases", widget: "tags" },
        { key: "age", label: "Age", widget: "text" },
        { key: "tags", label: "Tags", widget: "tags" },
        { key: "role_tags", label: "Role tags", widget: "tags" },
        { key: "household_id", label: "Household", widget: "text" },
      ],
    },
    {
      title: "Description",
      fields: [{ key: "description", label: "Description", widget: "textarea", rows: 4 }],
    },
    {
      title: "Voice",
      fields: [
        {
          key: "voice",
          label: "Voice",
          widget: "object",
          fields: [
            { key: "summary", label: "Summary", widget: "textarea", rows: 2 },
            { key: "voice_register", label: "Register", widget: "text" },
            { key: "samples", label: "Sample lines", widget: "stringList" },
            { key: "speech_patterns", label: "Speech patterns", widget: "stringList" },
            { key: "dos", label: "Dos", widget: "stringList" },
            { key: "donts", label: "Don'ts", widget: "stringList" },
            { key: "address_terms", label: "Address terms", widget: "map" },
          ],
        },
      ],
    },
    {
      title: "Image prompt",
      collapsed: true,
      fields: [
        {
          key: "image",
          label: "Image",
          widget: "object",
          fields: [
            { key: "base_prompt", label: "Base prompt", widget: "textarea", rows: 3 },
            { key: "negative_prompt", label: "Negative prompt", widget: "textarea", rows: 2 },
            { key: "canonical_seed", label: "Canonical seed", widget: "number" },
          ],
        },
      ],
    },
    {
      title: "Relationships",
      fields: [
        {
          key: "structural_relationships",
          label: "Relationships",
          widget: "objectList",
          fields: [
            { key: "to_ref", label: "To", widget: "ref", refKinds: ["character", "faction"] },
            { key: "kind", label: "Kind", widget: "text" },
            { key: "note", label: "Note", widget: "text" },
          ],
        },
      ],
    },
  ],
};

const REGISTRY: Partial<Record<EntityKind, EntityDescriptor>> = {
  character: CHARACTER,
};

export function getDescriptor(kind: EntityKind | string): EntityDescriptor | undefined {
  return REGISTRY[kind as EntityKind];
}

/** Every top-level frontmatter key a descriptor owns (for the Advanced fallback). */
export function managedKeys(descriptor: EntityDescriptor): string[] {
  return descriptor.sections.flatMap((s) => s.fields.map((f) => f.key));
}
