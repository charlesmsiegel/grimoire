// What the story page works out without the app (Sprint 115): the words a record, a reason or a review is said
// in, and the guard that keeps a late answer from drawing over a newer choice.

// A new operation id, unique enough to name one change: kept with its review so a retry reuses it.
export function newOperationId() {
  return "op-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10);
}

const KINDS = { scene: "Scene", accepted_event: "Accepted event", relationship: "Relationship", summary: "Scene summary",
  holdings: "Holdings", evidence: "Evidence", picture: "Picture", placement: "Picture placement", bookmark: "Bookmark",
  instance: "Character record", transcript: "Imported history", chat: "Chat", continuity: "Continuity record" };

export function kindText(kind) { return KINDS[kind] || String(kind || "Record"); }

// One record as a person reads it: "Scene 2: Tam says…" or "Accepted event: tam pays ines."
export function recordText(record) {
  if (!record) return "a record no longer in this campaign";
  const kind = kindText(record.kind);
  const name = record.kind === "scene" && record.number ? kind + " " + record.number : kind;
  const said = record.label ? clip(record.label, 90) : record.id || record.path;
  return name + ": " + said;
}

// Why a record is touched (054's reasons).
const REASONS = {
  scene: "it rests on this scene, exactly as recorded",
  dialogue: "its dialogue is declared to rest on it",
  evidence: "it is evidence drawn from it",
  summary: "a scene summary cites it",
  asset: "a picture or asset is declared to rest on it",
  causal_action: "an action is declared to follow from it",
  relationship: "a relationship it recorded",
  possession: "what someone holds because of it",
  accepted_event: "an accepted event follows from it",
  chronological: "it comes later in the same chat, and may have used it",
  recorded_source: "it was recorded from it",
};

export function reasonText(reason) { return REASONS[reason] || String(reason); }

export function certaintyText(certainty) { return certainty === "known" ? "Known" : "Possible"; }

const ACTIONS = { edit: "Edit it", cut: "Cut it", replace: "Replace it", replay: "Replay it", fork: "Fork from it" };
export function actionText(action) { return ACTIONS[action] || action; }

const DECLARED = { dialogue: "dialogue", causal_action: "an action that follows from it", asset: "a picture or asset" };
export function declaredText(reason) { return DECLARED[reason] || reason; }

// 049's semantic review reason codes, in words. `uncertain` ones say the ancestry is not recorded.
const SEMANTIC = {
  accepted_scene_changed: "an accepted event here rests on this scene exactly as this device has it",
  accepted_scene_missing: "an accepted event rests on a scene that would be missing",
  declared_scene_changed: "a declared dependency rests on this scene as this device has it",
  stale_dependency_endpoint: "a declared dependency names a version of a record that would no longer be there",
  missing_dependency_endpoint: "a declared dependency names a record that is missing",
  legacy_scene_history: "an accepted event of this chat has no record of the scenes it rests on, so its ancestry is uncertain",
  malformed_continuity_sources: "an accepted event's record of its scenes cannot be read, so its ancestry is uncertain",
  malformed_dependency: "a declared dependency cannot be read",
  malformed_semantic_record: "a scene or an accepted event cannot be read",
  unreadable_semantic_sources: "some of the campaign's records cannot be read, so nothing can be shown safe",
  potential_direct_relation_impact: "a relationship was changed directly while this scene was edited: it may depend on it",
};

export function semanticText(code) { return SEMANTIC[code] || String(code); }

// The sentence for what taking another device's version (or its deletion) here would leave unmet. With
// `uncertain`, no recorded premise unmet is not enough to call it safe: the scene's ancestry is not on record.
export function takingText(peer, review, { uncertain = false, deletion = false } = {}) {
  const what = deletion ? "the deletion from " + peer : peer + "'s version";
  if (!review) return deletion ? "Taking the deletion from " + peer + " cannot be reviewed here." : "Its version from " + peer + " cannot be read here.";
  if (!review.reasons.length) {
    return uncertain ? "Taking " + what + " here cannot be shown safe: what rests on this scene is not on record."
      : "Taking " + what + " here leaves no recorded premise unmet.";
  }
  return "Taking " + what + " here would break what rests on it: " + review.reasons.map(semanticText).join("; ") + ".";
}

// What one device's last sync did, from story.divergent's peers.
export function syncText(peer) {
  if (!peer.status) return "No sync with " + peer.peer + " since the app started.";
  const held = peer.held.length;
  if (peer.status === "synced") return "The last sync with " + peer.peer + " finished with nothing held.";
  if (peer.status === "incomplete") return "The last sync with " + peer.peer + " did not finish; nothing it did not finish was changed.";
  return "The last sync with " + peer.peer + " held " + count(held, "record") + " of this campaign for review.";
}

export function count(n, word) { return n + " " + word + (n === 1 ? "" : "s"); }

export function clip(text, most = 60) {
  const value = String(text || "");
  return value.length > most ? value.slice(0, most - 1) + "…" : value;
}

// A counter for requests whose answer is drawn only while nothing newer was asked: `next()` before asking,
// `current(turn)` when the answer comes.
export function latest() {
  let turn = 0;
  return { next: () => ++turn, current: (asked) => asked === turn, bump: () => { turn += 1; } };
}

// The request a review of a declaration makes, from what the person picked and the records as shown.
export function declaration(records, source, dependent, reason) {
  const find = (path) => records.find((item) => item.path === path);
  const from = find(source), to = find(dependent);
  if (!from || !to) return null;
  if (from.path === to.path) return null;
  return { reason, source: { path: from.path, revision: from.revision }, dependent: { path: to.path, revision: to.revision } };
}

// The sentence a declaration is reviewed in.
export function declarationText(plan) {
  if (plan.already_applied) return "This was already recorded: recording it again changes nothing.";
  return "Record that " + recordText(plan.dependent) + " rests on " + recordText(plan.source) + " (" + declaredText(plan.reason) + ").";
}
