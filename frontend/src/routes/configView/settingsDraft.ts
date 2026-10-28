import type { Config, PromptLayoutSection, SceneContext } from "../../api/client";
import { DRAFT_FIELDS, type Draft } from "../../configSections";

/** Every config field this page edits. One list, because it is what the draft
 *  is built from, what the dirty count is counted over, and what Save sends —
 *  three copies of the same list of names is how a field gets added to the
 *  form and quietly never saved. */
/** The floors `store/logs.py` accepts, quietest first — `logs.FLOORS`, which
 *  deliberately stops at `error` and does not include `critical`: the error
 *  store is a view over this file's ERROR rows, so a floor above them would be
 *  a setting that silently switches #156 off. The server clamps a hand-edited
 *  config the same way.
 *
 *  Spelled here rather than fetched: it is the vocabulary of a `<select>`, and
 *  a dropdown that cannot render until a request lands flickers on every
 *  visit. `GET /logs/level` reports the one actually in force. */
export const LOG_LEVELS = ["debug", "info", "warning", "error"] as const;

/** A stored `log_level` as the floor actually in force, mirroring
 *  `logs.level_name` then `logs.FLOORS`'s clamp.
 *
 *  A store written before `critical` stopped being offered still holds it, and
 *  without this the select renders with no matching option (showing the
 *  first), reads as unchanged against the dirty count, and writes `critical`
 *  straight back on the next save — against a backend that clamps it to
 *  `error` anyway. Anything unrecognized falls to the default the same way the
 *  server's `level_name` does, so the control never shows a floor that is not
 *  the one being applied. */
function normalizeFloor(value: string): string {
  const level = (value || "").trim().toLowerCase();
  if (level === "critical") return "error";          // clamped by `logs.FLOORS`
  return (LOG_LEVELS as readonly string[]).includes(level) ? level : "info";
}

/** The saved state, in the shape the form edits. `??` guards a store written
 *  by an older build that has never heard of one of these keys: absent, it
 *  would make the field permanently dirty against `undefined` and send
 *  `undefined` back on every Save. */
export function draftOf(c: Config): Draft {
  const d = {} as Draft;
  for (const f of DRAFT_FIELDS) d[f] = c[f] ?? "";
  // The one field that is not stored as it is edited. A store from the
  // three-theme era still holds `codex`/`manuscript`/`astral`, which matches no
  // segment of the picker — normalized here so the baseline the dirty count
  // compares against is the value the control can actually show, and an
  // untouched legacy theme does not read as an unsaved change.
  d.log_level = normalizeFloor(d.log_level);
  return d;
}

/** Whether two layouts would store the same thing. Compared field by field
 *  rather than by JSON string so a key-order change in the API response cannot
 *  read as an edit the reader never made. */
export function sameLayout(a: PromptLayoutSection[], b: PromptLayoutSection[]) {
  return a.length === b.length && a.every((row, i) =>
    row.id === b[i].id && row.label === b[i].label && row.enabled === b[i].enabled);
}

/** What the context bar is drawn from: a prompt some campaign actually built,
 *  and which campaign that was. */
export type Probe = { ctx: SceneContext; campaign: string } | null;

