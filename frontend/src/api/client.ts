import { parseSSEChunk, type ChatEvent } from "./stream";

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

export type Config = { model: string; theme: string; key_set: boolean };
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
};
export type SceneMeta = { id: string; title: string; model: string; created: string; updated: string };
export type Message = { role: "user" | "assistant"; content: string };
export type Scene = { meta: { id: string; title: string }; messages: Message[] };

async function streamPost(
  path: string,
  body: unknown,
  onEvent: (e: ChatEvent) => void,
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
    buffer = parseSSEChunk(buffer, decoder.decode(value, { stream: true }), onEvent);
  }
}

export const api = {
  getConfig: () => request<Config>("GET", "/api/config"),
  putConfig: (body: Partial<{ model: string; theme: string; openrouter_key: string }>) =>
    request<Config>("PUT", "/api/config", body),

  // worlds
  listWorlds: () => request<WorldMeta[]>("GET", "/api/worlds"),
  createWorld: (name: string) => request<{ id: string }>("POST", "/api/worlds", { name }),
  renameWorld: (wid: string, name: string) =>
    request<{ id: string; name: string }>("PUT", `/api/worlds/${wid}`, { name }),
  deleteWorld: (wid: string) => request<{ ok: boolean }>("DELETE", `/api/worlds/${wid}`),

  // campaigns
  listCampaigns: () => request<CampaignMeta[]>("GET", "/api/campaigns"),
  createCampaign: (name: string, world: string) =>
    request<{ id: string }>("POST", "/api/campaigns", { name, world }),
  getCampaign: (cid: string) =>
    request<{ meta: CampaignMeta; body: string }>("GET", `/api/campaigns/${cid}`),
  renameCampaign: (cid: string, name: string) =>
    request<{ id: string; name: string }>("PUT", `/api/campaigns/${cid}`, { name }),
  deleteCampaign: (cid: string) => request<{ ok: boolean }>("DELETE", `/api/campaigns/${cid}`),

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
};
