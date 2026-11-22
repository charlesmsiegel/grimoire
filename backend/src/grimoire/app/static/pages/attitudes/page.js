// An NPC's attitude checked against its own evidence (078, Sprint 116): #/attitudes/<ChatRef>?audience=director.
// The director picks one of the chat's NPCs as the holder (a player character's feelings are its player's and are
// never offered), whom the attitude is toward (another of the cast, or one of the campaign's groups), a dimension,
// the NPC's own account and an optional degree, and what it rests on, picked from that NPC's own evidence and
// current beliefs as the campaign hub shows them for it. attitudes.check asks 078 whether it would be admitted.
// Nothing is saved: 078 has no store, and the attitudes admitted here are held by this page only while it is open
// (each check admits them all again, from scratch, on the server). No NPC plays from an attitude yet. The table's
// view says whose this is and shows nothing.

import { DIMENSIONS, degreeOptions, headText, newAttitudeId, requestOf, towardOptions, verdictText } from "./logic.js";

const AUDIENCES = [{ value: "table", label: "Table view" }, { value: "director", label: "Director view" }];
const NOT_BUILT = { save: "Saving an attitude", play: "An NPC playing from its attitudes", reputation: "Reputation and allegiance" };

export async function mount(root, ctx) {
  const { ui } = ctx;
  const { el } = ui;
  ctx.styles("page.css");
  ctx.layout("page");
  const chat = ctx.path[0];
  if (!chat) { root.append(ui.notice("Open this from a chat's Details."), ui.actions(ui.buttonLink("All chats", "chats"))); return; }
  ctx.setTitle("Attitudes");
  ctx.setCrumb(ui.link("Chats", "chats"), " / ", ctx.registered("chat") ? ui.link("This chat", "chat", [chat]) : "This chat");
  const audience = ctx.query.get("audience") === "director" ? "director" : "table";
  const views = ui.segments("Whose view", AUDIENCES, { value: audience,
    onchange: (value) => ctx.navigate(ctx.href("attitudes", [chat], { audience: value === "director" ? "director" : undefined })) });
  root.append(el("div", { class: "g-row attitudes-views" }, views.node, el("span", { class: "g-grow g-muted" },
    audience === "director" ? "Check what an NPC feels about someone against what that NPC itself knows." : "An NPC's feelings are the director's.")));
  if (audience !== "director") {
    root.append(card(ui, "Attitudes", el("p", { class: "g-muted" },
      "What an NPC feels about others is the director's. Switch to the director view to check one.")));
    return;
  }

  const setting = await ctx.api("attitudes.cast", { chat, audience });
  const holders = setting.cast.filter((item) => item.may_hold);
  const form = { id: "", holder: "", toward: "", dimension: "", degree: "", account: "", evidence: [], beliefs: [] };
  let held = [];  // the attitudes this page holds as admitted: in memory only, admitted again on every check
  let asked = 0;
  const formSlot = el("div", {}), verdictSlot = el("div", {}), heldSlot = el("div", {});
  const status = el("div", { class: "g-field-error", role: "alert", hidden: true });
  const say = (text) => { status.textContent = text || ""; status.hidden = !text; };
  const changed = () => { asked += 1; verdictSlot.replaceChildren(); say(""); };

  const holder = ui.choice("Whose attitude", [{ value: "", label: "Choose an NPC…" },
    ...holders.map((item) => ({ value: item.actor, label: item.name || item.actor }))], { value: "",
    onchange: (value) => { form.holder = value; form.toward = ""; form.evidence = []; form.beliefs = []; changed(); loadGrounds(); } });
  root.append(card(ui, "Whose attitude",
    el("p", { class: "attitudes-saved" }, ui.pill("Nothing is saved", { kind: "accent" }),
      " A check asks only whether 078 would admit it. No NPC plays from it yet."),
    holders.length ? holder.node : ui.empty("No NPC of this chat is the director's, so nobody here holds an attitude the director writes."),
    el("ul", { class: "attitudes-lines" }, setting.cast.filter((item) => !item.may_hold).map((item) => el("li", {},
      el("strong", {}, item.name || item.actor), el("span", { class: "g-muted" }, " · " + item.why_not + ": not offered"))))),
  formSlot, verdictSlot, heldSlot,
  card(ui, "Not built yet", ...Object.entries(NOT_BUILT).map(([key, title]) => ui.unavailable(title, setting.not_available[key]))));
  drawHeld([]);

  async function loadGrounds() {
    const turn = asked;
    formSlot.replaceChildren();
    if (!form.holder) return;
    let grounds;
    try {
      grounds = await ctx.api("attitudes.grounds", { chat, holder: form.holder, audience });
    } catch (error) {
      if (turn === asked && !ctx.signal.aborted) formSlot.replaceChildren(card(ui, "The attitude", ui.errorBox(error)));
      return;
    }
    if (turn !== asked || ctx.signal.aborted) return;
    drawForm(grounds);
  }

  function drawForm(grounds) {
    const name = grounds.holder.name || grounds.holder.actor;
    const toward = ui.choice("Toward", towardOptions(setting.cast, setting.groups, form.holder), { value: form.toward,
      onchange: (value) => { form.toward = value; changed(); } });
    const degreeSlot = el("div", {});
    const drawDegree = () => {
      const degree = ui.choice("Degree (optional)", degreeOptions(form.dimension, setting.bipolar, setting.max_degree), { value: form.degree,
        onchange: (value) => { form.degree = value; changed(); } });
      degreeSlot.replaceChildren(degree.node);
    };
    const dimension = ui.choice("What " + name + " feels", [{ value: "", label: "Choose…" },
      ...setting.dimensions.map((value) => ({ value, label: DIMENSIONS[value] || value }))], { value: form.dimension,
      onchange: (value) => { form.dimension = value; form.degree = ""; changed(); drawDegree(); } });
    drawDegree();
    const account = ui.field(name + "'s own reading of it", { value: form.account, multiline: true,
      hint: "In the NPC's terms, at most " + setting.max_text + " characters." });
    account.input.addEventListener("input", () => { form.account = account.input.value; changed(); });
    const box = (kind, value, text) => {
      const input = el("input", { type: "checkbox", value, "data-focus-key": "attitudes:" + kind + ":" + value });
      input.checked = form[kind].includes(value);
      input.addEventListener("change", () => {
        form[kind] = input.checked ? [...form[kind].filter((item) => item !== value), value] : form[kind].filter((item) => item !== value);
        changed();
      });
      return el("label", { class: "g-row attitudes-check" }, input, el("span", {}, text));
    };
    const rests = el("fieldset", { class: "attitudes-checks" }, el("legend", {}, "What it rests on: " + name + "'s own"),
      ...grounds.evidence.map((item) => box("evidence", item.evidence, "“" + item.content + "” · " + item.type + ", " + item.channel
        + (item.attributed_to ? ", from " + (item.attributed_to.name || item.attributed_to.ref) : "") + (item.at === null ? "" : " · moment " + item.at))),
      ...grounds.beliefs.map((item) => box("beliefs", item.belief, "Believes (" + item.stance + "): " + (item.text || item.belief))),
      grounds.evidence.length || grounds.beliefs.length ? null : el("p", { class: "g-muted" }, name + " holds no evidence or belief yet, so no attitude can rest on anything."));
    formSlot.replaceChildren(card(ui, "The attitude",
      el("div", { class: "g-stack attitudes-form", role: "group", "aria-label": name + "'s attitude" },
        toward.node, dimension.node, degreeSlot, account.node, rests),
      el("p", { class: "g-muted" }, "Only " + name + "'s own evidence and current beliefs are offered, as the campaign hub shows them for " + name + "."),
      status, ui.actions(ui.button("Check it", check, { kind: "primary" }))));
  }

  const check = () => ctx.act(async () => {
    const built = requestOf({ ...form, id: newAttitudeId() }, { chat, held, maxText: setting.max_text });
    if (built.error) { say(built.error); return; }
    say("");
    const turn = ++asked;
    let answer;
    try {
      answer = await ctx.api("attitudes.check", built.request);
    } catch (error) {
      if (turn !== asked || ctx.signal.aborted) return;
      if (held.length && error && error.reason && error.reason !== "director_only" && error.reason !== "invalid_field") {
        held = [];  // the library changed under one held here: the page holds none of them any more
        drawHeld([]);
      }
      say(ui.errorText(error));
      return;
    }
    if (turn !== asked || ctx.signal.aborted) return;
    if (answer.verdict.admitted) held = [...held, built.request.attitude];
    drawHeld(answer.current);
    verdictSlot.replaceChildren(card(ui, "Verdict",
      el("p", { class: "attitudes-verdict", tabindex: "-1" }, ui.pill(answer.verdict.admitted ? "Admitted" : "Refused",
        { kind: answer.verdict.admitted ? "good" : "bad" }), " " + verdictText(answer.verdict)),
      el("p", { class: "g-muted" }, answer.unchanged ? "Nothing was written: what every character knows is as it was." : "The library changed meanwhile; nothing was written by this check.")));
    const focus = verdictSlot.querySelector(".attitudes-verdict");
    if (focus) focus.focus();
  });

  function drawHeld(current) {
    const holderName = (setting.cast.find((item) => item.actor === form.holder) || {}).name;
    heldSlot.replaceChildren(card(ui, "Held on this page",
      el("p", { class: "g-muted" }, "The attitudes admitted while this page is open. Leaving the page drops them; each check admits them all again first."),
      held.length ? el("ul", { class: "attitudes-lines" }, held.map((item) => el("li", { class: "g-row" },
        el("span", { class: "g-grow" }, (setting.cast.find((one) => one.actor === item.holder) || {}).name || item.holder, ": ",
          headText({ ...item, toward_name: nameOf(item.toward), degree: item.degree })),
        ui.button("Forget", () => { held = held.filter((one) => one.id !== item.id); changed(); drawHeld([]); }, { size: "small" }))))
        : ui.empty("None yet."),
      current.length && holderName ? el("div", { class: "g-stack" }, el("h3", { class: "attitudes-sub" }, holderName + " now holds"),
        el("ul", { class: "attitudes-lines" }, current.map((item) => el("li", {}, headText(item))))) : null));
  }

  function nameOf(ref) {
    const found = setting.cast.find((item) => item.actor === ref) || setting.groups.find((item) => item.actor === ref);
    return found ? found.name : ref;
  }
}

function card(ui, title, ...children) {
  const node = ui.card({ tag: "section", label: title }, ui.el("h2", { class: "g-heading" }, title), ...children);
  node.classList.add("attitudes-card");
  return node;
}
