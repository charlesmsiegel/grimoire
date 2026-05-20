/**
 * Parsing helpers for the ``core.present-cast`` widget payload. Kept in
 * its own module so ``PresentCastChip.tsx`` stays a pure component file
 * (react-refresh dislikes mixing exports).
 */

import { asArray, asNumber, asRecord, asString } from "./widgets/widget-common";

export interface PresentCastChipData {
  character_id: string;
  character_ref?: string;
  name: string;
  portrait_url?: string;
  mood?: { emoji?: string; label?: string };
  current_action?: string;
  internal_thought?: string;
  drift?: { score: number; threshold?: number };
  source?: string;
  pinned_extras?: { key: string; value: string }[];
}

export function parsePresentCast(data: unknown): PresentCastChipData[] {
  const arr =
    asArray(data) ?? asArray(asRecord(data)?.chips ?? asRecord(data)?.cast);
  if (!arr) return [];
  return arr
    .map((raw): PresentCastChipData | null => {
      const r = asRecord(raw);
      if (!r) return null;
      const id = asString(r.character_id) ?? asString(r.id);
      const name = asString(r.name);
      if (!id || !name) return null;
      const moodRec = asRecord(r.mood);
      const mood = moodRec
        ? {
            emoji: asString(moodRec.emoji) ?? undefined,
            label: asString(moodRec.label) ?? undefined,
          }
        : undefined;
      const driftRec = asRecord(r.drift);
      const driftScore = driftRec ? asNumber(driftRec.score) : null;
      const drift =
        driftRec && driftScore !== null
          ? {
              score: driftScore,
              threshold: asNumber(driftRec.threshold) ?? undefined,
            }
          : undefined;
      const pinnedRaw = asArray(r.pinned_extras);
      const pinned_extras = pinnedRaw
        ? pinnedRaw
            .map((item): { key: string; value: string } | null => {
              const er = asRecord(item);
              if (!er) return null;
              const key = asString(er.key);
              const value = asString(er.value);
              if (!key || !value) return null;
              return { key, value };
            })
            .filter((x): x is { key: string; value: string } => x !== null)
        : undefined;
      return {
        character_id: id,
        character_ref: asString(r.character_ref) ?? undefined,
        name,
        portrait_url: asString(r.portrait_url) ?? undefined,
        mood,
        current_action: asString(r.current_action) ?? undefined,
        internal_thought: asString(r.internal_thought) ?? undefined,
        drift,
        source: asString(r.source) ?? undefined,
        pinned_extras,
      };
    })
    .filter((x): x is PresentCastChipData => x !== null);
}
