// What the attitudes page (078, Sprint 116) works out without the app: the form as a check request and the words
// a verdict is said in. Pure, so it runs under `node --test`.

export const DIMENSIONS = {
  trust: "Trust", affection: "Affection", fear: "Fear", rivalry: "Rivalry", resentment: "Resentment",
  debt: "Debt (feels it owes them)",
};

// A new attitude id: names one checked attitude while the page is open.
export function newAttitudeId() {
  return "att-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 8);
}

// The degrees a dimension takes: trust and affection run from -3 to 3, the others are intensities from 0 to 3.
export function degreeOptions(dimension, bipolar, max = 3) {
  const low = bipolar.includes(dimension) ? -max : 0;
  const options = [{ value: "", label: "No degree: the account says it" }];
  for (let value = low; value <= max; value += 1) options.push({ value: String(value), label: (value > 0 && low < 0 ? "+" : "") + value });
  return options;
}

// Who an attitude may point at: the rest of the cast, then the campaign's groups.
export function towardOptions(cast, groups, holder) {
  return [{ value: "", label: "Choose whom…" },
    ...cast.filter((item) => item.actor !== holder).map((item) => ({ value: item.actor, label: item.name || item.actor })),
    ...groups.map((item) => ({ value: item.actor, label: (item.name || item.actor) + " (a group)" }))];
}

// The check request, or the first thing still missing. `held` are the attitudes this page holds as admitted.
export function requestOf(form, { chat, held, maxText = 400 }) {
  if (!form.holder) return { error: "Choose whose attitude it is." };
  if (!form.toward) return { error: "Choose whom the attitude is toward." };
  if (!form.dimension) return { error: "Choose what the attitude is." };
  const account = String(form.account || "").trim();
  if (!account) return { error: "Write the NPC's own reading of it." };
  if (account.length > maxText) return { error: "The account is at most " + maxText + " characters." };
  if (!form.evidence.length && !form.beliefs.length) return { error: "Pick what it rests on: the NPC's own evidence or beliefs." };
  const attitude = { id: form.id, holder: form.holder, toward: form.toward, dimension: form.dimension, account,
    evidence: [...form.evidence], beliefs: [...form.beliefs] };
  if (form.degree !== "" && form.degree !== undefined && form.degree !== null) attitude.degree = Number(form.degree);
  return { request: { chat, audience: "director", held: held.map((item) => ({ ...item })), attitude } };
}

const REASONS = {
  unavailable_grounds: "it rests on something this NPC does not hold, or held only later",
  unrelated_grounds: "nothing it rests on concerns whom it is toward: a perception alone never says who did it",
  future_moment: "it is dated after the campaign's present",
  pc_interiority: "a player character's feelings are its player's",
  npc_authority_required: "only the director authors an NPC's attitude",
  unknown_control: "nobody controls this character now",
  ambiguous_control: "this character's control is ambiguous",
  unsupported_holder_kind: "only a character holds an attitude here",
  not_agent: "the holder is not an agent",
  not_agent_or_group: "it may point only at an agent or a group",
  unresolved_actor: "that actor is not in this chat's campaign as the page reads it",
  unsuperseded_conflict: "this NPC already holds that attitude toward them here: forget it first",
  nonmonotonic_revision: "a change of an attitude must be dated later than the one it replaces",
  self_attitude: "an attitude points at someone else",
  invalid_text: "the account is empty or too long",
  invalid_degree: "that degree is outside the dimension's range",
  ungrounded_attitude: "an attitude rests on at least one ground",
  budget_exceeded: "an attitude rests on at most 8 grounds",
  duplicate_ground: "a ground is picked twice",
};

// The sentence a verdict is said in, with 078's reason kept.
export function verdictText(verdict) {
  if (verdict.admitted) return "078 admits it: it rests on this NPC's own grounds, and one concerns whom it is toward.";
  const reason = verdict.reason || "refused";
  return "078 refuses it: " + (REASONS[reason] || reason) + " (" + reason + ").";
}

export function headText(item) {
  return (DIMENSIONS[item.dimension] || item.dimension) + " toward " + (item.toward_name || item.toward)
    + (item.degree === null || item.degree === undefined ? "" : " (" + (item.degree > 0 && ["trust", "affection"].includes(item.dimension) ? "+" : "") + item.degree + ")")
    + ": " + item.account;
}
