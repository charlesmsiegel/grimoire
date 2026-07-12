export type RollProposalPayload = {
  id: string;
  check?: string; check_label?: string;
  actor?: string; actor_label?: string;
  difficulty?: number; modifier?: number; reason?: string;
  available?: Record<string, [string, string][]>;
  problems: string[];
};
export type ChatEvent = { delta?: string; done?: boolean; error?: { detail: string; kind: string }; proposal?: RollProposalPayload };

export type LocalizeSummary = {
  total: number;
  localized: number;
  skipped: number;
  failed: number;
  capped: boolean;
};
export type LocalizeEvent = {
  total?: number;
  done?: number;
  summary?: LocalizeSummary;
  error?: { detail: string; kind: string };
};

export type ChubGallerySummary = { attempted: number; stored: number };
export type ChubGalleryEvent = {
  total?: number;
  done?: number;
  summary?: ChubGallerySummary;
  error?: { detail: string; kind: string };
};

// Appends a chunk to `buffer`, emits each complete `data:` event, returns the leftover buffer.
export function parseSSEChunk<T = ChatEvent>(
  buffer: string,
  chunk: string,
  emit: (event: T) => void,
): string {
  buffer += chunk;
  let idx: number;
  while ((idx = buffer.indexOf("\n\n")) !== -1) {
    const raw = buffer.slice(0, idx);
    buffer = buffer.slice(idx + 2);
    const line = raw.split("\n").find((l) => l.startsWith("data:"));
    if (!line) continue;
    const data = line.slice("data:".length).trim();
    if (!data) continue;
    try {
      emit(JSON.parse(data) as T);
    } catch {
      // ignore malformed event fragments
    }
  }
  return buffer;
}
