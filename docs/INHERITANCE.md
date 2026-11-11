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
- **Art** attaches to an **entity**, across its revisions ([IMAGES](IMAGES.md)), so it inherits **live**. When the catalog Mara gets a new portrait, every portrayal, and every individual that follows its source, shows it at once in any slot it has not overridden.
- **A pinned chat's actor pins its pictures too** (the owner's call). New art reaches it only through the same reviewed rebase as card text ([Pinned art](#pinned-art)).

## Inspecting a portrayal: `grimoire.effective.inspect_definition(view, ref)`

This call composes three things from one `LibraryView`: the revision, its pinned base, and the base entity's current head. It returns an `Inspection(ref, base, base_head, fields, notices)`. `base_head` is set only when the head is a different revision than `base`. Each field is a `FieldView(field, state, source, upstream)`, keyed by 022's provenance names: `name`, `description`, `section:<name>`, `facet:<name>`.

| `state` | Meaning |
|---|---|
| `inherited` | The value comes from the base chain, and the pinned base is its entity's head. |
| `pinned` | The value comes from the base chain, but the base has a newer head that this revision does not follow until a rebase. |
| `overridden` | This revision replaces an inherited value (022 `replaced`). |
| `restated` | This revision repeats the inherited value. It no longer follows the base. |
| `local` | This revision authors a value the base never had. A standalone definition is all `local`. |
| `cleared` | This revision clears an inherited value with a typed clear. A clear made further up the chain is `inherited` (or `pinned`), and its `source` is the revision that cleared it. |
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

  A standalone definition inherits nothing: it may `set`, mark `unresolved` or `hide` its slot. An `adjust` or `inherit` there is `mode_not_allowed`. Its `hide` reaches what inherits from it like any other placement.
- **Origins.** `Resolution.origin(slot)` gives an `ArtOrigin(slot, origin, placed_by)`:

  | `origin` | Meaning |
  |---|---|
  | `local` | The subject's own `set` or `hide`, over nothing inherited. |
  | `replaced` | The subject's own `set`, over an inherited slot. |
  | `cleared` | The subject's own `hide`, over an inherited slot. |
  | `adjusted` | The subject's own `adjust` of the inherited picture (it may be `stale_adjust` or `nothing_to_adjust`). |
  | `missing` | The subject's own `unresolved`. |
  | `broken` | The subject's own placement cannot be read, is not allowed where it sits, or sits in a damaged layer (`invalid_placement`, `unsupported_format`). A readable `set` whose picture is missing keeps its `local` or `replaced` origin, and its slot is `Unavailable` with the reason. |
  | `inherited` | An ancestor's placement decides the slot. `placed_by` names that ancestor. |

- **Instances.** An individual layers its derivation entity's whole lineage beneath its campaign placements. A world's clear reaches its instances, and a campaign may re-crop any picture its lineage shows.
- **It fails closed.**
  - A missing, excluded, unlinked or mismatched base inherits **nothing**, with the reason as a problem. A same-named entity's picture is never shown instead.
  - A damaged portrayal layer is treated like a damaged campaign layer: every slot it would inherit, and any `adjust` or `inherit` in it, is `Unavailable(invalid_placement, "portrayal layer damaged")`.
  - An `adjust` that names a picture other than the one inherited is refused `stale_adjust` when written. If the base later changes its picture, the `adjust` goes `stale_adjust` and shows nothing.

## Pinned art

The store keeps no older placement, because a placement is rewritten in place. So a pinned actor keeps a **snapshot**: its instance file's `art_pin` holds every placement of its pinned base revision's lineage, as it read when the actor was pinned. Image objects and blobs are create-only, so the pictures a snapshot names stay the same pictures.

- **Who writes it.**
  - `ChatLibrary` writes the snapshot when it makes a new actor for a pinned binding. A pinned binding that reuses an actor with no snapshot for its own `base` (a following chat made it, or an older build did) writes one too, of the art the actor shows now, in the same commit and guarded by the same etag.
  - A [reviewed rebase](REBASE.md#rebase-review-sourcerebasereviewactor-targetnone) writes the snapshot its review showed, never newer art.
  - An actor that follows its head gets no snapshot. A rebase drops one only when no pinned chat binds the actor any more.
  - One actor can be bound by pinned and following chats at once. It then shows pinned art in all of them, so no pinned chat's pictures move unreviewed. The following chats see new art with each reviewed rebase instead.
  - `grimoire.imagestore.freeze_art(store, base)` takes the snapshot of one pinned revision.
- **What it shows.**
  - The instance's campaign layer, live, over the frozen layers. A campaign may still hide, set or re-crop any slot. A campaign `adjust` names the frozen picture the actor shows, not the entity's live one (`stale_adjust` otherwise).
  - Only what a slot shows is kept: mode, role, image, crop, focus and expression, and an `unresolved` slot's note. Open provenance and unknown fields are left out, so a deep or large placement can never make the instance file unwritable.
  - The lineage is the pinned revision's own `derivation` chain, which never changes, not the head's.
- **It is data, and it fails closed.**
  - The resolver re-checks every frozen placement as it checks a live one: its mode, and that a `set` names its own entity's source (`wrong_scope` otherwise).
  - A snapshot that cannot be read inherits nothing (`art_pin_unreadable`). So does one whose entities are not the pinned revision's chain (`art_pin_mismatch`), so a namesake's picture is never shown.
  - A frozen layer's `derived` flag is clamped, never trusted: a layer may hide, re-crop or reset only where its revision's chain lets it (every member but the root, and the root only while its own head is derived).
  - A hand-edited snapshot can still show any picture of a source in the chain, in any slot of that source's layer. That is accepted: anyone who can edit the instance file can already set such a picture in its campaign layer.
- **When it is ignored.**
  - A snapshot taken for another `base` is ignored, and the actor's art is live again. This happens when a build older than this one rebased the actor and kept the field.
  - A build older than this one keeps the field but ignores it, so it shows live art.
- **Review.** `RebaseReview.art` lists each slot that would show something different: its `before` and `after` slot. When only the art changed, a review of the actor's own revision is allowed. It has no field rows and re-pins no chat. `up_to_date` now means that neither the revision nor the art changed. A pinned actor with no snapshot for its own `base` can also be reviewed at its own revision: the review has no rows and no art changes, and applying it pins the art the actor shows now.

## Answers to the sprint's open questions

- **Which fields inherit live and which must snapshot?** Fields snapshot through the pinned base revision. Art is live per entity, except for a pinned chat's actor, which keeps a snapshot until a reviewed rebase ([Pinned art](#pinned-art), the owner's call).
- **What is the semantics of explicit null?** Null never clears, and blank text never clears. A clear is always typed: `clear_sections`, `clear_facets`, `clear_description`, or a placement `hide`. A `derivation: null` is not a derivation: 022 refuses it, it inherits no art, and the edit helpers refuse it `not_derived`.
- **How are deprecated source values shown?** As `upstream: removed` on the field that still shows the pinned value. A clear of a value the head dropped says `removed` too.

## Limits

- **Pinned art.**
  - An actor made before this build, or by a V1 or card import, has no snapshot. Its art stays live until a pinned chat binds it again or a review pins it (a review of no change is allowed for this).
  - An actor shared by pinned and following chats shows pinned art in the following chats too.
  - A lineage with more than 1024 placements is not pinned, so its art stays live.
  - A review adopts all of the new art or none of it. A slot cannot keep its old picture while the rest move.
- **Sync.** [Device sync](SYNC.md#art) holds a derived portrayal's `adjust` and `inherit`, a campaign `inherit`, and a campaign `adjust` on an actor with pinned art: each shows what the base chain (or the snapshot) shows, which sync does not compare yet. Every other derived placement syncs like any source placement.
- **Instance fields.** An individual still has no field-level override of description, sections or card extras ([REBASE](REBASE.md) `not_overridable`). Keeping a campaign's own voice, greeting or system prompt when a card changes is not built.
- **Not in this slice:**
  - versioned sheets, mechanics baselines and nested content (milestone 3);
  - an inspector UI;
  - multiple bases and follow-head derivation (022).
- **Facet resets.** Resetting a facet keeps 022's first-slice rule: a facet the revision keeps that requires the reset one must be restated by its author.
