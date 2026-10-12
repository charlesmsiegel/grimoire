# 11-S1: The knowledge store — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Review gates: substitute review (no Codex CLI in this environment); the Codex
gates are owed, and `@codex review` on the PR stands in for them (CLAUDE.md).**

**Goal:** `store/knowledge.py` owns `<campaign>/knowledge.json`: the file
format, the fingerprints, the precedence of §7.2 as a pure function, group
expansion through 07's `affiliated`, the lifecycle hooks of §7.5 and the
writer guard. No routes, no UI, and nothing reads the file on a prompt path,
so no prompt changes. Delivers 11-C3 (part).

**Spec:** `2026-10-09-roadmap-11-epistemic-history-retrieval-design.md`
§7.1-7.5, §10, §11 ("Overrides"), Slices (11-S1).

## Re-checked against main

- 07-C1/C2 have landed: `membership.campaign_index(cid)` is the supported
  door and `membership.affiliated(index, group)` is leader-first (07 §12.4).
  So the soft 07-S2 need is met and group expansion is the spec's full form.
- 08 has not landed, so nothing yet produces `EvidencePost.key`. This slice
  defines the key spelling 08 §10.1 step 5 names (`r-<response_id>`, else
  `p-<post_id>`, else `""`, with the reply's `response_part` beside it) in
  one helper, `knowledge.post_key`, which 08 may adopt.
- Variant ids survive a branch (`responses._cloned_record` deep-copies the
  record and changes only its id), so a copied post entry keeps its variant.
- `overlay.delete_actor` and `overlay.delete_entity` are not journalled
  today. The spec's "journalled with the delete" is met by journalling each
  pruned entry as a `manual` row **with no reversal** (`undo: None`, a
  `why`): restoring a statement about an actor whose slug may since have
  been reissued would grant a stranger knowledge, which is the leak the
  prune exists to stop.

## Design

**Import shape.** `knowledge` must be importable from `overlay` (the delete
hooks) and from `undo`, so it may not import `overlay` or `membership`
(which imports `overlay`). It imports `atomic`, `journal`, `locks`,
`responses` (for `transcript_hash`, the one definition of a scene's hash),
`campaigns.paths`, `paths` and `scenes.read`/`scenes.identity`. Group
expansion therefore takes rosters as an argument (`audiences(...)` reads any
object with `ref`, `name`, `leader`, `members` — a `membership.Roster`), and
the caller reads `membership.campaign_index(cid)`.

**File.** `{"version": 1, "entries": {"k-<8 hex>": entry}}`, entry as §7.1
plus `part` on each post and an optional `updated`. Written whole through
`atomic.write_text` under `locks.campaign_lock(cid)`.

**Validation** (`normalize(entry) -> dict`, raises `KnowledgeError`, a
`ValueError`):
- `audience`: `characters:<id>`, `groups:<id>` (`paths.safe_id`), or `"*"`;
  `audience_name` a string, forced `""` for `"*"`;
- `status` in `knows | suspects | unaware`; `source` in `manual | accepted`
  (default `manual`); `note` a string; `created` a string (set on `add`);
- `subject.kind == "posts"`: `scene` a 32-hex identity, `posts` a non-empty
  list of `{key, part, variant, digest}` with `key` `r-`/`p-` prefixed,
  `digest` 16 hex, no repeated `(key, part)`;
- `subject.kind == "scene"`: `scene` identity and `digest` 64 hex.

**Reads.**
- `read(cid) -> dict[str, dict]`: tolerant. Missing, unparseable or
  wrong-shaped file reads `{}`; an entry that does not normalize is skipped.
- `read_checked(cid) -> (entries, intact)`: the same, plus whether nothing
  was lost. S2's failure table needs it: a skipped entry might have been an
  `unaware`, so a lossy read must not let derived rules widen.
- `get(cid, kid)`.

**Mutators** (each under `campaign_lock`, each reading the document
strictly — a file that will not parse or is the wrong shape raises
`KnowledgeError` rather than publishing an empty ledger over it, the
`facts._read_ledger` rule; entries it does not touch are kept verbatim, so a
malformed one a person can still repair is never dropped by an unrelated
write):
`add(cid, entry) -> kid`, `update(cid, kid, entry) -> dict` (keeps
`created`, stamps `updated`; `KeyError` for a missing id), `remove(cid, kid)
-> dict | None`, `restore(cid, kid, value)` (None removes),
`forget_audience(cid, ref) -> dict` (journals each pruned entry, best-effort,
with no reversal), `copy_for_branch(cid, src, dst, rid_map, copied)`,
`drop_scene(cid, identity) -> int`.

**Fingerprints** (pure):
- `post_key(m)`, `post_part(m)`, `digest(text)` (sha256 of the raw text, 16
  hex);
- `SceneFacts(posts: {(key, part): (variant, digest, excluded)}, digest)`
  built by `scene_facts(messages, variants)`; `current(cid, sid)` reads one
  scene and its variants for a caller that has neither (S4's routes);
- `fingerprint(m, variants) -> dict` (a subject post, as a route stores it);
- `holds(entry, facts)`: a posts subject holds while every post is present,
  not hidden, on the same variant and the same digest; a scene subject while
  the transcript hash is unchanged. `facts=None` (unreadable scene) holds
  nothing.

So a swipe and Keep writing (new active variant), an edit (new digest) and a
hide (`excluded`) each stop a post `knows`, and an edit or a hide (the hash
covers `excluded`) stops a scene `knows`. `unaware` never consults `holds`.

**Precedence** (`decide(entries, *, actor, scene, post, author, facts,
audiences) -> Match | None`, pure):
1. candidates: `subject.scene == scene`; for a post part, a posts subject
   listing `post` (rank 0) or a scene subject (rank 1); for the summary part
   (`post=None`) or a keyless post, scene subjects only;
2. audience: the actor's own ref (0) — and for `knows`/`suspects` only while
   `audiences.names[actor] == audience_name`; a group (1) whose current
   affiliation holds the actor — for `knows`/`suspects` only while the
   group's current name equals `audience_name`, and a group that no longer
   resolves expands to nobody; `"*"` (2) unless the actor is `author`;
3. `knows`/`suspects` only while `holds`;
4. best by (subject rank, audience rank), then newest `created`, then
   `unaware` < `suspects` < `knows`.
`Match(kid, status, basis)` with basis `override`, `override_group` or
`override_all` (§3.5). `audiences(names, rosters)` builds the expansion with
`affiliated`'s order (leader first, members once).

**Hooks.**
- `overlay.delete_actor`: `knowledge.forget_audience(cid, f"characters:{aid}")`
  for a character, under its hold.
- `overlay.delete_entity`: the same for `groups:<eid>`.
- `overlay._forget_in_campaign` (the world-level fan-out §13.5 asks the plan
  to trace): the same for a world `characters` or `groups` delete, in each
  dependent campaign that did not keep its own copy. A campaign that keeps a
  copy keeps its statements, as it keeps the record.
- `branch.branch_scene`: `knowledge.copy_for_branch` beside
  `regex_rewrites.copy_for_branch`, handed the `(key, part)` set of the kept
  posts; `branch.discard`: `knowledge.drop_scene` by the identity it reads.
  Whole-scene entries are not copied (the hash differs by construction).

**Undo.** `store/undo.py` gains `{"w": "knowledge", "id": kid}`:
`read_value` is `knowledge.get`, `write_value` is `knowledge.restore`.

**Repo rules.** `store.knowledge` joins `locks.DOMAIN_MODULES` (with a
comment), the store facade and `store_api_baseline.json`.
`test_knowledge_writer_guard.py` (bindings resolved as
`test_absorb_writer_guard.py` does) fails a call to `add`, `update`,
`remove`, `restore`, `forget_audience`, `copy_for_branch` or `drop_scene`
outside `routes/ledger.py`, `store/knowledge.py`, `store/overlay.py`
(delete hooks), `store/undo.py` (`restore` only) and `store/branch.py`
(`copy_for_branch`, `drop_scene`), and the detector is shown catching a call
planted in `store/absorb/`. CONTRIBUTING.md's guard table gains its row.

## Tasks

1. Tests first, `backend/tests/test_knowledge_store.py`: read tolerance and
   `read_checked`; validation refusals; strict mutators refuse a wrong-shape
   file without writing; add/update/remove/restore/ids; precedence (post
   `unaware` beats group whole-scene `knows`, actor > group > `*`, recency,
   tie-break, `*` skips the author, summary sees scene entries only);
   fingerprints over real scenes (swipe, edit, Keep writing, hide stop a
   `knows`, none stops an `unaware`); group expansion through a real
   `campaign_index` (leader, member, non-member, member added later,
   renamed group, deleted group); actor/group/world delete prune and
   journal; branch copy (re-keyed, whole-scene skipped, partial skipped) and
   discard; undo round trip.
2. `store/knowledge.py`.
3. Hooks in `overlay.py`, `branch.py`, `undo.py`; `locks.py`; facade and
   baseline.
4. `test_knowledge_writer_guard.py`; CONTRIBUTING.md row.
5. `ruff`, mypy/eslint ratchets, the guards and the touched suites; then
   `make check-py`.

## Plan gate (substitute review) and dispositions

An independent reviewer agent read this plan against the spec and `main`
(no Codex CLI here). Findings and what changed:

| Finding | Disposition |
|---|---|
| B1 `knowledge` importing `responses` / the scene reader cycles back to `overlay` | Fixed. `store/knowledge.py` imports only `atomic`, `group_refs`, `journal`, `locks`, `campaigns.paths`, `paths`. Scene reads, `scene_facts`, `current` and `audiences_for` live in a new read-only `store/knowledge_scene.py` that nothing below `overlay` imports |
| B2 an Undo of an earlier hand removal re-grants a pruned statement to a same-named actor that took the slug | Fixed. `forget_audience` first retires every live journal offer whose `restore` names the audience (`journal.retire_offers`, strict like the membership fences), then prunes |
| S3 branch copy drops withholding | Fixed, and spec §7.5 amended: an `unaware` travels narrowed to the kept posts, a whole-scene `unaware` travels as is; grants travel only whole, as specified. Copies get fresh ids |
| S4 malformed `knowledge.json` during a delete | Fixed. `knowledge.require_readable` (409 `GroupIdentityConflict`) preflights `delete_actor` (with `journal.require_readable`, which the offer retirement needs), `delete_entity` for groups, and `world_identity_scope`'s per-campaign loop, before anything is removed |
| S5 writer guard misses `store.knowledge.add` via the facade | Fixed: owner spellings ending `store.knowledge` are calls; planted cases cover it and `store/context/` |
| S6 undo reads through the tolerant `get` | Fixed: `get_strict` |
| S7 `audiences` re-implemented `affiliated` | Fixed: `knowledge_scene.audiences_for` calls `membership.affiliated` |
| S8 empty `audience_name` accepted | Fixed: refused for an actor or group audience; a missing current name never matches a grant |
| M9 a roll continuation moves the active variant | Documented in `knowledge_scene.scene_facts` (fails closed; re-stamp confirms) |
| M10 duplicate `(key, part)` | Fixed: `AMBIGUOUS`, holds nothing |
| M11 unknown `version` | Fixed: reads as nothing and not intact; mutators refuse |
| M12 no final tie-break | Already present (`kid` last); `created` left as text |
| M13 reclassify test; world fan-out unstamped | Fixed: reclassified-group test; `_forget_in_campaign` bumps the campaign's revision when it prunes |

Also found while building: the lock-domain guard resolves a bare-name method
call (`seen.add`) against the module's own `add`, so `_subject` keeps a dict.
