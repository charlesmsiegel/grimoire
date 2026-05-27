/**
 * Lightweight TypeScript mirrors of the backend payload shapes the views
 * consume. The backend serialises Pydantic models; only the fields the
 * frontend actually reads are typed here, and unknown extras are tolerated.
 */

export interface VoiceAnchor {
  summary: string;
  voice_register: string;
  samples: string[];
  speech_patterns: string[];
  address_terms: Record<string, string>;
  dos: string[];
  donts: string[];
}

export interface ImagePromptTemplate {
  base_prompt: string;
  negative_prompt: string;
  canonical_seed: number | null;
  extra: Record<string, unknown>;
}

export interface CharacterCard {
  id: string;
  name: string;
  role: string;
  world_id: string | null;
  aliases: string[];
  age: string | null;
  tags: string[];
  voice: VoiceAnchor;
  image: ImagePromptTemplate | null;
  description: string;
  body: string;
  file_path: string;
  version: number;
}

export interface ResolutionSource {
  layer: "emergent" | "override" | "library_snapshot" | "library_live";
  scope: string;
  library_id: string | null;
  world_id: string | null;
  version: number | null;
  override_applied: boolean;
}

export interface ResolvedCharacter {
  character: CharacterCard;
  current_state: Record<string, unknown>;
  capabilities: Record<string, unknown>[];
  source_chain: ResolutionSource[];
  overrides_applied: string[];
}

export interface ResolvedEntity {
  kind: string;
  asset_id: string;
  world_id: string | null;
  name: string;
  frontmatter: Record<string, unknown>;
  body: string;
  source_chain: ResolutionSource[];
  overrides_applied: string[];
  extras: Record<string, unknown>;
}

export interface WorldRef {
  world_id: string;
  priority: number;
  include: string[];
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

export interface Greeting {
  id: string;
  world_id: string;
  name: string;
  starting_location: string | null;
  starting_time: string | null;
  present_characters: string[];
  pov_character: string | null;
  mood: string;
  body: string;
  tags: string[];
}

export interface InGameTime {
  moment: string;
}

export interface SceneSummary {
  id: string;
  campaign_id: string;
  ordinal: number;
  slug: string;
  title: string;
  location_ref: string | null;
  in_game_start: InGameTime | null;
  in_game_end: InGameTime | null;
  present_character_refs: string[];
  present_pc_refs: string[];
  mood: string;
  post_count: number;
  tags: string[];
  closed: boolean;
  threads_introduced: Thread[];
  threads_paid_off: Thread[];
  summary: string;
  key_beats: string[];
}

export interface Thread {
  text: string;
  introduced_in_post: string | null;
  paid_off_in_post: string | null;
  tags: string[];
}

export interface ImageMetadata {
  id: string;
  campaign_id: string;
  file_path: string;
  thumbnail_path: string | null;
  prompt: string;
  negative_prompt: string;
  backend: string;
  model: string;
  seed: number | null;
  scene_id: string | null;
  post_id: string | null;
  created_at: string | null;
  user_starred: boolean;
  tags: string[];
}

export interface MechanicsManifest {
  id: string;
  name: string;
  version?: string;
  description?: string;
  entity_kinds?: string[];
}

export interface RegisteredMechanicsModule {
  manifest: MechanicsManifest;
  source: string;
  load_error: string | null;
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

export interface WorldMeta {
  id: string;
  name: string;
  description: string;
  tags: string[];
  pc_role_tags: string[];
  genre: string;
  version: number;
}
