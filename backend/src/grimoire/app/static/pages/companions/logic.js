// What the companions page (050, Sprint 115) works out without the app: the capture form as a request, and the
// words a capture's review is said in. Pure, so it runs under `node --test`.

export const OUTCOMES = [{ value: "success", label: "Caught" }, { value: "failed", label: "Got away" },
  { value: "declined", label: "Not attempted after all" }];
export const PLACEMENTS = [{ value: "auto", label: "Active if there is room, else reserve" },
  { value: "party", label: "Active party" }, { value: "reserve", label: "Reserve" }];

// A new attempt id: one capture decision, kept with its review so a retried commit names the same attempt.
export function newAttemptId() {
  return "capture-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10);
}

const who = (item) => (item && (item.name || item.actor)) || "someone";

// The request the form names, or { error } naming the first thing to fix.
export function requestOf(form, { chat }) {
  if (!form.trainer) return { error: "Choose the trainer." };
  if (!form.creature) return { error: "Choose the creature." };
  if (!form.turn) return { error: "Choose the turn it happened in." };
  const capacity = String(form.capacity || "").trim();
  if (!/^\d{1,4}$/.test(capacity) || Number(capacity) > 1000) return { error: "Say how many the trainer's active party holds: a whole number from 0 to 1000." };
  return { request: { chat, audience: "director", trainer: form.trainer, creature: form.creature, outcome: form.outcome || "success",
    device: form.device || null, capacity: Number(capacity), placement: form.placement || "auto", turn: form.turn } };
}

// The sentence a capture's review is said in, from what companions.capture_plan answered.
export function planText(plan) {
  if (plan.already_applied) return "This capture was already made: making it again changes nothing and spends nothing.";
  const creature = who(plan.creature) + (plan.creature && plan.creature.species ? " (" + plan.creature.species + ")" : "");
  const spends = plan.spends ? " It spends one " + plan.device + " from " + who(plan.trainer) + "'s holdings." : "";
  if (plan.outcome === "success") {
    return who(plan.trainer) + " captures " + creature + ": it joins " + who(plan.trainer) + "'s "
      + (plan.placement === "party" ? "active party" : "reserve") + "." + spends;
  }
  if (plan.writes) return creature + " gets away: no companion and no party change." + spends;
  return "Nothing is written: " + (plan.outcome === "declined" ? "a capture not attempted" : "a failed capture with no device")
    + " is no event, and a later attempt needs a new decision.";
}

// What a companion is, in a line: its species and where it travels.
export function companionText(item) {
  return who(item) + (item.species ? " · " + item.species : "") + " · "
    + (item.placement === "active" ? "active party" : item.placement === "reserve" ? "reserve" : "in neither");
}
