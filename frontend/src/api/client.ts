import { parseSSEChunk, type ChatEvent, type LocalizeEvent, type ChubGalleryEvent } from "./stream";

export class ApiError extends Error {
  constructor(public status: number, public detail: string, public kind?: string) {
    super(detail);
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new ApiError(res.status, data.detail ?? res.statusText, data.kind);
  }
  return res.json() as Promise<T>;
}

async function requestForm<T>(path: string, form: FormData, method = "POST"): Promise<T> {
  const res = await fetch(path, { method, body: form });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new ApiError(res.status, data.detail ?? res.statusText, data.kind);
  }
  return res.json() as Promise<T>;
}

export type Config = {
  model: string; theme: string; key_set: boolean; system_prompt: string;
  quote_color: string; user_label: string; assistant_label: string;
};
export type DataDirInfo = {
  data_dir: string;
  default: string;
  is_default: boolean;
  source: "env" | "custom" | "default";
  exists: boolean;
};
export type WorldMeta = {
  id: string;
  name: string;
  created: string;
  updated: string;
  counts: Record<string, number>;
};
export type CampaignMeta = {
  id: string;
  name: string;
  world: string;
  created: string;
  updated: string;
  scenes: number;
  last_scene: string;
};
export type SceneMeta = { id: string; title: string; model: string; created: string; updated: string };
export type Message = { role: "user" | "assistant"; content: string; speaker?: string };
export type Scene = { meta: { id: string; title: string }; messages: Message[] };

// entities (locations | lore)
export type EntityKind = "locations" | "lore";
export type EntityScope = { kind: "world" | "campaign"; id: string };
export type EntitySummary = { id: string; name: string; keys?: string; owners?: string };
export type EntityDetail = { meta: { id: string; name: string; keys?: string; owners?: string }; body: string };

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
  [k: string]: unknown;
};
export type Card = { spec: string; spec_version: string; data: CardData };
export type VersionRef = { id: string; name: string };
export type CharacterSummary = { id: string; name: string; default_version: string; has_avatar?: boolean; versions: VersionRef[] };
export type CharacterDetail = {
  meta: { id: string; name: string; default_version: string; birthdate?: string };
  versions: { id: string; name: string; card: Card; images?: string[]; chub_source?: string; is_chub?: boolean }[];
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
export type Persona = { name: string; pronouns: string; summary: string; description: string; birthdate?: string };
export type PCSummary = { id: string; name: string; tags: string[]; default_version: string; versions: VersionRef[] };
export type PCDetail = {
  meta: { id: string; name: string; tags: string[]; default_version: string };
  versions: { id: string; name: string; persona: Persona }[];
};

// greetings & plot maps
export type Greeting = {
  id: string;
  name: string;
  character: string;
  version: string;
  present: string[];
  requires_tags: string[];
  predecessor_join: "all" | "any";
};
export type Edges = { leads_to: string[]; excludes: string[] };
export type GreetingDetail = { meta: Greeting; body: string; edges: Edges; predecessors: string[] };
export type GreetingDraft = {
  name: string;
  character: string;
  version: string;
  body?: string;
  present?: string[];
  requires_tags?: string[];
  predecessor_join?: "all" | "any";
};
export type Availability = { id: string; name: string; available: boolean; reasons: string[] };

// cast
export type Actor = { kind: "characters" | "pcs"; id: string; role: "player" | "npc" };
export type RosterEntry = { kind: string; id: string; version: string; role: string; scenes: string[] };
export type SceneLocationRef = { id: string; name: string };
export type SceneLocation = { current: SceneLocationRef | null; visited: SceneLocationRef[] };
export type SceneDatetimeCast = { kind: string; id: string; name: string; age: number | null; birthday_today: boolean };
export type SceneDatetimeFacts = {
  native: string; friendly: string; weekday: string; secondary_friendly: string | null;
  holidays_today: string[]; upcoming: { name: string; in_days: number } | null; cast: SceneDatetimeCast[];
};
export type SceneDatetime = { current: SceneDatetimeFacts | null; history: string[] };
export type CalendarBlock = {
  provider: string; region: string;
  custom_holidays: Array<{ name: string; month: number; day?: number; nth?: number; weekday?: number }>;
  anchor: { native: string; gregorian: string } | null;
};
export type CalendarConfig = { primary: CalendarBlock; secondary: CalendarBlock | null; confirmed: boolean };
export type ContextSection = { label: string; text: string; tokens: number };
export type SceneContext = { model: string; total_tokens: number; sections: ContextSection[] };
export type CastDetail = { kind: "characters" | "pcs"; id: string; name: string; version: string; body: string };
export type TimelineEvent = { date: string; text: string };
export type StagedEdit = {
  id: string; kind: "character_state" | "lore" | "authored" | "relationship" | "bond" | "plot";
  target: { kind: string; id: string }; label: string; field: string;
  before: string; after: string; authored: boolean;
  payload?: Record<string, unknown>;
};
export type SceneAbsorb = {
  one_line: string; summary: string; keywords: string[];
  timeline_events: TimelineEvent[]; cast: string[]; location: string; date: string;
  edits: StagedEdit[];
};
export type SceneSuggestion = {
  title: string; premise: string;
  cast: { kind: string; id: string; name: string }[];
  location: { id: string; name: string } | null;
};
export type ChronicleEntry = {
  id: string; one_line: string; summary: string; keywords: string[];
  cast: string[]; location: string; date: string; absorbed: string;
};
export type DiffLine = { op: "equal" | "insert" | "delete"; text: string };
export type FieldDiff = { field: string; label: string; diff: DiffLine[] };
export type RecordChange = {
  ref: { kind: string; id: string }; name: string;
  scene: { id: string; title: string; date: string };
  fields: FieldDiff[];
};

// lorebook import
export type LoreEntryDraft = { name: string; keys: string[]; body: string; category: EntityKind };

function entityBase(scope: EntityScope): string {
  return scope.kind === "world" ? `/api/worlds/${scope.id}` : `/api/campaigns/${scope.id}`;
}

async function streamPost<T = ChatEvent>(
  path: string,
  body: unknown,
  onEvent: (e: T) => void,
): Promise<void> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok || !res.body) {
    const data = await res.json().catch(() => ({}));
    throw new ApiError(res.status, data.detail ?? res.statusText, data.kind);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer = parseSSEChunk<T>(buffer, decoder.decode(value, { stream: true }), onEvent);
  }
}

export const api = {
  getConfig: () => request<Config>("GET", "/api/config"),
  putConfig: (body: Partial<{ model: string; theme: string; openrouter_key: string; system_prompt: string; quote_color: string; user_label: string; assistant_label: string }>) =>
    request<Config>("PUT", "/api/config", body),
  getDataDir: () => request<DataDirInfo>("GET", "/api/config/data-dir"),
  putDataDir: (data_dir: string | null) =>
    request<DataDirInfo>("PUT", "/api/config/data-dir", { data_dir }),

  // worlds
  listWorlds: () => request<WorldMeta[]>("GET", "/api/worlds"),
  createWorld: (name: string) => request<{ id: string }>("POST", "/api/worlds", { name }),
  renameWorld: (wid: string, name: string) =>
    request<{ id: string; name: string }>("PUT", `/api/worlds/${wid}`, { name }),
  deleteWorld: (wid: string) => request<{ ok: boolean }>("DELETE", `/api/worlds/${wid}`),

  // campaigns
  listCampaigns: () => request<CampaignMeta[]>("GET", "/api/campaigns"),
  createCampaign: (name: string, world: string, region?: string) =>
    request<{ id: string }>("POST", "/api/campaigns", region ? { name, world, region } : { name, world }),
  getCampaign: (cid: string) =>
    request<{ meta: CampaignMeta; body: string }>("GET", `/api/campaigns/${cid}`),
  renameCampaign: (cid: string, name: string) =>
    request<{ id: string; name: string }>("PUT", `/api/campaigns/${cid}`, { name }),
  deleteCampaign: (cid: string) => request<{ ok: boolean }>("DELETE", `/api/campaigns/${cid}`),
  campaignChanges: (cid: string) =>
    request<RecordChange[]>("GET", `/api/campaigns/${cid}/changes`),

  // scenes
  listScenes: (cid: string) => request<SceneMeta[]>("GET", `/api/campaigns/${cid}/scenes`),
  createScene: (cid: string, title?: string) =>
    request<{ id: string }>("POST", `/api/campaigns/${cid}/scenes`, { title }),
  getScene: (cid: string, sid: string) =>
    request<Scene>("GET", `/api/campaigns/${cid}/scenes/${sid}`),
  renameScene: (cid: string, sid: string, title: string) =>
    request<{ id: string; title: string }>("PUT", `/api/campaigns/${cid}/scenes/${sid}`, { title }),
  deleteScene: (cid: string, sid: string) =>
    request<{ ok: boolean }>("DELETE", `/api/campaigns/${cid}/scenes/${sid}`),

  chat: (cid: string, sid: string, content: string, onEvent: (e: ChatEvent) => void) =>
    streamPost(`/api/campaigns/${cid}/scenes/${sid}/chat`, { content }, onEvent),
  retry: (cid: string, sid: string, onEvent: (e: ChatEvent) => void) =>
    streamPost(`/api/campaigns/${cid}/scenes/${sid}/retry`, undefined, onEvent),
  regenerate: (cid: string, sid: string, onEvent: (e: ChatEvent) => void, guidance?: string) =>
    streamPost(`/api/campaigns/${cid}/scenes/${sid}/regenerate`,
               guidance ? { guidance } : undefined, onEvent),

  getWorld: (wid: string) =>
    request<{ meta: WorldMeta; body: string; counts: Record<string, number> }>("GET", `/api/worlds/${wid}`),

  // entities (locations | lore), world or campaign scope
  listEntities: (scope: EntityScope, kind: EntityKind) =>
    request<EntitySummary[]>("GET", `${entityBase(scope)}/${kind}`),
  createEntity: (scope: EntityScope, kind: EntityKind, body: { name: string; body?: string; keys?: string; owners?: string }) =>
    request<{ id: string }>("POST", `${entityBase(scope)}/${kind}`, body),
  readEntity: (scope: EntityScope, kind: EntityKind, id: string) =>
    request<EntityDetail>("GET", `${entityBase(scope)}/${kind}/${id}`),
  updateEntity: (scope: EntityScope, kind: EntityKind, id: string,
                 patch: { name?: string; body?: string; keys?: string; owners?: string }) =>
    request<{ ok: boolean }>("PUT", `${entityBase(scope)}/${kind}/${id}`, patch),
  deleteEntity: (scope: EntityScope, kind: EntityKind, id: string) =>
    request<{ ok: boolean }>("DELETE", `${entityBase(scope)}/${kind}/${id}`),

  // tags
  listTags: (wid: string) => request<Record<string, string>>("GET", `/api/worlds/${wid}/tags`),
  addTag: (wid: string, name: string) => request<{ id: string }>("POST", `/api/worlds/${wid}/tags`, { name }),
  renameTag: (wid: string, tid: string, name: string) =>
    request<{ id: string; name: string }>("PUT", `/api/worlds/${wid}/tags/${tid}`, { name }),
  deleteTag: (wid: string, tid: string) => request<{ ok: boolean }>("DELETE", `/api/worlds/${wid}/tags/${tid}`),

  // characters
  listCharacters: (wid: string) => request<CharacterSummary[]>("GET", `/api/worlds/${wid}/characters`),
  createCharacter: (wid: string, body: { name: string; version_name?: string; card?: Card }) =>
    request<{ character: string; version: string }>("POST", `/api/worlds/${wid}/characters`, body),
  readCharacter: (wid: string, cid: string) =>
    request<CharacterDetail>("GET", `/api/worlds/${wid}/characters/${cid}`),
  setDefaultVersion: (wid: string, cid: string, vid: string) =>
    request<{ ok: boolean }>("PUT", `/api/worlds/${wid}/characters/${cid}`, { default_version: vid }),
  setCharacterBirthdate: (wid: string, cid: string, birthdate: string) =>
    request<{ ok: boolean }>("PUT", `/api/worlds/${wid}/characters/${cid}/birthdate`, { birthdate }),
  deleteCharacter: (wid: string, cid: string) =>
    request<{ ok: boolean }>("DELETE", `/api/worlds/${wid}/characters/${cid}`),
  getCharacterTagline: (wid: string, cid: string) =>
    request<{ tagline: string }>("GET", `/api/worlds/${wid}/characters/${cid}/tagline`),
  setCharacterTagline: (wid: string, cid: string, tagline: string) =>
    request<{ ok: boolean }>("PUT", `/api/worlds/${wid}/characters/${cid}/tagline`, { tagline }),
  generateCharacterTagline: (wid: string, cid: string) =>
    request<{ tagline: string }>("POST", `/api/worlds/${wid}/characters/${cid}/tagline/generate`),
  createVersion: (wid: string, cid: string, body: { name: string; card: Card }) =>
    request<{ version: string }>("POST", `/api/worlds/${wid}/characters/${cid}/versions`, body),
  updateVersion: (wid: string, cid: string, vid: string, card: Card) =>
    request<{ ok: boolean }>("PUT", `/api/worlds/${wid}/characters/${cid}/versions/${vid}`, { card }),
  deleteVersion: (wid: string, cid: string, vid: string) =>
    request<{ ok: boolean }>("DELETE", `/api/worlds/${wid}/characters/${cid}/versions/${vid}`),
  importCharacter: (wid: string, file: File, format: string, into?: string) => {
    const form = new FormData();
    form.append("file", file);
    form.append("format", format);
    if (into) form.append("into", into);
    return requestForm<{ character: string; version: string }>(`/api/worlds/${wid}/characters/import`, form);
  },
  localizeImages: (wid: string, cid: string, vid: string, onEvent: (e: LocalizeEvent) => void) =>
    streamPost<LocalizeEvent>(
      `/api/worlds/${wid}/characters/${cid}/versions/${vid}/localize`, undefined, onEvent),
  imageUrl: (wid: string, cid: string, vid: string, name: string) =>
    `/api/worlds/${wid}/characters/${cid}/versions/${vid}/images/${name}`,
  campaignImageUrl: (cid: string, char: string, vid: string, name: string) =>
    `/api/campaigns/${cid}/characters/${char}/versions/${vid}/images/${name}`,
  putImage: (wid: string, cid: string, vid: string, name: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return requestForm<{ name: string; ext: string }>(
      `/api/worlds/${wid}/characters/${cid}/versions/${vid}/images/${name}`, form, "PUT");
  },
  deleteImage: (wid: string, cid: string, vid: string, name: string) =>
    request<{ ok: boolean }>("DELETE", `/api/worlds/${wid}/characters/${cid}/versions/${vid}/images/${name}`),
  importCharacterBook: (wid: string, cid: string, vid: string) =>
    request<{ created: { kind: string; id: string }[] }>(
      "POST", `/api/worlds/${wid}/characters/${cid}/versions/${vid}/lorebook/import`),
  importCharacterFromChub: (wid: string, url: string, into?: string, intoVersion?: string) =>
    request<ChubImportResult>(
      "POST", `/api/worlds/${wid}/characters/import/chub`,
      into ? { url, into, into_version: intoVersion } : { url }),
  setCharacterChubSource: (wid: string, cid: string, vid: string, url: string) =>
    request<{ chub_source: string }>(
      "POST", `/api/worlds/${wid}/characters/${cid}/versions/${vid}/chub-source`, { url }),
  clearCharacterChubSource: (wid: string, cid: string, vid: string) =>
    request<{ chub_source: string }>(
      "DELETE", `/api/worlds/${wid}/characters/${cid}/versions/${vid}/chub-source`),
  downloadCharacterChubGallery: (wid: string, cid: string, vid: string, onEvent: (e: ChubGalleryEvent) => void) =>
    streamPost<ChubGalleryEvent>(
      `/api/worlds/${wid}/characters/${cid}/versions/${vid}/chub-gallery`, undefined, onEvent),
  downloadCharacterChubLorebooks: (wid: string, cid: string, vid: string) =>
    request<{ lorebooks_found: number; created: { kind: string; id: string }[] }>(
      "POST", `/api/worlds/${wid}/characters/${cid}/versions/${vid}/chub-lorebooks`),
  findChubUnlinked: (wid: string) =>
    request<{ versions: ChubUnlinkedVersion[] }>("GET", `/api/worlds/${wid}/characters/chub-unlinked`),

  // pcs
  listPCs: (wid: string) => request<PCSummary[]>("GET", `/api/worlds/${wid}/pcs`),
  createCampaignPC: (cid: string, body: { name: string; tags?: string[]; persona?: Persona }) =>
    request<{ pc: string; version: string }>("POST", `/api/campaigns/${cid}/pcs`, body),
  listCampaignPCs: (cid: string) => request<PCSummary[]>("GET", `/api/campaigns/${cid}/pcs`),
  createPC: (wid: string, body: { name: string; tags?: string[]; persona?: Persona }) =>
    request<{ pc: string; version: string }>("POST", `/api/worlds/${wid}/pcs`, body),
  readPC: (wid: string, pid: string) => request<PCDetail>("GET", `/api/worlds/${wid}/pcs/${pid}`),
  updatePC: (wid: string, pid: string, patch: { default_version?: string; tags?: string[] }) =>
    request<{ ok: boolean }>("PUT", `/api/worlds/${wid}/pcs/${pid}`, patch),
  deletePC: (wid: string, pid: string) => request<{ ok: boolean }>("DELETE", `/api/worlds/${wid}/pcs/${pid}`),
  createPCVersion: (wid: string, pid: string, body: { name: string; persona: Persona }) =>
    request<{ version: string }>("POST", `/api/worlds/${wid}/pcs/${pid}/versions`, body),
  updatePCVersion: (wid: string, pid: string, vid: string, persona: Persona) =>
    request<{ ok: boolean }>("PUT", `/api/worlds/${wid}/pcs/${pid}/versions/${vid}`, { persona }),
  deletePCVersion: (wid: string, pid: string, vid: string) =>
    request<{ ok: boolean }>("DELETE", `/api/worlds/${wid}/pcs/${pid}/versions/${vid}`),

  // greetings & plot maps
  listGreetings: (wid: string) => request<Greeting[]>("GET", `/api/worlds/${wid}/greetings`),
  createGreeting: (wid: string, draft: GreetingDraft) =>
    request<{ id: string }>("POST", `/api/worlds/${wid}/greetings`, draft),
  readGreeting: (wid: string, gid: string) =>
    request<GreetingDetail>("GET", `/api/worlds/${wid}/greetings/${gid}`),
  updateGreeting: (wid: string, gid: string,
                   patch: { name?: string; body?: string; present?: string[]; requires_tags?: string[]; predecessor_join?: string }) =>
    request<{ ok: boolean }>("PUT", `/api/worlds/${wid}/greetings/${gid}`, patch),
  deleteGreeting: (wid: string, gid: string) =>
    request<{ ok: boolean }>("DELETE", `/api/worlds/${wid}/greetings/${gid}`),
  setEdges: (wid: string, gid: string, edges: { leads_to?: string[]; excludes?: string[] }) =>
    request<{ ok: boolean }>("PUT", `/api/worlds/${wid}/greetings/${gid}/edges`, edges),
  importGreetings: (wid: string, body: { character: string; version: string }) =>
    request<{ greetings: string[] }>("POST", `/api/worlds/${wid}/greetings/import`, body),

  // campaign cast & play
  listAppearances: (cid: string) => request<RosterEntry[]>("GET", `/api/campaigns/${cid}/appearances`),
  getCast: (cid: string, sid: string) => request<Actor[]>("GET", `/api/campaigns/${cid}/scenes/${sid}/cast`),
  addToCast: (cid: string, sid: string,
              body: { kind: string; id: string; version?: string; role?: string }) =>
    request<{ ok: boolean }>("POST", `/api/campaigns/${cid}/scenes/${sid}/cast`, body),
  availableGreetings: (cid: string) =>
    request<Availability[]>("GET", `/api/campaigns/${cid}/greetings/available`),
  startFromGreeting: (cid: string, sid: string, greeting: string) =>
    request<{ ok: boolean }>("POST", `/api/campaigns/${cid}/scenes/${sid}/start-from-greeting`, { greeting }),
  getSceneLocation: (cid: string, sid: string) =>
    request<SceneLocation>("GET", `/api/campaigns/${cid}/scenes/${sid}/location`),
  setSceneLocation: (cid: string, sid: string, location: string) =>
    request<{ ok: boolean; moved: boolean; name: string }>(
      "PUT", `/api/campaigns/${cid}/scenes/${sid}/location`, { location }),
  getSceneDatetime: (cid: string, sid: string) =>
    request<SceneDatetime>("GET", `/api/campaigns/${cid}/scenes/${sid}/datetime`),
  setSceneDatetime: (cid: string, sid: string, datetime: string) =>
    request<{ ok: boolean; advanced: boolean; friendly: string; id: string }>(
      "PUT", `/api/campaigns/${cid}/scenes/${sid}/datetime`, { datetime }),
  getCalendarConfig: (cid: string) =>
    request<CalendarConfig>("GET", `/api/campaigns/${cid}/calendar`),
  setCalendarConfig: (cid: string, cfg: CalendarConfig) =>
    request<{ ok: boolean }>("PUT", `/api/campaigns/${cid}/calendar`, cfg),
  getSceneContext: (cid: string, sid: string) =>
    request<SceneContext>("GET", `/api/campaigns/${cid}/scenes/${sid}/context`),
  sceneSuggestions: (cid: string) =>
    request<{ suggestions: SceneSuggestion[] }>("POST", `/api/campaigns/${cid}/scene-suggestions`),
  getCastDetail: (cid: string, sid: string, kind: string, id: string) =>
    request<CastDetail>("GET", `/api/campaigns/${cid}/scenes/${sid}/cast/${kind}/${id}`),
  editMessage: (cid: string, sid: string, index: number, content: string) =>
    request<{ ok: boolean }>("PUT", `/api/campaigns/${cid}/scenes/${sid}/messages/${index}`, { content }),
  absorbScene: (cid: string, sid: string) =>
    request<SceneAbsorb>("POST", `/api/campaigns/${cid}/scenes/${sid}/absorb`),
  saveChronicle: (cid: string, sid: string,
                  body: { one_line: string; summary: string; keywords: string[];
                          timeline_events: TimelineEvent[]; edits: StagedEdit[] }) =>
    request<ChronicleEntry & { applied: string[] }>("PUT", `/api/campaigns/${cid}/scenes/${sid}/chronicle`, body),
  getChronicle: (cid: string) =>
    request<ChronicleEntry[]>("GET", `/api/campaigns/${cid}/chronicle`),
  opener: (cid: string, sid: string, prompt: string, onEvent: (e: ChatEvent) => void) =>
    streamPost(`/api/campaigns/${cid}/scenes/${sid}/opener`, { prompt }, onEvent),
  firstPost: (cid: string, sid: string, text: string) =>
    request<{ ok: boolean }>("POST", `/api/campaigns/${cid}/scenes/${sid}/first-post`, { text }),

  // lorebook import
  lorebookParse: (wid: string, file: File, format: string) => {
    const form = new FormData();
    form.append("file", file);
    form.append("format", format);
    return requestForm<{ entries: LoreEntryDraft[] }>(`/api/worlds/${wid}/lorebook/parse`, form);
  },
  lorebookImport: (wid: string, entries: LoreEntryDraft[]) =>
    request<{ created: { kind: string; id: string }[] }>("POST", `/api/worlds/${wid}/lorebook/import`, { entries }),
};
