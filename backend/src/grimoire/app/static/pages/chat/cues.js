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
// localStorage is shared by every tab of this device and has no transaction, so each turn's cue is its own
// key (`grimoire.vn.cue:<chat>\u001f<turn>`, holding {version, cue}): a save writes only its turn, and two
// tabs saving different turns, however interleaved, never touch each other's cue. Same-turn saves are last
// writer wins. A stored turn this build cannot read (malformed, or a newer version) is kept as it is and
// reported; saving over it is refused, and Reset cue or Reset local cues removes it. A change that could not
// be written (storage full or gone) stays applied in memory, with its warning, through every reload, and is
// written again with the next change. `reload` takes up another tab's change (the page calls it on the
// `storage` event).
const CUE_KEY = "grimoire.vn.cue:";
const MIGRATED = "grimoire.vn.migrated:"; // #694's cues were carried over (or this chat already had cues)
export function cueStore(chat, storage, legacy) {
  let cues = {}, warning = "", unreadable = new Map(); // turn -> "malformed" | "newer"
  const unsaved = new Map(); // turn -> cue, or null for a removal, not yet written
  const probe = (get) => { try { return get(); } catch { return null; } };
  // Only the page's own stores carry #694's cues forward: a caller that hands in a store hands in both.
  if (!storage) {
    storage = probe(() => globalThis.localStorage);
    if (legacy === undefined) legacy = probe(() => globalThis.sessionStorage);
  }
  const prefix = CUE_KEY + chat + "\u001f";
  const keyOf = (turn) => prefix + turn;
  const UNSAVED = "Local cues not saved: storage unavailable or full; changes remain in memory.";
  function read(raw) {
    let parsed;
    try { parsed = JSON.parse(raw); } catch { return { problem: "malformed" }; }
    if (!parsed || typeof parsed !== "object") return { problem: "malformed" };
    if (typeof parsed.version === "number" && parsed.version > 2) return { problem: "newer" };
    if (parsed.version !== 2 || !validCues({ t: parsed.cue })) return { problem: "malformed" };
    return { cue: parsed.cue };
  }
  // Every stored turn of this chat. Throws only when storage is unusable.
  function storedTurns() {
    if (!storage) throw Error("unavailable");
    const found = {}, problems = new Map();
    for (let i = 0; i < storage.length; i += 1) {
      const key = storage.key(i);
      if (typeof key !== "string" || !key.startsWith(prefix)) continue;
      const turn = key.slice(prefix.length), got = read(storage.getItem(key));
      if (got.problem) problems.set(turn, got.problem);
      else found[turn] = got.cue;
    }
    return { found, problems };
  }
  function describe() {
    if (!unreadable.size) return "";
    const newer = [...unreadable.values()].includes("newer");
    return (newer ? "Some local cues were saved by a newer version of the app; they are kept. " : "Malformed local cues are kept. ") +
      "Reset cue or Reset local cues to replace them.";
  }
  function overlay(found) {
    for (const [turn, cue] of unsaved)
      if (cue === null) delete found[turn];
      else found[turn] = cue;
    return found;
  }
  // #694's cues are queued as unsaved changes and written like any other, so a migration that storage
  // interrupts resumes on the next load; the marker is set only once every write has succeeded. A turn this
  // device already stores, readable or not, is never replaced by its older cue.
  function migrate(found, problems) {
    if (storage.getItem(MIGRATED + chat)) return;
    const old = legacy ? probe(() => legacy.getItem(STORE_V1 + chat)) : null;
    const parsed = old ? probe(() => JSON.parse(old)) : null;
    if (parsed?.version === 1 && validCues(parsed.cues, 1))
      for (const [turn, cue] of Object.entries(parsed.cues))
        if (!(turn in found) && !problems.has(turn) && !unsaved.has(turn)) unsaved.set(turn, (found[turn] = fromV1(cue)));
    flush();
  }
  function load() {
    try {
      const { found, problems } = storedTurns();
      unreadable = problems;
      migrate(found, problems);
      cues = overlay(found);
      warning = unsaved.size ? UNSAVED : describe();
    } catch {
      cues = overlay({ ...cues });
      warning = unsaved.size ? UNSAVED : "Local cues not saved: storage unavailable.";
    }
  }
  // Writes this turn and every still-unsaved one. True: all written; false: kept in memory.
  function flush() {
    try {
      for (const [turn, cue] of [...unsaved]) {
        if (cue === null) storage.removeItem(keyOf(turn));
        else storage.setItem(keyOf(turn), JSON.stringify({ version: 2, cue }));
        unreadable.delete(turn);
        unsaved.delete(turn);
      }
      storage.setItem(MIGRATED + chat, "1");
      return true;
    } catch {
      return false;
    }
  }
  // One turn's change (a cue, or null to remove it). True: saved. "unsaved": applied in memory only, and
  // written with a later change. False: refused, nothing changed.
  function change(turn, cue) {
    load();
    if (cue !== null && unreadable.has(turn)) {
      warning = describe();
      return false;
    }
    const next = { ...cues };
    if (cue === null) delete next[turn];
    else next[turn] = cue;
    if (!validCues(next) || Object.keys(next).length + unreadable.size - (unreadable.has(turn) ? 1 : 0) > MAX_CUES) {
      warning = `Invalid cues: maximum ${MAX_CUES} cues and ${MAX_NOTE} note characters.`;
      return false;
    }
    unsaved.delete(turn);
    unsaved.set(turn, cue);
    cues = next;
    if (flush()) {
      warning = describe();
      return true;
    }
    warning = UNSAVED;
    return "unsaved";
  }
  load();
  return {
    get cues() { return cues; },
    get warning() { return warning; },
    get unreadable() { return unreadable.size > 0; },
    reload: load,
    put: (turn, cue) => change(turn, cue),
    remove: (turn) => change(turn, null),
    // Reset local cues: removes every cue of this chat, readable or not.
    reset() {
      load();
      const turns = new Set([...Object.keys(cues), ...unreadable.keys()]);
      for (const turn of turns) {
        unsaved.delete(turn);
        unsaved.set(turn, null);
      }
      cues = {};
      if (flush()) {
        unreadable = new Map();
        warning = "";
        return true;
      }
      warning = UNSAVED;
      return "unsaved";
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
// Only actors the table can place at this turn (`here`) hold a place: on a stage, an unheard actor's kept
// place is free for a new speaker, and the actor shares it if they return.
function freePlace(blocking, except, here) {
  const taken = new Set([...blocking].filter(([id, b]) => id !== except && here.has(id) && b.position !== "off").map(([, b]) => b.position));
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
//   carried: each actor's place and art coming into this turn, before its cue ("off" included).
// `blocking` holds every actor's carried place, including "off"; `shownAt` who was drawn at the previous kept
// turn, so an entrance or a move is told against the turn before it in the transcript (the reader may have
// jumped from elsewhere; the directions still describe the scene's own sequence).
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
      // An unchanged place stays as it was, off stage included; with none yet the actor takes a free place.
      const position = change.position || prior?.position || freePlace(blocking, id, byId);
      blocking.set(id, { name: byId.get(id).name, position, art: change.art || prior?.art || "" });
    }
    // Speakers nobody blocked take a free place; one blocked off stage stays off (heard, not seen).
    for (const a of present)
      if (a.speaking && !blocking.has(a.actor))
        blocking.set(a.actor, { name: a.name, position: freePlace(blocking, a.actor, byId), art: "" });
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
      if (a.entered && staged && before.has(a.actor)) directions.push(`${a.name} is seen again, ${placeName(a.position)}.`);
      else if (a.entered) directions.push(`${a.name} ${entries.length ? "enters" : "stands"} ${placeName(a.position)}.`);
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
    const carried = Object.fromEntries([...before].map(([id, b]) => [id, { position: b.position, art: b.art }]));
    entries.push({ turn: turn.turn, number: turn.number, cue, actors, focus, background, carried,
      transition: cue?.transition || "none", note: cue?.note || "", directions, unavailable });
    shownAt = new Map(actors.map((a) => [a.actor, a.position]));
    // A cue from #694 staged its own turn only: the turns after it go back to what came before it, less any
    // departure this turn established.
    if (cue?.once) {
      const restored = new Map();
      for (const [id, b] of before) if (blocking.has(id)) restored.set(id, { ...b, name: blocking.get(id).name });
      // speakers first placed at this turn keep a place, chosen again against the restored stage
      for (const [id, b] of blocking)
        if (!before.has(id) && !cue.actors[id]) restored.set(id, { ...b, position: freePlace(restored, id, byId) });
      blocking = restored;
      background = backgroundBefore;
    }
  }
  return entries;
}

export function placeName(position) {
  return position === "center" ? "centre stage" : "stage " + position;
}
