// The VN presentation of a chat (072): the same kept turns as the transcript, staged from the authored cue
// timeline in cues.js. Authored, unsynced view metadata only: the authorized scene remains the sole transcript
// owner, switching to the transcript and back changes no text, and nothing here posts, keeps or writes a line.
//
// Art is the actor's current shown art for the slot a cue names (an outfit's expression sprite, else the
// portrait or avatar), never a hidden or unavailable slot and never another actor's; with none, an initial
// stands in. Transitions play only when the reader arrives at a turn (stepping, choosing it), not on every
// redraw, and prefers-reduced-motion turns them off; the stage directions say the same in words either way.
import { chatPictures } from "./pictures.js";
import { POSITIONS, actorArt, blankCue, cueStore, outfitOf, placeName, presentActors, speakerLabels, timeline, trimCue } from "./cues.js";

export { actorArt, cueStore, eligibleActors, presentActors, timeline } from "./cues.js";

const COLUMN = Object.fromEntries(POSITIONS.map((p, i) => [p, i]));

export function makeStaging(ctx, chat, { log }) {
  ctx.styles("staging.css");
  const { el } = ctx.ui;
  const store = cueStore(chat);
  let scene = null, selected = null, editing = false, editDraft = null, revision = 0, shown = null;
  const node = el("section", { class: "vn-controls", "aria-label": "VN presentation" }),
    body = el("div", {}),
    status = el("p", { role: "status" }),
    // One live region for the whole presentation, outside the redrawn body: a region inserted already filled is
    // not announced, and a redraw must not announce again. It is set only when the reader arrives at a turn.
    announce = el("p", { class: "vn-announce", "aria-live": "polite" });
  const choice = (name, values, value, onchange) => {
    const select = el("select", { name, "aria-label": name, "data-stage-focus": name, onchange: () => onchange(select.value) },
      ...values.map(([v, label]) => el("option", { value: v }, label)));
    select.value = value;
    return select;
  };
  const button = (text, fn, attrs = {}) => el("button", { type: "button", "data-stage-focus": text, onclick: fn, ...attrs }, text);
  const mode = choice("stage-mode", [["transcript", "Transcript"], ["vn", "VN presentation"]], "transcript", () => {
    log.hidden = mode.value === "vn";
    announce.textContent = ""; // nothing stale for a reading cursor once the transcript is back
    shown = null; // coming back to the stage arrives at its turn again
    draw();
  });
  node.append(
    el("label", {}, "Presentation ", mode),
    el("p", {}, "Local unsynced cues · current decorative art, not historical wardrobe"),
    status,
    announce,
    body,
  );
  // Another tab of this device changed this chat's cues: take them up (cues.js applies each change to what
  // is stored, so neither tab's cues are lost).
  try {
    globalThis.addEventListener?.("storage", () => {
      if (ctx.signal.aborted) return;
      store.reload();
      if (scene && !editing) draw();
    }, { signal: ctx.signal });
  } catch { /* no window events: this tab still applies its own changes to what is stored */ }
  const pictures = chatPictures(ctx, chat, () => {
    if (scene && !ctx.signal.aborted) draw();
  });
  const member = (actor) => scene.cast.find((m) => m.actor === actor);

  function draw(nextFocus) {
    const active = document.activeElement;
    const focusKey = nextFocus || (body.contains(active) ? active.getAttribute("data-stage-focus") : null);
    drawContent();
    if (focusKey) {
      const target = [...body.querySelectorAll("[data-stage-focus]")].find((item) => item.getAttribute("data-stage-focus") === focusKey);
      // A step to the first or last turn disables the button that made it: keep the reader in the turn
      // controls rather than on Presentation, where an arrow key would switch the view.
      const turnChoice = body.querySelector('[name="Kept turn"]');
      (target && !target.disabled ? target : turnChoice && focusKey.endsWith(" turn") ? turnChoice : mode).focus({ preventScroll: true });
    }
  }
  function go(turn) {
    selected = turn;
    editing = false;
    draw();
  }

  function drawContent() {
    const previousNote = body.querySelector('[name="Cue note"]');
    if (editing && editDraft && previousNote) editDraft.note = previousNote.value;
    const epoch = ++revision;
    status.textContent = store.warning;
    body.replaceChildren();
    body.hidden = mode.value !== "vn";
    if (body.hidden || !scene) return;
    const turns = scene.turns.filter((t) => t.kept);
    if (selected === null && turns.length) selected = turns.at(-1).turn;
    const index = turns.findIndex((t) => t.turn === selected);
    const turn = turns[index];
    body.append(
      el("div", { class: "vn-nav" },
        button("Previous turn", () => go(turns[index - 1].turn), { disabled: index <= 0 }),
        choice("Kept turn",
          [...turns.map((t) => [t.turn, "Turn " + (t.number ?? t.turn) + (store.cues[t.turn] ? " · cue" : "")]),
            ...(!turn && selected ? [[selected, "Unavailable turn " + selected]] : [])],
          selected || "", go),
        button("Next turn", () => go(turns[index + 1].turn), { disabled: index < 0 || index >= turns.length - 1 })),
    );
    const stale = Object.keys(store.cues).filter((id) => !turns.some((t) => t.turn === id));
    if (stale.length) body.append(el("p", {}, "Unavailable cue turns: " + stale.join(", ")));
    if (!turn) {
      shown = null;
      announce.textContent = "";
      body.append(el("p", {}, "Selected kept turn unavailable."));
      body.append(button("Reset local cues", () => { store.reset(); selected = null; draw(); }));
      return;
    }
    const staged = timeline(turns, store.cues);
    const entry = staged[index];
    const arrived = shown !== selected;
    shown = selected;
    if (arrived) announce.textContent = "Turn " + (turn.number ?? turn.turn) + ". " + entry.directions.join(" ");
    body.append(drawFrame(entry, arrived, epoch));
    body.append(
      el("section", { class: "vn-directions", "aria-label": "Stage directions" },
        el("ul", {}, ...(entry.directions.length ? entry.directions : ["No change on stage."]).map((d) => el("li", {}, d))),
        ...entry.unavailable.map((u) => el("p", {}, "Cue not honoured: " + u + "."))),
    );
    for (const item of [...(turn.posts || [turn.post]), ...turn.kept.lines].filter(Boolean)) {
      const to = item.to?.length ? " to " + item.to.join(", ") : "";
      body.append(el("article", { class: "vn-line" },
        el("strong", {}, (item.speaker || item.actor || "Narrator") + to),
        el("p", {}, item.text),
        item.withheld ? el("p", {}, "Only you know this.") : item.private ? el("p", {}, "Private.") : null));
    }
    if (entry.note) body.append(el("p", {}, "Authored cue: " + entry.note));
    body.append(drawTimeline(staged));
    if (!editing) {
      body.append(
        button("Edit cue", () => {
          editDraft = JSON.parse(JSON.stringify(store.cues[selected] || blankCue()));
          editing = true;
          draw();
          body.querySelector('[name="Cue note"]').focus();
        }),
        button("Reset cue", () => {
          store.remove(selected);
          draw();
        }),
        button("Reset local cues", () => { store.reset(); draw(); }),
      );
      return;
    }
    body.append(drawEditor(turn, entry));
  }

  function resolveImage(image, holder, alt, epoch) {
    const fallback = el("span", {}, alt + " · art unavailable (initial fallback)");
    holder.append(fallback);
    Promise.resolve()
      .then(() => ctx.picture(image))
      .then((url) => {
        if (url && epoch === revision && !ctx.signal.aborted) {
          const imageNode = el("img", { src: url, alt, onload: () => fallback.remove(), onerror: () => imageNode.remove() });
          holder.append(imageNode);
        }
      }, () => {});
  }

  function drawFrame(entry, arrived, epoch) {
    const play = arrived ? entry.transition : "none";
    const frame = el("div", { class: "vn-frame" + (play === "fade" ? " vn-fade" : "") + (entry.focus.actor ? " vn-has-focus" : "") });
    const backdrop = pictures.art()?.backdrop;
    const bg = backdrop?.state === "shown" ? backdrop : null;
    if (entry.background) {
      if (bg && bg.image === entry.background) {
        const holder = el("div", { class: "vn-background" });
        frame.append(holder);
        resolveImage(bg.image, holder, "Current decorative backdrop", epoch);
      } else frame.append(el("p", {}, "Background unavailable."));
    }
    if (!entry.actors.length) frame.append(el("p", { class: "vn-empty" }, "Nobody is staged at this turn."));
    for (const a of entry.actors) {
      const art = actorArt(member(a.actor));
      const asset = a.art ? art.find((s) => s.slot === a.art) : art[0];
      const focused = entry.focus.actor === a.actor;
      const motion = play === "move" && a.from && a.from !== a.position
        ? " vn-move vn-shift-" + (COLUMN[a.from] - COLUMN[a.position] > 0 ? "p" : "m") + Math.abs(COLUMN[a.from] - COLUMN[a.position])
        : (play === "fade" || play === "move") && a.entered ? " vn-enter" : "";
      const outfit = outfitOf(asset?.slot);
      const holder = el("figure", {
        class: "vn-actor vn-" + a.position + (focused ? " vn-focus" : "") + motion,
        "aria-current": focused ? "true" : null,
        "data-actor": a.actor,
      }, el("figcaption", {}, a.name + " · " + a.actor + (outfit ? " · " + outfit + " outfit" : "") +
        (focused ? " · focus (" + entry.focus.source + ")" : "") + (a.speaking ? "" : " · silent")));
      if (asset) resolveImage(asset.image, holder, a.name + " — current decorative art", epoch);
      else holder.append(el("span", {}, a.name.slice(0, 1) + " · " + (a.art ? "Art unavailable" : "Initial fallback")));
      frame.append(holder);
    }
    return frame;
  }

  function drawTimeline(staged) {
    const authored = staged.filter((e) => e.cue);
    return el("section", { class: "vn-timeline", "aria-label": "Cue timeline" },
      el("p", {}, authored.length ? "Cue timeline" : "Cue timeline: no cues yet; speakers are staged automatically."),
      authored.length
        ? el("ol", {}, ...authored.map((e) => el("li", { "aria-current": e.turn === selected ? "true" : null },
          button("Go to turn " + (e.number ?? e.turn), () => go(e.turn)),
          " " + summary(e))))
        : null);
  }
  function summary(entry) {
    const cue = entry.cue, parts = [];
    const moves = Object.keys(cue.actors).length;
    if (moves) parts.push(moves + (moves === 1 ? " blocking change" : " blocking changes"));
    if (cue.focus && cue.focus !== "auto") parts.push("focus");
    if (cue.background !== "inherit") parts.push("background");
    if (cue.transition !== "none") parts.push(cue.transition);
    if (cue.note) parts.push("“" + (cue.note.length > 60 ? cue.note.slice(0, 60) + "…" : cue.note) + "”");
    return parts.join(" · ") || "no change";
  }

  function drawEditor(turn, entry) {
    const draft = editDraft || (editDraft = JSON.parse(JSON.stringify(store.cues[selected] || blankCue())));
    const editor = el("fieldset", {}, el("legend", {}, "Edit local cue"));
    const present = presentActors(turn, speakerLabels(scene.turns.filter((t) => t.kept)));
    const carried = new Map(Object.entries(entry.carried));
    for (const a of present) {
      const state = draft.actors[a.actor] || { position: "", art: "" };
      draft.actors[a.actor] = state;
      const was = carried.get(a.actor);
      const art = actorArt(member(a.actor));
      editor.append(el("label", {}, a.name + " · " + a.actor + (a.speaking ? "" : " · silent"),
        choice("Position " + a.actor,
          [["", "Unchanged (" + (was ? (was.position === "off" ? "off stage" : placeName(was.position)) : a.speaking ? "placed automatically" : "not staged") + ")"],
            ...POSITIONS.map((x) => [x, x]), ["off", "Off stage"]],
          state.position, (v) => (state.position = v)),
        choice("Art " + a.actor,
          [["", was?.art ? "Unchanged (" + was.art + ")" : "Automatic fallback"],
            ...art.map((s) => [s.slot, s.slot]),
            ...(state.art && !art.some((s) => s.slot === state.art) ? [[state.art, "Unavailable: " + state.art]] : [])],
          state.art, (v) => (state.art = v))));
    }
    for (const id of Object.keys(draft.actors).filter((id) => !present.some((a) => a.actor === id)))
      editor.append(el("p", {}, "Not present at this turn: " + id + " (kept in the cue, not staged)"));
    const backdrop = pictures.art()?.backdrop;
    const bg = backdrop?.state === "shown" ? backdrop : null;
    editor.append(
      choice("Focus actor",
        [["auto", "Automatic (last speaker)"], ["none", "None"], ...present.map((a) => [a.actor, a.name + " · " + a.actor]),
          ...(draft.focus && !["auto", "none", ""].includes(draft.focus) && !present.some((a) => a.actor === draft.focus)
            ? [[draft.focus, "Unavailable: " + draft.focus]] : [])],
        draft.focus === "" ? "none" : draft.focus, (v) => (draft.focus = v)),
      choice("Transition", [["none", "None"], ["fade", "Fade"], ["move", "Move (blocking slides)"]], draft.transition,
        (v) => (draft.transition = v)),
      choice("Background",
        [["inherit", "Unchanged"], ["", "None"], ...(bg ? [[bg.image, "Existing authorized backdrop"]] : []),
          ...(draft.background && draft.background !== "inherit" && bg?.image !== draft.background ? [[draft.background, "Unavailable backdrop"]] : [])],
        draft.background, (v) => (draft.background = v)),
    );
    const note = el("textarea", { name: "Cue note", "data-stage-focus": "Cue note", "aria-label": "Cue note", maxlength: "1000" });
    note.value = draft.note;
    editor.append(
      el("label", {}, "Cue note", note),
      button("Save cue", () => {
        draft.note = note.value;
        // A refused cue (too many, or stored cues this build cannot change) keeps the editor and its draft open;
        // one kept only in memory is applied, with the warning saying so.
        if (store.put(selected, trimCue(draft)) === false) {
          draw("Save cue");
          return;
        }
        editing = false;
        draw("Edit cue");
      }),
      button("Cancel cue", () => {
        editing = false;
        draw("Edit cue");
      }),
    );
    return editor;
  }

  return {
    node,
    update(next) {
      scene = next;
      draw();
    },
  };
}
