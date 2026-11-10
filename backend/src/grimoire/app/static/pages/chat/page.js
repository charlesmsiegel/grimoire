// One chat (044's chat view, ported into the 108 shell; Sprint 111 owns it from here): #/chat/<ChatRef>. The
// composer's text is a draft: it survives leaving the chat and coming back, and is cleared only once posted.

export async function mount(root, ctx) {
  const { ui } = ctx;
  const { el } = ui;
  const chat = ctx.path[0];
  if (!chat) { root.append(ui.notice("No chat is named in this address."), ui.actions(ui.link("All chats", "chats"))); return; }
  ctx.styles("page.css");
  const view = el("div", {});
  const composer = ui.field("Your turn", { draft: ctx.draft("composer:" + chat), multiline: true, placeholder: "Say or do something" });
  const send = ui.button("Post", () => ctx.act(post), { kind: "primary" });
  const bar = el("div", { class: "chat-bar" }, el("div", { class: "chat-bar-row" }, composer.node, send));
  root.append(el("div", { class: "g-actions" }, ui.link("All chats", "chats")), view, bar);
  if (ctx.has("sync")) ctx.setActions(syncButton(ctx, () => open()));
  // Answer only where the app serves generate and a model is set up: no button pretends to work.
  const generates = () => ctx.has("generate") && Boolean(ctx.library && ctx.library.generation);

  async function open() { render(await ctx.api("open", { chat })); }

  // A scene with no NPC who can speak is answered by the director: generate is refused nothing_to_generate
  // before anything is written, so the director reply is asked only then.
  async function answer() {
    try { return await ctx.api("generate", { chat }); }
    catch (error) { if (error.reason !== "nothing_to_generate") throw error; }
    return ctx.api("generate", { chat, director: true });
  }

  async function post() {
    const text = composer.value.trim();
    if (!text) return;
    const scene = await ctx.api("post", { chat, text });
    composer.clear();
    render(scene);  // the post is shown even if the answer then fails, so it is never sent twice
    if (generates()) render(await answer());
  }

  const speakerOf = (line) => line.speaker || "Narrator";
  const line = (item, cls = "chat-line") => el("div", { class: cls }, el("span", { class: "chat-speaker" }, speakerOf(item) + ": "), item.text);

  function render(scene) {
    ctx.setTitle(scene.label || (scene.launch && scene.launch.ref) || scene.chat);
    const parts = [];
    const cast = el("div", { class: "chat-cast" });
    for (const member of scene.cast) {
      const avatar = member.art.slots.find((slot) => slot.slot === "avatar");
      const face = el("div", { class: "chat-face", "aria-hidden": "true" }, avatar && avatar.state === "unavailable" ? "?" : "·");
      if (avatar && avatar.state === "shown") ctx.picture(avatar.image).then((url) => { if (url) face.replaceWith(el("img", { class: "chat-face", src: url, alt: member.name })); });
      cast.append(el("div", { class: "chat-member" }, face, member.name, member.persona ? el("div", { class: "g-muted" }, "you") : null));
    }
    parts.push(cast);
    for (const problem of scene.problems) parts.push(ui.notice(problem.code + (problem.detail ? ": " + problem.detail : "")));
    if (scene.history) {
      parts.push(el("p", { class: "g-muted" }, "Imported history"));
      for (const item of scene.history) parts.push(line(item));
    }
    for (const turn of scene.turns) {
      if (turn.post) parts.push(el("div", { class: "chat-post" }, el("span", { class: "chat-speaker" }, turn.post.speaker + ": "), turn.post.text));
      if (turn.kept) for (const item of turn.kept.lines) parts.push(line(item));
      for (const take of turn.offered) {
        const box = el("div", { class: "chat-take" }, el("div", { class: "g-muted" }, "Take " + take.take + " (" + take.state + ")"));
        for (const item of take.lines) box.append(line(item));
        for (const call of take.calls) box.append(el("div", { class: "g-muted" }, call.actor + ": " + call.status + (call.reason ? " (" + call.reason + ")" : "")));
        if (scene.state.keepable.includes(take.take)) {
          const unscreened = scene.state.unscreened.includes(take.take);
          box.append(ui.button(unscreened ? "Keep (uses unscreened lore)" : "Keep", () => ctx.act(async () =>
            render(await ctx.api("keep", { chat, turn: turn.turn, take: take.take, acknowledge_unscreened: unscreened }))), { kind: "primary" }));
        }
        parts.push(box);
      }
    }
    const waiting = scene.state.waiting;
    if (scene.fork) {
      const box = ui.notice("This chat forked: both devices played on. Choose the branch that goes on.");
      for (const branch of scene.fork.branches.filter((item) => !item.set_aside)) {
        box.append(ui.button("Keep the branch from " + (branch.device || "this device"), () => ctx.act(async () =>
          render(await ctx.api("choose", { chat, turn: branch.head })))));
      }
      parts.push(box);
    }
    for (const conflict of scene.state.conflicts) {
      const peer = ctx.library && ctx.library.peers[0] ? ctx.library.peers[0].peer : null;
      const box = ui.notice("In conflict after sync: " + conflict.path);
      if (peer) for (const keep of ["mine", "theirs"]) {
        box.append(ui.button(keep === "mine" ? "Keep this device's" : "Keep " + peer + "'s", () => ctx.act(async () => {
          const report = await ctx.api("resolve", { peer, path: conflict.path, mine: conflict.mine, theirs: conflict.theirs, keep });
          ctx.notify("Resolve: " + report.status + (report.reason ? " (" + report.reason + ")" : ""));
          await open();
        })));
      }
      parts.push(box);
    }
    const actions = ui.actions();
    if (!scene.state.seated) actions.append(ui.button("Take your seat", () => ctx.act(async () => render(await ctx.api("continue", { chat }))), { kind: "primary" }));
    if (generates()) actions.append(ui.button("Answer", () => ctx.act(async () => render(await answer()))));
    const openTurn = scene.state.open_turn;  // Pass names the turn on screen, so a late tap never closes a newer one
    if (openTurn) actions.append(ui.button("Pass", () => ctx.act(async () => render(await ctx.api("pass", { chat, turn: openTurn })))));
    if (waiting.includes("paused")) actions.append(ui.button("Resume", () => ctx.act(async () => render(await ctx.api("resume", { chat })))));
    else actions.append(ui.button("Pause", () => ctx.act(async () => render(await ctx.api("pause", { chat })))));
    actions.append(addPicture(scene));
    parts.push(actions);
    if (waiting.length) parts.push(el("p", { class: "g-muted" }, "Waiting: " + waiting.join(", ")));
    view.replaceChildren(...parts);
    window.scrollTo(0, document.body.scrollHeight);
  }

  function addPicture(scene) {
    const who = ui.choice("Picture for", scene.cast.map((member) => ({ value: member.actor, label: member.name })));
    const file = el("input", { type: "file", accept: "image/*", hidden: true });
    file.addEventListener("change", () => ctx.act(async () => {
      const chosen = file.files[0]; file.value = "";
      if (!chosen) return;
      const data = new Uint8Array(await chosen.arrayBuffer());
      let binary = "";
      for (let i = 0; i < data.length; i += 0x8000) binary += String.fromCharCode.apply(null, data.subarray(i, i + 0x8000));
      const member = scene.cast.find((item) => item.actor === who.value);
      const slot = member && member.art.slots.find((item) => item.slot === "avatar");
      const args = { chat, actor: who.value, slot: "avatar", media: btoa(binary) };
      if (slot && slot.etag) args.expected = slot.etag;
      render(await ctx.api("add_art", args));
    }));
    return el("span", { class: "chat-picture" }, who.node, ui.button("Add picture", () => file.click()), file);
  }

  await open();
}

function syncButton(ctx, after) {
  return ctx.ui.button("Sync", () => ctx.act(async () => {
    const peers = (ctx.library && ctx.library.peers) || [];
    if (!peers.length) { ctx.notify("No paired device is configured for this app.", { bad: true }); return; }
    for (const peer of peers) {
      const report = await ctx.api("sync", { peer: peer.peer });
      ctx.notify(peer.peer + ": " + report.status + (report.reason ? " (" + report.reason + ")" : "")
        + (report.conflicts.length ? ", " + report.conflicts.length + " in conflict" : ""), { bad: report.status !== "synced" });
    }
    await ctx.refreshLibrary();
    await after();
  }));
}
