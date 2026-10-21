/** Characters, PCs, greetings and the image routes that hang off them.
 *  Spread into `api` by `client.ts`. */
import { memoKey, recall, remembering } from "../freshness";
import { notifyArt, notifyArtOnLanded } from "../notify";
import { type ImageOpts, entityBase, withImageQuery } from "../paths";
import { describeImage, draftRun, draftScopeOf } from "../runs";
import type { ChubGalleryEvent, LocalizeEvent, RunHandle, TaglineBatchEvent } from "../stream";
import { request, requestForm, streamPost } from "../transport";
import type { Appearance, CampaignImage, CampaignLibrary, Card, CardFormat, CharacterDetail,
  CharacterSummary, ChubImportResult, ChubUnlinkedVersion, EntityKind, EntityScope,
  GalleryImage, Greeting, GreetingAvailabilityPreview, GreetingDetail, GreetingDraft,
  ImageDescriptionGuards, ImageUsageReport, PCDetail, PCRevision, PCSummary, Persona,
  UndescribedImage, WorldImage } from "../types";
import { artChanged } from "../../appEvents";
import { encodeSegment } from "../../urlSegment";

/** The `PCCreate` body, which both PC create routes take (#14). */
type PCCreateBody = { name: string; tags?: string[]; version_name?: string; persona?: Persona };

export const charactersApi = {
  // characters
  /** Remembered per scope, like `getWorld`: every caller's answer feeds the
   *  next paint of the roster grid, a prefetch's included. */
  listCharacters: (scope: EntityScope) =>
    remembering(memoKey("characters", scope.kind, scope.id),
                request<CharacterSummary[]>("GET", `${entityBase(scope)}/characters`)),
  /** The last `listCharacters(scope)` answer from this store, or `undefined`.
   *  For painting while that read is in flight -- never instead of it. */
  rememberedCharacters: (scope: EntityScope) =>
    recall<CharacterSummary[]>(memoKey("characters", scope.kind, scope.id)),
  // Both scopes since #60: a campaign-scoped create makes a character who
  // exists only here, with no world counterpart and no sync ref — which is
  // exactly what "emergent" means. `promoteToLibrary` is what ends that.
  createCharacter: (scope: EntityScope, body: { name: string; version_name?: string; card?: Card }) =>
    request<{ character: string; version: string }>("POST", `${entityBase(scope)}/characters`, body),
  parseCharacterText: (wid: string, body: { text: string; filename?: string }, signal?: AbortSignal) =>
    request<{ card: Card }>("POST", `/api/worlds/${wid}/characters/parse-text`, body, { signal }),
  refineCharacterText: (wid: string, body: { text: string; card: Card }, signal?: AbortSignal) =>
    draftRun<{ card: Card }>({ at: "world", id: wid }, (attempt) =>
      request<{ run: RunHandle }>("POST", `/api/worlds/${wid}/characters/refine-text`, body,
                                { attempt, signal }), { signal }),
  readCharacter: (scope: EntityScope, cid: string) =>
    request<CharacterDetail>("GET", `${entityBase(scope)}/characters/${cid}`),
  setDefaultVersion: (scope: EntityScope, cid: string, vid: string) =>
    request<{ ok: boolean }>("PUT", `${entityBase(scope)}/characters/${cid}`, { default_version: vid }),
  /** Rename the container (#13). Campaign edits materialize a local copy. */
  setCharacterName: (scope: EntityScope, cid: string, name: string) =>
    request<{ ok: boolean }>("PUT", `${entityBase(scope)}/characters/${cid}/name`, { name }),
  setCharacterBirthdate: (scope: EntityScope, cid: string, birthdate: string) =>
    request<{ ok: boolean }>("PUT", `${entityBase(scope)}/characters/${cid}/birthdate`, { birthdate }),
  // Both scopes since #60: campaign scope removes the character from THIS
  // campaign -- an emergent NPC outright, an inherited one by tombstone --
  // and leaves the library's alone. Creating one you cannot delete was the
  // gap this closes.
  deleteCharacter: (scope: EntityScope, cid: string) =>
    request<{ ok: boolean }>("DELETE", `${entityBase(scope)}/characters/${cid}`),
  getCharacterTagline: (wid: string, cid: string) =>
    request<{ tagline: string }>("GET", `/api/worlds/${wid}/characters/${cid}/tagline`),
  setCharacterTagline: (wid: string, cid: string, tagline: string) =>
    request<{ ok: boolean }>("PUT", `/api/worlds/${wid}/characters/${cid}/tagline`, { tagline }),
  /** Detached, like every computing draft: the POST answers 202 with a run and
   *  this waits for it. The returned shape is unchanged, so callers cannot
   *  tell -- except that backgrounding the tab no longer loses the sentence. */
  generateCharacterTagline: (wid: string, cid: string, signal?: AbortSignal) =>
    draftRun<{ tagline: string }>({ at: "world", id: wid }, (attempt) =>
      request<{ run: RunHandle }>(
        "POST", `/api/worlds/${wid}/characters/${cid}/tagline/generate`,
        undefined, { attempt, signal }), { signal }),
  /** Derive a tagline for every character in the world that has none (#57).
   *
   *  A stream rather than a call per character: the roster can be hundreds
   *  long, each entry costs a provider call, and a progress line the user can
   *  walk away from is the difference between a feature and a frozen tab.
   *  Unlike `generateCharacterTagline` this PERSISTS as it goes — see
   *  `TaglineBatchEvent` — so nothing here needs a save afterwards. */
  generateWorldTaglines: (wid: string, onEvent: (e: TaglineBatchEvent) => void,
                          signal?: AbortSignal) =>
    streamPost<TaglineBatchEvent>(`/api/worlds/${wid}/characters/taglines/generate`,
                                  undefined, onEvent, signal),
  /** Scope-aware: a campaign-local character (an absorb `new_character`, say) has
   *  no world counterpart, so the campaign route is the only way it can ever be
   *  given an anchor. Campaign scope reads through the overlay and writes
   *  campaign-side. */
  getCharacterVoiceAnchor: (scope: EntityScope, cid: string) =>
    request<{ voice_anchor: string }>("GET", `${entityBase(scope)}/characters/${cid}/voice-anchor`),
  /** A blank anchor REMOVES it, opting the character back out of drift detection. */
  setCharacterVoiceAnchor: (scope: EntityScope, cid: string, voice_anchor: string) =>
    request<{ ok: boolean }>("PUT", `${entityBase(scope)}/characters/${cid}/voice-anchor`, { voice_anchor }),
  generateCharacterVoiceAnchor: (scope: EntityScope, cid: string, signal?: AbortSignal) =>
    draftRun<{ voice_anchor: string }>(draftScopeOf(scope), (attempt) =>
      request<{ run: RunHandle }>(
        "POST", `${entityBase(scope)}/characters/${cid}/voice-anchor/generate`,
        undefined, { attempt, signal }), { signal }),
  /** The campaign's own capsule for a character (#62). Campaign-only: a
   *  campaign-local character has no world tagline to read or write. Saved by
   *  PUT on Save, never by the draft below. */
  getCampaignCharacterTagline: (cid: string, char: string) =>
    request<{ tagline: string }>("GET", `/api/campaigns/${cid}/characters/${char}/tagline`),
  /** `tagline` is the sentence the campaign now shows: a blank save of an
   *  inherited capsule answers the library's, not the blank that was sent. */
  setCampaignCharacterTagline: (cid: string, char: string, tagline: string) =>
    request<{ ok: boolean; tagline: string }>("PUT", `/api/campaigns/${cid}/characters/${char}/tagline`, { tagline }),
  /** A drafted capsule and tag list, read from the card and the character's
   *  first scene. Preview only, like every draft: the answer is the form's to
   *  keep or discard. */
  generateCampaignCapsule: (cid: string, char: string, version: string, signal?: AbortSignal) =>
    draftRun<{ capsule: string; tags: string[] }>(draftScopeOf({ kind: "campaign", id: cid }), (attempt) =>
      request<{ run: RunHandle }>(
        "POST", `/api/campaigns/${cid}/characters/${char}/capsule/generate?version=${encodeURIComponent(version)}`,
        undefined, { attempt, signal }), { signal }),
  createVersion: (scope: EntityScope, cid: string, body: { name: string; card: Card }) =>
    request<{ version: string }>("POST", `${entityBase(scope)}/characters/${cid}/versions`, body),
  updateVersion: (scope: EntityScope, cid: string, vid: string, card: Card) =>
    request<{ ok: boolean }>("PUT", `${entityBase(scope)}/characters/${cid}/versions/${vid}`, { card }),
  /** `into` adds the card as a version of an existing character rather than
   *  making a new one; `versionName` says what that version is called. Without
   *  it the name is derived from the card, which gives every version of a
   *  character the same one. */
  importCharacter: (wid: string, file: File, format: string, into?: string, versionName?: string) => {
    const form = new FormData();
    form.append("file", file);
    form.append("format", format);
    if (into) form.append("into", into);
    if (versionName) form.append("version_name", versionName);
    return requestForm<{ character: string; version: string }>(`/api/worlds/${wid}/characters/import`, form);
  },
  localizeImages: (wid: string, cid: string, vid: string, onEvent: (e: LocalizeEvent) => void) =>
    streamPost<LocalizeEvent>(
      `/api/worlds/${wid}/characters/${cid}/versions/${vid}/localize`, undefined, (event) => {
        if (event.summary && event.summary.localized > 0) artChanged();
        onEvent(event);
      }),
  /** A card download for one version. Not a `request` — the response is binary
   *  and the route names the file, so this is an href for a `<a download>`. */
  exportUrl: (wid: string, cid: string, vid: string, format: CardFormat) =>
    `/api/worlds/${wid}/characters/${cid}/versions/${vid}/export?format=${format}`,
  entityImageUrl: (scope: EntityScope, kind: EntityKind, eid: string, name: string,
                   opts?: ImageOpts) =>
    withImageQuery(`${entityBase(scope)}/${kind}/${eid}/images/${name}`, opts),
  listEntityImages: (scope: EntityScope, kind: EntityKind, eid: string) =>
    request<{ name: string; ext: string; v: string; image_id?: string | null; description?: string; described?: boolean }[]>(
      "GET", `${entityBase(scope)}/${kind}/${eid}/images`),
  campaignCoverUrl: (cid: string, opts?: ImageOpts) =>
    withImageQuery(`/api/campaigns/${cid}/cover`, opts),
  putCampaignCover: (cid: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return requestForm<{ ext: string; v: string; image_id?: string | null }>(`/api/campaigns/${cid}/cover`, form, "PUT");
  },
  deleteCampaignCover: (cid: string) =>
    request<{ ok: boolean }>("DELETE", `/api/campaigns/${cid}/cover`),

  // ---- the campaign's own image library (#376) ----
  /** Images that belong to the campaign and to none of its records — what a
   *  narrator post has to draw on, since "Grimoire" is not an actor with a
   *  version to hold art. `name` goes into the URL raw: the server accepts
   *  only names that survive one (`store.image_library.addressable`), which
   *  is the same rule its listing filters by, so a name this builder is ever
   *  handed is already URL- and markdown-safe. */
  campaignImageUrl: (cid: string, name: string, opts?: ImageOpts) =>
    withImageQuery(`/api/campaigns/${cid}/images/${name}`, opts),
  /** The library this campaign can see, and what it has hidden.
   *
   *  `{images, hidden}` rather than a bare array since the library began
   *  reading through to the world: `images` carries the campaign's own uploads
   *  and its world's alike (each row saying which via `inherited`), and
   *  `hidden` names the inherited pictures this campaign has tombstoned — the
   *  only surface from which one can be restored. */
  listCampaignImages: (cid: string, fresh = false) =>
    request<CampaignLibrary>("GET", `/api/campaigns/${cid}/images`, undefined, { fresh }),
  /** Un-hide an inherited image. Idempotent, so a stale entry left by a sweep
   *  that could not reach this campaign clears the same way a fresh one does. */
  restoreCampaignImage: (cid: string, name: string) =>
    request<{ ok: boolean }>(
      "POST", `/api/campaigns/${cid}/images/${encodeSegment(name)}/restore`).then(notifyArt),
  putCampaignImage: (cid: string, name: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return requestForm<CampaignImage>(`/api/campaigns/${cid}/images/${name}`, form, "PUT").then(notifyArt);
  },
  deleteCampaignImage: (cid: string, name: string) =>
    request<{ ok: boolean }>("DELETE", `/api/campaigns/${cid}/images/${name}`).then(notifyArt),
  // ---- the world's cover and its own image library ----
  /** The world's cover: the worlds shelf, the world header, and a bundle. */
  worldCoverUrl: (wid: string, opts?: ImageOpts) =>
    withImageQuery(`/api/worlds/${wid}/cover`, opts),
  putWorldCover: (wid: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return requestForm<{ ext: string; v: string; image_id?: string | null }>(`/api/worlds/${wid}/cover`, form, "PUT");
  },
  deleteWorldCover: (wid: string) =>
    request<{ ok: boolean }>("DELETE", `/api/worlds/${wid}/cover`),

  /** Art that belongs to the world and to none of its records — a map, a
   *  banner, establishing art for the setting. Every campaign on the world
   *  reads through to these, so describing one here describes it once for all
   *  of them.
   *
   *  This URL is for the world's OWN surfaces. A post never carries it: what a
   *  campaign inserts is `campaignImageUrl`, which follows the campaign even
   *  for a picture it inherits (`store/context/art.py:url_for`). */
  worldImageUrl: (wid: string, name: string, opts?: ImageOpts) =>
    withImageQuery(`/api/worlds/${wid}/images/${name}`, opts),
  /** Named for the library, not for "the world's images": `listWorldImages`
   *  next door is the whole-world GALLERY (every record's art), and two
   *  functions a letter apart returning different things is how the wrong one
   *  gets called. */
  listWorldLibrary: (wid: string, fresh = false) =>
    request<WorldImage[]>("GET", `/api/worlds/${wid}/images`, undefined, { fresh }),
  putWorldImage: (wid: string, name: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return requestForm<WorldImage>(`/api/worlds/${wid}/images/${name}`, form, "PUT").then(notifyArt);
  },
  deleteWorldImage: (wid: string, name: string) =>
    request<{ ok: boolean }>("DELETE", `/api/worlds/${wid}/images/${encodeSegment(name)}`).then(notifyArt),
  setWorldImageDescription: (wid: string, name: string, description: string) =>
    request<{ ok: boolean }>(
      "PUT", `/api/worlds/${wid}/images/${encodeSegment(name)}/description`,
      { description }).then(notifyArt).catch(notifyArtOnLanded),

  importCharacterBook: (wid: string, cid: string, vid: string) =>
    request<{ created: { kind: string; id: string }[] }>(
      "POST", `/api/worlds/${wid}/characters/${cid}/versions/${vid}/lorebook/import`),
  importCharacterFromChub: (wid: string, url: string, into?: string, intoVersion?: string,
                            versionName?: string) =>
    request<ChubImportResult>(
      "POST", `/api/worlds/${wid}/characters/import/chub`,
      { url,
        ...(into ? { into, into_version: intoVersion } : {}),
        ...(versionName ? { version_name: versionName } : {}) }),
  setCharacterChubSource: (wid: string, cid: string, vid: string, url: string) =>
    request<{ chub_source: string }>(
      "POST", `/api/worlds/${wid}/characters/${cid}/versions/${vid}/chub-source`, { url }),
  clearCharacterChubSource: (wid: string, cid: string, vid: string) =>
    request<{ chub_source: string }>(
      "DELETE", `/api/worlds/${wid}/characters/${cid}/versions/${vid}/chub-source`),
  downloadCharacterChubGallery: (wid: string, cid: string, vid: string, onEvent: (e: ChubGalleryEvent) => void) =>
    streamPost<ChubGalleryEvent>(
      `/api/worlds/${wid}/characters/${cid}/versions/${vid}/chub-gallery`, undefined, (event) => {
        if (event.summary && event.summary.stored > 0) artChanged();
        onEvent(event);
      }),
  downloadCharacterChubLorebooks: (wid: string, cid: string, vid: string) =>
    request<{ lorebooks_found: number; created: { kind: string; id: string }[] }>(
      "POST", `/api/worlds/${wid}/characters/${cid}/versions/${vid}/chub-lorebooks`),
  findChubUnlinked: (wid: string) =>
    request<{ versions: ChubUnlinkedVersion[] }>("GET", `/api/worlds/${wid}/characters/chub-unlinked`),

  // pcs
  listPCs: (scope: EntityScope) => request<PCSummary[]>("GET", `${entityBase(scope)}/pcs`),
  // Both PC creates post the one `PCCreate` body, so both spell it the same:
  // `version_name` names the first version (the server defaults it to
  // "default"), exactly as `createCharacter` already names a character's.
  createCampaignPC: (cid: string, body: PCCreateBody) =>
    request<{ pc: string; version: string }>("POST", `/api/campaigns/${cid}/pcs`, body),
  /** What a campaign begun in world `wid` would refuse a new PC called `name`
   *  with, or `null` when it would take it. The campaign wizard asks before it
   *  creates anything, because `createCampaignPC` can only answer once the
   *  campaign exists. Advice: that route's own check still binds. `fresh`, so
   *  a name asked about again is asked again. */
  checkCampaignPCName: (wid: string, name: string) =>
    request<{ refusal: string | null }>("GET",
      `/api/worlds/${encodeSegment(wid)}/campaign-pc-name?name=${encodeURIComponent(name)}`,
      undefined, { fresh: true }),
  listCampaignPCs: (cid: string) => request<PCSummary[]>("GET", `/api/campaigns/${cid}/pcs`),
  createPC: (wid: string, body: PCCreateBody) =>
    request<{ pc: string; version: string }>("POST", `/api/worlds/${wid}/pcs`, body),
  readPC: (scope: EntityScope, pid: string) => request<PCDetail>("GET", `${entityBase(scope)}/pcs/${pid}`),
  updatePC: (scope: EntityScope, pid: string, patch: { default_version?: string; tags?: string[] }) =>
    request<{ ok: boolean }>("PUT", `${entityBase(scope)}/pcs/${pid}`, patch),
  deletePC: (scope: EntityScope, pid: string) =>
    request<{ ok: boolean }>("DELETE", `${entityBase(scope)}/pcs/${pid}`),
  createPCVersion: (scope: EntityScope, pid: string, body: { name: string; persona: Persona }) =>
    request<{ version: string }>("POST", `${entityBase(scope)}/pcs/${pid}/versions`, body),
  updatePCVersion: (scope: EntityScope, pid: string, vid: string, persona: Persona) =>
    request<{ ok: boolean }>("PUT", `${entityBase(scope)}/pcs/${pid}/versions/${vid}`, { persona }),
  // PC revision history (#67): the earlier texts of one version. A campaign
  // lists only what was edited in that campaign, never the world's.
  listPCRevisions: (scope: EntityScope, pid: string, vid: string) =>
    request<PCRevision[]>("GET", `${entityBase(scope)}/pcs/${pid}/versions/${vid}/revisions`,
                          undefined, { fresh: true }),
  readPCRevision: (scope: EntityScope, pid: string, vid: string, rid: string) =>
    request<Persona>("GET", `${entityBase(scope)}/pcs/${pid}/versions/${vid}/revisions/${rid}`),
  restorePCRevision: (scope: EntityScope, pid: string, vid: string, rid: string) =>
    request<{ ok: boolean }>("POST",
      `${entityBase(scope)}/pcs/${pid}/versions/${vid}/revisions/${rid}/restore`),
  // PC images (#219) — the character calls one folder over. Kept as their own
  // entries rather than folded into `putImage` & co. with a kind argument: the
  // character helpers are called from a dozen places that have no PC in hand,
  // and a required extra argument on all of them buys nothing here.

  // greetings & plot maps
  previewGreetings: (wid: string, tags: string[]) =>
    request<GreetingAvailabilityPreview[]>("GET",
      `/api/worlds/${encodeSegment(wid)}/greeting-availability?tags=${encodeURIComponent(tags.join(","))}`),
  /** Each row carries its greeting's plot-map `edges` (see `Greeting.edges`).
   *  `fresh` for a caller reading to find out whether a write landed: identical
   *  in-flight GETs are shared, and a shared promise started before that write
   *  answers from before it (see `retireInflight`). The plot map reads every
   *  greeting's edges from here in order to write them back, so it asks that
   *  way (#9). */
  listGreetings: (scope: EntityScope, opts?: { fresh?: boolean }) =>
    request<Greeting[]>("GET", `${entityBase(scope)}/greetings`, undefined, opts),
  createGreeting: (scope: EntityScope, draft: GreetingDraft) =>
    request<{ id: string }>("POST", `${entityBase(scope)}/greetings`, draft),
  /** `fresh` for the reason `listGreetings` gives. */
  readGreeting: (scope: EntityScope, gid: string, opts?: { fresh?: boolean }) =>
    request<GreetingDetail>("GET", `${entityBase(scope)}/greetings/${gid}`, undefined, opts),
  updateGreeting: (scope: EntityScope, gid: string,
                   patch: { name?: string; body?: string; present?: string[]; requires_tags?: string[];
                            predecessor_join?: string; pcless?: boolean; location?: string;
                            phase?: string; sequence?: number | null; optional?: boolean;
                            /** re-point at a different character/version, keeping the
                             *  greeting id and its plot-map edges (#17); "" clears */
                            character?: string; version?: string;
                            rev?: string }) =>
    request<{ ok: boolean }>("PUT", `${entityBase(scope)}/greetings/${gid}`, patch),
  deleteGreeting: (scope: EntityScope, gid: string) =>
    request<{ ok: boolean }>("DELETE", `${entityBase(scope)}/greetings/${gid}`),
  setEdges: (scope: EntityScope, gid: string, edges: { leads_to?: string[]; excludes?: string[] }) =>
    request<{ ok: boolean }>("PUT", `${entityBase(scope)}/greetings/${gid}/edges`, edges),
  importGreetings: (wid: string, body: { character: string; version: string }) =>
    request<{ greetings: string[] }>("POST", `/api/worlds/${wid}/greetings/import`, body),
  getGreetingSubjects: (wid: string, gid: string) =>
    request<Record<string, string[]>>("GET", `/api/worlds/${wid}/greetings/${gid}/subjects`),
  /** Every image in this scope with no description entry — the backlog
   *  `DescribeQueue` steps through. A world's queue is its own art; a
   *  campaign's is only what the world's cannot reach — its own image library,
   *  which hangs off no record, and art it has diverged. */
  listUndescribedImages: (scope: EntityScope) =>
    request<UndescribedImage[]>("GET", `${entityBase(scope)}/images/undescribed`),
  /** How long that backlog is, without it: the same filter, answered as
   *  `{count}`. What a button label needs -- the list is every entry with its
   *  record's name and URL, which is a lot to download to print one number. */
  countUndescribedImages: (scope: EntityScope) =>
    request<{ count: number }>("GET", `${entityBase(scope)}/images/undescribed?count=1`)
      .then((r) => r.count),
  /** Every record that places this image object, by bucket. */
  getImageUsage: (imageId: string, opts?: { fresh?: boolean }) =>
    request<ImageUsageReport>("GET", `/api/images/${encodeURIComponent(imageId)}/usage`, undefined, opts),
  /** Every image in a world, from all eight bases, in one response (#200).
   *  World-scoped only: a campaign reaches most of its art through its world,
   *  and the art it has diverged is listed in its own editors — the same split
   *  `listUndescribedImages` draws. One request rather than one per record per
   *  version, which is the whole reason the route exists.
   *
   *  `fresh` for a read taken AFTER a write, which is the case the coalescing
   *  above cannot serve: handed a promise issued before the subjects PUT, the
   *  caller gets a pre-write answer and leaves the tile it just tagged marked
   *  unfinished. Same reasoning, and the same flag, as `campaignChanges`. */
  listWorldImages: (wid: string, fresh = false) =>
    request<GalleryImage[]>("GET", `/api/worlds/${wid}/gallery`, undefined, { fresh }),
  /** The same listing for what a CAMPAIGN sees: its world's art plus its own,
   *  resolved through the overlay, so a diverged copy shadows the world's and
   *  campaign-only art appears at all. Every URL in it is campaign-scoped. */
  listCampaignGallery: (cid: string, fresh = false) =>
    request<GalleryImage[]>("GET", `/api/campaigns/${cid}/gallery`, undefined, { fresh }),
  /** Describe one image. `description: ""` is meaningful and is NOT the same as
   *  never having described it: it means "reviewed, nothing to say", which
   *  takes the image out of the describe queue without offering it to the
   *  model. Campaign-scoped writes land campaign-side, so describing art a
   *  thin campaign still inherits does not diverge the art itself. */
  setCharacterImageDescription: (scope: EntityScope, cid: string, vid: string,
                                 name: string, description: string, guards?: ImageDescriptionGuards) =>
    request<{ ok: boolean }>("PUT",
      `${entityBase(scope)}/characters/${cid}/versions/${vid}/images/${encodeSegment(name)}/description`,
      { description, ...guards }).then(notifyArt).catch(notifyArtOnLanded),
  /** Ask the model what a picture shows. A PREVIEW: the caller decides whether
   *  to keep it and persists through `setCharacterImageDescription`. World-side
   *  only — a description drafted from the bytes is a claim about the bytes,
   *  and a campaign reaches most of its art through its world. */
  draftCharacterImageDescription: (wid: string, cid: string, vid: string, name: string,
                                   signal?: AbortSignal) =>
    describeImage({ at: "world", id: wid },
      `/api/worlds/${wid}/characters/${cid}/versions/${vid}/images/${encodeSegment(name)}/description/draft`,
      signal),
  draftPCImageDescription: (wid: string, pid: string, vid: string, name: string,
                            signal?: AbortSignal) =>
    describeImage({ at: "world", id: wid },
      `/api/worlds/${wid}/pcs/${pid}/versions/${vid}/images/${encodeSegment(name)}/description/draft`,
      signal),
  draftEntityImageDescription: (wid: string, kind: EntityKind, eid: string, name: string,
                                signal?: AbortSignal) =>
    describeImage({ at: "world", id: wid },
      `/api/worlds/${wid}/${kind}/${eid}/images/${encodeSegment(name)}/description/draft`,
      signal),
  draftCampaignImageDescription: (cid: string, name: string, signal?: AbortSignal) =>
    describeImage({ at: "campaign", id: cid },
      `/api/campaigns/${cid}/images/${encodeSegment(name)}/description/draft`, signal),
  /** The world library's draft. `at: "world"` like its three world siblings —
   *  the run is subjected to the world, not to a campaign. */
  draftWorldImageDescription: (wid: string, name: string, signal?: AbortSignal) =>
    describeImage({ at: "world", id: wid },
      `/api/worlds/${wid}/images/${encodeSegment(name)}/description/draft`, signal),
  setPCImageDescription: (scope: EntityScope, pid: string, vid: string,
                          name: string, description: string, guards?: ImageDescriptionGuards) =>
    request<{ ok: boolean }>("PUT",
      `${entityBase(scope)}/pcs/${pid}/versions/${vid}/images/${encodeSegment(name)}/description`,
      { description, ...guards }).then(notifyArt).catch(notifyArtOnLanded),
  setEntityImageDescription: (scope: EntityScope, kind: EntityKind, eid: string,
                              name: string, description: string, guards?: ImageDescriptionGuards) =>
    request<{ ok: boolean }>("PUT",
      `${entityBase(scope)}/${kind}/${eid}/images/${encodeSegment(name)}/description`,
      { description, ...guards }).then(notifyArt).catch(notifyArtOnLanded),
  setCampaignImageDescription: (cid: string, name: string, description: string) =>
    request<{ ok: boolean }>("PUT",
      `/api/campaigns/${cid}/images/${encodeSegment(name)}/description`, { description }).then(notifyArt).catch(notifyArtOnLanded),
  setImageSubjects: (wid: string, gid: string, name: string, subjects: string[]) =>
    name.startsWith("/") || /^https?:\/\//i.test(name)
      ? request<{ ok: boolean }>("PUT", `/api/worlds/${wid}/greetings/${gid}/subjects`, { image: name, subjects })
      : request<{ ok: boolean }>("PUT", `/api/worlds/${wid}/greetings/${gid}/images/${encodeSegment(name)}/subjects`, { subjects }),
  listImageAppearances: (wid: string, cid: string) =>
    request<Appearance[]>("GET", `/api/worlds/${wid}/characters/${cid}/appearances`),
  /** The greeting-image tagging backlog. `fresh` for the same reason
   *  `listWorldImages` takes it: a re-read after the queue has written is
   *  asking what the store holds NOW, and a promise issued before those PUTs
   *  answers a question nobody asked. */
  listUntaggedImages: (wid: string, fresh = false) =>
    request<Appearance[]>("GET", `/api/worlds/${wid}/subjects/untagged`, undefined, { fresh }),
  copyGreetingImage: (scope: EntityScope, cid: string, vid: string,
                      body: { gid: string; name: string; slot: "avatar" | "gallery" }) =>
    request<{ name: string; ext: string }>(
      "POST", `${entityBase(scope)}/characters/${cid}/versions/${vid}/images/copy-from-greeting`, body).then(notifyArt),

  // campaign world-copy actions
  markGreeting: (cid: string, gid: string, status: "completed" | "skipped" | "none") =>
    request<{ ok: boolean }>("POST", `/api/campaigns/${cid}/greetings/${gid}/mark`, { status }),
  pickVersion: (cid: string, kind: "characters" | "pcs", aid: string, version: string) =>
    request<{ ok: boolean }>("POST", `/api/campaigns/${cid}/${kind}/${aid}/pick-version`, { version }),
  importVersion: (cid: string, kind: "characters" | "pcs", aid: string, version: string) =>
    request<{ ok: boolean }>("POST", `/api/campaigns/${cid}/${kind}/${aid}/import-version`, { version }),
  /** One actor's image, whichever kind it is: `kind` IS the asset base, so
   *  characters and PCs address their art identically (#219). The single
   *  builder for both — the character-only `actorImageUrl` and campaign-only
   *  `campaignImageUrl` it replaced were the same URL twice over, and every
   *  place that drew a portrait beside a name had "characters" written into
   *  it, which is what left every PC showing initials. */
  actorImageUrl: (scope: EntityScope, kind: "characters" | "pcs", aid: string,
                  vid: string, name: string, opts?: ImageOpts) =>
    withImageQuery(`${entityBase(scope)}/${kind}/${aid}/versions/${vid}/images/${name}`, opts),
};
