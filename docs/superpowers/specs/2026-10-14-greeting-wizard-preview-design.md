# Greeting availability before campaign creation (#68)

The campaign wizard should show which openings match the character being
chosen, while that choice is still a draft. Today its only availability read
happens after campaign creation. The user approved adding a world preview,
a typed client, and a step-2 display with stale-response protection and tests.

## Contract

- Add `GET /api/worlds/{wid}/greeting-availability?tags=a,b` in
  `routes/greetings.py`. Keep it outside the greeting-ID namespace: an existing
  greeting named Availability must remain readable at `/greetings/availability`.
- Resolve the world through `_world_root_or_404`. Read its greeting items and
  plot map, and call `store.greetings.availability` with empty played/skipped
  sets and the supplied, trimmed, nonempty tag IDs. Return its rows unchanged.
  Missing worlds answer 404; empty worlds answer an empty list.
- This is a read-only calculation. It creates no campaign, PC, scene, marks,
  or vocabulary records and makes no model calls.
- Tag matching uses IDs, never labels. The world vocabulary supplies suggestions;
  campaign draft tags may remain free strings, exactly as they are today.
  Commas and surrounding spaces have the same meaning as in persisted PC tags:
  `pcs._write_meta` joins with commas and `pcs._tags_of` splits and strips them.
  A draft tag containing a comma therefore previews its eventual stored tags.
- Preserve the engine's predecessor, exclusion, and required-tag semantics.
  Matching tags alone must not unlock a greeting with unplayed predecessors.
  The preview carries `pcless` and location like the engine; offscreen rows are
  labelled as such, rather than presented as a promise about scene seating.

## Wizard and client

- Add `api.previewGreetings(wid: string, tags: string[])` and a preview response
  type containing the engine's fields, without fictitious campaign marks,
  `unlocked`, or recommendations. Existing campaign availability keeps its type.
- Step 2 previews the selected world PC's tags, or the new PC's draft tags
  when no existing PC is selected. Deselecting restores the draft's preview.
- A small read-only `GreetingAvailabilityPreview` component owns the request
  and display. Debounce changes by 250 ms. Key its lifetime by world and
  effective tags, so a changed choice immediately stops displaying old rows;
  cleanup cancels unsent requests and ignores late successes and failures.
- Show loading, no-greetings, and request-failure states. A failure must not
  read as an empty world or prevent wizard navigation. Show available openings
  and blocked openings, including each blocked row's reasons.
- World PC and vocabulary list reads must ignore late answers after switching
  worlds; otherwise the preview could use another world's PC tags. Clear those
  lists and the picked PC when the world changes.
- Step 4 continues to read actual campaign availability and start greetings in
  the existing way. Previewing never commits the draft or selects an opener.

## Scope and verification

No role-tag namespace, character-as-player tags (#18), storage migration, or
PC editor integration. No added dependencies; Python remains 3.11+, Node 18+,
and pydantic usage remains v1/v2 compatible. Fixtures use the repository's
placeholder names and every backend route test uses an isolated store.

Tests cover empty and missing worlds; a greeting whose ID is `availability`;
exact tag subsets and IDs; predecessor
blocking; read-only effects; client URL encoding; new/existing/deselected PCs;
debouncing; changing worlds/tags during a pending response; unmount cleanup;
and failures distinct from an empty result. Run focused tests first, then
`make check` with the main checkout's Python interpreter supplied explicitly.
Follow the spec, plan, implementation, and final spec-conformance Codex gates.

## Theory note

Availability is a projection of a world plus proposed player tags and no play
history. Reuse the existing engine instead of teaching TypeScript a second
matching rule. The UI owns only request lifetime and presentation; the next
consumer can use the same component and endpoint. Each settled choice costs
one greeting-list/plot-map read. The main risk is an old world's response
being presented as the current character's preview.
