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
- **Replacing:** if the source is already aliased → 409 `{kind:'alias_exists', to}`, unless the request carries `replace: true`. A replace is one journalled row whose restore is the previous record. A review's apply carries no `replace`, but it replaces a source's stored alias that does not resolve (dangling, wrong-type or in a cycle): §26 leaves such a source its own effective record, so the review offers it as one, and refusing it as “already merged elsewhere” would contradict the page. A live alias is still refused. The check is made under the lock the write holds (Slice G final review; see §31). Pinned by `test_continuity_review_routes.py::test_applying_a_duplicate_replaces_the_sources_broken_merge` and `test_continuity_apply.py::test_replace_broken_still_refuses_a_live_merge`.
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

**One wording per relation** (Slice G plan, Decision 12; this closes Slice F's h8). The Ledger's Reviewed links / merges and the Story Graph lead a link with one phrase, `RELATION_PHRASES` in `components/continuity/labels.ts`: “Due by” for `by`, the form the Meaning column gives, and the graph's `RELATION_PHRASE[r].out` is taken from that table rather than redeclared. The journal uses the sentence forms of the Meaning column (“is due by”, §12.8), because a journal label is read as one sentence while both UI tables lead a line under a record's title. The graph's inbound phrases (“Deadline for”, “Continued by” and the rest) have no Ledger counterpart and stay the graph's.

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
- **A cascade that fails after the delete has landed** answers a structured 500 (Slice G plan, Decision 10). The 409 above refuses what a strict check can see, but an `OSError`, or a `ContinuityError` from a file garbled under the hold, can still escape `forget_ref` once the record is gone. Before Slice G that reached the reader as a bare 500, which read as a failed delete; the activity middleware's bump-on-raise already moved the write token. Now `routes/ledger.forget_or_partial` catches either error, bumps the revision itself (an `HTTPException` reaches neither the middleware's stamp, which is for responses below 300, nor its bump-on-raise), and answers 500 `{kind: "partial_delete", landed: [<noun>], detail}`, saying what landed, by noun. A thread or commitment delete is journalled, so its sentence says the delete can be undone; an event delete writes no journal row, so its sentence points at Reviewed links / merges, which lists each link left behind as broken. The Ledger and the events panel re-read on it, since the row is gone. And it records one ERROR row through `store.errors` (module `ledger`, the campaign, the ref and the exception's class, with its frames; never the record's title), since an `HTTPException` reaches neither the terminal nor the error store the way the old unhandled raise did (`test_ledger_routes.py::test_a_delete_whose_cascade_fails_is_logged_by_id`). Pinned by `test_ledger_routes.py::test_a_delete_whose_cascade_fails_moves_the_token` and `test_events_routes.py::test_an_event_delete_whose_cascade_fails_moves_the_token`.
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

If the candidate file is malformed, ignore it. Reconciliation can rebuild it. A single record that does not fit its kind -- refs of the wrong type or count, or a pair naming one record twice -- is ignored alone, its neighbours kept: it is listed nowhere, counted by no chore, and applying or dismissing it is refused as no longer pending.

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
| `graph` | §19. Read-only: in no lock-domain list, scanned by the §11.6 writer guard. It takes `best_effort_campaign_lock` itself, because only it knows which reads go inside the hold (the campaign files) and which run plugin code outside it (`calendars.primary_provider`, `pressure.build`, `drivers.snapshot`, `events.list_events`, `calendars.fixed_of`, `calendars.friendly`) (Slice F plan, Decision 1) | `calendars`, `chronicle`, `events`, `fieldtext`, `locks`, `overlay`, `relationships`, `scene_ideas`, `campaigns.paths`, `scenes.read`, `candidates`, `canon`, `doc`, `drivers`, `effective`, `involvement`, `pending`, `pressure`. Never `suggest`, `briefing`, `context`, `timeline`, `chores`, `scene_refs`, `undo`, `review` or `reconcile`; imported only by `routes/continuity.py` and the frozen sweep (Slice F plan, Decision 1) |

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
- **A zero vector is never cached** (Slice G plan, Decision 5). `vectors.save` keeps no vector without a direction, so a ref whose text the provider answers with zeros stays vectorless, and so stays required: its text is re-sent once per sweep, within `RECONCILE_WARM_LIMIT`. That is accepted. It holds back no other text (the required/warm split and the subset retry, §31 Slice D), and the ref's prior pairs are kept under the vectorless rule. Remembering “unembeddable” texts in the cache basis was rejected: it adds a key for one provider quirk, and a provider that later answered properly would never be asked again. Pinned by `test_continuity_reconcile.py::test_a_zero_vector_text_costs_one_resend_per_sweep`.
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
- `due` is kept only if the original row carried a non-blank one. A blank `due` means “lift the deadline” only on a row that named the record; on a row that would have opened one there was no deadline to lift, so it is dropped rather than carried onto the stored record, whose deadline it would clear. The as-existing alternatives of §10.3 follow the same rule (Slice G final review; see §31).

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

   Candidates over the cap persist without a proposal. At most one LLM call per pass (below), metered as `store.usage.meter("continuity-reconcile", campaign=cid)` in `routes/`.
4. **Second persist (proposals).** Same hold rules as step 2; it merges proposals into the cache. An LLM failure therefore keeps the deterministic candidates (§26).

**Passes, not triggers** (Slice D plan, Decision 6; Slice G plan, Decision 4). An End Scene that finds a reconcile already live adopts it: it leaves the refs its scene touched on the campaign's pending set and starts nothing. A run makes at most one model call per pass, and has at most two passes: its own, and, once that pass has landed, one incremental follow-on that carries every ref adopters pended before the run took the set. So End Scene and Refresh each cost at most one call, a run at most two, and a burst of adopters exactly one more. Refs pended after that last take wait for the next fresh run: an incremental run takes them before its own pass, and a full Refresh leaves them for its follow-on. That is this section's own rule, that a skipped automatic run is caught by the next one. A follow-on per adopter, or looping until the set is empty, was rejected: either makes the calls in one run grow with the number of saves, which is the N+1 §25.1 forbids. Pinned by `test_continuity_reconcile_routes.py::test_a_burst_of_adopters_coalesces_into_one_follow_on` and `::test_a_full_refresh_keeps_refs_left_pending_for_its_follow_on`.

**Storage relocation.** `PUT /config/data-dir` is refused while any run is live, background runs included. That is accepted: the run is bounded by the warm limits, the candidate cap and one LLM call per pass, so the window is short.

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

Every entry speaks in words, never in store tokens (Slice G plan, Decision 12). A broken entry's reason is a sentence from `BROKEN_REASONS` in `labels.ts` (“The merged record no longer exists.”, “One of its records no longer exists.”, “It is part of a loop of merges.” and the rest), and a code the table does not hold reads “This entry can no longer be followed.”, never the code. A record that no longer exists is named by its kind (“a missing thread”, “a missing commitment”, “a missing event”, or “a missing record”), never by its ref, because `review.describe` answers a missing record with its ref. A ref-titled record whose ledger cannot be read right now reads “a thread that cannot be read right now” (or commitment, or event) instead, since `GET /continuity` reports that ledger as `unreadable` and the record may well exist. `GET /continuity`'s raw links carry `a_title` and `b_title`, as effective links already do.

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

A link's label is one sentence, `<a> <relation words> <b>`, never the stored relation token (Slice G plan, Decision 12): “Mara's map pays off Mara's oath”, with the “ — removed” and “ — removed with deleted record” suffixes as before. `review.RELATION_WORDS` gives `continues` → “continues”, `subthread_of` → “is a subthread of”, `pays_off` → “pays off”, `before` / `on` / `after` / `by` → “is due before” / “is due on” / “is due after” / “is due by”, and `related_to` → “is related to”, the sentence forms of §5.3's Meaning column; a relation the table does not hold reads “is linked to”. The two link refusals carry no token either: “That kind of link cannot join these two records.” (`create_link`) and “That kind of link cannot join these records in that direction.” Pinned by `test_continuity_review.py::test_link_journal_labels_use_relation_words`, `test_continuity_routes.py::test_link_refusals_carry_no_store_token` and `test_continuity_wording.py::test_relation_words_cover_every_relation`. A record a label or a refusal names but cannot title — gone, untitled, or in a ledger that will not read — is named by its kind (“a missing event”, “an untitled thread”, “a thread that cannot be read right now”; `review.reader_name`), never by its ref, so Remove link and Unmerge on a broken entry leave no ref in the History rail (pinned by `test_continuity_review.py::test_labels_and_refusals_about_missing_records_carry_no_ref`). A refusal because a ledger will not read names the records by kind, never by the file that holds them: “threads cannot be read right now” (or “commitments”, “events”, or “threads and events” for a link whose ends are both unreadable), never “plot” (pinned by `test_continuity_review.py::test_an_unreadable_ledger_refusal_names_the_kind_not_the_file`).

Continuity alias and link writes are journalled through `undo.journalled` under the same **best-effort** policy as ledger hand edits: the write is authoritative, and a failed journal append is logged, not raised.

Do not journal derived candidate-file updates or suppressions.

## 12.9 Refresh and stale handling

- **Refresh** starts the §11.1 run. The section shows the run in progress and re-reads candidates when it lands. Refresh is enabled even with no LLM or embeddings connection, and the matching-mode line says what it will do.
- **A `409 stale_candidate`** on apply re-reads `GET /continuity/candidates` only. It never starts a run. The 409 body carries the current records (§22), so the detail can re-render immediately. The reader may resubmit against the new fingerprint after looking.
- **A finding whose meaning moved opens its action form fresh.** The form's input (the open action, a closing beat, its evidence scene, a temporal relation, a due copy) was chosen against one reading of the finding, so when the same candidate id comes back with a new fingerprint or a changed proposal (decision, relation or evidence scenes) — after a Refresh, or a 409 laying the current records over it — the form closes and reopens on the new defaults. A re-read that changes none of those keeps a half-filled form.

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

**Residual: Hebrew month-only birthdays across the leap cycle** (Slice G rulings, b4). A month-only key matches the year's own month keys literally, a rule inherited from `_when`: `--Adar1` gives no occurrence in a common year, and `--Adar` none in a leap year, so such a birthday drops out of pressure, the drivers and the prompts' Birthdays line in those years. Day-bearing keys are folded by the provider and are not affected. The fix is in the month-key match, which the intent prompt's Birthdays line also renders, and §15 holds that line byte-identical, so it waits for a later correctness fix (§33).

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
- **Offscreen:** with `offscreen=true`, driver `actors` drop PC tokens, exactly as the suggestion snapshot does. A PC's birthday driver is **kept**, with `actors: []`: an offscreen scene of NPCs planning around the player's birthday is legitimate, and the Birthdays line already names the PC offscreen. The suggestion parser's `token_ok` still drops every PC cast token, so the ref renders while the cast rule holds (Slice E plan, Decision 15).

`drivers.snapshot(cid, offscreen=False, *, pressure_result=None)` returns:

    {now, friendly, fixed, matching, drivers: [Driver], anchors: [AnchorOption]}

    AnchorOption = {ref, kind: "event" | "birthday" | "holiday", label,
                    native, friendly, fixed | null, in_days | null,
                    precision: "exact" | "yearless" | "month"}

`anchors` is the bounded list of upcoming temporal drivers, ordered by `in_days`, nulls last. The suggestion snapshot (§15), the drivers read route (§21), and the suggestion request validation (§16.3) all use this **one** function.

`pressure_result` is keyword-only. A caller that has already run `pressure.build` passes its result, and the function uses it rather than computing pressure again. The suggestion snapshot does this, so its `timeline` and its `driver_index` come from one pressure computation at one `now`. Without the keyword the behaviour is unchanged (Slice E plan, Decision 3).

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
- `cast`, unchanged except for any needed actor-ref normalization. In an offscreen snapshot a PC's birthday driver still renders in the index and the timeline (§14), while `token_ok` still drops every PC cast token, so no PC is ever cast offscreen (Slice E plan, Decision 15);
- canonical active threads, **with ids rendered** and with dormancy;
- canonical unresolved commitments, with id, kind, due, latest beat, and aging/pressure;
- `timeline`: every pressure item (§13). The snapshot holds them all, so the parser, the grader and the tests see them all. What the prompt **renders** is bounded with the index's rule (AC8; Slice E plan, Decision 11): items whose `ref` or `subject` is a focus, must or anchor ref, and the item `events.sooner` would pick, always render; the rest are ranked by `SORT_ORDER`, then `in_days`, and capped at `DRIVER_PROMPT_CAP` less the always-rendered count; the kept rows render in the snapshot's own order, followed by “and N more dated items” when any were cut. A timeline under the cap renders whole;
- `driver_index`: the drivers from `drivers.snapshot`, each carrying `dormancy` (`None` for temporal kinds). The rendered index is capped at `DRIVER_PROMPT_CAP` (40), ordered by pressure state in `SORT_ORDER` (the §16.2 display order, not the decision precedence `PRESSURE_STATES`, which ranks `passed` second and would let passed events push due-soon drivers out of the cap) and then dormancy, coldest first. Focus, must and anchor refs are always included; the rest is summarised as “and N more” (Slice E plan, Decision 9);
- `links`: reviewed links among active drivers.

The snapshot also carries additive keys (Slice E plan, Decision 9):

- `anchors`: `drivers.snapshot`'s anchor options, the set a time anchor is validated against (§15.2, §16.3);
- `fixed`: the pressure result's fixed day for `now` (`None` without a calendar);
- `near_days`: `max(warn_days, 7)`, the `near` window (§16.3);
- `sooner_ref`: the ref of the item `events.sooner` picks (`""` when it picks nothing), so the prompt's bounded timeline can pin it (Decision 11).

Thread rows gain `ref`, and commitment rows carry `ref` and `dormancy` as well as their pressure.

**Removing `upcoming`.** Only the snapshot's `upcoming` key is removed, once the suggestion templates have moved to `timeline`. Leave these unchanged:

- `today_facts`, `day_facts` and `events.sooner`;
- the per-turn Today block in context assembly;
- the other `today_facts['upcoming']` readers.

`events.sooner`'s docstring forbids the prompt and the snapshot from disagreeing, so the timeline must contain the item that `sooner` would pick.

**Scene intent.** `scene_intent` shares the snapshot and includes `scene_suggestions/user.j2` verbatim. The new sections render behind a `drivers` flag, which `build_intent_prompt` passes as false, so the intent prompt is unchanged. The flag is also the snapshot's shape: `build_snapshot(cid, offscreen, drivers=False)` returns today's key set exactly, **`upcoming` included**, and does no pressure or driver work. Only the suggestion snapshot (`drivers=True`) drops `upcoming` (Slice E plan, Decision 2), because deriving the intent prompt's Upcoming line from the timeline would change both its tie order and its holiday spelling.

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

The schema (§15.2), the action vocabulary and the diversity guidance above go in a **drivers addendum**, and focus, avoid, must and time go in a **controls addendum**. Both are appended **after** the byte-identical instruction section, so it stays a prefix of the system message. The drivers addendum is present whenever the rendered driver index is non-empty, and the controls addendum only when a control is active. So the system message as a whole gains the drivers addendum whenever the campaign has drivers, and a campaign with none gets today's system message exactly (Slice E plan, Decision 1).

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
- `time_anchor` must name one of the captured snapshot's `anchors` (§14, §15): membership in `anchors`, not merely a temporal driver. Passed and undated events are drivers but not anchors, and no date can be derived against them, so a model `time_anchor` naming one is dropped, with no auto-added driver (Slice E plan, Decision 5). If a valid `time_anchor` is present, its ref is added to `drivers` with action `anchor`, if missing.
- The parser **never drops a suggestion** for a constraint miss. Each suggestion carries `unmet_must: [ref]` (must refs it does not claim) and `avoided: [ref]` (avoid refs it claims).

What the route returns per suggestion, resolved server-side from the captured driver index:

    drivers:      [{ref, kind, action, label}]          (avoided refs excluded)
    time_anchor:  {ref, kind, relation, label, friendly, in_days} | null
    date, date_friendly, in_days, date_rejected
    date_rejected_by: "anchor" | "time" | null   (which rule blanked the date)
    unmet_must:   [{ref, label}]
    avoided:      [{ref, label}]

## 15.3 Date derivation

All comparisons use primary-provider fixed days. An anchor's time component is stripped with `split_native`. D is the anchor's fixed day, and d is the suggestion's date.

| Relation | Rule |
|---|---|
| `on` | the date is **derived** from the anchor; the model's date is ignored |
| `on`, month-only birthday | the model's date is kept only if its month key matches and now ≤ d, otherwise blanked (the anchor month can be the present one) |
| `before` | now ≤ d < D |
| `by` | now ≤ d ≤ D |
| `after` | D < d ≤ D + `RESOLVE_WINDOW_DAYS` |

**A month-only birthday anchor takes only `on`** (Slice E plan, Decision 6). Its `fixed` is null, so `before`, `by` and `after` have no D to compare against. A request pairing it with another relation is a 400 `anchor_relation` (§16.3); a request that sends it with no relation has that relation resolved to `on` before the prompt is built, so the controls addendum names only `on` rather than offering the model a choice; and a model relation for it is coerced to `on`. Its month comes from its ref (`birthday:<kind>:<id>:month:<year>-<key>`), since the pressure items carry no month key. An `on` anchor's derived date is `provider.format(D)`, the canonical spelling, not the stored text (Decision 12).

**With no `now`** (no clock and no chronicle date), the `now ≤` lower bounds and the `near`/`move` checks are skipped, while the D bound still applies. No date is fabricated. An absent or unparseable model date is `""` and is never `date_rejected`: rejection means a date was given and fails the rule (Decision 12).

**Batch anchor.** With an anchor in the request, every suggestion's `time_anchor` is forced to that ref. Its relation is the request's relation if given; otherwise the model's relation, if valid; otherwise `on`.

**A failing date is blanked**, not the suggestion: `date: ""`, `date_rejected: true`, `date_rejected_by: "anchor"`. The card shows “date not consistent with anchor”.

**Unanchored suggestions** keep today's date behaviour (`date_addendum.j2`). `time_mode` (§16.3) may constrain them further. A date blanked by `near` or `move` is marked `date_rejected` too, with `date_rejected_by: "time"`, and its card reads “date not consistent with the time setting” (Decision 12). That holds on an anchored card as well: a date the anchor allowed or derived (an `on` anchor 31 days out) can still be refused by `near`, and the card names the time setting, not the anchor (Slice E plan, deviation 22). `date_rejected_by` is `null` when nothing blanked the date. The reply's `next_date` is checked under `near`/`move`, and is not checked under an anchor: it answers “if none is used”, and an anchor constrains suggestions, not that.

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
- **Stay near current date** and **Let time move** are disabled, with a visible hint, when the drivers read's `now` is empty: no clock and no chronicle date. That is the same `now` the suggestion snapshot resolves (both go through `clock.now`, which falls back to the newest chronicle date), and without it `near`/`move` ask for nothing (§15.3). **Choose anchor…** is disabled when there are no anchors. A `near`/`move` held from a read that had a date falls back to Any date when a re-read has none, and is named in the reset note; the request never sends either without a date (Slice E plan, deviation 21).
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

1. Canonicalize every ref through `effective.live_canon`, not `canonical_refs` (Slice E plan, Decision 4). The driver index is built from effective rows, which follow an alias only to an existing record; `canonical_refs` follows a stored alias even to a deleted target, so a control naming a dangling alias's source (its own live driver) would canonicalize outside the index and draw a spurious 409. If `live_canon` raises, canonicalization is the identity.
2. `must ∩ avoid` → 400.
3. More than 3 must refs, or a temporal must ref → 400.
4. `time_mode: "anchor"` with no anchor, a `time_anchor_ref` with any other mode, a `time_anchor_ref` whose prefix is not `event`, `birthday` or `holiday`, or a month-only birthday anchor with a relation other than `on` → 400.
5. A ref outside the request-time driver index → 409 `{kind: "stale_drivers", refs}`. The chooser re-reads drivers and shows which selections dropped, until the next suggestion request goes out; a later refusal names only what it reset (Slice E plan, Decision 19, deviation 20). A temporal `time_anchor_ref` missing from the captured `anchors` is a 409 too, not a 400: the chooser only offers anchors, so a missing one means the campaign moved since the read (an event passed, or a holiday left the horizon). The structural anchor check is the 400 of step 4 (Decision 5).
6. Remaining overlaps resolve by precedence:

       must_include > avoid > focus > normal

   **The batch anchor beats avoid** (Decisions 8, 26). The anchor is a separate control, and the more specific instruction: every suggestion is forced onto it (§15.3). So an anchor ref in `avoid` is dropped from `avoid` silently rather than refused, and the chooser disables Avoid on the anchored row.

A 400 body is `{kind: "bad_controls", detail, reason}`, with `reason` one of `must_avoid`, `must_cap`, `must_kind`, `anchor_missing`, `anchor_kind`, `anchor_without_mode` or `anchor_relation`. `time_mode` and `time_anchor_relation` are `Literal`s, so an unknown value is FastAPI's 422. The existing 409 `missing_key` stays first: the order is 404, the connection check, the snapshot, every 400, then the 409, then `runs.run_draft` (Decision 7). A body, when present, wins wholly over the query parameters.

The `work` closure captures the snapshot and driver index, and passes them to the payload shaper. So parse-time validation and labels use exactly what the prompt showed.

The routing task stays `suggestions`.

## 16.4 Cards show why

A generated card renders validated provenance:

- `date_friendly · <relation> <anchor label>` when anchored;
- one chip per driver, labelled by action: Advances / May close / Addresses / May fulfil / May break / May expire / Anchored to. The time anchor's own `anchor` entry draws no chip, since the dated line says it; an `anchor` claim on any other ref (a passed or undated event the card is about) does. A save keeps one anchor per record (§17, Decision 16), so such a claim is shown on the card and not stored with the idea (Slice E plan, deviation 23);
- warning chips for `unmet_must` and `avoided`, worded “Doesn't claim to address <label>” and “Claims to address <label> (avoided)” (Slice E plan, deviation 15);
- “date not consistent with anchor” when `date_rejected_by` is `"anchor"`, and “date not consistent with the time setting” when it is `"time"`, i.e. a `near` or `move` check blanked the date, anchored or not (§15.3; Slice E plan, deviation 22). A reply from before the field falls back to whether the card is anchored.

Example using placeholders:

    Midnight at Saltmarch
    Tomorrow · before The coronation
    Advances: Mara's map
    Addresses: Mara's promise

These labels are derived from validated ids, not from model-written explanatory prose.

While any structured control is active, the picker shows every generated suggestion, not only the slots left over by greetings. “Active” follows the batch on screen: the picker asks whether the reply now shown was requested with an active control, so a control edit not yet sent never re-slices the cards the reader is looking at (Slice E plan, Decision 22).

## 16.5 Handoff from the Story Graph

“Focus next scene” navigates to `/campaigns/{cid}/scenes` with history state:

    {chooser: {drivers: {[ref]: "focus"}}}

For event, birthday and holiday nodes, “Anchor next scene” uses:

    {chooser: {anchor: {ref, relation: "on"}}}

The handoff then proceeds as follows:

- ScenesView adopts the state once, then replaces it with null, and opens NewSceneChooser seeded with it. The chooser still opens at mode selection. This adoption is **new** code in ScenesView: the `seedPrompt` adoption the pattern copies lives in CampaignView, and ScenesView only sends (Slice E plan, Decision 20). A malformed `chooser` state is ignored.
- A seeded open **waits for the scene list** before the chooser opens, because `afterSid` comes from it and an open before it lands would make the hook ask again, a second paid ranked call (Decision 20).
- A seeded chooser holds its auto-ask, and its Suggest button, until the drivers read settles, so the ranked call made when a mode is picked carries the seeded controls rather than racing them (Decision 19).
- A seeded ref missing from the drivers read is dropped, with a visible note. The note outlives the ranked call that carries the seed, and clears when the reader changes a control (deviation 20).
- A seeded Story Pressure disclosure opens expanded, so the reader sees what was set (Decision 19).

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
- drop refs whose record does not exist **in any status**. Existence, not activity, is the write test. An unreadable ledger keeps the ref, since the read reclassifies it. Holiday and birthday **occurrence** refs are computed rather than stored records, so they have no record whose existence could be tested: they are written when well-formed (`holiday:<int>:<name>`, `birthday:<kind>:<id>:<int>` or `birthday:<kind>:<id>:month:<year>-<key>`) (Slice E plan, Decision 16);
- de-duplicate refs by ref; the first entry wins;
- `time_anchor` passes the same existence test as a driver ref of its kind, so a passed anchor is stored and can later read “has passed”. Its relation is **coerced** on write: one outside before/on/after/by becomes `on`, and a month ref is always `on` (§15.3). A dropped anchor takes its `anchor` driver entry with it, and an `anchor` entry naming any other ref is dropped, so a record never claims two anchors (Decision 16);
- store `time_anchor.native`: an event's date as currently stored, `provider.format` of a day occurrence's fixed day, `""` for a month ref. With no calendar, a day occurrence anchor is dropped.

A plain save, carrying neither field, writes exactly today's record: `drivers` and `time_anchor` are written only when non-empty (Decision 18).

On read:

- canonicalize;
- classify each **stored** ref as:
  - `live`;
  - `finished`: thread closed, commitment resolved, or occurrence past or fired — except that an event on today's date is `live` whether or not it fired, by §13.5's own-day carve-out (reaching the day fires it, and an idea anchored on it is still for today, as a holiday or birthday occurrence on today is);
  - `dangling`: the record is gone, or an event anchor's stored `native` differs from the event's current date;
- return live and finished refs, each with a `state`, and drop dangling refs from the returned list;
- derive `stale_reason` from the stored refs **before** anything is dropped.

Read details (Slice E plan, Decision 17):

- **Occurrence refs are never `dangling`.** Holiday and birthday occurrences are computed, so there is no record to lose: they are `finished` when their day (or, for a month ref, their month in calendar order) is behind `now`, and `live` otherwise.
- **Unknown classifies as `live`.** Classification reads one tolerant ledger load and `live_canon`, never the raising `effective.records`. An unreadable ledger, an unreadable status, or a calendar or `now` that cannot be resolved classifies the ref as live, so unknown is never stale. Each idea's annotation runs inside its own guard, and one bad read falls back to the stored refs unannotated rather than emptying the saved list.
- **Canonical on read.** Each stored ref is mapped through `live_canon`, returned under its canonical ref with the canonical record's label and state, and de-duplicated, the first stored entry winning. The file keeps both spellings.
- **Labels are derived on read**, since §17 stores none: a thread's or commitment's canonical title; an event's current name; a holiday's name from its ref (a shortened name's digest shown as `…`); a birthday's actor's current name; else the ref's id.
- **`anchor_date`** is added to each read: the anchor's date when its relation is `on` and the anchor is live and dated, else `""`. It is the server-supplied anchor date of §17.1.
- A **deleted** anchor is dropped from the read; the idea is stale only when no other stored ref is live (an anchor-only idea then reads “Its drivers no longer exist”) (Decision 17).

Reads never rewrite scene_ideas.json.

**Re-saving.** When `_standing_match` finds an existing idea, the new save's driver refs are unioned in, by ref. The stored `time_anchor` is kept, unless it was absent and the new save carries one. Provenance is never thrown away.

**SceneIdeaCreate** gains `drivers` and `time_anchor` as optional plain fields. Saving a generated card sends its validated `drivers` (`[{ref, action}]`, excluding avoided refs) and `time_anchor` (`{ref, relation}`).

## 17.1 Derived staleness

Do not add a stored “stale” status.

`stale_reason: "" | "<human-readable reason>"` is non-empty iff the idea stored at least one driver ref or a time anchor, **and** one of the following holds:

- **No stored ref is live.** Reason: “Every thread it was about is closed”, “Every commitment it was about is resolved”, “Its drivers no longer exist”, or, for a mixed set, “Nothing it was about is still open” (a fourth text, Slice E plan, Decision 17). A finished `after` anchor counts as live here, because its passing is what the idea waits for.
- **The anchor is past.** Its relation is before/by/on and the anchor occurrence is past or fired (never on its own day, above). Reason: “<anchor> has passed”, where a birthday anchor reads “<actor>'s birthday has passed”.
- **The anchor moved.** It is dangling because it was rescheduled. Reason: “<event> moved to <friendly>”.

Holiday and birthday anchors are occurrence refs (§4). A past occurrence is past, even though the holiday recurs.

An active idea with a `stale_reason` stays recoverable:

- it is listed under a collapsed **Stale (n)** subgroup in the picker, excluded from the four-slot budget;
- it stays pickable and adaptable, with its reason shown as a hint;
- the hub's Play next card skips it.

Picking a saved idea anchored `on` an upcoming occurrence uses the server-supplied anchor date, rather than the chooser's `nextDate`.
Picking any other anchored saved idea (`before`, `by` or `after`, or `on` an occurrence that has passed) leaves the date empty for the reader. The chooser's `nextDate` is not anchor-aware and may sit on the wrong side of the event the idea names, and the idea's stored date is a fossil of whenever it was saved. Only an un-anchored idea borrows `nextDate` (Slice E plan, Decision 23; deviation 19).

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

“Three small JSON reads” takes the reading §19.7 gives “once” (Slice G plan, Decision 7): each of those files, and events.json (§31 Slice D), is read a constant number of times per request, independent of record and finding counts, and never more often as the ledger grows. Pinned by `test_continuity_read_cost.py::test_the_continuity_chores_read_a_constant_number_of_files`.

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
- **Arcs:** one row per canonical thread or commitment (a merged-away record is reached through its canonical); selecting one selects that node (Slice F plan, Decision 19; Task 9).

**Main.** Main holds the drawing in a horizontally scrolling pane, and the selected node's detail **below** the drawing, outside it.

**URL state.** `?lens=`, `?arc=` and `?node=` live in the URL. An unknown lens reads as Story, an `?arc=` that names no canonical thread or commitment as no filter, and a `?node=` that names no node as no selection. A lens or arc change pushes a history entry; a node pick replaces it. Choosing the lens already shown, or any write that leaves the query string as it was, navigates nowhere, so Back is never spent on an identical entry. The Show toggles are local state, and each lens restores its own defaults (Slice F plan, Decision 19).

**Phone column.** `PageShell` gains an optional `dismissKey`: when its value changes, the phone column closes, as it already does on a pathname change. An Arcs row changes only `?node=`, so the page passes `` `${node}#${pick}` ``, where `pick` counts every Arcs-row and node click. Re-tapping the selected row therefore still closes the sheet over the detail, while Lens and Show rows (filters) keep it up (Slice F plan, Decision 22).

**Campaign name.** The page reads it from `useCampaignShell(cid, { demand: false })`, which skips the arrival `retry()` and reads only what the shell context already holds. Every existing caller keeps the default, `demand: true`. This keeps the graph read the page's only API call (§28.9; Slice F plan, Decision 18).

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

**Which nodes exist** (Slice F plan, Decisions 6 and 11):

- **Ideas:** only stored ideas whose status is `active`. A used idea became a scene, which is already on the spine, and a dismissed idea is the reader's own “no”.
- **Actors:** only actors some edge names (appearances, involvement, relationships, birthdays). **Locations:** only locations named in some scene's location history.
- **Threads and commitments:** every effective record, closed and resolved included, plus every alias source as its own node (§19.3).
- **Events:** every stored event, fired, passed and undated included.
- **Holidays and birthdays:** exactly pressure's occurrence items (§13), so only occurrences inside its horizon (`calendars.UPCOMING_WINDOW_DAYS`) and not in the past. Their ids are byte-identical to the anchor refs. A node is never synthesized by parsing a ref back apart.
- **Groups and standing facts** are not built in this capstone; they are parked by §33 (more graph node families). See §31's Slice G rulings, (f3).

**Dates.** Every dated node carries `native`, `friendly`, `fixed: number | null` (the primary provider's fixed day) and `in_days: number | null` (relative to the campaign clock). The payload has a top-level `now: {native, friendly, fixed}`. The frontend positions dated nodes only by `fixed` / `in_days` and **never parses `native`**: native dates are provider strings, and sorting them is alphabetical by month name. A node whose `fixed` is null goes in an “Undated” bucket, never at a guessed position.

A record that carries its own date keeps `native`, `friendly` and `fixed` when the campaign has no present (no clock, and no chronicle date the provider can read): only `in_days` and pressure need a present, and a fixed day does not. A commitment takes its dated fields from its deadline item (§13.4) when pressure made one; otherwise a **live** commitment with a parseable `due` takes them from the due itself, labelled as a deadline item would be, with `in_days: null`. §13.5's no-`now` rule withholds the pressure item, not the day, so a campaign with no clock still places its deadlines on the Calendar lens. Where a present exists a live parseable due always has its item, so a dated campaign's payload is unchanged. A free-text due and a resolved commitment carry no deadline either way. A dated record's status line, and so its button's accessible name, says how far off it is (“in 3 days”) when there is a present and the day itself (its `friendly`, else its `native`) when there is none, so a scheduled event or a dated idea never reads “undated” merely because the campaign has no clock; only a record with no `fixed` does. The suggestion prompt and the chooser keep the same rule (Slice G final review; see §31): a timeline or Story drivers row, and an anchor option, whose item has a `fixed` but no `in_days` reads “no current date” rather than “undated”, since the prompt prints the day beside it and the event is an anchor option. Pinned by `test_suggest_store.py::test_a_dated_event_is_not_called_undated_when_the_campaign_has_no_present`.

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

Beats carry no status, so `opened_in`, `advanced_in` and `closed_in` are derived exactly as described. Concretely (Slice F plan, Decision 8), from each effective record's merged beats in play order:

- `opened_in` targets the **first** beat's scene when that scene is listed. A first beat in a deleted scene gives no `opened_in`, and the edge never moves on to the next beat;
- `advanced_in` / `touched_in` targets each distinct listed beat scene **other than** the opening scene;
- `closed_in` / `resolved_in` applies when `not effective.is_live(kind, status)` and targets the effective `last_scene` when it is listed. It may coexist with an `advanced_in` edge to the same scene;
- movement edges are unique per `(kind, from, to)`.

`serves` carries the stored driver action (`DriverAction`) as its `relation`, and `anchored_to` the stored anchor relation. Both are kept only when the target is a node (Slice F plan, Decision 12). `feeling` carries typed `trust`, `affection`, `tension` (each clamped to 0–5) and `note`; `bond` carries `bond_type` and `since_scene`, kept even when that scene is deleted (it is provenance). A token whose prefix is not `characters` or `pcs` gives no edge (Slice F plan, Decision 14).

Reviewed continuity links (`source: "reviewed"`, `relation` set) are edges of `kind: "link"`, with the stored link id as `id`, and are kept only when both ends are nodes (Slice F plan, Decision 13). The relations are:

- continues;
- subthread_of;
- pays_off;
- related_to;
- before/on/after/by.

Alias provenance (`source: "alias"`): `merged_into`, shown only when “Show merged” is enabled. The one exception is the arc filter, which shows the arc's own merged nodes and `merged_into` edges whatever the toggle says, because §19.6 asks it to show “its aliases” and that is the more specific rule (Slice F plan, Decision 20).

Candidates (`source: "candidate"`, `candidate_id` set) render as dashed suggestion edges when “Show review candidates” is enabled. A pair finding (`possible_duplicate`, `possible_relation`) becomes an edge whose `kind` is the candidate kind and whose `id` is the candidate id. Only verdicts in `pending.VISIBLE` are drawn. Every visible finding is also listed on the nodes it names (§20), so the edge carries no information of its own (Slice F plan, Decision 13).

Never render an embedding score as an asserted story relation.

## 19.4 Layout

The default **Story** lens is play-order oriented, not a force-directed hairball.

- **Spine:** every scene, absorbed or still in play, in **scene-id order** (the order `timeline.build` emits; never `list_scenes`, which sorts by `updated`). Play order is deliberately not date order, because flashbacks exist.
- **Now boundary:** drawn after the last scene and, when that column is shown, after “Reached” (below), which sits between them. Everything left of Now is played history (Slice F plan, Decision 21).
- **Right of Now:**
  - upcoming events, holidays, birthdays and parseable deadlines, by ascending `in_days`;
  - then active saved ideas, dated ones by `in_days` and undated ones in a trailing “Unscheduled” column.
- **Lanes:** thread and commitment nodes and arcs sit in lanes connected to the scenes that moved them.
- **Density:** actors and locations are secondary nodes, toggled to reduce density.
- **Fired events:** fired or passed events are not on the Story spine: the Story preset drops them. They appear in the Calendar lens, in node detail, and in the play axis's “Reached” column, left of Now, on the Continuity lens or under an arc filter whose link neighbours include one (below; Slice F plan, Decision 21). An event on today's date is the exception, by §13.5's own-day carve-out: reaching the day fires it, but its driver still reads `today` and the chooser still offers it as an anchor, so a fired event with `in_days == 0` is never “Reached”: it stays on Story, in its temporal slot right of Now.

The **Calendar** lens places scenes by their opening date's `fixed`, and puts Now at `now.fixed`.

**Columns** (Slice F plan, Decision 21; Task 10). Both axes are **ordinal**: one column per distinct slot, never a linear day scale, which would put a year's gap between two scenes a flashback apart. Coordinates come from a pure layout function, so tests can assert them.

- The **play axis** serves Story, Cast and Continuity. Left to right:
  - a leading **“Not in a scene”** column, for a node with no visible scene column (an arc in a view that shows no scene, an actor with no appearance);
  - the scenes in play order, one column each, headed by ordinal (“Scene 1”, …). An ordinal stays true under an arc filter that hides the scenes between;
  - **“Reached”**, for fired and passed events on the play axis, except one on its own day (`in_days == 0`), which takes its temporal slot right of Now (above): the Continuity lens, or an arc filter whose link neighbours include one (on any play lens, Story included). The Story preset drops them and Cast shows no events;
  - **Now**, which holds only its marker, so the marker never runs through a button;
  - the temporal slots right of Now by `in_days` (upcoming events, holidays, birthdays, and deadlines still ahead). In a campaign with no present, where nothing has an `in_days`, an unfired event with a `fixed` takes a slot by `fixed` instead, headed by its date, rather than “Undated”; a dated commitment keeps its scene lane, because “still ahead” needs a present;
  - **“Undated”**, for unfired events and birthdays whose `fixed` is null (an undated non-idea node);
  - the idea slots, one per distinct date (a dated idea in the past still takes one);
  - **“Unscheduled”**, kept for ideas with no date, as above.
- The **calendar axis** serves Calendar. Left to right: the days by `fixed`, with Now ordered among them (first on a tie), then “Undated”, then **“Not dated”** for record nodes that carry no date by nature (threads, commitments without a deadline, and merged nodes, which an arc filter or the Merged toggle can bring onto this lens), then **“People and places”** for actors and locations.
- Threads and commitments take lanes. A thread sits in the column of the latest scene that moved it; a commitment whose deadline is still ahead takes its temporal slot instead. A merged node is laned **directly below** its canonical, in its canonical's column, whenever that canonical is drawn.
- Node buttons are a fixed 44px tall (the touch target), with a one-line ellipsized label and status, so a long title can never grow into the next lane. Every geometry constant is justified structurally and is to be tuned against real campaigns later.

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

How the presets read the list above (Slice F plan, Decision 20):

- **Story** also shows saved ideas with their `serves` and `anchored_to` edges (§19.4 places them right of Now), and drops fired and passed events (§19.4).
- **Cast** lists no actors of its own: its actors and their edges come only from the Actors toggle, which Cast turns on by default, so unchecking Actors in Cast does what it says.
- **Calendar** shows a commitment **only when it has a deadline** (`native` non-empty), because the list says “deadlines”, not every commitment; a deadline the calendar cannot parse still goes under “Undated” (§19.2). It shows **no ideas**, so it has no `anchored_to` edges.
- **Continuity** shows threads, commitments and events, with links, `merged_into` and candidate edges; Merged and Review candidates are on by default.
- **Show toggles** work on top of every lens: Actors adds actors and the actor edges, Locations adds locations and `occurred_at`, Merged records adds merged nodes and `merged_into` edges (on every lens, Calendar included), and Review candidates adds candidate edges. An edge is visible only when both its ends are.
- **Arc filter:** with `?arc=` set, the visible nodes are the arc, its merged nodes, its reviewed-link neighbours, the scenes its movement edges reach and the actors its `involves` edges reach. That set replaces the lens's node kinds and toggles; the arc's aliases show **whatever the Merged toggle says** (recorded against §19.3), the lens still chooses the layout, and candidate edges still need their toggle.
- **Pending lifecycle findings** (`possible_thread_closure`, `possible_commitment_resolution`) are named, in §30's wording, on the node's status line, and so in its button's accessible name, on **every** lens, whatever the toggles say. A pair finding is an edge, and is restated in the detail of both its ends (§19.6).

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
- the saved ideas that serve it, each with the driver action its `serves` edge carries;
- candidate findings.

Actions:

- Focus next scene (§16.5);
- Open ledger entry (§12.1 address);
- Filter to this arc: sets `?arc=` and shows that record, its aliases and link neighbours, the scenes it touched, and the actors it involves. On a merged record it filters to the **canonical** (`merged_into`), since a non-canonical `?arc=` reads as no filter; it reads “Show the whole graph” when that canonical is already the filter (Slice F plan, Decision 23).

Focus next scene is shown unless the record is merged, and is enabled only when the record is among `drivers.snapshot`'s thread and commitment drivers (the node's `focusable`), so the graph never sends a ref the chooser would drop. Open ledger entry is built only with `ledgerHref` (§12.1). Each candidate finding reads “<finding phrase> with <the other record>” for a pair kind, and links to its Ledger address.

A `serves` edge's action is named at both ends, in the chooser's driver-chip words (Advances / May close / Addresses / May fulfil / May break / May expire / Anchored to): an idea's “Serves” row reads “<action> <record>”, and the served record's row reads “<idea> <action>”. The edge layer is `aria-hidden`, so two ideas that serve one driver with different actions would otherwise read alike to everyone (Slice F plan, Decision 26).

Selecting an event, birthday or holiday shows its date, in-days, linked records (an idea that serves it with its action, as above), and **Anchor next scene** (§16.5). Anchor is enabled only when the ref is among `drivers.snapshot`'s `anchors` (the node's `anchorable`).

Selecting an actor shows:

- scenes;
- related active drivers;
- relationships;
- an upcoming birthday, if one is recorded. “Upcoming” means inside `calendars.UPCOMING_WINDOW_DAYS`, because birthday nodes exist only inside pressure's horizon (§19.2). An actor whose recorded birthday falls beyond it, or already passed this year, has no Birthday section at all, never a “no birthday” line, which would be false (Slice F plan, Decision 11).

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
- **“Once” is a constant number of whole-file reads, with no N+1** (Slice F plan, Decision 4). Literally one read of each file would mean threading one `Ledgers` through `pressure`, `drivers`, `involvement` and `effective.records`, a cross-slice refactor handed to Slice G's performance audit. What is guaranteed is no per-node card read, no image scan and no per-record ledger read, pinned by a test that counts `plot.read` and `chronicle.read_chronicle` for one thread and for five and requires the counts to be equal. Slice G kept this reading rather than literal once, and holds every capstone read path to it (§25.3; Slice G plan, Decision 7): literal once would thread one `Ledgers` through five signatures across four slices, for no N+1 and no growth. Per request, the graph reads:
  - outside the hold: the primary provider, `pressure.build`, `drivers.snapshot(cid, pressure_result=…)` over that same result, and `events.list_events`;
  - inside one hold: the scene list and one frontmatter-head location history per scene (never a transcript), the chronicle, `effective.Ledgers`, the effective records, links and live canon, involvement, the continuity doc's malformed check, the candidate cache and pending findings, `relationships.json` and `scene_ideas.json`;
  - outside again: the three name rosters, and the per-row `calendars.fixed_of` / `calendars.friendly` on scene, event and idea dates, and on a live commitment's due that no deadline item dates (§19.2).
- **Names:** characters from `overlay.character_roster`; PCs from `overlay.pc_roster`, a new image-free listing, because `overlay.list_pcs` scans images per PC; locations from a whole-kind `overlay.list_entities(cid, "locations")` read, which parses every location file but is constant per request. Slice G kept that whole-kind read (Slice G plan, Decision 8): it is one call per graph read, constant per request, and the same sweep the turn loop pays, while narrowing it to the locations a history names would need a second entity reader plus one read per named location, cheaper only for a world with many unvisited locations. `test_continuity_read_cost.py::test_the_graph_reads_location_names_once` pins one call per `graph.build`, at both sizes. The three are read separately, so one unreadable location file costs only location labels. A ref missing from its roster is labelled with its bare id (Slice F plan, Decision 5). For the same rule, `birthdays.gather` names a PC from its meta (`pcs.name_of`) rather than through `pcs.read_pc`, which scans images; the birthday line is byte-identical.
- **Fail soft (§3.9, §26):** every source and every family builder runs through one helper, so a source that raises costs only its own nodes and edges and never fails the read. The response's top-level `omitted` names which source was lost (§20), so a reader can tell an empty campaign from a broken file. A free-text date such as “midsummer” is ordinary data and records nothing (Slice F plan, Decision 16).
- **Every edge names two nodes**, and node and edge order are deterministic, so two reads of an unchanged campaign are equal (Slice F plan, Decision 15).

---

# 20. Graph and driver API types

Define typed backend and TS unions, not open string bags.

Python declares the tuples `NODE_KINDS`, `EDGE_KINDS`, `EDGE_SOURCES`, `DRIVER_KINDS`, `DRIVER_ACTIONS`, `PRESSURE_STATES` and `LINK_RELATIONS`. The TS unions mirror them, and a backend test pins the tuples so a change shows up in review.

`DRIVER_ACTIONS` (a flat tuple) and `ACTIONS_BY_KIND` (kind → its tuple of actions, §15.2) live in `store/continuity/drivers.py`, beside `DRIVER_KINDS` (Slice E plan, Decision 24).

**Nodes** are a discriminated union on `kind` ∈ scene | character | pc | location | thread | commitment | event | idea | birthday | holiday.

- `id` is the §4 canonical ref, with the kind→prefix mapping stated once (§4).
- Each kind has typed fields; there is no `meta` bag.

**Where each tuple lives** (Slice F plan, Decision 17): `NODE_KINDS`, `EDGE_KINDS`, `EDGE_SOURCES`, and two the graph adds, `EVENT_STATUSES` (`scheduled | fired | passed | undated`) and `PARTS` (the sources `omitted` may name), live in `store/continuity/graph.py`. `LINK_RELATIONS = tuple(RELATIONS)` lives in `store/continuity/effective.py`, beside the table it names. `DRIVER_KINDS` and `DRIVER_ACTIONS` live in `drivers.py`, and `PRESSURE_STATES` in `pressure.py`. The TS unions `NodeKind`, `EdgeKind`, `EdgeSource`, `LinkRelation`, `EventStatus` and `GraphPart` are hand-written in `api/types.ts` and pinned to their tuples by a backend test that parses that file. The chooser's `DriverKind`, `DriverAction`, `PressureState` and `AnchorRelation`, and the review's `CandidateKind`, are reused, never redeclared. `DriverLink.relation` is narrowed from `string` to `LinkRelation`.

**Node fields beyond the example** (Slice F plan, Decisions 9, 13 and 16). Every dated kind carries §19.2's `native`, `friendly`, `fixed` and `in_days`. In addition:

- `scene`: `order` (its index in play order), `done` (absorbed), `pcless`, and `place` (the chronicle record's flat location name, which the detail shows when the scene has no location history);
- `thread` and `commitment`: `status`, `live`, `merged_into` (the canonical ref, or null), `aliases`, `latest_beat`, `pressure` (`{state, in_days, friendly}` from the record's driver, or null when it is not one), `focusable`, and `findings`. A commitment adds `commitment_kind` and `due`, and dates from its own deadline item. A merged node carries its own title and status, `pressure: null`, `focusable: false`, and no movement or involvement edges;
- `event`: `status` (`EVENT_STATUSES`, first match of fired, passed, undated, then scheduled), `pressure` (or null), `anchorable` and `findings`, because a `possible_relation` can pair a commitment with an event;
- `holiday` and `birthday`: `pressure` and `anchorable`; a birthday adds `actor`, `precision` and `age`;
- `idea`: `premise`, `source`, `pcless` and its stored `time_anchor: {ref, relation, native} | null`, kept even when the anchor has no node.

`findings` is a list of `{id, kind, other}`: a visible review finding on the node it names, where `other` is the canonical ref at the pair's other end (when it is a node; else null) and null for a lifecycle kind. `focusable` is membership in `drivers.snapshot`'s thread and commitment drivers, and `anchorable` membership in its `anchors`.

**Top level:** the payload is `{now: {native, friendly, fixed}, nodes, edges, omitted}`. `omitted` is a list of `PARTS` (`calendar | scenes | chronicle | plot | commitments | events | continuity | candidates | relationships | scene_ideas | names`), in that order: the sources this read could not use (§19.7). It is additive, and the page names them in one note, so a broken file never reads as an empty campaign.

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

**`relation` is typed per edge kind** (Slice F plan, Decisions 12, 13 and 14). In TS, `GraphEdge` is discriminated on `kind`: a `link` carries a `LinkRelation`, a `serves` a `DriverAction`, an `anchored_to` an `AnchorRelation`, and every other kind `null`. A hand-edited action or relation outside its vocabulary draws no edge. Two kinds carry typed extras beyond the seven keys: `feeling` adds `trust`, `affection`, `tension` and `note`, and `bond` adds `bond_type` and `since_scene`. Candidate edges take the candidate kind (`possible_duplicate | possible_relation`) as `kind`. A structural or alias edge id is `"e" + sha256(kind \0 from \0 to)[:20]`, the `link_id` recipe; a link edge uses the link id and a candidate edge the candidate id.

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

**One exception: calendar plugin code** (Slice E plan, Decision 25). A user calendar plugin can raise anything, not only `CalendarError`. The suggestion snapshot's calendar block, the suggestion parser and `date_normalizer` resolve the calendar through a broad fail-soft wrapper, so under such a plugin they degrade to blank dates where they used to fail; the intent route and the saved-idea read degrade the same way. Nothing is fabricated (§26). On a working calendar every output is byte-identical. `calendars.primary_provider` itself, and its other callers across the app, are unchanged.

---

# 25. Performance and cost

## 25.1 No N+1 LLM reconciliation

- At most one identity-resolver call per absorb, and only when ambiguous proposed-new records exist.
- At most one reconciliation call per pass, on a bounded, prioritized set of candidates that have no cached proposal, and at most two passes per run: its own, and one follow-on carrying every ref adopters pended (§11.1). End Scene and Refresh each cost at most one call, and a burst of saves during a live run adds exactly one more, never one per save (Slice G plan, Decision 4).
- Do not call an LLM for every pair.

## 25.2 Candidate generation complexity

There is no numpy (Android). Pure-Python dot products are O(d) each, so all-pairs is O(n²·d). The sweep therefore:

- runs in a worker thread, never on the event loop;
- restricts pair scoring to changed × all (incremental) or all × all (explicit refresh), under a hard `RECONCILE_MAX_PAIRS` cap. When pairs are dropped at the cap, the run reports `pairs_capped: true`;
- computes identity texts once;
- computes each record's lexical features (normalized tokens and character trigrams) once per sweep, cached on the record's `Subject` (`Subject.features()`), not once per pair; only the two title comparisons stay per pair, because a title is a few words. The memory is bounded by the pool size times `CONTINUITY_IDENTITY_BYTES`, for one sweep. An `lru_cache` keyed on text was rejected: unbounded across sweeps unless sized, and a size would be one more constant to tune (Slice G plan, Decision 6; `test_continuity_similarity.py::test_lexical_normalizes_each_text_once`, `test_continuity_reconcile.py::test_a_sweep_builds_each_identity_text_once`);
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

“Once per request” is a constant number of whole-file reads (§19.7), and Slice G holds every capstone read path to it, not only the graph (Slice G plan, Decision 7): the Ledger, `GET /continuity`, the candidates read, the drivers read, the graph, the saved-idea read and the two Todo continuity chores. On each, every whole file is read a constant number of times, independent of record, scene and finding counts; no single scene file is read more often as the campaign grows; and no transcript, card version or image directory is read. Pinned by `test_continuity_read_cost.py::test_read_paths_read_each_file_a_constant_number_of_times` and `::test_the_continuity_chores_read_a_constant_number_of_files`.

The no-card, no-image rule applies to the candidates read and the saved-idea read too (Slice G plan, Decision 9). An actor's name comes from its meta, never from its card versions or image directories: `relationships.actor_name` reads `characters.name_and_versions` for a character and `pcs.name_of` for a PC, and `suggest._actor_name` names a PC through `pcs.name_of`. The names are byte-identical, so prompts and the frozen snapshot do not move. Pinned by `test_relationships_store.py::test_actor_name_reads_meta_only`, `test_continuity_read_cost.py::test_continuity_review_reads_name_actors_without_cards` and `::test_the_saved_idea_read_names_a_pc_birthday_without_images`.

## 25.4 Candidate cache

Todo and shell badge reads must not perform embedding calls or full transcript reads. Todo's continuity chores use the §18.2 live filter only. No new shell badge is added in this milestone.

The rule binds what the capstone adds to Todo and the shell (Slice G plan, Decision 17). `GET /shell` read each open scene's transcript to count its model replies before the capstone (`routes/shell._scene_turns`), a cost its docstring bounds by the open scenes, and removing that rail count is not the capstone's to do (§33). The shell's transcript reads stay exactly those, whatever the continuity records and findings number, and it does no continuity review work: `test_continuity_read_cost.py::test_the_shell_read_does_no_continuity_review_work`.

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
| calendar unavailable | Temporal arithmetic degrades to undated/free-text; no fabricated dates. This includes a calendar plugin that raises anything: the suggestion snapshot's calendar block, the suggestion parser and `date_normalizer` degrade to blank dates rather than failing the run (§24; Slice E plan, Decision 25) |
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
- no generation call: across mount and every lens or toggle change, the only API call is the graph read, made once -- under StrictMode too, whose rehearsed mount joins the read the first setup has in flight rather than sending a second (the read stays `fresh`: only an in-flight read is joined, so a later mount, a Retry or a campaign switch reads afresh).

## 28.10 LLM eval cases

Add synthetic cases using the established placeholder names. Each case ships a `<case>.compliant.json` and at least one counterexample declaring its exact failing checks. `prompt.*` checks render the enum and decision instructions verbatim. Behavioural claims (does the model *choose* correctly) are answered only by `evals/run.py --live`.

At minimum:

1. Same obligation, different wording → identity resolver selects existing.
2. Same topic, distinct plot questions → not merged (distinct or related).
3. Broad thread and concrete continuation → continuation (or subthread) from the concrete record, not duplicate.
4. Thread and commitment about the same incident → pays_off/related, never duplicate.
5. Thread whose accumulated beats clearly answer its question → close.
6. Old but unresolved thread → keep_open.
7. Deadline passed and promise demonstrably kept → fulfilled.
8. Deadline passed but the evidence does not establish an outcome → keep_open/uncertain.
9. Two focused drivers with several valid alternative scenes → suggestions distribute coverage rather than cloning one premise.
10. Anchor date in custom calendar notation → parser/date derivation remains valid.

There is no scene-suggestion eval case today. Add a `scene-suggestions` case and grader for cases 9–10.

The case-to-check table lives in Appendix B: each of the ten cases names its eval case, its grader check and the counterexample recording that trips that check, and `test_capstone_acceptance.py` holds the table to `evals/cases.py`. Cases 2–8 share one eval case, `continuity-reconcile`, whose first four counterexamples never tripped `reconcile.cross_type`, `reconcile.close` or `reconcile.fulfilled`, so a grader that stopped scoring cases 4, 5 and 7 would have stayed green. The `continuity-reconcile.timid` counterexample trips exactly those three (Slice G plan, Decision 14).

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

Both rows ship, and each carries fields beyond the list above, every one a count or a closed mode (Slice G plan, Decisions 1–3). Their exact key sets and vocabularies:

- **`continuity identity check`** (`routes/scenes._identity_outcome`, one per absorb that proposes a new thread or commitment; an absorb that proposes none logs no row, and neither does one whose row-writing step itself raises, which `_identify` records as a failed phase instead): `kind` (`continuity-identity`), `campaign`, `scene`, `status` (`ok` | `degraded` | `failed` | `skipped`), `matching` (`basic` | `semantic`), `embedding` (`off` | `configured` | `failure`), `embedding_error` (empty, a word from `llm_errors.KINDS`, or `unexpected`), and the counts `Examination.counts()` gives: `proposed`, `examined`, `candidates`, `deterministic`, `semantic`, `embedded`, one per `identity.CHECK_DECISIONS` word (`existing`, `new`, `uncertain`, `unchecked`), `downgraded` and `hint_only`. When the examination itself raised, the row carries the modes only, with `embedding` and `embedding_error` empty.
- **`continuity reconcile`** (`routes/continuity._log_pass`, one per pass): `kind` (`continuity-reconcile`), `campaign`, `sweep` (`full` | `incremental`), `matching`, `embedding` and `embedding_error` (as above), `llm` (`off` | `skipped` | `ok` | `failed`), `continuity` (`ok` | `malformed`), the counts `candidates`, `deterministic`, `semantic` and `adjudicated`, one count per word in `reconcile.DECISIONS`, and the flags `pairs_capped` and `superseded`. Only this row carries the two flags, because only the sweep caps pairs or races a newer run.

Every count is an integer and every other string field comes from its closed vocabulary; no title, beat, identity text, prompt line or model reason appears anywhere in either row. Pinned by `test_absorb_identity.py::test_identity_log_row_is_counts_and_closed_modes_only` and `test_continuity_reconcile_routes.py::test_reconcile_log_row_is_counts_and_closed_modes_only`. The route above is pinned verbatim, as the only route claiming a `continuity-*` task, by `test_routing.py::test_the_continuity_route_is_spelled_as_section_29_spells_it`. The Debug-capture note lives in `templates/README.md`, under each continuity template family, and in `docs/incoming-llm-capture.md`, which `test_docs_guard.py::test_llm_capture_doc_names_every_continuity_task` requires to name every task the route claims.

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
- “Claims to address” — on a card, a self-reported claim on an avoided driver reads “Claims to address <label> (avoided)”, and a missed must reads “Doesn't claim to address <label>” (Slice E plan, deviation 15; §16.4)

Avoid:

- “AI detected duplicate” as a certainty;
- “Continuity score”;
- “Broken campaign”;
- “Embedding required.”

The README may gain a concise mention of reconciliation and the Story Graph after implementation. Implementation details belong in docs.

These lists are held to the code (Slice G plan, Decision 12). `test_continuity_wording.py::test_no_continuity_surface_uses_a_phrase_section_30_avoids` reads every non-test source file under `backend/src/grimoire/` and `frontend/src/`, and `README.md`, for the avoided phrases, since a list of capstone files would miss the next surface. Because that scan is the whole app and not only the continuity surfaces, it looks for the phrases the spec avoids by name and their plain rewordings and nothing else, case-insensitively and as whole words: §30's four — “AI detected duplicate” (also unattributed, reordered, or with an article or plural: “AI detected a duplicate”, “Duplicates detected”), “Continuity score” (with §2's “continuity health score”), “Broken campaign”, and “Embedding required” (“Embeddings are required”) — and §3.3's “continuity disabled” (“Continuity is disabled”). A phrase the spec does not name, such as “health score”, is not scanned for, since it is plain copy for other pages (a mechanics module's HP display). `::test_the_avoid_list_is_the_spec_phrases_and_their_rewordings` holds the list to those five; `::test_the_avoid_scan_catches_each_avoided_claim_in_its_plain_forms` and `::test_the_avoid_scan_leaves_plain_copy_for_other_pages_alone` hold its reach by example, so a narrowing that stops catching a form fails. `::test_section_30_preferred_phrases_are_used` pins each preferred phrase, as whole words, to the surface that shows it. No store token reaches a reader on a review surface: journal labels and link refusals speak relation words (§12.8), Reviewed links / merges speaks reasons and missing records in words (§12.6), and each link relation has one wording across the Ledger, the Story Graph and the journal (§5.3). Unchanged by decision: an unknown relation in `RELATION_PHRASES` reads as itself (reachable only by a hand edit), the hedged proposal labels (“Suggested: before” and its siblings, pinned against each other on both sides), and the chooser's time-anchor options, whose relation is not a link.

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

Two Slice B read tolerances went beyond the Slice B plan, each added by a review fix and until Slice G recorded only in the slice's own ledger (Slice G plan, Decision 16):

- **`events._UNREADABLE = (CalendarError, ValueError, OverflowError)`**, the set `birthdays` already guards each actor with. One hand-edited event date that a provider's arithmetic cannot read (a year past its range) reads as undated in the events panel, the advance digest and pressure, rather than taking every event out of the pressure list.
- **`continuity.doc._UNREADABLE = (ValueError, RecursionError, OSError)`**. A continuity.json whose JSON the parser refuses without a decode error (an overlong integer, deep nesting) reads empty like any other unreadable file, so the briefing and the advance digest keep the obligations the prompts show, and the writer still refuses it with `ContinuityError` rather than leaking a raw `ValueError`.

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
- **Additive response fields** (Decisions 23, 24; the failure-note fields refined during implementation, plan deviation 27): the candidates read adds `beats`, `due`, `stale_reason`, `names`, `scenes` and `run`; `GET /continuity` suppressions add `live` and `titles`; a reconcile run's result carries `follow_on` and `continuity`, and a failed run's `error` carries `sweep`, `saved` (whether persist 1 landed) and `follow_on` (whether the follow-on pass failed after the first landed). Refresh chooses its failure note from them: a failed follow-on pass after a first pass that saved says the first pass's findings are listed; otherwise “basic findings are listed” only when `saved` is true; a start the POST itself refused, or a run that failed before persist 1 landed (`busy`, `io`, `malformed`), says nothing it found was saved; a failure that says neither — including a poll's `run_gone` 404, from a run reaped or a server restarted after it may have saved — says only that its outcome could not be read back. A `malformed` refusal at persist 2 says the model's suggestions were not saved, and one before a follow-on pass's persist 1 says nothing that pass found was saved.
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
- **An empty candidate cache answers the candidates read without the join**: with no cached record (no sweep yet, one that found nothing, or a cache that will not parse) the read does no pressure pass, no current view and no scene list, and answers `names: {}` and `scenes: []` beside the usual `generated`, `matching`, `diagnostics` and `run`. `names` and `scenes` exist to label findings and only an open finding's detail reads them, while the Ledger mounts this read for every section and re-reads it on every write (the cost rule §18.3 holds Todo to, applied to the review read).
- **§28.10 cases 2 and 3 are graded as "not merged"**, not by a single word: `reconcile.distinct` accepts `distinct` or `related` for case 2, and `reconcile.continuation` accepts `continuation` or `subthread` for case 3, each with the concrete record as `from`. The shipped system prompt offers `related` for two questions that bear on each other, which two same-topic plot questions do by construction, and names `continuation` and `subthread` with no rule between them, so a single word would fail answers the prompt treats as correct. What both cases claim is the not-duplicate half: a `duplicate` still fails each, and a `subthread` from the broad record still fails case 3 (Task 18; slice ledger, Task 18 review fix).

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

Slice G implementation deviations. Each was decided in the Slice G plan (`docs/superpowers/plans/2026-10-06-continuity-capstone-g-polish.md`) and is cited by its Decision there.

- **The two §29 log rows carry fields beyond §29's list**, every one a count or a closed mode; §29 names them (Decision 1).
- **A run may make two model calls**, its own pass's and one follow-on's that carries every ref adopters pended; never one per trigger (Decision 4; §11.1, §25.1).
- **A zero-vector text is re-sent once per sweep** (Decision 5; §9.4).
- **Lexical features are computed once per record per sweep**, on the `Subject`, with the two title comparisons kept per pair (Decision 6; §25.2).
- **“Once per request” is a constant number of whole-file reads**, never literal once, on every capstone read path (Decision 7; §18.2, §19.7, §25.3), and **location names stay a whole-kind read** (Decision 8; §19.7).
- **Actor names on the candidates and saved-idea reads come from meta**, never from card versions or image directories (Decision 9; §25.3).
- **A partial delete answers a structured 500 `partial_delete`**, worded by noun, and bumps the write token itself (Decision 10; §5.7).
- **Four small hand-off fixes** (Decision 11): an unmerge or a removed link refreshes the rail's counts (`api.removeAlias` / `api.removeLink` notify the shell, as apply, dismiss and restore do); `birthdays.crossed` skips a birthdate that raises any of `birthdays._UNREADABLE`, not only `CalendarError`, so a huge-year birthdate no longer fails an advance; a failed reconcile run's error body is typed (`ReconcileRunError`); and the scene list's “Try again” reads the list again.
- **No store token reaches a reader on a review surface**: journal labels and link refusals speak relation words, Reviewed links / merges speaks reasons and missing records in words, raw links carry titles, and `by` reads “Due by” in the Ledger as in the Story Graph (Decision 12; §5.3, §12.6, §12.8, §30).
- **The §30 avoid-list guard scans for the phrases the spec avoids by name — §30's four and §3.3's “continuity disabled” — and their plain rewordings only** (Decision 12; §3.3, §30). The plan's Task 5 list was bare substrings and also named “ai detected” and “health score”; the scan covers every non-test source file, so a phrase the spec does not name would fail an unrelated page, and “health score” was dropped. “ai detected” is kept only where it attributes a duplicate, as part of a whole-word pattern that also reaches articles, plurals and verb forms the substrings missed (“AI detected a duplicate”, “Duplicates detected”, “Embeddings are required”).
- **§28.10's cases are each held to a grader check that a counterexample trips**, with `continuity-reconcile.timid` for cases 4, 5 and 7 (Decision 14; §28.10, Appendix B).
- **§25.4 binds what the capstone adds**; the shell's pre-capstone transcript read stays (Decision 17; §25.4).
- **A dated item with no present reads “no current date” in the suggestion prompt and the chooser, not “undated”** — the timeline and Story drivers rows and the anchor options. Plan E's Decision 10 mapped every null `in_days` to `undated`, which printed “13 May 2026 (undated; ok)” for an event the same prompt offered as an anchor; “undated” is kept for an item with no `fixed` (final code review, not a plan Decision; §19.2).
- **A blank `due` is dropped when a row is moved onto an existing commitment** — by an accepted `existing` or by an as-existing alternative — rather than kept because the key was present: on a row that would have opened a record `""` lifts nothing, and carried across it cleared the stored deadline (final code review, not a plan Decision; §10.2).
- **A review's merge replaces a source's broken alias** — one whose hop does not resolve — in one journalled row whose undo restores it, rather than answering `alias_exists`; a live alias is still refused, and the manual route still needs `replace: true` (final code review, not a plan Decision; §5.1, §26).

Slice G rulings. Every hand-off Slices A–F addressed to Slice G, or deferred, that Slice G does not close, with its reason (Decision 16). A deferred defect remains open for §33's correctness-defect channel; a parked feature — (f3)'s graph node families and (f5)'s wide-canvas virtualization — is parked by §33 and is not a correctness defect.

- **(a2) Alias edge cases reachable only by hand.** Cycle detection reads the raw alias graph; `create_alias`'s `source` is not validated; `affected` under-reports over a hand-edited cycle; the private `_text` helpers do not strip (Slice A review F8, F11, d and f). Deferred: each is reachable only by a hand edit or an internal parameter, and `effective.live_canon` already stops at the bad hop.
- **(b2) `clock.read` on a hand-edited clock.json.** It raises on a very long integer or deep nesting. Deferred: it is a pre-capstone reader, and the capstone reads the clock only through soft wrappers (`pressure._soft`, `_IdeaContext.load`), so no capstone surface fails on it.
- **(b4) Hebrew month-only birthdays across the leap cycle.** Deferred, and recorded as a residual in §13.6: the fix moves the intent prompt's Birthdays line, which §15 holds byte-identical.
- **(b5) A calendar plugin whose `describe` raises on one day** still fails the events panel's read (`routes/campaigns.py`). Deferred: it predates the capstone, it is reachable only through a broken calendar plugin, and its fix would change the events-panel read, outside Slice G's decisions.
- **(c1) Slice C's final minors.** The “Switched” label's `before !== ""` check (a race); candidate titles folded by `_text` and a moved candidate's raw `latest_beat` (display-only); `identityProposal`'s bare id for a record that was not offered (the model's own spelling of an id it named); a spent budget skipping semantic matching with no `fallback` (a race); `distinguished_from` carrying an id `_offerable` excluded (hand-edited ids only); fallback survival pinned for two of five paths, and the frontend `checkedRows` guard on the fallback hint untested. Deferred: each is display-only, race-only or hand-edit-only.
- **(d2) The Ledger's delete and mutator paths keep record ids unencoded.** Deferred: this is pre-capstone Ledger behaviour; new ids are slugs (`materializer` allocates `slugify(title)`, and an explicit new id must already be one); fixing a `/` in a legacy id needs a path converter on every sibling ledger route.
- **(d7) Two Refresh-note nuances.** A second Refresh pass overwrites the first pass's note, and the follow-on note replaces the no-model note. Deferred: copy nuance in rare sequences, which the next Refresh corrects.
- **(e3) Tuning `DRIVER_PROMPT_CAP`**, which the driver index and the rendered timeline share. Deferred: tuning needs real prompts, and Slice G measures only synthetic stores. The constant's comment says to tune it against real prompts later.
- **(f3) Groups and facts as graph nodes.** Deferred: §19.2 makes them optional, and §33 parks more graph node families.
- **(f5) One wide canvas for a very large campaign.** Deferred: §19.7 forbids a cap, and column virtualization is the stated fix if one is ever needed. (A scene stamped in a secondary calendar's notation reading as Undated is not deferred: §3.8 dates on the primary provider only, so it is honest.)
- **(f7) The Story Graph's node-detail copy and its play-axis column headings** (“Review this finding”, “Feels toward” / “Felt by”, “since a deleted scene”, “Scene idea before this:” and its siblings, “Served by”, “Birthday of”; “Scene <n>” columns). Deferred: Slice F names no defect in either, only a wording pass, and rewording them would change `storyGraph/NodeDetail.tsx` and `storyGraph/layout.ts`, outside Decision 12. The §30 avoid-list guard still reads both files, and the anchored-to “due by this:” already agrees with “Due by”.

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

8. Suggestions see a bounded list of upcoming events, holidays, birthdays and deadlines, rather than only the single nearest item. The snapshot's `timeline` holds every pressure item; the rendered timeline is bounded with the driver index's rule, always keeping the controls' refs and `events.sooner`'s pick (§15; Slice E plan, Decision 11).

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

---

# Appendix B. Acceptance evidence

Each acceptance criterion (§32) and the stopping rule (§33), with the tests and eval cases that prove it on the integrated tree (Slice G plan, Decision 14). `backend/tests/test_capstone_acceptance.py` fails when a criterion has no row, or when a cited backend test, frontend test title or eval case does not exist, so a rename cannot leave a criterion silently proven by nothing. Backend tests live under `backend/tests/`.

| Criterion | Claim | Evidence |
|---|---|---|
| AC1 | A new thread or commitment is compared against plausible same-type records before it is treated as new | `test_absorb_identity.py::test_close_candidate_mapped_to_existing_rewrites_the_row`; `test_absorb_identity.py::test_a_new_record_with_no_close_neighbour_makes_no_identity_call`; `test_continuity_identity.py::test_reworded_duplicate_is_examined_with_the_record_as_candidate`; `test_continuity_identity.py::test_cross_type_never_a_candidate`; eval `continuity-identity` |
| AC2 | Matching works without embeddings and improves with them | `test_continuity_identity.py::test_wordless_paraphrase_is_not_a_candidate_without_embeddings`; `test_continuity_identity.py::test_semantic_matching_finds_a_wordless_paraphrase`; `test_continuity_reconcile.py::test_unconfigured_makes_no_embedding_call`; `test_continuity_reconcile_routes.py::test_refresh_works_with_no_connection` |
| AC3 | Todo says when semantic matching is not configured, and that basic matching still works | `test_todo_route.py::test_missing_embeddings_shows_one_library_chore_on_the_global_page`; `test_todo_route.py::test_embeddings_with_recall_depth_zero_show_no_chore`; `test_todo_route.py::test_no_embeddings_still_shows_continuity_chores` |
| AC4 | Reconciliation proposes duplicate, relation, closure and resolution findings without applying them | `test_continuity_reconcile_routes.py::test_a_duplicate_proposal_mutates_nothing_until_apply`; `test_continuity_reconcile.py::test_a_stale_thread_is_nominated_for_closure_not_closed`; `test_continuity_reconcile.py::test_a_passed_deadline_nominates_but_does_not_resolve`; `test_continuity_reconcile.py::test_thread_and_commitment_overlap_is_a_relation_never_a_duplicate`; `test_continuity_reconcile_prompt.py::test_resolutions_need_evidence_too_and_carry_their_status`; `test_continuity_writer_guard.py::test_discovery_modules_never_write_reviewed_state`; `test_continuity_writer_guard.py::test_the_scanned_modules_exist`; eval `continuity-reconcile` |
| AC5 | A reviewed duplicate is merged non-destructively and reversibly | `test_continuity_effective.py::test_removing_alias_restores_two_records`; `test_continuity_review.py::test_create_alias_journals_and_undo_removes`; `test_continuity_undo.py::test_undo_alias_create_and_redo`; `test_continuity_review_routes.py::test_applying_a_duplicate_writes_an_alias_only` |
| AC6 | Prompts show a merged record once, under its canonical, and a campaign with no aliases sees byte-identical sections | `test_continuity_effective.py::test_identity_law_threads`; `test_continuity_effective.py::test_identity_law_commitments`; `test_continuity_effective.py::test_render_helpers_obey_identity_law`; `test_continuity_effective.py::test_render_helpers_show_canonical_ids_only`; `test_context.py::test_the_play_prompt_lists_a_merged_record_once_under_its_canonical`; `test_absorb_store.py::test_absorb_snapshots_show_canonical_ids_only`; `test_briefing_route.py::test_a_merged_thread_briefs_once_under_its_canonical`; `test_frozen_campaign.py::test_the_frozen_campaign_still_reads_the_way_it_was_recorded` |
| AC7 | Commitments are first-class suggestion inputs | `test_suggest_store.py::test_snapshot_has_ids_commitments_timeline_and_index`; `test_suggest_store.py::test_prompt_renders_refs_commitments_timeline_and_index` |
| AC8 | Suggestions see a bounded list of events, holidays, birthdays and deadlines, keeping the controls' refs and the sooner pick | `test_continuity_pressure.py::test_several_holidays_are_kept_and_today_included`; `test_continuity_pressure.py::test_events_listed_regardless_of_horizon_and_ordered_on_the_fixed_axis`; `test_continuity_pressure.py::test_hebrew_birthday_and_event_ordering`; `test_suggest_store.py::test_snapshot_has_ids_commitments_timeline_and_index`; `test_suggest_controls.py::test_timeline_is_capped_but_keeps_sooner_and_anchors`; `test_suggest_store.py::test_timeline_contains_what_sooner_picks` |
| AC9 | Date arithmetic is provider-driven and deterministic | `test_continuity_pressure.py::test_hebrew_birthday_and_event_ordering`; `test_continuity_pressure.py::test_fake_provider_axis`; `test_suggest_controls.py::test_on_derives_the_date`; `test_suggest_store.py::test_plugin_calendar_dates_derive_in_native_notation`; eval `scene-suggestions-anchor-on` |
| AC10 | The reader can focus on, avoid and require drivers, and choose a time anchor | `test_suggestion_controls_route.py::test_must_and_avoid_overlap_is_400`; `test_suggestion_controls_route.py::test_must_cap_and_kind_are_400`; `test_suggestion_controls_route.py::test_stale_refs_are_409`; `test_suggest_controls.py::test_focus_avoid_must_report_misses`; `test_suggest_controls.py::test_batch_anchor_forces_every_suggestion`; `frontend/src/components/NewSceneChooser.test.tsx` "driver controls alter the request body"; `frontend/src/components/NewSceneChooser.test.tsx` "anchor mode sends the anchor and relation" |
| AC11 | Cards show validated reasons and drivers, and make constraint misses visible | `frontend/src/components/SceneIdeaPicker.test.tsx` "a generated card shows its validated reasons"; `frontend/src/components/SceneIdeaPicker.test.tsx` "constraint misses show warning chips"; `frontend/src/components/SceneIdeaPicker.test.tsx` "a rejected date says so"; `test_suggest_store.py::test_parse_output_resolves_labels_and_dates`; eval `scene-suggestions` |
| AC12 | Saved ideas keep driver and time provenance, and go visibly stale without being destroyed | `test_scene_ideas_provenance.py::test_post_scene_idea_round_trips_provenance`; `test_scene_ideas_provenance.py::test_stale_reason_after_resolution`; `test_scene_ideas_provenance.py::test_reads_never_write`; `test_suggestion_controls_route.py::test_saved_card_round_trip_goes_stale_after_resolution`; `frontend/src/components/SceneIdeaPicker.test.tsx` "stale ideas sit under a collapsed Stale group outside the budget" |
| AC13 | Todo surfaces pending continuity review without model calls | `test_todo_route.py::test_todo_makes_no_embedding_calendar_or_client_call`; `test_todo_route.py::test_continuity_counts_come_from_the_cache_after_the_live_filter`; `test_continuity_read_cost.py::test_no_read_only_path_reaches_a_model_or_an_embedding` |
| AC14 | The Story Graph shows played history and obligations from the data suggestions use | `test_continuity_graph.py::test_focusable_is_exactly_the_chooser_driver_set`; `test_continuity_graph.py::test_anchorable_is_exactly_the_chooser_anchor_set`; `test_continuity_graph.py::test_scenes_follow_play_order_not_recency_or_date`; `frontend/src/routes/StoryGraphView.test.tsx` "renders the drawing from one graph read" |
| AC15 | Every alias and link write is reviewable and journalled, and undoes | `test_continuity_undo.py::test_undo_alias_create_and_redo`; `test_continuity_undo.py::test_undo_link_create_and_redo`; `test_continuity_review.py::test_create_link_journals_and_round_trips`; `frontend/src/routes/LedgerContinuity.test.tsx` "apply requires an explicit action" |
| AC16 | No read-only navigation path launches an embedding or a model request | `test_continuity_read_cost.py::test_no_read_only_path_reaches_a_model_or_an_embedding`; `test_continuity_routes.py::test_graph_route_makes_no_model_call`; `test_suggestion_controls_route.py::test_opening_paths_make_no_model_call`; `frontend/src/routes/StoryGraphView.test.tsx` "only one graph read across mount, lenses, toggles, arcs and nodes"; `frontend/src/components/NewSceneChooser.test.tsx` "changing a control starts no generation"; `frontend/src/routes/LedgerContinuity.test.tsx` "a stale 409 re-renders current records and does not start a run" |
| AC17 | Existing campaigns open and play with no migration | `test_frozen_campaign.py::test_the_frozen_campaign_still_reads_the_way_it_was_recorded`; `test_frozen_campaign.py::test_the_read_only_sweep_writes_nothing`; `test_continuity_doc.py::test_absent_file_reads_empty`; `test_continuity_candidates.py::test_absent_cache_reads_empty`; `test_scene_ideas_provenance.py::test_ideas_without_provenance_read_nothing_extra` |
| AC18 | Settings discloses automatic continuity embedding before it first happens | `frontend/src/routes/ConfigView.test.tsx` "the embeddings chip reports embedding separately from recall depth"; `frontend/src/routes/ConfigView.test.tsx` "the disclosure names every embedded payload" |
| AC19 | The gate passes, and both final reviews ran or were stood in for | `make check` (every target, frontend typecheck and template verification included); `evals/run.py` (every recording scores as declared); §31 Slice G's gate bullet, which records both final reviews and who stood in for them (Slice G plan, Task 8) |
| §33 | The capstone stops at its spec: no kind, node, edge or control beyond it | `test_continuity_candidates.py::test_kinds_are_the_four_in_spec_order`; `test_continuity_graph.py::test_graph_tuples_are_pinned`; `test_suggest_controls.py::test_controls_tuples_are_pinned`; the stopping-rule review over the whole capstone (Slice G plan, Task 8) |

Each of §28.10's ten cases, held to the grader check that scores it and the counterexample recording that trips that check. `test_evals.py::test_recording_scores_as_declared` proves each recording trips exactly the checks it declares, and `test_capstone_acceptance.py` holds this table to those declarations. Where a row names two eval cases, its three columns list the clauses in the same order, separated by `;`.

| §28.10 case | Eval case | Check | Tripped by |
|---|---|---|---|
| 1 | `continuity-identity` | `identity.same_obligation` | `unknown-id` |
| 2 | `continuity-reconcile`; `continuity-identity` | `reconcile.distinct`; `identity.distinct` | `merged`; `merged` |
| 3 | `continuity-reconcile`; `continuity-identity` | `reconcile.continuation`; `identity.continuation` | `merged`; `merged` |
| 4 | `continuity-reconcile` | `reconcile.cross_type` | `timid` |
| 5 | `continuity-reconcile` | `reconcile.close` | `timid` |
| 6 | `continuity-reconcile` | `reconcile.keep_open` | `eager` |
| 7 | `continuity-reconcile` | `reconcile.fulfilled` | `timid` |
| 8 | `continuity-reconcile` | `reconcile.unproven` | `eager` |
| 9 | `scene-suggestions` | `suggest.focus_coverage`, `suggest.distinct` | `cloned` |
| 10 | `scene-suggestions`; `scene-suggestions-anchor-on` | `suggest.date_consistent`; `suggest.on_derived` | `bad-date`; `compliant` (an “on” batch's date is derived from its anchor, so no reply can get it wrong: the compliant recording, graded in a custom calendar, is the evidence) |
