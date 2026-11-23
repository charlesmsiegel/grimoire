// The Voices page's pure logic (116, sprint 083's casting): who speaks in a chat and what each can be cast as,
// the arguments of a review and of the write made as reviewed, and what a review says. No DOM, no requests.

export const RIGHTS = [
  { value: "engine", label: "A voice that ships with the engine" },
  { value: "licensed", label: "A voice used under a licence" },
  { value: "consented", label: "A voice made from a person's recordings, with their consent" },
];

// The campaign a chat reference names (campaign:<id>#chat/<chat>), or null.
export function campaignOf(chat) {
  const match = /^campaign:([^#/]+)#/.exec(chat || "");
  return match ? match[1] : null;
}

const pinned = (ref) => /@r\d+$/.test(ref);
const revisionOf = (ref) => { const at = ref.lastIndexOf("@"); return at < 0 ? "" : ref.slice(at + 1); };

// What one rule a cue considered could be cast as, in words the person can check; null for a rule that names no
// target this page casts.
export function targetOf(rule, names, speaker, campaign) {
  if (rule === "narrator") return campaign ? { target: "narrator", ref: null, campaign, label: "The narrator of this campaign (" + campaign + ")" } : null;
  if (rule === "narrator_every_campaign") return { target: "narrator", ref: null, campaign: null, label: "The narrator of every campaign" };
  if (rule === "actor") return { target: "actor", ref: names, campaign: null, label: speaker + " in this campaign only" };
  if (rule === "revision") return { target: "character", ref: names, campaign: null, label: speaker + " at revision " + revisionOf(names) + " only" };
  if (rule === "character_any_revision") return { target: "character", ref: names, campaign: null, label: speaker + " at any revision" };
  if (rule === "voice_profile") {
    return { target: "character", ref: names, campaign: null,
      label: "The voice profile " + names + (pinned(names) ? " (that revision only)" : "") };
  }
  if (rule === "voice_profile_any_revision") return { target: "character", ref: names, campaign: null, label: names + " at any revision (the profile's character)" };
  return null;
}

export function targetKey(item) {
  return item.target + "|" + (item.ref || "") + "|" + (item.campaign || "");
}

// Every speaker of a plan, in the order they first speak: their name, what voices them now and each target they
// can be cast as (from the rules their cues looked at, most specific first, each once).
export function speakersOf(plan, chat) {
  const campaign = campaignOf(chat);
  const found = new Map();
  for (const cue of plan.cues) {
    const key = cue.actor || "narrator";
    let speaker = found.get(key);
    if (!speaker) {
      speaker = { key, name: cue.speaker, actor: cue.actor, cues: 0, voices: new Set(), reasons: new Set(), targets: [] };
      found.set(key, speaker);
    }
    speaker.cues += 1;
    if (cue.status === "voiced" && cue.voice) speaker.voices.add(cue.voice.engine + "/" + cue.voice.voice);
    else if (cue.reason) speaker.reasons.add(cue.reason);
    for (const item of cue.considered) {
      const target = targetOf(item.rule, item.names, cue.speaker, campaign);
      if (target && !speaker.targets.some((held) => targetKey(held) === targetKey(target))) speaker.targets.push(target);
    }
  }
  return [...found.values()].map((speaker) => ({ ...speaker, voices: [...speaker.voices], reasons: [...speaker.reasons] }));
}

// The voices this device can cast now: every voice of every engine that has some.
export function voiceOptions(engines) {
  const options = [];
  for (const engine of engines) for (const voice of engine.voices) options.push({ value: engine.engine + "/" + voice, label: engine.engine + "/" + voice });
  return options;
}

// The arguments of voices.review and of voicecast.cast/uncast for a target, a voice ("engine/voice") and rights.
export function castArgs(target, { voice = "", rights = "engine", note = "" } = {}) {
  const args = { target: target.target };
  if (target.target === "narrator") args.campaign = target.campaign;
  else args.ref = target.ref;
  const slash = voice.indexOf("/");
  args.engine = slash < 0 ? voice : voice.slice(0, slash);
  args.voice = slash < 0 ? "" : voice.slice(slash + 1);
  args.rights = rights;
  if (note.trim()) args.note = note.trim();
  return args;
}

export function targetArgs(target) {
  return target.target === "narrator" ? { target: "narrator", campaign: target.campaign } : { target: target.target, ref: target.ref };
}

// Why the form cannot be reviewed yet, in the words the page shows, or null.
export function formProblem(form) {
  if (!form.target) return "Choose what to cast.";
  if (!form.voice) return "Choose a voice.";
  if (form.rights !== "engine" && !form.note.trim()) return "A licensed or consented voice needs a note: what was agreed, and with whom.";
  return null;
}

// What a review says the write will do, in one sentence.
export function reviewText(review) {
  const where = review.describes;
  if (review.change === "remove") return "Remove " + review.before.key + " from " + where + ". It then stays text unless another rule voices it.";
  if (review.change === "same") return where + " is already cast " + review.after.key + ": nothing would change.";
  const rights = review.after.rights === "engine" ? "" : " (" + review.after.rights + ": " + review.after.note + ")";
  if (review.change === "replace") return "Cast " + review.after.key + rights + " for " + where + ", replacing " + review.before.key + ".";
  return "Cast " + review.after.key + rights + " for " + where + ".";
}

// Whether the casting `read` now holds what `review` reviewed: a write retried after it was made is then done.
export function holdsReviewed(read, review) {
  const entry = read.casting.find((item) => item.target === review.target && (item.ref || null) === (review.ref || null)
    && (item.campaign || null) === (review.campaign || null));
  if (review.change === "remove") return !entry;
  return Boolean(entry && review.after && entry.engine === review.after.engine && entry.voice === review.after.voice
    && entry.rights === review.after.rights && (entry.note || null) === (review.after.note || null));
}
