import { describeHeld, describeReason } from "./loreReasons";

const names = {
  "lore:realm-charter": "Realm charter",
  "items:lantern": "the lantern",
  "characters:mara": "Mara",
  "locations:saltmarch": "Saltmarch",
};

test("a key reason names the key and the post", () => {
  expect(describeReason({ type: "key", key: "Saltmarch", secondary: null, post: 41, seed: false }, names))
    .toBe("key 'Saltmarch' in post #41");
});

test("a key matched in the seed names the turn's note", () => {
  expect(describeReason({ type: "key", key: "Saltmarch", secondary: null, post: null, seed: true }, names))
    .toBe("key 'Saltmarch' in this turn's input");
});

test("a key reason with a secondary cites both keys", () => {
  expect(describeReason({ type: "key", key: "Saltmarch", secondary: "Winifred", post: 7, seed: false }, names))
    .toBe("key 'Saltmarch' + 'Winifred' in post #7");
});

test("a recursion reason names the entry that pulled it in", () => {
  expect(describeReason({ type: "recursion", via: "lore:realm-charter", key: "Saltmarch" }, names))
    .toBe("pulled in by Realm charter");
});

test("a ref the names map lacks names itself", () => {
  expect(describeReason({ type: "recursion", via: "lore:gone" }, names)).toBe("pulled in by lore:gone");
});

test("keyless and pinned", () => {
  expect(describeReason({ type: "keyless" }, names)).toBe("always on");
  expect(describeReason({ type: "pinned" }, names)).toBe("pinned");
});

test("a keyless owned entry shows only its owner", () => {
  expect(describeReason({
    type: "keyless", owner: "items:lantern",
    owner_presence: { type: "held_by", via: "characters:mara" },
  }, names)).toBe("owner present: the lantern (held by Mara)");
});

test("an owner is appended to the main reason", () => {
  expect(describeReason({
    type: "key", key: "Saltmarch", secondary: null, post: 41, seed: false,
    owner: "items:lantern", owner_presence: { type: "held_by", via: "characters:mara" },
  }, names)).toBe("key 'Saltmarch' in post #41 · owner present: the lantern (held by Mara)");
});

test.each([
  [{ type: "led_by", via: "characters:mara" }, "owner present: the lantern (led by Mara)"],
  [{ type: "headquarters", via: "locations:saltmarch" }, "owner present: the lantern (headquartered here)"],
  [{ type: "habitat", via: "locations:saltmarch" }, "owner present: the lantern (lives here)"],
  [{ type: "current_location", via: null }, "owner present: the lantern (here)"],
  [{ type: "activated", via: null }, "owner present: the lantern (mentioned)"],
  [{ type: "cast", via: null }, "owner present: the lantern"],
] as const)("owner presence %j", (presence, text) => {
  expect(describeReason({ type: "pinned", owner: "items:lantern", owner_presence: presence }, names))
    .toBe(`pinned · ${text}`);
});

test("a cast owner reads without parentheses", () => {
  expect(describeReason({ type: "keyless", owner: "characters:mara", owner_presence: { type: "cast", via: null } }, names))
    .toBe("owner present: Mara");
});

test("sticky counts the posts left and where it started", () => {
  expect(describeReason({ type: "sticky", from_post: 38, remaining: 2 }, names))
    .toBe("sticky — 2 posts left (from post #38)");
  expect(describeReason({ type: "sticky", from_post: 38, remaining: 1 }, names))
    .toBe("sticky — 1 post left (from post #38)");
});

test("recall shows the similarity to two decimals", () => {
  expect(describeReason({ type: "recall", score: 0.52 }, names)).toBe("recalled (similarity 0.52)");
  expect(describeReason({ type: "recall", score: 0.5 }, names)).toBe("recalled (similarity 0.50)");
});

test("a cooldown counts the posts it still holds the entry back", () => {
  expect(describeHeld({ type: "cooldown", remaining: 3 })).toBe("on cooldown — 3 posts");
  expect(describeHeld({ type: "cooldown", remaining: 1 })).toBe("on cooldown — 1 post");
});
