// What one chat shows beyond its cast (docs/MEDIA_SYNC_UI.md): its backdrop and the pictures on its posts, as
// media.chat answers them. One read per chat page, shared by ./backdrop.js and ./attach.js, read again after
// either writes. These pictures sit beside the transcript: nothing here reads or changes a turn.
//
// chatPictures(ctx, chat, redraw) -> { served, writes, subject, backdropSlot, art(), reload() }
//   served        whether the app serves media.chat at all (else nothing of this is shown)
//   writes        whether it also serves what choosing or attaching a picture needs
//   subject       the chat's campaign as a whole ("campaign:<id>"): whose slots the pictures are in
//   backdropSlot  the slot of this chat's backdrop there
//   art()         the last answer, or null before the first one and where the read failed
//   reload()      read again, then redraw()

import { linkStyles as pickerStyles } from "../art/picker.js";

const shared = new WeakMap();  // ctx -> the one loader of its page

export function chatPictures(ctx, chat, redraw) {
  const had = shared.get(ctx);
  if (had) return had;
  ctx.styles("pictures.css");
  pickerStyles();  // the zoom steps a framed picture is shown with are the picker's
  const subject = chat.split("#")[0];
  const id = chat.slice(chat.indexOf("#chat/") + 6);
  let answer = null;
  const pictures = {
    served: ctx.has("media.chat"), subject, backdropSlot: "backdrop." + id,
    writes: ctx.has("media.chat") && ctx.has("media.art") && ctx.has("media.gallery") && ctx.has("media.upload"),
    art: () => answer,
    async reload() {
      if (!pictures.served) return;
      try { answer = await ctx.api("media.chat", { chat }); } catch (error) {
        if (error && error.reason === "left") return;
        answer = null;  // a chat whose pictures cannot be read shows none
      }
      redraw();
    },
  };
  shared.set(ctx, pictures);
  pictures.reload();
  return pictures;
}
