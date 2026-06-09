/**
 * TypeScript mirrors of the backend payload shapes the views consume. The
 * backend serialises Pydantic models; only the fields the frontend actually
 * reads are typed here, and unknown extras are tolerated.
 *
 * Shapes that feed the high-traffic list endpoints are defined once as Zod
 * schemas in `api/schemas/` (used by the client's `checkSchema` drift check,
 * issue #599) and re-exported here so the compile-time type can never drift
 * from the runtime validator. Payloads without a schema stay plain
 * interfaces.
 */

export type {
  CharacterCard,
  ImagePromptTemplate,
  ResolutionSource,
  ResolvedCharacter,
  ResolvedEntity,
  VoiceAnchor,
} from "./schemas/resolved";
export type { InGameTime, SceneSummary, Thread } from "./schemas/scene";
export type { Greeting, WorldMeta } from "./schemas/world";
export type { ImageMetadata } from "./schemas/image";
export type {
  ModuleManifest as MechanicsManifest,
  RegisteredMechanicsModule,
} from "./schemas/mechanics";

export interface WorldRef {
  world_id: string;
  priority: number;
  /** `null`/missing = include every kind; an explicit list (even `[]`) is literal. */
  include: string[] | null;
  bound_at_version: number;
  track_latest: boolean;
}

export interface Composition {
  worlds: WorldRef[];
  mechanics: string | null;
  style_guide_id: string | null;
  image_preset_id: string | null;
  inline_style_guide: string | null;
  content_boundaries: string | null;
}

export interface UpgradeReport {
  campaign_id: string;
  world_id: string;
  from_version: number;
  to_version: number;
  changed_entities: string[];
  added_entities: string[];
  removed_entities: string[];
}

export interface WorldDiffChange {
  path: string;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
}

export interface WorldDiff {
  world_id: string;
  from_version: number;
  to_version: number;
  added: string[];
  removed: string[];
  changed: WorldDiffChange[];
}
