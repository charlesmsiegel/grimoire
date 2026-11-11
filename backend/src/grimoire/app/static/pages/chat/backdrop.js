// The chat's backdrop: a picture behind the scene bar, and the "Backdrop" button that chooses it in the picture
// picker (pages/art/picker.js). The backdrop is a picture of the chat's campaign in the slot "backdrop.<chat id>"
// (docs/MEDIA_SYNC_UI.md); a chat played from a scenario is also offered that scenario's backdrops, which
// media.backdrop_from makes the campaign's own. The scene bar keeps its white text over a dark scrim.
//
// makeBackdrop(ctx, chat, { run, redraw }) -> { draw(sceneBar) }
//   draw(sceneBar)  call after the scene bar's own children are set: adds the picture and the button to it

import { openPicker, zoomClass, zoomOf } from "../art/picker.js";
import { chatPictures } from "./pictures.js";

export function makeBackdrop(ctx, chat, { run, redraw }) {
  const { el } = ctx.ui;
  const pictures = chatPictures(ctx, chat, redraw);
  if (!pictures.served) return { draw: () => {} };

  const choose = () => run(async () => {
    const art = pictures.art();
    if (!art) return;
    const offered = art.offered.length && ctx.has("media.backdrop_from") ? {
      pictures: art.offered.map((item) => ({ ref: item.image, label: item.provenance.note || "Scenario backdrop", from: item.from })),
      place: (image, frame, expected) => ctx.api("media.backdrop_from", { chat, image, expected, ...frame }),
    } : null;
    const done = await openPicker(ctx, { subject: pictures.subject, slot: pictures.backdropSlot, art: art.art, aspect: "16/5",
      target: "the backdrop of this chat", use: "Shown as a band behind the scene bar", offered });
    if (done) await pictures.reload();
  });

  function draw(sceneBar) {
    const art = pictures.art();
    const shown = art && art.backdrop.state === "shown" ? art.backdrop : null;
    sceneBar.classList.toggle("chat-backdrop-on", Boolean(shown));
    if (shown) {
      const zoom = zoomOf(shown.crop);
      const holder = el("span", { class: "chat-backdrop", "aria-hidden": "true" });
      sceneBar.prepend(holder);
      ctx.picture(shown.image).then((url) => {
        if (url) holder.replaceChildren(el("img", { class: zoom && zoom > 100 ? zoomClass(zoom) : undefined, src: url, alt: "" }),
          el("span", { class: "chat-backdrop-scrim" }));
      }, () => {});
    }
    if (art && pictures.writes) {
      const button = el("button", { type: "button", class: "chat-scene-button chat-backdrop-button", onclick: choose,
        "data-focus-key": "chat-backdrop", "aria-label": shown ? "Change the backdrop of this chat" : "Choose a backdrop for this chat" },
      "Backdrop");
      // before the bar's last control (the side panel's own button), which stays last
      const nodes = [...sceneBar.childNodes];
      const last = nodes.length > 1 ? nodes.pop() : null;
      sceneBar.replaceChildren(...nodes, button, ...(last ? [last] : []));
    }
  }
  return { draw };
}
