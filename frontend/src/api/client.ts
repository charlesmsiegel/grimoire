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
export type ConvMeta = { id: string; title: string; model: string; created: string; updated: string };
export type Message = { role: "user" | "assistant"; content: string };
export type Conversation = { meta: { id: string; title: string }; messages: Message[] };

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
  listConversations: () => request<ConvMeta[]>("GET", "/api/conversations"),
  createConversation: (title?: string) => request<{ id: string }>("POST", "/api/conversations", { title }),
  getConversation: (id: string) => request<Conversation>("GET", `/api/conversations/${id}`),

  chat: (id: string, content: string, onEvent: (e: ChatEvent) => void) =>
    streamPost(`/api/conversations/${id}/chat`, { content }, onEvent),

  retry: (id: string, onEvent: (e: ChatEvent) => void) =>
    streamPost(`/api/conversations/${id}/retry`, undefined, onEvent),
};
