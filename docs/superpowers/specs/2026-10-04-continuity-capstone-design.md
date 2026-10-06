# Continuity Capstone — persistent narrative graph, reconciliation, temporal pressure, and scene-driver control

**Date:** 2026-10-04 (revised 2026-10-05 after adversarial review)  
**Status:** Design specification. The spec → planning adversarial review has been run, and its findings are resolved in place; Appendix A maps each finding to the section that resolves it. Codex was not available in the session that ran the gate, so the review was carried out by four independent Claude reviewers, each checking one slice of this document against the code. Ready for implementation planning.  
**Branch:** continuity-capstone-specs  
**Supersedes:** no existing continuity feature. This is the post-umbrella capstone built on the completed Scene Lifecycle & Continuity phases documented in docs/superpowers/PHASE-STATUS.md.  
**Primary implementation target:** Claude Code or another coding agent working in this repository.

---

## 0. Read this before changing code

This specification assumes the repository as it exists on 2026-10-04. Before implementation, read in full:

- AGENTS.md
- CLAUDE.md
- CONTRIBUTING.md
- docs/store-guarantees.md
- templates/README.md
- docs/superpowers/PHASE-STATUS.md
- docs/superpowers/specs/2026-06-30-scene-lifecycle-continuity-design.md
- docs/superpowers/specs/2026-08-14-scene-ledger-design.md
- docs/superpowers/specs/2026-07-12-mechanics-phase5-absorb-validation-design.md, the best existing example of a second-pass, reviewable validator beside absorb

The mandatory project workflow still applies:

1. This spec receives /codex:adversarial-review. (Done — see Appendix A.)
2. Findings are resolved in the spec. (Done — see Appendix A.)
3. A concrete implementation plan is written under docs/superpowers/plans/.
4. That plan receives /codex:adversarial-review.
5. Implementation is done with inline TDD and the repository gate.
6. The completed diff receives /codex:review.
7. The completed diff plus this originating spec receives a final /codex:adversarial-review for implementation drift.

Do not skip those gates because this document is already detailed. Where Codex is unavailable, an independent adversarial review that checks claims against the code stands in for it, and the substitution is recorded.

Privacy rules matter especially here. No committed example may use a name, count, event, campaign title, or other fact from the user's real data store. Use the repository's established placeholder names, such as Seraphine, Mara, Winifred, Realm and Saltmarch.

---

# 1. Purpose

Grimoire's continuity subsystem already keeps a useful long-running campaign memory:

- chronicle entries;
- timeline summaries;
- per-character state and knowledge;
- relationships;
- plot threads;
- commitments;
- standing facts;
- campaign changes and journal history;
- scene ideas;
- dates, calendars, holidays, birthdays, and scheduled events;
- scene suggestions;
- pre-scene briefings;
- a scene-state tracker.

The remaining weakness is not that these facts are missing. It is that they are still mostly separate ledgers assembled into prompts. Long campaigns can accumulate:

- semantically duplicate threads;
- commitments that are no longer really open;
- near-identical obligations under different wording;
- time-sensitive story business that is present in the files but not strongly represented when the next scene is chosen.

This capstone turns the existing continuity files into a maintained, inspectable narrative graph without replacing them.

The finished feature must make four things true:

1. **History stays coherent over long campaigns.** New extraction should prefer to attach to existing records. A separate reconciliation system should find likely duplicates, closures, continuations and related records that scene-local extraction missed.

2. **Time matters structurally.** Scene suggestions and continuity review must reason over the campaign date, scheduled events, commitment deadlines, holidays, birthdays and reviewed temporal links. They must use the existing calendar-provider arithmetic, not model date arithmetic.

3. **The reader can steer the next scene by story pressure.** Generated suggestions must say which live narrative drivers they serve. The reader can focus on, avoid or require drivers, and can anchor the batch to meaningful upcoming moments.

4. **The same structure is visible.** A campaign Story Graph shows scenes, actors, places, threads, commitments, events, reviewed links and future scene ideas. It is not a decorative side database: it is a projection of the same structures that suggestions and reconciliation use.

When this capstone is complete, continuity feature development is considered **parked**, except for correctness defects and small usability fixes. Further narrative-analysis ideas belong in a later backlog. The next major product investment should move to mechanics.

---

# 2. Non-goals

This work does **not**:

- replace chronicle.json, plot.json, commitments.json, facts.json, relationships.json, events.json, scene files, or scene_ideas.json with a graph database;
- add SQLite, a vector database, or a compiled nearest-neighbor dependency;
- add a frontend graph, chart or layout library (PlotMapEditor and CostTrend are the precedent for hand-built SVG/HTML drawings);
- make embeddings mandatory;
- make an LLM authoritative over merges, closures, facts, dates, or any store write;
- silently rewrite or delete historical thread/commitment records;
- automatically close a thread because it is old;
- infer that a commitment was fulfilled merely because its deadline passed;
- merge plot threads with commitments;
- make Todo, opening the graph, opening the ledger, or opening the scene chooser launch hidden LLM work. The chooser's existing ranked suggestion call when a scene mode is picked is pre-existing behaviour, not hidden work (§3.10);
- create a numerical “continuity health score”;
- make every lore record a graph node by default;
- solve act/chapter detection, theme detection, literary analysis, or automatic campaign-era labeling;
- replace the current review-before-apply posture;
- make semantic recall depth control continuity embeddings;
- add mechanics-aware scene drivers in this milestone. The graph and driver schema must leave room for them later, but mechanics is the next, separate body of work.

---

# 3. Design principles and hard invariants

## 3.1 Existing files remain the source of truth

The graph is a projection. Plot beats still live in plot.json. Commitments still live in commitments.json. Scene facts still live in chronicle.json and scenes. Relationships still live in relationships.json. Dates still use calendar providers.

New continuity metadata may state that two existing records are aliases or related, but it never becomes a second copy of their actual narrative content.

## 3.2 Embeddings discover; they never assert

Cosine similarity is candidate generation only.

A high cosine score may mean:

- duplicate;
- continuation;
- parent/subthread;
- same characters but a different issue;
- a commitment and thread about the same incident;
- merely topical overlap.

No score, threshold or nearest-neighbor rank may by itself write a merge, close a thread, resolve a commitment, or create a semantic link. This is enforced structurally by a writer guard (§11.6).

## 3.3 No embeddings means degraded recall, not a disabled feature

Every continuity feature except semantic nearest-neighbor recall must still work with no embedding connection configured.

Fallback candidate generation uses deterministic structural and lexical signals. A campaign without embeddings still receives:

- exact/normalized-title matching;
- slug matching;
- token/shingle similarity;
- shared-actor signals;
- shared-scene signals;
- shared reviewed time-anchor signals;
- stale/overdue/target-passed classifications;
- scene-driver controls;
- the Story Graph;
- Todo continuity findings produced by those local signals.

The UI must describe this as “Basic matching active — semantic matching not configured”, never as “continuity disabled”. Every read that a continuity surface renders carries a `matching: "basic" | "semantic"` field so the frontend never has to infer it. `matching` is `"semantic"` iff `store.embed_space.resolve()` is non-None (§9.3).

## 3.4 Reviewed semantics and derived candidates are separate

A possible duplicate is not the same kind of data as an accepted continuation link.

Pending findings are disposable and recomputable.

Reader-approved links, merge aliases and explicit dismissals are durable campaign state. Where they change campaign semantics (aliases and links), they are journalled and reversible. Dismissals (suppressions) are durable but are not journalled; they are restored through their own route (§21).

## 3.5 Merges are non-destructive

A duplicate thread or commitment is never deleted merely because another record becomes canonical.

A reviewed merge creates an alias from the duplicate reference to the canonical reference. The original source record stays in its original file, unchanged.

Every effective reader used for prompts, suggestions, Todo and the Story Graph canonicalizes aliases, so the duplicate does not act like a second live obligation.

Removing the alias restores the previous view with no reconstruction.

## 3.6 Different record types are never merged

A plot thread and a commitment may be strongly related. They remain different things.

A thread advances and closes.

A commitment is owed, and resolves as fulfilled, broken or expired.

Cross-type semantic relationships are links such as pays_off or related_to, never aliases.

## 3.7 Closure requires positive evidence

Staleness can nominate a thread for review. A passed event can nominate a commitment for resolution review. Neither is enough to resolve the object.

A thread closes only after a reviewed closure operation backed by an existing beat or scene, or an explicitly accepted manual decision.

A commitment resolves only to one of its existing resolution states, and only after review.

A merge must not become a back door to closure. Merging an open record into a closed or resolved canonical is refused unless the reviewer explicitly accepts the status change (§5.1).

## 3.8 Timeline math is deterministic

The model does not calculate day offsets, birthdays, holiday dates, or event ordering.

Use CalendarProvider and the existing fixed-day axis (`calendars.fixed_of`). Every before/after comparison is an integer comparison of fixed days on the campaign's **primary** provider.

The model may interpret prose deadlines that deterministic code cannot parse, but it does not manufacture a numeric date for them.

## 3.9 Reads fail soft; writes fail visibly

Graph projection, candidate generation, driver projection and prompt assembly must tolerate a malformed optional continuity file by omitting the affected enhancement.

A write that cannot preserve continuity semantics must reject visibly. It must never silently drop an alias, link, candidate decision or reviewed operation.

## 3.10 Opening a read-only surface costs no model call

These are pure reads:

- Todo;
- Ledger, including its Continuity review section;
- Story Graph, including every lens and toggle change;
- the scene chooser's driver/anchor read (`GET /continuity/drivers`), its Story Pressure control changes, and the Story Graph's “Focus next scene” / “Anchor next scene” navigation.

The scene chooser's **existing** ranked suggestion call, made when a scene mode is picked (the deliberate #319 reversal documented in `useSceneSuggestions.ts`), is unchanged. It is not a read-only navigation path for acceptance criterion 16. This work adds no *other* model call to the chooser. Changing Story Pressure controls never starts a generation by itself: the reader presses Regenerate.

Generation occurs only where generation already belongs:

- scene suggestion generation;
- absorb, including its identity step (§10);
- the reconciliation run after End Scene (§11.1);
- the explicit “Refresh continuity review” action.

---

# 4. Terminology

## Canonical reference

A typed, stable identifier. The prefix is everything before the **first** `:`, and the id is everything after it; ids may themselves contain `:`.

| Ref form | Names | Notes |
|---|---|---|
| `scene:<sid>` | a scene | sids are renamed on title change, first date stamp and repad; stores that persist them join the `scene_refs.repoint` fan-out (§5.6) |
| `characters:<id>` | an NPC | same spelling as relationships, scene_ideas and owner refs |
| `pcs:<id>` | a player character | |
| `locations:<id>` | a location | plural, matching `ENTITY_KINDS` and owner refs |
| `groups:<id>` | a group | plural; graph-optional |
| `thread:<plot-id>` | a plot thread | |
| `commitment:<commitment-id>` | a commitment | |
| `event:<event-id>` | a scheduled event | event ids are slugs and become free on delete (§5.7) |
| `fact:<fact-id>` | a standing fact | graph-optional |
| `idea:<scene-idea-id>` | a stored scene idea | composed greeting ideas (`greeting:<gid>`) are not stored and get no idea ref |
| `holiday:<fixed>:<name>` | one holiday **occurrence** | built with `notices.holiday_key`, so the key matches notices |
| `birthday:<kind>:<id>:<fixed>` | one exact or yearless birthday occurrence | `<kind>` is `characters` or `pcs` |
| `birthday:<kind>:<id>:month:<year>-<monthkey>` | one month-only birthday occurrence | has no day; never given an invented one |

Existing actor tokens in some stores use `characters/<id>` or `pcs/<id>` (chronicle cast, appearances keys). The continuity layer has one internal colon form. Conversion happens at the boundary, in one helper. Do not rewrite existing files merely for notation.

Graph node kinds map to prefixes once, in one table (§20): scene→`scene:`, character→`characters:`, pc→`pcs:`, location→`locations:`, thread→`thread:`, commitment→`commitment:`, event→`event:`, idea→`idea:`, birthday→`birthday:`, holiday→`holiday:`.

## Canonical record

The record chosen as authoritative among reviewed duplicate records.

## Alias

A durable mapping from one same-type ref to another same-type canonical ref.

Aliases are acyclic and transitively resolved.

## Link

A durable, reviewed semantic relationship between two refs, read as `a <relation> b` (§5.3).

## Candidate

A derived finding worth review: a possible duplicate, possible closure, possible relation, or possible commitment resolution.

## Driver

A current reason a future scene may be worth playing. Drivers are:

- live plot threads;
- unresolved commitments;
- upcoming events;
- birthdays;
- holidays.

Reviewed links are attached to drivers; they are not drivers themselves.

## Pressure

Derived urgency or time relevance attached to a driver. It has a fixed contract (§13.4).

## Time anchor

A dated campaign occurrence used to constrain a suggestion: a scheduled event, a holiday occurrence, or a birthday occurrence. In v1, commitment deadlines are **not** anchors: they contribute pressure, and a commitment is reached through the `drivers` list (§15.2).

---

# 5. New durable campaign metadata: continuity.json

Add one campaign-owned file:

    <campaign>/continuity.json

It holds only reviewed semantics and durable reader decisions. It never holds raw embeddings or generated candidate rankings.

Version 1 shape:

    {
      "version": 1,
      "aliases": {
        "thread:duplicate-id": {
          "to": "thread:canonical-id",
          "created": "<iso>",
          "source": "review",
          "note": ""
        }
      },
      "links": {
        "<link-id>": {
          "a": "thread:...",
          "b": "commitment:...",
          "relation": "pays_off",
          "created": "<iso>",
          "scene": "",
          "note": ""
        }
      },
      "suppressions": {
        "<fingerprint>": {
          "kind": "possible_duplicate",
          "refs": ["thread:a", "thread:b"],
          "decision": "dismiss",
          "created": "<iso>"
        }
      }
    }

Writes use `store.atomic` and `locks.campaign_lock`. JSON is written as `json.dumps(indent=2, sort_keys=True) + "\n"`, like the sibling ledgers. Unknown top-level keys and unknown record fields are preserved on write.

**Reader/writer split.** Follow `events.read` / `events._mutable`, not `scene_ideas` (whose reader raises):

- The **reader** tolerates unparseable JSON, `OSError`, `UnicodeDecodeError` and a non-dict top level, all of which read as empty.
- Each of `aliases`, `links` and `suppressions` is checked **separately**. A malformed section reads as empty; the other sections still apply.
- `GET /continuity` reports `malformed: [<section>…]`.
- The **writer** raises `ContinuityError` on any of those conditions rather than overwriting.

## 5.1 Alias rules

Only these alias pairs are valid:

- thread -> thread;
- commitment -> commitment.

No scene, event, actor, fact, idea or location aliasing in this milestone.

API semantics:

- **Self-alias:** an alias to itself → 400; nothing is stored.
- **Existence:** both source and target must exist physically at creation → 404 otherwise.
- **Cycles:** the alias graph must be acyclic. Creating A -> B is rejected (409 `{kind:'alias_cycle'}`) if resolving B eventually reaches A.
- **Replacing:** if the source is already aliased → 409 `{kind:'alias_exists', to}`, unless the request carries `replace: true`. A replace is one journalled row whose restore is the previous record.
- **Storage:** `to` is stored as given; existing chains are not flattened. `source` ∈ {`review`, `manual`}.
- **Response:** lists any other sources that now resolve transitively to the new target, so the reviewer sees the full effect.
- **Liveness mismatch:** if the source's liveness differs from the canonical's — an open/advanced thread into a closed one, an unresolved commitment into a resolved one, or the reverse — the merge is refused with 409 `{kind:'liveness_mismatch'}`, unless the request carries `accept_status_change: true`. The review UI shows, before apply, the source's status, kind and due that the canonical will override.
- **Due:** if the source commitment has a non-empty `due` and the canonical's is empty, the review offers to copy it. Copying is an explicit, separate journalled write: the client's `PUT /ledger/commitments/{canonical id}` carrying the due, so that every hand edit to the ledger stays in `routes/ledger.py`. The alias refusal's `extra` carries both dues so the review can offer it. A due is never inherited silently.

## 5.2 Canonicalization

Provide one shared function:

    canonical_ref(cid, ref) -> ref

and a batch form:

    canonical_refs(cid, refs) -> dict[ref, canonical_ref]

Resolve transitively with a cycle guard, even though writes reject cycles: hand-edited files exist.

If continuity.json is unreadable, return the input ref. A **strict** variant, used by mutators and by candidate apply, raises `ContinuityError` instead of returning the input ref.

## 5.3 Link vocabulary

Every link is read as `a <relation> b`. The validator rejects any (kind(a), relation, kind(b)) triple not listed here.

| Relation | a | b | Meaning | Directional |
|---|---|---|---|---|
| `continues` | thread | thread | a is the later thread that carries b's question forward | yes |
| `subthread_of` | thread | thread | a is a narrower question inside b | yes |
| `pays_off` | thread | commitment | the thread is where the commitment comes due | yes |
| `before` / `on` / `after` / `by` | thread or commitment | event | a is due before / on / after / by event b | yes |
| `related_to` | thread, commitment or event | thread, commitment or event | reviewed relevance | no |

There is no endpoint of kind scene, actor, location, fact, idea, birthday or holiday in v1. An idea's temporal relation lives only in scene_ideas.json (§17); this avoids two sources of truth.

Do not add same_as. Identity is represented by aliasing.

`related_to` is canonicalized as an unordered pair for deduplication.

A future mechanics phase may add mechanics-specific relations without changing existing records.

## 5.4 Link identity

Endpoints are stored as given at creation, when they are canonical. They are **never rewritten** when aliases change later.

    link_id = "l" + sha256(f"{relation}\0{a}\0{b}").hexdigest()[:20]

For `related_to`, (a, b) is sorted before hashing. The id is computed once, at creation, and never recomputed. Deriving it from canonicalized endpoints at read time would change it whenever an alias was created or removed.

The **effective** view, `links(cid)`:

- canonicalizes endpoints at read;
- deduplicates on (relation, canonical a, canonical b);
- hides a link whose endpoints canonicalize to the same ref;
- lists hidden, duplicate and dangling links under `diagnostics`.

Creation is refused with 409 `{kind:'link_exists'}` when an existing link already canonicalizes to the same triple.

## 5.5 Candidate identity, fingerprints and suppressions

Three identifiers. Each has one definition, in one module.

**Candidate id**, stable across refreshes so that outstanding apply URLs keep working:

    candidate_id = f"{kind}-{sha256('\0'.join(sorted(canonical refs))).hexdigest()[:16]}"

**Fingerprint**, the current meaning of the records a candidate is about:

    fingerprint = "fp1_" + sha256(canonical JSON, sort_keys=True, separators=(",", ":")).hexdigest()

Its input depends on the candidate kind:

- **Pair kinds** (`possible_duplicate`, `possible_relation`): `{kind, sorted canonical refs, for each side: (title, status, kind, due)}`. Beat text is deliberately excluded. Otherwise a “these are distinct” dismissal would reappear after almost every scene, because live threads gain beats constantly. A dismissal sticks until a title, status or merge changes it.
- **Lifecycle kinds** (`possible_thread_closure`, `possible_commitment_resolution`): `{kind, canonical ref, effective status, effective beat count, sha256(effective latest_beat), due (commitments only), sorted ids of reviewed temporal links on the ref}`.

`last_scene` is deliberately excluded from both. A scene rename repoints it, and that is not a change of meaning. Beat count plus latest-beat hash captures every advance.

**Suppression.** Each candidate record stores its `fingerprint`. Dismissing a candidate stores that same value as the suppression key. Apply recomputes the same function to detect staleness (§22).

If the record later advances, the fingerprint changes and the candidate may legitimately return.

Do not suppress by cosine score or embedding model; those are discovery details. Bump the `fp` version only on a deliberate rule change, accepting that dismissals reappear once.

“Keep open” writes the same suppression as Dismiss, with `decision: "keep_open"` stored for display. The two differ only in their label.

## 5.6 Scene renames

Scene ids are filenames: they change on title rename, the first datetime stamp and repad. `scene_refs.repoint` fans out to every store that persists a scene id.

- continuity.json joins that fan-out through `continuity.doc.repoint_scenes(cid, mapping)`. It is tolerant like `commitments.repoint_scenes`, runs under `campaign_lock`, and covers `links[*].scene`. Update the `scene_refs` docstring's store count.
- The candidate cache is **not** repointed. It is derived: on apply, an evidence scene that no longer exists in `scenes.list_scenes` makes the candidate stale (409 `stale_candidate`), and the next reconcile rebuilds it.
- A deleted scene named in `links[*].scene` stays as provenance, like `scene_ideas.used_scene`.

## 5.7 Deleting a referenced record

Thread, commitment and event ids are slugs, and they become free again on delete. A recreated record of the same name would otherwise silently inherit the dead record's aliases and links. This is the same reason the event DELETE route already retires notice keys under one hold.

- `routes/ledger.py`'s `delete_thread` and `delete_commitment`, and the event DELETE route, call `continuity.review.forget_ref(cid, ref)` inside their existing `campaign_lock` hold. It removes aliases whose source or target is `ref`, and links naming `ref`. Each removal is journalled as a `manual` row. It lives in `review` rather than `doc` because journalling needs `undo`, and `doc` cannot import it.
- A thread/commitment DELETE is refused with 409 `{kind:'malformed'}` before any write while continuity.json (or its aliases or links section) is malformed. A strict check cannot tell whether the record is an alias target, and a cascade that fails after the delete has landed would report a completed write as an error.
- Deleting a record that is an alias **target** is refused first with 409 `{kind:'has_merged_records', refs}`, unless `?force=1` is passed. The Ledger shows the 409 as “This record has merged records: unmerge them first, or delete anyway”.
- Deletions that do not pass through those handlers (undo of a create, cascade reversal) leave dangling refs. These surface as broken in `GET /continuity` diagnostics. Slug reuse in those paths is a documented residual.

## 5.8 Forks

A retrospective fork carries continuity.json as it stands at the fork moment. Aliases and links made after the cut point come along; refs to records the cut removed surface as broken diagnostics. This is a documented residual, not a defect.

---

# 6. Derived candidate cache: continuity_candidates.json

Add a campaign-local derived file:

    <campaign>/continuity_candidates.json

This file may be deleted and reconstructed. It exists so that:

- Todo can be cheap;
- the Ledger can show pending review without running embeddings;
- opening the graph never launches a model;
- a reconciliation result survives app restarts until reviewed.

Shape:

    {
      "version": 1,
      "generated": "<iso>",
      "generation": "<run start stamp>",
      "basis": {
        "embedding_space": "<space or empty>",
        "embedding_model": "<model or empty>",
        "identity_hashes": {"thread:a": "<sha256 of identity text>", "...": "..."}
      },
      "records": {
        "<candidate-id>": {
          "kind": "possible_duplicate",
          "refs": ["thread:a", "thread:b"],
          "fingerprint": "fp1_...",
          "signals": {
            "title_exact": false,
            "slug_equal": false,
            "lexical": 0.0,
            "cosine": null,
            "shared_actors": ["characters:mara"],
            "shared_scenes": ["012--..."],
            "shared_anchors": ["event:coronation"]
          },
          "proposal": {
            "decision": "duplicate",
            "from": "thread:b",
            "to": "thread:a",
            "relation": "",
            "status": "",
            "reason": "...",
            "evidence_scenes": ["012--..."]
          },
          "created": "<iso>"
        }
      }
    }

`proposal` is `null` when no model adjudicated the candidate. Possible reasons: none was configured, the candidate was over the cap (§11.2), or the call failed.

`basis.identity_hashes` is what the automatic sweep compares against to stay incremental (§11.1). There is deliberately no campaign-revision field: the revision token moves on every campaign write, including the reconcile's own persist, so it cannot decide staleness. Staleness is decided only by per-candidate fingerprints (§22).

The candidate cache is not itself journalled. Applying a reviewed operation writes the durable source store and/or continuity.json, which is journalled.

If the candidate file is malformed, ignore it. Reconciliation can rebuild it.

A candidate-cache write moves the campaign's revision token:

- on a 2xx route, through the activity middleware;
- at the detached persist, through an explicit `revision.bump` inside the persisting lock hold.

Add the persist to `revision.py`'s list of writers that stamp for themselves. Over-bumping is the token's deliberate failure direction.

## 6.1 Candidate kinds

Required:

- possible_duplicate
- possible_relation
- possible_thread_closure
- possible_commitment_resolution

Do not add other kinds in this milestone. Every kind creates Todo, UI and review behaviour. Stale threads and passed event-linked obligations are expressed as `possible_thread_closure` and `possible_commitment_resolution` nominations (§11.1), not as separate kinds.

## 6.2 How kinds map to review groups and Todo chores

| Kind | Continuity review group (§12) | Todo chore (§18.2) |
|---|---|---|
| possible_duplicate | Possible overlaps | continuity-overlaps |
| possible_relation | Possible overlaps | continuity-overlaps |
| possible_thread_closure | Possible closures | continuity-closures |
| possible_commitment_resolution | Possible commitment resolutions | continuity-closures |

---

# 7. Canonical projections over existing ledgers

Do not teach every consumer how aliases work.

## 7.0 Package layout

The obvious single-package layout fails `test_import_guard`. That guard requires module-scope imports, an acyclic module graph, and submodule binding inside `store/`. Three edges make a single package impossible:

- `scene_refs` must import the continuity storage module (§5.6);
- `undo` must import it to register targets (§12.4);
- the projections must import `plot` and `commitments`.

So `backend/src/grimoire/store/continuity/` is split:

| Module | Role | May import |
|---|---|---|
| `doc` | continuity.json IO and mutators: aliases, links, suppressions, `repoint_scenes`, `restore_alias`, `restore_link`. A leaf. | `atomic`, `locks`, `paths`, `campaigns.paths`, stdlib |
| `candidates` | the candidate cache IO. A leaf. | as `doc` |
| `canon` | `canonical_ref` / `canonical_refs`, fingerprint and candidate-id functions | `doc` |
| `effective` | `threads`, `commitments`, `links`, the render helpers for prompt snippets | `canon`, `plot`, `commitments`, `prompts` |
| `involvement` | §8 | `effective`, `chronicle`, `appearances` |
| `pressure` | §13 | `effective`, `calendars`, `events`, `aging`, `birthdays`, `notices`, `clock` (§13.1's `clock.now`), `campaigns.paths` (the calendar root) — never imported by `clock` (Slice B plan, Decision 1) |
| `drivers` | §14 | `pressure`, `involvement`, `effective`, `embed_space` (the one definition of `matching`), `aging` (a driver's `stale`), `appearances.cast` (offscreen player tokens; it may not import `suggest`) (Slice B plan, Decisions 1, 10, 12) |
| `similarity` | §9 | `effective`, `embed_space`, `vectors`, `embeddings` |
| `identity` | §10 prompt build/parse and the deterministic pre-pass | `similarity`, `prompts`, `absorb.parse` |
| `pending` | what a cached finding means now: its current fingerprint and verdict, and the live filter the review read, Todo, apply and both persists share. Read-only. (Slice D plan, Decision 1) | `fieldtext`, `candidates`, `canon`, `doc`, `effective` |
| `reconcile` | §11 discovery, prompt build/parse and persist | `embeddings`, `prompts`, `aging`, `calendars`, `chronicle`, `clock`, `embed_space`, `errors`, `fieldtext`, `locks`, `paths`, `relationships`, `revision`, `scene_ids`, `vectors`, `absorb.parse`, `campaigns.paths`, `scenes.read`, `candidates`, `canon`, `effective`, `involvement`, `pending`, `pressure`, `similarity`. It reads continuity.json only through `pending` and imports no writer the §11.6 guard forbids (Slice D plan, deviation 22) |
| `review` | §12 apply/dismiss orchestration; journalled alias/link writes and `forget_ref` (§5.7) | `commitments`, `events`, `fieldtext`, `locks`, `paths`, `plot`, `undo`, `scenes.read`, `candidates`, `canon`, `doc`, `effective`, `pending` (Slice A's edges plus Slice D's; Slice D plan, deviation 22) |
| `graph` | §19 | the readers above |

`continuity/__init__.py` imports no submodule that reaches `scenes`, `undo` or `scene_refs`. `plot.render_open` and `commitments.render_open` stay physical. Context assembly, the absorb snapshot and the advance digest call the new `effective` render functions over the same snippets. `scripts/verify_templates.py`'s render_open checks are extended to cover the effective form.

Each module that writes continuity.json or continuity_candidates.json goes into `locks.DOMAIN_MODULES`, and each of its public cid-taking mutators takes `locks.campaign_lock(cid)`. `UNREVIEWED` does not grow.

Required public projections:

    effective.threads(cid, include_closed=False) -> list[dict]
    effective.commitments(cid, include_resolved=False) -> list[dict]
    effective.links(cid) -> list[dict]
    candidates.records(cid) -> list[dict]
    drivers.snapshot(cid, offscreen=False) -> dict
    graph.build(cid) -> dict

## 7.1 Effective merged records

**Live canonical.** `canonical_ref` (§5.2) answers what the stored alias graph says. Every *current-state* question uses one stricter resolver, `effective.live_canon(cid)`. That covers effective records, links, involvement, the alias target's group status, link creation and the Ledger write redirect.

The resolver walks hop by hop from a source, following a hop only when all of these hold:

- the alias record is a dict with a string `to`;
- both refs parse;
- both share a prefix in {thread, commitment};
- the target record exists, or its file is unreadable, in which case existence is unknown and treated as passable;
- the target has not already been visited.

It stops at the last valid hop. A source caught in a cycle resolves to itself. So A→B with B→gone merges A into B, while a dangling or wrong-type alias never redirects a write to a record that does not exist. Each walk that stops early is reported in diagnostics under the failing hop's reason.

**Identity law.** A canonical record with no live aliases projects **byte-identically** to `plot.open_threads` / `commitments.open_commitments` for that id: the same fields, values, beat order, `latest_beat`, `last_scene` and sort position, plus `aliases: []`.

This rule is load-bearing:

- `set_movement` stamps `last_scene` on every call, even one that adds no beat;
- `latest_beat` is the last *appended* beat, not the last by scene order;
- aging, dormancy, the briefing and the frozen-campaign sweep all read these fields.

Any “improvement” applied to unaliased records would change prompts, the ledger and the digest for every existing campaign.

Only when aliases exist:

- **beats:** the canonical's stored beats, followed by each alias's stored beats, merged by a **stable** sort keyed on scene play order. A scene is comparable when `scene_ids.parse_sid()` returns non-None. A beat whose scene is `""` or unparseable keeps its position immediately after its stored predecessor.
- **duplicate beats:** an exact (scene, text) duplicate is dropped only **across** different physical records, never within one.
- **effective `last_scene`:** the latest, by play order, of every member's physical `last_scene` and every member's beat scenes, ignoring `""`. If none is comparable, use the canonical's physical `last_scene`.
- **`latest_beat`:** the last beat of the merged list.
- **title, kind, due and status:** the canonical record's are authoritative.
- **keys:** the effective record keeps `open_threads`' row keys (id, title, status, last_scene, latest_beat; for commitments also kind and due), so that `aging.annotate`, dormancy and the snippet templates work unchanged.
- **`aliases`:** `[{ref, title, status}]` lists the hidden physical records, for Ledger and Graph detail.

No physical file is rewritten to produce this view.

If an alias points to a missing physical canonical record, do not hide the source. Degrade by treating the source as its own record, and surface the dangling alias in Continuity review diagnostics.

## 7.2 Writers addressing an alias source

A write to a hidden alias source would otherwise be silently ignored, since the canonical's status is authoritative. Consider an absorb review staged before a merge and saved after it: its status move would vanish. So:

- **Absorb materialization** redirects a row whose id, or whose slug collision (§10.5), names a current alias source to its canonical before staging. The staged row's label adds “→ merged into <title>”.
- **The Ledger PUT routes** (`put_thread`, `put_commitment`) redirect the same way, unless `?physical=1` is passed (used only by the alias detail's read-only physical view, if one is built).
- A status carried by a physical alias record is shown in alias detail. It is never read as effective.

## 7.3 Consumers that must switch to canonical projections

The plan classifies every direct consumer; this list is the starting audit, taken from a grep of the tree at review time. The plan re-runs the grep.

**Effective current state** (switch to `continuity.effective`):

- `store/briefing.py` (open threads/commitments, and its touched-scene logic → shared involvement, §8)
- `store/clock.py` advance digest (“what is still owed”)
- `store/suggest.py` `build_snapshot`
- `store/context/assemble.py` `render_open` (context form)
- `store/absorb/snapshots.py` `render_open(with_id=True)`. The absorb prompt shows canonical ids only.
- `routes/campaigns.py` `get_ledger` (§12.5)
- `routes/todo.py` `_chore_owed` and `_items_owed`
- `routes/shell.py` `ledger_open`. This becomes the effective **commitment** count, and its rail tail label is corrected from “open threads” to “open commitments”, which is what it has always counted.

**Physical history or writer paths** (stay on physical ids):

- `store/timeline.py` (play-timeline beats per scene)
- `store/search.py` (keyword corpus)
- `store/retcon.py`
- `store/absorb/materializer.py` (staging targets, plus the alias redirect of §7.2)
- `store/absorb/apply.py` and `store/absorb/conflicts.py`
- `store/undo.py` and `routes/ledger.py`
- `store/cascade.py`, and `scene_refs.repoint`
- `scripts/verify_templates.py` and `frozen_campaign/sweep.py` keep their physical checks and gain effective ones.

Historical all-record views may still expose physical records with alias metadata, so provenance stays inspectable.

---

# 8. Shared involvement projection

briefing.py determines, for each **focus** actor, whether that actor's scenes intersect a record's touched scenes. It never computes “which actors a record touched” across the whole roster. The shared helper is therefore a generalisation, not a straight extraction.

Required projection:

    involvement.of(cid, refs) -> {
      "<input ref>": {
        "actors": ["characters:...", "pcs:..."],   # sorted
        "scenes": ["..."]                          # sorted
      }
    }

Rules:

- **Scene→actors index:** built from `appearances.cast.roster(cid)` ∪ chronicle `cast` snapshots, as briefing does today. Union them; never choose one as authoritative. Chronicle cast refs use `<kind>/<id>`; the output uses `<kind>:<id>`.
- **Touched scenes:** beat scenes ∪ `last_scene`, excluding `""`, aggregated over the ref's whole canonical group (the canonical plus its aliases).
- **Keying:** output is keyed by the **input** ref.
- **Malformed records:** tolerated piece by piece.
- **No LLM call.**
- **No lock is taken.** Callers that need consistency hold `campaign_lock`, as `briefing.build` does. Todo and graph use `best_effort_campaign_lock`.

Sequencing: briefing.py is first refactored onto the helper **with no behaviour change**; its existing tests pass unmodified. Canonicalization is added as a separate step.

---

# 9. Identity text and similarity

## 9.1 Canonical identity text

Add pure functions whose output is stable and intentionally separate from UI and prompt rendering:

    thread_identity_text(effective_thread) -> str
    commitment_identity_text(effective_commitment) -> str

Thread text contains, in this order:

1. record type label;
2. title;
3. latest beat;
4. up to two previous non-duplicate beats, newest first.

Commitment text contains:

1. kind plus record type label (a blank kind renders as the default `promise`);
2. title;
3. latest beat;
4. due prose, when present;
5. up to two previous beats.

Do not include ids: they contain lexical accidents and should not influence semantics.

Clip with `embed_space.clip(text, CONTINUITY_IDENTITY_BYTES)`, a named constant beside the other embedding bounds, not a new unbounded path.

## 9.2 Deterministic lexical signals

Implement these before embeddings.

Required signals:

- **Title equality:** casefolded, NFKC-normalized title equality.
- **Slug equality.** Ignored when either slug is the `untitled` fallback that `paths.slugify` produces for titles with no ASCII alphanumerics. It counts only when each slug spells its whole title: `slugify` drops what is not ASCII, so two different mixed-script or accented titles can share a slug that says nothing about either (Slice C implementation; see the plan's deviations appendix).
- **Token Jaccard** over identity text, after casefold, NFKC and simple punctuation/whitespace normalization. Combining marks stay inside their word, and the commitment `due ` prefix every dated commitment shares is not counted (Slice C implementation; see the plan's deviations appendix).
- **Character 3-gram Jaccard.** This carries the lexical score for text with no whitespace tokens. If `difflib.SequenceMatcher` is used anywhere, it is constructed with `autojunk=False`.
- **Shared actors:** count and list (§8).
- **Shared touched scenes:** count and list.
- **Shared reviewed event/time-anchor refs.**

No new compiled dependency.

Lexical scoring ranks candidates only. It never merges anything automatically.

## 9.3 Embedding reuse

Reuse the existing embedding infrastructure:

- `embeddings.py`'s client;
- `store/vectors.py`'s unit-vector content-hash cache;
- `store/embed_space.py`'s connection/model namespace.

Do **not** use `semantic_recall_depth` as an enable/disable switch.

**Availability.** Continuity semantic matching is available iff `store.embed_space.resolve()` is non-None. That function already encodes the required conditions:

- the model is non-empty;
- the connection exists and is `openai_compatible`;
- `base_url` is set.

**Cache sharing.** Identity texts share `home()/.cache/embeddings` and the `space` namespace with recall and search. Keys are `sha256(space\0text)`, so equal text in one space means an equal vector. A changed identity text leaves one stray vector; this is the cache's documented bounded leak, and strays are not pruned.

The semantic recall threshold is not the continuity duplicate threshold. Continuity candidate generation has its own internal candidate floor and top-k constants. They are documented as candidate-generation parameters, not truth thresholds.

Initial behaviour:

- retrieve at most the nearest 3 same-type neighbors for each proposed-new record;
- use a loose candidate floor;
- still include structurally strong neighbors that fall below the cosine floor;
- never expose a pair solely because it is one of the top-k when every signal is weak.

Exact numerical thresholds belong in the implementation plan, after test fixtures are constructed. They are justified structurally there and are to be tuned against real prompts later. Do not justify them from private campaign measurements in committed docs.

## 9.4 Embedding mechanics

`EmbeddingsClient.embed` is synchronous httpx. It returns all vectors or raises, works in batches of `embeddings.BATCH`, and gives every batch one shared `TIMEOUT` deadline. So:

- **Threading:** every embedding call from async code goes through `run_in_threadpool`. Such a call cannot be cancelled, which is why every call is bounded.
- **Inside absorb (§10):** embed only the proposed-new rows that have at least one same-type record to compare against (Slice C implementation; see the plan's deviations appendix), plus at most `IDENTITY_WARM_LIMIT` uncached neighbours that the lexical pre-filter kept. Use `deadline = min(monotonic() + embeddings.TIMEOUT, absorb budget deadline)`.
- **In a sweep (§11):** embed misses with one `embed()` call per `embeddings.BATCH` chunk under one shared deadline, calling `vectors.save` after each chunk so a later failure keeps earlier work. Cap at `RECONCILE_WARM_LIMIT` per run, rotated with `embed_space.warm_window` seeded by `cid`, so that repeated runs cover the whole ledger.
- **Width:** the reference width is that of vectors embedded in this run; if none were, it is the most common width among the loaded vectors. Vectors of any other width get `vectors.forget` and are treated as misses. Recall and search compare against a fresh query vector; a fully cached sweep has none, which is why this rule exists.
- **Failure:** any `EmbeddingsError` or `OSError` leaves lexical/structural candidates intact and sets the run's embedding mode to `failure`.
- **Metering:** embedding calls stay unmetered, consistent with recall and search, so semantic-matching cost does not appear on Costs.

Opening Todo, Graph or Ledger never calls this path.

## 9.5 Settings disclosure for automatic embedding

Today the Settings page says embedding is off when the connection is blank *or* recall depth is `0`. It discloses only that scene text and world info are sent.

This capstone embeds thread and commitment summaries automatically, after End Scene, whenever a connection and model are set, regardless of depth. A user who set depth to `0` to turn embedding off would otherwise start sending new text after upgrading. Therefore, in the **same slice as the first automatic continuity embedding call**:

- The Settings section `semantic` is relabelled **Embeddings**.
- Its status chip reports `embed_space.resolve() is not None` separately from recall depth.
- Its copy states: “With a connection and model set, Grimoire also embeds plot-thread and commitment summaries after each wrap-up to find possible overlaps. Recall depth does not control this. Set the connection to Off to stop all embedding.”
- The disclosure paragraph names both payloads.

---

# 10. Hardening absorb: new-record identity resolution

The first defence against duplicate ledgers is not to open a duplicate record.

## 10.1 Prompt contract change

The absorb prompt must continue to show current open threads and commitments with ids. Those are now the **canonical** effective records (§7.3).

Strengthen the instruction. For every plot or commitment movement, the model chooses one of:

- move an existing id;
- resolve/close an existing id;
- open a new record (no id).

A new record is only correct when no shown existing record represents the same narrative obligation or question.

There is no new action enum: today a missing id already means “new”. An id-less row may carry two optional fields:

    why_new              (string)
    distinguished_from   (list of snapshot ids the model considered close but distinct)

`absorb.parse.parse_output` carries them only when they are present and well-typed. Unknown ids in `distinguished_from` are dropped. They are defined once, as `absorb.parse.IDENTITY_FIELDS = ("why_new", "distinguished_from")`, and the absorb eval case's prompt needles include them.

These fields are decision aids and review evidence. They are not stored in plot.json or commitments.json. `distinguished_from` never suppresses resolver gating: the model saying “distinct” is not evidence.

## 10.2 Do not trust first-pass “new”

After `parse_output`, collect proposed-new plot and commitment rows before materialization allocates final ids. A row is proposed-new when it is either:

- an id-less row with an alphanumeric title; or
- a row whose id names no stored record (its title, or else its id, becomes the title text).

For each proposed-new row:

1. build its temporary identity text. Its actors come from the prepared scene facts' cast and appearances; its scene is the current scene;
2. generate same-type nearest candidates using exact, lexical and structural signals;
3. enrich them with embeddings if configured (§9.4 bounds);
4. if no candidate is plausibly related, keep the row new and make no call;
5. otherwise, send all ambiguous rows in **one batched identity-resolver LLM call**, not one call per row.

Closed and resolved records are eligible as neighbours, so the resolver can see that something was already settled.

The identity resolver receives only:

- the proposed rows;
- each row's transcript citation (quote/speaker/certainty) and `why_new` / `distinguished_from`;
- up to a bounded number of same-type neighbour records per row (3, §9.3), with ids, status and short beat histories;
- the deterministic similarity signals.

It does not receive the entire campaign.

Allowed decisions:

    existing
    new
    uncertain

- `existing` must name one supplied candidate id;
- `new` may include a short reason;
- an unknown decision, or `existing` naming an unsupplied id, becomes `uncertain`.

**Accepting `existing`.** An `existing` decision is accepted only when both hold:

- the named id resolves, canonicalized, to an **open/advanced thread or an unresolved commitment**; and
- no other row in this batch already targets it (the materializer keeps one edit per record per scene, so a second row would silently lose a beat).

Otherwise it is downgraded to `uncertain`. An `existing` naming a closed or resolved neighbour is always downgraded: identity resolution never reopens a record.

When `existing` is accepted, the parsed row is rewritten before `materialize`:

- `id` := the canonical existing id;
- plot `status` := the model's original `closed` or `advanced` if it gave one, else `advanced`. It is never `open`, which `parse_output` uses as a default and which would regress an advanced thread;
- commitment `kind` := `""` (keep stored);
- commitment `status` is kept only if it is in `commitments.RESOLVED`, else `""`;
- `due` is kept only if the original row carried it.

**Uncertain rows.** An `uncertain` row stays a new staged row, but it gets `review.band = "low"` after materialize, so it arrives unticked in the NEEDS YOU drawer. Under the existing save rule an unticked row is **still written unless rejected**, and its hint says so: “Possible existing record — reject this row, or switch it to the existing record, if it is the same business.”

**Unchecked rows.** A row the check never answered — omitted from a partial reply, or every row when the call failed, was refused by the budget, or had no connection — is `unchecked` / `hint_only`, and it gets the same `low` band. Every examined row has at least one plausible candidate, so an unanswered one is a possible duplicate nobody ruled out; its routing band would pre-approve it outside NEEDS YOU. A partially answered batch reports the phase `degraded`, never `ok` (Slice C plan, revised after the Codex review on #466).

The identity resolver is advisory. Final staged edits still go through the normal review.

## 10.3 Review hints and switching rows

Every row the identity step examined carries:

    identity_check: {
      decision: "existing" | "new" | "uncertain" | "unchecked",
      status: "accepted" | "downgraded" | "hint_only",
      reason: "",
      proposed: {title, why_new, distinguished_from},
      candidates: [{ref, title, status, latest_beat, signals}],
      alternatives: [StagedEdit]
    }

`alternatives` are **server-rendered, complete** staged rows:

- one per eligible candidate (open/unresolved, not targeted elsewhere in the batch);
- plus, for a row that was retargeted, the as-new variant.

Each has its own `before` staleness token, computed by `conflicts.plot_line` / `commitment_line`. So the reviewer can swap a row wholesale, and `check_conflicts` still verifies it, without the client computing a token. The client removes `alternatives` from the PUT body on save.

The review panel shows a “Possible existing record” chip on the row, modelled on the existing contradiction badge. It offers “Use <title> instead” for each alternative.

Slice C implementation (see the plan's deviations appendix): `candidates` are re-resolved at staging through the current alias map, so a record merged away during the absorb is listed as its canonical; the chip follows the record the row writes now — “Matched an existing record” for an accepted retarget, “Switched to an existing record” for one the reviewer swapped; and the row shows the title the extraction proposed, with its `why_new`. The field is named `identity_check`, not `identity`, to avoid confusion with a scene's identity.

## 10.4 Placement, budget and failure behaviour

The identity step is **chained onto the extraction coroutine** inside `_gather_phases`, inside the `_watched` race:

    extraction → parse_output → deterministic neighbours → optional embeddings → optional resolver call

It does not run after `_gather_phases` returns, because it would then wait for the slowest phase (the per-NPC dossier loop) and find the wall-clock budget spent. It never raises out of absorb, except `Abandoned`.

- **Connection:** resolved alongside the other phases, as `_soft_connection(lambda: _require_connection("continuity-identity", cid))`. With no connection the step runs deterministic neighbours only and attaches hints.
- **Budget:** bounded by the absorb `_Budget` (`budget.run(...)`), the same wall-clock budget as the other phases. Absorb has no token or cost budget.
- **Metering:** its own nested `store.usage.meter("continuity-identity", campaign=cid, scene=sid)` in `routes/scenes.py`. Dossier, voice-drift and audit are already metered separately; this is not hidden inside `absorb`.
- **Phase report:** a block `{status: ok|degraded|failed|skipped, reason, attempted, budget_exhausted, counts}` and a fifth `phases` row named `identity`. The frontend `AbsorbPhase` union and `PHASE_LABELS` gain it.
- **No retry route** of its own.

Failure behaviour:

- **Embeddings fail:** use lexical/structural neighbours; status `degraded`.
- **The resolver LLM call fails, or returns no decodable object:** status `failed`. First-pass extraction is kept, and every examined row carries its `identity_check` with `status: "hint_only"`, its candidates and its alternatives, so the reviewer can still switch rows.
- **No model connection:** normal absorb already has larger limitations; add no new special failure.
- **The call only runs** when at least one proposed-new row has a plausible candidate. Do not pay for it on every scene. In particular, existing absorb tests that script fakes by call order must stay call-count-stable; the plan audits tests that assert `len(fake.requests)`.

## 10.5 Plot slug collisions

Plot materialization today gives an id-less row `slugify(title)`. If that slug names *any* stored thread, closed ones included, the row is staged as a movement on that thread. That is a silent merge before any resolver sees the row, and every non-ASCII title collides on `untitled`.

Plot materialization adopts the commitment predicate (`_new_commitment_id`): a slug collision counts only when the stored thread is not closed **and** has the same casefolded title. Otherwise allocate `slug-N`.

The §28.2 test “semantically different identical-slug safety” covers this.

---

# 11. Reconciliation sweep after history is written

Scene-local extraction cannot reliably clean up every historical issue. Add a separate campaign reconciliation computation.

## 11.1 Run model and triggers

Reconciliation is a **`background`-class run on the campaign subject** `runs.campaign_subject(cid)`, kind `continuity-reconcile`.

Add `runs.reserve_campaign_background(app, cid, kind, attempt_id=None) -> Run | None`. It calls `start_or_existing(("campaign", cid), "background", kind, attempt_id, …)` and maps `StoreMovingError` to `None`. Like the rolling summary, the run:

- declares no exclusion key;
- holds no scene;
- sends no notification;
- is not a draft: it writes its own derived store.

At most one reconcile is live per campaign. A start while one is live returns the live run.

Update CLAUDE.md's detached-run inventory (the handler count and the members of the background class) in the same change.

**Automatic trigger.** `PUT /chronicle` (the End Scene save) gains `client = Depends(get_llm)`. After its campaign-lock block, and only on a **fresh** commit — never on the idempotent replay path — it calls a `_start_background`-style helper:

- whether or not some edits failed, the helper starts an **incremental** sweep;
- the helper swallows and logs any reservation or start failure;
- the save response never depends on it.

The incremental sweep:

- pairs records whose identity-text hash changed since `basis.identity_hashes` against all records;
- re-checks lifecycle candidates for records the scene touched;
- re-checks lifecycle candidates for commitments whose pressure state is overdue or passed-linked.

A skipped automatic run is therefore caught by the next one.

**Explicit refresh.** `POST /campaigns/{cid}/continuity/reconcile`:

- is `@computes_only` and honours `X-Grimoire-Attempt`;
- reserves inside `runs.reservation` and answers `202 {run}`;
- runs a **full** sweep;
- works with no LLM or embeddings connection (deterministic discovery only), and the response says which mode it ran in;
- is polled by the client through the campaign run routes (`api.draftRun`'s campaign scope).

Both triggers run deterministic discovery **inside the run**, never inside the request. Discovery is lexical, structural and pressure work, and pressure may run user calendar-plugin code, so it is kept out of the save's lock hold.

**Steps of one run:**

1. **Compute, outside the lock:** effective records, identity texts and hashes, involvement, pressure, lexical/structural pairs, embeddings (§9.4), and lifecycle nominations:
   - a thread whose aging state is `stale` → `possible_thread_closure`;
   - an unresolved commitment that is `overdue`, or whose linked event is reached (§13.5) → `possible_commitment_resolution`;
   - a thread or commitment whose accumulated beats were touched by this scene → lifecycle re-check.
2. **First persist (deterministic).** Inside one `campaign_lock` hold (via `run_in_threadpool`): reload continuity.json, recompute each candidate's fingerprint from the current store, drop candidates that are suppressed, already satisfied (alias or link exists), fingerprint-mismatched or whose refs are gone, carry forward cached proposals whose fingerprint still matches, write, and `revision.bump`. The persist refuses to overwrite a cache whose `generation` is newer than this run's start: the newest start wins, and a superseded run reports `superseded: true`.
3. **Adjudicate** (only if a `continuity-reconcile` connection resolves, via `_soft_connection`): send at most `RECONCILE_MAX_CANDIDATES` candidates that have **no cached proposal and no suppression**, prioritized as follows:
   1. overdue / passed-linked resolution nominations;
   2. duplicates, by strongest structural signal;
   3. the rest.

   Candidates over the cap persist without a proposal. One LLM call per run, metered as `store.usage.meter("continuity-reconcile", campaign=cid)` in `routes/`.
4. **Second persist (proposals).** Same hold rules as step 2; it merges proposals into the cache. An LLM failure therefore keeps the deterministic candidates (§26).

**Storage relocation.** `PUT /config/data-dir` is refused while any run is live, background runs included. That is accepted: the run is bounded by the warm limits, the candidate cap and one LLM call, so the window is short.

## 11.2 Reconciliation input

Bounded campaign context:

- canonical active threads;
- canonical unresolved commitments;
- the recently closed/resolved same-type records needed for identity comparison;
- recent chronicle one-lines;
- candidate-specific relevant beats;
- involvement actors;
- reviewed links and time anchors;
- the current date and deterministic pressure classification;
- candidate similarity signals.

Do not send every scene transcript. When a candidate claims that a specific scene proves closure or resolution, send only the short chronicle line and beat evidence needed to adjudicate it. Full transcripts are never part of the reconciliation payload.

The scene ids sent in the payload (beat scenes and chronicle lines) form the run's **known scene set** (§11.4).

## 11.3 LLM decision vocabulary

Same-type pairs (thread/thread, commitment/commitment):

- duplicate
- continuation
- subthread
- related
- distinct
- uncertain

Cross-type pairs (thread/commitment):

- pays_off
- related
- distinct
- uncertain

Never duplicate across types.

**Direction.** Every directional decision carries `from` and `to`:

- `duplicate`: `from` is the record to alias, `to` is the canonical;
- `continuation`: `from` continues `to`;
- `subthread`: `from` is a subthread of `to`;
- `pays_off`: `from` is the thread, `to` is the commitment.

The parser rejects any direction/type combination that §5.3 does not allow, downgrading it to `uncertain`.

Thread lifecycle:

- close
- keep_open
- uncertain

Commitment lifecycle:

- fulfilled
- broken
- expired
- keep_open
- uncertain

The model may not invent a new status.

**Temporal interpretation.** Temporal proposals are `possible_relation` candidates with `relation ∈ {before, on, after, by}`, or no proposal when the model answers `unrelated`. They are nominated only for open commitments whose `due` the calendar cannot parse, paired with events inside the suggestion horizon.

## 11.4 Positive-evidence requirement

close/fulfilled/broken/expired requires both:

- `evidence_scenes` containing at least one id from the run's known scene set; and
- a non-empty `reason`.

Grounding in beat or chronicle content cannot be checked mechanically. The non-empty reason plus a known evidence scene is the mechanical floor, and the review UI shows the evidence for the reader to judge.

The parser drops unknown scene ids. If no known scene supports the operation, the decision is downgraded to `uncertain`: a lifecycle proposal is never materialized with fabricated evidence.

## 11.5 Candidate proposal vs application

The LLM result updates continuity_candidates.json only.

Nothing in the reconciliation computation changes plot.json, commitments.json, continuity.json's aliases or links, events.json, or scene ideas. (It *reads* suppressions, aliases and links.)

The reader reviews each proposed structural operation.

## 11.6 Writer guard

Add `backend/tests/test_continuity_writer_guard.py`, modelled on `test_absorb_writer_guard.py` and resolving import bindings the same way. It fails if any module in `store/continuity/{similarity,identity,reconcile,pressure,drivers,graph}` calls:

- `plot.set_movement`, `plot.restore`;
- `commitments.set_movement`, `commitments.restore`;
- any alias or link mutator in `continuity.doc`.

Only `continuity.review` (and the route layer) may apply reviewed operations. Name the guard in CONTRIBUTING.md's guard table; `test_docs_guard` requires this.

---

# 12. Continuity review UI

Continuity review lives **inside LedgerView**. Do not build a second app-navigation rail.

## 12.1 Placement and addressing

LedgerView becomes addressable by path, as WorldView's sections already are:

    /campaigns/{cid}/ledger                                   → facts (unchanged default)
    /campaigns/{cid}/ledger/{section}
    /campaigns/{cid}/ledger/{section}/{id}
    /campaigns/{cid}/ledger/continuity/{group}
    /campaigns/{cid}/ledger/continuity/{group}/{candidate-id}

- **Sections:** `section` ∈ facts | threads | commitments | relationships | standings | changes | timeline | continuity. Use the existing section keys where they differ in spelling.
- **Row addresses:** a row address scrolls to and highlights that row without opening its editor. An aliased physical id resolves to its canonical row.
- **Missing findings:** a candidate address that is no longer pending shows its group, with “This finding is no longer pending.”
- **Who uses them:** Todo chores and items, the Story Graph's “Open ledger entry”, and candidate links all use these addresses.
- **Rail:** the rail's prefix match still lights the Ledger row.

The column gains a second `ColumnSection`, **Continuity review**. It holds:

- one row per group, with counts (“—” while reading);
- the matching mode line: “Basic matching active — semantic matching not configured”, or “Semantic matching active”;
- the Refresh action.

Groups:

- Possible overlaps
- Possible closures
- Possible commitment resolutions
- Reviewed links / merges
- Dismissed findings (collapsed)

Selecting a group lists its findings in main. Selecting a finding opens a read-only `.detail-view` (the list/detail pattern):

- `.detail-main`: the affected records, beats, signals and proposal;
- `.detail-sidebar`: the actions in `.form-actions`, and metadata in `.side-section`s;
- “‹ All findings” returns to the list.

An action that needs input (a closing beat, a relation direction) reveals its form only after it is chosen. The candidates read is its own request with its own failure state, like the changes read.

## 12.2 Candidate detail

`GET /continuity/candidates` joins each cached candidate, at read time, to the current effective records it names:

    {ref, kind, title, status, latest_beat, last_scene: {id, title}, pressure, aliases}

Each candidate also carries `stale: boolean` (its fingerprint no longer matches; deterministic, no model call). The response's top level carries `generated`, `matching`, and `diagnostics` (dangling aliases, broken links, malformed sections).

Each candidate detail shows:

- the affected records, with current statuses and latest beats;
- relevant dates and pressure;
- structural signals such as shared actors and scenes;
- semantic similarity when available, labelled as a discovery signal rather than confidence;
- the LLM proposal and reason, if present;
- evidence scenes, as navigation links.

A stale candidate's actions are disabled, with “Records have changed since this was found.” The candidate stays visible until the next refresh.

## 12.3 Overlap review actions

**Same-type pair:**

- Keep A (B merges into A)
- Keep B (A merges into B)
- A continues B / B continues A
- A is a subthread of B / B is a subthread of A (threads only)
- Related
- Dismiss

**Cross-type pair:**

- Pays off (the thread → commitment direction is fixed by §5.3)
- Related
- Dismiss

There is never a canonical choice across types.

A merge (Keep A / Keep B) creates an alias only. It goes through the §5.1 liveness check: a 409 `liveness_mismatch` surfaces as a confirmation (“Merging will hide an open thread behind a closed one”) that resubmits with `accept_status_change: true`. It also offers the explicit due copy.

A temporal `possible_relation` offers Accept (with a before/on/after/by select defaulted to the proposal), Related, and Dismiss.

## 12.4 Closure review actions

For a thread:

- Close thread
- Keep open
- Dismiss finding

For a commitment:

- Fulfilled
- Broken
- Expired
- Keep open
- Dismiss finding

**Applying a closure.** The write calls `set_movement` on the **canonical physical** id, with:

- `title=""`, plus `kind=""` and `due=None` for commitments;
- `scene` = the record's stored `last_scene`, so `last_scene` does not move.

**The optional beat.** The closure may append a beat only if the reader writes or edits one. The candidate's reason is never automatically a story beat. If a beat is given, its scene is the reader-chosen evidence scene, and `last_scene` stays at the later, by play order, of the stored value and that scene. Implement this with a `keep_last_scene` parameter or a restore inside the same lock hold.

**Locking.** `plot.set_movement` takes no lock (store.plot is on the frozen `UNREVIEWED` list), so the route holds `campaign_lock(cid)` across the read, check and write.

**Shared helper.** These writes reuse a helper factored out of `routes/ledger.py`'s `put_thread` / `put_commitment`. That keeps CLAUDE.md's “hand edits to the ledger go through routes/ledger.py” true. If the plan finds that impossible, it amends CLAUDE.md in the same change.

## 12.5 Ledger rows against aliases

- **Rows:** `GET /ledger` returns **effective** rows: the canonical id, merged beats, and `aliases: [{ref, title, status}]`. Alias-source physical rows are not returned at top level.
- **Note line:** the Threads and Commitments sections show “Merged: <alias titles>” on the note line, linking to the Reviewed links / merges group.
- **Edit actions:** Edit, Close/Fulfil and Delete on an effective row act on the canonical physical record. Delete obeys §5.7.
- **Aging:** computed on the effective `last_scene` and `due`.

## 12.6 Reviewed links / merges group

This group lists aliases and links from `GET /continuity`. Each alias offers **Unmerge** (DELETE alias) and each link **Remove link** (DELETE link); both are journalled. Dangling aliases and broken links (§26) show in this group, marked “Broken”, with the same remove actions.

## 12.7 Dismissed group

This group lists suppressions whose fingerprint still matches current records. That `live` flag is computed on read; suppressions for records that have since changed are not shown, because they no longer suppress anything. Each entry has a resolved label and a **Restore** action (`DELETE /continuity/suppressions/{fingerprint}`). Restored candidates reappear at the next refresh.

## 12.8 Journalling

Add undo targets for continuity alias and link mutations:

    {"w": "continuity_alias", "ref": <source ref>}   value: alias record or None
    {"w": "continuity_link",  "id": <link id>}      value: link record or None

Register both in `undo.read_value` / `undo.write_value`. Restoring goes through `continuity.doc.restore_alias(cid, ref, value)` and `restore_link(cid, id, value)`:

- `restore_alias` validates the type pairing and acyclicity. The compare-and-swap covers only the one record, so undoing the deletion of A→B after B→A was created would otherwise write a cycle.
- `restore_link` validates the relation and refuses an equivalent effective link.
- Both raise a continuity-specific error, which `undo.write_value` converts to `UndoConflict` (409), never a 500.

Journal labels name both sides, for example “Mara's map → merged into Winifred's chart”. `before` and `after` are display text only.

Continuity alias and link writes are journalled through `undo.journalled` under the same **best-effort** policy as ledger hand edits: the write is authoritative, and a failed journal append is logged, not raised.

Do not journal derived candidate-file updates or suppressions.

## 12.9 Refresh and stale handling

- **Refresh** starts the §11.1 run. The section shows the run in progress and re-reads candidates when it lands. Refresh is enabled even with no LLM or embeddings connection, and the matching-mode line says what it will do.
- **A `409 stale_candidate`** on apply re-reads `GET /continuity/candidates` only. It never starts a run. The 409 body carries the current records (§22), so the detail can re-render immediately. The reader may resubmit against the new fingerprint after looking.

---

# 13. Temporal pressure service

Create one shared read-only projection, so that Todo, suggestions, graph and reconciliation agree about time.

Public API:

    pressure.build(cid, now=None, horizon=None, sources=ALL) -> dict

`sources` is a subset of {`event`, `holiday`, `birthday`, `deadline`, `linked_deadline`}. A caller that wants deadlines only passes the deadline kinds; Todo may do so for its overdue/due-soon split (§18.3), which Slice B does not take. An unknown source name is a `ValueError`. `horizon` (default `UPCOMING_WINDOW_DAYS`) bounds only the holiday and birthday sources, and `now=None` reads the campaign clock (Slice B plan, Decision 9).

Output:

    {
      "now": "<native>",
      "friendly": "...",
      "fixed": 739000,
      "items": [
        {
          "ref": "event:...",
          "kind": "event",
          "label": "...",
          "native": "...",
          "friendly": "...",
          "fixed": 739003,
          "in_days": 3,
          "relation": "on",
          "state": "upcoming",
          "subject": null,
          "due_text": "",
          "precision": "exact",
          "actor": null,
          "age": null
        }
      ]
    }

`subject` is the commitment ref for deadline kinds, and null otherwise.

`ref` names the dated thing the item measures from (Slice B plan, Decision 4):

| Kind | `ref` | `subject` |
|---|---|---|
| `event` | `event:<eid>` | null |
| `holiday` | `notices.holiday_key(fixed, name)` | null |
| `birthday` | the occurrence ref (§13.6) | null |
| `deadline` | `commitment:<id>` | the commitment ref |
| `linked_deadline` | `event:<eid>` — the linked **event**, not the commitment | the commitment ref |

`label` is the event name, the holiday name, the actor name, or the commitment title for both deadline kinds. `due_text` is the commitment's stored `due` on deadline kinds, and `""` otherwise. The three typed extras that drivers and anchors need (Decision 4):

- `precision` is the occurrence's (`exact` | `yearless` | `month`) for a birthday, `"exact"` for anything else with a `fixed`, and null when `fixed` is null;
- `actor` is a birthday's `<kind>:<id>`, and null otherwise;
- `age` is an exact birthday's age, and null otherwise.

An event's `native` is its stored date, time kept (`"…T20:00"`); all arithmetic is day-level. Items are ordered on the fixed-day axis, undated last, ties broken by kind order, then `ref`, then `subject` (Decision 5).

## 13.1 Sources

Include:

- scheduled events: today's and upcoming;
- holidays: today's and every holiday inside the bounded horizon, not merely the nearest one;
- birthdays: every upcoming birthday occurrence that `birthdays.occurrences` (§13.6) can honestly derive;
- parseable open commitment due dates, via the existing aging arithmetic;
- reviewed temporal links in continuity.json (§13.5);
- overdue commitments;
- passed-but-unresolved event-linked obligations (§13.5).

Pressure **composes** the existing readers: `calendars.upcoming_holidays`, `events.list_events` (the rows `events.upcoming` / `on_day` read; pressure lists every unfired event plus fired ones on today, Slice B plan, Decision 6), `aging.prepare` / `age`, `birthdays.occurrences`, and `clock.now`. It re-implements no scan. Each source fails soft independently, as `notices.pending` does. A calendar failure degrades to undated items, and no dates are fabricated (§26).

The pressure module must not be imported by `clock`.

## 13.2 Horizons

Two concepts:

- **Warning horizon:** the campaign calendar's `warn_days`, which drives urgency and Todo. `warn_days == 0` is legal and means “warnings off”.
- **Suggestion horizon:** `calendars.UPCOMING_WINDOW_DAYS`, the calendar package's established upcoming window.

Limits:

- every parseable deadline and every unfired future event is listed **regardless of horizon**;
- holidays and birthdays are limited to the suggestion horizon;
- a month-only birthday is never given an exact day;
- free-text commitment due strings are not turned into dates unless the calendar provider can parse them.

## 13.3 Structured time links

A free-text commitment due remains untouched.

When reconciliation or user review establishes that a commitment is due before/on/after/by a known event, that is stored as a continuity link:

    commitment:x --before--> event:y

This avoids rewriting “before the bells stop” into a fabricated native date. Pressure uses the event's exact date to determine urgency, while the prose due text is kept for display (`due_text`).

## 13.4 Pressure contract

| Field | Definition |
|---|---|
| `kind` | `event` \| `holiday` \| `birthday` \| `deadline` (parseable due) \| `linked_deadline` (via a temporal link) |
| `fixed` | the target's fixed day on the primary provider; `null` for a month-only birthday, an event whose date the primary provider cannot read (every event, when no provider resolves), and a link to an event whose `fired` stamp is set but whose date cannot be read (§13.5). An unparseable or free-text due produces **no** item at all — the `kind` row and §13.1 restrict `deadline` to a parseable due, and §13.2 keeps free text non-arithmetic — so a calendar failure drops dated deadline items rather than listing them undated (Slice B plan, Decision 8) |
| `in_days` | `fixed(target) − fixed(now)`: negative in the past, `null` when `fixed` is null or no `now` can be read (no clock, or a `now` the provider cannot read) |
| `relation` | `on` for events, holidays, exact/yearless birthdays and deadlines; `in_month` for month-only birthdays; `before` \| `by` \| `on` \| `after` for linked deadlines |

`state` is decided by the first match:

1. `overdue`: a deadline kind with `in_days < 0`, on an unresolved commitment;
2. `passed`: an unfired event whose `passed` reading is true;
3. `today`: `in_days == 0`, or an event on today, fired ones included;
4. `due_soon`: a deadline kind with `0 < in_days ≤ warn_days`; never when `warn_days == 0`;
5. `upcoming`: `0 < in_days ≤ UPCOMING_WINDOW_DAYS`;
6. `stale`: a thread or commitment whose aging state is `stale` (driver pressure only, §14);
7. `ok`.

**High pressure** means `overdue`, `today` or `due_soon`. This is the meaning §15.1 relies on.

## 13.5 Link deadlines and reached events

For `commitment --R--> event`, where the event is dated D:

- the effective deadline is D−1 for `before`, and D for `by` and `on`;
- `after` sets no deadline. It yields only an `upcoming` item, with `in_days` to D.

An event is **reached** when its `fired` stamp is set, or its `passed` reading is true.

A **passed-but-unresolved event-linked obligation** is an unresolved canonical commitment with a before/by/on link to a reached event. Its state is `overdue`. It nominates a `possible_commitment_resolution` (§11.1), but nothing ever resolves it automatically.

When a commitment has both a parseable `due` and link deadlines, it still yields **one** deadline item, which carries `due_text`, the stored prose. The sentences above can disagree, so three readings are fixed (Slice B plan, Global Constraints and Decision 8):

- **Most urgent, then earliest.** Each candidate (the parseable `due`, and each before/by/on link to a dated event) is given its own state, and the item is the most urgent candidate by `PRESSURE_STATES` index, then the earliest `fixed` (nulls last), then `deadline` before `linked_deadline`, then the lowest link id. Among unreached candidates the state is monotone in `in_days`, so this is exactly "earliest wins"; it differs only when a link is reached, which is what keeps "reached → `overdue`" true when an earlier `due` exists, a fired event was re-dated (`events.update` keeps the stamp), or the clock went backwards. The item's `fixed`, `in_days`, `relation` and `ref` come from the chosen candidate.
- **The event's own day.** Reached forces `overdue` on a deadline kind, **except** a `by`/`on` link on its event's own day (`in_days == 0`), which is `today` whether or not the event fired — otherwise advancing onto the event's day (which fires it) would make its `by` commitment overdue before the scene that fulfils it.
- **`after` is only ever `upcoming`.** An `after` link yields its own `linked_deadline` item only while its event is dated, unreached and `0 < in_days ≤ UPCOMING_WINDOW_DAYS`. From D onwards, or further out than the window, it yields nothing, so it is never `today` (high pressure) or `ok`.

**No readable `now`, or no provider** (no clock set, or a calendar that fails): a `deadline` candidate needs a readable `now` and yields nothing without one, so a campaign with no clock has no `deadline` items. A link candidate whose event has a `fired` stamp is still a candidate — reachedness is a stamp, not arithmetic — with `fixed` (null when the date cannot be read) and `in_days: null`, and its state is `overdue`. Any other link candidate needs both a readable `now` and a readable event date (Decision 8).

Dangling or unparseable links contribute nothing, and nor do a thread's links (they become its driver's `time_anchors`, §14) or `related_to`.

## 13.6 Birthday occurrences

Add:

    birthdays.occurrences(cid, now_fixed, window=calendars.UPCOMING_WINDOW_DAYS, *,
                          provider=None, roster=None,
                          visible_characters=True) -> [{
      ref, name, actor, precision: "exact" | "yearless" | "month",
      fixed | None, year, month_key | None, month_name,
      in_days | None, age | None, native, friendly
    }]

- The three positional parameters come first. The keyword-only ones exist because `upcoming`'s callers choose the roster and `visible_characters` (suggestions pass the appearance roster with `visible_characters=True`), and `provider` lets pressure resolve a plugin once. `provider=None` resolves the primary provider (`[]` when none resolves); `roster=None` means the appearance roster (Slice B plan, Decision 2).
- The scan covers `[now_fixed, now_fixed + window]`, **today included** (birthdays always have; events and holidays use `(now, now + w]`).
- `year` is the provider year of the hit, for every precision; `month_name` is `describe(day)["month_name"]` (what `upcoming`'s `"in <month_name>"` renders); `native` and `friendly` feed `AnchorOption` (§14). A month-only row has `fixed`, `in_days` and `age` null, `native` `""` and `friendly` `"<month_name> <year>"`. Rows come in gather order, and within an actor in day order (Decision 3).
- `gather` gains `ref`.
- `upcoming` is a projection of the **same lazy per-actor scan** (`_actor_hits`) that `occurrences` materializes, not of the materialized `occurrences` list: it takes only the first hit per actor, so it stops on the same day it always has and a plugin raising on a later day cannot cost a line that renders today. The existing prompt line is byte-identical (Decision 2).
- Age is computed only for exact birthdays with a known year.

---

# 14. Scene drivers

A driver is a validated, current campaign object that can motivate a next scene.

Required driver kinds:

- thread
- commitment
- event
- birthday
- holiday

Driver shape:

    {
      "ref": "thread:...",
      "kind": "thread",
      "label": "...",
      "summary": "...",
      "actors": ["characters:mara"],
      "status": "advanced",
      "pressure": {
        "state": "ok|stale|upcoming|due_soon|overdue|today|passed",
        "in_days": null,
        "friendly": ""
      },
      "time_anchors": ["event:..."],
      "links": [{"id": "l...", "relation": "pays_off", "other": "commitment:...", "direction": "out"}]
    }

Rules:

- **Actors:** for threads and commitments, use the shared involvement projection (§8).
- **Aliases:** canonicalize before generating drivers.
- **Liveness:** resolved commitments and closed threads are not active drivers.
- **Temporal drivers:** upcoming temporal occurrences (event, holiday, birthday) may be drivers even when no thread links to them. A commitment's pressure comes from §13. A commitment is never itself a time anchor in v1.
- **Links:** reviewed links let a driver expose related obligations without collapsing them. A link is listed on both of its drivers: `direction` is `"out"` on its `a` endpoint, `"in"` on its `b` endpoint, and `"both"` for an undirected relation (`related_to`); `other` is the far endpoint. Links come from `effective.links`, so endpoints are canonical and broken or duplicate links are already excluded (Slice B plan, Decision 13).
- **Offscreen:** with `offscreen=true`, driver `actors` drop PC tokens, exactly as the suggestion snapshot does.

`drivers.snapshot(cid, offscreen=False)` returns:

    {now, friendly, fixed, matching, drivers: [Driver], anchors: [AnchorOption]}

    AnchorOption = {ref, kind: "event" | "birthday" | "holiday", label,
                    native, friendly, fixed | null, in_days | null,
                    precision: "exact" | "yearless" | "month"}

`anchors` is the bounded list of upcoming temporal drivers, ordered by `in_days`, nulls last. The suggestion snapshot (§15), the drivers read route (§21), and the suggestion request validation (§16.3) all use this **one** function.

---

# 15. Scene-suggestion snapshot and prompt changes

`suggest.build_snapshot` currently returns `now`, `friendly`, `notation`, `holidays_today`, `events_today`, a single `upcoming` item, `birthdays`, `story_so_far`, `open_threads` (ids not rendered), `cast` and `available_locations`.

Change it to consume the shared continuity projections.

The snapshot must include:

- **unchanged:**
  - `now`, `friendly` and `notation`. `notation` is what makes Hebrew and plugin-calendar dates parse, and the templates render under StrictUndefined;
  - `holidays_today` and `events_today`;
  - `story_so_far`;
  - `available_locations`;
- `cast`, unchanged except for any needed actor-ref normalization;
- canonical active threads, **with ids rendered** and with dormancy;
- canonical unresolved commitments, with id, kind, due, latest beat, and aging/pressure;
- `timeline`: the pressure items (§13);
- `driver_index`: the drivers from `drivers.snapshot`. The rendered index is capped at `DRIVER_PROMPT_CAP` (40), ordered by pressure-state precedence and then dormancy. Focus, must and anchor refs are always included; the rest is summarised as “and N more”;
- `links`: reviewed links among active drivers.

**Removing `upcoming`.** Only the snapshot's `upcoming` key is removed, once the suggestion templates have moved to `timeline`. Leave these unchanged:

- `today_facts`, `day_facts` and `events.sooner`;
- the per-turn Today block in context assembly;
- the other `today_facts['upcoming']` readers.

`events.sooner`'s docstring forbids the prompt and the snapshot from disagreeing, so the timeline must contain the item that `sooner` would pick.

**Scene intent.** `scene_intent` shares the snapshot and includes `scene_suggestions/user.j2` verbatim. The new sections render behind a `drivers` flag, which `build_intent_prompt` passes as false, so the intent prompt is unchanged.

## 15.1 Prompt instruction

The default batch should be diverse across narrative purposes.

Without explicit focus:

- at least one suggestion should address high pressure (§13.4), if any exists;
- cold or stale threads are worth reviving but are not mandatory;
- near-future temporal anchors are worth using;
- not every suggestion should service the same driver set;
- a quiet character or relationship scene is allowed when no urgent business dominates.

Do not hard-code exactly one category per card. Diversity is the objective, not a four-slot template.

**Byte-identity.** With no structured control set (§16.3 `time_mode: "auto"`, empty ref lists), the instruction section of the system prompt is byte-identical to today's. The snapshot sections (threads with ids, commitments, timeline) do change: that is the point of §15.

**Card order.** The picker shows a limited number of cards (four slots, shared with greetings), so batch-level objectives are judged over cards the reader may never see. The parser therefore orders suggestions so that the card addressing high pressure, and cards covering distinct focus refs, come first. While any structured control is active, the picker shows every generated suggestion (§16.4).

## 15.2 Suggestion output schema

Extend each suggestion the model returns:

    {
      "title": "...",
      "premise": "...",
      "date": "...",
      "cast": [...],
      "location": "...",
      "drivers": [
        {"ref": "thread:x", "action": "advance"},
        {"ref": "commitment:y", "action": "address"}
      ],
      "time_anchor": {
        "ref": "event:z",
        "relation": "before"
      }
    }

Allowed driver actions:

- **Thread:** advance, close_candidate
- **Commitment:** address, fulfill_candidate, break_candidate, expire_candidate
- **Temporal (event, holiday, birthday):** anchor

The suggestion does not itself mutate these records. “candidate” means the proposed scene is plausibly about that outcome, not that playing it guarantees one.

Validation:

- The parser validates every ref against the driver index captured at request time, and drops unknown refs and invalid kind/action pairs.
- `time_anchor` must name a temporal driver from that index. If a `time_anchor` is present, its ref is added to `drivers` with action `anchor`, if missing.
- The parser **never drops a suggestion** for a constraint miss. Each suggestion carries `unmet_must: [ref]` (must refs it does not claim) and `avoided: [ref]` (avoid refs it claims).

What the route returns per suggestion, resolved server-side from the captured driver index:

    drivers:      [{ref, kind, action, label}]          (avoided refs excluded)
    time_anchor:  {ref, kind, relation, label, friendly, in_days} | null
    date, date_friendly, in_days, date_rejected
    unmet_must:   [{ref, label}]
    avoided:      [{ref, label}]

## 15.3 Date derivation

All comparisons use primary-provider fixed days. An anchor's time component is stripped with `split_native`. D is the anchor's fixed day, and d is the suggestion's date.

| Relation | Rule |
|---|---|
| `on` | the date is **derived** from the anchor; the model's date is ignored |
| `on`, month-only birthday | the model's date is kept only if its month key matches, otherwise blanked |
| `before` | now ≤ d < D |
| `by` | now ≤ d ≤ D |
| `after` | D < d ≤ D + `RESOLVE_WINDOW_DAYS` |

**Batch anchor.** With an anchor in the request, every suggestion's `time_anchor` is forced to that ref. Its relation is the request's relation if given; otherwise the model's relation, if valid; otherwise `on`.

**A failing date is blanked**, not the suggestion: `date: ""`, `date_rejected: true`. The card shows “date not consistent with anchor”.

**Unanchored suggestions** keep today's date behaviour (`date_addendum.j2`). `time_mode` (§16.3) may constrain them further.

---

# 16. Scene chooser control surface

Keep the free-text Direction control. Add structured Story Pressure controls.

## 16.1 Driver states

Each active driver may be:

- normal;
- focus;
- avoid;
- must_include.

Semantics:

**normal**  
Available to the generator.

**focus**  
The batch should maximize useful coverage of focused drivers. Several focused drivers do not require every card to contain all of them.

**avoid**  
Do not intentionally advance or address this driver.

**must_include**  
Every generated suggestion must service this driver. At most 3 must refs, and only thread or commitment kinds; temporal constraints go only through the time anchor. When the model misses, the card shows an “unmet” warning chip (`unmet_must`) rather than pretending compliance. When it serves an avoided driver, the card shows an “avoided” warning (`avoided`). The card wording is “claims to address”, because what is checked is the model's self-report.

## 16.2 Controls UX

The controls render inside the **Generated** group, under Direction, as a disclosure that is collapsed by default. Its label states how many drivers are not Normal.

- Drivers are grouped by kind and sorted by pressure state (overdue, today, due_soon, upcoming, passed, stale, ok).
- Each driver has one segmented radio control (Normal / Focus / Avoid / Must), so it cannot hold two states.
- Time is a radio group:
  - **Any date** (default; sends `time_mode: "auto"`);
  - **Stay near current date** (`near`);
  - **Let time move** (`move`);
  - **Choose anchor…** (`anchor`): the anchors list, with friendly date and in-days, plus a before/on/after/by select.
- The anchors list is separate from the NoticeBanner, and both may show the same event.
- Story Pressure state lives in NewSceneChooser beside `direction`. It survives Back, and it resets when the campaign changes.
- The chooser reads `GET /continuity/drivers` **once** when it opens. If that read fails, the Story Pressure controls are hidden, and Direction and Suggest keep working.
- With `matching: "basic"`, the controls render and Suggest/Regenerate stays enabled. The chooser shows no embeddings note.

## 16.3 API request

`POST /scene-suggestions` takes a JSON body `SceneSuggestionsRequest` with plain BaseModel fields:

    after, offscreen, direction, rank,
    focus_refs, avoid_refs, must_refs,
    time_mode: "auto" | "near" | "move" | "anchor",
    time_anchor_ref, time_anchor_relation

An absent body keeps today's behaviour, and the existing query parameters still work for one release. `api.sceneSuggestions` switches to an options object.

`time_mode` semantics:

- `auto`: today's behaviour. No date checks are added, and the instruction section is byte-identical when no other control is set.
- `near`: the prompt asks for dates in [now, now + max(warn_days, 7)], and the parser blanks dates outside that range.
- `move`: the prompt asks for a later, premise-implied date, and the parser blanks dates ≤ now.
- `anchor`: requires `time_anchor_ref`.

**Validation.** Every check runs in the route **before** `runs.run_draft`, because a refusal raised inside the work closure would surface as a failed run rather than a 4xx:

1. Canonicalize every ref through `canonical_refs`.
2. `must ∩ avoid` → 400.
3. More than 3 must refs, or a temporal must ref → 400.
4. `time_mode: "anchor"` with no valid anchor, or a `time_anchor_ref` with any other mode → 400.
5. A ref outside the request-time driver index → 409 `{kind: "stale_drivers", refs}`. The chooser re-reads drivers and shows which selections dropped.
6. Remaining overlaps resolve by precedence:

       must_include > avoid > focus > normal

The `work` closure captures the snapshot and driver index, and passes them to the payload shaper. So parse-time validation and labels use exactly what the prompt showed.

The routing task stays `suggestions`.

## 16.4 Cards show why

A generated card renders validated provenance:

- `date_friendly · <relation> <anchor label>` when anchored;
- one chip per driver, labelled by action: Advances / May close / Addresses / May fulfil / May break / May expire;
- warning chips for `unmet_must` and `avoided`;
- “date not consistent with anchor” when `date_rejected`.

Example using placeholders:

    Midnight at Saltmarch
    Tomorrow · before The coronation
    Advances: Mara's map
    Addresses: Mara's promise

These labels are derived from validated ids, not from model-written explanatory prose.

While any structured control is active, the picker shows every generated suggestion, not only the slots left over by greetings.

## 16.5 Handoff from the Story Graph

“Focus next scene” navigates to `/campaigns/{cid}/scenes` with history state:

    {chooser: {drivers: {[ref]: "focus"}}}

For event, birthday and holiday nodes, “Anchor next scene” uses:

    {chooser: {anchor: {ref, relation: "on"}}}

The handoff then proceeds as follows:

- ScenesView adopts the state once, as it already adopts `seedPrompt`, then replaces it with null, and opens NewSceneChooser seeded with it. The chooser still opens at mode selection.
- The ranked call made when a mode is picked carries the seeded controls.
- A seeded ref missing from the drivers read is dropped, with a visible note.

---

# 17. Saved scene ideas become driver-aware

Scene idea storage gains optional fields:

    drivers: [{"ref": "...", "action": "..."}]
    time_anchor: {"ref": "...", "relation": "...", "native": "..."}

`time_anchor.native` is the anchor occurrence's date, resolved at save time.

Existing files without these fields remain valid. Ideas with no driver metadata are **never** stale.

**Validation lives in `suggest`, never in `scene_ideas.py`.** `scene_ideas` sits on the import path `scenes.lifecycle → scene_refs → scene_ideas`, and must not reach continuity.

On write:

- canonicalize aliases;
- drop refs whose kind/action pair is outside §15.2;
- drop refs whose record does not exist **in any status**. Existence, not activity, is the write test;
- store `time_anchor.native`.

On read:

- canonicalize;
- classify each **stored** ref as:
  - `live`;
  - `finished`: thread closed, commitment resolved, or occurrence past or fired;
  - `dangling`: the record is gone, or an event anchor's stored `native` differs from the event's current date;
- return live and finished refs, each with a `state`, and drop dangling refs from the returned list;
- derive `stale_reason` from the stored refs **before** anything is dropped.

Reads never rewrite scene_ideas.json.

**Re-saving.** When `_standing_match` finds an existing idea, the new save's driver refs are unioned in, by ref. The stored `time_anchor` is kept, unless it was absent and the new save carries one. Provenance is never thrown away.

**SceneIdeaCreate** gains `drivers` and `time_anchor` as optional plain fields. Saving a generated card sends its validated `drivers` (`[{ref, action}]`, excluding avoided refs) and `time_anchor` (`{ref, relation}`).

## 17.1 Derived staleness

Do not add a stored “stale” status.

`stale_reason: "" | "<human-readable reason>"` is non-empty iff the idea stored at least one driver ref or a time anchor, **and** one of the following holds:

- **No stored ref is live.** Reason: “Every thread it was about is closed”, “Every commitment it was about is resolved”, or “Its drivers no longer exist”.
- **The anchor is past.** Its relation is before/by/on and the anchor occurrence is past or fired. Reason: “<anchor> has passed”.
- **The anchor moved.** It is dangling because it was rescheduled. Reason: “<event> moved to <friendly>”.

Holiday and birthday anchors are occurrence refs (§4). A past occurrence is past, even though the holiday recurs.

An active idea with a `stale_reason` stays recoverable:

- it is listed under a collapsed **Stale (n)** subgroup in the picker, excluded from the four-slot budget;
- it stays pickable and adaptable, with its reason shown as a hint;
- the hub's Play next card skips it.

Picking a saved idea anchored `on` an upcoming occurrence uses the server-supplied anchor date, rather than the chooser's `nextDate`.

Dismissed and used semantics are unchanged.

---

# 18. Todo integration

Todo's contract is that it stores and caches nothing, and is recomputed on every read. This capstone amends that contract **narrowly**, in the `chores.py` and `todo.py` docstrings. Todo may read the continuity candidate cache, but only through a live re-validation filter in the same request (§18.2).

## 18.1 Embedding setup chore

When no usable embeddings connection/model is configured, Todo shows one library-scoped, ignorable note:

| Field | Value |
|---|---|
| id | `embeddings` |
| scope | `library` |
| group | Housekeeping |
| severity | `note` |
| n | 1 |
| what | **Semantic matching is not configured** |

Why text:

- Grimoire still uses basic lexical/structural matching;
- semantic matching improves detection when the same story business is phrased differently.

Rules:

- **Fix link:** `/config?section=semantic`. ConfigView honours a `?section=` query parameter (today the section is local state defaulting to Storage). The existing `unpriced` chore's link to Settings may adopt it in the same change.
- **When it shows:** emitted iff `embed_space.resolve() is None` **and** at least one campaign exists. It checks the connection and model, not `semantic_recall_depth`: if a connection and model are configured but semantic recall depth is zero, it is not shown.
- **Outages:** a transient endpoint outage is not “not configured”, and does not create this chore.
- **Placement:** it appears on the global To do page only, like every library chore.

## 18.2 Continuity-review chores

Campaign Todo uses the cached candidate file only. It never generates anything.

| Chore id | Counts | Group | Severity |
|---|---|---|---|
| `continuity-overlaps` | possible_duplicate + possible_relation | Continuity | note |
| `continuity-closures` | possible_thread_closure + possible_commitment_resolution | Continuity | note |

**Live filter.** Each count runs after a live filter in the same request, which drops candidates that are:

- suppressed in continuity.json;
- referring to refs that are missing or no longer canonical;
- lifecycle candidates whose target is already closed or resolved;
- stale by fingerprint.

The filter costs three small JSON reads per campaign: continuity.json, the candidate cache, and plot/commitments. There is no involvement, chronicle, calendar or embedding work. If the cache file is absent, no chore is shown.

**Registration and items.** Both chores are registered in `CAMPAIGN_BUILDERS` and `ITEMS`. Items are shaped `{id: candidate-id, label, detail, fix}`, with `fix` the §12.1 candidate address. There is one chore per kind group, never one per candidate.

## 18.3 Timeline chores

Only the existing `owed` chore is in scope:

- it counts **canonical** commitments;
- it is relabelled “N open commitments with a deadline”;
- it may call `pressure.build(cid, sources={"deadline", "linked_deadline"})` to split overdue and due-soon counts.

Slice B takes **neither** of the optional parts (Slice B plan, Decision 16). `owed` counts exactly today's rule made canonical — a live canonical commitment whose `due` is non-empty, parseable or not — and calls neither `pressure` nor `aging`: deadline pressure resolves the calendar provider, which is plugin code, and the overdue/due-soon split would put that on the global To do page. Link deadlines are **not** counted yet: counting them would change `owed` for a campaign that has links but no aliases, and would load all three ledgers through `effective.links` for every campaign. Both arrive together with the split, in a later slice. Todo's set is therefore a *stated* deadline, dated or not; pressure's is dated deadlines only.

Todo never requests birthdays or holidays. `live('')` runs every campaign builder for every campaign, birthday gathering reads every visible character's metadata, and holidays run user plugin code, so either would break Todo's cost rule.

Other time chores may use the same service later, but are out of scope for this milestone.

---

# 19. Story Graph

Add a campaign-level Story Graph page.

It is a read-only projection, plus navigation and filter controls. Opening it makes no model or embedding calls.

## 19.1 Placement

The page is at `/campaigns/{cid}/graph`, built on `PageShell`.

**Rail row.** It goes after the Ledger row in `CAMPAIGN_ROWS`:

    {id: "graph", label: "Story graph", icon: <an unused literal glyph>,
     to: campaignPath(ctx, "/graph"), match: isUnder(...)}

It has no tail, and `GET /api/shell` gains no field. `rail.test.ts` adds `/campaigns/c1/graph` to its PATHS list.

**Context column.** The column has three `ColumnSection`s:

- **Lens:** four rows (§19.5);
- **Show:** toggles for actors, locations, merged records, and review candidates;
- **Arcs:** one row per thread or commitment; selecting one selects that node.

**Main.** Main holds the drawing in a horizontally scrolling pane, and the selected node's detail **below** the drawing, outside it.

**URL state.** `?lens=`, `?arc=` and `?node=` live in the URL.

## 19.2 Node types

Required for completion:

- scene;
- character/PC;
- location;
- thread;
- commitment;
- event;
- saved scene idea (stored ideas only).

Timeline markers:

- birthday;
- holiday.

Optional, behind filters if implemented in this milestone:

- standing facts;
- groups.

Relationships are actor–actor edges, not nodes.

Do not dump all lore/items/creatures by default.

**Dates.** Every dated node carries `native`, `friendly`, `fixed: number | null` (the primary provider's fixed day) and `in_days: number | null` (relative to the campaign clock). The payload has a top-level `now: {native, friendly, fixed}`. The frontend positions dated nodes only by `fixed` / `in_days` and **never parses `native`**: native dates are provider strings, and sorting them is alphabetical by month name. A node whose `fixed` is null goes in an “Undated” bucket, never at a guessed position.

## 19.3 Edge types

Deterministic (`source: "structural"`):

- `appeared_in` (actor → scene);
- `occurred_at` (scene → location, from location history);
- `opened_in` (thread or commitment → scene): the first beat's scene;
- `advanced_in` / `touched_in` (thread or commitment → scene): each beat's scene;
- `closed_in` / `resolved_in` (thread or commitment → scene): `last_scene`, when closed or resolved;
- `involves` (thread or commitment → actor, from §8);
- `serves` (idea → driver, from the idea's `drivers`);
- `anchored_to` (idea → event, birthday or holiday, with a relation);
- `feeling` (actor → actor, from relationship meters);
- `bond` (actor ↔ actor);
- `birthday_of` (birthday → actor).

Beats carry no status, so `opened_in`, `advanced_in` and `closed_in` are derived exactly as described.

Reviewed continuity links (`source: "reviewed"`, `relation` set):

- continues;
- subthread_of;
- pays_off;
- related_to;
- before/on/after/by.

Alias provenance (`source: "alias"`): `merged_into`, shown only when “Show merged” is enabled.

Candidates (`source: "candidate"`, `candidate_id` set) render as dashed suggestion edges when “Show review candidates” is enabled.

Never render an embedding score as an asserted story relation.

## 19.4 Layout

The default **Story** lens is play-order oriented, not a force-directed hairball.

- **Spine:** every scene, absorbed or still in play, in **scene-id order** (the order `timeline.build` emits; never `list_scenes`, which sorts by `updated`). Play order is deliberately not date order, because flashbacks exist.
- **Now boundary:** drawn immediately after the last scene. Everything left of Now is played history.
- **Right of Now:**
  - upcoming events, holidays, birthdays and parseable deadlines, by ascending `in_days`;
  - then active saved ideas, dated ones by `in_days` and undated ones in a trailing “Unscheduled” column.
- **Lanes:** thread and commitment nodes and arcs sit in lanes connected to the scenes that moved them.
- **Density:** actors and locations are secondary nodes, toggled to reduce density.
- **Fired events:** fired or passed events are not on the Story spine. They appear in the Calendar lens and in node detail.

The **Calendar** lens places scenes by their opening date's `fixed`, and puts Now at `now.fixed`.

The layout is hand-built: absolutely positioned HTML node buttons over one SVG edge layer, following the precedent of `PlotMapEditor`, with no new dependency. It must stay useful on Android and narrow screens:

- tap selects;
- every node is a `<button>` whose accessible name includes its label and status;
- there is no hover-only information, and nothing essential lives only in a `title`;
- local horizontal scrolling is acceptable;
- filters reduce density;
- selected-node detail renders in main outside the drawing container, and at phone width it is reachable without horizontal scrolling.

## 19.5 Lenses and filters

Required lenses:

- **Story:** scenes + threads + commitments + events.
- **Cast:** actors + scenes + relationships (`feeling` / `bond`) + involvement.
- **Calendar:** scenes + dated events, holidays, birthdays and deadlines, on the fixed-day axis. This is named “Calendar” rather than “Timeline” because the Timeline page already shows play order.
- **Continuity:** aliases, reviewed links, pending candidates.

Lenses and toggles are **client-side presets over one payload**, and switching them issues no request.

Page-level keyboard bindings (switching lens, Escape to clear the selection) go through `useHotkeys`, with a label and group so the `?` sheet lists them. A node is activated by its own button.

## 19.6 Node detail

Selecting a thread or commitment shows:

- the canonical title;
- physical aliases;
- the current status;
- the latest beat;
- touched scenes;
- involved actors;
- pressure;
- reviewed links;
- candidate findings.

Actions:

- Focus next scene (§16.5);
- Open ledger entry (§12.1 address);
- Filter to this arc: sets `?arc=` and shows that record, its aliases and link neighbours, the scenes it touched, and the actors it involves.

Selecting an event, birthday or holiday shows its date, in-days, linked records, and **Anchor next scene** (§16.5).

Selecting an actor shows:

- scenes;
- related active drivers;
- relationships;
- an upcoming birthday, if one is recorded.

Selecting a scene shows:

- cast;
- location/date;
- plot/commitment movements;
- (mechanics may be added in Mechanics II later).

## 19.7 Backend graph API

    GET /campaigns/{cid}/continuity/graph

This is a deterministic endpoint. Its response is normalized nodes and edges, not UI coordinates; the frontend owns layout.

- **No lens parameter.** The endpoint returns one uncapped payload. There is no `truncated` field in v1: lenses are client-side, so a cap would silently omit nodes from a lens.
- **Cost:** it walks scenes, chronicle and the ledgers once per request, and uses `best_effort_campaign_lock`. Actor names come from roster and overlay summaries, never from per-node full card reads. There are no image-directory scans.

---

# 20. Graph and driver API types

Define typed backend and TS unions, not open string bags.

Python declares the tuples `NODE_KINDS`, `EDGE_KINDS`, `EDGE_SOURCES`, `DRIVER_KINDS`, `DRIVER_ACTIONS`, `PRESSURE_STATES` and `LINK_RELATIONS`. The TS unions mirror them, and a backend test pins the tuples so a change shows up in review.

**Nodes** are a discriminated union on `kind` ∈ scene | character | pc | location | thread | commitment | event | idea | birthday | holiday.

- `id` is the §4 canonical ref, with the kind→prefix mapping stated once (§4).
- Each kind has typed fields; there is no `meta` bag.

Example:

    {
      "id": "thread:maras-map",
      "kind": "thread",
      "label": "Mara's map",
      "status": "advanced",
      "aliases": [],
      "pressure": {"state": "stale", "in_days": null}
    }

**Edges:**

    {
      "id": "...",
      "kind": "advanced_in",
      "from": "thread:maras-map",
      "to": "scene:012--...",
      "source": "structural",
      "relation": null,
      "candidate_id": null
    }

`source` ∈ structural | reviewed | candidate | alias.

Do not put rendered prose into node fields when the frontend can derive it from typed fields.

---

# 21. Routes

All continuity routes live in one router, `routes/continuity.py`, included **before** `entities` in `routes/__init__.py`. The generic `/campaigns/{cid}/{kind}[/{eid}]` routes would otherwise capture `/continuity` and `/continuity/graph`. The new crossings are pinned in `tests/test_route_order.py`.

Read (pure; no model or embedding calls):

    GET    /campaigns/{cid}/continuity              aliases, links (raw + effective), suppressions, diagnostics, malformed, matching
    GET    /campaigns/{cid}/continuity/candidates   §12.2
    GET    /campaigns/{cid}/continuity/graph        §19.7
    GET    /campaigns/{cid}/continuity/drivers?offscreen=   §14 drivers.snapshot

Compute:

    POST   /campaigns/{cid}/continuity/reconcile    202 {run}; §11.1

Review writes:

    POST   /campaigns/{cid}/continuity/aliases                  {ref, to, replace?, accept_status_change?, note?}
    DELETE /campaigns/{cid}/continuity/aliases?ref=<source ref>
    POST   /campaigns/{cid}/continuity/links                    {a, b, relation, scene?, note?}
    DELETE /campaigns/{cid}/continuity/links/{link-id}
    POST   /campaigns/{cid}/continuity/candidates/{candidate-id}/apply
    POST   /campaigns/{cid}/continuity/candidates/{candidate-id}/dismiss   {decision: "dismiss" | "keep_open"}
    DELETE /campaigns/{cid}/continuity/suppressions/{fingerprint}

Aliases are deleted by `?ref=`, because refs contain `:` and model-written plot ids may contain `/`.

**Apply body.** There is one apply endpoint, whose body is a flat, plain-BaseModel `ContinuityApply`:

    {op, canonical, from, to, relation, status, beat, scene,
     expect_fingerprint, accept_status_change, copy_due}

- `op` ∈ alias | link | close | keep_open | resolve.
- `test_pydantic_guard` forbids `Field`, validators and unions, so every field is a plain optional value, validated in the handler.
- `expect_fingerprint` defaults to the candidate's cached fingerprint.

All campaign mutations take `campaign_lock`. Refusals use the established shape `HTTPException(409, detail={"kind": ..., "detail": ...})`.

Changes to existing routes:

- `PUT /chronicle` gains the reconcile trigger (§11.1);
- `POST /scene-suggestions` gains the body (§16.3);
- `GET /ledger` returns effective rows (§12.5);
- the Ledger PUT routes redirect alias sources (§7.2);
- `DELETE` on threads, commitments and events cascades (§5.7);
- `GET`/`POST /scene-ideas` handle driver metadata (§17).

---

# 22. Concurrency and stale-review rules

A candidate was computed against specific source records. Applying it later must prove those records still mean what was reviewed.

**On apply,** inside one `campaign_lock` hold:

1. resolve current aliases (strict canonicalization);
2. reload the source records;
3. check every evidence scene still exists;
4. recompute the candidate's fingerprint (§5.5);
5. if it differs from `expect_fingerprint`, or an evidence scene is gone, return 409:

       {kind: "stale_candidate", detail, current: {fingerprint, records: [current effective rows]}}

   and write nothing;
6. validate **every** part of the operation (refs, relation compatibility, liveness, status) before the first write;
7. write in the order: status → alias/link → suppression/cache cleanup. The status write and the alias/link write are each in their own `undo.journalled` block;
8. remove the candidate from the cache in the same hold.

**Partial writes.** `docs/store-guarantees.md` promises no cross-record transaction. Only an I/O error can stop the sequence part-way, because every validation precedes the first write. If one does, the 500 names the parts that landed, each of which is independently undoable.

**Dismiss** writes the suppression and removes the candidate from the cache in one hold.

**Reconcile persist.** A reconcile persist that races an apply or a dismiss cannot resurrect the candidate. The persist re-reads suppressions, aliases and links under its own hold, and recomputes fingerprints (§11.1).

No multi-campaign locks are introduced.

---

# 23. Prompt templates

All prompt text stays under templates/.

New template families:

    templates/continuity_identity/{system,user}.j2
    templates/continuity_reconcile/{system,user}.j2

Reuse the snippets for thread and commitment lines where the semantics match. Do not copy old line formatting into new templates when one shared snippet can serve both without obscuring purpose.

For each new family:

- `store/continuity/{identity,reconcile}.py` provides `build_prompt(...)` and `parse_output(text)`. Parsing goes through `absorb.extract_object`.
- `scripts/verify_templates.py` gets a builder-vs-render block for representative inputs.
- templates/README.md gets a section documenting variables and reply shape.
- `backend/tests/test_llm_fakes.py` `_rendered_prompts()` gets an entry, and `tests/fixtures/llm/campaign_flow.json` an entry keyed on a phrase unique to the new system prompt.

Changed families:

- **absorb:** the identity fields (§10.1), and canonical ids in the snapshot;
- **scene_suggestions:** ids, commitments, the timeline, the driver index, the action vocabulary, time modes and the anchor rules. Update its `verify_templates.py` FULL_SNAP, the `test_llm_fakes` render input, and the `campaign_flow.json` matcher wherever their wording moves;
- **scene_intent:** unchanged, via the `drivers` flag.

Prompt contracts must be explicit JSON with fixed enums. The parser rebuilds the known fields; it never passes arbitrary model JSON downstream.

---

# 24. Parsing and validation

Every model-returned field follows the current tolerant-parser discipline:

- **A reply with no decodable object** is a *failed* phase or run, not an empty answer. The identity block is `failed`, with hints attached; a reconcile is `failed`, with deterministic candidates kept. `extract_object` returns None precisely to keep these apart.
- **A decodable object** with nothing usable → no proposals.
- **Unknown ids** → dropped.
- **Unknown enum** → `uncertain` / no-op.
- **Malformed list element** → skipped.
- **Unknown evidence scene** → dropped.
- **Missing required evidence** for a destructive lifecycle proposal → downgraded to `uncertain`.
- **Disallowed direction/type** combination → `uncertain`.
- **No exception** may be raised solely because the model produced bad JSON.

Store and calendar errors beneath the parser keep their existing failure semantics. Do not broadly catch programmer or store bugs as “bad model output”.

---

# 25. Performance and cost

## 25.1 No N+1 LLM reconciliation

- At most one identity-resolver call per absorb, and only when ambiguous proposed-new records exist.
- At most one reconciliation call per run (End Scene or refresh), on a bounded, prioritized set of candidates that have no cached proposal (§11.1).
- Do not call an LLM for every pair.

## 25.2 Candidate generation complexity

There is no numpy (Android). Pure-Python dot products are O(d) each, so all-pairs is O(n²·d). The sweep therefore:

- runs in a worker thread, never on the event loop;
- restricts pair scoring to changed × all (incremental) or all × all (explicit refresh), under a hard `RECONCILE_MAX_PAIRS` cap. When pairs are dropped at the cap, the run reports `pairs_capped: true`;
- computes identity texts once;
- loads cached vectors once;
- embeds bounded misses in batches (§9.4);
- computes dot products in memory;
- keeps top-k / above-floor candidate pairs;
- dedupes unordered pair ids.

No vector DB.

## 25.3 Graph read cost

The graph endpoint may walk scenes, chronicle and the ledgers once per request.

Avoid per-node full character reads where roster or overlay summary APIs already provide names.

No image-directory scans merely to render graph nodes.

## 25.4 Candidate cache

Todo and shell badge reads must not perform embedding calls or full transcript reads. Todo's continuity chores use the §18.2 live filter only. No new shell badge is added in this milestone.

---

# 26. Failure/degradation matrix

| Failure | Required behavior |
|---|---|
| No embeddings configured | Lexical/structural matching works; the Todo setup note appears (global To do) |
| Embedding endpoint unavailable | Keep deterministic candidates; log failure; mode `failure`; no failed scene/wrap-up |
| Embedding cache corrupt | Existing vectors.py miss/re-embed behavior |
| Embedding width mismatch | Off-width vectors forgotten and re-embedded (§9.4) |
| Identity resolver LLM fails / undecodable | Stage first-pass rows; identity block `failed`; rows carry hints and alternatives; no hidden data loss |
| Reconciliation LLM fails / undecodable | Deterministic candidates persist (first persist already landed); run `failed` with `error.saved: true`; wrap-up succeeds |
| Reconcile run cannot be reserved | Save response unaffected; logged; next End Scene or refresh catches up |
| continuity.json malformed | Read enhancements omit per section; mutators refuse overwrite; `malformed` reported |
| candidate cache malformed | Treat as empty/rebuildable; no campaign failure |
| dangling alias | Source record remains visible/effective; Continuity review lists it as Broken |
| dangling or self-collapsing reviewed link | Omitted from drivers and graph edges; listed in `diagnostics.broken_links`; Broken in review |
| deleted record with aliases/links | §5.7 cascade; 409 for alias targets unless forced |
| calendar unavailable | Temporal arithmetic degrades to undated/free-text; no fabricated dates |
| stale candidate apply | 409 with current records; no partial write |
| concurrent reconcile runs | One live per campaign; newest generation wins at persist |
| undo of a continuity row that would now be invalid | 409 UndoConflict |

---

# 27. Migration and backward compatibility

No mandatory one-time migration.

All new files are absent in old campaigns, and read as empty state.

Existing plot and commitment records are untouched until the reader explicitly reviews a status change. By the identity law (§7.1), every effective reader produces byte-identical output for a campaign with no aliases.

Existing scene ideas without driver metadata remain valid, and are never stale.

Existing scene-suggestion clients must tolerate new fields. The query-parameter request form keeps working for one release (§16.3).

No background rewrite of old campaigns on app start.

A user may explicitly run continuity reconciliation on an old campaign to populate candidates.

Fixture and gate work this forces:

- `tests/store_api_baseline.json` is regenerated deliberately in the change that adds `store.continuity`;
- `frozen_campaign/sweep.py` gains the continuity projections, and `snapshot.json` is regenerated deliberately. The fixture's `home/` has no continuity.json, which proves absent-file reads; `home/` itself is never regenerated;
- the CLAUDE.md detached-run inventory and `CONTRIBUTING.md` guard table are updated;
- the `scene_refs` docstring store count is updated.

---

# 28. Evals and tests

This work changes model contracts. It needs both deterministic unit tests and LLM eval coverage.

## 28.1 Store unit tests

Alias:

- thread alias canonicalization;
- commitment alias canonicalization;
- transitive aliases;
- cycle rejection, at create and at undo-restore;
- missing target degrades safely;
- identity law: an unaliased record projects byte-identically to `open_threads` / `open_commitments`, including a status-only move whose `last_scene` is newer than every beat;
- canonical effective beat union, with stable order for uncomparable scenes;
- exact duplicate beat dedup across records only;
- removing an alias restores two effective records;
- wrong-type alias rejected;
- liveness-mismatch refusal and `accept_status_change`;
- writers addressing an alias source are redirected (ledger PUT, absorb materialization).

Links:

- relation/endpoint compatibility per §5.3;
- symmetric related_to dedup;
- link id stable across a later alias of an endpoint;
- dangling refs tolerated on read and listed in diagnostics;
- journal/undo add/delete.

Suppressions and fingerprints:

- same unchanged candidate stays suppressed;
- a new beat does **not** invalidate a pair suppression;
- a changed latest beat invalidates a lifecycle suppression;
- canonicalized aliases produce a stable pair fingerprint;
- a scene rename does not invalidate a lifecycle fingerprint.

Other substrate:

- reader tolerance per section, and writer refusal;
- unknown keys preserved;
- `repoint_scenes` covers `links[*].scene`;
- `forget_ref` cascade on thread, commitment and event delete;
- `has_merged_records` 409;
- the writer guard (§11.6).

## 28.2 Similarity tests

Use fake vectors, never real external embedding calls.

- normalized-title exact match;
- semantically different identical-slug safety, including `untitled`;
- lexical fallback with embeddings off;
- top-k candidate retrieval;
- width mismatch: off-width vectors forgotten;
- embedding failure preserves lexical candidates;
- cross-type records never enter the duplicate candidate set;
- a partial batch failure keeps earlier saved chunks;
- the warm limit is honoured.

## 28.3 Absorb identity tests

- the model proposes new with no close candidate → it stays new, and **no resolver call is made**;
- a close lexical candidate → the resolver can map it to existing, and the row is rewritten per §10.2 (status never `open`);
- the resolver names an unknown id → `uncertain`;
- the resolver names a closed/resolved neighbour → `uncertain`; never reopened;
- two rows map to the same existing record → the second is downgraded;
- the resolver fails or returns undecodable output → the staged new row survives, with hints and alternatives;
- one batch call for several ambiguous records;
- existing citation/review fields survive identity resolution;
- an uncertain or unchecked row is band `low`;
- alternatives carry valid `before` tokens that pass `check_conflicts`;
- the `identity` phase row appears, with status;
- existing absorb tests stay call-count-stable.

## 28.4 Reconciliation tests

- closure requires a known evidence scene;
- a duplicate proposal does not mutate anything until apply;
- applying a duplicate writes an alias only;
- thread/commitment high similarity becomes a relation candidate, never a merge;
- a passed deadline nominates a commitment but does not resolve it;
- a reviewed temporal link contributes to pressure;
- a stale fingerprint returns 409 with current records;
- a reconcile persist after a dismiss does not resurrect the candidate;
- a superseded run does not overwrite a newer cache;
- the LLM is called only for candidates without a cached proposal, capped and prioritized;
- `PUT /chronicle` replay does not start a run; a fresh commit does; a reservation failure does not affect the save response;
- the explicit refresh works with no connection (deterministic only).

## 28.5 Pressure tests

For Gregorian, Hebrew, and a fake CalendarProvider:

- several upcoming holidays are retained, not only the nearest;
- events and holidays are ordered on the fixed-day axis;
- exact birthday date and age;
- a yearless birthday gets no invented age;
- a month-only birthday gets no invented day (`in_days` null, relation `in_month`);
- parseable commitment `due_in`;
- free-text due remains non-arithmetic;
- a reviewed commitment-before-event derives urgency (D−1);
- `after` sets no deadline;
- earliest of due and link deadlines wins;
- the state precedence table, including `warn_days == 0`;
- a backwards or corrected campaign clock behaves as the existing aging/event rules require;
- `sources` restricts the work done (birthdays are not gathered when not requested).

## 28.6 Suggestion tests

- the snapshot contains ids for threads and commitments, plus `now`, `notation`, `holidays_today` and `events_today`;
- the intent prompt is unchanged;
- with no controls, the instruction section is byte-identical;
- returned drivers are validated; an unknown driver is dropped;
- the anchor is validated and auto-added to drivers;
- focus/avoid/must precedence; must∩avoid → 400; must cap → 400; stale refs → 409;
- an anchored `on` date is derived deterministically;
- an invalid before/after/by date is blanked with `date_rejected`, and the suggestion is kept;
- `near` / `move` blanking;
- `unmet_must` and `avoided` populated;
- card provenance uses resolved labels;
- a canonical alias prevents duplicate drivers;
- card ordering puts the high-pressure card first;
- a saved idea's `stale_reason` derives after resolution, from a past anchor, and from a rescheduled event;
- a re-save unions provenance.

## 28.7 Todo tests

- a missing embedding connection/model produces one library chore on the global To do;
- embeddings configured with semantic recall depth 0 produce no setup chore;
- no embeddings still shows continuity candidate chores;
- Todo performs no embedding or client call;
- pending candidate counts come from the cache after the live filter;
- dismissed, suppressed, stale and already-closed candidates do not count;
- an absent cache → no chore;
- `owed` counts canonical commitments.

## 28.8 Graph backend tests

- scenes are ordered by ascending scene id, using a fixture whose `updated` stamps and dates disagree with play order;
- actor appearance edges;
- location edges;
- thread/commitment beat edges (opened/advanced/closed);
- involvement edges;
- future event/idea nodes carry `fixed` / `in_days`, and undated ones carry null;
- an alias is hidden by default, and `merged_into` is present with source `alias`;
- reviewed links carry source `reviewed`;
- candidate edges carry source `candidate` and `candidate_id`;
- a malformed optional file does not fail the graph;
- no model or embedding client is touched.

## 28.9 Frontend tests

Continuity review:

- the section and its group counts render in the column;
- a candidate opens a read-only detail;
- apply requires an explicit action;
- the canonical side is selectable;
- a liveness-mismatch confirmation resubmits with the flag;
- a stale 409 re-renders current records and does not start a run;
- a dismissed finding is restorable;
- deep links open the right group and candidate;
- the matching-mode line renders “Basic matching active”.

Scene chooser:

- the drivers read happens once on open; a failure hides the controls;
- driver controls alter the request body;
- cards show validated reasons and warning chips;
- with `matching: "basic"`, Suggest/Regenerate stays enabled;
- a saved stale idea remains selectable and adaptable under Stale;
- Story Graph handoff state seeds the controls.

Story Graph:

- filter presets;
- tapping a node shows its detail;
- the Now boundary and future nodes;
- narrow width: at `innerWidth` 375, tapping an Arcs row or a node button renders that node's detail in main, outside the drawing container;
- no hover-only essential information: every node is a button with an accessible name that includes its label and status;
- no generation call: across mount and every lens or toggle change, the only API call is the graph read, made once.

## 28.10 LLM eval cases

Add synthetic cases using the established placeholder names. Each case ships a `<case>.compliant.json` and at least one counterexample declaring its exact failing checks. `prompt.*` checks render the enum and decision instructions verbatim. Behavioural claims (does the model *choose* correctly) are answered only by `evals/run.py --live`.

At minimum:

1. Same obligation, different wording → identity resolver selects existing.
2. Same topic, distinct plot questions → distinct.
3. Broad thread and concrete continuation → continuation, not duplicate.
4. Thread and commitment about the same incident → pays_off/related, never duplicate.
5. Thread whose accumulated beats clearly answer its question → close.
6. Old but unresolved thread → keep_open.
7. Deadline passed and promise demonstrably kept → fulfilled.
8. Deadline passed but the evidence does not establish an outcome → keep_open/uncertain.
9. Two focused drivers with several valid alternative scenes → suggestions distribute coverage rather than cloning one premise.
10. Anchor date in custom calendar notation → parser/date derivation remains valid.

There is no scene-suggestion eval case today. Add a `scene-suggestions` case and grader for cases 9–10.

---

# 29. Observability

Record model usage under distinct task labels, hyphenated like the existing ones:

- `continuity-identity`;
- `continuity-reconcile`;
- the existing scene-suggestion task keeps its current name, `suggestions`.

Routing: add one route to `store/routing.py`:

    Route("continuity", "Continuity checks",
          "The duplicate check beside absorb and the reconciliation sweep after End Scene or a refresh.",
          ("continuity-identity", "continuity-reconcile"), True)

`CONFIG_KEYS`, `config._CONFIG_KEYS`, connection-delete cleanup and the frontend picker all derive from it. `test_routing_guard` scans only `routes/`, so these must live in `routes/`, with literal task strings:

- the `_require_connection(...)` call;
- the `store.usage.meter(...)` call;
- the provider call.

Prompt building and parsing live in `store/continuity/`.

Structured log rows go through `store.logs.record` with counts only:

- campaign id;
- candidate count;
- deterministic vs semantic candidate counts;
- embedding mode (configured/off/failure);
- resolver decision counts;
- pairs capped;
- superseded.

No private narrative text goes into these rows.

Do not log full identity texts, beats, or prompt content in generic logs. Note in the docs that the Debug-level incoming response capture still records model replies (including reasons), under the existing Settings disclosure.

---

# 30. Documentation/UI wording

User-facing language.

Prefer:

- “Possible overlap”
- “May be finished”
- “Needs resolution review”
- “Basic matching active”
- “Semantic matching not configured”
- “Merged into”
- “Continuation of”
- “Claims to address”

Avoid:

- “AI detected duplicate” as a certainty;
- “Continuity score”;
- “Broken campaign”;
- “Embedding required.”

The README may gain a concise mention of reconciliation and the Story Graph after implementation. Implementation details belong in docs.

---

# 31. Suggested implementation slices

This is a design spec, not the required implementation plan. The later plan may rearrange tasks, but the dependencies strongly suggest this order:

### Slice A — canonical continuity substrate

- continuity.json reader/writer (`doc`);
- aliases, links, suppressions;
- canonicalization, fingerprints, candidate ids;
- effective thread/commitment projections under the identity law;
- shared involvement helper (briefing refactor first, with no behaviour change);
- alias-source redirect in the ledger;
- scene-rename fan-out and delete cascade;
- journal/undo targets;
- the continuity router's read and alias/link write routes;
- lock-domain classification.

### Slice B — temporal pressure and drivers

- `birthdays.occurrences`;
- pressure service and contract;
- driver projection and `GET /continuity/drivers`;
- canonical context, briefing, digest, ledger and shell readers.

### Slice C — similarity and absorb identity hardening

- identity text;
- lexical signals;
- embedding reuse and mechanics;
- plot slug-collision fix;
- batched identity resolver, its template family and eval cases;
- absorb integration (phase, alternatives, review chip);
- the Settings embeddings disclosure (§9.5), if C ships before D. It must ship no later than the first automatic embedding call.

### Slice D — reconciliation candidates/review

- candidate cache;
- deterministic discovery;
- `reserve_campaign_background` and the two triggers;
- reconciliation prompt/parser and eval cases;
- review routes, stale guards and the writer guard;
- Ledger addressing and the Continuity review UI;
- Todo candidate and setup chores.

Slice D implementation deviations. Each was decided in the Slice D plan (`docs/superpowers/plans/2026-10-05-continuity-capstone-d-reconcile-review.md`) and is cited by its Decision there; where the slice ledger refined one during implementation, the bullet says how.

- **A seventh module, `continuity.pending`** (Decision 1), holds the current fingerprint, the verdicts and the live filter, so the review read, Todo, apply and both persists cannot disagree. It goes beyond the module list above, and `review` and `reconcile` import it (§7.0's table now carries its row).
- **`runs.reserve_campaign_background` returns `tuple[Run, bool] | None`**, not `Run | None`, and `start_or_existing` gains `single_live`, so a second start is handed the live run (Decision 5; §11.1).
- **The automatic trigger also fires on a journalled resume** of `PUT /chronicle`, because §11.1 excludes only the idempotent replay (Decision 6).
- **Model-only nominations** (temporal pairs and touched-record lifecycle re-checks) are persisted only with a proposal; a declined one is cached as `settled` and hidden (Decision 9). §6's "`proposal` is `null` when no model adjudicated" still holds for every persisted deterministic finding. A declined nomination whose records later move reads `gone`, not `stale`, so it stays hidden and the next nomination re-asks the model (Decision 2, as refined in the slice ledger).
- **`warm_window` is seeded with `cid` plus the run stamp**, not `cid` alone, and `similarity.semantic` gains `warm_limit` and `loaded` (Decision 12; §9.4). With a rotating seed the sweep embeds its required texts and its warm window in separate `embed_missing` calls, and retries a failed required call once over a rotating proper subset of it, so one refused text cannot keep every changed record in its chunk unembedded; the count embedded stays within `RECONCILE_WARM_LIMIT` (slice ledger, Task 4).
- **§11.2's active and unresolved records are sent as the records the capped candidates name** (Decision 13).
- **Event sides of temporal pair fingerprints** are `{title: name, due: date}` (Decision 3; §5.5).
- **The generation fence ignores a stored generation from this machine's future**, and one that is not a generation stamp at all (Decision 4; §11.1).
- **The apply body is a `dict`, not a `BaseModel`** (Decision 15; §21), because the flat body's `from` key cannot be declared without `Field(alias=...)`, which the pydantic guard forbids.
- **`copy_due` is honoured inside apply**, as a separate journalled write through `routes/ledger.py` (Decision 16), instead of a second client PUT (§5.1).
- **Dismiss refuses a stale finding** unless the optional `expect_fingerprint` it gains (beyond §21's `{decision}`) equals the current fingerprint (Decision 17; §12.9, §22).
- **Additive response fields** (Decisions 23, 24): the candidates read adds `beats`, `due`, `stale_reason`, `names`, `scenes` and `run`; `GET /continuity` suppressions add `live` and `titles`; a reconcile run's result carries `follow_on` and `continuity`, and a failed run's `error` carries `sweep` and `saved` (whether persist 1 landed). Refresh chooses its failure note from `saved`: “basic findings are listed” only when it is true; a refused start, or a run that failed before persist 1 landed (`busy`, `io`, `malformed`), says nothing it found was saved; a failure that says neither says only that the refresh did not finish. A `malformed` refusal at persist 2 says the model's suggestions were not saved.
- **A review staged before a merge is refused at save** with 409 `edits_target_merged`, not re-staged (Decision 19; Slice C deviation 12).
- **The Todo live filter also reads events.json**, for temporal link ids and event sides, beyond §18.2's "three small JSON reads" (Decision 1, through `pending.Current`). It still does no involvement, chronicle, calendar or embedding work.
- **`unpriced`'s fix adopts `/config?section=pricing`** (§18.1's "may"; plan-gate resolution 13).
- **The Ledger's facts address stays the bare `/ledger`**, and the continuity group slugs are the plan's (Decision 22), since §12.1 names neither.
- **`reconcile` is not in `locks.DOMAIN_MODULES`** although §7.0 says every module that writes continuity_candidates.json goes there: it writes only through `candidates.write`, and `test_lock_domain_guard` rejects a listed module with no direct write. Its persists still hold `campaign_lock` (Decision 7; `test_persists_hold_the_campaign_lock`).
- **§11.3's same-type vocabulary and §12.3's "A continues B" are read per type against §5.3**: `continuation` and `subthread` are offered and accepted for thread pairs only; a commitment pair's vocabulary is `duplicate, related, distinct, uncertain`, and the parser downgrades anything else to `uncertain` (plan-gate resolution 8; Global Constraints).
- **Apply and dismiss answer 404 `not_found`** ("no longer pending"), not 409 `stale_candidate`, for a finding that is suppressed, satisfied, gone (deleted or merged away) or settled: those are hidden from the read, and the reader's view should say the finding is gone rather than offer to resubmit. A stale finding resubmitted against its current fingerprint is applied (Decisions 2, 16; §22 step 5, §12.9).
- **§6's basis gains `scored` (`{ref: generation}`) and `text_hashes`** (`{ref: sha256 of the identity text}`, unsalted), and `identity_hashes` is salted with the embedding space, so a capped sweep rotates, a space change reaches every ref, and a record that moved during a space change is still a touched re-check (Decisions 8, 10; `text_hashes` from the slice ledger, Task 3).
- **While continuity.json's file, aliases, links or suppressions are malformed, every cached finding is `unknown`**: hidden, kept, and never written over, and discovery adds nothing; a sweep that reaches a persist then fails with 409 `malformed` rather than landing (Decision 2; slice ledger, Task 8).
- **§7.0's "May import" column is amended** to the edges the code has: a new `pending` row, and the `reconcile` and `review` rows replaced (Decision 1). `candidates` stays a leaf (it validates refs with a private `_split` and imports no `canon`). `reconcile` imports no `doc`; it reads continuity.json through `pending`.
- **The writer guard's `FORBIDDEN` also covers the public mutators of `events` and `scene_ideas`**, and `plot`/`commitments`' `forget_scene` and `repoint_scenes`, beyond §11.6's list, because §11.5 names events.json, scene ideas, plot.json and commitments.json among what reconciliation never changes (Task 11).
- **Model-only nominations are filtered by verdict before adjudication**, and a cached temporal finding is retracted once the temporal source was read and no longer nominates it (Decisions 8, 11). §11.1 step 3 and §11.3 are kept; this is recorded because §6.2 and §11.1 say nothing about retracting a pair kind.
- **Dismiss writes the suppression before dropping the cache record**, and a dismiss that fails after the suppression landed answers 500 `partial_dismiss` and bumps the revision itself; an apply stopped the same way answers 500 `partial_apply` naming what landed (Decisions 16, 17). This orders §22's "suppression/cache cleanup" step internally.

### Slice E — scene suggestion control

- snapshot changes;
- driver-aware schema and date derivation;
- the request body and structured focus/avoid/must/time controls;
- card provenance;
- saved idea driver metadata and staleness;
- scene-suggestion eval case.

### Slice F — Story Graph

- graph projection endpoint;
- Story/Cast/Calendar/Continuity lenses;
- node detail and navigation, including the chooser handoff.

### Slice G — polish/evals/docs

- observability;
- performance audit;
- docs (CLAUDE.md inventory, CONTRIBUTING guard table, templates/README, store-guarantees if it lists campaign files);
- make check;
- final adversarial implementation-vs-spec review.

Do not start Slice F first because it is visually attractive. The graph must expose the real canonical continuity substrate, not become a parallel model.

---

# 32. Acceptance criteria

The capstone is complete only when all of these are true:

1. A new extracted thread/commitment is compared against plausible same-type existing records before it is treated as genuinely new.

2. Matching works without embeddings and improves with configured embeddings.

3. Todo tells the user when semantic matching is not configured, and states explicitly that basic matching still works.

4. A separate reconciliation system can propose duplicate, continuation/relation, thread closure and commitment resolution findings without silently applying them.

5. Reviewed duplicate handling is non-destructive and reversible.

6. Canonical current-state prompts do not show reviewed aliases as independent live threads/commitments, and campaigns with no aliases see byte-identical prompts for those sections.

7. Commitments are first-class inputs to scene suggestions.

8. Suggestions see a bounded list of upcoming events, holidays, birthdays and deadlines, rather than only the single nearest item.

9. Date arithmetic is provider-driven and deterministic.

10. The reader can focus on, avoid and require specific story drivers, and can choose meaningful time anchors.

11. Suggestion cards show validated reasons/drivers, and make constraint misses visible.

12. Saved scene ideas keep driver/time provenance, and can become visibly stale without being destroyed.

13. Todo surfaces pending continuity review without model calls.

14. The Story Graph shows the campaign's played history and future-facing obligations, from the same canonical data that suggestions use.

15. All new semantic writes (aliases and links) are reviewable and journalled/undoable.

16. No read-only navigation path launches an embedding or LLM request. (The chooser's pre-existing ranked call on mode pick is not a navigation path; §3.10.)

17. Existing campaigns require no migration to open and play.

18. The Settings page discloses automatic continuity embedding before it first happens (§9.5).

19. make check passes, relevant frontend tests/typecheck pass, template verification passes, and the final implementation receives both required Codex reviews (or recorded substitutes).

---

# 33. Explicit stopping rule

After this specification is implemented and accepted, **do not continue expanding continuity because another attractive narrative-analysis idea appears**.

Park the following, unless a correctness defect is found:

- act/era detection;
- theme clustering;
- automatic literary summaries;
- more graph node families;
- richer relationship inference;
- narrative centrality scoring;
- automatic chapter organization;
- mechanics-aware drivers beyond the extension seams already provided.

The purpose of this capstone is to make long-term persistent history trustworthy and controllable enough that Grimoire can move its main development focus to game mechanics.

---

# Appendix A. Adversarial review resolution log (2026-10-05)

The spec → planning gate was run by four independent reviewers. Each read one slice of the spec against the code under `backend/src/grimoire` and `frontend/src`. Every finding below is resolved in the section named. None was rejected outright. Where two reviewers proposed different resolutions, the choice is noted.

**Store substrate (§3, 5–8, 12.4, 21–22, 26–27)**

| # | Sev. | Finding | Resolved in |
|---|---|---|---|
| S1 | blocker | §7.1's merge rules changed every record's projection (`last_scene` stamped without beats; append-order `latest_beat`) | §7.1 identity law |
| S2 | major | merging open into closed canonical = closure without evidence | §3.7, §5.1 liveness mismatch |
| S3 | major | writes to a hidden alias source silently ignored | §7.2 |
| S4 | major | `set_movement` overwrites `last_scene`; plot mutators take no lock | §12.4 |
| S5 | major | link id derivation ambiguous and unstable under aliasing | §5.4 |
| S6 | major | link direction/endpoint kinds undefined; idea links duplicate §17 | §5.3 |
| S7 | major | candidate id, fingerprint and suppression key undefined | §5.5, §6 |
| S8 | major | revision token cannot be a staleness basis | §6 (field removed) |
| S9 | major | 409 recovery would need a paid refresh | §12.9, §22 |
| S10 | major | scene renames not fanned out | §5.6 |
| S11 | major | reused slug ids re-bind aliases/links | §5.7 |
| S12 | major | undo restore could write a cycle / 500 | §12.8 |
| S13 | major | lock domain and persist ordering unspecified | §7.0, §11.1, §22 |
| S14 | major | reconcile has no run class | §11.1 (see A1) |
| S15 | major | Ledger effective vs physical rows | §12.5 |
| S16 | major | package layout fails import guard | §7.0 |
| S17–S28 | minor | journal policy, transactionality, reader citation, alias API, route shapes, involvement as generalisation, ref spellings, dangling handling, revision bumps, writer guard, fixture/baseline work, fork residual | §12.8, §22, §5, §5.1, §21, §8, §4, §26, §6, §11.6, §27, §5.8 |

**Similarity, absorb identity and reconciliation (§9–11, 23–25, 29)**

| # | Sev. | Finding | Resolved in |
|---|---|---|---|
| A1 | blocker | reconcile run class / trigger unspecified; drafts are not durable | §11.1. The store reviewer proposed a scene-subject background run plus a draft for refresh; this spec takes the single campaign-subject background run so that both triggers share one helper and one-live-per-campaign rule. |
| A2 | blocker | mapping to `existing` could reopen or regress a record | §10.2 accepting `existing` |
| A3 | major | “redirect manually” needs UI that does not exist | §10.3 alternatives |
| A4 | major | “unselected by default” still saves | §10.2 uncertain rows |
| A5 | major | identity call placement/budget/visibility | §10.4 |
| A6 | major | plot slug collisions merge before the resolver | §10.5 |
| A7 | major | persist races | §11.1 steps, §22 |
| A8 | major | re-adjudicating the same candidates every End Scene | §11.1 step 3, incremental sweep |
| A9 | major | vocabulary gaps (cross-type, direction, temporal) | §11.3 |
| A10 | major | embedding mechanics (sync client, all-or-nothing, warm limit, width) | §9.4 |
| A11–A20 | minor/major | availability test, task names and routing, template/eval wiring, identity fields, undecodable replies, known scenes, pair complexity, observability, scene renames, data-dir refusal | §9.3, §29, §23, §10.1, §24, §11.4, §25.2, §29, §5.5–5.6, §11.1 |

**Temporal pressure, suggestions, scene ideas, Todo (§13–18)**

| # | Sev. | Finding | Resolved in |
|---|---|---|---|
| T1 | blocker | automatic embedding would start sending text users had turned “off” via depth 0 | §9.5, AC18 |
| T2 | major | idea ref validation erases staleness evidence; import cycle | §17 |
| T3 | major | holiday/birthday refs have no identity | §4, §13.6 |
| T4 | major | pressure contract undefined | §13.4 |
| T5 | major | link deadline urgency undefined | §13.5 |
| T6 | major | event ids reused after delete | §5.7, §17 dangling anchors |
| T7 | major | snapshot list dropped template-required fields | §15 |
| T8 | major | `time_mode` had no semantics | §16.3. The UI reviewer proposed `near` as the default; this spec keeps `auto` as the default so that no control means today's behaviour. |
| T9 | major | anchor validation incomplete | §15.3, §4 (deadlines not anchors) |
| T10 | major | no field for constraint misses | §15.2 `unmet_must` / `avoided`, §16.1 |
| T11 | major | request transport and validation order | §16.3 |
| T12 | major | no driver read route | §14, §21 |
| T13 | major | batch objectives judged over hidden cards | §15.1 card order, §16.4 |
| T14 | major | candidate cache vs Todo's no-cache contract | §18 preamble, §18.2 live filter |
| T15 | major | pressure in Todo breaks cost rule | §18.3 |
| T16–T24 | minor | setup chore mechanics, chore mapping, `upcoming` blast radius, intent template, harness updates, task name, `expire_candidate`, prompt cap, idea links | §18.1, §6.2/§18.2, §15, §15, §23, §29, §15.2, §15, §5.3 |

**Frontend and Story Graph (§12 UI, 16, 17 UI, 19, 20, 28.8–28.9)**

| # | Sev. | Finding | Resolved in |
|---|---|---|---|
| F1 | blocker | nothing supplies the chooser's drivers/anchors | §14, §16.2, §21 |
| F2 | blocker | graph nodes had no sortable date | §19.2 dates, §20 |
| F3 | major | Now boundary mixed axes | §19.4 |
| F4 | major | “Focus next scene” had no handoff | §16.5 |
| F5 | major | AC16 contradicted the chooser's existing call | §3.10, AC16 |
| F6 | major | controls UX/transport unspecified | §16.2, §16.3 |
| F7 | major | provenance not renderable from payload | §15.2, §16.4 |
| F8 | major | staleness vs read-time validation | §17 |
| F9 | major | provenance lost on save | §17 re-saving, SceneIdeaCreate |
| F10 | minor | Stale subgroup placement | §17.1 |
| F11 | major | Ledger placement ambiguous; no detail view | §12.1 |
| F12 | major | Ledger has no deep links | §12.1 addressing |
| F13 | major | kind→group mapping and actions missing | §6.2, §12.3, §12.6 |
| F14 | major | dismissed findings not restorable | §12.7, §21 |
| F15 | major | detail needs current state | §12.2 |
| F16 | major | refresh run and 409 behaviour | §12.9, §11.1 |
| F17 | major | Ledger edits vs aliases | §12.5, §5.7 |
| F18 | major | graph page placement | §19.1 |
| F19 | major | lens presets vs truncation | §19.5, §19.7 |
| F20 | major | edges insufficient for Cast lens | §19.3 |
| F21 | major | loose types | §20 |
| F22–F29 | minor | route order, chooser test wording, jsdom-testable graph tests, scene order trap, shell badge, Timeline name clash, node actions, keyboard | §21, §28.9, §28.9, §19.4/§28.8, §7.3, §19.5, §19.6, §19.5 |
