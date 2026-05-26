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

export const importSceneApi = {
  preview: (campaignId: string, path: string) =>
    api.post<ImportPreviewResponse>(
      `/api/campaigns/${enc(campaignId)}/scenes/import/preview`,
      { path },
    ),

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
          onProgress(JSON.parse(data));
        }
        if (type === "result") {
          sceneId = JSON.parse(data).scene_id;
          void reader.cancel();
          break outer;
        }
        if (type === "error") {
          void reader.cancel();
          const errPayload = JSON.parse(data);
          const errMsg = errPayload.detail ?? "Import pipeline error";
          const errStatus = typeof errPayload.status === "number" ? errPayload.status : 500;
          throw new ApiError(errStatus, errMsg, errMsg);
        }
      }
    }
    if (!sceneId) throw new Error("Import ended without result");
    return sceneId;
  },
};
