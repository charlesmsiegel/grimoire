# World authoring proposals: suggested records reviewed and accepted in part

**Status:** first slice of the [World Authoring Assistant](roadmap/sprints/world-authoring-assistant.md) (Sprint 087, Wave 9), milestones 1 and 2. It is implemented as:
- `grimoire.authoring`: pure values (a request, a proposal of candidates and links, a decision) and the provider-backed proposer;
- `grimoire.authoringstore`: the store-backed `AuthoringLibrary`, which gives the proposer a world's context, reviews a proposal against the world, and writes only the accepted subset through a [World Change Set](CHANGESETS.md) (043).

There is no app or UI (that is [116](roadmap/sprints/wave-9-ui-exit.md)). Tests use a scripted transport and synthetic worlds; no live model is called.

## What it means

- A **proposal** is a bounded set of suggested records (**candidates**) and **links** between them. Nothing in it is stored or canon. It lives only in memory until an author accepts part of it.
- Each candidate has a **role**: `individual` (one particular town or guild), `template` (a generic kind, such as "a fishing village"), or `brainstorm` (a loose idea). A brainstorm is never stored, and neither is anything the author does not accept: a rejected idea leaves no file, record or index entry.
- A **review** reads the world and reports, before anything is accepted, which candidates share a name with an existing record or with another candidate (**duplicates**), and which links point at a record that has gone, moved or is not a place (**link problems**).
- An author's **decision** accepts some candidates. A duplicate is accepted only when the decision names it as `distinct`; otherwise it is surfaced, never created and never merged.
- The accepted candidates and the links among them (and to existing places) are written together, all or nothing, as one change set the author sees byte for byte before it applies.

## Values (`grimoire.authoring`)

| Value | Fields and limits |
|---|---|
| `AuthoringRequest(request_id, source, brief, constraints=(), max_candidates=8)` | `source` is a `world:` source; any other (a shared character, an add-on) is `invalid_source`, since every world including it would carry the records. Up to 16 distinct constraint lines and 1 to 16 candidates. |
| `Candidate(key, kind, name, role, description="")` | `kind` is `location` or `organization` (the first record types formalized here). `key` is a segment of at most 60 characters, unique in its proposal. |
| `Link(link_id, relation, subject, object)` | `road` (a bidirectional way between two places) or `within` (the subject place lies in the object place). `subject` is a candidate key; `object` is a candidate key or a **pinned** `SourceRef` to an existing record of the same world. |
| `Proposal(proposal_id, request, candidates, links=(), rationale="", origin="unattributed")` | Up to 48 links. Both endpoints of a link are places; neither is a `template` (a generic kind of place is on no map). At most one road per pair, one parent per place, and no containment cycle. |
| `Decision(accept, decided_by, distinct=())` | `accept` names at least one candidate; `distinct` names accepted duplicates the author keeps on purpose. |

`AuthoringError(reason)` carries a reason and no retained text. Refusals of the values include `unsupported_kind`, `invalid_role`, `invalid_key`, `unpinned_endpoint`, `foreign_endpoint`, `self_link`, `unknown_endpoint`, `endpoint_not_place`, `template_endpoint`, `duplicate_road`, `two_parents`, `containment_cycle`, `candidate_bound`, `link_bound` and `nothing_accepted`.

**Namesakes.** `name_key(name)` folds a name (NFKC, case, accents, a possessive `'s`, a plural `s`, and the words "the", "a", "an", "of") into a sorted word tuple, so "The Lamplighters' Guild", "Lamplighter's Guild", "Guild of the Lamplighters" and "Lamplighters Guild" share a key, as do "Café Noir" and "Cafe Noir", while "Brine Wick" and "Brinewick" do not. It leans to flagging, because a false match costs one `distinct` mark and a missed one stores a second record. It flags likely duplicates for a person to judge; it is not identity.

`select(proposal, decision, duplicates, problems)` is the pure step from a decision to what will be written. It refuses `brainstorm_not_storable`, `duplicate_unacknowledged` and `distinct_not_flagged`, keeps the links whose endpoints were all accepted or exist, and drops the rest with a reason (`rejected_endpoint`, or the review's link problem).

## The proposer

`AuthoringGenerator(adapter, routes, handle, *, approvals, policy, max_output_tokens=4096)` runs one optional PLANNING call per `propose(request, context)`, metered and approved per attempt exactly like [preparation](PREPARATION.md) generation. Its handle must be untrusted, and a policy that budgets no planning call sends nothing (`not_budgeted`). `meters` keeps each call's booked attempts.

- The system message holds only fixed instructions. The brief, constraints and the world's existing records go in one JSON user message as data.
- `context.records` is what the model may link to: each existing record's pinned ref, kind and name (at most 200, locations first, with `records_truncated`).
- The reply is decoded as one JSON object with exactly the keys `candidates`, `links` and `rationale`, and each candidate and link with exactly its own keys. `parse_proposal` rebuilds it into a `Proposal`: a link object containing `:` must be a ref copied exactly from the offered context (`unknown_record` otherwise), so a model cannot invent a record, a revision or another world. Any refusal rejects the whole reply (`unparseable`, `invalid_shape`, `invalid_text`, or the value's own reason).
- The proposal's origin is `model:planning`. Proposals may also be built directly, for example by a person or a future deterministic generator.

## The service (`grimoire.authoringstore`)

`AuthoringLibrary(store, handle)` is trusted application code; its writes go through `ChangeSetLibrary` with the same handle and authority.

| Call | Notes |
|---|---|
| `context(source, *, limit=200)` | The world's current heads as an `AuthoringContext`, with a basis digest. Topology records are not offered. A blank world title is offered as none and a long one is cut; a record's name is its envelope name, else a typed payload's `name`; the limit applies before names are cut to 256 characters, so one long name never refuses the context. Refusals: `missing_source`, `excluded_source`. |
| `review(proposal)` | A `ProposalReview` of `duplicates` (against existing heads of the same kind, by name key or by holding the id the candidate's name would take, and between candidates) and link `problems` (`missing_endpoint`, `endpoint_moved`, `not_a_place`, and `template_endpoint` for an existing typed place whose nature is `template`). Writes nothing. |
| `draft(review, decision, *, topology=None)` | Under the store lock, judges the world again (`stale_review` if anything the review reported changed, or if the map moves or is edited in place while the draft is built), selects, and builds one change set from that one read. Returns an `AcceptanceDraft` holding the change-set review (`.changes`) and `.applicable`. |
| `approve(draft, authority, *, approved_by, new_id)` | 043's digest-bound approvals; only from a human's direct action on the shown draft. |
| `apply(draft, approvals=None)` | Writes exactly the drafted files, or raises 043's refusal and writes nothing. Returns `AcceptedRecords(proposal_id, records, topology, changeset)`. |
| `reject(draft, authority=None)` | Ends the draft, writing nothing. |

What a draft writes:
- **One `r1` entity per accepted candidate.** Its payload is a `grimoire.entity/0` definition (a location gets the `Spatial` facet; an individual or template nature from its role) and the envelope declares that schema, so the record is read by entities, effective views and source APIs like any hand-made one. Its extras record `authoring: {proposal, request, suggested_by, accepted_by}`, and the change set's evidence says the same. The entity id is the name's slug, made unique case-insensitively across every kind in the source and every place any of its maps names, even one whose record is gone (`brinewick-2`).
- **One topology revision ([059](TOPOLOGY.md)) for the kept links.** It uses the topology named by `topology=`, otherwise the world's only one, otherwise a new `map`; two or more without a name refuse `ambiguous_topology`. Existing places and paths are kept at their own pins; each road becomes a bidirectional path `<proposal>-<link>`, and each `within` sets the subject's parent. A draft with no kept links writes no topology.
- **Preconditions** pinning every existing record a kept link relies on, whether or not the map already holds it, at the revision and bytes the draft validated, so an edit to one (even during the draft) conflicts at apply and nothing is written. A manifest edited while the draft is built refuses it as `stale_review`.
- The source manifest, as 043 always writes it.

Failure behavior: a conflict, stale manifest or denied write writes nothing (043). A rejected or never-accepted candidate leaves nothing to clean up because nothing was ever written.

## Change Set extension

`RecordChange` gains an optional `schema` (at most 128 characters, encoded only when set), so a created record can declare its envelope schema. A revision keeps its base's schema; naming one on a revise is `invalid_field`. See [CHANGESETS](CHANGESETS.md).

## Boundaries and deferrals

| Not here | Owner |
|---|---|
| Campaign individuals (campaign-scoped instances). Only world records are written | 087 later; campaign writes go through [accepted turn effects](EFFECTS.md) |
| Creatures, lore and other record kinds; links beyond `road` and `within` | 087 open question: which types to formalize |
| Milestone 3: larger linked regions with seeds and controlled provenance | 087 milestone 3 |
| Persisting pending proposals across restarts | 087 later, with 043 milestone 3 |
| Canon conflicts beyond namesakes (contradicting facts) | 087 open question |
| Retrieval of more than names and kinds into the prompt | [Catalog Composition](roadmap/sprints/catalog-composition.md) and later 087 work |
| The review UI | [116](roadmap/sprints/wave-9-ui-exit.md) |

A map revision moves the head of the world topology; campaign path overrides stay with the revision they were made against (059), and the draft does not yet tell the reviewer about them.

`context`, `review` and `draft` load the whole library view, as other first slices do; a large library pays that cost per call.
