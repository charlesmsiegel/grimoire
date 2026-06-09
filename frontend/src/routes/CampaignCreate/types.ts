import { slugify as slugifyShared } from "../../lib/slugify";

/**
 * Wizard state shape. Each step contributes a slice of this draft. The final
 * step transforms it into a `CampaignCreateInput` for the backend.
 */

export interface DraftWorldRef {
  world_id: string;
  priority: number;
  include: string[]; // empty = all kinds
  track_latest: boolean;
}

export interface DraftPC {
  character_ref: string;
  name: string;
  owner: string;
  origin: "library" | "new";
  role_tags: string[];
  profileDescription: string;
  profileGoals: string[];
  profilePlayerNotes: string;
}

export type StyleGuideMode = "library" | "inline" | "none";

export interface WizardDraft {
  // Step 1 — identity
  id: string;
  name: string;
  description: string;
  tags: string[];

  // Step 2 — composition
  worldRefs: DraftWorldRef[];

  // Step 3 — mechanics
  mechanicsId: string | null; // null = "No mechanics"
  bulkCreateSheets: boolean;

  // Step 4 — PCs
  pcs: DraftPC[];

  // Step 5 — style & content
  styleGuideMode: StyleGuideMode;
  styleGuideId: string | null;
  inlineStyleGuide: string;
  imagePresetId: string | null;
  contentBoundaries: string;

  // Starting-scene step (zero-indexed 5; "Step 6" in UI numbering)
  greetingId: string | null;
  startingLocation: string;
  startingTime: string;
  startingCast: string[];
}

export const ENTITY_KINDS = [
  "characters",
  "items",
  "locations",
  "lore",
  "factions",
  "greetings",
  "monsters",
] as const;

export type EntityKind = (typeof ENTITY_KINDS)[number];

export function emptyDraft(): WizardDraft {
  return {
    id: "",
    name: "",
    description: "",
    tags: [],
    worldRefs: [],
    mechanicsId: null,
    bulkCreateSheets: true,
    pcs: [],
    styleGuideMode: "library",
    styleGuideId: null,
    inlineStyleGuide: "",
    imagePresetId: null,
    contentBoundaries: "",
    greetingId: null,
    startingLocation: "",
    startingTime: "",
    startingCast: [],
  };
}

/** Campaign ids cap at 64 chars; spelling otherwise matches library ids. */
export function slugify(input: string): string {
  return slugifyShared(input, { maxLength: 64 });
}
