// What the Authoring page works out without the app (Sprint 116): the hand builder's state and the proposal it
// sends, which candidates can be accepted, a suggestion's request id, an audit's budget and the words a removal
// preview is said in. Every check that decides anything is the server's (087, 082, 079): these only shape input.

const plural = (n, word) => n + " " + word + (n === 1 ? "" : "s");
export { plural };

export const KIND_NAMES = { location: "Place", organization: "Group" };
export const ROLE_NAMES = { individual: "A record to keep", template: "A kind (template)", brainstorm: "A loose idea (never kept)" };
export const RELATION_NAMES = { road: "Road between", within: "Lies within" };

// A new request id for one suggestion: kept with its estimate, so a request retried after a lost answer finds the
// proposal it already gave and sends nothing.
export function newRequestId() {
  return "author-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10);
}

// The hand builder's state: { candidates: [{key, kind, role, name, description}], links: [{id, relation, subject,
// object}], rationale }. Restored from the page's draft (JSON text): anything unreadable starts empty.
export function emptyBuilder() {
  return { candidates: [], links: [], rationale: "" };
}

const KEY = /^[a-z0-9][a-z0-9-]{0,63}$/;

export function restoreBuilder(text) {
  let raw;
  try { raw = JSON.parse(text || "null"); } catch (error) { return emptyBuilder(); }
  if (!raw || typeof raw !== "object") return emptyBuilder();
  const str = (value) => (typeof value === "string" ? value : "");
  const candidates = (Array.isArray(raw.candidates) ? raw.candidates : []).filter((item) => item && KEY.test(str(item.key)))
    .map((item) => ({ key: item.key, kind: KIND_NAMES[item.kind] ? item.kind : "location",
      role: ROLE_NAMES[item.role] ? item.role : "individual", name: str(item.name), description: str(item.description) }));
  const links = (Array.isArray(raw.links) ? raw.links : []).filter((item) => item && KEY.test(str(item.id)))
    .map((item) => ({ id: item.id, relation: RELATION_NAMES[item.relation] ? item.relation : "road",
      subject: str(item.subject), object: str(item.object) }));
  return { candidates, links, rationale: str(raw.rationale) };
}

// The next stable key of a list: prefix plus one more than the highest number used ("c1", "c2", ...), so a key
// once given is never given again to another row while the builder lives.
export function nextKey(items, prefix, field) {
  let high = 0;
  for (const item of items) {
    const found = new RegExp("^" + prefix + "(\\d+)$").exec(item[field]);
    if (found) high = Math.max(high, Number(found[1]));
  }
  return prefix + (high + 1);
}

export function addCandidate(state) {
  return { ...state, candidates: [...state.candidates, { key: nextKey(state.candidates, "c", "key"), kind: "location", role: "individual", name: "", description: "" }] };
}

export function addLink(state) {
  const first = state.candidates[0] ? state.candidates[0].key : "";
  return { ...state, links: [...state.links, { id: nextKey(state.links, "l", "id"), relation: "road", subject: first, object: "" }] };
}

// A candidate removed takes the links that name it with it: a link to nothing is never sent.
export function removeCandidate(state, key) {
  return { ...state, candidates: state.candidates.filter((item) => item.key !== key),
    links: state.links.filter((item) => item.subject !== key && item.object !== key) };
}

export function removeLink(state, id) {
  return { ...state, links: state.links.filter((item) => item.id !== id) };
}

// What a link's far end may be: another candidate (named as typed) or an existing record the world offers, by its
// exact pinned ref (087 refuses any other). A road joins places; a place lies within a place.
export function endpointOptions(state, records, subject) {
  const named = (item) => item.name.trim() || "Unnamed " + (KIND_NAMES[item.kind] || item.kind).toLowerCase() + " (" + item.key + ")";
  return [
    ...state.candidates.filter((item) => item.key !== subject).map((item) => ({ value: item.key, label: named(item) + " (proposed)" })),
    ...records.filter((record) => record.place).map((record) => ({ value: record.ref, label: (record.name || record.ref) + " (in the world)" })),
  ];
}

// The proposal the builder sends to authoring.build, or { errors } naming what to fix first. The server checks the
// rest exactly as it checks a model's reply.
export function builderBody(state) {
  const errors = [];
  if (!state.candidates.length) errors.push("Add at least one place or group.");
  for (const item of state.candidates) {
    if (!item.name.trim()) errors.push("Name " + item.key + ".");
  }
  const keys = new Set(state.candidates.map((item) => item.key));
  for (const link of state.links) {
    if (!keys.has(link.subject)) errors.push("Choose what link " + link.id + " starts from.");
    if (!link.object) errors.push("Choose what link " + link.id + " leads to.");
  }
  if (errors.length) return { errors };
  return {
    body: {
      candidates: state.candidates.map((item) => ({ key: item.key, kind: item.kind, name: item.name.trim(), role: item.role, description: item.description.trim() })),
      links: state.links.map((link) => ({ id: link.id, relation: link.relation, subject: link.subject, object: link.object })),
      rationale: state.rationale.trim(),
    },
  };
}

// Whether a proposal's candidate can be accepted at all: a brainstorm never is (087 never stores one).
export function acceptable(candidate) {
  return candidate.role !== "brainstorm" && candidate.storable !== false;
}

// A suggestion's constraints, one to a line, blank lines left out.
export function constraintsOf(text) {
  return String(text || "").split("\n").map((line) => line.trim()).filter(Boolean);
}

// An audit's budget from the page's fields, checked against the bounds authoring.source answered:
// { values } or { errors } (by field name). The server checks again.
export function budgetValues(bounds, read) {
  const values = {}, errors = {};
  for (const [name, bound] of Object.entries(bounds)) {
    const raw = String(read(name) === undefined || read(name) === null ? "" : read(name)).trim();
    if (!raw) { values[name] = bound.default; continue; }
    if (!/^\d{1,6}$/.test(raw) || Number(raw) < bound.low || Number(raw) > bound.high) {
      errors[name] = "A whole number from " + bound.low + " to " + bound.high + ".";
      continue;
    }
    values[name] = Number(raw);
  }
  return Object.keys(errors).length ? { errors } : { values };
}

export const BUDGET_NAMES = { max_steps: "Steps at most", max_reads: "Records read at most", max_findings: "Findings at most", max_seconds: "Seconds at most" };

export const STATUS_WORDS = {
  finished: "Finished", budget_exhausted: "Stopped at its budget", failed: "Failed", cancelled: "Cancelled",
};

// Where a citation points, in a line: the field and the words quoted from it.
export function citationLine(citation) {
  return citation.field + (citation.quote ? ": “" + citation.quote + "”" : "") + " (" + citation.revision + ")";
}

// The sentence a removal preview is said in.
export function removalLine(removal) {
  if (!removal.references) return "Nothing elsewhere in this library refers to this source.";
  const parts = [plural(removal.references, "reference") + " elsewhere in this library"];
  if (removal.missing) parts.push(removal.missing + " would be left missing");
  if (removal.attention) parts.push(removal.attention + " need attention");
  return parts.join(", ") + ".";
}

// 079's statuses: of one reference elsewhere in the library, and of one of the source's own entities.
export const REFERENCE_WORDS = {
  unchanged: "Unchanged", follows_head: "Follows its head, which moves", edited: "Edited", changed: "Reaches something else",
  missing: "Left missing", still_missing: "Already missing", restored: "Restored",
};
export const ENTITY_WORDS = { added: "Added", removed: "Would go", moved: "Moves", unchanged: "Unchanged" };

export const DEPENDENCY_WORDS = {
  pinned: "Pinned at its current version", drifted: "Pinned at an older version", following: "Follows its head",
  missing: "Not in this library",
};

// A short version for display: "sha256:abcd…" stays readable.
export function shortVersion(version) {
  if (typeof version !== "string") return "none";
  const at = version.indexOf(":");
  return at > 0 && version.length > at + 13 ? version.slice(0, at + 13) + "…" : version;
}
