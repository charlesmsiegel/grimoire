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

const LOCATION: EntityDescriptor = {
  kind: "location",
  sections: [
    {
      title: "Identity",
      fields: [
        { key: "name", label: "Name", widget: "text" },
        { key: "id", label: "ID", widget: "text", readOnly: true },
        {
          key: "kind",
          label: "Kind",
          widget: "enum",
          options: [
            { value: "city", label: "City" },
            { value: "building", label: "Building" },
            { value: "room", label: "Room" },
            { value: "region", label: "Region" },
            { value: "outdoor", label: "Outdoor" },
            { value: "other", label: "Other" },
          ],
        },
        { key: "parent_id", label: "Parent location", widget: "ref", refKinds: ["location"] },
        { key: "aliases", label: "Aliases", widget: "tags" },
        { key: "tags", label: "Tags", widget: "tags" },
      ],
    },
    {
      title: "Geography",
      fields: [
        { key: "climate_zone", label: "Climate zone", widget: "text" },
        { key: "indoor", label: "Indoor", widget: "bool" },
        {
          key: "coordinates",
          label: "Coordinates",
          widget: "object",
          fields: [
            { key: "x", label: "X", widget: "number" },
            { key: "y", label: "Y", widget: "number" },
          ],
        },
      ],
    },
    {
      title: "Detail",
      fields: [
        { key: "permanent_features", label: "Permanent features", widget: "stringList" },
        { key: "typical_occupants", label: "Typical occupants", widget: "stringList" },
        { key: "description", label: "Description", widget: "textarea", rows: 4 },
      ],
    },
    {
      title: "Connections",
      collapsed: true,
      fields: [
        {
          key: "connections",
          label: "Connections",
          widget: "objectList",
          fields: [
            { key: "to", label: "To", widget: "ref", refKinds: ["location"] },
            { key: "via", label: "Via", widget: "text" },
            { key: "duration_min", label: "Duration (min)", widget: "number" },
            { key: "notes", label: "Notes", widget: "text" },
          ],
        },
      ],
    },
  ],
};

const ITEM: EntityDescriptor = {
  kind: "item",
  sections: [
    {
      title: "Identity",
      fields: [
        { key: "name", label: "Name", widget: "text" },
        { key: "id", label: "ID", widget: "text", readOnly: true },
        { key: "aliases", label: "Aliases", widget: "tags" },
        { key: "tags", label: "Tags", widget: "tags" },
      ],
    },
    {
      title: "Detail",
      fields: [
        { key: "provenance", label: "Provenance", widget: "text" },
        { key: "current_holder", label: "Current holder", widget: "ref", refKinds: ["character"] },
        { key: "description", label: "Description", widget: "textarea", rows: 4 },
      ],
    },
  ],
};

const MONSTER: EntityDescriptor = {
  kind: "monster",
  sections: [
    {
      title: "Identity",
      fields: [
        { key: "name", label: "Name", widget: "text" },
        { key: "id", label: "ID", widget: "text", readOnly: true },
        {
          key: "category",
          label: "Category",
          widget: "enum",
          options: [
            { value: "beast", label: "Beast" },
            { value: "undead", label: "Undead" },
            { value: "dragon", label: "Dragon" },
            { value: "fey", label: "Fey" },
            { value: "demon", label: "Demon" },
            { value: "aberration", label: "Aberration" },
            { value: "humanoid", label: "Humanoid" },
            { value: "construct", label: "Construct" },
            { value: "elemental", label: "Elemental" },
            { value: "other", label: "Other" },
          ],
        },
        { key: "aliases", label: "Aliases", widget: "tags" },
        { key: "tags", label: "Tags", widget: "tags" },
      ],
    },
    {
      title: "Detail",
      fields: [
        { key: "threat_level", label: "Threat level", widget: "text" },
        { key: "habitat", label: "Habitat", widget: "stringList" },
        { key: "abilities", label: "Abilities", widget: "stringList" },
        { key: "weaknesses", label: "Weaknesses", widget: "stringList" },
        { key: "description", label: "Description", widget: "textarea", rows: 4 },
      ],
    },
  ],
};

const FACTION: EntityDescriptor = {
  kind: "faction",
  sections: [
    {
      title: "Identity",
      fields: [
        { key: "name", label: "Name", widget: "text" },
        { key: "id", label: "ID", widget: "text", readOnly: true },
        { key: "kind", label: "Kind", widget: "text" },
        { key: "tags", label: "Tags", widget: "tags" },
      ],
    },
    {
      title: "Detail",
      fields: [
        { key: "base_location", label: "Base location", widget: "ref", refKinds: ["location"] },
        { key: "description", label: "Description", widget: "textarea", rows: 4 },
      ],
    },
    {
      title: "Membership",
      fields: [
        { key: "leaders", label: "Leaders", widget: "refList", refKinds: ["character"] },
        { key: "members", label: "Members", widget: "refList", refKinds: ["character"] },
        { key: "allies", label: "Allies", widget: "refList", refKinds: ["faction"] },
        { key: "rivals", label: "Rivals", widget: "refList", refKinds: ["faction"] },
      ],
    },
  ],
};

const LORE: EntityDescriptor = {
  kind: "lore",
  sections: [
    {
      title: "Identity",
      fields: [
        { key: "title", label: "Title", widget: "text" },
        { key: "id", label: "ID", widget: "text", readOnly: true },
        { key: "tags", label: "Tags", widget: "tags" },
        { key: "keywords", label: "Keywords", widget: "tags" },
        {
          key: "secrecy",
          label: "Secrecy",
          widget: "enum",
          options: [
            { value: "public", label: "Public" },
            { value: "common-knowledge", label: "Common knowledge" },
            {
              value: "common-knowledge-among-kindred",
              label: "Common knowledge (among kindred)",
            },
            { value: "restricted", label: "Restricted" },
            { value: "secret", label: "Secret" },
          ],
        },
      ],
    },
    {
      title: "Relations",
      fields: [
        {
          key: "related_locations",
          label: "Related locations",
          widget: "refList",
          refKinds: ["location"],
        },
        {
          key: "related_factions",
          label: "Related factions",
          widget: "refList",
          refKinds: ["faction"],
        },
        {
          key: "related_characters",
          label: "Related characters",
          widget: "refList",
          refKinds: ["character"],
        },
      ],
    },
    {
      title: "Activation (lorebook)",
      collapsed: true,
      fields: [
        { key: "secondary_keys", label: "Secondary keys", widget: "tags" },
        {
          key: "selective_logic",
          label: "Selective logic",
          widget: "enum",
          options: [
            { value: "and_any", label: "AND any" },
            { value: "and_all", label: "AND all" },
            { value: "not_any", label: "NOT any" },
            { value: "not_all", label: "NOT all" },
          ],
        },
        { key: "constant", label: "Constant", widget: "bool" },
        { key: "enabled", label: "Enabled", widget: "bool" },
        { key: "priority", label: "Priority", widget: "number" },
        { key: "probability", label: "Probability", widget: "number" },
        {
          key: "position",
          label: "Position",
          widget: "enum",
          options: [
            { value: "before_cast", label: "Before cast" },
            { value: "after_cast", label: "After cast" },
            { value: "at_depth", label: "At depth" },
            { value: "archive", label: "Archive" },
          ],
        },
        { key: "at_depth", label: "At depth", widget: "number" },
        { key: "scan_depth", label: "Scan depth", widget: "number" },
        { key: "case_sensitive", label: "Case sensitive", widget: "bool" },
        { key: "match_whole_words", label: "Match whole words", widget: "bool" },
        { key: "comment", label: "Comment", widget: "textarea", rows: 2 },
      ],
    },
  ],
};

const REGISTRY: Partial<Record<EntityKind, EntityDescriptor>> = {
  character: CHARACTER,
  location: LOCATION,
  item: ITEM,
  monster: MONSTER,
  faction: FACTION,
  lore: LORE,
};

export function getDescriptor(kind: EntityKind | string): EntityDescriptor | undefined {
  return REGISTRY[kind as EntityKind];
}

/** Every top-level frontmatter key a descriptor owns (for the Advanced fallback). */
export function managedKeys(descriptor: EntityDescriptor): string[] {
  return descriptor.sections.flatMap((s) => s.fields.map((f) => f.key));
}
