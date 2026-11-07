# Live inheritance and overrides (046)

A portrayal or an individual inherits its source's defaults until one of its own properties is explicitly
changed. Nothing is copied: every shown value names the revision that authored it, and nothing is ever
matched by name. This slice covers two things.

- **Fields:** an inspector, and one-field edits, for [022](EFFECTIVE_VIEWS.md) derived portrayals.
- **Art:** live, slot-by-slot inheritance of [placements](IMAGES.md) down a portrayal's base chain.

Sprint: [live-inheritance-and-overrides](roadmap/sprints/live-inheritance-and-overrides.md).

## Fields follow the pinned base; art follows the entity

The two kinds of value inherit differently, on purpose.

- **Fields** (name, description, sections, facets) come from 022's composition over a **pinned** `derivation.base`. A newer base revision never reaches a portrayal on its own. The inspector shows it as a source diff, and a reviewed rebase ([REBASE](REBASE.md), 042) re-pins.
- **Art** attaches to an **entity**, across its revisions ([IMAGES](IMAGES.md)), so it inherits **live**. When the catalog Mara gets a new portrait, every portrayal and individual that has not overridden that slot shows it at once.

Pinning art to a revision for pinned chats is not in this slice ([Limits](#limits)).

## Inspecting a portrayal: `grimoire.effective.inspect_definition(view, ref)`

This call composes three things from one `LibraryView`: the revision, its pinned base, and the base entity's current head. It returns an `Inspection(ref, base, base_head, fields, notices)`. `base_head` is set only when the head is a different revision than `base`. Each field is a `FieldView(field, state, source, upstream)`, keyed by 022's provenance names: `name`, `description`, `section:<name>`, `facet:<name>`.

| `state` | Meaning |
|---|---|
| `inherited` | The value comes from the base chain, and the pinned base is its entity's head. |
| `pinned` | The value comes from the base chain, but the base has a newer head that this revision does not follow until a rebase. |
| `overridden` | This revision replaces an inherited value (022 `replaced`). |
| `restated` | This revision repeats the inherited value. It no longer follows the base. |
| `local` | This revision authors a value the base never had. A standalone definition is all `local`. |
| `cleared` | This revision clears an inherited value with a typed clear. |
| `missing` | Only the base's head has this value. It arrives with a rebase. `source` is that head. |

`source` is the pinned revision that authored, or cleared, the shown value.

`upstream` compares the pinned base with the base's head for that field:

| `upstream` | Meaning |
|---|---|
| `same` | The head has the same value. |
| `changed` | The head has a different value. |
| `removed` | The head no longer has the value. A deprecated source value is shown, never dropped. |
| `added` | Only the head has the value. |
| `""` | There is nothing to compare: the definition is standalone, or its base is the head. |

**Refusals.**

- A portrayal that does not compose raises 022's `PortrayalError`, such as `unlinked_derivation`, `missing_base` or `stale_clear`.
- A head that cannot be composed leaves `base_head` unset and adds a `base_head_unavailable` notice.
- The pure `grimoire.core.inheritance.inspect(leaf, base, base_head)` raises `ValueError` for a base that is not the leaf's own `chain[1]`, or for a head of another entity. It never compares a portrayal with another entity.

## Editing one field: `grimoire.core.inheritance`

Each helper takes a derived payload and returns an independent copy for the **next** revision, changed in exactly one field. The caller writes that copy as a new revision and links it, so the core definition and every other portrayal are untouched.

| Helper | Next revision |
|---|---|
| `override_section(p, name, text)` | Replaces section `name` with `text`, and drops any clear of it. |
| `clear_section(p, name)` | Clears inherited section `name`. |
| `reset_section(p, name)` | Inherits section `name` again: its own value and its clear are removed. |
| `override_description(p, text)`, `clear_description(p)`, `reset_description(p)` | The same three, for the description. |
| `clear_facet(p, name)`, `reset_facet(p, name)` | Clear a facet, or inherit it again. |

They raise `OverrideError` with a `reason`:

- `not_derived`: the payload has no `derivation` object, including an explicit `null`;
- `invalid_field`: a name that is not a valid segment, or blank override text.

Text rules and the facet rules stay 022's and are checked when the revision composes. A clear of a value the base lacks is written, and then refused on compose as `stale_clear`, never applied silently.

## Art down a base chain

Before this slice, a source placement could only `set` or mark a picture `unresolved`, and a derived portrayal inherited no art. Now:

- **Lineage.** An entity's lineage is read from its **head** revision's own `derivation`, through 022's `derivation_chain`, so art and fields agree about ancestry. Every ancestor's current placements are layered beneath the entity's own, root first. A head with no `derivation` key is standalone, exactly as before, and costs no library load. `Resolution.lineage` lists the entities, root first.
- **Modes.** A derived portrayal's placement may:
  - `set` a picture of its own source;
  - `hide` a slot (a clear);
  - `adjust` the inherited picture (layer `source_adjusted`);
  - mark a slot `unresolved`;
  - `inherit`, which resets the slot to whatever its base chain shows. The store has no deletes, so this is how a slot goes back to inheriting.

  A standalone definition still may only `set` or mark `unresolved`. A `hide` or `inherit` there is `mode_not_allowed`.
- **Origins.** `Resolution.origin(slot)` gives an `ArtOrigin(slot, origin, placed_by)`:

  | `origin` | Meaning |
  |---|---|
  | `local` | The subject's own `set` or `hide`, over nothing inherited. |
  | `replaced` | The subject's own `set`, over an inherited slot. |
  | `cleared` | The subject's own `hide`, over an inherited slot. |
  | `adjusted` | The subject's own `adjust` of the inherited picture (it may be `stale_adjust` or `nothing_to_adjust`). |
  | `missing` | The subject's own `unresolved`. |
  | `broken` | The subject's own placement cannot be shown (`Unavailable` for another reason). |
  | `inherited` | An ancestor's placement decides the slot. `placed_by` names that ancestor. |

- **Instances.** An individual layers its derivation entity's whole lineage beneath its campaign placements. A world's clear reaches its instances, and a campaign may re-crop any picture its lineage shows.
- **It fails closed.**
  - A missing, excluded, unlinked or mismatched base inherits **nothing**, with the reason as a problem. A same-named entity's picture is never shown instead.
  - A damaged portrayal layer is treated like a damaged campaign layer: every slot it would inherit, and any `adjust` or `inherit` in it, is `Unavailable(invalid_placement, "portrayal layer damaged")`.
  - An `adjust` that names a picture other than the one inherited is refused `stale_adjust` when written. If the base later changes its picture, the `adjust` goes `stale_adjust` and shows nothing.

## Answers to the sprint's open questions

- **Which fields inherit live and which must snapshot?** Fields snapshot through the pinned base revision. Art is live per entity. Whether pinned chats should pin pictures too waits on the owner's call, after pin-by-default lands.
- **What is the semantics of explicit null?** Null never clears, and blank text never clears. A clear is always typed: `clear_sections`, `clear_facets`, `clear_description`, or a placement `hide`. A `derivation: null` is not a derivation: 022 refuses it, it inherits no art, and the edit helpers refuse it `not_derived`.
- **How are deprecated source values shown?** As `upstream: removed` on the field that still shows the pinned value. A clear of a value the head dropped says `removed` too.

## Limits

- **Pinned art.** Art follows the entity, not a chat's pinned revision. A pinned chat therefore sees new base art at once.
- **Sync.** [Device sync](SYNC.md#art) holds a derived portrayal's `adjust` and does not copy it yet. Every other derived placement syncs like any source placement.
- **Instance fields.** An individual still has no field-level override of description, sections or card extras ([REBASE](REBASE.md) `not_overridable`). Keeping a campaign's own voice, greeting or system prompt when a card changes is not built.
- **Not in this slice:**
  - versioned sheets, mechanics baselines and nested content (milestone 3);
  - an inspector UI;
  - multiple bases and follow-head derivation (022).
- **Facet resets.** Resetting a facet keeps 022's first-slice rule: a facet the revision keeps that requires the reset one must be restated by its author.
