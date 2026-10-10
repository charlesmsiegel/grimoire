# 11. Epistemic history retrieval

**Status:** Draft — cross-linked; spec gate pending.
**Date:** 2026-10-09
**Roadmap:** 11 in `ROADMAP-CHECKLIST.md`. Lane: retrieval (08 + 01e + 01h +
01i → 09 → 10 → **11** → 12), fed by the decision lane (02-C5a).
**Baseline:** `main` at `35c1fb7`.
**Reconciles:** bundle draft `11-epistemic-history-retrieval.md` (2026-10-06,
written against `7f80c42`). It extends, without superseding, the actor-scoped
prompt contract that the character-turns work landed
(`2026-09-11-character-turns.md`, realised in `store/context/actor.py` and
`store/context/assemble.py`), the POV filter of #116, the lore `known_by`
control of `2026-10-05-lore-activation-controls-design.md`, and the
hide-from-context rule of `2026-10-05-play-controls-hide-from-context-design.md`.

> Implementation rule: re-read the code and every PR landed on this subsystem
> since the baseline before planning. In particular, 02, 07, 08, 09 and 10
> were specified in parallel with this document. Contract IDs follow
> `ROADMAP-CHECKLIST.md`; section 13 lists what must still be re-checked
> against their landed form.

## Depends on

Matches the checklist edge `11 ← 09-C1, 09-C3 (H); 02-C5a (H for the
Decision stage); 07-C1 (H for group overrides); 07-C3c, 10-C1, 08-C3b,
01b-C1, 01d-C1/C2, 01c-C4 (S)`.

| Contract | Provided by | What this spec uses it for | Hard or soft |
|---|---|---|---|
| 09-C1 `history.retrieve(Query) -> Evidence`, with a `perspective` seam; evidence carries scene identity, post indices, keys and texts | 09 | The units this spec classifies (section 3.2). 11 adds no retrieval of its own | Hard |
| 09-C3 `history_recall` section, none in NPC prompts until 11 | 09 | 11-C2 splits that section by perspective and by class, inside 09's budget | Hard |
| 02-C5a epistemic access kit (`epistemic` route, `epistemic-access` task, `build_items`, `access_of`, fail-closed mapping, 01d-C1 policy row) | 02 | The Decision stage of 11-C1 (section 5) | Hard for the Decision stage only. The deterministic stage and 11-C2 ship without it |
| 07-C1 `members` on the group record, with overlay semantics | 07 | Expanding a group-audience override (section 7.3) | Hard for group overrides only |
| 07-C3c retrieval projections (`scene_groups`, `co_affiliates`, `prompt_visible`) | 07 | Naming a group audience in the narrator annotation only where the group is prompt-visible | Soft |
| 10-C1 `history_plan` route on Fast, taking a perspective | 10 | An actor call's planner input is actor-visible (section 6.4) | Soft: 11 classifies whatever 09 returns, planned or not |
| 08-C3b `expand(...)`, the caller naming the phase | 08 | Rendering the visible sub-ranges of a split excerpt in the prompt phase (section 4.4) | Soft: 09-C1 already hands over post texts |
| 01b-C1 one capture helper at every decide site | 01b | Capturing the Decision stage to the prompt log | Soft |
| 01d-C1 / 01d-C2a / 01d-C2b task policy, trigger with an answer filter, one escalation hop | 01d | The one-hop escalation 02-C5a's policy row declares for `epistemic-access` | Soft: without it an answer stands as given, and a failure is `unknown` |
| 01c-C4 no sampled stand-in for an abstention | 01c | An abstention stays `unknown` | Soft (already true of `decide` today) |

## Required by

| Contract (provided here) | Consumer | What the consumer uses it for | Hard or soft |
|---|---|---|---|
| 11-C1 per-actor classes | 12 (12-C1, 12-C3) | Every investigation tool that returns history in RP actor mode filters through it, and E1's selection is re-classified at finish | Hard for RP mode |
| 11-C2 narrator and actor prompt separation | 12 (12-C3) | What an RP investigation selects is rendered by the same split, never as model prose | Hard for RP mode |
| 11-C4 leakage eval suite | 12 (12-C2c) | 12's eval gate reuses the leakage graders | Soft |
| 11-C3 knowledge overrides | 12 (12-C1, read only) | `get_actor` reports overrides naming the actor; nothing in 12 writes them | Soft (informational; not a checklist edge) |

09-C3 also relies on 11-C2 for its one cross-spec decision: no history in NPC
prompts until 11-C2 lands (`ROADMAP-CHECKLIST.md`, "Cross-spec decisions";
section 6.5 here).

## 1. Current state (reconciled against main)

The draft treated epistemic filtering as greenfield. It is not. An NPC's prompt
is already scoped to that NPC, and several layers of knowledge control exist.
What is missing is narrower than the draft assumed: there is no *history*
retrieval yet, so there is nothing yet that could leak historical evidence,
and the structures that will decide who may see it are scene-local.

### 1.1 Every turn is already a per-actor call

- Character turns are always on: `routes/character_turns.py:51`
  (`enabled()` returns `True`). Each round composes one prompt per assigned
  actor, through `_compose` (`routes/character_turns.py:84-117`), which passes
  `actor_ref` to `store.context.compose_turn` or `compose_director_turn`
  (`store/context/assemble.py:1637`, `:1700`).
- The actor is either an NPC ref or `"grimoire"`, the narrator.
  `assemble._assemble` treats `"grimoire"` as unscoped:
  `actor_scoped = actor_ref is not None and actor_ref != "grimoire"`
  (`store/context/assemble.py:227`). This is the draft's
  `perspective = narrator | actor:<ref>`, already in the code under another
  name.
- Composition runs **inside `campaign_lock`** in `_prepare`
  (`routes/character_turns.py:476-477`). Nothing on that path may await a
  model. Any Decision call this spec adds must happen before `_prepare`, and
  hand its result in.

### 1.2 What an actor-scoped prompt may hold

`assemble._assemble` states it as a contract (`assemble.py:521-538`):

- It blanks `story_entries`, `archive_entries`, `plot_lines`,
  `commitment_lines`, `group_states`, `secret_group_states`,
  `offscene_active`, `offscene_known`, `players`, `refs`, `ref_names` and
  `available_art`. Its comment calls the list "what an NPC's prompt is
  allowed to hold", and keeps it even though `_campaign_view` no longer
  gathers most of these for an actor.
- It replaces `relationship_lines` with `actor.own_relationships`
  (`store/context/actor.py:80-95`): the actor's own feelings and the bonds it
  is party to, never another actor's feelings.
- It narrows mechanics sheets and checks to the actor, and blanks `today`
  because "scheduled events have no knowledge attribution".

So **today an NPC sees no campaign history at all** beyond the current scene,
apart from what absorb has distilled into its own `state.md`. The recap and the
archive ("Earlier scenes", `assemble.py:976`) are narrator-only by
construction. That is safe, and it is also why NPCs forget their own past.
09 is what lets an NPC recall it, and 11 is what keeps that recall from
becoming omniscience.

### 1.3 Evidence boundaries that already exist

| Layer | Where | What it decides |
|---|---|---|
| Presence intervals | `appearances.json` `presence[sid]` as half-open `{start, end}` transcript intervals; written by `transitions._start_presence` (`store/appearances/transitions.py:24`) and closed on leave (`:103-105`); kept aligned through cuts by `paths.remap_presence` (`store/appearances/paths.py:103-135`); dropped on scene delete (`:138-150`); copied to a branch (`:175-200`) | Which posts of a scene an actor was there for |
| Observed history | `actor.observed_history` (`store/context/actor.py:25-36`) | The current scene's posts an NPC call sees. With no interval record it falls back to "the latest player input", because unknown historical presence is not proof of observation (`actor.py:1-5`) |
| Lore audience | `actor.knows` / `known_entries` (`actor.py:54-77`), reading `secrecy` (`store/entities.py:26-47`) and `known_by` (`store/lore_fields.py:25-51`) | `gm-only` reaches no actor; a `known_by` list decides alone; else owners, else public lore with no actor owner. Applied in `world_state._world_info` and again in `assemble.py:440-442` as a belt-and-braces no-op |
| Tracker awareness | `tracker/view.py:25-72` | A tracked value is visible to its owner, to everyone if `aware: "present"`, or to the refs in a list ("told") |
| Suspicion filter (#116) | `world_state._visible_suspects` and `_character_states` (`store/context/world_state.py:1071-1260`) | In a scene with a player, an NPC's `suspects` entries that name another present actor are withheld. The docstring names this "the coarse half of #116" and says true POV needs "knowledge to be stored per-entry rather than as prose (#122)" |
| Perception instructions | `templates/scene/response_actor.j2` ("What this actor can perceive"), plus the optional `perception` fence (`config.perception_rider`, `store/config.py:493`; parsed by `store/response_protocol.py:166-230`) | The model is told that private thoughts are not heard, and may be asked to cite the source of each perception |
| Hidden posts | `scenes/serialize.py:86-99` (`is_excluded`, `in_context`); `chronicle.transcript_text` drops them by default (`store/chronicle.py:221-240`) | A hidden post reaches no prompt, for anyone |

### 1.4 What the store records about knowledge, and in what form

- **`state.md` per NPC** (`store/playstate.py`): `current_state`, `knows`,
  `suspects` as **prose sections**, rewritten whole at each absorb and applied
  only through the review (`store/absorb/materializer.py:962-972`,
  `store/absorb/apply.py:203`). The absorb prompt already encodes a
  source rule: narration may set what a character "knows"; a character's
  claim about someone else is recorded under "suspects" for those who heard
  it (`templates/absorb/system.j2:29`). The draft's "explicit knows/suspects
  state" exists, but as prose. It cannot be joined to an evidence unit
  mechanically.
- **`dossier.md`** (`store/dossiers.py`): a narrator-side paragraph, blanked
  for actors (`offscene_active`).
- **Facts ledger** (`store/facts.py`): standing truths with `scene` and
  lifecycle, and **no actor attribution at all**. `store/casefile.py:24-28`
  says so: "facts.json records no actors — a fact is a sentence about the
  world".
- **Relationships** (`store/relationships.py`) and the append-only
  `relationship_history.json` (`store/relationship_history.py`): directed
  feelings and symmetric bonds, keyed by actor pairs.
- **Groups**: `entity_schema.FIELDS["groups"]` has `group_type`, `leader` and
  `headquarters`, and **no members** (`store/entity_schema.py:145-151`). 07-C1
  adds them.
- **Scene-to-actor joins**: `continuity/involvement.scene_actors` unions the
  chronicle's per-scene `cast` snapshot with `appearances.json`
  (`store/continuity/involvement.py:1-20`, `:101`), because neither alone
  covers every scene.
- **Post keys**: a player post can carry `post_id` and a reply carries
  `response_id` (`store/scenes/serialize.py:430-436`). The tracker keys its
  records by these, "never from an index" (`store/tracker/walk.py:8`), and
  the regex rewrite records do the same (`store/regex/rewrites.py:16`,
  `:72-99`). Older posts may carry neither.

### 1.5 Where the draft does not match the code

- **"Explicit knows/suspects state"** is prose (1.4). 11 uses it as context
  for the Decision stage, never as a deterministic join.
- **The draft's actor prompt carried a narrator-only section with an
  instruction not to use it.** The current contract blanks narrator-side data
  rather than instructing around it (1.2). 11 keeps blanking. A narrator-only
  unit never reaches an actor's prompt, under any heading.
- **"Decision integration is later."** The decision lane now provides an
  epistemic access kit (02-C5a). The Decision stage is specified here, behind
  its own switch, and off until 02-C5a's gate passes.
- **Presence was described as "appearances/cast".** It is post-granular
  (presence intervals) where the record has intervals, and scene-granular
  elsewhere. The classifier has to treat the two differently (4.2).

## 2. Goal

Historical evidence that 09 retrieves for a turn carries an **epistemic
relation to the actor the turn is written for**. An actor's prompt receives
only the evidence that actor has access to, labelled by how they have it. The
narrator's prompt receives all of it, annotated with who knows what.

Concretely:

1. A deterministic classifier turns each retrieved unit into one or more
   slices, each with an access class and the basis for it. Obvious cases
   (an actor was there, an actor was absent, the user said so) need no model.
2. A bounded Decision stage refines only the cases the classifier cannot
   settle *and* for which a plausible channel exists. It never runs for the
   narrator, never upgrades anything to "was there", and never persists.
3. The prompt separates narrator and actor views. An actor-scoped prompt holds
   no narrator-only evidence by construction, not by instruction.
4. The user can state authoritative knowledge ("Mara knows what happened in
   that scene", "Winifred never heard that whisper") in one campaign file.
   That file beats every derived classification.
5. An eval suite measures knowledge leakage separately from retrieval recall.

**Not the goal:**

- Re-deciding lore visibility. `actor.knows` stays the one rule for world
  info. 11 classifies *history* (scenes, excerpts, summaries, facts), not
  lore entries.
- Replacing the in-scene perception rules. The current scene's
  `observed_history` and the perception instructions are unchanged.
- Modelling the player character's knowledge. Nothing generates the PC's
  words. PCs appear in the narrator's annotations only.
- Making knowledge state a second `state.md`. Overrides are sparse
  corrections, not a ledger of everything every actor knows.

## 3. Vocabulary

### 3.1 Perspective

```python
@dataclass(frozen=True)
class Perspective:
    actor: str | None          # "characters:<id>" for an NPC call; None for the narrator
    present: tuple[str, ...]   # every actor ref on stage this turn (scene_cast, after excludes)
```

`Perspective.of(actor_ref, scene_cast)` maps `None` and `"grimoire"` to the
narrator, which is exactly `assemble`'s `actor_scoped` test. Built from the
same post-exclude cast `_assemble` uses (`assemble.py:193-201`), so an actor
the reader excluded from context is neither a perspective nor a "known to"
name.

### 3.2 Unit

The unit is 09-C1's evidence item, seen through the fields 11 needs. 09-C1
carries the scene identity and the post indices, keys and texts of each
item; 11 reads them as:

```python
@dataclass(frozen=True)
class Unit:
    key: str                     # stable within one evidence set
    kind: str                    # "excerpt" | "scene" | "fact"
    sid: str                     # the scene file it came from ("" for a fact with no scene)
    identity: str                # the scene's identity token (scenes/identity.py:200)
    posts: tuple[int, ...]       # transcript indices an excerpt was cut from, ascending; () for scene/fact
    post_keys: tuple[str, ...]   # "p-<post_id>" / "r-<response_id>" per post, "" where absent; aligned with posts
    texts: tuple[str, ...]       # each post's prompt-view text, aligned with posts (excerpt only)
    text: str                    # the rendered text 09 would show (scene summary, fact sentence, joined excerpt)
    fact: str = ""               # fact id, kind == "fact"
```

- An **excerpt** is a window of posts from one scene, as 08-C3b's `expand`
  returns it.
- A **scene** unit is anything that summarises a whole scene: a chronicle
  summary, a SearchDocument projection.
- A **fact** unit is a facts-ledger row.

`texts` are already the **prompt view** (`regex.view.view(...,
phase="prompt")`) with hidden posts and director notes removed
(`scenes_serialize.in_context`). 11 re-checks this (section 4.1); it does not
re-read the transcript to render.

### 3.3 Access classes

One class per (perspective actor, slice):

| Class | Meaning | Reaches the actor's prompt as |
|---|---|---|
| `witnessed` | The actor was present for every post in the slice, or wrote it | "You were there" (subject to the perception rules) |
| `known` | Established that the actor knows it without having been there: an override, or a Decision answer | "You know this" |
| `suspected` | Established belief, not confirmed: an override, or a Decision answer | "You suspect this (unconfirmed)" |
| `narrator_only` | Established that the actor does not have it: an `unaware` override | Nothing |
| `unknown` | Access cannot be shown | Nothing |

`unknown` is never collapsed into `known`. Both of the last two rows are
withheld from the actor. They differ only in what may happen next: `unknown`
may go to the Decision stage, and `narrator_only` never does.

For the narrator, each unit additionally carries an aggregate:
`known_to: tuple[str, ...]` (present actors and PCs whose class is
`witnessed` or `known`), `suspected_by`, and `narrator_only: bool`, which is
true when no present actor has any access.

### 3.4 Basis

Each slice carries `basis: tuple[str, ...]` from a closed set:

`override`, `override_group`, `override_all`, `authored`,
`presence_interval`, `absent_interval`, `scene_cast`, `whole_scene`,
`partial_scene`, `no_record`, `decision`, `decision_abstained`,
`decision_failed`, `decision_skipped`.

**The basis never reaches a prompt.** It feeds the inspector rows and the
prompt-log capture only, as activation reasons do (CLAUDE.md, "Reasons never
reach the prompt"). The class does reach the prompt, because the model has to
know that a suspicion is unconfirmed.

## 4. The deterministic stage

`store/context/epistemic.py`, a pure, synchronous module:

```python
def classify(cid: str, perspective: Perspective, units: Sequence[Unit]) -> EpistemicView
```

It reads three things, once, under one `locks.best_effort_campaign_lock(cid)`
hold. That is the policy `_assemble` uses, and its comment says why a
`StoreBusy` must not strand a turn (`assemble.py:162-169`). The three reads
are:

- `appearances.paths.record(cid)` (presence and scene lists);
- `responses.actor_refs(cid, sid)` for each distinct `sid` among the units
  (`store/responses.py:69-80`), which says which reply each NPC wrote;
- `knowledge.read(cid)`, the overrides (section 7).

It calls no model, takes no `campaign_lock`, and writes nothing. It is in
`store/context/` on purpose: `test_regex_prompt_guard.py` scans that
directory, and the Decision items this module builds carry transcript text.

### 4.1 Admissibility (before any rule)

A unit, or a post inside one, is dropped for **every** perspective,
narrator included, when:

- the post is hidden from context or is a director note (`in_context`). This
  is already 09's job. 11 checks it again because the cost of a miss is a
  hidden OOC post in a prompt, which the hide-from-context spec promises never
  happens;
- its scene no longer exists, or its identity no longer matches `identity`
  (a scene deleted and its id reissued, which `forget_presence` exists to
  stop: `store/appearances/paths.py:138`);
- it is a fact unit whose fact is not `active` as of the turn
  (`facts.is_active`, `store/facts.py:123`). 09 decides whether retired facts
  are evidence at all. 11 does not let one stand as current knowledge.

### 4.2 Rules, in order, for one actor

Rules are applied post by post for an excerpt and once for a scene or fact
unit. The first rule that settles a post wins. Posts that end in different
classes **split the unit into slices**: contiguous runs of one class. An
excerpt is never shown whole to an actor who was there for only part of it.

| # | Rule | Applies to | Result | Basis |
|---|---|---|---|---|
| R1 | An override matches this actor and this post, scene or fact (section 7.2 gives precedence) | all | `known`, `suspected` or `narrator_only` | `override`, `override_group` or `override_all` |
| R2 | The post is a reply whose `response_id` maps to this actor in `responses.actor_refs` | excerpt | `witnessed` | `authored` |
| R3 | The actor's record has `presence[sid]` and an interval covers the post index | excerpt | `witnessed` | `presence_interval` |
| R4 | The actor's record has `presence[sid]` and no interval covers the post | excerpt | `unknown` | `absent_interval` |
| R5 | No `presence[sid]` for the actor, but the scene is in the actor's `scenes` list or the chronicle `cast` for it | excerpt | `unknown` | `scene_cast` |
| R6 | Scene unit: the actor's intervals cover `[0, len(transcript))` of that scene | scene | `witnessed` | `whole_scene` |
| R7 | Scene unit: the actor was present for part of it, or attended with no intervals | scene | `unknown` | `partial_scene` or `scene_cast` |
| R8 | Fact unit: classify the fact's recording scene as a scene unit (R6 and R7) | fact | as R6 or R7 | as R6 or R7 |
| R9 | Nothing above | all | `unknown` | `no_record` |

Why each rule is shaped the way it is:

- **R1 first.** The user's statement is authoritative over every derived
  signal, presence included. That is what makes the "present, but the secret
  was whispered privately" case fixable without a model: an `unaware`
  override on the whispered posts.
- **R2 before R3.** A reply an NPC wrote proves presence at that post even in
  a record whose intervals were lost, for example a legacy scene. A PC's post
  carries no actor ref (`post_id` only) and proves nothing about NPCs.
- **R3 is presence, not perception.** The draft says "presence is not proof
  that every secret in a scene was perceived", and it is right. 11 handles
  that the same way the current scene already does. A `witnessed` excerpt
  renders under the perception rules in `response_actor.j2` (section 6.2): the
  transcript may narrate another character's private thoughts, and being in
  the room does not grant them. 11 does not send `witnessed` slices to a model
  (section 5.1 says why), and an override is the deterministic fix.
- **R4 and R5 are `unknown`, not `narrator_only`.** Absence from a scene is
  not proof of not knowing. Someone may have told the actor in a later scene.
  That is exactly the "later scene where actor learns the fact" case, and only
  the Decision stage (with the later scene as context) or an override can
  establish it.
- **R5 does not count as presence.** It applies where the record has a scene
  but no intervals. That is what any scene recorded before presence intervals
  existed looks like, and `actor.observed_history` already refuses to treat it
  as observation (`actor.py:1-5`, `:29-31`). Admitting R5 slices would make a
  legacy campaign's NPCs omniscient about every scene they ever stood in,
  arrivals and departures included. The cost runs in one direction: a legacy
  NPC's recall is thinner until the Decision stage or an override fills it.
  Section 14, question 1 asks whether to keep that default.
- **R6 needs the whole scene.** A summary covers everything that happened in
  the scene, including the part the actor missed. A partial attendee gets
  excerpts (their R3 slices), never the summary.
- **R8 has no finer rule.** A fact names no actor (1.4). Its recording scene
  is the only structural link, and a fact can be established anywhere in that
  scene.

### 4.3 The narrator

For the narrator perspective, every admissible unit is visible. `classify`
runs R1–R9 once per **present** actor (and per present PC, for the
annotation only) to fill `known_to`, `suspected_by` and `narrator_only`. No
Decision call is ever made for the narrator: the annotation is a reading aid
for the writer, and a model call per actor per unit per narrator turn would
cost more than everything else on the turn. An `unknown` actor is simply not
listed under `known_to`.

### 4.4 Rendering a split excerpt

A slice's text is rebuilt from the `texts` of its posts, in order, using the
same transcript renderer 09 uses for a whole excerpt (08-C3b's `expand`,
with the phase named `prompt`, or `chronicle.transcript_text` over the prompt-view messages, which
`test_regex_prompt_guard.py` accepts). A split never re-reads the scene. If
`texts` is absent, the unit is treated as unsplittable: it is `witnessed`
only if every post is, and otherwise the whole unit is withheld. Showing too
little is the safe direction.

### 4.5 Result shape

```python
@dataclass(frozen=True)
class Slice:
    posts: tuple[int, ...]        # () for scene and fact units
    access: str                   # one of ACCESS
    basis: tuple[str, ...]
    text: str                     # rendered for this slice

@dataclass(frozen=True)
class Classified:
    unit: Unit
    slices: tuple[Slice, ...]     # for an actor perspective; one slice per contiguous class run
    known_to: tuple[str, ...]     # narrator annotation: present refs with witnessed/known access
    suspected_by: tuple[str, ...]
    narrator_only: bool           # no present actor has any access

@dataclass(frozen=True)
class EpistemicView:
    perspective: Perspective
    units: tuple[Classified, ...]   # in 09's order
    decided: int                    # slices settled by the Decision stage
    pending: tuple[tuple[str, tuple[int, ...]], ...]   # (unit key, posts) eligible for Decision

    def visible(self) -> tuple[Classified, ...]: ...   # actor: slices in VISIBLE; narrator: all
```

`ACCESS = ("witnessed", "known", "suspected", "narrator_only", "unknown")` and
`VISIBLE = ("witnessed", "known", "suspected")` are module constants. A
renderer for an actor prompt may read only `visible()`. Section 9 holds that
with a test. The JSON form, for the inspector and the capture, is the same
fields with tuples as lists:

```json
{"perspective": "characters:mara",
 "units": [{"key": "u3", "kind": "excerpt", "sid": "0007--the-pier",
            "slices": [{"posts": [41, 42], "access": "witnessed", "basis": ["presence_interval"]},
                       {"posts": [43], "access": "unknown", "basis": ["absent_interval"]}],
            "known_to": ["characters:mara", "pcs:seraphine"], "suspected_by": [],
            "narrator_only": false}],
 "decided": 0}
```

The shape keeps the draft's `{scene, excerpt, epistemic, basis}` in
substance, with the class per slice rather than per unit.

## 5. The Decision stage

```python
# routes/epistemic.py (helpers; no router)
async def refine(cid: str, sid: str, view: EpistemicView, *, client: LLMClient,
                 post: int | None, round_id: str) -> EpistemicView
```

It runs on the turn path **after** retrieval and **before** `_prepare`, outside
any campaign lock, as the speaker pick does (`routes/character_turns.py:728-761`):
the resolution and item building happen in the threadpool, `decide` renders
in a worker thread, and the capture goes back to the threadpool.

### 5.1 Which slices may be asked about

A slice is **eligible** only when all of these hold:

1. the perspective is an actor (never the narrator, 4.3);
2. its class is `unknown`, and its basis is `absent_interval`, `scene_cast`,
   `partial_scene` or `no_record`. A `narrator_only` slice from an override is
   never asked about: the user's statement is not a question;
3. a **channel** is plausible, meaning at least one of:
   - (a) the basis is `scene_cast` or `partial_scene`: the actor stood in the
     scene;
   - (b) the actor's own `state.md` has non-empty `knows` or `suspects`
     (`playstate.read_state`);
   - (c) the same evidence set holds a slice the actor `witnessed` from a
     **later** scene in play order, in which an actor who has access to this
     slice was also present (a plausible teller, read off the same presence
     record).

The gate exists for cost and for correctness. Without (3), every
narrator-side scene in the evidence set would cost a Decision item on every
NPC turn, and a model asked "might Mara know this?" with no channel in view
can only guess.

`witnessed` slices are **not** eligible. Auditing them would put every
recalled scene through a model call on every turn. In-room perception is
handled by the perception contract the current scene already relies on, and
the whisper case has a deterministic fix (an override). Section 14,
question 3 asks whether to add an opt-in audit.

### 5.2 Items and questions

One `decisions.Item` per eligible slice, at most `EPISTEMIC_DECIDE_ITEMS = 8`
per actor turn. That is one structured chunk
(`decisions.MAX_ITEMS_PER_CALL`), so the structured path costs at most one
call per actor turn. On a native backend each item is its own request, under
`NATIVE_CONCURRENCY`. Eligible slices beyond the cap stay `unknown` with
basis `decision_skipped`, chosen in 09's rank order. The constant is argued
from the chunk size alone and should be tuned against real prompts later.

The items are built by 02-C5a's kit, not by 11:

```python
# store/epistemic_access.py (02-C5a)
questions = [AccessQuestion(actor_ref=..., actor_name=...,
                            evidence_ref=<09's evidence id + slice posts>,
                            excerpt=<the slice text, prompt view, capped>,
                            basis=<the slice's 11 basis codes>)
             for each eligible slice]
items = epistemic_access.build_items(questions)
```

11 adds to each question's excerpt, inside the kit's bound, the actor's own
`state.md` knows/suspects (capped) and up to two later `witnessed` channel
slices from 5.1 (3c), so the item can see the channel that made it eligible.

- The task and route are 02-C5a's: task `epistemic-access` on the `epistemic`
  route (`operation="decide"`, `default_role="decision"`). Resolve it with
  `require_inference("epistemic-access", cid, operation="decide")` and call
  `operations.decide("epistemic-access", items, client=…, resolved=…,
  campaign=cid, scene=sid, post=…, round_id=…, capture=…, around=…)`
  (`inference.py:684-714`). Read the answers with `epistemic_access.access_of`.
- The item context is self-contained: one actor, one slice, the actor's own
  state, and the channel excerpts. It never carries another actor's
  `state.md`, a dossier, or a narrator-only unit, because the context is
  itself a prompt that a later capture shows.
- No rationale is requested (`explain=""`). Nothing stands on one, and a
  native backend has none to give (CLAUDE.md, decide section).

### 5.3 Reading the answer

| `access_of` result (02-C5a) | Result | Basis |
|---|---|---|
| `known` | `known` | `decision` |
| `experienced` | `known` (never `witnessed`) | `decision` |
| `suspected` | `suspected` | `decision` |
| `narrator_only` | stays `unknown` (withheld) | `decision` |
| `unknown` (abstained, refused, unreadable, `NOT_AN_OPTION`, an error, an item never reached; 02-C5a's fail-closed mapping) | stays `unknown` | `decision_abstained` |
| the call failed (`LLMError`, `BudgetRefused`, `DecideRequestError`, 409 `incapable` at resolution) | every eligible slice stays `unknown` | `decision_failed` |

Three rules follow:

- **A model never produces `witnessed` or `narrator_only`.** Presence is a
  structural fact, and `narrator_only` is the user's word. So the kit's
  `experienced` is read as `known`, and its `narrator_only` is withheld the
  same way as `unknown` without being recorded as established.
- **An answer is taken as given, apart from 02-C5a's escalation.** No
  probability threshold is invented here. 02-C5a's 01d-C1 policy row escalates
  a `refused` or low-margin permissive answer one hop (01d-C2a's trigger with
  its answer filter, 01d-C2b's hop), and a failed or skipped hop is `unknown`.
  A distribution is never sampled (01c-C4): this is a classification, not a
  choice the story should vary.
- **The stage is fail-soft.** A failure leaves every slice withheld and the
  turn proceeds. It never fails the turn, unlike the speaker pick, whose
  `LLMError` propagates. Here a failed classification has a safe default, and
  a pick does not.

### 5.4 Time and cost

- The whole stage runs under one ceiling, handed to `decide` as `around`:
  `common._bounded_call` with `config.llm_call_budget()`
  (`routes/common.py:555-600`), the ceiling every turn-path call already
  takes. An overrun is `decision_failed`.
- The stage is off unless the `epistemic_decide` config key is `on`. That
  follows the shape of `perception_rider` (`store/config.py:89`, `:493`), and
  the default stays `off` until 02-C5a's `decide-epistemic-access` gate case
  task passes (CLAUDE.md: "A call site converts only behind `evals/run.py
  --gate`").
- Every call is metered by `decide`'s own meter under the task. Spend counts
  against the campaign like any other.

### 5.5 Nothing derived is persisted

A Decision answer is used for the turn being composed and nothing else. It
lands in three places, none of them authoritative:

- the **frozen prompt snapshot** of the reply (`responses.prepare`). A reroll
  replays the snapshot rather than recomposing, so a reroll neither
  re-classifies nor pays again;
- the **prompt-log capture** (01b-C1), as a zero-token outcome section, with
  the same section id convention as the speaker capture
  (`routes/character_turns.OUTCOME_SECTION_ID`);
- the **inspector** for that turn (section 6.3), from which the user may
  *accept* an answer as an override (7.4).

No cache of answers is kept across turns. A cache keyed on inputs would hold
a model's output as though the inputs determined it, which is the thing 03
refuses to cache. The cost is a repeat call for the same slice on a later
turn, bounded by the cap above.

## 6. Prompt separation (11-C2)

### 6.1 The rule

09-C3 defines the `history_recall` section with a strict token budget. 11-C2 says how it
renders by perspective:

- **Narrator** (`actor_ref` `None` or `"grimoire"`): two sections.
  - `history_recall` (09-C3's section) lists every visible unit, each followed by a
    one-line annotation rendered from `known_to` / `suspected_by`: `Known to:
    Mara, Seraphine` / `Suspected by: Winifred`, or nothing when nobody
    present is listed.
  - `history_narrator_only` lists the units with `narrator_only` true, under a
    heading that carries the instruction `secret_world_info_bodies` already
    uses for secret lore: the writer may use it for continuity, and no
    character acts on it unless it reaches them on the page. The section
    renders nothing when there are none.
- **Actor**: two sections, both rendered only from `EpistemicView.visible()`.
  - `history_known` ("What {name} remembers or knows"): `witnessed` slices
    under "You were there" and `known` slices under "You know this".
  - `history_suspected` ("What {name} suspects, unconfirmed"): `suspected`
    slices.
  - There is no narrator-only section in an actor prompt, under any name. The
    draft's "include it with a prohibition" is rejected for the reason the
    current contract gives: an instruction is weaker than an absence.

### 6.2 Templates and wording

New templates under `templates/scene/sections/`: `history_narrator.j2`,
`history_narrator_only.j2`, `history_actor_known.j2` and
`history_actor_suspected.j2`. 09-C3's `history_recall.j2` template becomes the
narrator one, or delegates to it. The actor templates restate, in one
sentence, the perception rule from `response_actor.j2`: being there means
what could be seen or heard, not other people's private thoughts. That keeps
a `witnessed` excerpt that narrates another character's interior consistent
with how the current scene is handled. The labels "You were there", "You know
this" and "suspected, unconfirmed" are also what the perception rider's
`known` field can cite as an "existing source".

`scripts/verify_templates.py` and the offline eval needles (CLAUDE.md, "After
editing anything in `templates/`") cover the new templates. The leakage suite
(section 8) adds needles that must be **absent**.

### 6.3 Placement in the build and the contract

- `assemble._assemble` gains one keyword, `history_view: EpistemicView |
  None`, threaded from `compose_turn` and `compose_director_turn`. Compose
  never classifies and never calls a model. It renders what it is handed.
  With `history_view=None`, no history section renders, which is the
  behaviour before 09.
- **The actor blanking list grows.** 09-C3's `history_recall` data keys and `history_narrator_only` join the tuple
  at `assemble.py:528`. On an actor-scoped compose they are emptied even if
  something populated them. The actor keys are set only from
  `history_view.visible()`. This is the same belt-and-braces shape as the
  `known_entries` re-application at `assemble.py:440-442`: what reaches an
  NPC's prompt must not rest on one call site.
- A **perspective mismatch is refused**. If `history_view.perspective.actor`
  is not the compose's `actor_ref` (narrator matching narrator), compose
  raises `ValueError` before rendering. A view built for the narrator handed
  to an NPC compose is precisely the leak this spec exists to prevent, so it
  must fail loudly rather than render.
- **Packer tiers.** All four sections keep 09-C3's tier. Within it,
  `history_narrator_only` is dropped before `history_recall`, and
  `history_suspected` before `history_known`. A suspicion costs less to lose
  than a memory.
- **The inspector** (`context_breakdown`) shows, per unit, the class and basis
  of each slice and, for an actor turn, the withheld slices with their basis
  (`absent_interval`, `decision_abstained`, …) and an "accept as override"
  action (7.4). The basis appears there and in the capture, never in a
  message (`test_epistemic_basis_never_reaches_the_prompt`).

### 6.4 The planner and the query

10-C1's `history_plan` planner writes the queries that 09 runs, and takes a
perspective. For an actor turn:

- The planner is handed the perspective, and **its inputs must be
  actor-visible**: the actor's observed slice of the current scene
  (`observed_history`), its own state, and the present roster. It must not be
  given narrator-only material. Otherwise its queries can encode a secret
  ("did Mara learn that Winifred is the informer?"). The retrieval results are
  filtered regardless, but a secret-shaped query skews the ranking of
  legitimately witnessed evidence toward the secret, and the query text is
  captured. 10-C1 takes the perspective for this reason.
- 09 retrieves for an actor without regard to access, and 11 filters
  afterwards. Filtering inside 09's candidate generation would be cheaper, but
  it would make 09 depend on 11. Section 14, question 5 asks whether to
  prefilter by `scene_cast` in 09's structural tier.

### 6.5 Before 11 lands

Until 11-C2 is implemented, 09-C3 renders history **only into
narrator-scoped prompts** (09-C3's headline, and the checklist's cross-spec
decision "History in NPC prompts"): an actor-scoped compose gets no history section.
That is the current contract (1.2) extended to a new key, and it is the
required behaviour of 09 at the moment 09 lands. It is listed as a back-edge
in "Required by".

## 7. Authoritative knowledge overrides (11-C3)

### 7.1 Storage

`store/knowledge.py` owns `<campaign>/knowledge.json`:

```json
{"version": 1,
 "entries": {
   "k-3f9a1c2e": {
     "audience": "characters:mara",
     "status": "knows",
     "subject": {"kind": "scene", "scene": "<scene identity>", "posts": ["r-6d1e", "p-8a40"]},
     "note": "Winifred told her on the walk back.",
     "source": "manual",
     "created": "2026-10-09T12:00:00Z"},
   "k-77b0e4d1": {
     "audience": "*",
     "status": "unaware",
     "subject": {"kind": "scene", "scene": "<scene identity>", "posts": ["r-91c2"]},
     "note": "Whispered aside; nobody else heard.",
     "source": "manual",
     "created": "2026-10-09T12:05:00Z"},
   "k-0c55aa90": {
     "audience": "groups:saltmarch-watch",
     "status": "knows",
     "subject": {"kind": "fact", "fact": "f12"},
     "note": "", "source": "accepted", "created": "2026-10-09T12:10:00Z"}}}
```

- `audience` is an actor ref (`characters:` only. PCs are not generated, 2),
  a group ref (`groups:<id>`), or `"*"` (every actor).
- `status` is `knows`, `suspects` or `unaware`.
- `subject.kind` is `scene` (the whole scene when `posts` is empty, otherwise
  those posts) or `fact`.
- **Subjects are addressed by scene identity and post key, never by `sid` or
  index.** A `sid` moves on rename and is reissued after a delete, and an
  index moves on every cut. The identity and the `p-`/`r-` keys are what the
  tracker and the rewrite records already use for exactly this reason (1.4).
  A post without a key can only be covered by a whole-scene subject, which the
  editor says when it offers the choice.
- `source` is `manual` (typed by the user) or `accepted` (an inspector
  classification the user accepted, 7.4). Both are authoritative. The field
  only says where the entry came from.
- Ids are `k-` plus 8 hex characters from `uuid4`. A pruned id is never
  reissued in practice, and nothing addresses an entry by position.

The module follows the ledger conventions:

- `read` is tolerant: an unparseable file reads as empty, and a malformed
  entry is skipped while its neighbours are kept;
- the mutators (`add`, `update`, `remove`, `restore`) refuse a document of the
  wrong shape rather than publishing an empty file over it (the
  `facts._read_ledger` rule, `store/facts.py:87-101`);
- writes are whole-file via `store.atomic`, under `locks.campaign_lock(cid)`.

### 7.2 Precedence when several entries match

For one actor and one post (or scene or fact), collect the matching entries,
then choose by:

1. **audience specificity**: the actor's own ref, then a group the actor is an
   effective member of (07-C1), then `"*"`;
2. **subject specificity**: post-listed, then whole-scene. A fact entry
   matches only its fact unit;
3. **newest `created`**;
4. on an exact tie, `unaware` before `suspects` before `knows`. The tie is
   broken in the withholding direction, as #116's filter is.

One entry wins. Its status maps to `known`, `suspected` or `narrator_only`,
and its basis is `override`, `override_group` or `override_all`.

### 7.3 Group audiences

A `groups:<id>` audience is expanded through 07-C1's effective members
(campaign overlay over world) **as of now**. Co-membership is not knowledge
by itself, and the draft is right on this. A group audience is an explicit
statement by the user that the members share this knowledge. "As of now"
means someone who joins the group later is covered. That is the natural
reading of "the Watch knows", and it is stated in the editor. A group whose
ref no longer resolves (deleted, or reclassified to another kind) expands to
nobody, which fails closed. That is the property `lore_fields.KNOWN_BY_KINDS`
protects for lore refs (`store/lore_fields.py:36-38`).

### 7.4 Writers

- **Hand edits** go through `routes/ledger.py`, the existing home of ledger
  hand edits. That module takes the campaign lock and wraps every write in
  `undo.journalled`, so the edit lands in the Changes panel as `manual` and
  can be reversed (CLAUDE.md, "Hand edits to the ledger go through
  `routes/ledger.py`"). Routes:
  `POST /campaigns/{cid}/ledger/knowledge`,
  `PUT /campaigns/{cid}/ledger/knowledge/{kid}`,
  `DELETE /campaigns/{cid}/ledger/knowledge/{kid}`.
  Bodies are plain `BaseModel` fields, dumped via `routes.common._dump`
  (pydantic v1/v2 agnostic). A malformed audience, status or subject is
  refused with 400. A subject naming a scene identity that does not resolve is
  refused with 404, never stored as a dangling claim.
- **Accept from the inspector**: an inspector row for a classified slice
  offers "Mark as known / suspected / unaware to {name}". It posts the same
  `POST` with `source: "accepted"` and the slice's post keys. That is how a
  one-turn Decision answer becomes durable: only by the user's action.
- **Absorb does not write overrides in this spec.** Absorb already records
  what a character learned, as `knows`/`suspects` prose through the review
  (1.4). A structured "learned" row from absorb is a later extension (section
  14, question 6). The guard below makes sure nothing under `store/absorb/`
  calls the knowledge mutators until a spec says it may.
- **No model writes this file**, ever. `test_knowledge_writer_guard.py`
  (modelled on `test_absorb_writer_guard.py`, resolving import bindings) fails
  if anything outside `routes/ledger.py` and `store/knowledge.py` calls
  `knowledge.add`, `update`, `remove` or `restore`, and it names
  `store/context/`, `store/absorb/`, `store/continuity/` and 12's
  investigation package explicitly.

The ledger UI follows the list/detail page pattern (CLAUDE.md, Frontend): a
"Knowledge" section with `.editor-list` rows, a read-only detail view (the
audience as a clickable chip, the subject as a chip to the scene, the status
as a plain chip, the note rendered as markdown), and an explicit Edit step.
Tests cover row → view, Edit → form, and `+ New` → form.

### 7.5 Lifecycle of referenced records

- **Scene rename**: nothing to do, since entries hold identities.
- **Scene delete**: an entry whose identity no longer resolves is inert. It
  matches nothing, because 4.1 drops units from a deleted scene. The ledger
  lists it under "points at a deleted scene", with Remove. It is not pruned
  automatically, because a hand-written statement is not deleted behind the
  user's back.
- **Branch**: `store/branch.branch_scene` copies keyed records through each
  owner's helper (`store/branch.py:165-230`, as
  `regex_rewrites.copy_for_branch` does at `:218`). `knowledge.copy_for_branch(cid,
  src_identity, dst_identity, rid_map, through_keys)` copies post-keyed
  entries whose posts are all among the copied posts, re-keying `r-` ids
  through `rid_map`, and copies whole-scene entries as they are.
  `branch.discard` drops the branch's entries.
- **Campaign fork**: the file is campaign-local and travels with the fork
  copy. No world-side counterpart exists, and none is added.
- **Fact delete** (`facts.forget`): an entry naming it becomes inert, as for a
  deleted scene.
- **The frozen campaign** has no `knowledge.json`. An absent file is no
  overrides, and `snapshot.json` is unchanged.

## 8. Evals for leakage (11-C4)

### 8.1 Corpus

`evals/cases/epistemic/` holds synthetic campaigns built with invented names
only (Seraphine, Mara, Winifred, Realm, Saltmarch). Each case is a small store
fixture plus an evidence set, a perspective, and two sets of needles:
propositions the actor **may** see, and propositions that **must not** appear.
The required cases come from the draft, plus three this spec's rules create:

1. the actor was absent for a revelation (R4): it must not appear;
2. the actor was present, but the secret was whispered privately, with an
   `unaware` override on the whisper (R1 over R3): it must not appear;
3. the same as 2 **without** the override (R3): offline it is visible, which
   is expected; live, the graded output must not use it. This tests the
   perception contract, not the classifier;
4. the actor explicitly knows (`knows` override): it appears under "You know
   this";
5. the actor only suspects: it appears under the suspicion heading and nowhere
   else;
6. the narrator knows and no actor does: it appears in
   `history_narrator_only` in a narrator prompt and in no actor prompt;
7. a later scene in which the actor learns the fact: deterministic
   classification leaves it `unknown`; with the Decision stage on, the gold
   answer is `known`, with the later scene as the channel;
8. a similarly named fact from the wrong event: classified by its own
   scene, never by name;
9. a partial attendee: the summary is withheld, and only the attended excerpt
   appears;
10. a legacy scene with no intervals (R5): withheld without the Decision
    stage;
11. a group-audience override: a member sees it, a non-member does not, and a
    member added after the override is created sees it.

### 8.2 Graders

- **Offline** (runs inside `pytest backend`, as the existing offline eval
  suite does): compose the actor and the narrator prompt for each case, and
  assert every forbidden needle is absent from **every message** of the actor
  prompt (not only the history section, since a leak through any other
  section is still a leak) and every allowed needle is present. This is
  deterministic and needs no model.
- **Decision gate** (`evals/run.py --gate`, through 02-C5a's
  `decide-epistemic-access` case): the 7 and 10
  classes, with gold answers, graded for correctness and for abstention on
  the no-channel controls. With 01a, it is also reported for cost and
  latency.
- **Live** (`evals/run.py --live`, opt-in): generate the actor's reply and
  grade it for using a forbidden proposition. The grader is a needle check
  first and a decide-based judge second. **Leakage is reported separately
  from recall**, as the draft requires: a configuration that recalls more but
  leaks more is a regression on the leakage line, whatever its recall line
  says.

## 9. Contract

**11-C1: Actor-knowledge classifications, deterministic first, Decision second.**

- *Inputs*: a campaign id; a `Perspective` (narrator, or an NPC ref plus the
  present cast); 09-C1's evidence units carrying the fields of 3.2.
- *Outputs*: an `EpistemicView` (4.5). For an actor, each unit is split into
  slices classed `witnessed | known | suspected | narrator_only | unknown`
  with a basis. For the narrator, each unit carries `known_to`,
  `suspected_by` and `narrator_only`.
- *Guarantees*:
  - the deterministic stage calls no model, takes no `campaign_lock`, and
    writes nothing;
  - hidden posts and director notes are inadmissible for everyone;
  - an override beats every derived signal;
  - `unknown` is never shown to an actor;
  - a model can never produce `witnessed` or `narrator_only`;
  - the Decision stage runs only for actor perspectives, only on eligible
    slices (5.1), at most `EPISTEMIC_DECIDE_ITEMS` per actor turn, behind
    `epistemic_decide`, under the turn-path ceiling;
  - nothing derived is persisted.
- *Failure*: a deterministic read failure (an unreadable `appearances.json`
  or `knowledge.json`) makes the affected actor's slices `unknown`. For the
  overrides file, an unreadable file means no override can be trusted, so
  every slice a *derived* rule would have shown is withheld too. A failed
  override read must not turn an `unaware` correction back into "was there".
  A Decision failure leaves eligible slices `unknown`. Neither fails the turn.

**11-C2: Narrator and actor prompt separation for retrieved history.**

- *Inputs*: an `EpistemicView` handed to compose as `history_view`.
- *Outputs*: for the narrator, `history_recall` (annotated) and
  `history_narrator_only`. For an actor, `history_known` and
  `history_suspected`, rendered from `visible()` only.
- *Guarantees*:
  - an actor-scoped prompt contains no slice whose class is outside `VISIBLE`,
    under any section;
  - the narrator keys are on the actor blanking list;
  - a view whose perspective does not match the compose is refused with
    `ValueError`;
  - basis strings never reach a message;
  - with `history_view=None`, the prompt is byte-identical to the
    pre-09 prompt.
- *Failure*: a render error in one section omits that section, with the same
  per-section policy as `_character_states`, and never the turn.

**11-C3: Authoritative knowledge overrides** (added; the checklist folds this
into C1).

- `<campaign>/knowledge.json` via `store/knowledge.py`.
- Entries are `{audience, status, subject, note, source, created}`, addressed
  by scene identity and post key.
- Precedence is as in 7.2. Group audiences expand through 07-C1, failing
  closed.
- The only writers are `routes/ledger.py` (manual and accepted), journalled
  as manual edits, and a guard holds that.
- Branch copies, rename needs nothing, delete leaves inert entries.

**11-C4: Leakage eval suite** (added; the checklist folds this into C1/C2).

- The cases of 8.1.
- The offline needle-absence grader runs in `pytest backend`.
- The Decision gate runs through `evals/run.py --gate`.
- Live leakage is reported apart from recall.
- 12-C2's eval gate reuses the graders.

## 10. Interaction with repo rules

- **Lock domain** (`test_lock_domain_guard.py`): `store/knowledge.py` goes in
  `DOMAIN_MODULES`, and its `cid`-taking mutators take
  `locks.campaign_lock(cid)`. `store/context/epistemic.py` is read-only and
  takes `best_effort_campaign_lock`. It goes in `OUTSIDE_DOMAIN` with that
  reason.
- **Atomic writes** (`test_atomic_guard.py`): `knowledge.json` is written
  through `store.atomic` only.
- **Import guard**: module-scope imports only. `epistemic.py` binds submodules
  (`from ..appearances import paths as appearances_paths`, `from .. import
  knowledge, responses, playstate`) and never names off a package.
- **Regex prompt guard**: Decision item contexts and slice texts are built
  from the prompt view handed in by 09/08. `epistemic.py` lives in
  `store/context/`, which the guard scans. Its item builder renders a
  template from post content, so it falls under the guard's heuristic rule
  and must reach `view(` or be pinned.
- **Routing and operation guards**: the Decision stage uses 02-C5a's task with
  `operation="decide"` through `require_inference`. That route flips to
  `decide` in the change whose call site decides, which is this one (CLAUDE.md
  safety rule), so 02-C5a and the 11 slice that adds the call land together or
  in that order.
- **Revision token**: override writes are ordinary 2xx route writes. The
  activity middleware stamps them. Nothing in 11 writes from a detached run.
- **Journal and undo**: every override write is `undo.journalled` as
  `manual`, so `store/undo.py` gains a `knowledge` target kind whose restore
  calls `knowledge.restore`.
- **Hide from context**: inadmissible for everyone (4.1). Toggling a post
  hidden needs no 11 hook, because classification happens per turn.
- **Detached runs**: none added. The Decision stage runs inside the turn run
  the request already holds. It adds no exclusion key and no handler.
- **Metering and capture**: `decide` meters per chunk. The capture goes
  through 01b-C1 and is off the decide path.
- **Privacy**: fixtures and eval cases use invented names only. The
  inspector, the capture and the Knowledge ledger show private prose. They are
  local views like the prompt log, and nothing here adds a new export.
- **Android / pydantic v1**: pure Python, no new dependency; route bodies are
  plain `BaseModel` fields.
- **Docs guard**: CONTRIBUTING.md's guard table names
  `test_knowledge_writer_guard.py`.

## 11. Tests and acceptance

**Deterministic classifier** (`backend/tests/test_epistemic_classify.py`):

- R1–R9 one case each, using presence fixtures built with
  `appearances.transitions.appear` / `leave` so intervals are real;
- a split excerpt yields two slices and the actor text holds only the
  covered posts;
- a scene summary for a partial attendee is withheld; for a whole-scene
  attendee it is `witnessed`;
- `authored` proves presence in a record with no intervals;
- a legacy `scenes`-only record is `unknown` (`scene_cast`);
- a hidden post inside an excerpt is dropped for the narrator too;
- a unit from a deleted-and-reissued sid (identity mismatch) is dropped;
- an unreadable `knowledge.json` withholds what a derived rule would have
  shown.

**Overrides** (`test_knowledge_store.py`, `test_ledger_knowledge_routes.py`):

- precedence across actor, group and `*`, post and scene, and timestamps,
  including the withholding tie-break;
- a group override expands through 07-C1 members and fails closed for a
  reclassified group;
- a branch copies post-keyed entries inside `through` and re-keys `r-` ids;
- discard drops them;
- routes refuse a malformed subject (400) or an unknown identity (404);
- writes journal as `manual` and undo restores;
- `test_knowledge_writer_guard.py` fails for a call planted in
  `store/absorb/`.

**Decision stage** (`test_epistemic_refine.py`, with `llm_fakes`):

- the eligibility gate: no call when every unknown lacks a channel, and no
  call for the narrator;
- the item cap and `decision_skipped`;
- each answer row of 5.3;
- a failed call, an overrun of the ceiling, and a 409 `incapable` at
  resolution each leave the slices withheld and the turn completes;
- a reroll replays the snapshot and makes no call.

**Prompt separation** (`test_epistemic_prompt.py`):

- an actor compose with a narrator-only unit: its needle is absent from
  every message;
- the same view handed to a narrator compose: it is present in
  `history_narrator_only` with the annotation;
- a narrator-perspective view handed to an actor compose raises;
- `test_epistemic_basis_never_reaches_the_prompt`;
- `history_view=None` is byte-identical to the baseline (a golden in the
  style of `test_lore_golden.py`, recorded before 09 lands);
- the packer drops `history_narrator_only` before `history_recall`, and
  `history_suspected` before `history_known`.

**Frontend**: the Knowledge ledger section follows the list/detail tests
(row → read-only view, Edit → form, `+ New` → form). The inspector shows a
withheld slice with its basis and posts an accept.

**Acceptance** (the draft's five, made checkable):

1. An actor turn requests perspective-aware retrieval by construction:
   compose refuses a mismatched view.
2. `known`, `suspected` and narrator-only evidence render in distinct
   sections, and narrator-only never in an actor's.
3. Every case in 8.1 except 3 (live) and 7 (Decision) is settled with no
   model call.
4. The Decision stage is off by default, gated, capped and fail-soft.
5. The offline leakage grader passes in `make check`, and the live suite
   reports leakage apart from recall.

## 12. Non-goals

- Re-deciding lore visibility, or adding group refs to lore `known_by`. A
  group channel for lore would be a change to `actor.knows`, which belongs to
  07-C3's integration points or a later spec, not to history retrieval.
- Classifying the current scene. `observed_history` already does it, and
  the perception contract covers the rest.
- Tracker awareness as history evidence. Tracker values are per-post state,
  not 09 units. A later spec may make "was told this value" a `known` basis.
- A durable per-actor knowledge graph, and any automatic write of knowledge
  by absorb, by a model, or by 12.
- Classifying for PCs, or filtering the player's view.
- Any per-unit Decision call for the narrator.

## 13. Interface requirements on parallel specs (missing edges)

These are not in the checklist's edge list for 11 and must be confirmed with
the owning spec before planning:

1. **09-C1** evidence items carry `sid`, scene identity, the transcript
   indices of an excerpt, per-post keys where they exist, and per-post
   prompt-view texts (3.2). Scene-level and fact-level items are marked as
   such.
2. **09-C1 / 09-C3 run before compose and are handed in.** Retrieval (and so
   classification and the Decision stage) must be computable outside
   `campaign_lock` and passed to compose. `_prepare` composes under the lock
   (`routes/character_turns.py:476-477`). The edge `11 ← 09-C3` should be
   added to the checklist.
3. **09-C3 renders nothing into an actor-scoped compose until 11-C2 lands**
   (6.5).
4. **10-C1** accepts a perspective, and for an actor builds its input from
   actor-visible material only (6.4).
5. **02-C5** names the task (assumed `epistemic-classify`) and route, with
   `operation="decide"` and the Decision role, and supplies its `--gate`
   corpus seed from 8.1's cases 7 and 10.

## 14. Open questions

1. **Legacy scenes with no presence intervals (R5).** Withhold by default
   (thin legacy recall), or admit `scene_cast` slices as `witnessed` (rich
   recall, and a leak whenever someone left mid-scene)? *Recommendation:*
   withhold, with the Decision stage as the remedy. Offer a per-campaign
   "trust old cast lists" switch only if evals show legacy recall is
   unusable.
2. **Do `unknown` slices deserve a narrator hint in an actor turn?** For
   example, "Mara may or may not know about the pier; let her ask." That
   would leak the topic. *Recommendation:* no. The actor prompt names nothing
   it withholds.
3. **An opt-in Decision audit of `witnessed` slices** for in-room
   perception (whispers, thoughts)? *Recommendation:* not in this spec. The
   perception contract plus `unaware` overrides cover it. Revisit only if
   live case 3 leaks.
4. **Task and route names for the Decision stage.** *Recommendation:* route
   `epistemic` ("Knowledge checks"), task `epistemic-classify`, Decision
   role, campaign-scoped. 02-C5 owns the final spelling.
5. **Prefilter in 09** (drop units no present actor attended before ranking,
   for actor turns)? *Recommendation:* no for v1. It couples 09 to 11 and
   saves little, since 09's set is already bounded. Revisit if the Decision
   stage's skipped count is high.
6. **Should absorb propose structured "learned" overrides** as review rows
   (a `knowledge` StagedEdit, applied at `PUT /chronicle`)?
   *Recommendation:* yes, but as a follow-up spec once the inspector's accept
   flow shows which overrides people actually write. The writer guard leaves
   room for it.
7. **"As of now" group membership** (7.3) versus membership at the time of
   the scene? *Recommendation:* as of now. 07 has no membership history to
   ask, and "the Watch knows" reads as a standing statement.
