/** Every address inside a world, in one place.
 *
 *  `WorldView` used to keep the open section in React state, so there was no
 *  path to build and nothing to parse. Now that a section and a record are
 *  both path segments, two things have to agree exactly — what the producers
 *  emit and what the page reads back — and they agree by both living here.
 *
 *  A leaf module: no React, and nothing imported that could drag a cycle in.
 */
import type { EntityScope } from "./api/types";
import { encodeSegment } from "./urlSegment";

/** Which of the two shapes of this page is being addressed. A campaign's fork
 *  of a world is not a world: it carries no tag vocabulary, no setup overview,
 *  no push and no gallery. */
export type Shape = "world" | "campaign";

/** The sections that hold records you can open one of. */
export const RECORD_SECTIONS = [
  "characters", "pcs", "creatures", "groups",
  "locations", "items", "lore", "greetings",
] as const;
export type RecordSection = (typeof RECORD_SECTIONS)[number];

/** ...and the four screens that are not a list of records: the world's own
 *  setup, the campaigns it feeds, its art, and its tag vocabulary. */
const FLAT_SECTIONS = ["overview", "push", "images", "tags"] as const;

export type Section = RecordSection | (typeof FLAT_SECTIONS)[number];

export const WORLD_SECTIONS: readonly Section[] = [...RECORD_SECTIONS, ...FLAT_SECTIONS];
/** The campaign shape renders every index row except Tags, and none of the
 *  three world-only screens — which is the filter `WorldView`'s column already
 *  applies, stated once here so the router and the column cannot disagree. */
export const CAMPAIGN_SECTIONS: readonly Section[] = [...RECORD_SECTIONS];

export function sectionsFor(shape: Shape): readonly Section[] {
  return shape === "world" ? WORLD_SECTIONS : CAMPAIGN_SECTIONS;
}

/** Where a shape lands when its tail names nothing. The world opens on its own
 *  setup; a campaign's fork opens on its cast, which is what it defaulted to
 *  while the section was state. */
export function defaultSection(shape: Shape): Section {
  return shape === "world" ? "overview" : "characters";
}

export function shapeOf(scope: EntityScope): Shape {
  return scope.kind === "world" ? "world" : "campaign";
}

/** One address inside a world.
 *
 *  A discriminated union rather than `(section, rid?, query?)`, because the
 *  loose signature can spell `?v=` on Lore, `?for=` on Items, an owner beside
 *  a record id, or a record id on Tags — and "every producer goes through the
 *  helper" does not make any of those illegal. Here each modifier sits on the
 *  single variant that gives it meaning, so they cannot be written at all. */
export type SectionTarget =
  // a section's own screen
  | { kind: "section"; at: "overview" | "push" | "tags" }
  | { kind: "section"; at: "images"; forCampaign?: string }
  | { kind: "section"; at: "greetings"; view?: "graph" }
  | { kind: "section"; at: "lore"; newOwner?: string }
  | { kind: "section"; at: Exclude<RecordSection, "lore" | "greetings"> }
  // one record inside one
  | { kind: "record"; at: "characters"; rid: string; v?: string }
  | { kind: "record"; at: Exclude<RecordSection, "characters">; rid: string };

function baseOf(scope: EntityScope): string {
  return scope.kind === "world"
    ? `/worlds/${encodeSegment(scope.id)}`
    : `/campaigns/${encodeSegment(scope.id)}/world`;
}

/** The only thing that builds one of these paths.
 *
 *  Every target is `<base>/<section>[/<rid>]` with no exceptions — which is
 *  what moving a campaign's character page under `/world/` bought. The one
 *  case that is not a segment is the world's overview, which IS the base.
 *
 *  Throws on a section the shape does not have. `scope` and `at` are
 *  independent parameters, so that pairing is the one rule the union above
 *  cannot state, and a silently-wrong URL is worse than a loud one. */
export function sectionHref(scope: EntityScope, target: SectionTarget): string {
  const shape = shapeOf(scope);
  if (!sectionsFor(shape).includes(target.at)) {
    throw new Error(`a ${shape} has no ${target.at} section`);
  }
  const base = baseOf(scope);
  let path = target.at === "overview" ? base : `${base}/${target.at}`;
  if (target.kind === "record") path += `/${encodeSegment(target.rid)}`;

  const q = new URLSearchParams();
  if (target.kind === "record" && target.at === "characters" && target.v) q.set("v", target.v);
  if (target.kind === "section" && target.at === "images" && target.forCampaign) {
    q.set("for", target.forCampaign);
  }
  if (target.kind === "section" && target.at === "greetings" && target.view) {
    q.set("view", target.view);
  }
  if (target.kind === "section" && target.at === "lore" && target.newOwner) {
    q.set("owner", target.newOwner);
  }
  const qs = q.toString();
  return qs ? `${path}?${qs}` : path;
}

export type ParsedTail =
  | { ok: true; section: Section; rid: string | null }
  | { ok: false };

function hasRecords(section: Section): section is RecordSection {
  return (RECORD_SECTIONS as readonly string[]).includes(section);
}

/** Read a splat tail back into a section and an optional record.
 *
 *  Empty segments are dropped, so `items/`, `/items` and `items//` all mean
 *  `items`. This reports only what a tail MEANS; whether it was spelled the
 *  way `sectionHref` spells it is decided by the caller, which has the
 *  canonical string to compare against and this does not. Everything the shape
 *  does not have is
 *  `{ ok: false }`, which the caller redirects: the page must not mount for
 *  `/worlds/w/garbage` wearing a heading that says Overview.
 *
 *  A malformed escape (`%ZZ`) makes `decodeURIComponent` throw, and a throw
 *  during render takes the whole app down rather than the one bad link — so it
 *  is caught and answered as "not an address", same as any other. */
export function parseWorldTail(tail: string, shape: Shape): ParsedTail {
  const raw = tail.split("/").filter(Boolean);
  let segs: string[];
  try {
    segs = raw.map(decodeURIComponent);
  } catch {
    return { ok: false };
  }
  if (segs.length === 0) {
    return { ok: true, section: defaultSection(shape), rid: null };
  }
  if (segs.length > 2) return { ok: false };
  const [section, rid] = segs;
  if (!sectionsFor(shape).includes(section as Section)) return { ok: false };
  if (rid !== undefined && !hasRecords(section as Section)) return { ok: false };
  return { ok: true, section: section as Section, rid: rid ?? null };
}

/** What an old `?section=…` link was asking for, or null if it was not one.
 *
 *  `section` and `id` are *consumed* here and never copied into the new
 *  address: leaving the trigger in place lets the redirect fire against
 *  itself. Modifiers are rebuilt per destination rather than carried through,
 *  so `v` cannot land on Lore and `for` cannot land on Items.
 *
 *  A section this shape does not have returns null rather than a target,
 *  which is what today's code already does by falling through — translating it
 *  would manufacture exactly the invalid paths `parseWorldTail` refuses. */
export function legacyTarget(search: URLSearchParams, shape: Shape): SectionTarget | null {
  const at = search.get("section") ?? "";
  if (!sectionsFor(shape).includes(at as Section)) return null;
  const section = at as Section;
  const rid = search.get("id") ?? "";

  if (!hasRecords(section)) {
    if (section === "images") {
      const forCampaign = search.get("for") ?? "";
      return forCampaign
        ? { kind: "section", at: "images", forCampaign }
        : { kind: "section", at: "images" };
    }
    return { kind: "section", at: section };
  }
  if (rid) {
    if (section === "characters") {
      const v = search.get("v") ?? "";
      return v
        ? { kind: "record", at: "characters", rid, v }
        : { kind: "record", at: "characters", rid };
    }
    return { kind: "record", at: section, rid };
  }
  // No id: the section's own screen. `owner` is the one modifier that belongs
  // to a recordless form — it pre-owns a new lore entry — so it is read only
  // here, never beside an id.
  if (section === "lore") {
    const newOwner = search.get("owner") ?? "";
    return newOwner
      ? { kind: "section", at: "lore", newOwner }
      : { kind: "section", at: "lore" };
  }
  if (section === "greetings") return { kind: "section", at: "greetings" };
  return { kind: "section", at: section };
}
