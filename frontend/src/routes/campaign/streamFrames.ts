import type { ChatEvent } from "../../api/stream";
import { applyDisplay } from "../../components/play/displayFrames";
import type { PreviewUpdate } from "./useRunSession";

/** One observer's text and part offsets. Outcome/registry policy stays with its caller. */
export function createStreamFrames(render: (update: PreviewUpdate) => void) {
  let acc = "";
  // Where the part a display frame rewrites begins: keep is relative to this
  // part, not to the preceding speakers. A continuation seed is display-only
  // and never contributes to offsets, Retry evidence, or resume text.
  let partStart = 0;
  function accept(e: ChatEvent): boolean {
    if (e.round_start) render({ kind: "round", round: e.round_start });
    else if (e.response_start) {
      partStart = acc.length;
      render({
        kind: "start", part: {
          id: e.response_start.id,
          speaker: e.response_start.speaker, actor_ref: e.response_start.actor_ref,
          offset: partStart, seed: e.response_start.extend?.seed
        }
      });
    } else if (e.thinking_reset || e.thinking_delta) {
      render({ kind: "thinking", reset: !!e.thinking_reset, delta: e.thinking_delta ?? "" });
    } else if (e.response_end) render({ kind: "end", id: e.response_end.id });
    else if (e.delta) { acc += e.delta; render({ kind: "text", text: acc }); }
    else if (e.display) {
      acc = applyDisplay(acc, partStart, e.display);
      render({ kind: "text", text: acc });
    } else return false;
    return true;
  }
  return { accept, text: () => acc };
}
