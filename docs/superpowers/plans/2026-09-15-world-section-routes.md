# World Section Routes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every section and record of a world — Characters, Items, Lore and the rest — its own URL, and make every navigation-only control a real link, so a reader can bookmark a section, press Back through them, and open a character in a new tab.

**Architecture:** One splat route per shape (`/worlds/:wid/*` and `/campaigns/:cid/world/*`) keeps a single `WorldView` instance across every section; the page parses and validates the splat tail instead of holding a `section` state. A new leaf module, `src/worldPaths.ts`, is the only thing that builds or reads these paths. Selection moves from nonce-carrying props into the route, which deletes five push-a-value-into-a-child channels.

**Tech Stack:** React 18, react-router-dom v6, TypeScript, vitest + React Testing Library, plain CSS (`src/index.css`).

**Spec:** `docs/superpowers/specs/2026-09-15-world-section-routes-design.md`

## Global Constraints

- **No appearance changes.** Every screen keeps its current layout, type, spacing and controls. CSS is touched only to *preserve* an appearance an anchor conversion would otherwise change.
- **Frontend only.** No backend change, no new API, `backend/` is not opened.
- `src/shortcuts/` owns every key the app answers — this work adds no `keydown` listener anywhere.
- **Run vitest from `frontend/`**: `cd frontend && npx vitest run <path>`. Running it from the repo root skips `frontend/vitest.config.ts` and disables `globals`, failing every mock-based test.
- **In a frontend test an `await` means the page has SETTLED**, not that the query it named passed (`src/test-setup.ts` wraps RTL's `asyncWrapper`). Never assert against a control before the first `await`.
- The full gate is `make check` from the repo root. `make check-web` is the frontend half.
- The three lint gates are ratcheted against `lint-baselines/<tool>.json`. **Resolving a finding fails the gate too** — if `check-eslint` reports a stale count, run `make baseline` and commit the smaller file with the fix.
- Commit messages end with:
  `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `src/worldPaths.ts` | **New.** The path vocabulary: `SectionTarget`, `sectionHref`, `parseWorldTail`, the per-shape allowlists, `legacyTarget`. No React, no imports beyond `api/types` and `urlSegment`. | Create |
| `src/worldPaths.test.ts` | **New.** Unit tests for all of the above. | Create |
| `src/App.tsx` | The route table. | Modify |
| `src/routes/WorldView.tsx` | Derives section + record from the route; redirects; column rows as links. | Modify |
| `src/components/character/shared.tsx` | `characterHref` / `charactersHref` delegate to `sectionHref`. | Modify |
| `src/components/EntityEditor.tsx` | `selected` prop replaces `nav`; rows and `+ New` become links. | Modify |
| `src/components/GreetingEditor.tsx` | `selected` replaces `focus`+`focusNonce`; rows become links. | Modify |
| `src/components/PCEditor.tsx` | Same. | Modify |
| `src/components/CharacterGrid.tsx` | Cards become links; `resetSignal` retired. | Modify |
| `src/components/WorldOverview.tsx` | Tiles and check rows become links. | Modify |
| `src/routes/SearchView.tsx` | `hitTo` emits record paths. | Modify |
| `src/routes/CharacterPage.tsx` | Lore and greeting chips emit record paths. | Modify |
| `src/routes/CampaignHub.tsx` | Cast card foot and per-face links. | Modify |
| `src/shell/rail.ts` | Images row `to` + `match`. | Modify |
| `src/index.css` | `text-decoration` on `.row` / `.char-card-main`; `.row` typography pinned. | Modify |

Task order is chosen so **the app works after every task**. Tasks 1–3 land the routes while the editors still use their existing props; Tasks 4–7 convert one editor each; Task 8 moves the remaining producers; Task 9 is the gate.

---

### Task 1: The path vocabulary

**Files:**
- Create: `frontend/src/worldPaths.ts`
- Test: `frontend/src/worldPaths.test.ts`

**Interfaces:**
- Consumes: `EntityScope` from `./api/types`, `encodeSegment` from `./urlSegment`.
- Produces: `type SectionTarget`, `sectionHref(scope, target): string`, `parseWorldTail(tail, shape): ParsedTail`, `type ParsedTail`, `legacyTarget(search, shape): SectionTarget | null`, `WORLD_SECTIONS`, `CAMPAIGN_SECTIONS`, `RECORD_SECTIONS`, `defaultSection(shape)`.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/worldPaths.test.ts`:

```ts
import {
  sectionHref, parseWorldTail, legacyTarget, defaultSection,
} from "./worldPaths";

const W = { kind: "world" as const, id: "realm" };
const C = { kind: "campaign" as const, id: "run" };

describe("sectionHref", () => {
  test("a section's own screen, both shapes", () => {
    expect(sectionHref(W, { kind: "section", at: "items" })).toBe("/worlds/realm/items");
    expect(sectionHref(C, { kind: "section", at: "items" })).toBe("/campaigns/run/world/items");
  });

  test("the world root is the overview, with no trailing segment", () => {
    expect(sectionHref(W, { kind: "section", at: "overview" })).toBe("/worlds/realm");
  });

  test("a record inside a section", () => {
    expect(sectionHref(W, { kind: "record", at: "lore", rid: "the-salt-pact" }))
      .toBe("/worlds/realm/lore/the-salt-pact");
    expect(sectionHref(C, { kind: "record", at: "items", rid: "the-salt-pact" }))
      .toBe("/campaigns/run/world/items/the-salt-pact");
  });

  test("a campaign's character record lives under its world copy, like every other kind", () => {
    expect(sectionHref(C, { kind: "record", at: "characters", rid: "mira" }))
      .toBe("/campaigns/run/world/characters/mira");
  });

  test("modifiers reach the destination that gives them meaning", () => {
    expect(sectionHref(W, { kind: "record", at: "characters", rid: "mira", v: "veiled" }))
      .toBe("/worlds/realm/characters/mira?v=veiled");
    expect(sectionHref(W, { kind: "section", at: "images", forCampaign: "run" }))
      .toBe("/worlds/realm/images?for=run");
    expect(sectionHref(W, { kind: "section", at: "greetings", view: "graph" }))
      .toBe("/worlds/realm/greetings?view=graph");
    expect(sectionHref(W, { kind: "section", at: "lore", newOwner: "characters:seraphine" }))
      .toBe("/worlds/realm/lore?owner=characters%3Aseraphine");
  });

  test("an absent modifier adds no query string at all", () => {
    expect(sectionHref(W, { kind: "section", at: "images" })).toBe("/worlds/realm/images");
    expect(sectionHref(W, { kind: "record", at: "characters", rid: "mira" }))
      .toBe("/worlds/realm/characters/mira");
  });

  test("ids and world names are encoded as single segments", () => {
    expect(sectionHref({ kind: "world", id: "a b" }, { kind: "record", at: "items", rid: "c/d" }))
      .toBe("/worlds/a%20b/items/c%2Fd");
  });

  test("a campaign cannot address a world-only section", () => {
    expect(() => sectionHref(C, { kind: "section", at: "tags" })).toThrow(/tags/);
    expect(() => sectionHref(C, { kind: "section", at: "overview" })).toThrow(/overview/);
    expect(() => sectionHref(C, { kind: "section", at: "push" })).toThrow(/push/);
    expect(() => sectionHref(C, { kind: "section", at: "images" })).toThrow(/images/);
  });
});

describe("parseWorldTail", () => {
  test("an empty tail is the shape's default section", () => {
    expect(parseWorldTail("", "world"))
      .toEqual({ ok: true, section: "overview", rid: null });
    expect(parseWorldTail("", "campaign"))
      .toEqual({ ok: true, section: "characters", rid: null });
  });

  test("a section, and a section with a record", () => {
    expect(parseWorldTail("items", "world"))
      .toEqual({ ok: true, section: "items", rid: null });
    expect(parseWorldTail("items/the-salt-pact", "world"))
      .toEqual({ ok: true, section: "items", rid: "the-salt-pact" });
  });

  test("a segment is decoded", () => {
    expect(parseWorldTail("items/c%2Fd", "world"))
      .toEqual({ ok: true, section: "items", rid: "c/d" });
  });

  test("empty segments are dropped, so a trailing or doubled slash is the section root", () => {
    expect(parseWorldTail("items/", "world"))
      .toEqual({ ok: true, section: "items", rid: null });
    expect(parseWorldTail("/items", "world"))
      .toEqual({ ok: true, section: "items", rid: null });
    expect(parseWorldTail("items//", "world"))
      .toEqual({ ok: true, section: "items", rid: null });
  });

  test("a segment spelled any other way still names the same record", () => {
    // `it%65ms` is "items"; `%2f` is a slash inside an id. Both parse to what
    // they mean -- WorldView compares the result against `sectionHref`'s own
    // spelling and replaces the URL, so canonicalisation needs no flag here.
    expect(parseWorldTail("it%65ms/x", "world"))
      .toEqual({ ok: true, section: "items", rid: "x" });
    expect(parseWorldTail("items/a%2fb", "world"))
      .toEqual({ ok: true, section: "items", rid: "a/b" });
  });

  test("a malformed escape is rejected, not thrown", () => {
    expect(() => parseWorldTail("items/%ZZ", "world")).not.toThrow();
    expect(parseWorldTail("items/%ZZ", "world")).toEqual({ ok: false });
  });

  test("an unknown section is rejected rather than rendered", () => {
    expect(parseWorldTail("garbage", "world")).toEqual({ ok: false });
    expect(parseWorldTail("garbage/x", "world")).toEqual({ ok: false });
  });

  test("a world-only section is rejected on the campaign shape", () => {
    expect(parseWorldTail("tags", "campaign")).toEqual({ ok: false });
    expect(parseWorldTail("images", "campaign")).toEqual({ ok: false });
    expect(parseWorldTail("overview", "campaign")).toEqual({ ok: false });
    expect(parseWorldTail("push", "campaign")).toEqual({ ok: false });
  });

  test("a record is rejected on a section that has none", () => {
    expect(parseWorldTail("tags/x", "world")).toEqual({ ok: false });
    expect(parseWorldTail("push/x", "world")).toEqual({ ok: false });
    expect(parseWorldTail("images/x", "world")).toEqual({ ok: false });
    expect(parseWorldTail("overview/x", "world")).toEqual({ ok: false });
  });

  test("more than two segments is not an address", () => {
    expect(parseWorldTail("items/a/b", "world")).toEqual({ ok: false });
  });
});

describe("legacyTarget", () => {
  test("a section and a record", () => {
    expect(legacyTarget(new URLSearchParams("section=lore&id=the-salt-pact"), "world"))
      .toEqual({ kind: "record", at: "lore", rid: "the-salt-pact" });
    expect(legacyTarget(new URLSearchParams("section=lore"), "world"))
      .toEqual({ kind: "section", at: "lore" });
  });

  test("a character carries its version, and only when one was named", () => {
    expect(legacyTarget(new URLSearchParams("section=characters&id=mira&v=veiled"), "world"))
      .toEqual({ kind: "record", at: "characters", rid: "mira", v: "veiled" });
    expect(legacyTarget(new URLSearchParams("section=characters&id=mira&v="), "world"))
      .toEqual({ kind: "record", at: "characters", rid: "mira" });
  });

  test("section=characters with no id is the grid, not an empty character", () => {
    expect(legacyTarget(new URLSearchParams("section=characters"), "world"))
      .toEqual({ kind: "section", at: "characters" });
  });

  test("images keeps `for`, and nothing else does", () => {
    expect(legacyTarget(new URLSearchParams("section=images&for=run"), "world"))
      .toEqual({ kind: "section", at: "images", forCampaign: "run" });
    expect(legacyTarget(new URLSearchParams("section=items&for=run"), "world"))
      .toEqual({ kind: "section", at: "items" });
  });

  test("`owner` reaches a recordless lore root and never joins an id", () => {
    expect(legacyTarget(new URLSearchParams("section=lore&owner=characters:sera"), "world"))
      .toEqual({ kind: "section", at: "lore", newOwner: "characters:sera" });
    expect(legacyTarget(new URLSearchParams("section=lore&id=x&owner=characters:sera"), "world"))
      .toEqual({ kind: "record", at: "lore", rid: "x" });
  });

  test("no section, an unknown one, or one this shape forbids, is not a legacy link", () => {
    expect(legacyTarget(new URLSearchParams(""), "world")).toBeNull();
    expect(legacyTarget(new URLSearchParams("id=x"), "world")).toBeNull();
    expect(legacyTarget(new URLSearchParams("section=garbage"), "world")).toBeNull();
    expect(legacyTarget(new URLSearchParams("section=tags"), "campaign")).toBeNull();
  });

  test("a legacy target never carries `section` or `id` into its new address", () => {
    const t = legacyTarget(new URLSearchParams("section=lore&id=x"), "world")!;
    expect(sectionHref(W, t)).toBe("/worlds/realm/lore/x");
  });
});

describe("defaultSection", () => {
  test("each shape has one", () => {
    expect(defaultSection("world")).toBe("overview");
    expect(defaultSection("campaign")).toBe("characters");
  });
});
```

- [ ] **Step 2: Run the test and watch it fail**

Run: `cd frontend && npx vitest run src/worldPaths.test.ts`
Expected: FAIL — `Failed to resolve import "./worldPaths"`.

- [ ] **Step 3: Write the module**

Create `frontend/src/worldPaths.ts`:

```ts
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
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `cd frontend && npx vitest run src/worldPaths.test.ts`
Expected: PASS, all cases.

- [ ] **Step 5: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/worldPaths.ts frontend/src/worldPaths.test.ts
git commit -m "Add the world's path vocabulary

Every address inside a world in one leaf module: a discriminated target
that cannot spell an illegal modifier, a tail parser that refuses what a
shape does not have, and a reader for the ?section= links this replaces.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: The route table, and moving a campaign's character page

**Files:**
- Modify: `frontend/src/App.tsx:264` (the world character route) and `:237` (the campaign one)
- Modify: `frontend/src/components/character/shared.tsx:125-140`
- Test: `frontend/src/components/character/shared.test.ts` (create)
- Test: `frontend/src/routes/CharacterPage.test.tsx:138-146` (campaign harness)

**Interfaces:**
- Consumes: `sectionHref` from Task 1.
- Produces: routes `/worlds/:wid/*`, `/campaigns/:cid/world/*`, `/campaigns/:cid/world/characters/:eid`, and a redirect from `/campaigns/:cid/characters/:eid`. `characterHref` and `charactersHref` keep their signatures.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/character/shared.test.ts`:

```ts
import { characterHref, charactersHref } from "./shared";

const W = { kind: "world" as const, id: "realm" };
const C = { kind: "campaign" as const, id: "run" };

test("a character's page, in both shapes", () => {
  expect(characterHref(W, "mira")).toBe("/worlds/realm/characters/mira");
  expect(characterHref(C, "mira")).toBe("/campaigns/run/world/characters/mira");
});

test("a version rides along when one is named", () => {
  expect(characterHref(W, "mira", "veiled")).toBe("/worlds/realm/characters/mira?v=veiled");
  expect(characterHref(W, "mira", "")).toBe("/worlds/realm/characters/mira");
});

test("the grid they came from", () => {
  expect(charactersHref(W)).toBe("/worlds/realm/characters");
  expect(charactersHref(C)).toBe("/campaigns/run/world/characters");
});
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd frontend && npx vitest run src/components/character/shared.test.ts`
Expected: FAIL — the campaign cases return `/campaigns/run/characters/mira` and `/campaigns/run/world?section=characters`.

- [ ] **Step 3: Delegate both helpers**

In `frontend/src/components/character/shared.tsx`, add to the imports at the top:

```ts
import { sectionHref } from "../../worldPaths";
```

Replace the two functions (currently at lines 125–140) with:

```ts
export function characterHref(scope: EntityScope, cid: string, vid?: string): string {
  return sectionHref(scope, vid
    ? { kind: "record", at: "characters", rid: cid, v: vid }
    : { kind: "record", at: "characters", rid: cid });
}

/** Where its grid lives — the page a character's `‹ All characters` goes back
 *  to, which is a section of the world view in both scopes. */
export function charactersHref(scope: EntityScope): string {
  return sectionHref(scope, { kind: "section", at: "characters" });
}
```

- [ ] **Step 4: Run the test and watch it pass**

Run: `cd frontend && npx vitest run src/components/character/shared.test.ts`
Expected: PASS.

- [ ] **Step 5: Move the route and add the two splats**

In `frontend/src/App.tsx`, replace the campaign character route (line 237):

```tsx
        <Route path="/campaigns/:cid/characters/:eid" element={<CharacterPage campaign />} />
```

with a redirect, and mount the new address beside the world's:

```tsx
        {/* A campaign's character used to live here, outside the world copy
            that holds its other seven record kinds. It moved so the two shapes
            are one shape; this keeps a link somebody already holds working,
            and is a redirect rather than a second live route so there is still
            exactly one address per screen. */}
        <Route path="/campaigns/:cid/characters/:eid" element={<LegacyCharacterRedirect />} />
```

Then, beside the existing `/worlds/:wid/characters/:eid` route (line 264), make the world block read:

```tsx
        {/* A character owns a screen rather than a third of one — see
            `CharacterPage`. Both scopes, because a campaign's copy of a
            character is a different record from the world's. Declared before
            the splats for a reader; the matcher ranks static segments above a
            splat on its own. */}
        <Route path="/worlds/:wid/characters/:eid" element={<CharacterPage />} />
        <Route path="/campaigns/:cid/world/characters/:eid" element={<CharacterPage campaign />} />
        {/* One splat per shape, so every section and record of a world is the
            same route object and React keeps ONE `WorldView` across all of
            them. Sibling routes per section would remount the page on every
            column click and re-read the world and its cover each time. */}
        <Route path="/worlds/:wid/*" element={<WorldView />} />
        <Route path="/campaigns/:cid/world/*" element={<WorldView campaign />} />
```

Delete the now-superseded standalone routes `<Route path="/worlds/:wid" …>` and `<Route path="/campaigns/:cid/world" …>` — the splats match an empty tail and cover both.

- [ ] **Step 6: Add the redirect component**

At the bottom of `frontend/src/App.tsx`, above `export default function App()`:

```tsx
/** The address a campaign's character page used to have.
 *
 *  Carries the version and whatever `location.state` the link was followed
 *  with — `CharacterPage`'s own back link passes `{ reveal }` so a campaign
 *  grid does not swallow an unseated character, and a redirect that dropped it
 *  would make them read as deleted. */
export function LegacyCharacterRedirect() {
  const { cid = "", eid = "" } = useParams();
  const location = useLocation();
  const v = new URLSearchParams(location.search).get("v") ?? "";
  // Through the helper, not a template string: it is the only thing allowed to
  // know where a character's page lives, and it encodes segments the way the
  // rest of the app does.
  return <Navigate replace state={location.state}
                   to={characterHref({ kind: "campaign", id: cid }, eid, v || undefined)} />;
}
```

Import `characterHref` from `./components/character/shared` at the top of `App.tsx`.

Add `useParams` to the existing `react-router-dom` import at the top of the file.

- [ ] **Step 7: Point the campaign harness at the new address**

In `frontend/src/routes/CharacterPage.test.tsx`, in `renderCampaign` (lines 138–146), change the default url and the route path:

```tsx
async function renderCampaign(url = "/campaigns/run/world/characters/seraphine") {
```
```tsx
        <Route path="/campaigns/:cid/world/characters/:eid" element={<CharacterPage campaign />} />
```

Then update the three assertions that spell the old grid address — `CharacterPage.test.tsx:905`, `:940` and `:1044` — from `/campaigns/run/world?section=characters` (and the `toContain("section=characters")` at `:940`) to `/campaigns/run/world/characters`.

- [ ] **Step 8: Write the redirect's own test**

Append to `frontend/src/routes/CharacterPage.test.tsx`:

```tsx
test("the address a campaign character used to have still lands on them", async () => {
  let seen = "";
  function Probe() { seen = useLocation().pathname + useLocation().search; return null; }
  render(
    <MemoryRouter initialEntries={["/campaigns/run/characters/seraphine?v=veiled"]}>
      <Probe />
      <Routes>
        <Route path="/campaigns/:cid/characters/:eid" element={<LegacyCharacterRedirect />} />
        <Route path="/campaigns/:cid/world/characters/:eid" element={<div>landed</div>} />
      </Routes>
    </MemoryRouter>,
  );
  await screen.findByText("landed");
  expect(seen).toBe("/campaigns/run/world/characters/seraphine?v=veiled");
});
```

Import `LegacyCharacterRedirect` from `../App` in the test — Step 6 already exports it.

- [ ] **Step 9: Repair the one assertion this task invalidates elsewhere**

`charactersHref` changed shape, so `WorldView.test.tsx:465` — which asserts `lastPath` is `/worlds/w?section=characters` — now reads:

```tsx
  expect(lastPath).toBe("/worlds/w/characters");
```

That is the only assertion outside the character suites that spells the old grid address; the sweep in the next step proves it.

- [ ] **Step 10: Run the whole frontend suite, and leave it green**

Run: `cd frontend && npx vitest run`
Expected: PASS. This task must land green like every other one — if a suite fails, it is naming an address this task changed and the assertion belongs in this commit, not a later one.

- [ ] **Step 11: Commit**

```bash
git add frontend/src/App.tsx frontend/src/components/character/shared.tsx \
        frontend/src/components/character/shared.test.ts frontend/src/routes/CharacterPage.test.tsx \
        frontend/src/routes/WorldView.test.tsx
git commit -m "Mount a world's sections as routes, and move a campaign's character page

The two shapes now address identically: a campaign's character sits under
its world copy beside its other seven record kinds, and the address it had
redirects so a held link still lands. One splat per shape keeps a single
WorldView across every section.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `WorldView` reads its section off the route, and old links keep landing

*Legacy translation lands in the same task as the redirects it has to outrank.
Split across two, there is a commit in between where
`/campaigns/c/world?section=lore&id=x` is answered by the campaign root's
default redirect and the destination is lost — and the arrival effect that
handles it today races a render-time `<Navigate>` rather than losing to it
predictably. One ordered chain of redirects, defined once, is the only version
of this that is reviewable.*

**Files:**
- Modify: `frontend/src/routes/WorldView.tsx` — `:89` (the `section` state), `:250-263` (`select`), `:358-382` (the palette), `:400-462` (the column), `:466-478` (the footer)
- Test: `frontend/src/routes/WorldView.test.tsx`

**Interfaces:**
- Consumes: `sectionHref`, `parseWorldTail`, `defaultSection`, `shapeOf` from Task 1; the splat routes from Task 2.
- Produces: `WorldView` renders the section its URL names, and `?section=` stops being written anywhere. The children keep their current props this task — `focus={rid}` is fed into the existing `focus`/`focusNonce` pairs and `navFor` into `nav`, so the page keeps working; Tasks 4–6 replace those with `selected`/`recordHref`, and Task 7 drops `CharacterGrid`'s `resetSignal`.

- [ ] **Step 1: Write the failing tests**

In `frontend/src/routes/WorldView.test.tsx`, find the existing `renderAtUrl` helper and make sure it mounts the splat route (`path="/worlds/:wid/*"`). Then append:

```tsx
test("each section has its own address", async () => {
  renderAtUrl("/worlds/w/items");
  expect(await screen.findByRole("heading", { level: 1 })).toHaveTextContent("Items");
});

test("the world root is the overview", async () => {
  renderAtUrl("/worlds/w");
  expect(await screen.findByRole("heading", { level: 1 })).toHaveTextContent("Overview");
});

test("a column row is a link, which is what lets it open in a new tab", async () => {
  renderAtUrl("/worlds/w");
  const row = await screen.findByRole("link", { name: /Items/ });
  expect(row).toHaveAttribute("href", "/worlds/w/items");
});

test("clicking a column row changes the address", async () => {
  renderAtUrl("/worlds/w");
  fireEvent.click(await screen.findByRole("link", { name: /Lore/ }));
  await waitFor(() => expect(lastPath).toBe("/worlds/w/lore"));
});

test("back steps through sections rather than leaving the world", async () => {
  const router = renderWithRouter("/worlds/w");
  fireEvent.click(await screen.findByRole("link", { name: /Lore/ }));
  await waitFor(() => expect(lastPath).toBe("/worlds/w/lore"));
  fireEvent.click(await screen.findByRole("link", { name: /Items/ }));
  await waitFor(() => expect(lastPath).toBe("/worlds/w/items"));
  await router.navigate(-1);
  await waitFor(() => expect(lastPath).toBe("/worlds/w/lore"));
});

test("a tail spelled any other way is replaced with the one address the screen has", async () => {
  for (const odd of ["/worlds/w/items/", "/worlds/w//items", "/worlds/w/it%65ms"]) {
    renderAtUrl(odd);
    await waitFor(() => expect(lastPath).toBe("/worlds/w/items"));
    cleanup();
  }
});

test("...and the redirect keeps the modifiers the link was carrying", async () => {
  renderAtUrl("/worlds/w/lore/?owner=characters%3Asera");
  await waitFor(() => expect(lastPath).toBe("/worlds/w/lore"));
  expect(lastSearch).toBe("?owner=characters%3Asera");
});

test("an old section link lands on the record's new address, replacing it", async () => {
  renderAtUrl("/worlds/w?section=lore&id=the-salt-pact");
  await waitFor(() => expect(lastPath).toBe("/worlds/w/lore/the-salt-pact"));
  expect(lastSearch).toBe("");
});

test("an old character link keeps its version", async () => {
  renderAtUrl("/worlds/w?section=characters&id=mira&v=main");
  await waitFor(() => expect(lastPath + lastSearch).toBe("/worlds/w/characters/mira?v=main"));
});

test("section=characters with no id is still the grid, not an empty character", async () => {
  renderAtUrl("/worlds/w?section=characters");
  await waitFor(() => expect(lastPath).toBe("/worlds/w/characters"));
});

test("an old images link keeps the campaign it was narrowed to", async () => {
  renderAtUrl("/worlds/w?section=images&for=run");
  await waitFor(() => expect(lastPath + lastSearch).toBe("/worlds/w/images?for=run"));
});

test("a modern record path ignores a stray section param rather than obeying it", async () => {
  renderAtUrl("/worlds/w/lore/current?section=items&id=old");
  await waitFor(() => expect(lastPath).toBe("/worlds/w/lore/current"));
});

test("a legacy section this shape does not have is ignored, not translated", async () => {
  renderCampaignAtUrl("/campaigns/c/world?section=tags");
  await waitFor(() => expect(lastPath).toBe("/campaigns/c/world/characters"));
});

test("legacy translation beats the campaign root's default redirect", async () => {
  // The whole reason this task is not two tasks: a split leaves a commit in
  // which this URL lands on Characters and the lore entry is lost.
  renderCampaignAtUrl("/campaigns/c/world?section=lore&id=x");
  await waitFor(() => expect(lastPath).toBe("/campaigns/c/world/lore/x"));
});

test("an id containing a slash survives the round trip", async () => {
  // The one case a decoded splat param cannot express: react-router hands
  // `useParams()["*"]` back as `items/a/b`, which reads as three segments and
  // is not an address at all. Rendered rather than unit-tested, because the
  // decoding this guards against happens in the router, not in the parser.
  renderAtUrl("/worlds/w/items/a%2Fb");
  await waitFor(() => expect(lastPath).toBe("/worlds/w/items/a%2Fb"));
  await screen.findByRole("heading", { name: "A slash B" });
});

test("an unknown section redirects rather than rendering a page headed Overview", async () => {
  renderAtUrl("/worlds/w/garbage");
  await waitFor(() => expect(lastPath).toBe("/worlds/w"));
});

test("a campaign is sent to its cast, and cannot address a world-only section", async () => {
  renderCampaignAtUrl("/campaigns/c/world");
  await waitFor(() => expect(lastPath).toBe("/campaigns/c/world/characters"));
  renderCampaignAtUrl("/campaigns/c/world/tags");
  await waitFor(() => expect(lastPath).toBe("/campaigns/c/world/characters"));
});

test("the world is read once across a section change, while the counts re-read", async () => {
  renderAtUrl("/worlds/w");
  await screen.findByRole("heading", { level: 1 });
  const worldReads = (api.getWorld as any).mock.calls.length;
  const countReads = (api.listEntities as any).mock.calls.length;
  fireEvent.click(await screen.findByRole("link", { name: /Lore/ }));
  // The counts are started inside a promise, so they land a microtask after the
  // pathname changes -- assert them through waitFor, not on the next line.
  await waitFor(() =>
    expect((api.listEntities as any).mock.calls.length).toBeGreaterThan(countReads));
  expect(lastPath).toBe("/worlds/w/lore");
  expect((api.getWorld as any).mock.calls.length).toBe(worldReads);
});

test("a campaign is redirected before its world id has arrived", async () => {
  // getCampaign never settles: the redirect must not be waiting on it.
  (api.getCampaign as any).mockReturnValue(new Promise(() => {}));
  renderCampaignAtUrl("/campaigns/c/world");
  await waitFor(() => expect(lastPath).toBe("/campaigns/c/world/characters"));
});
```

Three harness notes, because two of these tests cannot pass without them:

- `renderAtUrl` must mount the **splat** route now: `<Route path="/worlds/:wid/*" element={<WorldView />} />`. `renderCampaignAtUrl` mirrors it with `<Route path="/campaigns/:cid/world/*" element={<WorldView campaign />} />`.
- **`window.history.back()` does not drive a `MemoryRouter`** — it has its own in-memory stack and ignores the browser's. Add a third helper built on the data router, which exposes one:

```tsx
import { createMemoryRouter, RouterProvider } from "react-router-dom";

function renderWithRouter(url: string) {
  const router = createMemoryRouter(
    [{ path: "/worlds/:wid/*", element: <Harness><WorldView /></Harness> }],
    { initialEntries: [url] },
  );
  render(<RouterProvider router={router} />);
  return router;           // router.navigate(-1) is Back
}
```

`Harness` is whatever `renderAtUrl` already wraps `WorldView` in (the shell status and palette providers, and the `useLocation` probe that sets `lastPath`).
- Assert **after** an `await`, never before. `src/test-setup.ts` makes every `findBy*`/`waitFor` return only once the DOM has gone quiet; a bare `expect` on the statement after a click is reading a page still building itself.

- [ ] **Step 2: Run them and watch them fail**

Run: `cd frontend && npx vitest run src/routes/WorldView.test.tsx`
Expected: FAIL — `findByRole("link", …)` finds nothing (the rows are buttons), and `/worlds/w/items` renders the overview.

- [ ] **Step 3: Derive the section from the route**

In `frontend/src/routes/WorldView.tsx`, add `Navigate` to the **existing**
`react-router-dom` import at line 2 — it already brings in `Link`,
`useLocation`, `useNavigate`, `useParams` and `useSearchParams`, so `Navigate`
is the only name this task adds to it, and a second import of the same module
would fail eslint. `EntityScope` is likewise already imported from `../api/client` at
line 3 — do not import it again. The only new import line is:

```ts
import {
  defaultSection, legacyTarget, parseWorldTail, sectionHref,
  type RecordSection, type Section, type SectionTarget,
} from "../worldPaths";
```

Delete the `section` state (line 89) and replace `SectionKey` with `Section` throughout. Immediately after `const { wid: widParam = "", cid = "" } = useParams();` add:

```ts
  const shape = campaign ? "campaign" : "world";
  const params = useSearchParams()[0];
  const tail = location.pathname.split("/").filter(Boolean).slice(campaign ? 3 : 2).join("/");
  const parsed = parseWorldTail(tail, shape);
  const section: Section = parsed.ok ? parsed.section : defaultSection(shape);
  const rid = parsed.ok ? parsed.rid : null;
```

**Every hook in this component must stay above the redirects added in Step 5.**
React requires the same hooks in the same order on every render, and an early
`return <Navigate />` placed among them would break that rule the first time an
address was invalid. Put the redirects where the existing
`if (campaign && !wid) return null;` sits — after the last `useEffect`,
`useMemo`, `useCallback` and `usePaletteSource` — and above that line.

**The raw tail, not `useParams()["*"]`.** React Router decodes a splat param,
so `/worlds/realm/items/a%2Fb` arrives as `items/a/b` — the boundary between a
slash *inside* an id and a slash *between* segments is gone, and the parser
would see three segments and reject a perfectly good address. Slice it off the
undecoded pathname instead and let `parseWorldTail` do the decoding, once:

```ts
  // `location.pathname` is raw. `useParams()["*"]` is not, and an id
  // containing a slash is exactly what the difference loses.
  const tail = location.pathname.split("/").filter(Boolean).slice(campaign ? 3 : 2).join("/");
```

Two segments for `/worlds/:wid`, three for `/campaigns/:cid/world` — the route
patterns are fixed, so the counts are too.

`loreReset` **stays**: `LorebookImport`'s `onImported` bumps it and the Lore
`EntityEditor` is keyed on it (`WorldView.tsx:99`, `:551`, `:553`), which is a
remount-after-import signal and not a selection channel. What goes in this task
is `charReset`, `focusGreeting`, `focusPC`, `pcFocus`, `scopeKey` (which existed
only to scope `focusPC`, and is unused the moment it does — leaving it fails the
lint gate) and the `entityNav` state with its setter.

**`navFor` stays, rebuilt.** It is the thing that keeps a nav aimed at Lore from
being consumed by Items, and Step 8 gives it a route-derived body. Task 4
retires it along with the prop it feeds.

- [ ] **Step 4: Delete the `?section=` arrival effect**

Delete the whole `useEffect` at lines 327–357 — the one whose docstring begins
*"Open what the URL names"*. It calls `openEntity` and friends, which Step 7
turns into navigations, so leaving it in place would put a second writer of the
URL beside the redirect chain below. Its job moves there.

- [ ] **Step 5: Add the redirect chain, above the `!wid` guard**

`WorldView` returns `null` at line 385 while a campaign's world id is in flight. Both redirects must sit **above** it: neither needs `wid`, and waiting for one would leave the wrong address on screen for a round trip. Put this immediately before `if (campaign && !wid) return null;`:

```tsx
  // What the tail could not name, and the campaign shape's root. The legacy
  // translation immediately below runs before both, so `?section=` is not
  // thrown away by a default redirect that also applies at the same address.
  const scopeForPaths: EntityScope = campaign
    ? { kind: "campaign", id: cid }
    : { kind: "world", id: widParam };
  // FIRST: an old `?section=X&id=Y` link, translated once and replaced.
  //
  // Ahead of everything below, and that ordering is the point rather than an
  // accident of where it was written. `/campaigns/:cid/world?section=lore&id=x`
  // satisfies the campaign root's default redirect too, and firing that one
  // would throw the destination away and land on Characters.
  //
  // Only at the legacy roots. A URL that already names a record by path is
  // addressing it, and a stray `?section=` riding along does not outrank the
  // path -- obeying it would send a reader somewhere they did not ask to go.
  const legacy = tail === "" ? legacyTarget(params, shape) : null;
  if (legacy) {
    return <Navigate replace to={sectionHref(scopeForPaths, legacy)} />;
  }

  if (!parsed.ok) {
    return <Navigate replace
                     to={sectionHref(scopeForPaths,
                                     { kind: "section", at: defaultSection(shape) } as SectionTarget)} />;
  }
  if (campaign && tail === "") {
    return <Navigate replace
                     to={sectionHref(scopeForPaths, { kind: "section", at: "characters" })} />;
  }
  // `/worlds/w/items/` means Items and so does `/worlds/w/it%65ms` -- but
  // neither is how `sectionHref` spells it, and "exactly one address per
  // screen" is the rule this page is being rebuilt around. So the canonical
  // string is BUILT and compared, rather than the tail being inspected for the
  // particular ways it might be odd: a trailing slash, a doubled one, an
  // over-escaped segment and an id whose own escaping differs all come out the
  // same way, and no list of cases has to be kept complete.
  //
  // Loop-safe by construction: the destination's pathname IS `canonicalPath`,
  // so the next render's comparison succeeds. The query is carried through
  // verbatim -- `?owner=` and `?v=` are the screen's modifiers and dropping
  // them here would be a redirect that loses what the link asked for.
  const canonicalPath = rid
    ? sectionHref(scopeForPaths, { kind: "record", at: section as RecordSection, rid } as SectionTarget)
    : sectionHref(scopeForPaths, { kind: "section", at: section } as SectionTarget);
  if (location.pathname !== canonicalPath) {
    return <Navigate replace to={canonicalPath + location.search} />;
  }
```

Neither target passes a modifier, so `canonicalPath` never carries a query of
its own and the comparison is pathname against pathname.

The chain in order — **legacy, then invalid, then campaign root, then
canonical** — is the whole of this page's redirect behaviour, and each link in
it is unreachable from the one before because every destination satisfies the
tests above it. `legacyTarget` drops `section` and `id` rather than copying them
forward, so the address it produces cannot re-trigger the first.

Note `scopeForPaths` uses `widParam`, not the async `wid` — for the world shape they are the same value and `widParam` is available on the first render.

- [ ] **Step 6: Replace `select` with a href builder**

Delete `function select(key: SectionKey)` (lines 250–263) and add:

```ts
  /** Where a row, a tile or a palette item points. The scope this page is
   *  *about* — a campaign's fork addresses under its own base. */
  const hrefFor = (at: Section) =>
    sectionHref(scopeForPaths, { kind: "section", at } as SectionTarget);
```

Then:

- **The column** (lines 400–462): every `<button className={"column-row" + …} onClick={() => select(k)}>` becomes `<Link className={"column-row" + (section === k ? " active" : "")} to={hrefFor(k)}>`, keeping the `<span>` children byte-for-byte. `.column-row` already pins its own typography and `text-decoration`, and `CampaignHub` already renders the class on a `<Link>`, so this is pixel-identical with no CSS change.
- **The campaign back button** (line 403) becomes `<Link className="column-back" to={`/campaigns/${cid}`}>`, matching the world shape's `‹ All worlds` beside it. `a.column-back` is already styled. (A campaign's hub is not an address inside a world, so this one is not `sectionHref`'s to build.)
- **The fork's footer link to its source world** (line 468) is already a `<Link>` but spells `/worlds/${wid}` by hand. It is an address inside a world, so it goes through the helper: `to={sectionHref({ kind: "world", id: wid }, { kind: "section", at: "overview" })}`. Same string today — and still the right one the day the world root moves.
- **The palette** (lines 363–382): each `run: () => select(x)` becomes `run: () => navigate(hrefFor(x))`. Add `name` and `hrefFor`'s dependencies to the `useCallback` dep list; `scopeForPaths` is rebuilt each render, so depend on `campaign`, `cid`, `widParam`, `groups` and `name`.
- **The two footer importers** (lines 470–478) keep their `<button>` and their disclosure state, and swap `setSection("lore")` / `setSection("overview")` for `navigate(hrefFor("lore"))` / `navigate(hrefFor("overview"))`.

- [ ] **Step 7: Point the cross-navigation callbacks at the route**

`openGreeting`, `openLore`, `openEntity` and `openOwner` currently set a nonce record and a section. Each becomes a navigation:

```ts
  function openGreeting(gid: string) {
    navigate(sectionHref(scopeForPaths, { kind: "record", at: "greetings", rid: gid }));
  }

  function openLore(nav: { focusEntry?: string; newOwner?: string }) {
    if (nav.focusEntry) {
      navigate(sectionHref(scopeForPaths, { kind: "record", at: "lore", rid: nav.focusEntry }));
    } else {
      // Branched rather than passing a possibly-undefined `newOwner`: the two
      // are different addresses, and one of them has no query string at all.
      navigate(sectionHref(scopeForPaths, nav.newOwner
        ? { kind: "section", at: "lore", newOwner: nav.newOwner }
        : { kind: "section", at: "lore" }));
    }
  }

  function openEntity(kind: RecordSection, id: string) {
    navigate(sectionHref(scopeForPaths, { kind: "record", at: kind, rid: id } as SectionTarget));
  }
```

`openOwner` keeps its split-on-first-colon and routes `characters` through `openCharacter`, `pcs` and the entity kinds through `openEntity`. `openCharacter` keeps `characterHref` and its `{ replace: true }`.

Delete `charReset`, `focusGreeting`, `focusPC`, `pcFocus` and `entityNav`'s
setter. **`loreReset` stays** — it is a remount-after-import signal, not a
selection channel — and so does `navFor`, rebuilt in Step 8 below to carry the
route's record and `?owner=` into the props `EntityEditor` still has until
Task 5.

- [ ] **Step 8: Feed the derived record — and the pre-owned form — into the children unchanged**

This task does not touch the editors. Pass the route's record id into the props they already have, so the page keeps working until Tasks 4-6 replace them one editor at a time:

- `<GreetingEditor … focus={rid} focusNonce={0} … />`
- `<PCEditor … focus={rid} focusNonce={0} … />`
- `<CharacterGrid … resetSignal={0} … />`

Their effects depend on `focus` itself, so a changed `rid` still re-fires; the nonce being constant is what those tasks remove.

`EntityEditor`'s `nav` carried **two** things, and dropping the second one is
how "start a lore entry owned by Seraphine" silently becomes a blank unowned
form. Build both halves:

```ts
  /** `?owner=` is only meaningful on a recordless Lore screen: it pre-owns the
   *  blank form. Read nowhere else, so it cannot ride along to Items or sit
   *  beside a record that already has owners of its own. */
  const newOwner = section === "lore" && !rid ? (params.get("owner") ?? "") : "";

  const navFor = (kind: RecordSection) =>
    section !== kind ? null
      : rid ? { kind, focusEntry: rid }
      : newOwner ? { kind, newOwner }
      : null;
```

…and keep passing `nav={navFor("lore")} onNavConsumed={() => {}}` at the six
`EntityEditor` call sites. Task 4 replaces both props; until then this is what
keeps the owner chip working.

- [ ] **Step 9: Keep `section` in the counts effect**

The counts effect (line 236) lists `section` in its dependencies **on purpose** — leaving a section is the first moment the column can hear about a record created in it. `section` is now derived rather than state, so the line is unchanged; confirm it still reads `[campaign, cid, wid, groups, section, populated]`.

- [ ] **Step 10: Run the tests and watch them pass**

Run: `cd frontend && npx vitest run src/routes/WorldView.test.tsx`
Expected: PASS, including the "world read once, counts re-read" case.

- [ ] **Step 11: Commit**

```bash
git add frontend/src/routes/WorldView.tsx frontend/src/routes/WorldView.test.tsx
git commit -m "Read a world's open section off its URL

The column's rows are links now, so a section can be bookmarked, opened in
a new tab and stepped back through. An unknown section and the campaign
root redirect rather than rendering a page whose heading lies about what it
is showing.

?section= becomes readable but never written, and it is translated in the
same breath as those redirects rather than after them: the old address
satisfies the campaign root's default too, and a chain that answered that
one first would land on Characters with the record thrown away.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: `EntityEditor` selects from the route

**Files:**
- Modify: `frontend/src/components/EntityEditor.tsx:354-368` (props), `:530-550` (the nav effect), `:856-893` (the row), `:912-913` (`+ New`)
- Modify: `frontend/src/index.css:1088` (`.row`)
- Modify: `frontend/src/routes/WorldView.tsx` (the six `<EntityEditor>` call sites)
- Test: `frontend/src/components/EntityEditor.test.tsx`

**Interfaces:**
- Consumes: `sectionHref` from Task 1; `rid` from Task 3.
- Produces: `EntityEditor` takes `selected: string | null` and `sectionPath: string` in place of `nav` and `onNavConsumed`.

- [ ] **Step 1: Write the failing test**

Append to `frontend/src/components/EntityEditor.test.tsx`:

```tsx
test("a record row is a link to that record's address", async () => {
  renderEditor({ kind: "items", selected: null });
  const row = await screen.findByRole("link", { name: /Salt Pact/ });
  expect(row).toHaveAttribute("href", "/worlds/realm/items/the-salt-pact");
});

test("an unowned blank form can be pre-owned by the chip that opened it", async () => {
  renderEditor({ kind: "lore", selected: null, newOwner: "characters:seraphine" });
  expect(await screen.findByRole("button", { name: /Seraphine/ })).toBeInTheDocument();
});

test("leaving a record for the section root does not get reopened by its own late read", async () => {
  const slow = deferred();
  (api.readEntity as any).mockImplementationOnce(() => slow.promise);
  const { rerender } = renderEditor({ kind: "items", selected: "the-salt-pact" });
  rerender(editorWith({ kind: "items", selected: null }));
  slow.resolve(entityFixture("the-salt-pact", "The Salt Pact"));
  await screen.findByRole("link", { name: /New item/ });
  expect(screen.queryByRole("heading", { name: "The Salt Pact" })).toBeNull();
});

test("+ New is a link back to the section root", async () => {
  renderEditor({ kind: "items", selected: "the-salt-pact" });
  expect(await screen.findByRole("link", { name: /New item/ }))
    .toHaveAttribute("href", "/worlds/realm/items");
});

test("the selected record opens read-only", async () => {
  renderEditor({ kind: "items", selected: "the-salt-pact" });
  await screen.findByRole("heading", { name: "The Salt Pact" });
  expect(screen.queryByRole("textbox")).toBeNull();
});

test("a later selection wins even when the earlier read lands last", async () => {
  const slow = deferred(); const fast = deferred();
  (api.readEntity as any)
    .mockImplementationOnce(() => slow.promise)
    .mockImplementationOnce(() => fast.promise);
  const { rerender } = renderEditor({ kind: "items", selected: "a" });
  rerender(editorWith({ kind: "items", selected: "b" }));
  fast.resolve(entityFixture("b", "Bee"));
  slow.resolve(entityFixture("a", "Ay"));
  await screen.findByRole("heading", { name: "Bee" });
  expect(screen.queryByRole("heading", { name: "Ay" })).toBeNull();
});

test("the same id under a changed scope reads the new scope's record", async () => {
  const { rerender } = renderEditor({ kind: "items", selected: "the-salt-pact" });
  await screen.findByRole("heading", { name: "The Salt Pact" });
  rerender(editorWith({ kind: "items", selected: "the-salt-pact",
                        scope: { kind: "campaign", id: "run" } }));
  await waitFor(() => expect((api.readEntity as any).mock.calls.at(-1)[0])
    .toEqual({ kind: "campaign", id: "run" }));
});
```

```tsx
test("re-selecting the record already open leaves an unsaved draft alone", async () => {
  const props = { kind: "items" as const, selected: "the-salt-pact" };
  const { rerender } = renderEditor(props);
  await screen.findByRole("heading", { name: "The Salt Pact" });
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  fireEvent.change(await screen.findByLabelText(/Name/), { target: { value: "Half written" } });
  const reads = (api.readEntity as any).mock.calls.length;
  rerender(editorWith(props));              // the same address, asked for again
  expect((api.readEntity as any).mock.calls.length).toBe(reads);
  expect(screen.getByLabelText(/Name/)).toHaveValue("Half written");
});

test("...and Cancel is still the control that discards it", async () => {
  renderEditor({ kind: "items", selected: "the-salt-pact" });
  await screen.findByRole("heading", { name: "The Salt Pact" });
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  fireEvent.change(await screen.findByLabelText(/Name/), { target: { value: "Half written" } });
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  await screen.findByRole("heading", { name: "The Salt Pact" });
  expect(screen.queryByDisplayValue("Half written")).toBeNull();
});
```

These two are the pair the spec calls a deliberate behaviour change: clicking
the rail row you are already reading used to re-read and silently discard the
draft, and under a URL it does nothing. The second test is what stops that
reading as "edits can no longer be thrown away".

Three helpers, all local to this suite — a helper one suite uses belongs in that
suite, not in `testkit/`:

```tsx
const DEFAULTS = {
  wid: "realm", scope: { kind: "world" as const, id: "realm" },
  sectionPath: "/worlds/realm/items",
  recordHref: (r: string) => `/worlds/realm/items/${r}`,
};
const editorWith = (p: Partial<Props> & { kind: EntityKind }) => (
  <MemoryRouter><EntityEditor {...DEFAULTS} {...p} /></MemoryRouter>);
const renderEditor = (p: Partial<Props> & { kind: EntityKind }) => render(editorWith(p));

function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => { resolve = r; });
  return { promise, resolve };
}
```

`entityFixture(id, name)` mirrors whatever the suite's existing `readEntity`
mock resolves. `sectionPath`/`recordHref` sit in `DEFAULTS` so a test names only
what it is about; the `kind: "lore"` cases override both.

**`<Link>` needs a router.** Every render here goes inside a `MemoryRouter` or
it throws — that is what `editorWith` is for.

- [ ] **Step 2: Run it and watch it fail**

Run: `cd frontend && npx vitest run src/components/EntityEditor.test.tsx`
Expected: FAIL — no `link` role; `selected` is not a prop.

- [ ] **Step 3: Swap the props**

`EntityEditor` imports nothing from `react-router-dom` today, so add what the
rows now need at the top of the file:

```ts
import { Link } from "react-router-dom";
```

Then, in `EntityEditor`'s signature (line 354), replace `nav` and `onNavConsumed` with:

```ts
  /** The record the URL names, or null for the section's own screen — which
   *  is the blank new-record form this editor already opens on. */
  selected?: string | null;
  /** An owner to pre-fill a blank form with (`characters:seraphine`), from
   *  `?owner=`. Only ever set when `selected` is null: it belongs to the form,
   *  not to a record that has owners of its own. */
  newOwner?: string;
  /** This section's own address, for `+ New`. */
  sectionPath: string;
  /** ...and one record's, for a rail row. A CALLBACK rather than a string this
   *  file concatenates onto `sectionPath`: `sectionHref` is the only thing
   *  allowed to build these paths, and an id containing a slash or a space is
   *  exactly what hand-joining gets wrong. */
  recordHref: (rid: string) => string;
```

Replace the nav effect (lines 530–550) with:

```ts
  // Whatever the route names.
  //
  // The token is bumped on EVERY change, the null one included. Without that,
  // navigating /items/a -> /items resets the form and then A's slower read
  // lands and reopens A at an address that names no record.
  useEffect(() => {
    const req = ++readReq.current;
    if (selected) void select(selected, req);
    else resetForm(newOwner);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected, newOwner, scope.kind, scope.id]);
```

`select` takes the token it was started with rather than minting its own, so
the two callers cannot disagree about which read is current:

```ts
  async function select(id: string, req = ++readReq.current) {
    // ...unchanged body...
    const e = await api.readEntity(scope, kind, id);
    if (req !== readReq.current) return;   // the scope moved on, or a later select won
```

`resetForm` gains the owner argument, replacing the branch the old `nav` effect
held:

```ts
  function resetForm(owner = "") {
    // ...unchanged...
    setOwners(owner ? [owner] : []);
```

- [ ] **Step 4: Make the rows and `+ New` links**

The row (line 856) becomes a `<Link>` with its children unchanged:

```tsx
  const row = (e: EntitySummary) => (
    <Link key={e.id}
          className={"row" + (e.has_image ? " loc-row" : "") + (editing === e.id ? " active" : "")}
          to={recordHref(e.id)}>
```

…closing with `</Link>`. `+ New` (line 913) becomes:

```tsx
        <Link className="primary new" to={sectionPath}>+ New {label}</Link>
```

The module **template** rows (line 948) stay `<button>`: they preview a record that does not exist in this world and has no id to address.

- [ ] **Step 5: Measure `.row` before changing it**

`index.css` has no global `button { font: inherit }` and `.row` sets no font
family, so `button.row` renders in the browser's default button font while
`a.row` would inherit the body serif. The values have to be **read off a running
page, not guessed** — a UA button default is not a number to reproduce from
memory, and this step is what keeps "no appearance changes" true rather than
intended.

Launch the app with the `verify` skill (isolated store, mocked provider — the
real `~/.grimoire` is untouched), open a world's Items section, and in the
browser console run:

```js
const s = getComputedStyle(document.querySelector(".editor-list .row"));
JSON.stringify({ fontFamily: s.fontFamily, fontSize: s.fontSize,
                 lineHeight: s.lineHeight, textAlign: s.textAlign }, null, 2);
```

Save the output to the scratchpad and screenshot the section. **Do not proceed
with a placeholder** — the measured values go straight into the next step, so
no commit in this plan ever carries one.

- [ ] **Step 6: Pin it**

At `index.css:1088`, with the numbers from Step 5 substituted literally:

```css
.row {
  display: flex; align-items: center; gap: 6px; width: 100%;
  border: 1px solid var(--rule-soft); padding: 8px 10px; margin-bottom: 6px;
  background: var(--surface); color: var(--ink);
  /* Pinned because this class is worn by an anchor now: a <button> took its
     type from the UA and an <a> takes it from the body, and the difference is
     visible. `.column-row` has always pinned its own, which is why converting
     THAT one needed no CSS at all. */
  font-family: /* measured */; font-size: /* measured */; line-height: /* measured */;
  text-align: left; text-decoration: none; cursor: pointer;
}
```

Reload the verify instance and re-run the snippet: every property must match
what Step 5 recorded, and `textDecorationLine` must read `none` in both.

- [ ] **Step 7: Update the six call sites**

In `WorldView.tsx`, each `<EntityEditor … nav={…} onNavConsumed={…} />` becomes:

```tsx
<EntityEditor wid={wid} scope={scope} kind="items"
              selected={section === "items" ? rid : null}
              sectionPath={sectionHref(scopeForPaths, { kind: "section", at: "items" })}
              recordHref={(r) => sectionHref(scopeForPaths,
                                             { kind: "record", at: "items", rid: r })}
              onReclassified={openEntity} onOpenOwner={openOwner} module={moduleCtx} />
```

Lore is the one that also takes the owner, and keeps its `key={loreReset}`:

```tsx
<EntityEditor key={loreReset} wid={wid} scope={scope} kind="lore"
              selected={section === "lore" ? rid : null}
              newOwner={newOwner}
              sectionPath={sectionHref(scopeForPaths, { kind: "section", at: "lore" })}
              recordHref={(r) => sectionHref(scopeForPaths,
                                             { kind: "record", at: "lore", rid: r })}
              onReclassified={openEntity} onOpenOwner={openOwner} module={moduleCtx} />
```

Delete `navFor` and the `newOwner` derivation's `nav` wrapper from Task 3 Step 8 — `newOwner` itself stays, as its own prop. Only the mounted section renders, so `section === kind` is belt-and-braces; keep it, because it is what makes a nav aimed at Lore unable to reach Items if a future change mounts two.

- [ ] **Step 8: Run the tests**

Run: `cd frontend && npx vitest run src/components/EntityEditor.test.tsx src/routes/WorldView.test.tsx`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add frontend/src/components/EntityEditor.tsx frontend/src/components/EntityEditor.test.tsx \
        frontend/src/routes/WorldView.tsx frontend/src/index.css
git commit -m "Select an entity from the route rather than a pushed nonce

The rail's rows and its + New are links, so a record can be opened in a new
tab. The nav handshake goes; the request token that makes an overtaken read
lose stays, because a URL changes neither the hazard nor its fix.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: `GreetingEditor`, and the derived list/graph view

**Files:**
- Modify: `frontend/src/components/GreetingEditor.tsx:20-40` (props), `:211` (`select`), `:431-465` (the rail)
- Modify: `frontend/src/routes/WorldView.tsx:566-605` (the greetings section)
- Test: `frontend/src/components/GreetingEditor.test.tsx`, `frontend/src/routes/WorldView.test.tsx`

**Interfaces:**
- Consumes: `sectionHref`; `rid` and `section` from Task 3.
- Produces: `GreetingEditor` takes `selected` and `sectionPath`; `greetingView` is derived, not state.

- [ ] **Step 1: Write the failing tests**

```tsx
// GreetingEditor.test.tsx
test("a greeting row is a link", async () => {
  renderGreetings({ selected: null });
  expect(await screen.findByRole("link", { name: /Tide Watch/ }))
    .toHaveAttribute("href", "/worlds/realm/greetings/tide-watch");
});

test("leaving a greeting for the section root survives its own late read", async () => {
  const slow = deferred();
  (api.readGreeting as any).mockImplementationOnce(() => slow.promise);
  const { rerender } = renderGreetings({ selected: "tide-watch" });
  rerender(greetingsWith({ selected: null }));
  slow.resolve(greetingFixture("tide-watch", "Tide Watch"));
  await screen.findByRole("link", { name: /New greeting/ });
  expect(screen.queryByRole("heading", { name: "Tide Watch" })).toBeNull();
});

// WorldView.test.tsx — uses `renderWithRouter` from Task 3, not history.back()
test("the plot map is an address, and Back returns to the list", async () => {
  const router = renderWithRouter("/worlds/w/greetings");
  fireEvent.click(await screen.findByRole("button", { name: "Plot map" }));
  await waitFor(() => expect(lastPath + lastSearch).toBe("/worlds/w/greetings?view=graph"));
  fireEvent.click(await screen.findByRole("button", { name: "List" }));
  await waitFor(() => expect(lastSearch).toBe(""));
  await router.navigate(-1);
  await waitFor(() => expect(lastSearch).toBe("?view=graph"));
  expect(await screen.findByTestId("plot-map")).toBeInTheDocument();
});

test("a greeting's own address shows the list, which is the only view with a detail pane", async () => {
  renderAtUrl("/worlds/w/greetings/tide-watch?view=graph");
  await screen.findByRole("heading", { name: "Tide Watch" });
  expect(screen.queryByTestId("plot-map")).toBeNull();
});

test("switching to the map and back does not lose a half-written greeting", async () => {
  renderAtUrl("/worlds/w/greetings");
  fireEvent.change(await screen.findByLabelText(/Name/), { target: { value: "Half written" } });
  fireEvent.click(screen.getByRole("button", { name: "Plot map" }));
  await waitFor(() => expect(lastSearch).toBe("?view=graph"));
  fireEvent.click(screen.getByRole("button", { name: "List" }));
  expect(await screen.findByLabelText(/Name/)).toHaveValue("Half written");
});
```

- [ ] **Step 2: Run them and watch them fail**

Run: `cd frontend && npx vitest run src/components/GreetingEditor.test.tsx src/routes/WorldView.test.tsx -t "greeting|plot map"`
Expected: FAIL.

- [ ] **Step 3: Swap `focus`/`focusNonce` for `selected`**

`GreetingEditor` has no `react-router-dom` import either — add
`import { Link } from "react-router-dom";` at the top of the file.

In `GreetingEditor`'s signature, replace `focus`/`focusNonce` with
`selected?: string | null`, `sectionPath: string` and
`recordHref: (rid: string) => string` — the same three `EntityEditor` took, and
`recordHref` for the same reason: `sectionHref` is the only thing that builds
these paths.

`GreetingEditor` has no request ref today (`select` takes a `still()`
predicate its caller supplies), so add one — `const selReq = useRef(0)` — and
bump it on **every** selection change, null included:

```ts
  useEffect(() => {
    const req = ++selReq.current;
    if (selected) void select(selected, () => req === selReq.current);
    else resetForm();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected, scope.kind, scope.id]);
```

Without the bump on the null branch, leaving `/greetings/tide-watch` for
`/greetings` resets the form and then Tide Watch's slower read lands and
reopens it at an address naming no greeting.

Delete the `focusNonce` docstring; its reason ("opening the same greeting twice
is two events") no longer holds, and Task 3's commit records the replacement.

The rail's rows (line 434) become `<Link className={"row" + …} to={recordHref(g.id)}>`, children unchanged; `+ New greeting` becomes `<Link className="primary new" to={sectionPath}>`.

- [ ] **Step 4: Derive the view in `WorldView`**

Delete the `greetingView` state. Add:

```ts
  /** Which rendering of the greetings the route asks for.
   *
   *  A record segment forces the list: the graph has no detail pane, so
   *  `/greetings/tide-watch?view=graph` would be a URL contradicting what it
   *  renders. Derived rather than held, or the chips and the Back button
   *  disagree within three clicks — push ?view=graph, click List, press Back,
   *  and a graph URL renders the list. */
  const greetingView: "list" | "graph" =
    !rid && params.get("view") === "graph" ? "graph" : "list";
```

The two chips navigate instead of setting state:

```tsx
onClick={() => navigate(sectionHref(scopeForPaths,
  key === "graph" ? { kind: "section", at: "greetings", view: "graph" }
                  : { kind: "section", at: "greetings" }))}
```

They stay `<button aria-pressed>` — a `role="group"` of toggles, not two links — because the editor is kept mounted-but-`hidden` behind the graph so a half-written greeting survives the switch.

The `GreetingEditor` call site gains the same two props as the entity editors:

```tsx
<GreetingEditor scope={scope} wid={wid} onOpenCharacter={openCharacter}
                onOpenLocation={(id) => openEntity("locations", id)}
                selected={section === "greetings" ? rid : null}
                sectionPath={sectionHref(scopeForPaths, { kind: "section", at: "greetings" })}
                recordHref={(r) => sectionHref(scopeForPaths,
                                               { kind: "record", at: "greetings", rid: r })}
                onChanged={() => setGreetingEpoch((n) => n + 1)}
                onBusy={setListSaving} onEdgeDraft={setListEdgeDraft}
                hold={mapWriting ? "The plot map is still writing these links. Wait for it before saving." : null}
                refreshKey={mapEpoch} />
```

- [ ] **Step 5: Do not let the view change remount the editor**

Leave `<div hidden={greetingView !== "list"}>` exactly as it is, and give `GreetingEditor` no `key` that includes the view. This is the draft-loss failure mode and it is invisible until someone loses one.

- [ ] **Step 6: Run the tests**

Run: `cd frontend && npx vitest run src/components/GreetingEditor.test.tsx src/routes/WorldView.test.tsx`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/GreetingEditor.tsx frontend/src/components/GreetingEditor.test.tsx \
        frontend/src/routes/WorldView.tsx frontend/src/routes/WorldView.test.tsx
git commit -m "Address a greeting, and derive which view of them is showing

The plot map is a URL now rather than a flag, so Back through the switch
renders what the address says. A record segment forces the list, since the
graph has no detail pane to contradict it with.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: `PCEditor` selects from the route

**Files:**
- Modify: `frontend/src/components/PCEditor.tsx:18-30` (props), `:85-88` (the focus effect), `:309-331` (the rail)
- Modify: `frontend/src/routes/WorldView.tsx:537` (the call site)
- Test: `frontend/src/components/PCEditor.test.tsx`

**Interfaces:**
- Consumes: `sectionHref`; `rid` from Task 3.
- Produces: `PCEditor` takes `selected` and `sectionPath`.

- [ ] **Step 1: Write the failing test**

```tsx
test("a PC row is a link to that PC's address", async () => {
  renderPCs({ selected: null });
  expect(await screen.findByRole("link", { name: /Winifred/ }))
    .toHaveAttribute("href", "/worlds/realm/pcs/winifred");
});

test("the selected PC opens", async () => {
  renderPCs({ selected: "winifred" });
  await screen.findByRole("heading", { name: "Winifred" });
});

test("the section root closes whoever was open", async () => {
  const { rerender } = renderPCs({ selected: "winifred" });
  await screen.findByRole("heading", { name: "Winifred" });
  rerender(pcsWith({ selected: null }));
  await screen.findByRole("button", { name: /New PC/ });
  expect(screen.queryByRole("heading", { name: "Winifred" })).toBeNull();
});

test("a later selection wins even when the earlier read lands last", async () => {
  const slow = deferred(); const fast = deferred();
  (api.readPC as any)
    .mockImplementationOnce(() => slow.promise)
    .mockImplementationOnce(() => fast.promise);
  const { rerender } = renderPCs({ selected: "winifred" });
  rerender(pcsWith({ selected: "mara" }));
  fast.resolve(pcFixture("mara", "Mara"));
  slow.resolve(pcFixture("winifred", "Winifred"));
  await screen.findByRole("heading", { name: "Mara" });
  expect(screen.queryByRole("heading", { name: "Winifred" })).toBeNull();
});
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd frontend && npx vitest run src/components/PCEditor.test.tsx`
Expected: FAIL — no `link` role.

- [ ] **Step 3: Swap the props and the rows**

`PCEditor` has no `react-router-dom` import — add
`import { Link } from "react-router-dom";` at the top of the file.

Replace `focus`/`focusNonce` with `selected?: string | null` and
`recordHref: (rid: string) => string`. There is no `sectionPath` prop here: the
`+ New PC` button is **not** a navigation (see below), so nothing in this file
needs the section's own address.

The effect at line 85 becomes — note the `else`, which the current code has no
equivalent of because nothing could previously un-focus a PC:

```ts
  useEffect(() => {
    const req = ++selReq.current;
    if (selected) void select(selected, undefined, req);
    else { setDetail(null); setVid(""); setPersona(BLANK); setMode("view"); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected, scope.kind, scope.id]);
```

…and `select` takes the token it was started with rather than minting its own,
so navigating `/pcs/winifred` → `/pcs` cannot be undone by Winifred's late read:

```ts
  async function select(pid: string, version?: string, req = ++selReq.current) {
    setError(null);
    const d = await api.readPC(scope, pid);
    if (req !== selReq.current) return;
```

The row at line 314 becomes a `<Link className={"row" + …} to={recordHref(p.id)}>`
with its `Portrait` and `row-name` spans untouched.

**`+ New PC` stays a `<button>`.** `newPC` (line 127) prompts for a name,
`POST`s a PC and then opens it — it creates a record rather than going to a
screen, so there is no address to put in an href. This is the one place the
editors differ from each other, and it differs because the control does.

- [ ] **Step 4: Update the call site**

```tsx
{section === "pcs" && <PCEditor scope={scope} wid={wid} onOpenLore={openLore}
                                selected={rid}
                                recordHref={(r) => sectionHref(scopeForPaths,
                                                               { kind: "record", at: "pcs", rid: r })}
                                module={moduleCtx} />}
```

`WorldView`'s render-time `pcFocus` scope derivation goes with this: it existed so a stale id could not reach a new scope a render early, and a route carries its own scope.

- [ ] **Step 5: Run the tests**

Run: `cd frontend && npx vitest run src/components/PCEditor.test.tsx src/routes/WorldView.test.tsx`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/PCEditor.tsx frontend/src/components/PCEditor.test.tsx \
        frontend/src/routes/WorldView.tsx
git commit -m "Select a PC from the route

Takes the scope derivation with it: it existed so a stale id could not reach
a new scope a render early, and a route carries its own scope.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: A character card opens in a new tab

**Files:**
- Modify: `frontend/src/components/CharacterGrid.tsx:32-38` (props), `:503` (the card), `:482` (the unlinked row), `:115-145` (`resetSignal`)
- Modify: `frontend/src/index.css:1457` (`.char-card-main`)
- Modify: `frontend/src/routes/WorldView.tsx:536` (the call site)
- Test: `frontend/src/components/CharacterGrid.test.tsx:99,115,393`

**Interfaces:**
- Consumes: `characterHref` from Task 2.
- Produces: `CharacterGrid` no longer takes `resetSignal`.

- [ ] **Step 1: Rewrite the three navigation tests as link assertions**

`CharacterGrid.test.tsx` lines 99, 115 and 393 currently click and assert `lastLocation`. A click still works on a `<Link>`, so keep those, and add:

```tsx
test("a character card is a link, which is what lets it open in a new tab", async () => {
  await renderGrid();
  expect(await screen.findByRole("link", { name: /Seraphine/ }))
    .toHaveAttribute("href", "/worlds/realm/characters/seraphine");
});

test("a campaign's card points inside that campaign's world copy", async () => {
  await renderGrid({ scope: { kind: "campaign", id: "run" } });
  expect(await screen.findByRole("link", { name: /Seraphine/ }))
    .toHaveAttribute("href", "/campaigns/run/world/characters/seraphine");
});
```

- [ ] **Step 2: Run them and watch them fail**

Run: `cd frontend && npx vitest run src/components/CharacterGrid.test.tsx`
Expected: FAIL — no `link` role.

- [ ] **Step 3: Make the card a link**

`CharacterGrid` imports `useNavigate` at line 2 but not `Link`; add `Link` to
that same import. Line 503 becomes:

```tsx
<Link className="char-card-main" to={characterHref(scope, c.id)}>
```

…closing with `</Link>`, children untouched. The unlinked-version row at line 482 becomes `<Link className="subtle" to={characterHref(scope, u.character, u.version)}>`. The three remaining `navigate(characterHref(…))` calls at lines 155, 210 and 371 are *post-action* navigations (after a create, an import, a wizard) — they stay `navigate`, because there is no anchor for a reader to click.

- [ ] **Step 4: Preserve the card's appearance**

`.char-card-main` already sets `font-family: var(--fb)`, so only the underline is at risk. At line 1457 append `text-decoration: none;` to the existing declaration block and change nothing else.

- [ ] **Step 5: Drop `resetSignal`**

Remove the prop, its type and its use in the `[scope.kind, scope.id, resetSignal]` dependency list at line 138. Keep `reveal` — "reveal a character the filter is hiding" is a different question from "which record is open", and `CharacterPage`'s back link still passes it through `location.state`.

- [ ] **Step 6: Run the tests**

Run: `cd frontend && npx vitest run src/components/CharacterGrid.test.tsx src/routes/WorldView.test.tsx`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/CharacterGrid.tsx frontend/src/components/CharacterGrid.test.tsx \
        frontend/src/index.css frontend/src/routes/WorldView.tsx
git commit -m "Open a character card in a new tab

The one screen on this page that already had a real URL still could not be
ctrl-clicked, because the card was a button that called navigate.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: The remaining producers

**Files:**
- Modify: `frontend/src/routes/SearchView.tsx:65-77` (`hitTo`), `:419` (the hit button)
- Modify: `frontend/src/routes/CharacterPage.tsx:691-725` (four chip hrefs)
- Modify: `frontend/src/routes/CampaignHub.tsx:545-546`, `:572`
- Modify: `frontend/src/components/WorldOverview.tsx:24-25` (`onNavigate`), `:79`, `:91`
- Modify: `frontend/src/shell/rail.ts:267-294` (the Images row)
- Test: `frontend/src/routes/SearchView.test.tsx:98,146-163`, `frontend/src/shell/rail.test.ts:154,170,189`

**Interfaces:**
- Consumes: `sectionHref` from Task 1.
- Produces: no `?section=` is emitted anywhere in the app.

- [ ] **Step 1: Update the existing assertions and add the rail's**

In `SearchView.test.tsx`, change lines 98 and 146–163 to the new forms:

```tsx
expect(hitTo(hit())).toBe("/worlds/realm/lore/the-salt-pact");
expect(hitTo(campaignHit())).toBe("/campaigns/run/world/lore/the-salt-pact");
expect(hitTo(charHit("veiled"))).toBe("/worlds/realm/characters/seraphine?v=veiled");
expect(hitTo(charHit())).toBe("/worlds/realm/characters/seraphine");
expect(hitTo(campaignCharHit())).toBe("/campaigns/run/world/characters/seraphine");
```

In `rail.test.ts`, change lines 154 and 170, and add the one the row's own comment has been waiting for:

```ts
expect(images().to(ctx, WITH_WORLD)).toBe("/worlds/saltmarch/images?for=c1");
expect(images().to(ctx, anon)).toBe("/worlds/saltmarch/images");

test("the Images row lights on the screen it points at, now that it is one", () => {
  expect(images().match("/worlds/saltmarch/images", ctx)).toBe(true);
  expect(images().match("/worlds/saltmarch/lore", ctx)).toBe(false);
});
```

Add a test that no producer emits the old vocabulary:

```tsx
test("nothing in the app mints a ?section= link any more", () => {
  for (const href of [hitTo(hit()), charactersHref(W), characterHref(W, "mira")]) {
    expect(href).not.toContain("section=");
  }
});
```

…and one per producer this task touches, because a wrong href is invisible
until somebody follows it. In `CampaignHub.test.tsx`:

```tsx
test("the cast card points at the campaign's own world copy", async () => {
  await renderHub();
  expect(await screen.findByRole("link", { name: /Everyone/ }))
    .toHaveAttribute("href", "/campaigns/run/world/characters");
});

test("a face points at that character inside it", async () => {
  await renderHub();
  expect(await screen.findByRole("link", { name: "Seraphine" }))
    .toHaveAttribute("href", "/campaigns/run/world/characters/seraphine");
});
```

In `WorldOverview.test.tsx` (create it if the component has no suite):

```tsx
test("a tile is a link to its section", async () => {
  render(<MemoryRouter><WorldOverview wid="realm" hrefFor={(t) => `/worlds/realm/${t}`} /></MemoryRouter>);
  expect(await screen.findByRole("link", { name: /Locations/ }))
    .toHaveAttribute("href", "/worlds/realm/locations");
});

test("a checklist row is a link, and a row with no section is not", async () => {
  render(<MemoryRouter><WorldOverview wid="realm" hrefFor={(t) => `/worlds/realm/${t}`} /></MemoryRouter>);
  expect(await screen.findByRole("link", { name: /Has a location/ }))
    .toHaveAttribute("href", "/worlds/realm/locations");
  expect(screen.queryByRole("link", { name: /Calendar confirmed/ })).toBeNull();
});
```

In `CharacterPage.test.tsx`, the chips:

```tsx
test("a lore chip points at the entry, and the new-entry chip pre-owns the form", async () => {
  await renderWorld();
  expect(await screen.findByRole("link", { name: /The Salt Pact/ }))
    .toHaveAttribute("href", "/worlds/realm/lore/the-salt-pact");
  expect(screen.getByRole("link", { name: /New lore/ }))
    .toHaveAttribute("href", "/worlds/realm/lore?owner=characters%3Aseraphine");
});
```

- [ ] **Step 2: Run them and watch them fail**

Run: `cd frontend && npx vitest run src/routes/SearchView.test.tsx src/shell/rail.test.ts`
Expected: FAIL on every changed assertion.

- [ ] **Step 3: `hitTo`**

Replace the tail of `hitTo` (lines 71–77) with:

```ts
  const scope: EntityScope = hit.scope === "world"
    ? { kind: "world", id: hit.root } : { kind: "campaign", id: hit.root };
  const section = hit.kind === "state" || hit.kind === "dossier" ? "characters" : hit.kind;
  if (!INDEX_SECTIONS.has(section)) return base;
  // A hit is a record, so it points AT the record — a section carrying an id
  // was the nearest the old vocabulary could get.
  //
  // The cast is on the whole object, not on `at`. `SectionTarget` splits
  // characters (which may carry `v`) from every other kind, so an `at` that is
  // still the union matches neither member; narrowing the property alone does
  // not narrow which member was meant.
  return section === "characters" && hit.sub
    ? sectionHref(scope, { kind: "record", at: "characters", rid: hit.id, v: hit.sub })
    : sectionHref(scope, { kind: "record", at: section, rid: hit.id } as SectionTarget);
```

`SearchView` already imports `Link` at line 2. The hit itself (line 419) becomes `<Link className="search-hit" to={hitTo(hit)}>` — a search result is the thing a reader most wants to open beside what they are reading.

- [ ] **Step 4: `CharacterPage`'s four chips**

Replace each hand-built string (lines 691–725) with `sectionHref`:

```ts
  const loreHref = (id: string) =>
    sectionHref(scope, { kind: "record", at: "lore", rid: id });
  const newLoreHref = () =>
    sectionHref(scope, { kind: "section", at: "lore", newOwner: `characters:${eid}` });
  const greetingHref = (gid: string) =>
    sectionHref(scope, { kind: "record", at: "greetings", rid: gid });
```

The `worldScope ? … : …` branches all collapse — `sectionHref` is the thing that knows which base a scope has.

- [ ] **Step 5: `CampaignHub`**

Line 545: `to={sectionHref({ kind: "campaign", id: cid }, { kind: "section", at: "characters" })}`.
Line 572: `who.kind` is `"characters" | "pcs"`, so the same whole-object cast as `hitTo` applies — it is a union across two members of `SectionTarget`, and neither matches until the object is narrowed:

```tsx
to={sectionHref({ kind: "campaign", id: cid },
                { kind: "record", at: who.kind, rid: who.id } as SectionTarget)}
```

- [ ] **Step 6: `WorldOverview`**

`WorldOverview` has no `react-router-dom` import — add
`import { Link } from "react-router-dom";` at the top of the file.

Change the prop from `onNavigate: (tab: string) => void` to `hrefFor: (tab: string) => string`, and the tiles and check rows from `<button onClick={…}>` to `<Link className="overview-tile" to={hrefFor(t.tab)}>` / `<Link className={"check-row" + …} to={hrefFor(c.tab!)}>`. The static check row (no `tab`) stays a `<span>`. Check `index.css` for `.overview-tile` and `.check-row`: if either omits `text-decoration`, add `text-decoration: none` and nothing else. `WorldView` passes `hrefFor={(t) => hrefFor(t as Section)}`.

- [ ] **Step 7: The rail's Images row**

```ts
    to: (_ctx, s) => (s?.campaign?.world
      ? sectionHref({ kind: "world", id: s.campaign.world },
                    s.campaign.id
                      ? { kind: "section", at: "images", forCampaign: s.campaign.id }
                      : { kind: "section", at: "images" })
      : null),
    match: (p, _ctx, s) => {
      const w = s?.campaign?.world;
      // Through the helper rather than a template string, so an id needing
      // encoding matches the href the row itself emitted. Compared on the
      // pathname alone -- `?for=` is part of the question, not the screen.
      return !!w && isUnder(p, sectionHref({ kind: "world", id: w },
                                           { kind: "section", at: "images" }));
    },
```

Delete the "Never lit, deliberately" comment block and replace it with one line saying the row lights now that Images has a route — which is exactly what that comment said would happen. If `match`'s signature has no shell-payload parameter, thread one: the row needs the world's id, and it already reads it in `to`.

- [ ] **Step 8: Sweep for stragglers**

Run: `cd frontend && grep -rn "section=" src --include=*.ts --include=*.tsx | grep -v worldPaths`
Expected: only `worldPaths.ts`'s `legacyTarget` and its tests. Anything else is a producer that was missed.

- [ ] **Step 9: Run the frontend suite**

Run: `cd frontend && npx vitest run`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add frontend/src/routes/SearchView.tsx frontend/src/routes/SearchView.test.tsx \
        frontend/src/routes/CharacterPage.tsx frontend/src/routes/CharacterPage.test.tsx \
        frontend/src/routes/CampaignHub.tsx frontend/src/routes/CampaignHub.test.tsx \
        frontend/src/components/WorldOverview.tsx frontend/src/components/WorldOverview.test.tsx \
        frontend/src/shell/rail.ts frontend/src/shell/rail.test.ts \
        frontend/src/routes/WorldView.tsx frontend/src/index.css
git commit -m "Point every producer at the new addresses

A search hit points at the record rather than at a section carrying its id,
and the rail's Images row lights on the screen it points at — which its own
comment said would follow the day Images earned a route.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: The gate, and the appearance check that only a browser can make

**Files:**
- Modify: `lint-baselines/*.json` if a gate reports a stale count

**Interfaces:**
- Consumes: everything above.
- Produces: a green `make check` and evidence that nothing moved on screen.

`.row`'s typography was measured and pinned in Task 4 and `.char-card-main`'s
underline fixed in Task 7; this task confirms the whole set against `main`
rather than one class at a time.

- [ ] **Step 1: Capture both sides**

Launch the `verify` skill on `main`, open a world, and in the browser console:

```js
const pick = (sel) => { const el = document.querySelector(sel); if (!el) return null;
  const s = getComputedStyle(el);
  return { fontFamily: s.fontFamily, fontSize: s.fontSize, lineHeight: s.lineHeight,
           textAlign: s.textAlign, textDecorationLine: s.textDecorationLine }; };
JSON.stringify({ row: pick(".editor-list .row"), card: pick(".char-card-main"),
                 columnRow: pick(".column-row"), back: pick(".column-back"),
                 tile: pick(".overview-tile"), check: pick(".check-row"),
                 hit: pick(".search-hit") }, null, 2);
```

`pick` answers `null` for anything not on screen rather than throwing, because
these seven live on four different screens: run it on **Items** (`.row`), the
**Characters** grid (`.char-card-main`), the **Overview** (`.overview-tile`,
`.check-row`) and a **search results** page (`.search-hit`), and merge the four
readings. `.column-row` and `.column-back` appear on all of them.

Screenshot each of those four screens. Relaunch on the branch and repeat.

- [ ] **Step 2: Diff them**

Expected: every property identical on both sides, with `textDecorationLine`
reading `none` everywhere. A difference here is a bug in this work, not a value
to accept — the whole change promised no appearance change. Screenshots
likewise.

- [ ] **Step 3: Drive the feature in the browser**

- Right-click a column row → the context menu offers **Open link in new tab**; take it, and the new tab lands on that section.
- Right-click a character card → same, landing on the character.
- Middle-click a record row in Items → opens in a background tab.
- Press Back repeatedly from a record → record → section → section → the world.
- Paste an old `/worlds/<id>?section=lore&id=<entry>` URL → lands on `/worlds/<id>/lore/<entry>` and the address bar shows the new form.

- [ ] **Step 4: Run the full gate**

Run: `make check`
Expected: green. If `check-eslint`, `check-lint` or `check-mypy` reports a stale count because this work *resolved* findings, run `make baseline` and commit the smaller file with the change.

- [ ] **Step 5: Commit, if the gate moved a baseline**

```bash
git add lint-baselines
git commit -m "Re-baseline the lint gates after the routing change

Resolving a finding fails a ratcheted gate exactly as adding one does, so
the smaller file lands with the change that shrank it.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 6: The implementation gate**

Per `CLAUDE.md`, before this is considered complete: run `/codex:review` against the diff, then `/codex:adversarial-review` against the diff *and* `docs/superpowers/specs/2026-09-15-world-section-routes-design.md`, asking specifically whether the changes implement the spec. Note that the Codex sandbox helper is missing on this machine, so both gates silently review GitHub instead of the local diff — pipe the diff to `codex exec --sandbox read-only --skip-git-repo-check` instead, telling it its shell is broken and it must review only stdin.

---

## Self-Review

**Spec coverage.** Route table → Task 2. Section root as the new-record screen → Tasks 4-6, one editor each (the `+ New` links, and the null-`selected` branch that blanks the form). Splat mounting → Task 2 Step 5. Tail parsing and allowlists → Task 1. Campaign root redirect above the `!wid` guard → Task 3 Step 5. `sectionHref` as sole authority → Tasks 1, 2, 8. What deletes itself → Tasks 3, 4, 5, 6, 7. Stale-read tokens kept → Tasks 4, 5, 6. Re-selection is a no-op, and Cancel still discards → Task 4 Step 1, the last two tests. Anchors table → Tasks 3, 4, 5, 6, 7, 8. Greetings derived view → Task 5. Legacy rules 1–4 → Task 1 (`legacyTarget`) + Task 3's redirect chain. Legacy campaign-character path → Task 2. CSS risks → Tasks 4, 7, 9 (measured and pinned in 4, the card in 7, confirmed in 9). Test list → distributed; every failure case from the spec appears in Task 3 and Task 5.

Canonicalisation → Task 3 Step 5, by building `sectionHref`'s own spelling and comparing, so no list of odd spellings has to be kept complete. `?owner=` survival → Task 3 Step 8 and Task 4. Null-selection races → Tasks 4, 5, 6.

**Placeholders.** None. The `.row` typography is measured in Task 4 Step 5 and written in Step 6 of the same task, so no commit in this plan carries a placeholder value; Task 9 confirms the result rather than supplying it.

**Type consistency.** `selected` and `recordHref` are the prop names in Tasks 4, 5 and 6 and at every call site. `sectionPath` is on `EntityEditor` and `GreetingEditor` only — `PCEditor` has no `+ New` link to point, because `newPC` creates a record rather than going anywhere, and Task 6 says so rather than leaving it to judgment. `SectionTarget`, `Section`, `RecordSection`, `Shape`, `ParsedTail` are spelled the same in Task 1's module and every consumer. `hrefFor` is `WorldView`'s local builder in Task 3 and the prop name in Task 8 Step 6. `renderWithRouter` is defined in Task 3 and reused in Task 5.

**Every task lands green.** Task 2 repairs the one assertion elsewhere that its change invalidates rather than deferring it; Task 3 keeps `loreReset` and feeds the editors their existing props; Task 4's CSS is measured before it is written. There is no commit in this plan that knowingly leaves a red suite.
