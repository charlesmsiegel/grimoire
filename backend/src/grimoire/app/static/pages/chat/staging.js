// Authored, unsynced view metadata only. The authorized scene remains the sole transcript owner.
import { chatPictures } from "./pictures.js";
export function eligibleActors(turn) {
  if (!turn?.kept) return [];
  const actors = new Map();
  for (const item of [turn.post, ...turn.kept.lines].filter(Boolean))
    if (item.actor && !actors.has(item.actor))
      actors.set(item.actor, {
        actor: item.actor,
        name: item.speaker || (item.actor ? item.actor : "Narrator"),
      });
  return [...actors.values()];
}
export function actorArt(member) {
  return (member?.art?.slots || [])
    .filter(
      (s) =>
        s.state === "shown" &&
        s.image &&
        (s.slot.startsWith("expr.") || ["portrait", "avatar"].includes(s.slot)),
    )
    .sort(
      (a, b) =>
        Number(b.slot.startsWith("expr.")) - Number(a.slot.startsWith("expr.")),
    );
}
function valid(cues) {
  if (
    !cues ||
    typeof cues !== "object" ||
    Array.isArray(cues) ||
    Object.keys(cues).length > 100
  )
    return false;
  return Object.values(cues).every(
    (c) =>
      c &&
      typeof c === "object" &&
      typeof c.note === "string" &&
      c.note.length <= 1000 &&
      ["none", "fade"].includes(c.transition) &&
      typeof c.focus === "string" &&
      typeof c.background === "string" &&
      c.actors &&
      typeof c.actors === "object" &&
      !Array.isArray(c.actors) &&
      Object.values(c.actors).every(
        (a) =>
          a &&
          ["left", "center", "right"].includes(a.position) &&
          typeof a.art === "string",
      ),
  );
}
export function cueStore(chat, storage) {
  const key = "grimoire.vn.v1:" + chat;
  let cues = {},
    warning = "";
  try {
    storage = storage || globalThis.sessionStorage;
    const raw = storage.getItem(key);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (parsed.version !== 1 || !valid(parsed.cues)) throw Error("malformed");
      cues = parsed.cues;
    }
  } catch (e) {
    warning =
      e instanceof SyntaxError || e.message === "malformed"
        ? "Malformed local cues. Reset local cues to recover."
        : "Local cues not saved: storage unavailable.";
  }
  return {
    get cues() {
      return cues;
    },
    get warning() {
      return warning;
    },
    save(next) {
      if (!valid(next)) {
        warning = "Invalid cues: maximum 100 cues and 1000 note characters.";
        return false;
      }
      cues = next;
      try {
        storage.setItem(key, JSON.stringify({ version: 1, cues }));
        warning = "";
        return true;
      } catch {
        warning =
          "Local cues not saved: storage unavailable or full; changes remain in memory.";
        return false;
      }
    },
  };
}
const empty = () => ({
  actors: {},
  focus: "",
  background: "",
  transition: "none",
  note: "",
});
export function makeStaging(ctx, chat, { log }) {
  ctx.styles("staging.css");
  const { el } = ctx.ui;
  const store = cueStore(chat);
  let scene = null,
    selected = null,
    editing = false,
    revision = 0;
  const node = el("section", {
      class: "vn-controls",
      "aria-label": "VN presentation",
    }),
    body = el("div", {}),
    status = el("p", { role: "status" });
  const choice = (name, values, value, onchange) => {
    const select = el(
      "select",
      { name, "aria-label": name, onchange: () => onchange(select.value) },
      ...values.map(([v, label]) => el("option", { value: v }, label)),
    );
    select.value = value;
    return select;
  };
  const button = (text, fn) =>
    el("button", { type: "button", onclick: fn }, text);
  const mode = choice(
    "stage-mode",
    [
      ["transcript", "Transcript"],
      ["vn", "VN presentation"],
    ],
    "transcript",
    () => {
      log.hidden = mode.value === "vn";
      draw();
    },
  );
  node.append(
    el("label", {}, "Presentation ", mode),
    el(
      "p",
      {},
      "Local unsynced cues · current decorative art, not historical wardrobe",
    ),
    status,
    body,
  );
  const pictures = chatPictures(ctx, chat, () => {
    if (scene && !ctx.signal.aborted) draw();
  });
  function draw() {
    const epoch = ++revision;
    status.textContent = store.warning;
    body.replaceChildren();
    body.hidden = mode.value !== "vn";
    if (body.hidden || !scene) return;
    const turns = scene.turns.filter((t) => t.kept);
    if (selected === null && turns.length) selected = turns.at(-1).turn;
    const turn = turns.find((t) => t.turn === selected);
    body.append(
      choice(
        "Kept turn",
        [
          ...turns.map((t) => [t.turn, "Turn " + (t.number ?? t.turn)]),
          ...(!turn && selected
            ? [[selected, "Unavailable turn " + selected]]
            : []),
        ],
        selected || "",
        (v) => {
          selected = v;
          editing = false;
          draw();
        },
      ),
    );
    const stale = Object.keys(store.cues).filter(
      (id) => !turns.some((t) => t.turn === id),
    );
    if (stale.length)
      body.append(el("p", {}, "Unavailable cue turns: " + stale.join(", ")));
    if (!turn) {
      body.append(el("p", {}, "Selected kept turn unavailable."));
      body.append(
        button("Reset local cues", () => {
          store.save({});
          selected = null;
          draw();
        }),
      );
      return;
    }
    const actors = eligibleActors(turn),
      cue = store.cues[selected] || empty(),
      frame = el("div", {
        class: "vn-frame " + (cue.transition === "fade" ? "vn-fade" : ""),
      });
    const resolveImage = (image, holder, alt) => {
      const fallback = el(
        "span",
        {},
        alt + " · art unavailable (initial fallback)",
      );
      holder.append(fallback);
      Promise.resolve()
        .then(() => ctx.picture(image))
        .then(
          (url) => {
            if (url && epoch === revision && !ctx.signal.aborted) {
              const imageNode = el("img", {
                src: url,
                alt,
                onload: () => fallback.remove(),
                onerror: () => imageNode.remove(),
              });
              holder.append(imageNode);
            }
          },
          () => {},
        );
    };
    const backdrop = pictures.art()?.backdrop;
    const bg = backdrop?.state === "shown" ? backdrop : null;
    if (cue.background) {
      if (bg && bg.image === cue.background) {
        const holder = el("div", { class: "vn-background" });
        frame.append(holder);
        resolveImage(bg.image, holder, "Current decorative backdrop");
      } else frame.append(el("p", {}, "Background unavailable."));
    }
    for (const a of actors) {
      const art = actorArt(scene.cast.find((m) => m.actor === a.actor)),
        picked = cue.actors[a.actor],
        asset = picked?.art ? art.find((s) => s.slot === picked.art) : art[0];
      const holder = el(
        "figure",
        {
          class:
            "vn-actor vn-" +
            (picked?.position || "center") +
            (cue.focus === a.actor ? " vn-focus" : ""),
        },
        el("figcaption", {}, a.name + " · " + a.actor),
      );
      if (asset)
        resolveImage(asset.image, holder, a.name + " — current decorative art");
      else
        holder.append(
          el(
            "span",
            {},
            a.name.slice(0, 1) +
              " · " +
              (picked?.art ? "Art unavailable" : "Initial fallback"),
          ),
        );
      frame.append(holder);
    }
    for (const id of Object.keys(cue.actors))
      if (!actors.some((a) => a.actor === id))
        frame.append(el("p", {}, "Actor unavailable: " + id));
    if (cue.focus && !actors.some((a) => a.actor === cue.focus))
      frame.append(el("p", {}, "Focus actor unavailable: " + cue.focus));
    body.append(frame);
    for (const item of [turn.post, ...turn.kept.lines].filter(Boolean)) {
      const to = item.to?.length ? " to " + item.to.join(", ") : "";
      body.append(
        el(
          "article",
          { class: "vn-line" },
          el("strong", {}, (item.speaker || item.actor || "Narrator") + to),
          el("p", {}, item.text),
          item.withheld
            ? el("p", {}, "Only you know this.")
            : item.private
              ? el("p", {}, "Private.")
              : null,
        ),
      );
    }
    if (cue.note) body.append(el("p", {}, "Authored cue: " + cue.note));
    if (!editing) {
      body.append(
        button("Edit cue", () => {
          editing = true;
          draw();
          body.querySelector('[name="Cue note"]').focus();
        }),
        button("Reset cue", () => {
          const next = { ...store.cues };
          delete next[selected];
          store.save(next);
          draw();
        }),
        button("Reset local cues", () => {
          store.save({});
          draw();
        }),
      );
      return;
    }
    const draft = JSON.parse(JSON.stringify(cue)),
      editor = el("fieldset", {}, el("legend", {}, "Edit local cue"));
    for (const a of actors) {
      const state = draft.actors[a.actor] || { position: "center", art: "" };
      draft.actors[a.actor] = state;
      editor.append(
        el(
          "label",
          {},
          a.name + " · " + a.actor,
          choice(
            "Position " + a.actor,
            ["left", "center", "right"].map((x) => [x, x]),
            state.position,
            (v) => (state.position = v),
          ),
          choice(
            "Art " + a.actor,
            [
              ["", "Automatic fallback"],
              ...actorArt(scene.cast.find((m) => m.actor === a.actor)).map(
                (s) => [s.slot, s.slot],
              ),
              ...(state.art &&
              !actorArt(scene.cast.find((m) => m.actor === a.actor)).some(
                (s) => s.slot === state.art,
              )
                ? [[state.art, "Unavailable: " + state.art]]
                : []),
            ],
            state.art,
            (v) => (state.art = v),
          ),
        ),
      );
    }
    editor.append(
      choice(
        "Focus actor",
        [
          ["", "None"],
          ...actors.map((a) => [a.actor, a.name + " · " + a.actor]),
          ...(draft.focus && !actors.some((a) => a.actor === draft.focus)
            ? [[draft.focus, "Unavailable: " + draft.focus]]
            : []),
        ],
        draft.focus,
        (v) => (draft.focus = v),
      ),
      choice(
        "Transition",
        [
          ["none", "None"],
          ["fade", "Fade"],
        ],
        draft.transition,
        (v) => (draft.transition = v),
      ),
      choice(
        "Background",
        [
          ["", "None"],
          ...(bg ? [[bg.image, "Existing authorized backdrop"]] : []),
          ...(draft.background && bg?.image !== draft.background
            ? [[draft.background, "Unavailable backdrop"]]
            : []),
        ],
        draft.background,
        (v) => (draft.background = v),
      ),
    );
    const note = el("textarea", {
      name: "Cue note",
      "aria-label": "Cue note",
      maxlength: "1000",
    });
    note.value = draft.note;
    editor.append(
      el("label", {}, "Cue note", note),
      button("Save cue", () => {
        draft.note = note.value;
        store.save({ ...store.cues, [selected]: draft });
        editing = false;
        draw();
      }),
      button("Cancel cue", () => {
        editing = false;
        draw();
      }),
    );
    body.append(editor);
  }
  return {
    node,
    update(next) {
      scene = next;
      draw();
    },
  };
}
