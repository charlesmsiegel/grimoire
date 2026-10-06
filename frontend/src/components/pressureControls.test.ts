import type { AnchorOption, Driver, DriverKind, DriversSnapshot, PressureState } from "../api/types";
import {
  ACTION_LABELS, ANCHOR_RELATIONS, applySeed, chooseAnchor, controlOf, DRIVER_ACTIONS,
  DRIVER_KINDS, dropRefs, groupDrivers, isActive, KIND_HEADINGS, MUST_CAP, mustAllowed,
  NO_PRESSURE, pruneControls, sanitizeSeed, SORT_ORDER, steeredCount, TIME_MODES, timeLabel,
  timeModeAvailable, toRequest, whenPhrase, type PressureControls,
} from "./pressureControls";

const MAP = "thread:mara-s-map";
const LEDGER = "thread:find-the-ledger";
const OATH = "commitment:mara-s-oath";
const DEBT = "commitment:the-debt";
const SALT = "commitment:salt-owed";
const DEADLINE = "commitment:the-midnight-deadline";
const CORONATION = "event:the-coronation";
const EVE = "holiday:2026:Saltmarch Eve";
const BIRTHDAY = "birthday:npc:mara:month:2026-05";

function driver(ref: string, state: PressureState = "ok", inDays: number | null = null,
                label = ref): Driver {
  return {
    ref, kind: ref.split(":", 1)[0] as DriverKind, label, summary: "", actors: [], status: "",
    pressure: { state, in_days: inDays, friendly: "" }, time_anchors: [], links: [],
  };
}

function anchor(ref: string, precision: AnchorOption["precision"] = "exact",
                inDays: number | null = 5): AnchorOption {
  return {
    ref, kind: ref.split(":", 1)[0] as AnchorOption["kind"], label: ref, native: "",
    friendly: "", fixed: precision === "month" ? null : 100, in_days: inDays, precision,
  };
}

function snap(drivers: Driver[] = [], anchors: AnchorOption[] = []): DriversSnapshot {
  return { now: "2026-05-01", friendly: "1 May 2026", fixed: 1, matching: "basic",
           drivers, anchors };
}

const SNAP = snap(
  [driver(MAP), driver(LEDGER), driver(OATH), driver(DEBT), driver(SALT), driver(DEADLINE),
   driver(CORONATION, "upcoming", 5), driver(EVE, "upcoming", 9), driver(BIRTHDAY, "upcoming")],
  [anchor(CORONATION), anchor(EVE), anchor(BIRTHDAY, "month", null)],
);

function controls(drivers: PressureControls["drivers"] = {},
                  more: Partial<PressureControls> = {}): PressureControls {
  return { ...NO_PRESSURE, drivers, ...more };
}

test("the tuples are the server's vocabulary, in the server's order", () => {
  expect(SORT_ORDER).toEqual(["overdue", "today", "due_soon", "upcoming", "passed", "stale", "ok"]);
  expect(DRIVER_KINDS).toEqual(["thread", "commitment", "event", "birthday", "holiday"]);
  expect(DRIVER_ACTIONS).toEqual(["advance", "close_candidate", "address", "fulfill_candidate",
                                  "break_candidate", "expire_candidate", "anchor"]);
  expect(TIME_MODES).toEqual(["auto", "near", "move", "anchor"]);
  expect(ANCHOR_RELATIONS).toEqual(["before", "on", "after", "by"]);
  expect(MUST_CAP).toBe(3);
  expect(ACTION_LABELS).toEqual({
    advance: "Advances", close_candidate: "May close", address: "Addresses",
    fulfill_candidate: "May fulfil", break_candidate: "May break",
    expire_candidate: "May expire", anchor: "Anchored to",
  });
  expect(KIND_HEADINGS).toEqual({
    thread: "Threads", commitment: "Commitments", event: "Events", birthday: "Birthdays",
    holiday: "Holidays",
  });
  expect(NO_PRESSURE).toEqual({ drivers: {}, time: "auto", anchor: "", relation: "on" });
});

test("groupDrivers groups by kind and sorts each group by pressure, then days, then label", () => {
  const groups = groupDrivers([
    driver(EVE, "upcoming", 9),
    driver(MAP, "ok", null, "Mara's map"),
    driver(DEBT, "due_soon", 3),
    driver(LEDGER, "stale", null, "Find the ledger"),
    driver(OATH, "overdue", -2),
    driver(SALT, "due_soon", 1),
    driver(DEADLINE, "due_soon", null, "B deadline"),
    driver("commitment:another", "due_soon", null, "A deadline"),
  ]);
  expect(groups.map((g) => g.kind)).toEqual(["thread", "commitment", "holiday"]);
  expect(groups.map((g) => g.heading)).toEqual(["Threads", "Commitments", "Holidays"]);
  // a stale thread before an ok one
  expect(groups[0].rows.map((r) => r.ref)).toEqual([LEDGER, MAP]);
  // the overdue commitment first; due-soon ones by days, nulls last, then label
  expect(groups[1].rows.map((r) => r.ref)).toEqual(
    [OATH, SALT, DEBT, "commitment:another", DEADLINE]);
  expect(groups[2].rows.map((r) => r.ref)).toEqual([EVE]);
});

test("steeredCount counts the drivers not Normal, and nothing else", () => {
  expect(steeredCount(NO_PRESSURE)).toBe(0);
  // a time setting alone counts nothing: the summary shows it separately
  expect(steeredCount(controls({}, { time: "near" }))).toBe(0);
  expect(steeredCount(controls({ [MAP]: "focus", [OATH]: "must", [DEBT]: "normal" },
                               { time: "anchor", anchor: CORONATION }))).toBe(2);
});

test("timeLabel names a time setting and says nothing for auto", () => {
  expect(timeLabel(NO_PRESSURE)).toBe("");
  expect(timeLabel(controls({}, { time: "near" }))).toBe("near date");
  expect(timeLabel(controls({}, { time: "move" }))).toBe("time moves");
  expect(timeLabel(controls({}, { time: "anchor", anchor: CORONATION }))).toBe("anchored");
});

test("whenPhrase mirrors the prompt's phrase list", () => {
  expect(whenPhrase(0)).toBe("today");
  expect(whenPhrase(1)).toBe("in 1 day");
  expect(whenPhrase(12)).toBe("in 12 days");
  expect(whenPhrase(-1)).toBe("1 day ago");
  expect(whenPhrase(-3)).toBe("3 days ago");
  expect(whenPhrase(4, "month")).toBe("day unknown");
  expect(whenPhrase(null, "month")).toBe("day unknown");
  expect(whenPhrase(null)).toBe("undated");
  // a fixed day with no present to measure from is not undated (§19.2)
  expect(whenPhrase(null, "exact", true)).toBe("no current date");
  expect(whenPhrase(null, "month", true)).toBe("day unknown");
  expect(whenPhrase(3, "exact", true)).toBe("in 3 days");
  expect(whenPhrase(2, "exact")).toBe("in 2 days");
});

test("isActive is any steered driver or any time setting", () => {
  expect(isActive(NO_PRESSURE)).toBe(false);
  expect(isActive(controls({ [MAP]: "normal" }))).toBe(false);
  expect(isActive(controls({ [MAP]: "avoid" }))).toBe(true);
  expect(isActive(controls({}, { time: "move" }))).toBe(true);
});

test("mustAllowed: threads and commitments only, at most three", () => {
  const three = controls({ [MAP]: "must", [OATH]: "must", [DEBT]: "must" });
  expect(mustAllowed("event", CORONATION, NO_PRESSURE)).toBe(false);
  expect(mustAllowed("holiday", EVE, NO_PRESSURE)).toBe(false);
  expect(mustAllowed("birthday", BIRTHDAY, NO_PRESSURE)).toBe(false);
  expect(mustAllowed("thread", LEDGER, NO_PRESSURE)).toBe(true);
  expect(mustAllowed("commitment", SALT, three)).toBe(false);   // a fourth
  expect(mustAllowed("commitment", OATH, three)).toBe(true);    // already must
});

test("chooseAnchor anchors, forces `on` for a month anchor and lifts an avoid on it", () => {
  const held = controls({ [CORONATION]: "avoid", [MAP]: "avoid" }, { relation: "before" });
  const got = chooseAnchor(held, CORONATION, SNAP);
  expect(got.time).toBe("anchor");
  expect(got.anchor).toBe(CORONATION);
  expect(got.relation).toBe("before");
  expect(controlOf(got, CORONATION)).toBe("normal");
  expect(controlOf(got, MAP)).toBe("avoid");
  expect(controlOf(held, CORONATION)).toBe("avoid");   // never mutated

  const month = chooseAnchor(held, BIRTHDAY, SNAP);
  expect(month).toMatchObject({ time: "anchor", anchor: BIRTHDAY, relation: "on" });
});

test("toRequest sends anchor fields only in anchor mode, never a blank anchor", () => {
  const steered = controls({ [MAP]: "focus", [LEDGER]: "focus", [OATH]: "must",
                             [DEBT]: "avoid", [SALT]: "normal" });
  expect(toRequest(steered, SNAP)).toEqual({
    focus_refs: [MAP, LEDGER], avoid_refs: [DEBT], must_refs: [OATH],
    time_mode: "auto", time_anchor_ref: "", time_anchor_relation: "",
  });
  // a held anchor is not sent outside anchor mode
  expect(toRequest(controls({}, { time: "near", anchor: CORONATION, relation: "by" }), SNAP))
    .toMatchObject({ time_mode: "near", time_anchor_ref: "", time_anchor_relation: "" });
  expect(toRequest(controls({}, { time: "anchor", anchor: CORONATION, relation: "by" }), SNAP))
    .toMatchObject({ time_mode: "anchor", time_anchor_ref: CORONATION,
                     time_anchor_relation: "by" });
  // a month anchor held with `before` is sent as `on`
  expect(toRequest(controls({}, { time: "anchor", anchor: BIRTHDAY, relation: "before" }), SNAP))
    .toMatchObject({ time_mode: "anchor", time_anchor_ref: BIRTHDAY,
                     time_anchor_relation: "on" });
  // anchor mode with no anchor is auto
  expect(toRequest(controls({}, { time: "anchor", anchor: "" }), SNAP))
    .toMatchObject({ time_mode: "auto", time_anchor_ref: "", time_anchor_relation: "" });
  // the anchor beats avoid (Decision 26): the anchored ref is never sent as avoided
  expect(toRequest(controls({ [CORONATION]: "avoid" }, { time: "anchor", anchor: CORONATION }),
                   SNAP).avoid_refs).toEqual([]);
});

test("near and move need a current date; anchor needs an anchor", () => {
  const undated = { ...SNAP, now: "", friendly: "", fixed: null };
  for (const mode of ["near", "move"] as const) {
    expect(timeModeAvailable(mode, SNAP)).toBe(true);
    expect(timeModeAvailable(mode, undated)).toBe(false);
  }
  expect(timeModeAvailable("auto", undated)).toBe(true);
  expect(timeModeAvailable("anchor", SNAP)).toBe(true);
  expect(timeModeAvailable("anchor", snap([], []))).toBe(false);
});

test("toRequest never sends near or move for an undated campaign", () => {
  const undated = { ...SNAP, now: "", friendly: "", fixed: null };
  for (const time of ["near", "move"] as const) {
    expect(toRequest(controls({}, { time }), SNAP).time_mode).toBe(time);
    expect(toRequest(controls({}, { time }), undated).time_mode).toBe("auto");
    expect(toRequest(controls({}, { time }), null).time_mode).toBe("auto");
  }
});

test("sanitizeSeed accepts only the two chooser shapes", () => {
  expect(sanitizeSeed({ chooser: { drivers: { [MAP]: "focus" } } }))
    .toEqual({ drivers: { [MAP]: "focus" } });
  expect(sanitizeSeed({ chooser: { anchor: { ref: CORONATION, relation: "on" } } }))
    .toEqual({ anchor: { ref: CORONATION, relation: "on" } });
  expect(sanitizeSeed({ chooser: { drivers: { [MAP]: "must", [DEBT]: "avoid",
                                              [OATH]: "normal" } } }))
    .toEqual({ drivers: { [MAP]: "must", [DEBT]: "avoid", [OATH]: "normal" } });
  for (const bad of [
    null, undefined, 3, "chooser", {}, { chooser: 3 }, { chooser: null }, { chooser: {} },
    { chooser: [] }, { chooser: { drivers: [MAP] } }, { chooser: { drivers: {} } },
    { chooser: { drivers: { [MAP]: "pin" } } },
    { chooser: { drivers: { [MAP]: "focus", [DEBT]: 1 } } },
    { chooser: { anchor: { ref: CORONATION, relation: "during" } } },
    { chooser: { anchor: { ref: CORONATION } } },
    { chooser: { anchor: { ref: "", relation: "on" } } },
    { chooser: { anchor: { ref: 7, relation: "on" } } },
    { chooser: { anchor: CORONATION } },
  ]) {
    expect(sanitizeSeed(bad)).toBeNull();
  }
});

test("applySeed drops what the read does not offer, and says what it dropped", () => {
  const got = applySeed({ drivers: { [MAP]: "focus", "thread:gone": "focus",
                                     [CORONATION]: "must", [DEBT]: "avoid" } }, SNAP);
  expect(got.controls).toEqual(controls({ [MAP]: "focus", [DEBT]: "avoid" }));
  expect(got.dropped).toEqual(["thread:gone", CORONATION]);

  // past the cap
  const four = applySeed({ drivers: { [MAP]: "must", [LEDGER]: "must", [OATH]: "must",
                                      [DEBT]: "must" } }, SNAP);
  expect(steeredCount(four.controls)).toBe(3);
  expect(four.dropped).toEqual([DEBT]);

  // a failed read drops everything
  expect(applySeed({ drivers: { [MAP]: "focus" }, anchor: { ref: CORONATION, relation: "on" } },
                   null))
    .toEqual({ controls: NO_PRESSURE, dropped: [MAP, CORONATION] });

  expect(applySeed({ anchor: { ref: CORONATION, relation: "on" } }, SNAP))
    .toEqual({ controls: controls({}, { time: "anchor", anchor: CORONATION, relation: "on" }),
               dropped: [] });
  expect(applySeed({ anchor: { ref: BIRTHDAY, relation: "before" } }, SNAP).controls)
    .toEqual(controls({}, { time: "anchor", anchor: BIRTHDAY, relation: "on" }));
  expect(applySeed({ anchor: { ref: "event:gone", relation: "on" } }, SNAP))
    .toEqual({ controls: NO_PRESSURE, dropped: ["event:gone"] });
});

test("pruneControls resets what a fresh read no longer lists", () => {
  const held = controls({ [MAP]: "focus", "thread:gone": "must" },
                        { time: "anchor", anchor: "event:gone", relation: "by" });
  const got = pruneControls(held, SNAP);
  expect(got.controls).toEqual(controls({ [MAP]: "focus" }, { relation: "by" }));
  expect(got.dropped).toEqual(["thread:gone", "event:gone"]);

  const kept = controls({ [OATH]: "must" }, { time: "anchor", anchor: CORONATION });
  expect(pruneControls(kept, SNAP)).toEqual({ controls: kept, dropped: [] });
});

test("pruneControls resets near or move once the read has no current date", () => {
  const undated = { ...SNAP, now: "", friendly: "", fixed: null };
  for (const time of ["near", "move"] as const) {
    const held = controls({ [MAP]: "focus" }, { time });
    expect(pruneControls(held, undated)).toEqual(
      { controls: controls({ [MAP]: "focus" }), dropped: [timeLabel(held)] });
    expect(pruneControls(held, SNAP)).toEqual({ controls: held, dropped: [] });
  }
});

test("dropRefs returns the named refs to normal and clears a named anchor", () => {
  const held = controls({ [MAP]: "focus", [OATH]: "must" },
                        { time: "anchor", anchor: CORONATION, relation: "after" });
  expect(dropRefs(held, [MAP])).toEqual(
    controls({ [OATH]: "must" }, { time: "anchor", anchor: CORONATION, relation: "after" }));
  expect(dropRefs(held, [OATH, CORONATION])).toEqual(
    controls({ [MAP]: "focus" }, { relation: "after" }));
  // a held anchor outside anchor mode is cleared without moving the time setting
  expect(dropRefs(controls({}, { time: "near", anchor: CORONATION }), [CORONATION]))
    .toEqual(controls({}, { time: "near" }));
});
