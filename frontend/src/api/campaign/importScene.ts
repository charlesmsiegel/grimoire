import { api, ApiError } from "../client";

function enc(s: string): string {
  return encodeURIComponent(s);
}

export interface ImportPreviewResponse {
  post_count: number;
  detected_characters: {
    pc_refs: string[];
    npc_refs: string[];
  };
  sidecar: Record<string, unknown> | null;
}

export interface ImportRequest {
  path: string;
  title: string;
  location_ref?: string | null;
  in_game_start?: string | null;
  in_game_end?: string | null;
  mood?: string | null;
  tags?: string[];
  present_character_refs?: string[];
  present_pc_refs?: string[];
}

export interface ImportProgress {
  step: string;
  current: number;
  total: number;
  detail: string;
}

// SSE `data:` frames can arrive malformed or truncated. Parse at the boundary so
// a bad chunk surfaces a clear, typed error instead of an opaque SyntaxError.
function tryParseFrame(data: string): Record<string, unknown> | null {
  try {
    const parsed: unknown = JSON.parse(data);
    if (!parsed || typeof parsed !== "object") return null;
    return parsed as Record<string, unknown>;
  } catch {
    return null;
  }
}

export const importSceneApi = {
  preview: (campaignId: string, path: string) =>
    api.post<ImportPreviewResponse>(`/api/campaigns/${enc(campaignId)}/scenes/import/preview`, {
      path,
    }),

  import: async (
    campaignId: string,
    body: ImportRequest,
    onProgress: (p: ImportProgress) => void,
  ): Promise<string> => {
    const res = await fetch(`/api/campaigns/${enc(campaignId)}/scenes/import`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      const msg = detail.detail ?? "Import failed";
      throw new ApiError(res.status, msg, msg);
    }
    const reader = res.body!.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    let sceneId = "";
    outer: for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const parts = buf.split("\n\n");
      buf = parts.pop()!;
      for (const part of parts) {
        const eventMatch = part.match(/^event:\s*(\w+)\ndata:\s*(.+)$/s);
        if (!eventMatch) continue;
        const type = eventMatch[1]!;
        const data = eventMatch[2]!;
        if (type === "progress") {
          // Skip a single malformed progress frame rather than aborting the import.
          const payload = tryParseFrame(data);
          if (payload) onProgress(payload as unknown as ImportProgress);
        }
        if (type === "result") {
          void reader.cancel();
          const payload = tryParseFrame(data);
          if (!payload || typeof payload.scene_id !== "string") {
            throw new ApiError(0, "malformed import payload", "malformed import payload");
          }
          sceneId = payload.scene_id;
          break outer;
        }
        if (type === "error") {
          void reader.cancel();
          const payload = tryParseFrame(data);
          if (!payload) {
            throw new ApiError(0, "malformed import payload", "malformed import payload");
          }
          const errMsg =
            typeof payload.detail === "string" ? payload.detail : "Import pipeline error";
          const errStatus = typeof payload.status === "number" ? payload.status : 500;
          throw new ApiError(errStatus, errMsg, errMsg);
        }
      }
    }
    if (!sceneId) throw new Error("Import ended without result");
    return sceneId;
  },
};
