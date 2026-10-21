// entities
// The kinds THIS BUILD knows about, and the union everything else types
// against. The import dialogs' Category dropdown is this list intersected with
// `GET /api/entity-kinds` (see `components/useEntityKinds.ts`), so a kind
// added to `store.entities.ENTITY_KINDS` reaches it without either dialog
// being edited (#138) — once this list learns the kind, which
// `test_the_frontend_ships_the_same_kind_list` requires. The reclassify
// picker enumerates the array too, so a kind present in the union but
// missing from the list is simply unofferable, with nothing to say so. It has to stay the
// compile-time union anyway: the tabs, labels and per-kind field table are all
// written against named kinds, which is the same reason the dropdown will not
// offer a kind that is missing from it.
// Import it from `../api/types` and not through `../api/client`: a component
// that reads it at module scope would otherwise crash every suite that mocks
// the client wholesale, including suites that only import a helper out of it.
export const ENTITY_KINDS = ["locations", "lore", "items", "groups", "creatures"] as const;
export type EntityKind = (typeof ENTITY_KINDS)[number];
/** A kind as it comes back over HTTP: one of the above, or one this build has
 *  not heard of. `str` on the wire too (`routes.models.LoreEntry.category`),
 *  validated against `entities.ENTITY_KINDS` at the commit boundary rather than
 *  by its type. Use `EntityKind` for kinds this code names itself.
 *
 *  `string & {}` rather than a bare `string`: it widens to any string exactly
 *  as the wire does, but keeps the five known kinds as editor completions
 *  instead of collapsing the union away. */
export type EntityKindName = EntityKind | (string & {});
export type EntityScope = { kind: "world" | "campaign"; id: string };

/** The common Art surface deliberately excludes Greetings, whose art is
 *  authored through greeting Markdown, collections, and subject reviews. */
export const ART_RECORD_KINDS = ["characters", "pcs", ...ENTITY_KINDS] as const;
export type ArtRecordKind = (typeof ART_RECORD_KINDS)[number];
export type RecordArtTarget =
  | { scope: EntityScope; kind: "characters" | "pcs"; record_id: string; version: string }
  | { scope: EntityScope; kind: EntityKind; record_id: string; version?: never };
export type ArtSourceRef =
  | { surface: "record"; target: RecordArtTarget; name: string; expected_v: string }
  | { surface: "library"; scope: EntityScope; name: string; expected_v: string };
export type DescriptionState = {
  scope: "shared" | "legacy" | null; text: string; described: boolean;
  conflicts: { text: string; from: string }[];
};
export type ArtProvenance = "world" | "campaign" | "inherited" | "world-original";
export type ArtCreated = { name: string; ext: string; v: string; image_id: string };
export type ArtLinkResult =
  | { status: "ok"; created: ArtCreated; no_op?: boolean }
  | { status: "partial"; created: ArtCreated; error: { kind: string; detail: string } };
export type ArtOperation = "append" | "reuse" | "promote" | "focus" | "description" | "remove" | "draft_description";
export type DescriptionRecovery = { available: false } | {
  available: true; description_rev: string; effective_description: DescriptionState;
  write_target_description: DescriptionState;
};
export type DescriptionSaveResult =
  | { status: "ok" }
  | { status: "partial"; kind: "description_cleanup_incomplete"; committed: true;
      confirmed_text: string; recovery: DescriptionRecovery };
export type RecordArtImage = {
  name: string; ext: string; v: string; image_id?: string | null; url: string; thumb: string;
  description: string; described: boolean; description_scope?: "shared" | "legacy";
  description_available: boolean; description_rev: string | null;
  effective_description?: DescriptionState; write_target_description?: DescriptionState;
  focus?: number | null; width?: number | null; height?: number | null; animated?: boolean;
  provenance: ArtProvenance; placement: Extract<ArtSourceRef, { surface: "record" }>;
};
export type RecordArtRead = {
  target: RecordArtTarget; images: RecordArtImage[]; world_originals: RecordArtImage[];
  world_originals_error?: string; operations: ArtOperation[];
};
export type ImagePromoteGuards = { expected_v?: string; expected_primary_v?: string | null };
export type ImageDescriptionGuards = { expected_v?: string; expected_description_rev?: string; cleanup_only?: boolean };
// Group files author membership; these are read-only inverse projections.
export type MembershipRole = "leader" | "member";
export type MembershipRoster = {
  ref: string; name: string; secrecy: Secrecy; group_type: string;
  leader: string | null; members: string[]; headquarters: string | null;
};
export type MembershipAffiliation = { group: string; roles: MembershipRole[] };
export type MembershipResponse = {
  digest: string;
  groups: MembershipRoster[];
  by_actor: Record<string, MembershipAffiliation[]>;
};
export type ActorGroup = { ref: string; name: string; secrecy: Secrecy; roles: MembershipRole[] };
export type ActorMembershipResponse = { actor: string; groups: ActorGroup[] };

// ---- library moves (#52, #53, #60) ----
//
// The kinds that can move between a campaign and its world. Wider than
// EntityKind on both ends: greetings are a flat synced record too, and promote
// carries actors. Which of the four operations accepts which kind is the
// store's rule (`store/sync.py`), reported per record by `libraryStatus` —
// this type only says what is addressable.
export type LibraryKind = EntityKind | "greetings" | "characters" | "pcs";

/** Where one campaign record stands relative to its world's library.
 *
 *  `can_promote` / `can_push` are the server's own preconditions rather than
 *  anything derived here: an editor that recomputed them would drift into
 *  offering the button that 409s. */
export type LibraryStatus = {
  in_library: boolean;
  diverged: boolean;
  can_promote: boolean;
  can_push: boolean;
};


/** A campaign that would notice a library record going away. `has_copy` says
 *  whether it already holds its own — the ones that do not are what demote's
 *  copy-down is for. */
export type LibraryDependent = { id: string; name: string; has_copy: boolean };

// ---- typed entity fields (#37, #222) ----
//
// Mirrors backend/src/grimoire/store/entity_schema.py — keep in sync.

/** Every kind of record a `ref` field may name: the five entity kinds plus the
 *  two actor kinds. Mirrors `entity_schema.REF_KINDS`. */
export type RefKind = EntityKind | "characters" | "pcs";

/** A `choice` whose options are only known at runtime names the list it
 *  draws from; `EntityEditor` resolves each through the API. Mirrors
 *  `entity_schema.OPTION_SOURCES`. */
export type OptionSource = "climates";

/** One typed field on an entity kind. Mirrors store/entity_schema.py FIELDS,
 *  key for key: the spec is the whole constraint (#221), the save boundary
 *  refuses what a spec does not admit and the form renders the control the
 *  spec implies, so a bound or an option list declared here and not there
 *  (or the reverse) is a picker offering what the server rejects.
 *
 *  A `ref` field names other records in the `<kind>:<id>` spelling `owners:`
 *  uses — one, or a comma-separated list when `multi`. `kinds` is what makes
 *  it a picker rather than a text box: the editor offers exactly those kinds'
 *  records, and the backend refuses anything else at the save boundary. */
export type EntityFieldSpec = {
  key: string;
  label: string;
  widget: "text" | "ref" | "number" | "choice";
  /** `ref`: the kinds it may name, and whether it holds a list. */
  kinds?: readonly RefKind[];
  multi?: boolean;
  /** `number`: inclusive bounds, each optional. */
  min?: number;
  max?: number;
  /** `choice`: exactly one of a literal option list or a named source. */
  options?: readonly string[];
  source?: OptionSource;
};

export const ENTITY_FIELDS: Record<EntityKind, EntityFieldSpec[]> = {
  locations: [
    { key: "climate", label: "Climate", widget: "choice", source: "climates" },
    { key: "persistence", label: "Weather persistence", widget: "number", min: 0, max: 1 },
    { key: "weather_zone", label: "Weather zone", widget: "text" },
  ],
  lore: [],
  items: [
    { key: "item_type", label: "Type", widget: "text" },
    { key: "rarity", label: "Rarity", widget: "text" },
    { key: "holder", label: "Held by", widget: "ref",
      kinds: ["characters", "pcs", "groups", "locations"] },
  ],
  groups: [
    { key: "group_type", label: "Type", widget: "text" },
    { key: "leader", label: "Leader", widget: "ref", kinds: ["characters", "pcs"] },
    { key: "members", label: "Members", widget: "ref", kinds: ["characters", "pcs"], multi: true },
    { key: "headquarters", label: "Headquarters", widget: "ref", kinds: ["locations"] },
  ],
  creatures: [
    { key: "creature_type", label: "Type", widget: "text" },
    { key: "threat", label: "Threat", widget: "text" },
    { key: "habitat", label: "Habitat", widget: "ref", kinds: ["locations"], multi: true },
  ],
};

/** One activation control on a world-info entry. Mirrors
 *  `store.lore_fields.FIELD_KEYS`, in the same order, and its bounds and enums:
 *  a bound declared here and not there is an input offering what the save
 *  boundary refuses with a 400. Every kind carries them, so they live beside
 *  `ENTITY_FIELDS` rather than inside any one kind's list.
 *
 *  Values are flat strings like every other frontmatter field. `bool` is the
 *  string `"true"`, and its off state is the BLANK string -- the backend takes
 *  no `"false"` -- which is also how any field is cleared. `refs` is a comma
 *  list of `characters:<id>` / `pcs:<id>`. */
export const ACTIVATION_FIELDS: {
  key: string;
  label: string;
  widget: "text" | "number" | "choice" | "bool" | "refs";
  min?: number;
  max?: number;
  options?: string[];
}[] = [
  { key: "secondary_keys", label: "Secondary keys", widget: "text" },
  { key: "key_logic", label: "Key logic", widget: "choice",
    options: ["and_any", "and_all", "not_any", "not_all"] },
  { key: "scan_depth", label: "Scan depth", widget: "number", min: 0, max: 100 },
  { key: "sticky", label: "Sticky", widget: "number", min: 0, max: 50 },
  { key: "cooldown", label: "Cooldown", widget: "number", min: 0, max: 50 },
  { key: "priority", label: "Priority", widget: "number", min: 0, max: 1000 },
  { key: "keep", label: "Keep under budget", widget: "bool" },
  { key: "recursion", label: "Recursion", widget: "choice",
    options: ["both", "pulled_only", "pulls_only", "none"] },
  { key: "known_by", label: "Known by", widget: "refs" },
];

// Mirrors store.entities.SECRECY_LEVELS. `owners` says what puts an entry in
// the prompt; `secrecy` says how the prompt may use it once there — "secret"
// renders under a "don't let uninvolved characters reveal this" heading,
// "gm-only" never reaches the model at all. Absent == "public".
export const SECRECY_LEVELS = ["public", "secret", "gm-only"] as const;
export type Secrecy = (typeof SECRECY_LEVELS)[number];
export const SECRECY_LABELS: Record<Secrecy, string> = {
  public: "Public", secret: "Secret", "gm-only": "GM-only",
};

// `tokens` is what this record's body costs when it reaches a prompt, counted
// server-side with the same tokenizer as the context inspector (#51). Optional
// because it is a measurement rather than stored data — a payload written
// before it existed simply has none, and the badge stays off. Measured
// regardless of `secrecy`: it is the cost of the text, and a gm-only body
// costs nothing because it is never sent, not because it is short.
export type EntitySummary = { id: string; name: string; keys?: string; owners?: string;
  secrecy?: string; has_image?: boolean; image_v?: string | null; image_focus?: number | null; tokens?: number } & Record<string, unknown>;
/** Effective one-group refs and inherited identity diagnostics (spec 07). */
export type GroupMembershipContext = {
  layer: "world" | "campaign";
  members: string[];
  leader: string | null;
  filtered_members: string[];
  filtered_leader: string | null;
  unresolved_members: string[];
  unresolved_leader: string | null;
  /** Every retained Leader token needing review, dormant legacy extras
   *  included: the copy guard checks each one, so each needs its own opt-in.
   *  Absent from an older server, where `unresolved_leader` is all there is. */
  unresolved_leaders?: string[];
};
export type GroupRefBinding = { field: "members" | "leader"; actor: string };

export type EntityDetail = {
  membership_context?: GroupMembershipContext;
  meta: { id: string; name: string; keys?: string; owners?: string; secrecy?: string;
    sd_prompt?: string } & Record<string, unknown>;
  body: string;
  tokens?: number;
  /** Content hash of the record as read. Echo it back on save and the write is
   *  refused with 409 `stale_record` if the file moved underneath (#35). */
  rev: string;
};

// characters (V3 cards)
export type CardData = {
  name: string;
  description?: string;
  personality?: string;
  scenario?: string;
  first_mes?: string;
  mes_example?: string;
  system_prompt?: string;
  post_history_instructions?: string;
  alternate_greetings?: string[];
  creator?: string;
  creator_notes?: string;
  tags?: string[];
  character_book?: { entries?: unknown[] };
  extensions?: { sd_prompt?: string; [k: string]: unknown };
  [k: string]: unknown;
};
export type Card = { spec: string; spec_version: string; data: CardData };
/** The card containers the backend reads and writes (`_EXPORT_MEDIA`). */
export type CardFormat = "json" | "png" | "charx";
export type VersionRef = { id: string; name: string };
export type CharacterSummary = {
  id: string; name: string; default_version: string; has_avatar?: boolean;
  /** Cache token for the avatar's current bytes. Spent as `?v=`, which the
   *  server answers immutable — so it must name the BYTES, never a counter. */
  avatar_v?: string | null;
  avatar_focus?: number | null; gallery_count?: number; localized_count?: number;
  greeting_count?: number; tagline?: string;
  /** Whether a world-level voice anchor exists. OPTIONAL, so a response or
   *  fixture predating it reads as unknown rather than as "no anchor" —
   *  the backlog filters on `=== false`, never on falsiness. */
  has_voice_anchor?: boolean;
  versions: VersionRef[];
};
/** A world version of a character the campaign no longer holds (a pick purged
 *  it), passed through to the campaign page beside the version it shows.
 *  Read-only there: the pictures are the world's and so are the descriptions.
 *  Campaign reads only; a world read never carries them, and the list is empty
 *  when the campaign cannot see the world's (detached or deleted record, world
 *  gone) or holds every version that has art. Default version first. */
export type BaseVersion = {
  id: string; name: string; images: string[];
  image_v: Record<string, string>;
  /** Image identity per image name, only for images the image store holds;
   *  a legacy file has no entry. */
  image_ids?: Record<string, string>;
  image_descriptions: Record<string, string>;
};
/** The world's copy of a picture that a campaign file of the same name hides
 *  from the version's `images`. Read-only, and served from the WORLD route: the
 *  campaign route resolves that name to the campaign's own file. */
export type ShadowedImage = {
  name: string; v: string; description?: string;
  /** The world copy's identity in the image store; absent for a legacy file. */
  image_id?: string | null;
};
export type CharacterDetail = {
  meta: { id: string; name: string; default_version: string; birthdate?: string };
  base_versions?: BaseVersion[];
  versions: { id: string; name: string; card: Card; images?: string[];
              /** Per-image cache token, keyed by the names in `images`. */
              image_v?: Record<string, string>;
              /** Image identity keyed by the names in `images`, present only
               *  for images the image store holds (a legacy file has none). */
              image_ids?: Record<string, string>;
              /** Campaign reads only: the names in `images` the campaign holds
               *  no file for -- the world's, read through the overlay. */
              inherited?: string[];
              /** Campaign reads only: see `ShadowedImage`. */
              world_shadowed?: ShadowedImage[];
              /** What each image DEPICTS, in the author's words, keyed by the
               *  names in `images`. A key is absent while an image has never
               *  been reviewed and `""` once it has been reviewed and left
               *  deliberately undescribed — the two are not the same, and only
               *  the first belongs in the describe queue. */
              image_descriptions?: Record<string, string>;
              avatar_focus?: number | null; chub_source?: string; is_chub?: boolean;
              /** Embedded-lorebook entries the import would actually commit —
               *  server-side, through the same normalization the import runs,
               *  so it excludes the disabled and blank entries `character_book`
               *  can carry. Never count `card.data.character_book.entries`. */
              importable_lore?: number }[];
};
export type ChubImportResult = {
  character: string;
  version: string;
  updated: boolean;
  gallery: { attempted: number; stored: number };
  lore: { lorebooks_found: number; created: { kind: string; id: string }[] };
};
export type ChubUnlinkedVersion = { character: string; character_name: string; version: string; version_name: string };

// PCs
export type Persona = {
  name: string; pronouns: string; summary: string; description: string; birthdate?: string;
  /** Profile fields (#65). Optional because a persona written before they
   *  existed reads them as "", and older payloads may omit them entirely. */
  goals?: string; player_notes?: string;
};
/** One earlier text of a PC version (#67). `saved` is when that text was
 *  replaced; `name` is the persona name it held. Newest first in a listing. */
export type PCRevision = { id: string; saved: string; name: string };
export type PCSummary = {
  id: string; name: string; tags: string[]; default_version: string; versions: VersionRef[];
  // Same derived image fields a CharacterSummary carries, bar `localized_count`
  // — only a character card's text is localized, so a PC has no `embed-` images.
  has_avatar?: boolean; avatar_focus?: number | null; gallery_count?: number;
  /** See `CharacterSummary.avatar_v`: the avatar's bytes, spent as `?v=`. */
  avatar_v?: string | null;
};
export type PCDetail = {
  meta: { id: string; name: string; tags: string[]; default_version: string };
  versions: { id: string; name: string; persona: Persona; images?: string[];
              /** See `CharacterDetail` — absent and `""` differ. */
              image_descriptions?: Record<string, string>;
              avatar_focus?: number | null }[];
};

// campaign sync: what the world has that a campaign has not taken yet (#6, #8).
// A ref is `{kind, id}` and the shapes on either side of it differ by kind, so
// `IncomingBlob` is the union flattened into optional fields rather than a
// discriminated one: the backend tags nothing (`store/sync.py`), and which
// field arrived IS the discriminant.
export type IncomingRef = { kind: string; id: string };
/** `new` has no campaign copy to compare against; `update` means the campaign
 *  copy still matches the base the world moved on from, so taking the world's
 *  version loses nothing; `conflict` means both sides changed. */
export type IncomingStatus = "new" | "update" | "conflict";
/** One side of an incoming change. `card` for a locked character version,
 *  `persona` for a locked PC version, `body` for everything else — an entity, a
 *  plot map, or the version list of an actor with no locked version. */
export type IncomingField = { key: string; label: string; value: string; shown: string };
export type IncomingBlob = {
  name: string; version?: string; body?: string; card?: Card; persona?: Persona;
  fields?: IncomingField[];
};
export type IncomingItem = {
  ref: IncomingRef; status: IncomingStatus; world: IncomingBlob;
  /** Absent when the campaign has no copy of its own to weigh against. */
  mine?: IncomingBlob;
};
/** The composition overview (#71): one row per ref the campaign holds against
 *  the library — every sync.md ref plus every version-locked actor — with the
 *  state derived from the same world/base/mine hash comparison the sync engine
 *  runs on. Unlike `IncomingStatus` there is no `new` (a ref is only listed
 *  once the campaign holds it) and there IS `diverged`/`insync`, the two
 *  states `/incoming` never reports. */
export type CompositionState = "conflict" | "update" | "diverged" | "insync";
/** The appearance record's version lock, when the actor has one — a different
 *  system from the sync ref (its upgrade verb is `import_version`, not
 *  accept), which is why it rides beside the state instead of inside it. */
export type CompositionLock = { version: string; role: string; scenes: string[] };
export type CompositionRow = {
  ref: IncomingRef; name: string; state: CompositionState;
  /** Pinned against the sync engine: `/incoming` stops offering this ref and
   *  accept/reject ignore it, while `state` still says what the pin holds off. */
  pinned: boolean;
  lock: CompositionLock | null;
};

/** One campaign descended from a world, and how much of that world it has not
 *  taken yet — the world-side half of the same question (#8). */
export type WorldCampaignPending = {
  id: string; name: string;
  pending: { new: number; update: number; conflict: number };
};
