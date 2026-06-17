export type ChatEvent = { delta?: string; done?: boolean; error?: { detail: string; kind: string } };

// Appends a chunk to `buffer`, emits each complete `data:` event, returns the leftover buffer.
export function parseSSEChunk(
  buffer: string,
  chunk: string,
  emit: (event: ChatEvent) => void,
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
      emit(JSON.parse(data) as ChatEvent);
    } catch {
      // ignore malformed event fragments
    }
  }
  return buffer;
}
