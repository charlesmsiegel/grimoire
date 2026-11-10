// The shell's pure modules (108): routes and drafts. Run by test_app_shell_js.py with `node --test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const shell = path.join(here, "..", "..", "src", "grimoire", "app", "static", "shell");
const { RESERVED, href, pageFor, parse, takeToken } = await import(path.join(shell, "router.js"));
const { Drafts } = await import(path.join(shell, "drafts.js"));

test("a reference survives a round trip through the address exactly", () => {
  const chat = "chat:campaign-1/chat-2#x?y&z";
  const location = parse(href("chat", [chat]));
  assert.equal(location.route, "chat");
  assert.deepEqual(location.path, [chat]);
});

test("a query travels with the route and empty values are left out", () => {
  const hash = href("chats", [], { filter: "character", source: "src:a@1", limit: undefined });
  const location = parse(hash);
  assert.equal(location.route, "chats");
  assert.equal(location.query.get("filter"), "character");
  assert.equal(location.query.get("source"), "src:a@1");
  assert.equal(location.query.has("limit"), false);
});

test("anything that is not a route path is the default route", () => {
  for (const hash of ["", "#", "#token=abc", "#g-main", undefined]) assert.equal(parse(hash).route, null);
  assert.equal(parse("#/Not A Route").route, "");
});

test("a segment that does not decode is kept as written", () => {
  assert.deepEqual(parse("#/source/%E0%A4%A").path, ["%E0%A4%A"]);
});

test("a malformed route or segment is refused rather than linked", () => {
  assert.throws(() => href("Bad Route"));
  assert.throws(() => href("chat", [""]));
});

test("the token is taken from the address once and the rest is kept", () => {
  assert.deepEqual(takeToken("#token=a%2Bb"), { token: "a+b", hash: "" });
  assert.deepEqual(takeToken("#/chat/x?token=t1"), { token: "t1", hash: "#/chat/x" });
  assert.deepEqual(takeToken("#/chats?filter=character&token=t2"), { token: "t2", hash: "#/chats?filter=character" });
  assert.equal(takeToken("#/chats"), null);
});

test("every reserved route names its owning sprint", () => {
  for (const [route, entry] of Object.entries(RESERVED)) {
    assert.match(route, /^[a-z][a-z0-9-]*$/);
    assert.match(entry.owner, /^1(0[89]|1[0-4])$/);
  }
  assert.equal(pageFor([{ route: "chats", id: "chats" }], "chats").id, "chats");
  assert.equal(pageFor([], "chats"), null);
});

class Storage {
  constructor() { this.items = new Map(); this.full = false; }
  getItem(key) { return this.items.has(key) ? this.items.get(key) : null; }
  setItem(key, value) { if (this.full) throw new Error("quota"); this.items.set(key, value); }
  removeItem(key) { this.items.delete(key); }
}

test("a draft is kept until it is cleared, and an empty one is not kept", () => {
  const storage = new Storage();
  const drafts = new Drafts(storage);
  const slot = drafts.slot("chat:composer:x");
  slot.set("half a sentence");
  assert.equal(new Drafts(storage).get("chat:composer:x"), "half a sentence");  // after a reload
  slot.set("");
  assert.equal(storage.items.size, 0);
  slot.set("again");
  slot.clear();
  assert.equal(slot.get(), "");
});

test("a draft survives a full or missing tab storage for the life of the page", () => {
  const storage = new Storage();
  storage.full = true;
  const drafts = new Drafts(storage);
  drafts.set("k", "kept in memory");
  assert.equal(drafts.get("k"), "kept in memory");
  const bare = new Drafts(null);
  bare.set("k", "v");
  assert.equal(bare.get("k"), "v");
});
