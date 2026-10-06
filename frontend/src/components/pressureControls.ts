/** The Story Pressure controls' pure half (capstone §16.2-§16.5): the
 *  vocabularies the chooser shares with the server, and every rule about what
 *  a set of controls means and what it may send.
 *
 *  Kept apart from the component so each rule is tested as arithmetic rather
 *  than through a rendered chooser. Not named `storyPressure.ts`: that differs
 *  from `StoryPressure.tsx` only in case, and on a case-insensitive filesystem
 *  `./StoryPressure` would resolve to this module (plan Decision 21).
 *
 *  The tuples below are parsed by `backend/tests/test_suggest_controls.py`
 *  and held to the server's own, so each stays a literal `[...] as const`. */
import type {
  AnchorRelation, Driver, DriverAction, DriverControl, DriverKind, DriversSnapshot,
  PressureState, SceneSuggestionsOptions, TimeMode,
} from "../api/types";

/** The chooser's display order (`pressure.SORT_ORDER`). Not the order the
 *  server decides a state in, which ranks `passed` second. */
export const SORT_ORDER = [
  "overdue", "today", "due_soon", "upcoming", "passed", "stale", "ok",
] as const satisfies readonly PressureState[];
export const DRIVER_KINDS = [
  "thread", "commitment", "event", "birthday", "holiday",
] as const satisfies readonly DriverKind[];
export const DRIVER_ACTIONS = [
  "advance", "close_candidate", "address", "fulfill_candidate", "break_candidate",
  "expire_candidate", "anchor",
] as const satisfies readonly DriverAction[];
export const TIME_MODES = ["auto", "near", "move", "anchor"] as const satisfies readonly TimeMode[];
export const ANCHOR_RELATIONS = [
  "before", "on", "after", "by",
] as const satisfies readonly AnchorRelation[];
/** At most this many must-include drivers: every suggestion has to serve each. */
export const MUST_CAP = 3;

const DRIVER_CONTROLS: readonly DriverControl[] = ["normal", "focus", "avoid", "must"];
/** The kinds a must may name. Temporal constraints go through the anchor. */
const MUST_KINDS: readonly DriverKind[] = ["thread", "commitment"];

export const ACTION_LABELS: Record<DriverAction, string> = {
  advance: "Advances",
  close_candidate: "May close",
  address: "Addresses",
  fulfill_candidate: "May fulfil",
  break_candidate: "May break",
  expire_candidate: "May expire",
  anchor: "Anchored to",
};

export const KIND_HEADINGS: Record<DriverKind, string> = {
  thread: "Threads",
  commitment: "Commitments",
  event: "Events",
  birthday: "Birthdays",
  holiday: "Holidays",
};

/** What the reader has set. A ref absent from `drivers` is Normal. `anchor` is
 *  held across a change of `time`, and sent only in anchor mode. */
export type PressureControls = {
  drivers: Record<string, DriverControl>;
  time: TimeMode;
  anchor: string;
  relation: AnchorRelation;
};

export const NO_PRESSURE: PressureControls = { drivers: {}, time: "auto", anchor: "", relation: "on" };

export function controlOf(c: PressureControls, ref: string): DriverControl {
  return c.drivers[ref] ?? "normal";
}

function refsWith(c: PressureControls, control: DriverControl): string[] {
  return Object.entries(c.drivers).filter(([, v]) => v === control).map(([ref]) => ref);
}

function withoutRefs(drivers: Record<string, DriverControl>,
                     refs: Iterable<string>): Record<string, DriverControl> {
  const out = { ...drivers };
  for (const ref of refs) delete out[ref];
  return out;
}

/** The anchor cleared; anchor mode, which may not hold a blank anchor, falls
 *  back to `auto`, and any other time setting stands. */
function withoutAnchor(c: PressureControls): PressureControls {
  return { ...c, anchor: "", time: c.time === "anchor" ? "auto" : c.time };
}

export function groupDrivers(drivers: Driver[]): { kind: DriverKind; heading: string;
                                                   rows: Driver[] }[] {
  const order = (d: Driver) => (SORT_ORDER as readonly string[]).indexOf(d.pressure.state);
  const byPressure = (a: Driver, b: Driver) => {
    if (order(a) !== order(b)) return order(a) - order(b);
    const da = a.pressure.in_days, db = b.pressure.in_days;
    if ((da === null) !== (db === null)) return da === null ? 1 : -1;
    if (da !== null && db !== null && da !== db) return da - db;
    return a.label < b.label ? -1 : a.label > b.label ? 1 : 0;
  };
  return DRIVER_KINDS
    .map((kind) => ({ kind, heading: KIND_HEADINGS[kind],
                      rows: drivers.filter((d) => d.kind === kind).sort(byPressure) }))
    .filter((g) => g.rows.length > 0);
}

/** How many drivers are not Normal (§16.2), and nothing else: the time
 *  setting is shown beside it by `timeLabel`. */
export function steeredCount(c: PressureControls): number {
  return Object.values(c.drivers).filter((v) => v !== "normal").length;
}

export function timeLabel(c: PressureControls): string {
  switch (c.time) {
    case "auto": return "";
    case "near": return "near date";
    case "move": return "time moves";
    case "anchor": return "anchored";
  }
}

/** The prompt's phrase for a distance from now (`suggest._when`), so a row
 *  never reads "in -3 days" or "in 1 days". */
export function whenPhrase(inDays: number | null, precision?: string): string {
  if (precision === "month") return "day unknown";
  if (inDays === null) return "undated";
  if (inDays === 0) return "today";
  if (inDays > 0) return inDays === 1 ? "in 1 day" : `in ${inDays} days`;
  return inDays === -1 ? "1 day ago" : `${-inDays} days ago`;
}

/** Whether a request is steered at all -- what `controlled` and the request
 *  read, not the summary's count. */
export function isActive(c: PressureControls): boolean {
  return steeredCount(c) > 0 || c.time !== "auto";
}

export function mustAllowed(kind: DriverKind, ref: string, c: PressureControls): boolean {
  if (!MUST_KINDS.includes(kind)) return false;
  return controlOf(c, ref) === "must" || refsWith(c, "must").length < MUST_CAP;
}

/** Anchor the batch to `ref`. A month-only anchor has no day to be before or
 *  after, so it takes `on`; and the anchor beats avoid (Decision 26), so an
 *  avoid on the same ref is lifted rather than sent beside it. */
export function chooseAnchor(c: PressureControls, ref: string,
                             snap: DriversSnapshot): PressureControls {
  const option = snap.anchors.find((a) => a.ref === ref);
  const drivers = controlOf(c, ref) === "avoid" ? withoutRefs(c.drivers, [ref]) : c.drivers;
  return { drivers, time: "anchor", anchor: ref,
           relation: option?.precision === "month" ? "on" : c.relation };
}

/** The request's control fields. The last line of defence against a 400: no
 *  anchor mode without an anchor, no relation but `on` for a month anchor, and
 *  never the anchored ref as avoided. */
export function toRequest(c: PressureControls, snap: DriversSnapshot | null): Pick<
  SceneSuggestionsOptions,
  "focus_refs" | "avoid_refs" | "must_refs" | "time_mode" | "time_anchor_ref"
  | "time_anchor_relation"
> {
  const anchored = c.time === "anchor" && c.anchor !== "";
  const month = snap?.anchors.find((a) => a.ref === c.anchor)?.precision === "month";
  return {
    focus_refs: refsWith(c, "focus"),
    avoid_refs: refsWith(c, "avoid").filter((ref) => !anchored || ref !== c.anchor),
    must_refs: refsWith(c, "must"),
    time_mode: c.time === "anchor" && !anchored ? "auto" : c.time,
    time_anchor_ref: anchored ? c.anchor : "",
    time_anchor_relation: anchored ? (month ? "on" : c.relation) : "",
  };
}

/** Controls held to a fresh read (Decision 19): a driver or an anchor the read
 *  no longer lists goes back to Normal / `auto`, and is named in `dropped`. */
export function pruneControls(c: PressureControls, snap: DriversSnapshot): {
  controls: PressureControls; dropped: string[];
} {
  const listed = new Set(snap.drivers.map((d) => d.ref));
  const gone = Object.keys(c.drivers).filter((ref) => !listed.has(ref));
  let controls: PressureControls = { ...c, drivers: withoutRefs(c.drivers, gone) };
  const dropped = [...gone];
  if (c.anchor && !snap.anchors.some((a) => a.ref === c.anchor)) {
    controls = withoutAnchor(controls);
    dropped.push(c.anchor);
  }
  return { controls, dropped };
}

/** What a seeded open asks for (§16.5): the history state's `chooser`. */
export type ChooserSeed = {
  drivers?: Record<string, DriverControl>;
  anchor?: { ref: string; relation: AnchorRelation };
};

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/** A history state's `chooser`, or `null` when it is not one of the two
 *  shapes: `{drivers: {[ref]: control}}` or `{anchor: {ref, relation}}`. A
 *  state is someone else's to write, so anything malformed is ignored whole. */
export function sanitizeSeed(state: unknown): ChooserSeed | null {
  if (!isRecord(state) || !isRecord(state.chooser)) return null;
  const { drivers, anchor } = state.chooser;
  const seed: ChooserSeed = {};
  if (drivers !== undefined) {
    if (!isRecord(drivers)) return null;
    const entries = Object.entries(drivers);
    if (!entries.length) return null;
    for (const [ref, control] of entries) {
      if (!ref || !DRIVER_CONTROLS.includes(control as DriverControl)) return null;
    }
    seed.drivers = drivers as Record<string, DriverControl>;
  }
  if (anchor !== undefined) {
    if (!isRecord(anchor) || typeof anchor.ref !== "string" || !anchor.ref
        || !(ANCHOR_RELATIONS as readonly unknown[]).includes(anchor.relation)) return null;
    seed.anchor = { ref: anchor.ref, relation: anchor.relation as AnchorRelation };
  }
  return seed.drivers || seed.anchor ? seed : null;
}

/** A seed applied to the chooser's read. What the read does not offer -- a ref
 *  it does not list, a must on a temporal driver or past the cap, an anchor
 *  that is not an anchor option, or everything when the read failed -- is
 *  dropped and named, so the request cannot be refused for a seed the reader
 *  never saw. */
export function applySeed(seed: ChooserSeed, snap: DriversSnapshot | null): {
  controls: PressureControls; dropped: string[];
} {
  const dropped: string[] = [];
  let controls = NO_PRESSURE;
  for (const [ref, control] of Object.entries(seed.drivers ?? {})) {
    if (control === "normal") continue;
    const found = snap?.drivers.find((d) => d.ref === ref);
    if (!found || (control === "must" && !mustAllowed(found.kind, ref, controls))) {
      dropped.push(ref);
      continue;
    }
    controls = { ...controls, drivers: { ...controls.drivers, [ref]: control } };
  }
  if (seed.anchor) {
    if (snap?.anchors.some((a) => a.ref === seed.anchor?.ref)) {
      controls = chooseAnchor({ ...controls, relation: seed.anchor.relation },
                              seed.anchor.ref, snap);
    } else {
      dropped.push(seed.anchor.ref);
    }
  }
  return { controls, dropped };
}

/** The named refs back to Normal, and the anchor cleared if it is one of them
 *  -- what a stale refusal's `refs` undo. */
export function dropRefs(c: PressureControls, refs: string[]): PressureControls {
  const out = { ...c, drivers: withoutRefs(c.drivers, refs) };
  return c.anchor && refs.includes(c.anchor) ? withoutAnchor(out) : out;
}
