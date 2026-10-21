/** World-scoped library calls: worlds and their bundles, record art, the
 *  entity CRUD shared with campaigns, library moves, tags, and the scene,
 *  lorebook and scenario imports. Spread into `api` by `client.ts`. */
import { invalidateConfigCache, memoKey, recall, remembering } from "../freshness";
import { notifyArt, notifyArtOnLanded, notifyShell } from "../notify";
import { entityBase, membershipBase } from "../paths";
import { describeImage, draftRun } from "../runs";
import type { RunHandle } from "../stream";
import { postZip, request, requestForm } from "../transport";
import type { ActorMembershipResponse, ArtCreated, ArtLinkResult, ArtSourceRef, EntityDetail,
  EntityKind, EntityScope, EntitySummary, GroupRefBinding, LibraryDependent, LibraryKind,
  LibraryStatus, LoreEntryDraft, Message, RecordArtImage, RecordArtRead, RecordArtTarget,
  ScenarioImportResult, ScenarioProposal, SceneImportDraft, WorldDetail, WorldMeta,
  WorldProfile } from "../types";
import { encodeSegment } from "../../urlSegment";

function recordArtBase(target: RecordArtTarget, suffix = ""): string {
  const prefix = `/api/${target.scope.kind}s/${encodeSegment(target.scope.id)}`;
  const path = `${prefix}/record-art/${target.kind}/${encodeSegment(target.record_id)}${suffix}`;
  return target.kind === "characters" || target.kind === "pcs"
    ? `${path}?${new URLSearchParams({ version: target.version })}` : path;
}

function recordImageBase(target: RecordArtTarget, name: string): string {
  const prefix = `/api/${target.scope.kind}s/${encodeSegment(target.scope.id)}/${target.kind}/${encodeSegment(target.record_id)}`;
  const version = target.kind === "characters" || target.kind === "pcs"
    ? `/versions/${encodeSegment(target.version)}` : "";
  return `${prefix}${version}/images/${encodeSegment(name)}`;
}

export const worldsApi = {
  readRecordArt: (target: RecordArtTarget, opts?: { fresh?: boolean; signal?: AbortSignal | undefined }) =>
    // A caller-owned abort cannot cancel a shared GET for another owner. A
    // signalled Art read therefore takes the existing fresh/unshared path.
    request<RecordArtRead>("GET", recordArtBase(target), undefined,
      opts?.signal ? { ...opts, fresh: true } : opts),
  appendRecordArt: (target: RecordArtTarget, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return requestForm<ArtCreated>(recordArtBase(target, "/images"), form).then(notifyArt);
  },
  reuseRecordArt: (target: RecordArtTarget, source: ArtSourceRef, intent: "gallery" | "primary",
                   expectedPrimary?: string | null) =>
    request<ArtLinkResult>("POST", recordArtBase(target, "/images/link"), {
      source, intent, ...(intent === "primary" ? { expected_primary_v: expectedPrimary } : {}),
    }).then(notifyArt),
  removeRecordArt: (target: RecordArtTarget, image: Pick<RecordArtImage, "name" | "v" | "placement">) =>
    request<{ ok: boolean }>("DELETE", `${recordImageBase(target, image.name)}?${new URLSearchParams({ expected_v: image.v })}`)
      .then(notifyArt),
  promoteRecordArt: (target: RecordArtTarget, image: RecordArtImage, expectedPrimary: string | null) =>
    request<{ ok: boolean }>("POST", `${recordImageBase(target, image.name)}/promote`,
      { expected_v: image.v, expected_primary_v: expectedPrimary }).then(notifyArt),
  setRecordArtFocus: (target: RecordArtTarget, image: RecordArtImage, focus: number) =>
    request<{ ok: boolean }>("PUT", `${recordImageBase(target, "avatar")}/focus`,
      { focus, expected_v: image.v }).then(notifyArt),
  setRecordArtDescription: (target: RecordArtTarget, image: RecordArtImage, description: string,
                            expectedDescriptionRev: string, cleanupOnly = false) =>
    request<{ ok: boolean }>("PUT", `${recordImageBase(target, image.name)}/description`, {
      description, expected_v: image.v, expected_description_rev: expectedDescriptionRev,
      ...(cleanupOnly ? { cleanup_only: true } : {}),
    }).then(notifyArt).catch(notifyArtOnLanded),
  draftRecordArtDescription: (target: RecordArtTarget, image: RecordArtImage, signal?: AbortSignal) => {
    if (target.scope.kind !== "world") return Promise.reject(new Error("Description drafting requires a world target"));
    return describeImage({ at: "world", id: target.scope.id },
      `${recordImageBase(target, image.name)}/description/draft`, signal);
  },

  // worlds
  /** `fresh` for the caller refetching *because* a world just changed — the
   *  in-flight share would hand it a read issued before that mutation, which
   *  is precisely the answer it is trying to replace. Same trade
   *  `listCampaigns` makes; a fork can run for a minute, so the read it is
   *  racing is not hypothetical (Codex review).
   *
   *  Remembered (see "remembered reads" in `../freshness`), which is what lets the Worlds
   *  grid paint on its first render; `WorldsView` reads it through
   *  `useRevalidated`. */
  listWorlds: (fresh = false) =>
    remembering(memoKey("worlds"),
                request<WorldMeta[]>("GET", "/api/worlds", undefined, { fresh })),
  rememberedWorlds: () => recall<WorldMeta[]>(memoKey("worlds")),
  // The config carries `first_run`, which is partly a statement about whether
  // the store holds anything — so creating the first world changes the config
  // response even though nothing wrote to config.md through this client (#194).
  /** `profile` is optional (#38): a name alone makes the world it always made. */
  createWorld: (name: string, profile?: Partial<WorldProfile>) =>
    request<{ id: string }>("POST", "/api/worlds", { name, ...profile }).then((r) => {
      invalidateConfigCache();
      return r;
    }),
  renameWorld: (wid: string, name: string) =>
    request<{ id: string; name: string }>("PUT", `/api/worlds/${wid}`,
                                          { name }).then(notifyShell),
  /** Edit the world's profile and/or name (#38). A field left out of `patch`
   *  is left as it is on the server. */
  updateWorld: (wid: string, patch: Partial<WorldProfile> & { name?: string }) =>
    request<{ id: string; name: string }>("PUT", `/api/worlds/${wid}`, patch).then(notifyShell),
  deleteWorld: (wid: string) => request<{ ok: boolean }>("DELETE", `/api/worlds/${wid}`),
  /** Fork `wid` into a brand-new world called `name` (#41) — a deep copy of the
   *  whole directory, sharing nothing with the world it came from and changing
   *  nothing about it. No `invalidateConfigCache`, unlike `createWorld` and
   *  `importWorld`: forking needs a world to fork, so `first_run` was already
   *  false before the call and the cached config still says so. */
  forkWorld: (wid: string, name: string) =>
    request<{ id: string }>("POST", `/api/worlds/${wid}/fork`, { name }),

  // world bundles (#54). Export is a plain href so the browser streams the zip
  // straight to disk -- a world runs to a gigabyte, which is not something to
  // pull through fetch and hold in a Blob.
  exportWorldUrl: (wid: string) => `/api/worlds/${wid}/export.zip`,
  importWorld: (file: Blob) =>
    postZip<{ id: string }>("/api/worlds/import", file).then((res) => {
      invalidateConfigCache();   // same as createWorld: this changes `first_run`
      return res;
    }),

  /** Remembered (see "remembered reads" in `../freshness`), which is what lets the world
   *  page paint its header and its column's numbers on the first render of a
   *  revisit. */
  getWorld: (wid: string) =>
    remembering(memoKey("world", wid), request<WorldDetail>("GET", `/api/worlds/${wid}`)),
  /** The last `getWorld(wid)` answer from this store, or `undefined`. A value
   *  to paint while that read is in flight -- never instead of making it. */
  rememberedWorld: (wid: string) => recall<WorldDetail>(memoKey("world", wid)),

  /** Every category an entity may be filed under, server-side and in its own
   *  order. Read by the import review tables so their per-row Category
   *  dropdown is the store's list rather than a copy of it (#138). */
  entityKinds: () => request<{ kinds: string[] }>("GET", "/api/entity-kinds"),

  // Membership queries report stored refs, including a well-formed dangling actor.
  getActorMembership: (scope: EntityScope, actor: string) =>
    request<ActorMembershipResponse>("GET", `${membershipBase(scope)}/membership?actor=${encodeURIComponent(actor)}`),

  // entities, world or campaign scope
  listEntities: (scope: EntityScope, kind: EntityKind, fresh = false) =>
    request<EntitySummary[]>("GET", `${entityBase(scope)}/${kind}`, undefined, { fresh }),
  createEntity: (scope: EntityScope, kind: EntityKind,
                 body: { name: string; body?: string; keys?: string; owners?: string;
                         secrecy?: string; fields?: Record<string, string> }) =>
    request<{ id: string }>("POST", `${entityBase(scope)}/${kind}`, body),
  readEntity: (scope: EntityScope, kind: EntityKind, id: string) =>
    request<EntityDetail>("GET", `${entityBase(scope)}/${kind}/${id}`),
  updateEntity: (scope: EntityScope, kind: EntityKind, id: string,
                 patch: { name?: string; body?: string; keys?: string; owners?: string;
                          secrecy?: string; fields?: Record<string, string>; rev?: string;
                          group_ref_bindings?: GroupRefBinding[] }) =>
    request<{ ok: boolean }>("PUT", `${entityBase(scope)}/${kind}/${id}`, patch),
  /** What a record's stashed SillyTavern settings would still add: the native
   *  fields they map to, and the stash keys that map to nothing. Read-only.
   *  `inherited` (campaign scope only) is a record the campaign reads from its
   *  world: its settings are adopted there, and applying here is refused. */
  previewAdoptSt: (scope: EntityScope, kind: EntityKind, id: string) =>
    request<{ fields: Record<string, string>; unmapped: string[]; inherited?: boolean }>(
      "GET", `${entityBase(scope)}/${kind}/${id}/adopt-st`),
  adoptSt: (scope: EntityScope, kind: EntityKind, id: string) =>
    request<{ applied: Record<string, string> }>(
      "POST", `${entityBase(scope)}/${kind}/${id}/adopt-st`),
  /** Every record of the world (or the campaign's own), all kinds. */
  previewAdoptStAll: (scope: EntityScope) =>
    request<{ pending: number }>("GET", `${entityBase(scope)}/adopt-st`, undefined, { fresh: true }),
  adoptStAll: (scope: EntityScope) =>
    request<{ applied: { kind: string; id: string; fields: Record<string, string> }[];
              skipped: { kind: string; id: string; reason: string }[] }>(
      "POST", `${entityBase(scope)}/adopt-st`),
  deleteEntity: (scope: EntityScope, kind: EntityKind, id: string) =>
    request<{ ok: boolean }>("DELETE", `${entityBase(scope)}/${kind}/${id}`),
  /** Move a record to another generic kind, keeping its id where the
   *  destination is free (#119). World scope sweeps every campaign of that
   *  world and reports which; campaign scope moves only that campaign's copy.
   *  `rev` is the same precondition a save carries: the record is about to be
   *  moved, and moving text somebody else has rewritten is the write it
   *  refuses. The returned id is the one to navigate to -- it differs from
   *  `id` when the destination already held that slug. */
  reclassifyEntity: (scope: EntityScope, kind: EntityKind, id: string,
                     to: EntityKind, rev?: string | null) =>
    request<{ id: string; campaigns?: string[] }>(
      "POST", `${entityBase(scope)}/${kind}/${id}/reclassify`,
      { to, ...(rev ? { rev } : {}) }),

  // library moves: campaign -> world, and back (#52, #53, #60). `kind` is a
  // LibraryKind rather than EntityKind: promote and the status read carry
  // actors too, which the flat-entity CRUD above never does.
  libraryStatus: (cid: string, kind: LibraryKind, id: string) =>
    request<LibraryStatus>("GET", `/api/campaigns/${cid}/${kind}/${id}/library`),
  promoteToLibrary: (cid: string, kind: LibraryKind, id: string) =>
    request<{ ok: boolean }>("POST", `/api/campaigns/${cid}/${kind}/${id}/promote`),
  pushToLibrary: (cid: string, kind: LibraryKind, id: string, force = false) =>
    request<{ ok: boolean }>("POST", `/api/campaigns/${cid}/${kind}/${id}/push`, { force }),
  libraryDependents: (wid: string, kind: LibraryKind, id: string) =>
    request<LibraryDependent[]>("GET", `/api/worlds/${wid}/${kind}/${id}/dependents`),
  demoteFromLibrary: (wid: string, kind: LibraryKind, id: string,
                      body: { copy_down: boolean; target?: string | null } = { copy_down: true }) =>
    request<{ copied_down: string[]; dependents: string[] }>(
      "POST", `/api/worlds/${wid}/${kind}/${id}/demote`, body),

  // tags
  listTags: (wid: string) => request<Record<string, string>>("GET", `/api/worlds/${wid}/tags`),
  addTag: (wid: string, name: string) => request<{ id: string }>("POST", `/api/worlds/${wid}/tags`, { name }),
  renameTag: (wid: string, tid: string, name: string) =>
    request<{ id: string; name: string }>("PUT", `/api/worlds/${wid}/tags/${tid}`, { name }),
  deleteTag: (wid: string, tid: string) => request<{ ok: boolean }>("DELETE", `/api/worlds/${wid}/tags/${tid}`),

  // scene import (#92). The same split as the lorebook pair below: parse
  // writes NOTHING and hands back a draft to review, and only sceneImport
  // creates the scene — in one request, so a half-imported scene cannot exist
  // because the browser navigated away between two of them.
  sceneImportParse: (cid: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return requestForm<SceneImportDraft>(`/api/campaigns/${cid}/scenes/import/parse`, form);
  },
  sceneImport: (cid: string, body: {
    title: string; date: string; location: string; pcless: boolean;
    messages: Message[]; cast: { kind: string; id: string; role?: string }[];
    turn_sizes: number[] | null;
  }) => request<{ id: string; messages: number; cast: number }>(
    "POST", `/api/campaigns/${cid}/scenes/import`, body),

  // lorebook import
  lorebookParse: (wid: string, file: File, format: string) => {
    const form = new FormData();
    form.append("file", file);
    form.append("format", format);
    return requestForm<{ entries: LoreEntryDraft[] }>(`/api/worlds/${wid}/lorebook/parse`, form);
  },
  lorebookImport: (wid: string, entries: LoreEntryDraft[]) =>
    request<{ created: { kind: string; id: string }[] }>("POST", `/api/worlds/${wid}/lorebook/import`, { entries }),

  // scenario-card import (#217). The two parse calls write nothing — they hand
  // back a proposal to review; only scenarioImport touches the world.
  scenarioParse: (wid: string, file: File, format: string, signal?: AbortSignal) => {
    const form = new FormData();
    form.append("file", file);
    form.append("format", format);
    return draftRun<ScenarioProposal>({ at: "world", id: wid }, (attempt) =>
      requestForm<{ run: RunHandle }>(`/api/worlds/${wid}/scenario/parse`, form,
                                      "POST", attempt, signal), { signal });
  },
  scenarioParseUrl: (wid: string, url: string, signal?: AbortSignal) =>
    draftRun<ScenarioProposal>({ at: "world", id: wid }, (attempt) =>
      request<{ run: RunHandle }>(
        "POST", `/api/worlds/${wid}/scenario/parse-url`, { url },
        { attempt, signal }), { signal }),
  scenarioImport: (wid: string, proposal: ScenarioProposal, art: boolean) =>
    request<ScenarioImportResult>("POST", `/api/worlds/${wid}/scenario/import`, { ...proposal, art }),
};
