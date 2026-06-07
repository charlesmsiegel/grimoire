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
    // Arrays are typeof "object" too — require a plain object shape.
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;
    return parsed as Record<string, unknown>;
  } catch {
    return null;
  }
}

// Valid JSON is not necessarily a valid progress frame (e.g. `{}`, string-valued
// counters, or `total: 0`). The dialog computes `current / total * 100`, so the
// counters must be finite with a positive total — otherwise the bar renders a
// `NaN%`/`Infinity%` width. Validate the shape and bounds before accepting it.
function isImportProgress(
  p: Record<string, unknown>,
): p is ImportProgress & Record<string, unknown> {
  return (
    typeof p.step === "string" &&
    typeof p.detail === "string" &&
    typeof p.current === "number" &&
    Number.isFinite(p.current) &&
    p.current >= 0 &&
    typeof p.total === "number" &&
    Number.isFinite(p.total) &&
    p.total > 0
  );
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
    // Handle one SSE frame. Returns the scene id for a terminal `result` frame,
    // null for progress/unrecognized frames, and throws a typed ApiError on a
    // malformed result/error frame or a well-formed error frame.
    const handleFrame = (part: string): string | null => {
      const eventMatch = part.match(/^event:\s*(\w+)\ndata:\s*(.+)$/s);
      if (!eventMatch) return null;
      const type = eventMatch[1]!;
      const data = eventMatch[2]!;
      if (type === "progress") {
        // Skip a malformed or wrong-shaped progress frame rather than aborting.
        const payload = tryParseFrame(data);
        if (payload && isImportProgress(payload)) onProgress(payload);
        return null;
      }
      if (type === "result") {
        const payload = tryParseFrame(data);
        if (!payload || typeof payload.scene_id !== "string" || !payload.scene_id) {
          throw new ApiError(0, "malformed import payload", "malformed import payload");
        }
        return payload.scene_id;
      }
      if (type === "error") {
        const payload = tryParseFrame(data);
        if (!payload) {
          throw new ApiError(0, "malformed import payload", "malformed import payload");
        }
        const errMsg =
          typeof payload.detail === "string" ? payload.detail : "Import pipeline error";
        const errStatus = typeof payload.status === "number" ? payload.status : 500;
        throw new ApiError(errStatus, errMsg, errMsg);
      }
      return null;
    };

    const reader = res.body!.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    let sceneId: string | null = null;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) {
        // A truncated stream may leave a final frame in `buf` without its
        // trailing blank-line delimiter — process it so it still surfaces a
        // typed result/error instead of the generic "ended without result".
        const tail = buf.trim();
        if (tail) sceneId = handleFrame(tail);
        break;
      }
      buf += decoder.decode(value, { stream: true });
      const parts = buf.split("\n\n");
      buf = parts.pop()!;
      for (const part of parts) {
        sceneId = handleFrame(part);
        if (sceneId !== null) break;
      }
      if (sceneId !== null) {
        void reader.cancel();
        break;
      }
    }
    if (!sceneId) throw new Error("Import ended without result");
    return sceneId;
  },
};
