# Source-qualified identity contract

**Status:** first slice implemented in the pure core (`grimoire.core.identity`, `grimoire.core.index`). It is an in-memory contract; no on-disk schema is frozen. The reference **text forms** below are what later storage writes into Markdown/JSON. Roadmap: [Source-Qualified Identity](roadmap/sprints/source-qualified-identity.md).

A name, a legacy V1 slug or an inherited portrayal is never identity. Two definitions with the same name and no explicit link are unrelated.

## Identifier grammar

- **Segment:** `[a-z0-9][a-z0-9._-]*`, at most 128 characters. Lowercase only, so two IDs never differ only by case. This is not a filesystem-path guarantee: mapping IDs to paths belongs to the [native file store](roadmap/sprints/native-file-store-and-revisions.md).
- **Source:** one or more segments joined by `:`. Sources are **opaque**, and prefixes such as `world:` or `catalog:` are conventions only, so a world can become a reusable library without changing any ID. The first segment may not be a reserved scheme (`v1`, `campaign`).
- **Kind:** an open, stable namespace (`character`, `item`, `location`, …). Kind is part of identity and does not change when entity facets change. An entity ID is unique within its source across kinds.
- The grammar is enforced by every constructor, not only by parsing. Definition and instance reference text is at most 512 characters; a legacy V1 path may be up to 4096 characters because it must be kept exactly.

## Reference families

| Family | Value | Text form |
|---|---|---|
| Definition | `SourceRef(source, kind, entity_id, revision=None)` | `<source>/<kind>/<entity_id>[@<revision>]` |
| Campaign instance | `InstanceRef(campaign_id, kind, instance_id)` | `campaign:<campaign_id>/<kind>/<instance_id>` |
| Legacy V1 record | `LegacyId(archive_id, path)` | `v1:<archive_id>:<path>` |

The scheme makes the three forms mutually unambiguous. `parse_ref` dispatches on it. Parsing **rejects rather than normalizes**: whitespace, uppercase, non-ASCII, empty segments and stray separators are all errors. So `parse(str(x)) == x` exactly.

`CampaignActor.ref` is the actor's `InstanceRef`. Campaign and actor IDs on actors, control assignments, events, observations and beliefs obey the segment grammar.

## Revisions, pins and portrayals

- **Every definition is one revision:** its `ref` is pinned. Revision-less text exists only for references.
- **Pinned** (`…@r2`): exactly that revision, or a failure.
- **Unpinned:** follows the latest, meaning the entity's **declared head**. A single revision is its own head. Several revisions require a declared head.
- Revisions are entity-scoped and opaque, and no order is inferred from their spelling. A source *release* is manifest data that produces a heads map, so pinning a campaign to a release means freezing a heads map.
- Resolution reports the concrete revision it used (`ActorView.resolved_source`), so callers can record what an unpinned reference meant at that moment. Persisting the base revision belongs to the file store and [source rebase](roadmap/sprints/source-rebase-and-semantic-merge.md) sprints.
- **Portrayals are separate entities** joined by `alternate_portrayal`. Revisions are only one entity's edit history. Do not map V1 `<version>.json` portrayals to revisions.

## Definition index

`DefinitionIndex(sources, definitions, heads, links)` is immutable and read-only. It accepts any immutable definition with a pinned `.ref` and a `.name`: non-frozen dataclasses are rejected, and a definition whose ref is mutated after registration is never returned.

- `resolve(ref)` returns the definition. Otherwise it raises `MissingDefinition` with `.reason`: `missing_source`, `missing_entity` or `missing_revision`.
- `check(ref)` returns a `BrokenReference` or `None`, and `check_links()` reports link endpoints that do not resolve. Core never rewrites or drops a broken reference. Persisting broken-reference information is the file store's job.
- `find_by_name(name, kind=None)` matches names in any revision after NFKC + casefold + whitespace collapse. It returns sorted unpinned **candidates** and never makes a selection.
- `links_for(ref)` lists explicit links. Links never change what `resolve` returns.
- Registration problems raise `IdentityConflict(ValueError)`, and `.sources` names the sources to exclude. Covered problems:
  - a duplicate exact ref (no silent overwrite);
  - an entity ID reused across kinds;
  - a definition or link from an unregistered source;
  - an unpinned definition;
  - a missing or unknown head (heads are the entity's own source's declaration, so its source is blamed);
  - an invalid, duplicate or cyclic link (blaming the declarer of the offending link only).

  Excluding the named sources lets the rest build, so a broken add-on cannot make a user's world unresolvable. The one exception is two packages claiming the same source ID: no reference can tell them apart, and one must be renamed.

  **This holds only if the loader admits each package under its own source id.** The index trusts the `source`, `declared_by` and heads it is given. An add-on that ships a `world:faerun/...` revision or head would otherwise be blamed on, or redirect, Faerûn's own references. The loader obligation belongs to the [Native File Store](roadmap/sprints/native-file-store-and-revisions.md#inherited-from-wave-1).

## Explicit links

`DefinitionLink(subject, relation, object, declared_by)` takes `declared_by`, a registered source, as provenance.

| Relation | Direction | Endpoints |
|---|---|---|
| `same_individual` | symmetric | pinned or unpinned |
| `alternate_portrayal` | symmetric | pinned or unpinned |
| `derived_variant` | subject derived from object | **both pinned**: this revision, and its base |
| `overrides` | subject overrides object | **both pinned** |

Each derived revision states its own base, so a re-derivation is a new subject revision with a new base. A revision may list several bases.

The following are rejected:
- self-links;
- the same link declared twice by one source. Symmetric links are compared by unordered entity pair, directed links by their exact refs. When *different* sources state the same link, that is agreement: both are kept as provenance;
- cycles among directed links, checked between pinned revisions. Forks that sync across revisions (`b@v1` from `a@r1`, then `a@r2` from `b@v1`) are not a cycle. Only the link that first closes a cycle, in registration order, is blamed.

Links join definitions only. Instance relations belong to the [typed relationship graph](roadmap/sprints/typed-relationship-graph.md).

## Legacy V1 IDs

A `LegacyId` is an explicit archive ID plus an exact source-relative POSIX path, with case and Unicode form kept. The following paths are rejected rather than normalized:
- absolute paths;
- paths containing `\`, control, surrogate, format (e.g. zero-width) or line/paragraph-separator characters;
- paths with empty, `.` or `..` parts, or a trailing `/`.

`LegacyIdMap` maps legacy IDs to **pinned** V2 refs, the imported revision. The rules:
- many-to-one mappings and identical repeats are allowed;
- mapping one legacy ID to two refs is an `IdentityConflict` that blames no source, because the mapping itself is at fault;
- an unmapped ID raises `UnmappedLegacyId`;
- there is no lookup by slug or name.

Known review hazards for the migration sprints, none of them resolved automatically:
- V1 file names containing format characters (zero-width joiners in emoji, direction marks, soft hyphens) cannot get a `LegacyId` in this slice; the importer must list them for review instead of guessing;
- NFC and NFD spellings of one V1 path produce two IDs;
- the same slug can appear under `pcs/` and under `worlds/<w>/characters/`;
- campaign overlay copies of world records;
- alias or name-only references in V1 relations and ledgers.

## Not yet covered

- Fork, import and export round trips. Those subsystems do not exist in V2 yet; this slice proves the text-form round trip they rely on.
- Persisted manifests for sources, links and heads.
- Catalog include/exclude.
- Owner semantics beyond a label.
