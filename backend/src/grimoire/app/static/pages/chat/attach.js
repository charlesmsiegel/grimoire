// A picture on a post: the person's own illustration of what they posted. "Attach an image" in the composer
// holds one file (a thumbnail with Remove) until the post is made; then it is added to the chat's campaign in
// the slot "post.<turn id>" and shown under that post (docs/MEDIA_SYNC_UI.md). It sits beside the transcript,
// like a bookmark: the turn is never changed for it, and no model is sent it. A post whose turn was set aside
// or is gone keeps its picture in the campaign's art (the Art page lists it); it is not shown here.
//
// makeAttach(ctx, chat, { field, run, redraw }) -> { under(turn, row), posted(scene, before) }
//   field                 the composer's field node: the button and the waiting thumbnail are added to it
//   under(turn, row)      the post's row, with the picture on its turn (if any) under its text
//   posted(scene, before) call after a post was written: attaches the waiting file to the turn it made

import { ACCEPT, MAX_UPLOAD, base64, openPicker, zoomClass, zoomOf } from "../art/picker.js";
import { chatPictures } from "./pictures.js";

export function makeAttach(ctx, chat, { field, run, redraw }) {
  const { ui } = ctx;
  const { el } = ui;
  const pictures = chatPictures(ctx, chat, redraw);
  if (!pictures.served) return { under: (_turn, row) => row, posted: () => {} };

  let waiting = null;  // { file, url, turn: the post it failed to reach (set after a refused upload), problem }
  const input = el("input", { type: "file", accept: ACCEPT, hidden: true, "aria-label": "Image to attach" });
  const button = el("button", { type: "button", class: "chat-attach-button", "data-focus-key": "chat-attach",
    onclick: () => input.click() }, "Attach an image");
  const tray = el("div", { class: "chat-attach-tray", hidden: true });
  const drop = () => {
    if (waiting && waiting.url) URL.revokeObjectURL(waiting.url);
    waiting = null;
    show();
  };
  function show() {
    button.hidden = Boolean(waiting);
    tray.hidden = !waiting;
    if (!waiting) { tray.replaceChildren(); return; }
    const name = waiting.file.name || "the chosen image";
    const thumb = el("span", { class: "chat-attach-thumb" }, waiting.url ? el("img", { src: waiting.url, alt: "" }) : null);
    const remove = ui.button("Remove", () => { drop(); button.focus(); }, { kind: "linklike", size: "small" });
    remove.setAttribute("aria-label", "Remove the attached image");
    const again = waiting.turn ? ui.button("Try again", () => run(() => send(waiting.turn)), { size: "small" }) : null;
    tray.replaceChildren(...[thumb, el("span", { class: "chat-attach-text" },
      el("span", { class: "chat-attach-name" }, waiting.problem ? "Your post was made, but its image was not attached: " + waiting.problem
        : "Attached to your next post: " + name),
      waiting.problem ? null : el("span", { class: "g-muted" }, "Shown under your post. No character sees it and no model is sent it.")),
    again, remove].filter(Boolean));
    if (waiting.problem) tray.setAttribute("role", "alert"); else tray.removeAttribute("role");
  }
  input.addEventListener("change", () => {
    const file = input.files && input.files[0];
    input.value = "";
    if (!file) return;
    if (file.size > MAX_UPLOAD) { ctx.notify("An image is at most 32 MiB.", { bad: true }); return; }
    let url = null;
    try { url = URL.createObjectURL(file); } catch (error) { url = null; }
    if (waiting && waiting.url) URL.revokeObjectURL(waiting.url);
    waiting = { file, url, turn: null, problem: null };
    show();
  });
  if (pictures.writes) {
    field.classList.add("chat-attach-host");
    field.append(button, input, tray);
  }

  async function send(turn) {
    const held = waiting;
    if (!held) return;
    try {
      await ctx.api("media.upload", { scope: pictures.subject, media: await base64(held.file), note: null, origin: "attached to a post",
        subject: pictures.subject, slot: "post." + turn, expected: null });
    } catch (error) {
      if (error && error.reason === "left") return;
      // the post stands; the image waits here with the reason, to try again or to drop
      if (waiting === held) { waiting = { ...held, turn, problem: ui.errorText(error) }; show(); }
      return;
    }
    if (waiting === held) drop();
    await pictures.reload();
  }

  // After a post: the turn it made is the last one, and holds the post. A typed table request makes no turn, so
  // the image waits for the next post.
  function posted(scene, before) {
    const last = scene.turns[scene.turns.length - 1];
    if (!waiting || waiting.turn || scene.turns.length <= before || !last || !last.post) return undefined;
    return send(last.turn);
  }

  function under(turn, row) {
    const art = pictures.art();
    const found = art ? art.posts.find((item) => item.turn === turn.turn) : null;
    if (!found) return row;
    const body = row.querySelector("div.chat-said") || row;
    const frame = el("div", { class: "chat-attached" }, found.state === "shown" ? null
      : el("span", { class: "g-muted" }, "The image on this post cannot be shown (" + (found.reason || found.state) + ")."));
    if (found.state === "shown") {
      const zoom = zoomOf(found.crop);  // the framing chosen in the picker, as every other slot shows it
      ctx.picture(found.image).then((url) => {
        if (url) frame.prepend(el("span", { class: "chat-attached-frame" },
          el("img", { class: "chat-attached-img" + (zoom && zoom > 100 ? " " + zoomClass(zoom) : ""), src: url, alt: "Image attached to this post" })));
      }, () => {});
    }
    if (pictures.writes && ctx.has("media.place") && found.etag && !art.art.conflicts.includes(found.slot)) {
      // another picture or another framing, in the picture picker every other slot uses
      frame.append(el("button", { type: "button", class: "chat-tool", "data-local": "", "aria-label": "Change image on this post",
        onclick: () => run(async () => {
          const done = await openPicker(ctx, { subject: pictures.subject, slot: found.slot, art: art.art, target: "the image on this post" });
          if (done) await pictures.reload();
        }) }, "Change image"));
    }
    if (ctx.has("media.place") && found.etag && !art.art.conflicts.includes(found.slot)) {
      const remove = el("button", { type: "button", class: "chat-tool", "data-local": "", "aria-label": "Remove image from this post",
        onclick: () => run(async () => {
          await ctx.api("media.place", { subject: pictures.subject, slot: found.slot, mode: "hide", expected: found.etag });
          ctx.notify("Image removed from the post.");
          await pictures.reload();
        }) }, "Remove image");
      frame.append(remove);
    }
    body.append(frame);
    return row;
  }

  return { under, posted };
}
