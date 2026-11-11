// The Import page's card form: a card file (a PNG card with its data embedded, or a JSON document) is read by
// the app (`imports.card_inspect`, which opens no library and proposes nothing), shown as what was found, and
// then previewed (`imports.card_preview`) into the page's shared review. A PNG card is its own portrait, shown
// here from the dropped file itself; the lorebook a card embeds is listed and can be left out before the preview,
// and separate lorebook files can be added beside it. Several files, or a dropped folder, go to batch.js.
//
// Every value shown comes from a card or a file name and is only ever set as text, never as markup.

import { batchFlow, droppedFiles } from "./batch.js";

const PNG_SIGNATURE = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];
export const CARD_ACCEPT = ".png,image/png,.json,application/json";
const LOREBOOK_ACCEPT = ".json,application/json";
const PORTRAIT_ACCEPT = "image/png,image/jpeg,image/webp";

export const CARD_FORMATS = { chara_card_v3: "Character Card V3", chara_card_v2: "Character Card V2", lorebook_v3: "Lorebook V3",
  "grimoire.scenario-card/1": "Scenario card" };
const KINDS = { character: "A character", scenario: "A scenario", lorebook: "A lorebook" };
// what a file that cannot be imported is, said plainly; the app's own reason follows it
const UNREADABLE = {
  invalid_png: "This PNG is damaged or incomplete, so no card was read from it.",
  no_embedded_card: "This picture holds no character card: a PNG card carries its data inside the file, and this one has none.",
  invalid_json: "This file is not a JSON document.",
  invalid_card: "This card is missing something every card needs, such as a name.",
  unsupported_format: "This is not a card format this app reads.",
  too_large: "This file is larger than a card may be.",
  invalid_field: "This file could not be sent as a card.",
};

export function isPng(bytes) {
  return bytes.length >= PNG_SIGNATURE.length && PNG_SIGNATURE.every((byte, index) => bytes[index] === byte);
}

// The app's own reason for a refusal, without the details it folds onto the end of the message.
function why(error) {
  const text = String((error && error.message) || "").replace(/\s*\((?:\{.*\}|\[.*\])\)\s*$/s, "");
  return text ? "The app says: " + text + "." : "";
}

const entries = (count) => count + (count === 1 ? " entry" : " entries");

// env: what the page shares with its forms (see page.js).
export function cardForm(env) {
  const { ctx, ui, el, capabilities, stage, show, review, facts, rows, present, dropZone, startOver, bytesOf, base64, sizeOf, pictures, served } = env;
  const limits = (capabilities.imports && capabilities.imports.card) || {};
  const maxBooks = limits.lorebook_files_max || 1;
  const batch = served(["imports.card_batch_scan", "imports.card_batch_preview"]) ? batchFlow(env, UNREADABLE) : null;

  // Files chosen beside the card (a portrait, lorebooks): `onfiles` gets the ones chosen.
  function chooser(label, accept, onfiles, many = false) {
    const input = el("input", { type: "file", accept, hidden: true, multiple: many, "aria-label": label });
    input.addEventListener("change", () => { const files = [...(input.files || [])]; input.value = ""; if (files.length) ctx.act(() => onfiles(files)); });
    return input;
  }

  function unreadable(file, bytes, error) {
    show();
    stage.replaceChildren(ui.card({ tag: "section", label: "What was found" },
      ui.eyebrow("Not a card this app can import"),
      el("h2", { class: "import-found-name" }, file.name || "This file"),
      el("p", { class: "import-refusal" }, UNREADABLE[error.reason] || "This file cannot be imported."),
      el("p", { class: "g-muted import-refusal-why" }, why(error)),
      facts([["Size", sizeOf(bytes.length)]]),
      el("div", {}, ui.button("Use a different file", startOver, { kind: "linklike" }))));
    const again = stage.querySelector("button");
    if (again) again.focus();
  }

  async function found(file) {
    const bytes = await bytesOf(file);
    const png = isPng(bytes);
    const limit = png ? limits.png_max_bytes : limits.max_bytes;
    if (limit && bytes.length > limit) {
      throw Object.assign(new Error((file.name || "The card") + " is larger than a " + (png ? "PNG card" : "card") + " may be (" + sizeOf(limit) + ")"),
        { reason: "too_large" });
    }
    const card = base64(bytes);
    let read;
    try { read = await ctx.api("imports.card_inspect", { card }); }
    catch (error) {
      // a file the app read and refused is shown as what it is; anything else (no answer, a server fault) is an error
      if (error.status !== 400) throw error;
      unreadable(file, bytes, error);
      return;
    }
    pictures.clear();
    const name = read.name || file.name || "Card";
    const isBook = read.kind === "lorebook";
    const state = { portrait: null, embedded: Boolean(read.lorebook) && !isBook, lorebooks: [] };
    const scenario = el("input", { type: "checkbox" });
    const canBeScenario = read.format === "chara_card_v3" || read.format === "chara_card_v2";

    // -- the portrait: the PNG itself, or one added beside a JSON card ---------------------------------------
    const portraitInput = chooser("Portrait file", PORTRAIT_ACCEPT, async ([chosen]) => {
      state.portrait = { file: chosen, bytes: await bytesOf(chosen) };
      drawPortrait();
    });
    const portrait = el("div", { class: "import-portrait" });
    const picture = (data, type, alt) => el("img", { class: "import-portrait-img", src: pictures.of(data, type), alt });
    function drawPortrait() {
      if (read.portrait) {
        portrait.replaceChildren(picture(bytes, "image/png", "The picture in " + (file.name || "the PNG card")),
          el("span", { class: "g-muted import-portrait-note" }, "Portrait: the PNG itself"));
      } else if (state.portrait) {
        portrait.replaceChildren(portraitInput, picture(state.portrait.bytes, state.portrait.file.type || "image/png", "The portrait you added"),
          el("span", { class: "g-muted import-portrait-note" }, state.portrait.file.name || "Added portrait"),
          ui.button("Remove", () => { state.portrait = null; drawPortrait(); }, { kind: "linklike", size: "small" }));
      } else {
        portrait.replaceChildren(portraitInput,
          el("button", { type: "button", class: "import-portrait-add", onclick: () => portraitInput.click() }, "Add a portrait (optional)"));
      }
    }

    // -- the lorebooks: the one the card embeds, and files added beside it --------------------------------------
    const lorebookInput = chooser("Lorebook file", LOREBOOK_ACCEPT, async (chosen) => {
      if (state.lorebooks.length + chosen.length > maxBooks) {
        throw Object.assign(new Error("A card takes at most " + maxBooks + " separate lorebook files."), { reason: "too_many" });
      }
      const added = [];
      for (const file of chosen) added.push({ file, bytes: await bytesOf(file) });
      state.lorebooks.push(...added);
      drawLorebooks();
    }, maxBooks > 1);
    const lorebooks = el("section", { class: "g-card import-lorebooks", "aria-label": "Lorebooks" });
    const bookRow = (title, detail, origin, remove) => el("li", { class: "import-book" },
      el("span", { class: "import-book-text" }, el("span", { class: "import-book-name" }, title), el("span", { class: "g-muted import-book-detail" }, detail)),
      ui.pill(origin, { kind: origin === "embedded in the card" ? "accent" : "" }),
      ui.button("Remove", remove, { kind: "linklike", size: "small" }));
    function drawLorebooks() {
      const items = [];
      if (state.embedded) {
        items.push(bookRow(read.lorebook.name, entries(read.lorebook.entries), "embedded in the card",
          () => { state.embedded = false; drawLorebooks(); }));
      }
      for (const book of state.lorebooks) {
        items.push(bookRow(book.file.name || "A lorebook file", "A separate lorebook, " + sizeOf(book.bytes.length),
          "from a file", () => { state.lorebooks = state.lorebooks.filter((other) => other !== book); drawLorebooks(); }));
      }
      const count = items.length;
      lorebooks.replaceChildren(...present(lorebookInput,
        el("div", { class: "import-book-head" }, el("h2", { class: "g-heading" }, "Lorebooks"),
          el("span", { class: "g-muted import-book-count" }, count ? count + " attached" : "None attached")),
        count ? el("ul", { class: "import-books" }, items)
          : el("p", { class: "g-muted import-book-none" }, "No lorebook attached. The card imports on its own."),
        el("div", { class: "import-book-actions" },
          state.lorebooks.length >= maxBooks ? null : ui.button("Add a lorebook file", () => lorebookInput.click(), { kind: "dashed", size: "small" }),
          read.lorebook && !state.embedded ? ui.button("Put back the card's own lorebook", () => { state.embedded = true; drawLorebooks(); },
            { kind: "linklike", size: "small" }) : null),
        el("p", { class: "g-muted import-book-help" }, (read.lorebook
          ? "The card's own lorebook is imported with it unless you remove it here. Each lorebook becomes its own entry in the card's source."
          : "Each lorebook file becomes its own entry in the card's source.")
          + " A lorebook already in your library is not attached here: pick it when you start a chat.")));
    }

    async function preview() {
      // the same card as one already here updates that card (the app decides by the card's own bytes, never its name)
      const args = { card, origin: file.name, as_scenario: scenario.checked, update_same_card: true };
      if (state.portrait) args.portrait = base64(state.portrait.bytes);
      if (state.lorebooks.length) args.lorebooks = state.lorebooks.map((book) => base64(book.bytes));
      if (read.lorebook && !isBook && !state.embedded) args.embedded_lorebook = false;
      const proposal = await ctx.api("imports.card_preview", args);
      const r = proposal.report;
      const books = present(state.embedded ? read.lorebook.name + " (embedded in the card)" : null,
        state.lorebooks.map((book) => (book.file.name || "a lorebook file") + " (from a file)"));
      show(review(proposal, {
        title: r.status === "new" ? name + " becomes a new " + r.kind : name + ": " + r.kind + ", " + r.status,
        summary: facts([["Status", r.status], ["Destination", r.source], ["Format", CARD_FORMATS[r.format] || r.format], ["Becomes", r.kind],
          isBook ? null : ["Lorebooks", books.length ? books.join(", ") : "None"],
          isBook ? null : ["Portrait", read.portrait && !state.portrait ? "The PNG itself" : state.portrait ? "The picture you added" : "None"]].filter(Boolean)),
        notes: present(rows("Not carried over", r.losses), rows("Collisions", r.collisions),
          r.suggestions ? el("details", { class: "import-rows" }, el("summary", {}, "Name suggestions (shown, never linked)"),
            facts(Object.entries(r.suggestions))) : null),
      }));
    }

    drawPortrait();
    if (!isBook) drawLorebooks();
    show();
    stage.replaceChildren(el("div", { class: "g-stack" },
      el("div", { class: "import-found" },
        el("section", { class: "g-card import-found-card", "aria-label": "What was found" },
          isBook ? null : portrait,
          el("div", { class: "import-found-text" },
            ui.eyebrow(read.container === "png" ? "PNG card with its data embedded" : "JSON document", { accent: true }),
            el("h2", { class: "import-found-name" }, name),
            el("div", { class: "import-path" }, file.name || ""),
            facts([["Format", CARD_FORMATS[read.format] || read.format], ["Becomes", KINDS[read.kind] || read.kind],
              isBook && read.lorebook ? ["Holds", entries(read.lorebook.entries)] : null,
              ["Size", sizeOf(bytes.length)],
              read.losses ? ["Not carried over", read.losses + (read.losses === 1 ? " field: the preview lists it" : " fields: the preview lists them")] : null,
            ].filter(Boolean)),
            el("div", {}, ui.button("Use a different file", startOver, { kind: "linklike" })))),
        isBook ? null : lorebooks),
      el("div", { class: "import-next" },
        canBeScenario ? el("label", { class: "import-check" }, scenario, "Import as a scenario") : el("span", {}),
        el("span", { class: "import-next-end" },
          el("span", { class: "g-muted" }, "Nothing is written until you approve the preview."),
          ui.button("Preview import", () => ctx.act(preview), { kind: "primary" })))));
    const go = [...stage.querySelectorAll("button")].find((node) => node.textContent === "Preview import");
    if (go) go.focus();
  }

  const hint = "A PNG card with its data embedded fills in the card and the portrait at once. A Card V3 or V2 JSON, a scenario card or a "
    + "lorebook works too. Nothing is written until you approve the preview.";
  if (!batch) return [dropZone({ title: "Drop a card here", label: "Card file", accept: CARD_ACCEPT, onfile: found, hint })];
  // one file is read on its own; several, or a folder, are listed together and reviewed as one
  async function taken(reading) {
    const { files, skipped } = await reading;
    if (!files.length) throw Object.assign(new Error("No card files (.png or .json) were found in what you dropped."), { reason: "no_cards" });
    if (files.length === 1 && !skipped) await found(files[0].file);
    else await batch.open({ files, skipped });
  }
  const maxCards = (limits.batch && limits.batch.max_cards) || 50;
  return [dropZone({ title: "Drop a card, or a whole folder of them", label: "Card files", accept: CARD_ACCEPT, choose: "Choose files", hint,
    // read at once, inside the event: a drop's folder entries cannot be read later
    ondrop: (transfer) => taken(droppedFiles(transfer, maxCards)) })];
}
