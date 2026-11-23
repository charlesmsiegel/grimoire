// Voices (116, sprint 083's casting): which local voice speaks for whom when a chat is listened to
// (docs/LISTEN.md, docs/VOICE_CASTING.md). Addresses:
//   #/voices                     the casting and this device's engines
//   #/voices?chat=<ChatRef>      also that chat's speakers, each with what it can be cast as
// The casting is this device's configuration (config/voices/casting.json), never a source, campaign or chat
// record: nothing here writes a turn or a source. Every change is reviewed first (voices.review writes nothing and
// says what the write would do, refusing as the write would) and then made as reviewed (voicecast.cast or
// voicecast.uncast naming the etag the review read): a casting changed in between refuses it `changed`, and the
// page reads it again. This is not 061's Voice page (speech recognition) or 067's Read aloud.

import { RIGHTS, castArgs, formProblem, holdsReviewed, reviewText, speakersOf, targetArgs, targetKey, voiceOptions } from "./logic.js";

const MAX_CHATS = 100;
const LATER = [
  ["Pronunciation, prosody and expression", "Sprint 083's milestone 2 (author-controlled pronunciation and prosody) is not built."],
  ["Mood and inflection cues", "Sprint 083's milestone 3 is not built."],
  ["Syncing a casting between devices", "A casting names this device's own engines, so device sync does not carry it; no sync of it is built."],
  ["Licensed or consented voice engines", "The casting records a voice's licence or consent, but no engine with such voices ships yet: only the engine's own voices can be heard."],
  ["A listening run with real people", "Whether the voices can be told apart by ear has not been judged with real listeners."],
];

export async function mount(root, ctx) {
  const { ui } = ctx;
  const { el } = ui;
  ctx.setTitle("Voices");
  ctx.layout("page");
  ctx.styles("page.css");
  const chat = ctx.query.get("chat") || null;
  if (chat && ctx.registered("chat")) ctx.setCrumb(ui.link("Back to the chat", "chat", [chat]));
  // read: voicecast.read; plan: the chat's voicecast.plan; review: { key, answer } waiting for Make this change;
  // forms: each speaker's unsent choices, kept across redraws
  const state = { read: null, plan: null, planError: null, review: null, reviewing: 0, forms: new Map(), editing: null, busy: false, said: {}, chats: null };

  const live = el("p", { class: "voices-live", role: "status", "aria-live": "polite", tabindex: "-1" });
  const engines = el("section", { class: "g-card voices-card", "aria-label": "Engines on this device" });
  const speakers = el("section", { class: "g-card voices-card", "aria-label": "Speakers in this chat" });
  const casting = el("section", { class: "g-card voices-card", "aria-label": "The casting" });
  const later = el("section", { class: "voices-later", "aria-label": "Not available yet" });
  root.replaceChildren(el("div", { class: "g-stack g-loose" },
    el("p", { class: "g-muted" }, "The voices a chat is listened to in, on this device. Voices are local: nothing is sent, billed or kept, and casting changes no story, source or chat."),
    live, speakers, casting, engines, later));

  const say = (text, { focus = true } = {}) => { live.textContent = text; if (focus) live.focus(); };

  async function load() {
    state.read = await ctx.api("voicecast.read", {});
    if (chat && ctx.has("voicecast.plan")) {
      try { state.plan = await ctx.api("voicecast.plan", { chat }); state.planError = null; }
      catch (error) { if (error.reason === "left") throw error; state.plan = null; state.planError = error; }
    }
    draw();
  }

  // -- a change: reviewed, then made as reviewed ------------------------------------------------------------------
  // Each review is numbered: an answer is shown only for the latest one, with the same form as when it was asked
  // (`unreviewed` numbers past it when a choice changes, so a late answer for the old choice is dropped).
  function unreviewed() { state.reviewing += 1; state.review = null; }

  // `still()` says whether the form still asks for `args` when the answer comes back.
  async function review(key, kind, args, still = () => true) {
    if (state.busy) return;
    const mine = ++state.reviewing;
    state.busy = true;
    state.review = null;
    draw();
    let shown = false;
    try {
      const answer = await ctx.api("voices.review", { kind, ...args });
      if (mine === state.reviewing && still()) { state.review = { key, kind, args, answer }; shown = true; }
    } catch (error) {
      if (error.reason === "left") return;
      if (mine === state.reviewing) { state.said[key] = "Not reviewed: " + ui.errorText(error); shown = true; }
    } finally {
      state.busy = false;
      if (!ctx.signal.aborted) draw();
    }
    if (ctx.signal.aborted || !shown) return;
    const box = root.querySelector('div[aria-label="Review this change"]');
    const buttons = box ? [...box.querySelectorAll("button")] : [];
    const makeButton = buttons.find((node) => node.textContent === "Make this change");
    if (makeButton) { makeButton.focus(); return; }
    if (box) {  // nothing would change: the sentence is said, and the focus goes to Cancel
      say(reviewText(state.review.answer), { focus: false });
      const cancel = buttons.find((node) => node.textContent === "Cancel");
      if (cancel) cancel.focus();
      return;
    }
    say(state.said[key] || "");
  }

  // The casting read again after a write whose outcome is not known; false (and said) where that read fails too.
  async function reread() {
    try {
      await load();
      return true;
    } catch (again) {
      if (again.reason !== "left") say("The app did not answer, so whether the change was made is not known. Reload this page to see the casting.");
      return false;
    }
  }

  async function make() {
    const reviewed = state.review;
    if (!reviewed || state.busy) return;
    state.busy = true;
    draw();
    const { kind, args, answer } = reviewed;
    try {
      await ctx.api(kind === "cast" ? "voicecast.cast" : "voicecast.uncast", { ...args, expected: answer.etag });
      state.review = null;
      state.editing = kind === "cast" ? null : state.editing;
      if (kind === "cast") state.forms.delete(reviewed.key);
      await load();
      say(kind === "cast" ? "Cast: " + answer.after.key + " for " + answer.describes + "." : "Removed the voice for " + answer.describes + ".");
    } catch (error) {
      if (error.reason === "left") return;
      state.review = null;
      if (error.reason === "changed") {
        if (!await reread()) return;
        say(holdsReviewed(state.read, answer)
          ? "That change was already made: the casting holds it."
          : "The casting changed since this was reviewed, so nothing was written. It is read again: review the change again.");
      } else if (error.reason === "network" || error.reason === "bad_response") {
        // the answer was lost, not the write: whether it was made is read back, never assumed
        if (!await reread()) return;
        say(holdsReviewed(state.read, answer)
          ? "The answer was lost, but the change was made: the casting holds it."
          : "The answer was lost, and the casting does not hold this change. Review it again.");
      } else {
        draw();
        say("Nothing was written: " + ui.errorText(error));
      }
    } finally {
      state.busy = false;
      if (!ctx.signal.aborted) draw();
    }
  }

  function reviewBox(key) {
    const reviewed = state.review;
    if (!reviewed || reviewed.key !== key) return null;
    const answer = reviewed.answer;
    const parts = [el("p", {}, reviewText(answer))];
    if (answer.change !== "same") {
      parts.push(el("p", { class: "g-muted" }, answer.removes_file ? "Removes " + answer.writes.join(", ") + " (the casting would be empty)."
        : "Writes " + answer.writes.join(", ") + " on this device. No turn, source or chat changes."));
    }
    if (answer.available === false) parts.push(ui.notice("This device has no such voice now: lines cast to it stay text here until it does.", { kind: "soft" }));
    const makeButton = answer.change === "same" ? null : ui.button("Make this change", make, { kind: answer.change === "remove" ? "danger" : "primary", disabled: state.busy });
    return el("div", { class: "voices-review", role: "group", "aria-label": "Review this change" }, ...parts,
      el("div", { class: "g-actions" }, makeButton, ui.button("Cancel", () => { unreviewed(); draw(); say("Not changed."); })));
  }

  // -- the sections ------------------------------------------------------------------------------------------------
  function drawEngines() {
    const read = state.read;
    engines.replaceChildren(el("h2", { class: "g-heading" }, "Engines on this device"),
      read.engines.length ? el("ul", { class: "voices-list" }, read.engines.map((engine) => el("li", {},
        el("strong", {}, engine.engine), " · ", engine.available ? engine.voices.join(", ") : "no voices now",
        el("div", { class: "g-muted" }, "Local, no network, free")))) : ui.empty("No speech engine is installed on this device: every line stays text."));
  }

  function drawCasting() {
    const read = state.read;
    const parts = [el("h2", { class: "g-heading" }, "The casting")];
    if (read.problem) {
      parts.push(ui.notice("The casting file " + read.problem.path + " cannot be read (" + read.problem.reason + "). Nothing is cast until it is mended or removed, and it is never written over.", { kind: "bad" }));
    }
    if (!read.casting.length) parts.push(ui.empty("Nobody is cast yet: every line stays text."));
    const list = el("ul", { class: "voices-list" });
    read.casting.forEach((entry) => {
      const key = "entry:" + entry.target + "|" + (entry.ref || "") + "|" + (entry.campaign || "");
      const target = { target: entry.target, ref: entry.ref, campaign: entry.campaign };
      list.append(el("li", { class: "voices-entry" },
        el("div", {}, el("strong", {}, entry.engine + "/" + entry.voice), " for ", entry.describes),
        el("div", { class: "g-muted" }, rightsText(entry), entry.available ? "" : " · not on this device now"),
        state.said[key] ? el("p", { role: "alert" }, state.said[key]) : null,
        reviewBox(key) || el("div", { class: "g-actions" }, ui.button("Remove…", () => { delete state.said[key]; review(key, "uncast", targetArgs(target)); },
          { size: "small", disabled: state.busy }))));
    });
    if (read.casting.length) parts.push(list);
    casting.replaceChildren(...parts);
  }

  function rightsText(entry) {
    if (entry.rights === "engine") return "Ships with the engine";
    return (entry.rights === "licensed" ? "Licensed" : "Consented") + ": " + (entry.note || "");
  }

  function drawSpeakers() {
    const parts = [el("h2", { class: "g-heading" }, "Speakers in this chat")];
    if (!chat) {
      parts.push(el("p", { class: "g-muted" }, "Choose a chat to cast its speakers, or open this page from a chat's Listen to the scene."));
      parts.push(chatPicker());
      speakers.replaceChildren(...parts);
      return;
    }
    if (state.planError) {
      parts.push(ui.notice(state.planError.reason === "turn_fork" ? "This chat forked: its speakers are listed once the fork is resolved."
        : "This chat's speakers cannot be read: " + ui.errorText(state.planError), { kind: "bad" }));
    } else if (state.plan) {
      const found = speakersOf(state.plan, chat);
      if (!found.length) parts.push(ui.empty("Nothing has been said in this chat yet."));
      const options = voiceOptions(state.read.engines);
      for (const speaker of found) parts.push(speakerRow(speaker, options));
    }
    speakers.replaceChildren(...parts);
  }

  function chatPicker() {
    const box = el("div", { class: "voices-picker" });
    if (!ctx.has("chats")) return box;
    if (!state.chats) state.chats = ctx.api("chats", { limit: MAX_CHATS });  // asked once, drawn on every redraw
    state.chats.then((listed) => {
      const pick = ui.choice("Chat", [{ value: "", label: "Choose a chat" },
        ...listed.chats.map((entry) => ({ value: entry.chat, label: entry.label || entry.chat }))],
      { onchange: (value) => { if (value) ctx.navigate(ctx.href("voices", [], { chat: value })); } });
      box.replaceChildren(pick.node);
    }, (error) => { if (error.reason !== "left") box.replaceChildren(ui.errorBox(error)); });
    return box;
  }

  function speakerRow(speaker, options) {
    const key = "speaker:" + speaker.key;
    const now = speaker.voices.length ? "Speaks with " + speaker.voices.join(", ")
      : "Text only" + (speaker.reasons.length ? " (" + speaker.reasons.map((reason) => reason.replace(/_/g, " ")).join(", ") + ")" : "");
    const row = el("div", { class: "voices-speaker", role: "group", "aria-label": speaker.name },
      el("div", { class: "voices-speaker-head" }, el("strong", {}, speaker.name), el("span", { class: "g-muted" }, " · " + speaker.cues + " cue" + (speaker.cues === 1 ? "" : "s") + " · " + now)));
    if (state.said[key]) row.append(el("p", { role: "alert" }, state.said[key]));
    if (state.editing !== speaker.key) {
      row.append(el("div", { class: "g-actions" }, ui.button("Cast a voice…", () => { state.editing = speaker.key; unreviewed(); draw(); refocus(speaker.name, "Cast for"); },
        { size: "small", disabled: state.busy || !options.length || Boolean(state.read.problem) })));
      if (!options.length) row.append(el("p", { class: "g-muted" }, "This device has no voices to cast."));
      return row;
    }
    const form = state.forms.get(speaker.key) || { target: targetKey(speaker.targets[0]), voice: options[0] ? options[0].value : "", rights: "engine", note: "" };
    state.forms.set(speaker.key, form);
    // A changed choice drops any review of the old one; the row is drawn again and the focus stays on that choice.
    const changed = (label, set) => (value) => { set(value); unreviewed(); draw(); refocus(speaker.name, label); };
    const target = ui.choice("Cast for", speaker.targets.map((item) => ({ value: targetKey(item), label: item.label })),
      { value: form.target, onchange: changed("Cast for", (value) => { form.target = value; }) });
    const voice = ui.choice("Voice", options, { value: form.voice, onchange: changed("Voice", (value) => { form.voice = value; }) });
    const rights = ui.choice("Rights", RIGHTS, { value: form.rights, onchange: changed("Rights", (value) => { form.rights = value; }) });
    const note = ui.field("Rights note", { value: form.note, hint: "Needed for a licensed or consented voice: what was agreed, and with whom (at most 500 characters)." });
    note.input.setAttribute("maxlength", "500");
    note.input.addEventListener("input", () => {
      form.note = note.input.value;
      const shown = state.review && state.review.key === key;
      unreviewed();  // a review of the old note is dropped, shown or on its way
      if (shown) {  // its box goes: the row is drawn again, and typing goes on where it was
        const at = note.input.selectionStart;
        draw();
        const again = refocus(speaker.name, "Rights note");
        if (again && typeof at === "number" && again.setSelectionRange) again.setSelectionRange(at, at);
      }
    });
    const chosen = speaker.targets.find((item) => targetKey(item) === form.target);
    const reviewButton = ui.button("Review", () => {
      const missing = formProblem(form);
      if (missing) { state.said[key] = missing; draw(); return; }
      delete state.said[key];
      const args = castArgs(chosen, form), sent = JSON.stringify(args);
      const asks = () => castArgs(speaker.targets.find((item) => targetKey(item) === form.target) || chosen, form);
      review(key, "cast", args, () => state.editing === speaker.key && JSON.stringify(asks()) === sent);
    }, { kind: "primary", disabled: state.busy });
    row.append(el("div", { class: "voices-form", role: "group", "aria-label": "Cast a voice for " + speaker.name },
      target.node, voice.node, rights.node, form.rights === "engine" ? null : note.node,
      reviewBox(key) || el("div", { class: "g-actions" }, reviewButton,
        ui.button("Cancel", () => { state.editing = null; unreviewed(); state.forms.delete(speaker.key); draw(); }))));
    return row;
  }

  // Focus the field labelled `label` in a speaker's cast form, as drawn now; the field, or null.
  function refocus(name, label) {
    const group = [...root.querySelectorAll("div")].find((node) => node.getAttribute("aria-label") === "Cast a voice for " + name);
    const tag = group ? [...group.querySelectorAll("label")].find((node) => node.textContent === label) : null;
    const id = tag ? tag.getAttribute("for") : null;
    const field = id ? [...group.querySelectorAll("select"), ...group.querySelectorAll("input"), ...group.querySelectorAll("textarea")]
      .find((node) => node.id === id) : null;
    if (field) field.focus();
    return field || null;
  }

  function drawLater() {
    later.replaceChildren(el("h2", { class: "g-heading" }, "Not available yet"), ...LATER.map(([what, why]) => ui.unavailable(what, why)));
  }

  function draw() {
    if (!state.read) return;
    drawSpeakers();
    drawCasting();
    drawEngines();
    drawLater();
  }

  await load();
}
