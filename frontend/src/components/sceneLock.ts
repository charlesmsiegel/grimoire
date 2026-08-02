// A scene's id is its filename, so anything that renames it — the rail's
// rename, a delete, or the *first* date set, which re-slugs — moves the file
// out from under a turn that is streaming into it. `finalize`,
// `_persist_reply` and the abort write all address the scene by the id the
// turn captured, so a move mid-turn strands them: the abort rescue swallows
// `SceneNotFound` and the partial the player watched arrive is gone (#95).
//
// Those surfaces are spread across the rail, the cast panel and the scene
// inspector, and review found them one at a time. The shared string is here so
// a fourth one is a lock and a label rather than a fourth wording — and so
// grepping this name lists every surface that knows about the rule.
//
// Renaming is no longer the only reason to hold the lock. A manual dice roll
// appends a transcript line, and a reroll cancelled before its first token is
// at that moment waiting to restore the reply it deleted — a restore that
// steps over trailing transitions but refuses outright behind a roll, whose
// line must stay in lockstep with rolls.json. Same window, same label, and a
// reply that nothing else holds either way.
export const LOCKED_WHILE_GENERATING = "Not while this scene is generating";
