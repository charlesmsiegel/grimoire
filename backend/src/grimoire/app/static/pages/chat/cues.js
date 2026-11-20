// The VN stage's authored cue timeline (072): view metadata kept on this device only, never in the library.
// The authorized scene stays the sole transcript owner: nothing here reads, writes or rewrites a line.
//
// A cue belongs to one kept turn and records only what changes there. Blocking (who stands where, in which
// art, or that they are off stage) and the background carry forward from turn to turn until a later cue
// changes them; focus, the transition and the note belong to the cue's own turn. `timeline` folds the cues
// over the kept turns in transcript order, so stepping from turn to turn reads as one staged scene, and every
// change it makes is also written out as a plain stage direction, for screen readers and for anyone who turns
// motion off.
//
// Who may stand on the stage at a turn is historical: the take's `present` (the textapi view's
// `table_present`), never the cast now. For a take written with no stage that is its whole cast, so an actor
// missing from it had left: their blocking ends. On a 041 stage (`staged`) it is only the player characters
// and the speakers the table heard, so an NPC missing from it may still be there, unheard: their blocking is
// kept but not drawn, and no departure is claimed. Either way nobody is drawn where the table could not place
// them, so a later arrival and a hidden NPC stay true to what the table knew.

export const POSITIONS = ["left", "center", "right"];
export const TRANSITIONS = ["none", "fade", "move"];
export const MAX_CUES = 100;
export const MAX_NOTE = 1000;
const STORE_V2 = "grimoire.vn.v2:";
const STORE_V1 = "grimoire.vn.v1:"; // #694's per-tab cues, read once into the device-local store

// The speakers of a kept turn (its post, then the kept take's heard lines), first appearance first, under the
// label the line was written with: a renamed character keeps its historical name here.
export function eligibleActors(turn) {
  if (!turn?.kept) return [];
  const actors = new Map();
  for (const item of [turn.post, ...turn.kept.lines].filter(Boolean))
    if (item.actor && !actors.has(item.actor))
      actors.set(item.actor, { actor: item.actor, name: item.speaker || item.actor });
  return [...actors.values()];
}

// Who the table can place on the stage at a kept turn: its speakers, then the rest of the take's historical
// `present`. A silent actor with no name now (no longer in the cast) takes `labels`' name, a label it spoke
// under in this chat, else its id. A payload from before `present` existed offers the speakers alone.
export function presentActors(turn, labels) {
  const found = new Map(eligibleActors(turn).map((a) => [a.actor, { ...a, speaking: true }]));
  for (const member of turn?.kept?.present || [])
    if (member?.actor && !found.has(member.actor))
      found.set(member.actor, { actor: member.actor, name: member.name || labels?.get(member.actor) || member.actor, speaking: false });
  return [...found.values()];
}

// An actor's shown art the stage may use: expression sprites (any outfit) first, then portrait and avatar.
// Hidden and unavailable slots are never offered.
export function actorArt(member) {
  return (member?.art?.slots || [])
    .filter((s) => s.state === "shown" && s.image && (s.slot.startsWith("expr.") || ["portrait", "avatar"].includes(s.slot)))
    .sort((a, b) => Number(b.slot.startsWith("expr.")) - Number(a.slot.startsWith("expr.")));
}

// The outfit of an expression slot (`expr.<outfit>.<name>`), or null for a portrait or avatar.
export function outfitOf(slot) {
  const parts = typeof slot === "string" ? slot.split(".") : [];
  return parts.length === 3 && parts[0] === "expr" ? parts[1] : null;
}

const isText = (v, max = 512) => typeof v === "string" && v.length <= max;
function validCue(c, version) {
  if (!c || typeof c !== "object" || Array.isArray(c)) return false;
  const positions = version === 1 ? POSITIONS : ["", "off", ...POSITIONS];
  const transitions = version === 1 ? ["none", "fade"] : TRANSITIONS;
  return (
    isText(c.note, MAX_NOTE) &&
    transitions.includes(c.transition) &&
    isText(c.focus) &&
    isText(c.background) &&
    (c.once === undefined || typeof c.once === "boolean") &&
    c.actors && typeof c.actors === "object" && !Array.isArray(c.actors) &&
    Object.keys(c.actors).length <= 50 &&
    Object.values(c.actors).every((a) => a && positions.includes(a.position) && isText(a.art))
  );
}
export function validCues(cues, version = 2) {
  return (
    !!cues && typeof cues === "object" && !Array.isArray(cues) &&
    Object.keys(cues).length <= MAX_CUES &&
    Object.values(cues).every((c) => validCue(c, version))
  );
}

// #694's cue applied to its own turn only: an empty focus meant none, an empty background none, and the turns
// after it were unstaged. `once` keeps that: the timeline puts blocking and background back after its turn.
function fromV1(cue) {
  return { ...cue, focus: cue.focus || "none", actors: { ...cue.actors }, once: true };
}

// A blank cue changes nothing: every actor keeps its blocking, the background is inherited, focus follows the
// speaker.
export const blankCue = () => ({ actors: {}, focus: "auto", background: "inherit", transition: "none", note: "" });

// The device-local cue store for one chat. Every failure is reported in `warning`, never thrown.
//
// localStorage is shared by every tab of this device, so a change is applied to what is stored now, never to
// this tab's copy: `put` and `remove` read the stored cues, change one turn and write them back, so two tabs
// editing different turns both keep their cues. Stored cues this build cannot read (malformed, or a newer
// version) are kept as they are until Reset local cues: changing one turn is refused rather than overwriting
// them. `reload` takes up another tab's change (the page calls it on the `storage` event).
export function cueStore(chat, storage, legacy) {
  let cues = {}, warning = "", unreadable = false;
  const probe = (get) => { try { return get(); } catch { return null; } };
  // Only the page's own stores carry #694's cues forward: a caller that hands in a store hands in both.
  if (!storage) {
    storage = probe(() => globalThis.localStorage);
    if (legacy === undefined) legacy = probe(() => globalThis.sessionStorage);
  }
  const key = STORE_V2 + chat;
  // What is stored now: {cues} when readable (or absent), else a reason. Throws only when storage is unusable.
  function stored() {
    if (!storage) throw Error("unavailable");
    const raw = storage.getItem(key);
    if (raw === null || raw === undefined) return { cues: null };
    let parsed;
    try { parsed = JSON.parse(raw); } catch { return { problem: "malformed" }; }
    if (!parsed || typeof parsed !== "object") return { problem: "malformed" };
    if (typeof parsed.version === "number" && parsed.version > 2) return { problem: "newer" };
    if (parsed.version !== 2 || !validCues(parsed.cues)) return { problem: "malformed" };
    return { cues: parsed.cues };
  }
  const describe = (problem) => problem === "newer"
    ? "Local cues were saved by a newer version of the app; they are kept. Reset local cues to replace them."
    : "Malformed local cues. Reset local cues to recover.";
  function load() {
    unreadable = false;
    try {
      const now = stored();
      if (now.problem) {
        unreadable = true;
        cues = {};
        warning = describe(now.problem);
        return;
      }
      if (now.cues) cues = now.cues;
      else {
        const old = legacy ? probe(() => legacy.getItem(STORE_V1 + chat)) : null;
        const parsed = old ? probe(() => JSON.parse(old)) : null;
        cues = {};
        if (parsed?.version === 1 && validCues(parsed.cues, 1)) {
          cues = Object.fromEntries(Object.entries(parsed.cues).map(([id, cue]) => [id, fromV1(cue)]));
          storage.setItem(key, JSON.stringify({ version: 2, cues }));
        }
      }
      warning = "";
    } catch {
      warning = "Local cues not saved: storage unavailable.";
    }
  }
  function write(next) {
    cues = next;
    try {
      storage.setItem(key, JSON.stringify({ version: 2, cues }));
      warning = "";
      unreadable = false;
      return true;
    } catch {
      warning = "Local cues not saved: storage unavailable or full; changes remain in memory.";
      return false;
    }
  }
  // One turn's change, applied to the cues stored now (or to this tab's copy when storage is unusable).
  function change(apply) {
    let base = cues;
    try {
      const now = stored();
      if (now.problem) {
        unreadable = true;
        warning = describe(now.problem);
        return false;
      }
      base = now.cues || {};
    } catch { /* unusable storage: change the copy in memory, and say so on write */ }
    const next = apply({ ...base });
    if (!validCues(next)) {
      warning = `Invalid cues: maximum ${MAX_CUES} cues and ${MAX_NOTE} note characters.`;
      return false;
    }
    return write(next);
  }
  load();
  return {
    get cues() { return cues; },
    get warning() { return warning; },
    get unreadable() { return unreadable; },
    reload: load,
    put: (turn, cue) => change((next) => ({ ...next, [turn]: cue })),
    remove: (turn) => change((next) => { delete next[turn]; return next; }),
    // Replaces everything stored for this chat: Reset local cues, the one way past unreadable stored cues.
    save(next) {
      if (!validCues(next)) {
        warning = `Invalid cues: maximum ${MAX_CUES} cues and ${MAX_NOTE} note characters.`;
        return false;
      }
      return write(next);
    },
  };
}

// The cue as it is saved: only the actors it changes, so an untouched actor keeps carrying its blocking. An
// edited cue is a timeline cue: it no longer applies to its turn alone.
export function trimCue(cue) {
  const actors = {};
  for (const [id, a] of Object.entries(cue.actors || {}))
    if (a.position || a.art) actors[id] = { position: a.position || "", art: a.art || "" };
  const { once: _once, ...rest } = cue;
  return { ...rest, actors };
}

// An unblocked speaker takes the first free place, in this order, so two speakers never share a place while
// one is free. With every place taken it joins the centre.
const AUTO_ORDER = ["center", "left", "right"];
function freePlace(blocking, except) {
  const taken = new Set([...blocking].filter(([id, b]) => id !== except && b.position !== "off").map(([, b]) => b.position));
  return AUTO_ORDER.find((p) => !taken.has(p)) || "center";
}
const copy = (blocking) => new Map([...blocking].map(([id, b]) => [id, { ...b }]));

// The labels each actor spoke under, in transcript order, for a silent actor no longer in the cast.
export function speakerLabels(kept) {
  const labels = new Map();
  for (const turn of kept)
    for (const item of [turn.post, ...turn.kept.lines].filter(Boolean))
      if (item.actor && item.speaker && !labels.has(item.actor)) labels.set(item.actor, item.speaker);
  return labels;
}

// The staged scene at every kept turn, in transcript order. Each entry:
//   turn, number, cue (authored, or null), actors [{actor, name, speaking, position, art, from, entered}],
//   focus {actor, source: authored|automatic|none}, background ("" none, else an image ref), transition,
//   note, directions (plain-text stage directions for the change into this turn), unavailable (authored
//   references this turn cannot honour).
// `blocking` holds every actor's carried place, including "off"; `shownAt` who was drawn at the previous turn,
// so an entrance or a move is told against what the reader last saw.
export function timeline(turns, cues) {
  const kept = (turns || []).filter((t) => t?.kept);
  const labels = speakerLabels(kept);
  let blocking = new Map(); // actor -> {position ("off" too), art, name}
  let background = "";
  let shownAt = new Map(); // actor -> position drawn at the previous turn
  const entries = [];
  for (const turn of kept) {
    const cue = cues?.[turn.turn] || null;
    const staged = !!turn.kept.staged;
    const present = presentActors(turn, labels);
    const byId = new Map(present.map((a) => [a.actor, a]));
    const before = copy(blocking), backgroundBefore = background;
    const directions = [], unavailable = [];
    const nameOf = (id) => byId.get(id)?.name || before.get(id)?.name || labels.get(id) || id;
    // Who left: on an unstaged take an actor missing from `present` was not in the scene, so their blocking
    // ends. On a stage they may only be unheard: keep it, draw nothing, claim nothing.
    if (!staged)
      for (const [id, b] of before)
        if (!byId.has(id)) {
          blocking.delete(id);
          if (shownAt.has(id)) directions.push(`${b.name} is no longer on stage.`);
        }
    // The cue's own changes.
    for (const [id, change] of Object.entries(cue?.actors || {})) {
      if (!byId.has(id)) {
        unavailable.push(`${nameOf(id)} ${staged ? "was not seen or heard" : "was not present"} at this turn`);
        continue;
      }
      const prior = blocking.get(id);
      const position = change.position || (prior && prior.position !== "off" ? prior.position : "") || freePlace(blocking, id);
      blocking.set(id, { name: byId.get(id).name, position, art: change.art || prior?.art || "" });
    }
    // Speakers nobody blocked take a free place; one blocked off stage stays off (heard, not seen).
    for (const a of present)
      if (a.speaking && !blocking.has(a.actor))
        blocking.set(a.actor, { name: a.name, position: freePlace(blocking, a.actor), art: "" });
      else if (blocking.has(a.actor)) blocking.get(a.actor).name = a.name;
    const onStage = (id) => byId.has(id) && blocking.has(id) && blocking.get(id).position !== "off";
    // Focus is the cue's own; otherwise the turn's last staged speaker.
    let focus = { actor: null, source: "none" };
    const want = cue?.focus ?? "auto";
    if (want && want !== "auto" && want !== "none") {
      if (onStage(want)) focus = { actor: want, source: "authored" };
      else unavailable.push(`focus on ${nameOf(want)}, who is not on stage at this turn`);
    } else if (want === "auto") {
      const lines = [turn.post, ...turn.kept.lines].filter((l) => l?.actor && onStage(l.actor));
      if (lines.length) focus = { actor: lines.at(-1).actor, source: "automatic" };
    }
    if (cue && cue.background !== "inherit") background = cue.background;
    const actors = present
      .filter((a) => onStage(a.actor))
      .map((a) => {
        const b = blocking.get(a.actor);
        return { actor: a.actor, name: b.name, speaking: a.speaking, position: b.position, art: b.art,
          from: shownAt.get(a.actor) || null, entered: !shownAt.has(a.actor) };
      });
    for (const a of actors) {
      if (a.entered) directions.push(`${a.name} ${entries.length ? "enters" : "stands"} ${placeName(a.position)}.`);
      else if (a.from !== a.position) directions.push(`${a.name} moves from ${placeName(a.from)} to ${placeName(a.position)}.`);
      const old = before.get(a.actor)?.art;
      if (!a.entered && old !== undefined && old !== a.art && outfitOf(a.art) && outfitOf(a.art) !== outfitOf(old))
        directions.push(`${a.name} appears in the ${outfitOf(a.art)} outfit.`);
    }
    for (const [id] of shownAt)
      if (byId.has(id) && blocking.get(id)?.position === "off") directions.push(`${nameOf(id)} steps off stage.`);
    for (const a of present)
      if (a.speaking && !onStage(a.actor)) directions.push(`${a.name} speaks from off stage.`);
    if (focus.actor) directions.push(`Focus on ${blocking.get(focus.actor).name}.`);
    if (entries.length && entries.at(-1).background !== background)
      directions.push(background ? "The background changes." : "The background clears.");
    entries.push({ turn: turn.turn, number: turn.number, cue, actors, focus, background,
      transition: cue?.transition || "none", note: cue?.note || "", directions, unavailable });
    shownAt = new Map(actors.map((a) => [a.actor, a.position]));
    // A cue from #694 staged its own turn only: the turns after it go back to what came before it, less any
    // departure this turn established.
    if (cue?.once) {
      const restored = new Map();
      for (const [id, b] of before) if (blocking.has(id)) restored.set(id, { ...b, name: blocking.get(id).name });
      for (const [id, b] of blocking) if (!before.has(id) && !cue.actors[id]) restored.set(id, b); // auto-placed speakers
      blocking = restored;
      background = backgroundBefore;
    }
  }
  return entries;
}

export function placeName(position) {
  return position === "center" ? "centre stage" : "stage " + position;
}
