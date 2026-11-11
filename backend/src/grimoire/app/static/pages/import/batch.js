// The Import page's batch of cards: several files (or a dropped folder of them) are listed by the app
// (`imports.card_batch_scan`, which plans each card and writes nothing) as a table of what was found and what
// importing each would do, and the ticked ones are reviewed together (`imports.card_batch_preview`): one proposal,
// every file of every card under one digest, approved or rejected as a whole in the page's shared review.
//
// The result of each row is the app's: a card is an update only when the library holds exactly that card, never
// because a name matches. A file that cannot be imported stays in the table with its reason and is left out.
//
// Every value shown comes from a card or a file name and is only ever set as text, never as markup.

const CARD_FILE = /\.(png|json)$/i;
const MAX_ENTRIES = 5000;  // entries of a dropped folder looked at before giving up
const MAX_DEPTH = 8;

const KINDS = { character: "Character", scenario: "Scenario", lorebook: "Lorebook" };

const plural = (count, one, many) => count + " " + (count === 1 ? one : many);

function refuse(reason, message) {
  return Object.assign(new Error(message), { reason });
}

const sentence = (text) => { const said = String(text || "").trim(); return said ? said[0].toUpperCase() + said.slice(1) + (/[.!?]$/.test(said) ? "" : ".") : ""; };

// What a row's result is called. `refusals` says a refusal reason plainly (card.js's own words).
export function resultText(row, refusals = {}) {
  if (!row.importable) return "Cannot be imported: " + (refusals[row.refused.reason] || sentence(row.refused.message));
  const result = row.result;
  if (result.status === "unchanged") return "Already in your library";
  if (result.status === "update") return result.from && result.to ? "Updates " + result.from + " to " + result.to : "Updates the card you have";
  return result.same_name ? "Same name as another card" : "New";
}

// The files of a drop: the files themselves, and for a dropped folder every card file under it (by its name:
// .png or .json), each with the path it was found at. Entries are read from the event before anything is awaited,
// as browsers require. `limit` bounds how many card files are gathered.
export async function droppedFiles(transfer, limit) {
  const items = transfer.items ? [...transfer.items] : [];
  const entries = items.map((item) => (typeof item.webkitGetAsEntry === "function" ? item.webkitGetAsEntry() : null));
  if (!entries.some((entry) => entry && entry.isDirectory)) {
    return { files: [...(transfer.files || [])].map((file) => ({ file, label: file.name })), skipped: 0 };
  }
  const found = [];
  let skipped = 0;
  let seen = 0;
  const fileOf = (entry) => new Promise((resolve, reject) => entry.file(resolve, reject));
  const label = (entry) => String(entry.fullPath || entry.name || "").replace(/^\/+/, "") || entry.name;
  async function add(entry) {
    if (found.length >= limit) {
      throw refuse("too_many_cards", "This folder holds more than " + limit + " card files, and one batch takes at most " + limit
        + ". Drop fewer at a time.");
    }
    found.push({ file: await fileOf(entry), label: label(entry) });
  }
  async function walk(entry, depth) {
    seen += 1;
    if (seen > MAX_ENTRIES) throw refuse("too_many_cards", "This folder holds too many files to look through. Drop a smaller folder.");
    if (entry.isFile) {
      if (CARD_FILE.test(entry.name)) await add(entry); else skipped += 1;
      return;
    }
    if (!entry.isDirectory || depth >= MAX_DEPTH) { skipped += 1; return; }
    const reader = entry.createReader();
    const children = [];
    for (;;) {  // a reader answers a folder in parts: read until it answers none
      const part = await new Promise((resolve, reject) => reader.readEntries(resolve, reject));
      if (!part.length) break;
      children.push(...part);
    }
    children.sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
    for (const child of children) await walk(child, depth + 1);
  }
  for (const entry of entries) {
    if (!entry) continue;
    if (entry.isFile) await add(entry);  // a file dropped beside the folder is taken as it is
    else await walk(entry, 0);
  }
  return { files: found, skipped };
}

// env: what the page shares with its forms (see page.js); `refusals` are card.js's plain words for a refusal.
export function batchFlow(env, refusals) {
  const { ctx, ui, el, capabilities, stage, show, review, facts, present, startOver, bytesOf, base64, sizeOf, pictures } = env;
  const limits = (capabilities.imports && capabilities.imports.card && capabilities.imports.card.batch) || {};
  const maxCards = limits.max_cards || 50;

  // files: [{file, label}], skipped: how many other files of a dropped folder were passed over
  async function open({ files, skipped }) {
    if (files.length > maxCards) {
      throw refuse("too_many_cards", "These are " + files.length + " files, and one batch takes at most " + maxCards + ". Import them a batch at a time.");
    }
    const all = [];
    let total = 0;
    for (const { file } of files) {
      const bytes = await bytesOf(file);
      total += bytes.length;
      if (limits.max_bytes && total > limits.max_bytes) {
        throw refuse("too_large", "These files are larger than one batch may be (" + sizeOf(limits.max_bytes) + " together). Import them in smaller batches.");
      }
      all.push(bytes);
    }
    const found = await ctx.api("imports.card_batch_scan", { cards: files.map(({ label }, index) => ({ card: base64(all[index]), origin: label })) });
    pictures.clear();
    table(found, all, skipped);
  }

  function table(found, all, skipped) {
    const rows = found.cards;
    const counts = found.counts;
    const picked = new Map(rows.map((row) => [row.item, el("input", { type: "checkbox",
      "aria-label": "Include " + (row.name || row.origin || "this file"),
      disabled: !row.importable || row.result.status === "unchanged" })]));
    for (const row of rows) picked.get(row.item).checked = row.importable && row.result.status !== "unchanged";
    const included = () => rows.filter((row) => picked.get(row.item).checked).map((row) => row.item);
    const go = ui.button("", () => ctx.act(reviewThem), { kind: "primary" });
    const count = () => {
      const chosen = included().length;
      go.textContent = "Review " + plural(chosen, "card", "cards");
      go.disabled = chosen === 0;
    };
    for (const box of picked.values()) box.addEventListener("change", count);

    const lore = (row) => (!row.importable ? "" : row.kind === "lorebook" ? plural(row.entries || 0, "entry", "entries")
      : row.lorebooks.length ? row.lorebooks.map((book) => book.name + " (" + plural(book.entries, "entry", "entries") + ")").join(", ") : "None");
    const face = (row) => (row.importable && row.container === "png" && row.portrait
      ? el("img", { class: "import-batch-pic", src: pictures.of(all[row.item], "image/png"), alt: "" })
      : ui.avatar(row.importable ? row.name : "?", { size: 30, key: row.importable ? row.result.source : "file" }));
    const tone = (row) => (!row.importable ? "import-batch-bad" : row.result.status === "unchanged" ? "import-batch-quiet"
      : row.result.status === "new" && !row.result.same_name ? "import-batch-good" : "import-batch-note");
    const body = rows.map((row) => el("tr", { class: row.importable && row.result.status !== "unchanged" ? null : "import-batch-out" },
      el("td", { class: "import-batch-tick" }, picked.get(row.item)),
      el("td", { class: "import-batch-face" }, face(row)),
      el("td", { class: "import-batch-name" }, row.importable ? row.name : "Left out",
        row.importable && row.kind !== "character" ? el("span", { class: "g-muted import-batch-kind" }, " · " + (KINDS[row.kind] || row.kind)) : null),
      el("td", { class: "import-path import-batch-file" }, row.origin || ""),
      el("td", { class: "g-muted" }, lore(row)),
      el("td", { class: "import-batch-result " + tone(row) }, resultText(row, refusals))));

    const said = present(counts.new ? counts.new + " new" : null,
      counts.update ? (counts.update === 1 ? "1 updates a card you have" : counts.update + " update cards you have") : null,
      counts.unchanged ? counts.unchanged + " already in your library" : null,
      counts.refused ? counts.refused + " cannot be imported" : null,
      skipped ? (skipped === 1 ? "1 other file in the folder is not a card file" : skipped + " other files in the folder are not card files") : null);
    show();
    stage.replaceChildren(el("div", { class: "g-stack" },
      el("div", { class: "import-batch-head" },
        el("div", {}, el("h2", { class: "import-batch-title" }, plural(counts.importable, "card", "cards") + " found"),
          el("div", { class: "g-muted import-batch-said" }, said.join(" · "))),
        ui.button("Use different files", startOver, { kind: "linklike" })),
      el("div", { class: "import-batch-wrap" },
        el("table", { class: "import-batch", "aria-label": "Cards found" },
          el("thead", {}, el("tr", {}, el("th", { scope: "col" }, el("span", { class: "import-sr" }, "Include")), el("th", { scope: "col" }, el("span", { class: "import-sr" }, "Picture")),
            el("th", { scope: "col" }, "Card"), el("th", { scope: "col" }, "File"), el("th", { scope: "col" }, "Lorebooks"), el("th", { scope: "col" }, "Result"))),
          el("tbody", {}, body))),
      el("div", { class: "import-next" },
        el("span", { class: "g-muted" }, "One review covers every card. Nothing is written until you approve it."), go)));
    count();
    if (!go.disabled) go.focus();

    async function reviewThem() {
      const proposal = await ctx.api("imports.card_batch_preview", { batch: found.batch, include: included() });
      const r = proposal.report;
      const cards = r.cards;
      const tally = (status) => cards.filter((card) => card.result.status === status).length;
      const files = plural(proposal.writes, "file", "files");
      const left = r.left_out || [];
      show(review(proposal, {
        title: plural(cards.length, "card", "cards") + " in one review",
        summary: facts([["Cards", cards.length], ["New", tally("new")], ["Updates", tally("update")],
          tally("unchanged") ? ["Already here", tally("unchanged")] : null, ["Files", proposal.writes],
          ["Left out", left.length ? left.length : "None"]].filter(Boolean)),
        notes: present(
          left.length ? el("details", { class: "import-rows", open: left.some((row) => row.why === "refused") },
            el("summary", {}, "Left out of this review (" + left.length + ")"),
            el("ul", {}, left.map((row) => el("li", {}, (row.origin || row.name || "A file") + ": "
              + (row.why === "refused" ? "cannot be imported. " + (refusals[row.reason] || sentence(row.message)) : "not ticked"))))) : null,
          cards.map((card) => {
            const report = card.report;
            const losses = (report.losses || []).length;
            const collisions = (report.collisions || []).length;
            if (!losses && !collisions) return null;
            return el("details", { class: "import-rows" },
              el("summary", {}, card.name + ": " + present(losses ? losses + " not carried over" : null, collisions ? plural(collisions, "collision", "collisions") : null).join(", ")),
              el("ul", {}, [...(report.losses || []), ...(report.collisions || [])].map((row) => el("li", {}, JSON.stringify(row)))));
          })),
        groups: cards.map((card) => ({ title: card.name, paths: card.paths,
          detail: (card.origin || "") + " · " + resultText({ importable: true, result: card.result }) + " · " + card.report.source })),
        approval: { label: "Approve all " + plural(cards.length, "card", "cards"),
          question: "Write " + files + " for " + plural(cards.length, "card", "cards") + " into this library, exactly as shown?", confirm: "Write " + files },
        written: (result) => el("ul", { class: "import-written" }, (result.cards || []).map((card) =>
          el("li", {}, card.name + ": " + (card.status === "unchanged" ? "already here, nothing written" : card.status === "update" ? "updated" : "added")
            + " as " + card.main))),
      }));
    }
  }

  return { open };
}
