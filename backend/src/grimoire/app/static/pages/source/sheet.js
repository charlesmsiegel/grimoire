// What the source page shows of a `sources.read` answer, as pure functions (no DOM), so they are tested on their
// own (tests/js/library.unit.mjs): which tabs an entry has, its version number, the line under its name, what it
// opens with, and two revisions compared field by field.

import { FAMILIES, modelOf } from "./drafts.js";

const TABS = [["sheet", "Sheet"], ["art", "Art"], ["edit", "Edit"], ["revisions", "Revisions"], ["raw", "Raw"]];
const KINDS = { character: "Character", scenario: "Scenario", lorebook: "Lorebook", location: "Location", item: "Item" };

// The tabs of an entry: Art only where the app can read a character's picture slots.
export function tabsFor(read, { art = false } = {}) {
  return TABS.filter(([name]) => name !== "art" || (art && read.kind === "character")).map(([value, label]) => ({ value, label }));
}

// The tab an address asks for, or the sheet when it names none of this entry's tabs.
export function askedTab(text, tabs) {
  return tabs.some((tab) => tab.value === text) ? text : "sheet";
}

// A kind as a word to show: "Character", "Lorebook", or the kind itself with a capital.
export function kindLabel(kind) {
  const text = String(kind || "");
  return KINDS[text] || (text ? text[0].toUpperCase() + text.slice(1) : "Entry");
}

// A revision's place among its entity's revisions, counted from 1 (the first written is version 1); null for a
// revision that is not one of them.
export function versionOf(revisions, revision) {
  const at = (revisions || []).indexOf(revision);
  return at < 0 ? null : at + 1;
}

// The version a save writes next.
export function nextVersion(read) {
  return (read.revisions || []).length + 1;
}

export function count(n, one, many) { return n + " " + (n === 1 ? one : many); }

// The line under the name, from what the answer holds: where it lives, which version is shown, and the chats that
// bind it now. Nothing here is guessed: a part with no data is left out.
export function metaParts(read) {
  const parts = [String(read.source || "").startsWith("card:") ? "Imported card" : (read.source_title || read.source)];
  const version = versionOf(read.revisions, read.revision);
  if (version !== null) parts.push(read.is_head ? "version " + version : "version " + version + " of " + read.revisions.length);
  if (read.derived) parts.push("a portrayal");
  if (Array.isArray(read.chats)) {
    const active = read.chats.filter((chat) => chat.active).length;
    if (active) parts.push(count(active, "chat", "chats"));
  }
  return parts.filter(Boolean);
}

// The chats bound to one revision of the entry.
export function chatsOn(read, revision) {
  return (read.chats || []).filter((chat) => chat.active && (chat.revisions || []).includes(revision));
}

// What a character or a scenario opens with: an imported card's greetings, or a scenario's openings.
export function opensWith(read) {
  const payload = read.payload || {};
  let items = [];
  if (read.family === "scenario") items = Array.isArray(payload.openings) ? payload.openings : [];
  else if (payload.card && typeof payload.card === "object" && Array.isArray(payload.card.greetings)) items = payload.card.greetings;
  return items.map((item) => (item && typeof item.text === "string" ? item.text : "")).filter((text) => text.trim());
}

// The lorebooks an imported card binds, by their exact refs.
export function loreBound(read) {
  const card = read.payload && read.payload.card;
  return card && typeof card === "object" && typeof card.lorebook === "string" && card.lorebook ? [card.lorebook] : [];
}

// A lorebook's entries as the sheet lists them: its keys, its text and whether it is off or always in context.
export function loreEntries(read) {
  if (read.family !== "lorebook") return [];
  const model = modelOf(read);
  if (model.locked) return [];
  return model.entries.map((entry) => ({ keys: entry.keys.split("\n").filter(Boolean), content: entry.content,
    enabled: entry.enabled, constant: entry.constant }));
}

const SIMPLE = { entity: [["name", "Name"], ["nature", "Nature"], ["description", "Description"]],
  scenario: [["title", "Title"], ["premise", "Premise"], ["instructions", "Instructions"]],
  lorebook: [["name", "Name"], ["description", "Description"]] };
const LIST = { entity: "sections", scenario: "openings", lorebook: "entries" };

const STORED = "Stored field ";  // a key the editor has no field for, compared as it is written
const written = (value) => (typeof value === "string" ? value : JSON.stringify(value, null, 2));

// A payload as labelled text fields, in the order a person reads them: the fields the editor knows first, each
// section, opening or entry as its own field, then every other stored key as it is written.
export function fieldsOfPayload(family, payload) {
  const out = new Map();
  const source = payload && typeof payload === "object" && !Array.isArray(payload) ? payload : {};
  const known = new Set();
  if (FAMILIES.includes(family)) {
    const model = modelOf({ family, payload: source });
    for (const [key, label] of SIMPLE[family]) {
      known.add(key);
      // a nature the payload does not write is not a field of it, though the editor shows its default
      if (model[key] && (key !== "nature" || Object.hasOwn(source, "nature"))) out.set(label, model[key]);
    }
    if (!model.locked) {
      known.add(LIST[family]);
      model[LIST[family]].forEach((item, index) => {
        if (family === "entity") out.set("Section " + item.name, item.text);
        else if (family === "scenario") out.set("Opening " + item.slot, item.text);
        else {
          const lines = [];
          if (item.keys) lines.push("Keys: " + item.keys.split("\n").join(", "));
          lines.push(item.content);
          if (!item.enabled) lines.push("(off)");
          if (item.constant) lines.push("(always in context)");
          out.set("Entry " + (index + 1), lines.join("\n"));
        }
      });
    }
  }
  for (const key of Object.keys(source).sort()) if (!known.has(key)) out.set(STORED + key, written(source[key]));
  return out;
}

// Two revisions compared field by field: [{ field, before, after, stored }] for each field that differs, where a
// side that does not have the field is null and `stored` marks a key shown as it is written. `before` is the older
// payload and `after` the one it is compared with.
export function diffRevisions(family, before, after) {
  const was = fieldsOfPayload(family, before);
  const now = fieldsOfPayload(family, after);
  const labels = [...new Set([...now.keys(), ...was.keys()])];
  return labels.filter((label) => was.get(label) !== now.get(label))
    .map((label) => ({ field: label, before: was.has(label) ? was.get(label) : null, after: now.has(label) ? now.get(label) : null,
      stored: label.startsWith(STORED) }));
}
