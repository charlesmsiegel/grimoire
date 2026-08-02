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
// grepping this name lists every rename surface that knows about the rule.
export const LOCKED_WHILE_GENERATING = "Not while this scene is generating";
