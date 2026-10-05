# Continuity Capstone — persistent narrative graph, reconciliation, temporal pressure, and scene-driver control

**Date:** 2026-10-04  
**Status:** Design specification — ready for mandatory adversarial review before implementation planning  
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
- docs/superpowers/specs/2026-07-12-mechanics-phase5-absorb-validation-design.md, because it is the best existing example of a second-pass, reviewable validator beside absorb

The mandatory project workflow still applies:

1. This spec receives /codex:adversarial-review.
2. Findings are resolved in the spec.
3. A concrete implementation plan is written under docs/superpowers/plans/.
4. That plan receives /codex:adversarial-review.
5. Implementation is done with inline TDD and the repository gate.
6. The completed diff receives /codex:review.
7. The completed diff plus this originating spec receives a final /codex:adversarial-review for implementation drift.

Do not skip those gates because this document is already detailed.

Privacy rules are especially important here. No committed example may use a name, count, event, campaign title, or other fact from the user's real data store. Use the repository's established placeholder names such as Seraphine, Mara, Winifred, Realm, and Saltmarch.

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

The remaining weakness is not that these facts are absent. It is that they are still mostly separate ledgers assembled into prompts. Long campaigns can accumulate semantically duplicate threads, commitments that are no longer truly open, near-identical obligations under different wording, and time-sensitive story business that is present in the files but not strongly represented in next-scene choice.

This capstone turns the existing continuity files into a maintained, inspectable narrative graph without replacing them.

The finished feature must make four things true:

1. **History remains coherent over long campaigns.** New extraction should preferentially attach to existing records, and a separate reconciliation system should find likely duplicates, closures, continuations, and related records that scene-local extraction missed.

2. **Time matters structurally.** Scene suggestions and continuity review must reason over campaign date, scheduled events, commitment deadlines, holidays, birthdays, and reviewed temporal links using the existing calendar-provider arithmetic rather than model date arithmetic.

3. **The reader can steer the next scene by story pressure.** Generated suggestions must identify which live narrative drivers they serve. The reader can focus, avoid, or require drivers and can anchor the batch to meaningful upcoming moments.

4. **The same structure is visible.** A campaign Story Graph exposes scenes, actors, places, threads, commitments, events, reviewed links, and future scene ideas. It is not a decorative side database; it is a projection of the same structures suggestions and reconciliation use.

When this capstone is complete, continuity feature development is considered **parked** except for correctness defects and small usability fixes. Further narrative-analysis ideas belong in a later backlog. The next major product investment should move to mechanics.

---

# 2. Non-goals

This work does **not**:

- replace chronicle.json, plot.json, commitments.json, facts.json, relationships.json, events.json, scene files, or scene_ideas.json with a graph database;
- add SQLite, a vector database, or a compiled nearest-neighbor dependency;
- make embeddings mandatory;
- make an LLM authoritative over merges, closures, facts, dates, or any store write;
- silently rewrite or delete historical thread/commitment records;
- automatically close a thread because it is old;
- infer that a commitment was fulfilled merely because its deadline passed;
- merge plot threads with commitments;
- make Todo, opening the graph, opening the ledger, or opening the scene chooser launch hidden LLM work;
- create a numerical “continuity health score”;
- make every lore record a graph node by default;
- solve act/chapter detection, theme detection, literary analysis, or automatic campaign-era labeling;
- replace the current review-before-apply posture;
- make semantic recall depth control continuity embeddings;
- add mechanics-aware scene drivers in this milestone. The graph and driver schema must leave room for them later, but mechanics is the next separate body of work.

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
- same characters but different issue;
- a commitment and thread about the same incident;
- merely topical overlap.

No score, threshold, or nearest-neighbor rank may itself write a merge, close a thread, resolve a commitment, or create a semantic link.

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

The UI must describe this as “basic matching active; semantic matching not configured,” never as “continuity disabled.”

## 3.4 Reviewed semantics and derived candidates are separate

A possible duplicate is not the same kind of data as an accepted continuation link.

Pending findings are disposable and recomputable.

Reader-approved links, merge aliases, and explicit dismissals are durable campaign state and must be journalled/reversible where they change campaign semantics.

## 3.5 Merges are non-destructive

A duplicate thread or commitment is never deleted merely because another record becomes canonical.

A reviewed merge creates an alias from the duplicate reference to the canonical reference. The original source record remains in its original file unchanged.

All effective readers used for prompts, suggestions, Todo, and the Story Graph canonicalize aliases so the duplicate does not act like a second live obligation.

Removing the alias restores the previous view with no reconstruction.

## 3.6 Different record types are never merged

A plot thread and a commitment may be strongly related. They remain different things.

A thread advances and closes.

A commitment is owed and resolves as fulfilled, broken, or expired.

Cross-type semantic relationships are links such as pays_off or related_to, never aliases.

## 3.7 Closure requires positive evidence

Staleness can nominate a thread for review. A passed event can nominate a commitment for resolution review. Neither is enough to resolve the object.

A thread closes only after a reviewed closure operation backed by an existing beat/scene or an explicitly accepted manual decision.

A commitment resolves only to one of its existing resolution states and only after review.

## 3.8 Timeline math is deterministic

The model does not calculate day offsets, birthdays, holiday dates, or event ordering.

Use CalendarProvider and the existing fixed-day axis.

The model may interpret prose deadlines that deterministic code cannot parse, but it does not manufacture a numeric date for them.

## 3.9 Reads fail soft; writes fail visibly

Graph projection, candidate generation, driver projection, and prompt assembly must tolerate a malformed optional continuity file by omitting the affected enhancement.

A write that cannot preserve continuity semantics must reject visibly. It must never silently drop an alias, relation, candidate decision, or reviewed operation.

## 3.10 Opening a read-only surface costs no model call

These are pure reads:

- Todo;
- Ledger;
- Story Graph;
- scene chooser initial local data reads;
- graph filtering;
- driver-chip filtering.

Generation occurs only where generation already belongs: scene suggestion generation, absorb/reconciliation computation, or an explicit refresh/reconcile action.

---

# 4. Terminology

## Canonical reference

A typed stable identifier in the form:

    scene:<sid>
    characters:<id>
    pcs:<id>
    location:<id>
    thread:<plot-id>
    commitment:<commitment-id>
    event:<event-id>
    fact:<fact-id>
    group:<id>
    idea:<scene-idea-id>
    birthday:<actor-ref>
    holiday:<provider-specific-key>

Existing actor tokens in some stores use characters/<id> or pcs/<id>. The new continuity layer has one internal colon form. Conversion happens at boundaries; do not rewrite existing files merely for notation.

## Canonical record

The record chosen as authoritative among reviewed duplicate records.

## Alias

A durable mapping from one same-type ref to another same-type canonical ref.

Aliases are acyclic and transitively resolved.

## Link

A durable reviewed semantic relationship between two non-aliased refs.

## Candidate

A derived finding worth review: possible duplicate, possible closure, possible relation, or deadline/event resolution candidate.

## Driver

A current reason a future scene may be worth playing. Drivers include live plot threads, unresolved commitments, upcoming events, birthdays, holidays, and reviewed future-facing links.

## Pressure

Derived urgency/time relevance attached to a driver.

## Time anchor

A dated or date-relative campaign object used to constrain a suggestion. Examples: a scheduled event, birthday, holiday, or parseable commitment deadline.

---

# 5. New durable campaign metadata: continuity.json

Add one campaign-owned file:

    <campaign>/continuity.json

It holds only reviewed semantics and durable reader decisions, never raw embeddings or generated candidate rankings.

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
          "created": "<iso>"
        }
      }
    }

Use normal atomic store writes and campaign_lock.

Wrong top-level shape on read degrades to an empty continuity overlay. A mutator must refuse to overwrite malformed existing content, following the reader/writer split used by events.py and scene_ideas.py.

## 5.1 Alias rules

Only these alias pairs are valid:

- thread -> thread;
- commitment -> commitment.

No scene, event, actor, fact, idea, or location aliasing in this milestone.

The alias graph must be acyclic.

Creating A -> B must reject if resolving B eventually reaches A.

Creating an alias whose source already resolves elsewhere is an edit, not a silent replacement. Surface the existing mapping in the review.

Creating an alias to itself is a no-op/refusal, not stored.

## 5.2 Canonicalization

Provide one shared function:

    canonical_ref(cid, ref) -> ref

and a batch form:

    canonical_refs(cid, refs) -> dict[ref, canonical_ref]

Resolve transitively with a cycle guard even though writes reject cycles. Hand-edited files exist.

If continuity.json is unreadable, return the input ref.

## 5.3 Link vocabulary

Version 1 allowed relations:

Same-type thread/thread:

- continues
- subthread_of
- related_to

Cross thread/commitment:

- pays_off
- related_to

Commitment/event, thread/event, or idea/event temporal links:

- before
- on
- after
- by

General reviewed relevance:

- related_to

Do not add same_as. Identity is represented by aliasing.

Relations are directional except related_to, which is canonicalized as an unordered pair for deduplication.

A future mechanics phase may add mechanics-specific relations without changing existing records.

## 5.4 Link identity

A link id is derived deterministically from canonicalized endpoints plus relation, or allocated from a stable slug/hash if that produces safer filenames/JSON keys.

The same semantic link may not be stored twice in opposite endpoint order when the relation is symmetric.

## 5.5 Suppressions

When the reader dismisses a candidate as not useful, store a fingerprint so deterministic rescans do not immediately recreate it.

Fingerprints must include enough source identity to become invalid when the underlying meaning changes.

For a pair candidate, hash:

- candidate kind;
- canonical refs;
- the identity-text content hashes for both records;
- relevant structural link/anchor ids.

For a closure candidate, hash:

- candidate kind;
- canonical ref;
- current last_scene;
- current latest-beat content hash;
- current status.

If the record later advances, the fingerprint changes and the candidate may legitimately return.

Do not suppress by cosine score or embedding model; those are discovery details.

---

# 6. Derived candidate cache: continuity_candidates.json

Add a campaign-local derived file:

    <campaign>/continuity_candidates.json

This file may be deleted and reconstructed. It exists so:

- Todo can be cheap;
- the Ledger can show pending review without running embeddings;
- opening the graph never launches a model;
- a reconciliation result survives app restarts until reviewed.

Shape:

    {
      "version": 1,
      "generated": "<iso>",
      "basis": {
        "campaign_revision": "<stable digest or relevant store stamp>",
        "embedding_space": "<space or empty>",
        "embedding_model": "<model or empty>"
      },
      "records": {
        "<candidate-id>": {
          "kind": "possible_duplicate",
          "refs": ["thread:a", "thread:b"],
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
            "canonical": "thread:a",
            "relation": "",
            "status": "",
            "reason": "...",
            "evidence_scenes": ["012--..."]
          },
          "created": "<iso>"
        }
      }
    }

The candidate cache is not itself journalled. Applying a reviewed operation writes the durable source store and/or continuity.json, which is journalled.

If the candidate file is malformed, ignore it. Reconciliation can rebuild it.

## 6.1 Candidate kinds

Required:

- possible_duplicate
- possible_relation
- possible_thread_closure
- possible_commitment_resolution

Optional if naturally convenient during implementation, but not required for completion:

- stale_driver
- passed_event_followup

Do not expand candidate kinds casually. Every kind creates Todo/UI/review behavior.

---

# 7. Canonical projections over existing ledgers

Do not teach every consumer how aliases work.

Create a new package under backend/src/grimoire/store/continuity/ or an equivalently cohesive package. It should own canonical projections.

Required public projections:

    threads(cid, include_closed=False) -> list[dict]
    commitments(cid, include_resolved=False) -> list[dict]
    links(cid) -> list[dict]
    candidate_records(cid) -> list[dict]
    driver_snapshot(cid, ...) -> dict
    graph(cid, options...) -> dict

## 7.1 Effective merged records

When multiple physical records alias to one canonical ref:

- expose one effective record;
- canonical record title/kind/due/status are authoritative;
- union beats from canonical and aliases;
- de-duplicate exact duplicate beats by (scene, text);
- order beats by scene play order when scene ids are comparable, then by original stable input order;
- effective last_scene is the newest surviving beat scene, falling back to the canonical physical last_scene if no beat exists;
- latest_beat comes from that effective beat list;
- include a read-only aliases field naming hidden physical refs for Ledger/Graph detail.

No physical file is rewritten to produce this view.

If an alias points to a missing physical canonical record, do not hide the source. Degrade by treating the source as its own record and surface the dangling alias in Continuity review diagnostics.

## 7.2 Existing consumers that must switch to canonical projections

At minimum audit and update:

- scene suggestion snapshot;
- normal scene context blocks for open plot threads and commitments;
- pre-scene briefing;
- Ledger active rows;
- aging/Todo continuity classifications;
- graph;
- any shell badge whose count means “open commitments” or similar.

Historical all-record views may still expose physical records with alias metadata so provenance is inspectable.

The implementation plan must search the repository for direct plot.open_threads and commitments.open_commitments consumers and classify each as physical-history or effective-current-state. Do not guess.

---

# 8. Shared involvement projection

briefing.py already contains valuable logic for determining which actors a plot/commitment record has touched by joining beat scenes against chronicle cast plus appearances.

Factor that logic into a shared lower-level continuity helper rather than copying it.

Required projection:

    involvement(cid, refs) -> {
      "<driver-ref>": {
        "actors": ["characters:...", "pcs:..."],
        "scenes": ["..."]
      }
    }

Rules:

- use both chronicle cast snapshots and appearances history as briefing.py currently does;
- union, never choose one as authoritative;
- tolerate malformed records piecewise;
- canonicalize aliased thread/commitment refs before aggregating;
- no LLM call.

briefing.py should consume the shared helper after extraction, preserving its current semantics and tests.

---

# 9. Identity text and similarity

## 9.1 Canonical identity text

Add pure functions whose output is stable and intentionally separate from UI/prompt rendering:

    thread_identity_text(effective_thread) -> str
    commitment_identity_text(effective_commitment) -> str

Thread text contains, in this order:

1. record type label;
2. title;
3. latest beat;
4. up to two previous non-duplicate beats, newest first.

Commitment text contains:

1. kind plus record type label;
2. title;
3. latest beat;
4. due prose when present;
5. up to two previous beats.

Do not include ids. IDs contain lexical accidents and should not influence semantics.

Clip using the existing embedding text utilities/bounds, not a new unbounded path.

## 9.2 Deterministic lexical signals

Implement before embeddings.

Required signals:

- casefolded normalized-title equality;
- slug equality;
- token Jaccard over identity text after simple punctuation/whitespace normalization;
- character 3-gram or 4-gram similarity, or stdlib SequenceMatcher, if it can be kept deterministic and dependency-free;
- shared actor count/list;
- shared touched-scene count/list;
- shared reviewed event/time-anchor refs.

No new compiled dependency.

Lexical scoring is for candidate ranking only, not automatic merge.

## 9.3 Embedding reuse

Reuse the existing embedding infrastructure:

- embeddings.py client;
- store/vectors.py unit-vector content-hash cache;
- store/embed_space.py connection/model namespace.

Do **not** use semantic_recall_depth as an enable/disable switch.

Continuity semantic matching is available when:

- embeddings_connection_id resolves to a usable OpenAI-compatible connection; and
- embeddings_model is non-empty.

The semantic recall threshold is not the continuity duplicate threshold.

Continuity candidate generation gets its own internal candidate floor/top-k constants, with conservative defaults documented as candidate-generation parameters rather than truth thresholds.

Initial recommended behavior:

- retrieve at most the nearest 3 same-type neighbors for each proposed-new record;
- use a loose candidate floor;
- still include structurally strong neighbors that fall below the cosine floor;
- never expose a pair solely because it is one of top-k if every signal is weak.

Exact numerical thresholds belong in the implementation plan after test fixtures are constructed. Do not justify them from private campaign measurements in committed docs.

## 9.4 Batch embedding

For a reconciliation scan:

- load cached vectors for all identity texts in one space;
- embed misses in bounded batches using the existing client limits;
- never issue one HTTP request per record;
- vector dimension mismatch evicts/rebuilds as semantic recall already does;
- embedding failure leaves deterministic candidates intact.

Opening Todo/Graph/Ledger never calls this path.

---

# 10. Hardening absorb: new-record identity resolution

The first defense against duplicate ledgers is to avoid opening a duplicate record.

## 10.1 Prompt contract change

The absorb prompt must continue to show current open threads/commitments with ids.

Strengthen the instruction:

For every plot or commitment movement, the model must explicitly choose one of:

- move an existing id;
- resolve/close an existing id;
- open a new record.

A new record is only correct when no shown existing record represents the same narrative obligation/question.

For proposed-new records, parse optional fields:

    why_new
    distinguished_from

distinguished_from is a list of existing ids the model considered close but distinct.

These fields are decision aids and review evidence. They need not be stored in plot.json or commitments.json.

## 10.2 Do not trust first-pass “new”

After parse_output, collect proposed-new plot and commitment rows before materialization allocates final ids.

For each proposed-new row:

1. build its temporary identity text;
2. generate same-type nearest candidates using exact/lexical/structural signals;
3. enrich with embeddings if configured;
4. if no candidate is plausibly related, keep it new;
5. otherwise send all ambiguous new rows in **one batched identity-resolver LLM call**, not one call per row.

The identity resolver receives only:

- proposed row;
- its transcript citation/review evidence;
- up to a bounded number of same-type neighbor records with ids and short beat histories;
- deterministic similarity signals.

It does not receive the entire campaign.

Allowed decisions:

    existing
    new
    uncertain

existing must name one supplied candidate id.

new may include a short reason.

uncertain remains a new staged row but must be low-confidence/unselected by default in review, with a visible “possible existing record” hint.

The identity resolver is advisory; final staged edits still go through the normal review.

## 10.3 Failure behavior

If embeddings fail: use lexical/structural neighbors.

If the identity LLM call fails: keep first-pass extraction, but attach candidate-neighbor hints to review so the user can redirect manually.

If no model connection is configured: normal absorb already has larger limitations; do not add a new special failure.

## 10.4 Cost/budget integration

This extra identity call belongs inside the existing absorb budget/fan-out accounting.

It should run only when there is at least one proposed-new plot/commitment row with a plausible candidate.

Do not pay for it on every scene.

Usage must be recorded under a distinct task name if the usage ledger architecture requires task-level attribution; do not hide it inside “absorb” if other secondary calls are separately routed.

---

# 11. Reconciliation sweep after history is written

Scene-local extraction cannot reliably clean every historical issue. Add a separate campaign reconciliation computation.

## 11.1 Trigger model

After a successful End Scene apply:

- run deterministic candidate discovery cheaply;
- if it produces no candidates, stop;
- if it produces candidates that require semantic adjudication and an appropriate LLM connection is available, run one bounded reconciliation call;
- persist resulting pending candidates to continuity_candidates.json;
- do not block the scene from being considered wrapped if this secondary enhancement fails.

Also expose an explicit “Refresh continuity review” action on the Ledger/Continuity surface.

The explicit refresh may do the same embedding/LLM work and replaces obsolete pending candidates while respecting suppressions.

## 11.2 Reconciliation input

Bounded campaign context:

- canonical active threads;
- canonical unresolved commitments;
- recently closed/resolved same-type records needed for identity comparison;
- recent chronicle one-lines;
- candidate-specific relevant beats;
- involvement actors;
- reviewed links/time anchors;
- current date and deterministic pressure classification;
- candidate similarity signals.

Do not send every scene transcript.

When a candidate claims a specific scene proves closure/resolution, fetch only the short chronicle/beat evidence necessary for adjudication. Full transcripts are not the default reconciliation payload.

## 11.3 LLM decision vocabulary

For same-type pair candidates:

- duplicate
- continuation
- subthread
- related
- distinct

For thread lifecycle candidates:

- close
- keep_open
- uncertain

For commitment lifecycle candidates:

- fulfilled
- broken
- expired
- keep_open
- uncertain

The model may not invent a new status.

For temporal interpretation:

- before
- on
- after
- by
- unrelated

## 11.4 Positive-evidence requirement

close/fulfilled/broken/expired requires:

- evidence_scenes containing known scene ids; and
- reason grounded in existing beat/chronicle content.

Parser drops unknown scene ids.

If no known scene supports the operation, downgrade to uncertain; never materialize a lifecycle proposal with fabricated evidence.

## 11.5 Candidate proposal vs application

The LLM result updates continuity_candidates.json only.

Nothing in the reconciliation computation changes plot.json, commitments.json, continuity.json, events.json, or scene ideas.

The reader reviews each proposed structural operation.

---

# 12. Continuity review UI

Extend the campaign Ledger page or add a Continuity section consistent with the page-shell/list-detail conventions. Do not build a second app-navigation rail.

Required groups:

- Possible overlaps
- Possible closures
- Possible commitment resolutions
- Reviewed links / merges
- Dismissed findings (collapsed)

Each candidate detail shows:

- affected records;
- current statuses;
- latest beats;
- relevant dates/pressure;
- structural signals such as shared actors/scenes;
- semantic similarity when available, labelled as a discovery signal rather than confidence;
- LLM proposal/reason if present;
- evidence scenes with navigation links.

## 12.1 Duplicate review actions

For duplicate candidate:

- choose canonical A;
- choose canonical B;
- mark continuation;
- mark related;
- dismiss.

Applying duplicate creates an alias only.

If candidate refs have changed/canonicalized since generation, reject stale review with 409 and refresh.

## 12.2 Closure review actions

For thread:

- Close thread
- Keep open
- Dismiss finding

Closing uses the existing plot mutator and journal/undo system. It may append an optional beat only if the reader edits/provides one; the candidate reason itself is not automatically a story beat.

For commitment:

- Fulfilled
- Broken
- Expired
- Keep open
- Dismiss finding

Use commitments.set_movement under journalled undo.

## 12.3 Link review actions

For continuation/subthread/pays_off/related/time relations:

- Accept link
- Change relation among allowed compatible relations
- Dismiss

Accepted link writes continuity.json and journal history.

## 12.4 Journalling

Add undo targets for continuity alias/link mutations.

A merge alias should be reversible by removing/restoring exactly that alias record, compare-and-swap protected like other manual edits.

Link additions/removals likewise.

Do not journal derived candidate-file updates.

---

# 13. Temporal pressure service

Create one shared read-only projection so Todo, suggestions, graph, and reconciliation agree about time.

Suggested public API:

    pressure.build(cid, now=None, horizon=None) -> dict

Output contains:

    {
      "now": "<native>",
      "friendly": "...",
      "items": [
        {
          "ref": "event:...",
          "kind": "event",
          "label": "...",
          "native": "...",
          "friendly": "...",
          "in_days": 3,
          "relation": "on",
          "state": "upcoming"
        }
      ]
    }

## 13.1 Sources

Include:

- scheduled events: today and upcoming;
- holidays: today and all holidays inside the bounded horizon, not merely the nearest one;
- birthdays: all upcoming birthday/month signals birthdays.py can honestly derive;
- parseable open commitment due dates via existing aging arithmetic;
- reviewed temporal links in continuity.json;
- overdue commitments;
- passed-but-unresolved event-linked obligations.

## 13.2 Horizons

Use two concepts:

- **warning horizon**: campaign calendar/config warn_days for urgency/Todo;
- **suggestion horizon**: a broader bounded upcoming window suitable for choosing future scenes, reusing the calendar package's established upcoming window where reasonable.

Do not pretend a month-only birthday has an exact day.

Do not turn free-text commitment due strings into dates unless the calendar provider can parse them.

## 13.3 Structured time links

A free-text commitment due remains untouched.

When reconciliation/user review establishes that a commitment is due before/on/after/by a known event, store that as a continuity link:

    commitment:x --before--> event:y

This avoids rewriting “before the bells stop” into a fabricated native date.

Pressure can use the event's exact date to determine urgency while retaining the prose due text for display.

---

# 14. Scene drivers

A driver is a validated current campaign object that can motivate a next scene.

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
        "state": "ok|stale|upcoming|due_soon|overdue|today",
        "in_days": null
      },
      "time_anchors": ["event:..."],
      "links": [...]
    }

For thread/commitment actors, use shared involvement projection.

Canonicalize aliases before generating drivers.

Resolved commitments and closed threads are not active drivers.

Upcoming temporal objects may be drivers even if not linked to a thread.

Reviewed links allow a driver to expose related obligations without collapsing them.

---

# 15. Scene-suggestion snapshot and prompt changes

suggest.build_snapshot currently includes story-so-far, open threads, cast, locations, current date, one upcoming item, and birthdays.

Change it to consume the shared continuity projections.

Snapshot must include:

- story_so_far unchanged;
- canonical active threads, with ids and dormancy;
- canonical unresolved commitments, with id, kind, due, latest beat, and aging/pressure;
- timeline items from pressure service;
- driver index;
- cast unchanged except any needed actor-ref normalization;
- locations unchanged;
- reviewed relevant links among active drivers.

Remove the lossy single “upcoming” representation once all prompt consumers have moved to the timeline list.

## 15.1 Prompt instruction

Default batch should be diverse across narrative purposes.

Without explicit focus:

- at least one suggestion should address high/immediate pressure if any exists;
- cold/stale threads are worth reviving but not mandatory;
- near-future temporal anchors are worth using;
- not every suggestion should service the same driver set;
- a quiet character/relationship scene is allowed when no urgent business dominates.

Do not hard-code exactly one category per card. Diversity is the objective, not a four-slot template.

## 15.2 Suggestion output schema

Extend each suggestion:

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

Thread:

- advance
- close_candidate

Commitment:

- address
- fulfill_candidate
- break_candidate

Temporal:

- anchor

The suggestion does not itself mutate these records. “candidate” means the proposed scene is plausibly about that outcome, not that playing it guarantees resolution.

Parser validates every ref against the snapshot's driver index and drops unknown refs.

time_anchor must name a supplied temporal driver.

## 15.3 Date derivation

When a time anchor deterministically identifies the scene day:

- derive the date in code.

Examples:

- relation on event -> event date;
- on birthday -> birthday date when exact;
- on holiday -> holiday date.

For before/after, the relation defines a range rather than a unique day; the model may return a proposed date but it must validate against the calendar and relation where that relation is mathematically checkable.

An invalid anchored date is dropped, not silently accepted.

Unanchored suggestions may still propose dates as today.

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
Do not intentionally advance/address this driver.

**must_include**  
Every generated suggestion must service this driver unless doing so is structurally impossible; if the model fails, parser/UI should make the miss visible rather than pretending compliance.

## 16.2 Time control

Add:

- Stay near current date
- Let time move
- Choose anchor…

Choose anchor lists bounded upcoming temporal drivers with friendly date/in-days.

A selected anchor is a hard batch constraint.

## 16.3 API request

sceneSuggestions gains a body or structured query params carrying:

    direction
    rank
    focus_refs
    avoid_refs
    must_refs
    time_anchor_ref
    time_anchor_relation
    time_mode

Validate every ref server-side.

Do not trust the frontend to enforce no overlap among focus/avoid/must. Define precedence:

    must_include > avoid > focus > normal

A ref in must and avoid is rejected as 400 rather than silently choosing.

## 16.4 Cards show why

Generated card renders validated provenance:

- date/time anchor;
- driver chips;
- action labels where useful.

Example using placeholders:

    Midnight at Saltmarch
    Tomorrow · before The Tribunal
    Advances: The missing map
    Addresses: Mara's promise

These labels are derived from validated ids, not model-written explanatory prose.

---

# 17. Saved scene ideas become driver-aware

Extend scene idea storage with optional:

    drivers: [{"ref": "...", "action": "..."}]
    time_anchor: {"ref": "...", "relation": "..."}

Existing files without them remain valid.

Write/read validation canonicalizes aliases and drops invalid refs.

## 17.1 Derived staleness

Do not add a stored “stale” status.

On read derive:

    stale_reason: "" | "<human-readable reason>"

Reasons include:

- every referenced thread is closed;
- every referenced commitment is resolved;
- anchored event is already past/fired and the premise was explicitly before it;
- all driver refs became invalid.

An active idea with stale_reason remains recoverable and visible under a “Stale” subgroup.

User may still open/adapt it.

Dismissed and used semantics remain unchanged.

---

# 18. Todo integration

## 18.1 Embedding setup chore

When no usable embeddings connection/model is configured, Todo shows one library-scoped ignorable note:

**Semantic matching is not configured**

Why text must say:

- Grimoire still uses basic lexical/structural matching;
- semantic matching improves detection when the same story business is phrased differently.

Fix goes to the embeddings configuration surface.

The chore checks embeddings_connection_id plus embeddings_model, not semantic_recall_depth.

If a connection/model is configured but semantic recall depth is zero, do not show this chore.

A transient endpoint outage is not equivalent to “not configured” and does not create this setup chore.

## 18.2 Continuity-review chores

Campaign Todo uses the cached candidate file only; no generation.

At minimum:

- N possible overlaps to review;
- N possible closures/resolutions to review.

Avoid one chore per candidate.

Expansion returns concrete candidate labels/details and links to Continuity review.

## 18.3 Timeline chores

Reuse pressure service for existing/future warnings rather than reimplementing date math.

Possible future chores can use the same service, but do not expand scope beyond current Todo requirements unless the implementation plan explicitly includes them.

---

# 19. Story Graph

Add a campaign-level Story Graph page.

This is a read-only projection plus navigation/filter controls. Opening it makes no model or embedding calls.

## 19.1 Core node types

Required by completion:

- scene;
- character/PC;
- location;
- thread;
- commitment;
- event;
- saved scene idea.

Timeline markers:

- birthday;
- holiday.

Optional, behind filters if implemented in this milestone:

- standing facts;
- groups;
- relationships as first-class nodes.

Relationships are usually better represented as actor-actor edges.

Do not dump all lore/items/creatures by default.

## 19.2 Required edge types

Deterministic:

- actor appeared_in scene;
- scene occurred_at location;
- thread opened_in / advanced_in / closed_in scene from beats/status history where knowable;
- commitment created_in / touched_in / resolved_in scene;
- scene idea serves driver;
- event occurs_on timeline point.

Reviewed continuity links:

- continues;
- subthread_of;
- pays_off;
- related_to;
- before/on/after/by.

Alias provenance:

- merged_into, shown only when “Show merged” is enabled.

Possible candidates may render as dashed suggestion edges when “Show review candidates” is enabled.

Never render an embedding score as an asserted story relation.

## 19.3 Layout

Default Story lens is timeline-oriented, not a force-directed hairball.

- scenes form the primary horizontal spine in play order;
- a visible Now boundary separates played history from future-facing drivers/ideas;
- thread and commitment nodes/arcs sit in lanes connected to the scenes that moved them;
- actors/locations may appear as secondary nodes or be toggled to reduce density;
- future events/holidays/birthdays and saved ideas sit right of Now.

The implementation may use SVG/canvas/frontend graph helpers, but the layout must remain useful on Android/narrow screens:

- tap selects;
- no hover-only information;
- local horizontal scrolling is acceptable;
- filters reduce density;
- selected-node detail is available outside the drawing.

## 19.4 Lenses/filters

Required filters:

- Story: scenes + threads + commitments + events;
- Cast: actors + scenes + relationships/involvement;
- Timeline: scenes + dated events/holidays/birthdays/deadlines;
- Continuity: aliases, reviewed links, pending candidates.

They may be implemented as presets over one graph payload.

## 19.5 Node detail

Selecting a thread/commitment shows:

- canonical title;
- physical aliases;
- current status;
- latest beat;
- touched scenes;
- involved actors;
- pressure;
- reviewed links;
- candidate findings.

Actions where appropriate:

- Focus next scene;
- Open ledger entry;
- Filter to this arc.

Selecting actor shows:

- scenes;
- related active drivers;
- relationships;
- upcoming birthday if recorded.

Selecting scene shows:

- cast;
- location/date;
- plot/commitment movements;
- relevant mechanics may be added in Mechanics II later.

## 19.6 Backend graph API

Add a deterministic endpoint such as:

    GET /campaigns/{cid}/continuity/graph

Response should be normalized nodes/edges, not UI coordinates.

Frontend owns layout.

Bound response size by sensible default filters if necessary, but do not silently omit relevant nodes from an explicitly requested lens. If a cap exists, return truncated=true and counts.

---

# 20. Graph and driver API types

Define typed backend/TS unions rather than open string bags where feasible.

Example conceptual node:

    {
      "id": "thread:missing-map",
      "kind": "thread",
      "label": "The missing map",
      "status": "advanced",
      "date": "",
      "meta": {...}
    }

Edge:

    {
      "id": "...",
      "kind": "advanced_in",
      "from": "thread:missing-map",
      "to": "scene:012--...",
      "reviewed": true
    }

Do not put arbitrary rendered prose into meta if the frontend can derive it from typed fields.

---

# 21. Routes

Exact route names can be adjusted in planning, but the public surface must cover:

Read:

    GET /campaigns/{cid}/continuity
    GET /campaigns/{cid}/continuity/candidates
    GET /campaigns/{cid}/continuity/graph

Compute:

    POST /campaigns/{cid}/continuity/reconcile

Review writes:

    POST /campaigns/{cid}/continuity/aliases
    DELETE /campaigns/{cid}/continuity/aliases/{ref-or-id}
    POST /campaigns/{cid}/continuity/links
    DELETE /campaigns/{cid}/continuity/links/{link-id}
    POST /campaigns/{cid}/continuity/candidates/{candidate-id}/apply
    POST /campaigns/{cid}/continuity/candidates/{candidate-id}/dismiss

Prefer one candidate apply endpoint whose body contains the reviewed operation over many nearly identical routes, if that keeps validation clearer.

All campaign mutations take campaign_lock.

Review apply uses stale guards against the candidate basis/current record state.

---

# 22. Concurrency and stale-review rules

A candidate was computed against specific source records. Applying it later must prove those records still mean what was reviewed.

Candidate records therefore carry fingerprints/source hashes.

On apply:

- resolve current aliases;
- reload source records;
- recompute relevant fingerprint;
- if it differs, return 409 stale_candidate;
- do not partially apply.

An alias/link mutation and any underlying plot/commitment status write for one reviewed action occur under one campaign lock.

If applying a commitment resolution plus continuity link, either both land or the action reports which durable part landed only if the existing store/journal architecture cannot provide transactionality. Prefer designing the operation as one lock-held sequence with journal rows that make the result reconstructable.

No multi-campaign locks are introduced.

---

# 23. Prompt templates

All prompt text remains under templates/.

New suggested template families:

    templates/continuity_identity/
    templates/continuity_reconcile/

Reuse snippets for thread/commitment lines where semantics match. Do not copy old line formatting into new templates if one shared snippet can serve both without obscuring purpose.

Register every template in scripts/verify_templates.py.

Prompt contracts must be explicit JSON with fixed enums. Parser rebuilds known fields; it never passes arbitrary model JSON downstream.

---

# 24. Parsing and validation

Every model-returned field follows current tolerant-parser discipline:

- non-object reply -> empty/no proposals;
- unknown ids -> dropped;
- unknown enum -> uncertain/no-op;
- malformed list element -> skipped;
- unknown evidence scene -> dropped;
- missing required evidence for destructive lifecycle proposal -> downgrade/drop;
- no exception solely because the model produced bad JSON.

Store/calendar errors beneath the parser retain their existing failure semantics; do not broadly catch programmer/store bugs as “bad model output.”

---

# 25. Performance and cost

## 25.1 No N+1 LLM reconciliation

One identity-resolver call per absorb at most, and only when ambiguous proposed-new records exist.

One reconciliation call per refresh/end-scene at most, containing a bounded set of candidates.

Do not call an LLM for every pair.

## 25.2 Candidate generation complexity

Ledger sizes are expected to remain small enough for a pure-Python pair scan, but use embeddings as nearest-neighbor candidate generation rather than serial pairwise LLM comparison.

For a full sweep:

- compute identity texts;
- load cached vectors once;
- embed bounded misses in batches;
- calculate dot products in memory;
- keep top-k/above-floor candidate pairs;
- dedupe unordered pair ids.

No vector DB.

## 25.3 Graph read cost

Graph endpoint may walk scenes/chronicle/ledgers once per request.

Avoid per-node full character reads where roster/overlay summary APIs already provide names.

No image-directory scans merely to render graph nodes.

## 25.4 Candidate cache

Todo and shell badge reads must not perform embedding calls or full transcript reads.

If adding a shell badge for continuity findings, it reads candidate-cache counts only.

---

# 26. Failure/degradation matrix

| Failure | Required behavior |
|---|---|
| No embeddings configured | Lexical/structural matching works; Todo setup note appears |
| Embedding endpoint unavailable | Keep deterministic candidates; log failure; no failed scene/wrap-up |
| Embedding cache corrupt | Existing vectors.py miss/re-embed behavior |
| Identity resolver LLM fails | Stage first-pass rows; show nearest-neighbor hints; no hidden data loss |
| Reconciliation LLM fails | Existing candidate cache remains or deterministic candidates persist; wrap-up succeeds |
| continuity.json malformed | Read enhancements omit; mutators refuse overwrite |
| candidate cache malformed | Treat as empty/rebuildable; no campaign failure |
| dangling alias | Source record remains visible/effective; Continuity review flags it |
| dangling reviewed link | Omit from effective driver graph or mark broken in Continuity detail; no crash |
| calendar unavailable | Temporal arithmetic degrades to undated/free-text; no fabricated dates |
| stale candidate apply | 409; no partial write |
| graph too large | explicit truncation metadata, never silent omission |

---

# 27. Migration and backward compatibility

No mandatory one-time migration.

All new files are absent in old campaigns and read as empty state.

Existing plot/commitment records are untouched until the reader explicitly reviews a status change.

Existing scene ideas without driver metadata remain valid.

Existing scene suggestion clients must tolerate new fields; new frontend should tolerate their absence from an older backend only if that compatibility posture already exists in surrounding types.

No background rewrite of old campaigns on app start.

A user may explicitly run continuity reconciliation on an old campaign to populate candidates/links.

---

# 28. Evals and tests

This work changes model contracts and requires both deterministic unit tests and LLM eval coverage.

## 28.1 Store unit tests

Alias:

- thread alias canonicalization;
- commitment alias canonicalization;
- transitive aliases;
- cycle rejection;
- missing target degrades safely;
- canonical effective beat union;
- exact duplicate beat dedup;
- removing alias restores two effective records;
- wrong-type alias rejected.

Links:

- relation compatibility;
- symmetric related_to dedup;
- dangling refs tolerated on read;
- journal/undo add/delete.

Suppressions:

- same unchanged candidate stays suppressed;
- changed latest beat invalidates suppression;
- canonicalized aliases produce stable pair fingerprint.

## 28.2 Similarity tests

Use fake vectors, never real external embedding calls.

- normalized-title exact match;
- semantically different identical-slug safety;
- lexical fallback with embeddings off;
- top-k candidate retrieval;
- dimension mismatch eviction behavior delegated/shared correctly;
- embedding failure preserves lexical candidates;
- cross-type records never enter duplicate candidate set.

## 28.3 Absorb identity tests

- model proposes new with no close candidate -> remains new;
- close lexical candidate -> resolver can map to existing;
- resolver names unknown id -> ignored;
- resolver fails -> staged new row survives with hint;
- one batch call for several ambiguous records;
- existing citation/review fields survive identity resolution;
- resolved commitment is visible as a neighbor for duplicate prevention but is never silently reopened.

## 28.4 Reconciliation tests

- closure requires known evidence scene;
- duplicate proposal does not mutate until apply;
- applying duplicate writes alias only;
- thread/commitment high similarity becomes relation candidate, never merge;
- passed deadline nominates commitment but does not resolve it;
- reviewed temporal link contributes to pressure;
- stale fingerprint returns 409.

## 28.5 Pressure tests

For Gregorian, Hebrew, and a fake CalendarProvider:

- several upcoming holidays are retained, not only nearest;
- events and holidays ordered on fixed-day axis;
- exact birthday date and age;
- yearless birthday no invented age;
- month-only birthday no invented day;
- parseable commitment due_in;
- free-text due remains non-arithmetic;
- reviewed commitment-before-event derives urgency;
- backwards/corrected campaign clock behaves as existing aging/event rules require.

## 28.6 Suggestion tests

- snapshot contains ids for threads and commitments;
- returned drivers validated;
- unknown driver dropped;
- anchor validated;
- focus/avoid/must precedence and conflict 400;
- anchored on-date derived deterministically;
- before/after invalid date dropped;
- card provenance uses resolved labels;
- canonical alias prevents duplicate drivers;
- saved idea stale_reason derives after resolution.

## 28.7 Todo tests

- missing embedding connection/model produces one library chore;
- embeddings configured with semantic recall depth 0 produces no setup chore;
- no embeddings still shows continuity candidate chores;
- Todo performs no embedding/client call;
- pending candidate counts come from cache;
- dismissed/suppressed candidates do not count.

## 28.8 Graph backend tests

- scenes ordered correctly;
- actor appearance edges;
- location edges;
- thread/commitment beat edges;
- future event/idea nodes;
- alias hidden by default and merged_into visible when requested;
- reviewed links solid;
- candidate links marked unreviewed;
- malformed optional file does not fail graph.

## 28.9 Frontend tests

Continuity review:

- candidate opens read-only detail;
- apply requires explicit action;
- canonical side selectable;
- stale 409 prompts refresh;
- dismissed group reversible if design exposes restore.

Scene chooser:

- driver controls alter request;
- cards show validated reasons;
- no embeddings warning does not disable Generate;
- saved stale idea remains selectable/adaptable.

Story Graph:

- filter presets;
- tap node -> detail;
- Now boundary/future nodes;
- narrow-width interaction;
- no hover-only essential information;
- no API generation call on mount/filter.

## 28.10 LLM eval cases

Add synthetic cases using established placeholder names.

At minimum:

1. Same obligation, different wording -> identity resolver selects existing.
2. Same topic, distinct plot questions -> distinct.
3. Broad thread and concrete continuation -> continuation, not duplicate.
4. Thread and commitment about same incident -> pays_off/related, never duplicate.
5. Thread whose accumulated beats clearly answer its question -> close.
6. Old but unresolved thread -> keep_open.
7. Deadline passed and promise demonstrably kept -> fulfilled.
8. Deadline passed but transcript does not establish outcome -> keep_open/uncertain.
9. Two focused drivers with several valid alternative scenes -> suggestions distribute coverage rather than cloning one premise.
10. Anchor date with custom calendar notation -> parser/date derivation remains valid.

---

# 29. Observability

Record model usage under distinct task labels for:

- continuity_identity;
- continuity_reconcile;
- existing scene_suggestions remains its current task.

Structured logs should include:

- campaign id;
- candidate count;
- deterministic vs semantic candidate counts;
- embedding mode configured/off/failure;
- resolver decision counts;
- no private narrative text.

Do not log full identity texts, beats, or prompt content into generic logs beyond the prompt logging facilities that already intentionally capture model prompts.

---

# 30. Documentation/UI wording

User-facing language:

Prefer:

- “Possible overlap”
- “May be finished”
- “Needs resolution review”
- “Basic matching active”
- “Semantic matching not configured”
- “Merged into”
- “Continuation of”

Avoid:

- “AI detected duplicate” as a certainty;
- “Continuity score”;
- “Broken campaign”;
- “Embedding required.”

README may gain a concise mention of reconciliation and Story Graph after implementation, but implementation details belong in docs.

---

# 31. Suggested implementation slices

This is a design spec, not the required implementation plan. The later plan may rearrange tasks, but dependencies strongly suggest:

### Slice A — canonical continuity substrate

- continuity.json reader/writer;
- aliases, links, suppressions;
- canonical thread/commitment projections;
- shared involvement helper;
- journal/undo.

### Slice B — temporal pressure and drivers

- pressure service;
- multi-upcoming holidays/events/birthdays/deadlines;
- driver projection;
- canonical context/briefing readers.

### Slice C — similarity and absorb identity hardening

- identity text;
- lexical signals;
- embedding reuse;
- batched identity resolver;
- absorb materialization integration.

### Slice D — reconciliation candidates/review

- candidate cache;
- deterministic discovery;
- reconciliation prompt/parser;
- review UI;
- stale guards;
- Todo candidate chores.

### Slice E — scene suggestion control

- snapshot changes;
- driver-aware schema;
- structured focus/avoid/must/time controls;
- card provenance;
- saved idea driver metadata/staleness.

### Slice F — Story Graph

- graph projection endpoint;
- Story/Cast/Timeline/Continuity lenses;
- node detail/navigation.

### Slice G — polish/evals/docs

- eval suite;
- observability;
- performance audit;
- make check;
- final adversarial implementation-vs-spec review.

Do not start Slice F first because it is visually attractive. The graph must expose the real canonical continuity substrate, not become a parallel model.

---

# 32. Acceptance criteria

The capstone is complete only when all of these are true:

1. A new extracted thread/commitment is compared against plausible same-type existing records before being treated as genuinely new.

2. Matching works without embeddings and improves with configured embeddings.

3. Todo tells the user when semantic matching is not configured while explicitly stating basic matching still works.

4. A separate reconciliation system can propose duplicate, continuation/relation, thread closure, and commitment resolution findings without silently applying them.

5. Reviewed duplicate handling is non-destructive and reversible.

6. Canonical current-state prompts do not show reviewed aliases as independent live threads/commitments.

7. Commitments are first-class inputs to scene suggestions.

8. Suggestions see a bounded list of upcoming events/holidays/birthdays/deadlines rather than only the single nearest item.

9. Date arithmetic is provider-driven and deterministic.

10. The reader can focus, avoid, and require specific story drivers and choose meaningful time anchors.

11. Suggestion cards show validated reasons/drivers.

12. Saved scene ideas retain driver/time provenance and can become visibly stale without being destroyed.

13. Todo surfaces pending continuity review without model calls.

14. The Story Graph shows the campaign's played history and future-facing obligations from the same canonical data used by suggestions.

15. All new semantic writes are reviewable and journalled/undoable.

16. No read-only navigation path launches an embedding or LLM request.

17. Existing campaigns require no migration to open and play.

18. make check passes, relevant frontend tests/typecheck pass, template verification passes, and the final implementation receives both required Codex reviews.

---

# 33. Explicit stopping rule

After this specification is implemented and accepted, **do not continue expanding continuity because another attractive narrative-analysis idea appears**.

Park, unless a correctness defect is found:

- act/era detection;
- theme clustering;
- automatic literary summaries;
- more graph node families;
- richer relationship inference;
- narrative centrality scoring;
- automatic chapter organization;
- mechanics-aware drivers beyond the extension seams already provided.

The purpose of this capstone is to make long-term persistent history trustworthy and controllable enough that Grimoire can move its main development focus to game mechanics.
