// The source page's drafts (109), as pure functions over `sources.read` answers: no DOM, so they are tested on
// their own (tests/js/library.unit.mjs).
//
// A structured draft is { base, etag, start, model }: the head it started from and that record's etag, the
// editor's model of that head, and the model as edited. A raw draft is { base, etag, start, text }: the head and
// its etag, its payload as JSON, and the text typed.
// What a draft changes is always what differs between its `start` and its edit, never what differs from some
// later head: so a draft held over a newer head lays only its own changes over that head (`rebase`), and a part
// that changed under it on both sides is a conflict, shown and never guessed at.

export const FAMILIES = ["entity", "scenario", "lorebook"];
const SIMPLE = { entity: ["name", "nature", "description"], scenario: ["title", "premise", "instructions"],
  lorebook: ["name", "description"] };
const LIST = { entity: "sections", scenario: "openings", lorebook: "entries" };

const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const text = (value) => (typeof value === "string" ? value : "");

// The editor's model of a revision's payload. List items carry `slot`: the section's name, the opening's id or the
// entry's index in this revision (null for one the draft adds). A list whose items are not all text is `locked`:
// the structured editor leaves it alone, and it is edited in the raw editor.
export function modelOf(read) {
  const payload = read.payload || {};
  if (read.family === "entity") {
    const sections = payload.sections && typeof payload.sections === "object" && !Array.isArray(payload.sections) ? payload.sections : {};
    return { name: text(payload.name), nature: payload.nature === "template" ? "template" : "individual",
      description: text(payload.description),
      sections: Object.entries(sections).map(([name, value]) => ({ slot: name, name, text: text(value) })),
      locked: Object.values(sections).some((value) => typeof value !== "string") };
  }
  if (read.family === "scenario") {
    const openings = Array.isArray(payload.openings) ? payload.openings : [];
    return { title: text(payload.title), premise: text(payload.premise), instructions: text(payload.instructions),
      openings: openings.map((item) => ({ slot: item && typeof item.id === "string" ? item.id : null, text: text(item && item.text) })),
      locked: openings.some((item) => !item || typeof item.id !== "string" || typeof item.text !== "string") };
  }
  if (read.family === "lorebook") {
    const entries = Array.isArray(payload.entries) ? payload.entries : [];
    return { name: text(payload.name), description: text(payload.description),
      entries: entries.map((entry, index) => ({ slot: index, original: JSON.stringify(entry),
        keys: (entry && Array.isArray(entry.keys) ? entry.keys.filter((key) => typeof key === "string") : []).join("\n"),
        content: text(entry && entry.content), constant: !!(entry && entry.settings && entry.settings.constant === true),
        enabled: !(entry && entry.enabled === false) })),
      locked: entries.some((entry) => !entry || typeof entry !== "object" || Array.isArray(entry)) };
  }
  return null;
}

// A draft's own id for an item it adds, so a save that lands while the draft is edited again can be told apart
// from an addition still to make.
export function addedId() {
  return "added-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10);
}

// What of a list item the person edits (not its slot or the head's original).
function props(family, item) {
  if (family === "entity") return [item.name, item.text];
  if (family === "scenario") return [item.text];
  return [item.keys, item.content, item.constant, item.enabled];
}

function itemLabel(family, item, number) {
  if (family === "entity") return "section " + (item.name || item.slot || number);
  if (family === "scenario") return "opening " + (item.slot || number);
  return "entry " + number;
}

// What a draft changes, by part: ["name", "section personality", "entry 2"...].
export function changesOf(family, start, model) {
  const found = SIMPLE[family].filter((key) => !same(start[key], model[key]));
  const list = LIST[family];
  const startBy = new Map(start[list].filter((item) => item.slot !== null).map((item) => [item.slot, item]));
  const kept = new Set();
  model[list].forEach((item, index) => {
    if (item.slot === null) { found.push(itemLabel(family, item, index + 1) + " (new)"); return; }
    kept.add(item.slot);
    const before = startBy.get(item.slot);
    if (!before || !same(props(family, before), props(family, item))) found.push(itemLabel(family, item, index + 1));
  });
  start[list].forEach((item, index) => {
    if (item.slot !== null && !kept.has(item.slot)) found.push(itemLabel(family, item, index + 1) + " (removed)");
  });
  return found;
}

// The parts of the payload that changed between two models: ["description", "entries"...].
export function changedParts(family, before, after) {
  return [...SIMPLE[family], LIST[family]].filter((key) => key === LIST[family]
    ? !same(before[key].map((item) => [item.slot, ...props(family, item)]), after[key].map((item) => [item.slot, ...props(family, item)]))
    : !same(before[key], after[key]));
}

// Everything a draft changes, by part, with the draft's own value (null for a part it removes): the copy a person
// is given of a draft that cannot be kept.
export function draftParts(family, start, model) {
  const parts = {};
  for (const key of SIMPLE[family]) if (!same(start[key], model[key])) parts[key] = model[key];
  const list = LIST[family];
  const startBy = new Map(start[list].filter((item) => item.slot !== null).map((item) => [item.slot, item]));
  const kept = new Set();
  model[list].forEach((item, index) => {
    if (item.slot === null) { parts[itemLabel(family, item, index + 1) + " (new)"] = shown(item); return; }
    kept.add(item.slot);
    const before = startBy.get(item.slot);
    if (!before || !same(props(family, before), props(family, item))) parts[itemLabel(family, item, index + 1)] = shown(item);
  });
  start[list].forEach((item, index) => {
    if (item.slot !== null && !kept.has(item.slot)) parts[itemLabel(family, item, index + 1) + " (removed)"] = null;
  });
  return parts;
}

// An item as the person sees it, for a copy of a conflict: no slot, no original.
function shown(item) {
  const { slot: _slot, original: _original, added: _added, ...rest } = item;
  return rest;
}

// What of an item counts when two writers may have made the same change (an entry's keys as they would be saved).
function saved(family, item) {
  return family === "lorebook" ? [splitKeys(item.keys).join("\n"), item.content, item.constant, item.enabled] : props(family, item);
}

// A draft's changes laid over `read`, the head now: { model, conflicts, copies, mine, changed }. `conflicts` names
// each part the draft changed that also changed under it, differently; the model then holds the head's value
// there, and `copies` holds the draft's own value for each, to copy (null for a part the draft removes). A change
// the head already has (the draft was saved, then the page left before the answer) is neither laid again nor a
// conflict.
export function rebase(read, draft) {
  const family = read.family;
  const head = modelOf(read);
  const { start, model: mine } = draft;
  const list = LIST[family];
  const out = { ...head, [list]: head[list].map((item) => ({ ...item })) };
  const conflicts = [];
  const copies = {};
  const conflict = (label, value) => { conflicts.push(label); copies[label] = value; };
  for (const key of SIMPLE[family]) {
    if (same(mine[key], start[key])) continue;
    if (!same(head[key], start[key]) && !same(head[key], mine[key])) conflict(key, mine[key]);
    else out[key] = mine[key];
  }
  const mineChanges = changesOf(family, start, mine);
  const mineBy = new Map(mine[list].filter((item) => item.slot !== null).map((item) => [item.slot, item]));
  if (head.locked) {
    // The head's list is no longer all text (the raw editor wrote it), so a structured save cannot send it: every
    // change the draft made to the list is a conflict, kept to copy, never laid over the head or dropped.
    for (const [part, value] of Object.entries(draftParts(family, start, mine))) {
      if (!SIMPLE[family].includes(part)) conflict(part, value);
    }
    return { model: out, conflicts, copies, mine: mineChanges, changed: changedParts(family, start, head) };
  }
  const items = out[list];
  // The head's items that are not the draft's start items: added (or, an entry, changed) under the draft.
  const startSlots = new Set(start[list].map((item) => item.slot));
  const startOriginals = start[list].map((item) => item.original);
  const fresh = new Set();
  if (family === "lorebook") {
    const left = [...startOriginals];
    head[list].forEach((item, where) => {
      const at = left.indexOf(item.original);
      if (at >= 0) left.splice(at, 1); else fresh.add(where);
    });
  } else {
    head[list].forEach((item, where) => { if (!startSlots.has(item.slot)) fresh.add(where); });
  }
  // A fresh head item the same as `item` would be saved: the draft's change is already there.
  const already = (item) => {
    const where = [...fresh].find((at) => same(saved(family, head[list][at]), saved(family, item)));
    if (where === undefined) return false;
    fresh.delete(where);
    return true;
  };
  // Each item the draft started from that it edits or removes is found in the head now: a section by its name and
  // an opening by its id; an entry, which has no id and whose index moves, by its exact content. Where the head
  // changed that item too (or it is gone), the draft's change is a conflict, unless both made the same change.
  const used = new Set();
  const removals = [];
  const freshAtStart = fresh.size;
  start[list].forEach((item, index) => {
    const edited = mineBy.get(item.slot);
    if (edited && same(props(family, edited), props(family, item))) return;  // left alone: the head's stands
    const label = itemLabel(family, item, index + 1);
    let at;
    if (family === "lorebook") {
      at = head[list].findIndex((other, where) => other.original === item.original && !used.has(where));
      if (at >= 0) used.add(at);
    } else {
      at = items.findIndex((other) => other.slot === item.slot);
      if (at >= 0 && !same(props(family, items[at]), props(family, item))) {  // changed in the head as well
        if (!(edited && same(props(family, items[at]), props(family, edited)))) conflict(label, shown(edited || item));
        return;
      }
    }
    if (at < 0) {  // gone from the head, or (an entry) changed there
      if (family === "lorebook" && edited) { if (!already(edited)) conflict(label, shown(edited)); return; }
      // an entry the draft removes that is gone, with nothing changed in its place: removed on both sides
      if (family === "lorebook" && freshAtStart) conflict(label + " (removed)", null);
      else if (edited) conflict(label, shown(edited));
      return;
    }
    if (edited) items[at] = { ...edited, slot: items[at].slot, original: items[at].original };
    else removals.push(at);
  });
  for (const at of removals.sort((x, y) => y - x)) items.splice(at, 1);
  const sent = (draft.sent && typeof draft.sent === "object") ? draft.sent : {};
  mine[list].forEach((item, index) => {
    if (item.slot !== null) return;
    const label = itemLabel(family, item, index + 1) + " (new)";
    const landed = item.added && sent[item.added];
    if (landed) {
      // This addition was saved, and edited again before the answer came: it is the head's item now, found as
      // it was sent, and the later edit is laid over it as an edit. Not found as sent, it is a conflict: never a
      // second copy.
      const at = family === "entity"
        ? items.findIndex((other) => other.name === landed.name && same(other.text, landed.text))
        : (() => {
          const where = [...fresh].find((candidate) => same(saved(family, head[list][candidate]), saved(family, landed)));
          if (where === undefined) return -1;
          fresh.delete(where);
          return items.findIndex((other) => other.slot === head[list][where].slot && other.original === head[list][where].original);
        })();
      if (at < 0) conflict(label, shown(item));
      else if (!same(saved(family, items[at]), saved(family, item))) items[at] = { ...item, slot: items[at].slot, original: items[at].original };
      return;
    }
    if (family === "entity" && items.some((other) => other.name === item.name)) {
      const other = items.find((candidate) => candidate.name === item.name);
      if (!same(other.text, item.text)) conflict(label, shown(item));
      return;
    }
    if (family !== "entity" && already(item)) return;
    items.push({ ...item });
  });
  return { model: out, conflicts, copies, mine: mineChanges, changed: changedParts(family, start, head) };
}

// The `fields` a structured save sends for `model` over `read`, the head the model is of: only the parts that
// differ from it. An entry the edit leaves alone is sent as its index alone, so the head keeps it exactly.
export function fieldsOf(read, model) {
  const family = read.family;
  const head = modelOf(read);
  const fields = {};
  for (const key of SIMPLE[family]) if (!same(model[key], head[key])) fields[key] = model[key];
  const list = LIST[family];
  if (head.locked || !changedParts(family, head, model).includes(list)) return fields;
  if (family === "entity") {
    fields.sections = Object.create(null);  // no inherited names: a section may be called "constructor" or "__proto__"
    for (const item of model.sections) {
      const name = item.name.trim();
      if (!name) {
        if (item.text.trim()) throw new Error("A new section needs a name.");
        continue;
      }
      if (Object.hasOwn(fields.sections, name)) throw new Error("Two sections are named " + JSON.stringify(name) + ".");
      fields.sections[name] = item.text;
    }
  } else if (family === "scenario") {
    fields.openings = model.openings.filter((item) => item.text.trim())
      .map((item) => (item.slot !== null ? { id: item.slot, text: item.text } : { text: item.text }));
  } else {
    fields.entries = [];
    for (const item of model.entries) {
      if (item.slot === null) {
        if (!item.content.trim()) continue;  // a new entry left empty is not written
        fields.entries.push({ keys: splitKeys(item.keys), content: item.content, constant: item.constant, enabled: item.enabled });
        continue;
      }
      const before = head.entries[item.slot];
      const entry = { index: item.slot };
      if (item.content !== before.content) {
        if (!item.content.trim()) continue;  // the person emptied it: it is removed
        entry.content = item.content;
      }
      if (item.keys !== before.keys) entry.keys = splitKeys(item.keys);
      if (item.constant !== before.constant) entry.constant = item.constant;
      if (item.enabled !== before.enabled) entry.enabled = item.enabled;
      fields.entries.push(entry);
    }
  }
  return fields;
}

function splitKeys(value) {
  return value.split("\n").map((key) => key.trim()).filter(Boolean);
}

// Whether a draft started from something other than the head `read` is of: another revision, or the same revision
// as it was before its record was changed in place (a hand edit of the file keeps the revision, not the etag).
export function stale(draft, read) {
  return draft.base !== read.head || (typeof draft.etag === "string" && draft.etag !== read.etag);
}

// -- the raw editor -------------------------------------------------------------------------------------

// A raw draft's changes laid over `read`, key by key of the payload: { text, conflicts, mine, changed }, or
// { error } when the draft is not a JSON object.
export function rebaseRaw(read, draft) {
  let mine;
  try { mine = JSON.parse(draft.text); } catch (error) { return { error: "Your draft is not JSON: " + error.message }; }
  if (!mine || typeof mine !== "object" || Array.isArray(mine)) return { error: "Your draft is not a JSON object." };
  const start = JSON.parse(draft.start);
  const head = read.payload || {};
  const out = { ...head };
  const conflicts = [];
  const keys = [...new Set([...Object.keys(start), ...Object.keys(mine)])].sort();
  const minePart = keys.filter((key) => !same(mine[key], start[key]));
  for (const key of minePart) {
    if (!same(head[key], start[key]) && !same(head[key], mine[key])) { conflicts.push(key); continue; }
    if (key in mine) out[key] = mine[key]; else delete out[key];
  }
  const changed = [...new Set([...Object.keys(start), ...Object.keys(head)])].filter((key) => !same(start[key], head[key])).sort();
  return { text: JSON.stringify(out, null, 2), payload: out, conflicts, mine: minePart, changed };
}

// Whether a payload holds a number the browser cannot keep exactly: a whole number beyond 2^53, already rounded
// once read into JavaScript, or one beyond its range (1e400 reads as Infinity, and is sent as null). Either way the
// raw editor would save a different number than the one written.
export function unsafeNumbers(value) {
  if (typeof value === "number") return !Number.isFinite(value) || (Number.isInteger(value) && !Number.isSafeInteger(value));
  if (Array.isArray(value)) return value.some(unsafeNumbers);
  if (value && typeof value === "object") return Object.values(value).some(unsafeNumbers);
  return false;
}
