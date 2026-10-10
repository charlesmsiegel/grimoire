# 11. Epistemic history retrieval

**Status:** Draft — spec gate (substitute review) folded in; Codex gate pending.
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
| 07-C1 `members` on the group record, with overlay semantics | 07 | Expanding a group-audience override, through 07's `affiliated` (members and leader, section 7.3) | Hard for group overrides only |
| 07-C3c retrieval projections (`scene_groups`, `co_affiliates`, `prompt_visible`) | 07 | Naming a group audience in the narrator annotation only where the group is prompt-visible | Soft |
| 10-C1 `history_plan` route on Fast, taking a perspective | 10 | An actor call's planner input is actor-visible (section 6.4) | Soft: 11 classifies whatever 09 returns, planned or not |
| 08-C3b `expand(...)`, the caller naming the phase | 08 | The post texts 09-C1 hands over come from it; 11 re-matches them against the transcript (section 4.1) | Soft: 11 reads 09-C1's `EvidencePost`s, not 08 directly |
| 01b-C1 one capture helper at every decide site | 01b | Capturing the Decision stage to the prompt log | Soft |
| 01d-C1 / 01d-C2a / 01d-C2b task policy, trigger with an answer filter, one escalation hop | 01d | The one-hop escalation 02-C5a's policy row declares for `epistemic-access`; once that row exists the call must pass `escalation=` (section 5.2) | Soft: without 01d an answer stands as given, and a failure is `unknown` |
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
- **Chronicle summaries and titles** are written from the narrator's view of
  the whole scene. The absorb prompt asks for a "self-contained paragraph"
  summary (`templates/absorb/system.j2:4-5`) and reads the player's steering
  notes beside the transcript (`:36`). A summary therefore compresses away
  the very source markers ("unseen", "she thinks") the perception rules rely
  on, and can state what no character in the room could perceive.
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
narrator's prompt receives all of it, annotated where access is established.

Concretely:

1. A deterministic classifier turns each retrieved item into slices, each
   with an access class and the basis for it. Obvious cases (an actor was
   there, an actor was not recorded there, the user said so) need no model.
2. A bounded Decision stage refines only cases the classifier cannot settle
   *and* for which a structural channel exists. It never runs for the
   narrator, never upgrades anything to "was there", and never persists.
3. The prompt separates narrator and actor views. An actor-scoped prompt holds
   no evidence outside the actor's access, by construction, not by
   instruction.
4. The user can state authoritative knowledge ("Mara knows what happened in
   that scene", "Winifred never heard that whisper") in one campaign file.
   That file beats every derived classification, and an entry stops granting
   knowledge when the text it was written about changes.
5. An eval suite measures knowledge leakage separately from retrieval recall,
   including the Decision stage's false `known` rate.

**Not the goal:**

- Re-deciding lore visibility. `actor.knows` stays the one rule for world
  info. 11 classifies *history* (09's scene items: header, summary and
  excerpt posts), not lore entries.
- Facts. 09-C1 produces no fact items, and facts reach no actor prompt today
  (1.2). A fact subject in overrides waits for a producer (section 13).
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
    cid: str
    sid: str                   # the scene being played
    identity: str | None       # its scene identity at compose time
    actor: str | None          # "characters:<id>" for an NPC call; None for the narrator
    present: tuple[str, ...]   # every actor and PC ref on stage this turn (post-exclude cast)

    def to_string(self) -> str: ...        # "narrator" or "actor:characters:<id>"
    @staticmethod
    def of_string(s: str, *, cid: str, sid: str, identity: str | None,
                  present: tuple[str, ...]) -> "Perspective": ...
```

- `Perspective.of(cid, sid, actor_ref, scene_cast)` maps `None` and
  `"grimoire"` to the narrator, which is exactly `assemble`'s `actor_scoped`
  test (`assemble.py:227`). `present` is built from the same post-exclude
  cast `_assemble` uses (`assemble.py:193-201`), so an actor the reader
  excluded from context is neither a perspective nor a "known to" name.
- **The string form is 09-C1's and 10-C1's `perspective: str`**:
  `"narrator"` or `"actor:characters:<id>"`. 09 raises `ValueError` for
  anything but `"narrator"` until 11-C1 lands (09 section 13); 11's slice
  lifts that and maps the string through `of_string`.
- Refs use the colon form (`characters:mara`). The appearance record keys
  actors as `characters/mara` (`store/appearances/paths.py:26-27`); the
  conversion is the one `actor.observed_history` already makes
  (`actor.py:26`), in both directions.

### 3.2 Unit

The unit is one 09-C1 `EvidenceItem`, seen through the fields 11 needs:

- `scene: SceneRef`: the sid at retrieval and the scene identity (`None` for
  a scene that predates the field; 09 section 7.2);
- the header fields 09 renders: title, in-fiction date, location;
- `summary: str | None`: the chronicle summary, when 09 includes it;
- `excerpts`: each a tuple of `EvidencePost(index, key, speaker, text)`, the
  transcript index at retrieval, the post key in 08's prefixed form
  (`r-<response_id>` or `p-<post_id>`, with the response `part` for a reply
  split around a roll; `""` for a post with neither), the stored speaker
  label, and the prompt-view text (09 section 8, 08-C3b). Override subjects
  (7.1) use the same prefixed keys, so a post matches by string equality.

11 classifies three parts of an item separately:

| Part | Classified by | Why |
|---|---|---|
| Each excerpt post | the post rules R1–R6 (4.2) | Presence is post-granular |
| The summary | the summary rule S1 (4.3) | It is narrator-authored text about the whole scene |
| The title | follows the summary | A title is a whole-scene product too (drafted by `scene-break-title` from the scene, or chosen by the author) |

The date and location of an item ride with any visible post: an actor who
was in the room knows where and when it was.

### 3.3 Access classes

One class per (perspective actor, part):

| Class | Meaning | Reaches the actor's prompt as |
|---|---|---|
| `witnessed` | The actor was recorded present for the post, or wrote it | "You were there" (under the perception rules) |
| `known` | Established that the actor knows it without having been there: an override, or a Decision answer | "You know this" |
| `suspected` | Established belief, not confirmed: an override, or a Decision answer | "You suspect this (unconfirmed)" |
| `narrator_only` | Established that the actor does not have it: an `unaware` override | Nothing |
| `unknown` | Access cannot be shown | Nothing |

`unknown` is never collapsed into `known`. `ACCESS` is the tuple of the five
classes and `VISIBLE = ("witnessed", "known", "suspected")`. The last two rows
are both withheld from the actor. They differ only in what may happen next:
`unknown` may go to the Decision stage, and `narrator_only` never does.

### 3.4 The narrator's aggregate

For the narrator each item carries one of three states, computed over the
present roster (actors and PCs):

| State | When | Renders |
|---|---|---|
| `attributed` | At least one present actor or PC is `witnessed`, `known` or `suspected` on some part | In `history_recall`, with `Known to:` / `Suspected by:` lines |
| `withheld` | At least one present actor is `narrator_only` on the item by an `unaware` override, and nobody present has any visible part | In `history_narrator_only` |
| `unattributed` | Neither: access is not shown either way | In `history_recall`, **with no annotation**, as 09 renders it without 11 |

"Access not shown" is not "nobody knows". A scene recorded before presence
intervals existed, or a public event the present cast did not attend, renders
as plain recall, never under the narrator-only heading. That keeps 11 from
regressing what the narrator already reads under 09.

### 3.5 Basis

Each slice carries `basis: tuple[str, ...]` from a closed set:

`override`, `override_group`, `override_all`, `authored`, `pc_speaker`,
`presence_interval`, `not_recorded_present`, `scene_cast`, `no_record`,
`summary_needs_grant`, `decision`, `decision_abstained`, `decision_failed`,
`decision_skipped`, `unverifiable`, `moved`.

**The basis never reaches a scene prompt.** It feeds the inspector rows and
the prompt-log capture only, as activation reasons do (CLAUDE.md, "Reasons
never reach the prompt"). The class does reach the prompt, because the model
has to know that a suspicion is unconfirmed. The Decision item (5.2) is a
separate prompt, and it is handed no basis code: 11 passes `basis=()` to
02-C5a's `AccessQuestion`.

## 4. The deterministic stage

`store/context/epistemic.py`, a pure, synchronous module:

```python
def classify(perspective: Perspective, items: Sequence[EvidenceItem], *,
             roster: Sequence[str] = ()) -> EpistemicView
```

`roster` is who to classify for. The default is the perspective's actor; the
narrator path passes `perspective.present`; the Decision stage's channel test
(5.1) passes the present roster too.

### 4.1 One read, and re-matching what 09 handed over

`classify` takes one `locks.best_effort_campaign_lock(cid)` hold, the policy
`_assemble` uses and documents (`assemble.py:162-169`), and reads, once each:

- `appearances.paths.record(cid)` (presence and scene lists);
- `knowledge.read(cid)` (overrides, section 7);
- for each distinct source scene among the items, `scenes_read.read_scene`,
  and `responses.actor_refs(cid, sid)` (`store/responses.py:69-80`).

The best-effort hold can read unlocked under contention, and then may see
two files a moment apart (`store/locks.py`'s docstring). 09 also took its
indices "at retrieval time", and a cut in a source scene between retrieval
and classification renumbers posts (`appearances.paths.remap_presence`).
**So every `EvidencePost` is re-matched against the scene read here before
any rule sees it:**

- the message at `index` must have the same post key when the post's key is
  non-empty, and the same raw-text digest as the message 09 viewed (09
  carries the prompt-view text; 11 compares the view of the message at that
  index, computed over the whole transcript with `regex.view.view(...,
  offset=0, total=len)` so depth-ranged rules see the right depth);
- a post that does not match is dropped for every perspective, basis
  `moved`. Showing less is the safe direction, and a mismatch means the
  appearance record and the evidence no longer describe the same post;
- the same read supplies `excluded`, the director-note flag and the speaker
  stamp. A hidden post or a director note is dropped for everyone
  (`scenes_serialize.in_context`, `serialize.py:95-99`).

The scene whose identity is `None` cannot be verified against a reissued
sid. Its parts are `unknown` (basis `unverifiable`) for every actor, and
render for the narrator as 09 renders them. An item whose identity no longer
resolves to its sid (a deleted scene whose id was reissued, which
`forget_presence` exists to stop, `store/appearances/paths.py:138`) is
dropped for everyone.

**Failure of one read narrows, never widens:**

| Unreadable | Effect |
|---|---|
| `appearances.json` | No R2–R5 for anyone: every post is `unknown`, apart from overrides |
| `responses.json` (`responses._read` raises `ValueError`, `store/responses.py:38-45`) | No R2 (`authored`); posts fall to the other rules |
| `knowledge.json` | No override can be trusted: every part a derived rule would have shown is withheld for actors too, so a failed read can never turn an `unaware` correction back into "was there" |
| A source scene | Its item is dropped for actors and rendered as 09 rendered it for the narrator |

### 4.2 The post rules, in order, for one actor

The first rule that settles a post wins. Posts that end in different classes
**split the excerpt into slices**: contiguous runs of one class. An excerpt is
never shown whole to an actor who was there for only part of it.

| # | Rule | Result | Basis |
|---|---|---|---|
| R1 | An override matches this actor and this post (precedence and fingerprints in 7.2) | `known`, `suspected` or `narrator_only` | `override`, `override_group` or `override_all` |
| R2 | The post is a reply whose `response_id` maps to this actor in `responses.actor_refs` | `witnessed` | `authored` |
| R3 | The actor's record has `presence[sid]`, and an interval covers the post's (re-matched) index | `witnessed` | `presence_interval` |
| R4 | The actor's record has `presence[sid]`, and no interval covers the post | `unknown` | `not_recorded_present` |
| R5 | No `presence[sid]` for the actor, but the scene is in the actor's `scenes` list or the chronicle `cast` for it | `unknown` | `scene_cast` |
| R6 | Nothing above | `unknown` | `no_record` |

For PCs, which are classified for the narrator's annotation only, one rule
joins R2: **R2'**, a user post whose speaker stamp resolves to a present PC's
name proves that PC was there for it (basis `pc_speaker`). A PC's post
carries a `post_id` and a speaker stamp, never a `response_id`, so without
R2' a PC could never be `witnessed` in a scene recorded before intervals.

Why each rule is shaped the way it is:

- **R1 first.** The user's statement is authoritative over every derived
  signal, presence included. That is what makes the "present, but the secret
  was whispered privately" case fixable without a model: an `unaware`
  override on the whispered post.
- **R2 before R3.** A reply an NPC wrote proves presence at that post even in
  a record whose intervals were lost.
- **R3 is presence, not perception.** A `witnessed` excerpt renders under the
  perception rules the current scene already relies on (`response_actor.j2`,
  "What this actor can perceive"): the transcript may narrate another
  character's private thoughts, and being in the room does not grant them.
  That defence holds for **excerpts only**, because an excerpt keeps the
  transcript's own source markers ("unseen", "she thinks"). It does not hold
  for a summary (4.3).
- **R4 is "not recorded present", not "absent".** `begin_observing` anchors a
  legacy cast member's interval "from now" (`store/appearances/paths.py:153-172`),
  so the posts before that anchor are unrecorded rather than missed. The
  inspector says "not recorded as present", never "was absent".
- **R4 and R5 are `unknown`, not `narrator_only`.** Absence from a scene is
  not proof of not knowing. Someone may have told the actor later. Only an
  override or the Decision stage establishes that.
- **R5 does not count as presence.** It applies where the record has a scene
  but no intervals, which is what any scene recorded before presence
  intervals looks like. `actor.observed_history` already refuses to treat it
  as observation (`actor.py:1-5`, `:29-31`). The tracker takes the opposite
  convention for *current* state, counting such a cast member as present
  throughout (`store/tracker/walk.py:159-174`), and that is right there: a
  tracked value about someone's visible clothing does not leak a secret. For
  history it would make a legacy NPC omniscient about every scene they ever
  stood in, arrivals and departures included. Open question 1.

### 4.3 The summary rule

**S1. Presence never makes a summary, or the item's title, visible to an
actor.** A summary or title is visible to an actor only through an override
on the whole scene (`knows` or `suspects`, with a matching scene fingerprint,
7.1) or a Decision answer about the summary (5.1). Otherwise it is `unknown`,
basis `summary_needs_grant`, and the actor gets their R1–R3 excerpt posts of
that scene instead.

The reason is 1.4: absorb writes the summary from the narrator's view, with
the steering notes in hand. "Winifred, unseen, slips the poison into the cup"
in the transcript becomes "Winifred poisoned the cup" in the summary, and
nothing a whole-scene attendee could perceive says so. The offline needle
grader would not catch it either, since a summary paraphrases. So presence,
which is the only evidence the classifier has, is not allowed to vouch for
it.

An actor-rendered item header therefore shows the date and location of an
item with at least one visible post, and the title only when the summary is
visible.

### 4.4 The narrator

For the narrator perspective every admissible part is shown. `classify` runs
the rules once per present actor and PC to compute the aggregate of 3.4 and
the `Known to` / `Suspected by` names (a PC counts only through R1–R3 and R2').
No Decision call is ever made for the narrator: the annotation is a reading
aid for the writer, and a model call per actor per item per narrator turn
would cost more than everything else on the turn.

### 4.5 Rendering a split excerpt

A slice's text is rebuilt from the re-matched posts' prompt-view texts, in
order, with the same line renderer 09 uses for a whole excerpt (09 section
9.1). A split never re-reads the scene: the one read of 4.1 is the source.

### 4.6 Result shape

```python
@dataclass(frozen=True)
class Slice:
    part: str                     # "posts" | "summary"
    posts: tuple[int, ...]        # re-matched indices; () for the summary
    access: str                   # one of ACCESS
    basis: tuple[str, ...]
    text: str

@dataclass(frozen=True)
class Classified:
    item: EvidenceItem
    slices: tuple[Slice, ...]             # for the perspective's actor
    header: dict                          # what this perspective may see of the header
    state: str                            # narrator aggregate: attributed | withheld | unattributed
    known_to: tuple[str, ...]
    suspected_by: tuple[str, ...]

@dataclass(frozen=True)
class EpistemicView:
    perspective: Perspective
    items: tuple[Classified, ...]         # in 09's order
    decided: int                          # slices settled by the Decision stage
    pending: tuple[tuple[str, str, tuple[int, ...]], ...]   # (scene key, part, posts) eligible for Decision

    def visible(self) -> tuple[Classified, ...]: ...
```

`visible()` returns, for an actor, only items with at least one slice in
`VISIBLE`, each holding only those slices; an item with none is omitted
entirely, ref and header included. For the narrator it returns every item. A
renderer for an actor prompt may read only `visible()`, and section 11 holds
that with a test. The JSON form (inspector, capture) is the same fields with
tuples as lists.

## 5. The Decision stage

```python
# routes/epistemic.py (helpers; no router)
async def refine(view: EpistemicView, *, client: LLMClient, post: int | None,
                 round_id: str, round_budget: RoundBudget) -> EpistemicView
```

It runs on the turn path after retrieval and before `_prepare`, outside any
campaign lock, as the speaker pick does (`routes/character_turns.py:728-761`):
the resolution and item building happen in the threadpool, and the capture
goes back to the threadpool.

### 5.1 Which parts may be asked about

A part is **eligible** only when all of these hold:

1. the perspective is an actor (never the narrator, 4.4);
2. its class is `unknown` with basis `not_recorded_present`, `scene_cast`,
   `no_record` or `summary_needs_grant`. A `narrator_only` from an override
   is never asked about, and neither is an `unverifiable` or `moved` part;
3. a **structural channel** exists, meaning at least one of:
   - (a) the actor stood in that scene: it is in the actor's `scenes` list,
     the chronicle `cast`, or the actor has any interval there. This is the
     only gate for a summary;
   - (b) the evidence set holds a post the actor `witnessed` in a **later**
     scene in play order, in which a member of the present roster who
     deterministically has access to this part was also recorded present
     (a plausible teller). "Deterministically" means `classify(...,
     roster=perspective.present)` with no Decision, so (b) costs one
     classifier pass over the roster and no model call.

The draft-era condition "the actor's own `state.md` has knows or suspects" is
dropped: after one absorb it is true of nearly every NPC and says nothing
about the part. The actor's own state still goes into the item's context
(5.2), where it can inform the answer.

`witnessed` posts are **not** eligible. Auditing them would put every recalled
scene through a model call on every turn. In-room perception is handled by
the perception contract and by `unaware` overrides. Open question 3.

### 5.2 Items, call and escalation

The items are built by 02-C5a's kit:

```python
# store/epistemic_access.py (02-C5a)
questions = [AccessQuestion(actor_ref=..., actor_name=...,
                            evidence_ref=<scene key + part + posts>,
                            excerpt=<the part's text, prompt view, capped>,
                            basis=())                  # basis codes never enter a prompt (3.5)
             for each eligible part]
items = epistemic_access.build_items(questions)
```

11 appends to each excerpt, inside the kit's bound, the actor's own
`state.md` knows/suspects (capped) and, for a (b) channel, up to two of the
later witnessed posts, so the item sees the channel that made it eligible.
The context never carries another actor's `state.md`, a dossier, or any part
the actor cannot see apart from the one being asked about, because the item
is itself a prompt that a capture shows.

```python
decision = await common._bounded_call(            # one outer ceiling, 5.4
    operations.decide(
        "epistemic-access", items, client=client, resolved=resolved,
        campaign=cid, scene=sid, post=post, round_id=round_id,
        capture=<01b-C1 helper>,
        escalation=lambda: run_in_threadpool(
            lambda: escalation_inference("epistemic-access", cid))),
    ceiling=<the stage ceiling>)
access = epistemic_access.access_of(questions, decision.items)
```

- The task and route are 02-C5a's: task `epistemic-access` on the `epistemic`
  route (`operation="decide"`, `default_role="decision"`), resolved with
  `require_inference("epistemic-access", cid, operation="decide")`
  (`inference.py:684-714`).
- **`escalation=` is required, not optional.** 02-C5a's policy row escalates
  (`escalate_to="primary"`), and 01d-C2b makes `decide` raise `ValueError`
  before any meter opens when a policy escalates and the call passes no
  `escalation`. Without it every refine would fail, and the fail-soft rule
  below would hide that permanently. `test_epistemic_refine.py` asserts a
  refine call reaches a meter.
- No rationale is requested (`explain=""`). Nothing stands on one, and a
  native backend has none to give (CLAUDE.md, decide section).

### 5.3 Reading the answer

| `access_of` result (02-C5a) | Result | Basis |
|---|---|---|
| `known` | `known` | `decision` |
| `experienced` | `known` (never `witnessed`) | `decision` |
| `suspected` | `suspected` | `decision` |
| `narrator_only` | stays `unknown` (withheld) | `decision` |
| `unknown` (abstained, refused, unreadable, `NOT_AN_OPTION`, an error, an item never reached; 02-C5a's fail-closed mapping, including a failed or skipped escalation hop) | stays `unknown` | `decision_abstained` |
| the call failed or overran (`LLMError`, `BudgetRefused`, `DecideRequestError`, the stage ceiling, 409 `incapable` at resolution) | every eligible part stays `unknown` | `decision_failed` |

- **A model never produces `witnessed` or `narrator_only`.** Presence is a
  structural fact, and `narrator_only` is the user's word.
- **An answer is taken as given, apart from 02-C5a's escalation.** No
  probability threshold is invented here. A distribution is never sampled
  (01c-C4).
- **The stage is fail-soft.** A failure leaves every part withheld and the
  turn proceeds. Unlike the speaker pick, whose `LLMError` propagates, a
  failed classification has a safe default.

### 5.4 Time, cost and when it does not run

- **One outer ceiling for the whole stage.** `decide`'s `around` wraps each
  facade call, not the stage, so eight native items, an escalation hop and a
  fallback stage could take several ceilings (CLAUDE.md works out the same
  arithmetic for the continuity sweep). So the whole `decide` call, escalation
  included, is wrapped in one `common._bounded_call` with
  `config.llm_call_budget()` (`routes/common.py:555-600`). An overrun is
  `decision_failed`. The abandoned call unwinds on its own and may still
  file its ledger rows: that is `_bounded_call`'s documented behaviour, and
  the ceiling bounds the wait, not the spend.
- **Caps.** At most `EPISTEMIC_DECIDE_ITEMS = 8` parts per actor step (one
  structured chunk, `decisions.MAX_ITEMS_PER_CALL`), and at most
  `EPISTEMIC_ROUND_ITEMS = 16` across a round (two chunks), shared first come
  first served by the round's actor steps (`RoundBudget`, carried on the
  round record so a resumed round does not reset it). Parts beyond either cap
  stay `unknown` with basis `decision_skipped`, chosen in 09's rank order.
  Both constants are argued from the chunk size alone and are to be tuned
  against real prompts later.
- **Not on a replay.** When the round record has a `pending_response` whose
  status is not complete (a retry, a recovery, a roll continuation),
  `_prepare` replays that response's frozen snapshot
  (`routes/character_turns.py:479-495`). The turn path reads that state
  outside the lock first and skips retrieval, `classify` and `refine`, so a
  replay pays for nothing it would throw away. `_prepare` re-checks it under
  the lock as today.
- **Off by default.** The stage runs only when the `epistemic_decide` config
  key is `on`, following the shape of `perception_rider`
  (`store/config.py:89`, `:493`). The default stays `off` until 02-C5a's
  `decide-epistemic-access` gate case passes its recorded bar (8.2).
- Every call is metered by `decide`'s own meter under the task. Spend counts
  against the campaign like any other.

### 5.5 Nothing derived is persisted

A Decision answer is used for the turn being composed and nothing else. It
lands in three places, none of them authoritative:

- the **frozen prompt snapshot** of the reply (`responses.prepare`). A reroll
  replays the snapshot rather than recomposing, so it neither re-classifies
  nor pays again;
- the **prompt-log capture**, through 01b-C1's capture helper, whose outcome
  section is 01b's envelope (the convention the speaker capture now uses,
  01b section 3.4). It records the parts asked, the answers, and the
  `access_of` mapping;
- the turn's capture view, from which the user may **accept** an answer as
  an override (7.4).

No cache of answers is kept across turns. A cache keyed on inputs would hold
a model's output as though the inputs determined it, which is the thing 03
refuses to cache.

## 6. Retrieval for a perspective, and prompt separation (11-C2)

### 6.1 Retrieval runs per compose step

09's `gather` runs once per round for a narrator compose (09 section 10). With
11, history becomes perspective-specific, so:

- **Retrieval runs per actor step.** Each compose step (one per assigned
  actor, `routes/character_turns.py:84-117`) calls `gather` with that step's
  perspective string, between `_select` and `_prepare`. A round with three
  NPC steps runs three retrievals: three query embeddings and, with rerank
  on, three rerank calls. That cost is real and is stated on the setting, and
  it is bounded by `history_recall_depth` and 09's phase deadline per step.
  Reusing one narrator-shaped retrieval for every actor was rejected: its
  query window and seeds are the narrator's, so the ranking itself would be
  steered by material the actor may not have (6.4).
- **For an actor, 09's query is built from actor-visible material.**
  `query.build` reads the actor's `observed_history` (`actor.py:25-36`), not
  the narrator's tail, and its structural seeds are restricted to the present
  roster, the actor's own relationship targets, and records named in that
  observed window. Thread and commitment seeds are not offered: the actor
  contract blanks `plot_lines` and `commitment_lines`
  (`assemble.py:527-530`).
- **`classify` runs inside `gather`, after every retrieval round**, so 10's
  planner and its sufficiency check (10-C3) read `view.visible()`, never the
  raw evidence. 10-C1's planner input for an actor is the observed window,
  the present roster and the visible evidence, with no records or threads
  offered.
- **`refine` runs once per step, after the last round**, under 5.4's
  ceilings.
- **`history_view` replaces `history=`.** Once 11 lands, compose takes
  `history_view: EpistemicView | None`, and 09's `history=` keyword is
  removed. The narrator's view wraps the narrator's evidence, so there is one
  path to the section.

### 6.2 The sections

- **Narrator** (`actor_ref` `None` or `"grimoire"`):
  - `history_recall` (09-C3's section) lists every item whose state is
    `attributed` or `unattributed`. An `attributed` item is followed by
    `Known to: Mara, Seraphine` / `Suspected by: Winifred` lines. An
    `unattributed` item renders exactly as 09 renders it.
  - `history_narrator_only` lists the `withheld` items, under a heading that
    carries the instruction `secret_world_info_bodies` already uses for
    secret lore: the writer may use it for continuity, and no character acts
    on it unless it reaches them on the page. An item is in one of the two
    sections, never both (09's no-repeat rule, 09 section 9.4).
- **Actor**: two sections, rendered only from `EpistemicView.visible()`.
  - `history_known` ("What {name} remembers or knows"): `witnessed` slices
    under "You were there" and `known` slices under "You know this".
  - `history_suspected` ("What {name} suspects, unconfirmed"): `suspected`
    slices.
  - There is no narrator-only section in an actor prompt, under any name. An
    instruction is weaker than an absence, which is the current contract's
    own reasoning (1.2).

### 6.3 Templates and wording

New templates under `templates/scene/sections/`:
`history_narrator_only.j2`, `history_actor_known.j2` and
`history_actor_suspected.j2`. 09-C3's `history_recall.j2` gains the
annotation lines. The actor templates restate, in one sentence, the
perception rule from `response_actor.j2`: being there means what could be
seen or heard, not other people's private thoughts. The labels "You were
there", "You know this" and "suspected, unconfirmed" are also what the
perception rider's `known` field can cite as an "existing source".

`scripts/verify_templates.py` and the offline eval needles (CLAUDE.md,
"After editing anything in `templates/`") cover the new templates. The
leakage suite (section 8) adds needles that must be **absent**.

### 6.4 Placement in the build and the contract

- `assemble._assemble` gains `history_view`, threaded from `compose_turn`
  and `compose_director_turn`. Compose never classifies and never calls a
  model. It renders what it is handed. With `history_view=None`, no history
  section renders.
- **The actor blanking list grows.** 09-C3's `history_recall` data keys and
  `history_narrator_only` join the tuple at `assemble.py:528`. On an
  actor-scoped compose they are emptied even if something populated them.
  The actor keys are set only from `history_view.visible()`. This is the
  same belt-and-braces shape as the `known_entries` re-application at
  `assemble.py:440-442`.
- **A perspective mismatch is refused.** Compose raises `ValueError` before
  rendering when the view's `cid`, `sid`, scene identity or actor differs
  from the compose's (narrator matching narrator). A view built for Mara in
  another scene, or before a cast change that removed her, must fail loudly
  rather than render.
- **Layout.** All four sections follow `history_recall`'s layout switch: a
  reader who turned "Recalled history" off gets none of them, in any prompt.
  The three new ids are catalog entries placed directly after
  `history_recall`, and `layout.py`'s upgrade rule places them in a saved
  layout.
- **Packer.** All four sections are in 09-C3's tier, and **each carries its
  own `shed` hook** with one unit per item, so the packer drops items rather
  than whole sections. 09's pin test (that only `_world_info_section` and
  `history_recall` shed) becomes the set of five. Within the tier,
  `history_narrator_only` gives way before `history_recall`, and
  `history_suspected` before `history_known`.
- **The inspector** (`context_breakdown`) composes a hypothetical turn with
  no spend (09 section 9.5). For it 11 runs `classify` only, never `refine`,
  and shows, per item, the class and basis of each part and, for an actor
  turn, the withheld parts with their basis. Decision answers appear only in
  the turn's prompt-log capture. The basis appears in the inspector and the
  capture, never in a message (`test_epistemic_basis_never_reaches_the_prompt`).

### 6.5 Before 11 lands

Until 11-C2 is implemented, 09-C3 renders history **only into
narrator-scoped prompts** (09-C3's headline, and the checklist's cross-spec
decision "History in NPC prompts"). It is listed as a back-edge in "Required
by".

## 7. Authoritative knowledge overrides (11-C3)

### 7.1 Storage

`store/knowledge.py` owns `<campaign>/knowledge.json`:

```json
{"version": 1,
 "entries": {
   "k-3f9a1c2e": {
     "audience": "characters:mara",
     "audience_name": "Mara",
     "status": "knows",
     "subject": {"kind": "posts", "scene": "<scene identity>",
                 "posts": [{"key": "r-6d1e", "variant": "v2", "digest": "<sha256/16>"},
                           {"key": "p-8a40", "variant": "", "digest": "<sha256/16>"}]},
     "note": "Winifred told her on the walk back.",
     "source": "manual",
     "created": "2026-10-09T12:00:00Z"},
   "k-77b0e4d1": {
     "audience": "*",
     "status": "unaware",
     "subject": {"kind": "posts", "scene": "<scene identity>",
                 "posts": [{"key": "r-91c2", "variant": "v1", "digest": "<sha256/16>"}]},
     "note": "Whispered aside; nobody else heard.",
     "source": "manual",
     "created": "2026-10-09T12:05:00Z"},
   "k-0c55aa90": {
     "audience": "groups:saltmarch-watch",
     "audience_name": "The Saltmarch Watch",
     "status": "knows",
     "subject": {"kind": "scene", "scene": "<scene identity>", "digest": "<transcript hash>"},
     "note": "", "source": "accepted", "created": "2026-10-09T12:10:00Z"}}}
```

- `audience` is an NPC ref (`characters:` only; PCs are not generated, 2), a
  group ref (`groups:<id>`), or `"*"`. `audience_name` is the display name at
  write time, for an actor or group audience (7.5).
- `status` is `knows`, `suspects` or `unaware`.
- **Subjects are addressed by scene identity and fingerprinted content,
  never by sid or index.** A sid moves on rename and is reissued after a
  delete; an index moves on every cut.
  - `posts`: each post by its 08-form key (`p-<post_id>`, or `r-<response_id>`
    with its `part`), the
    response's active variant id at write time (a reroll, a swipe and Keep
    writing all keep the `response_id`; the tracker keys `r-{rid}-{vid}` for
    this reason, `store/tracker/paths.py:44-47`), and a digest of the stored
    (raw) post text at write time.
  - `scene`: the whole scene, with `responses.transcript_hash` of the scene's
    messages at write time (`store/responses.py:118`).
- **A `knows` or `suspects` entry matches only while its fingerprint holds**:
  the post's current variant and raw-text digest, or the scene's current
  transcript hash. A swipe, a message edit, a retcon or a hidden-post toggle
  makes it stop matching, which fails closed. An `unaware` entry keeps
  matching through such changes, since withholding is the safe direction.
  The ledger lists a stale entry as "the text changed since; confirm again",
  with a one-click re-stamp that shows the current text.
- `source` is `manual` (typed by the user) or `accepted` (a captured
  classification the user accepted, 7.4). Both are authoritative.
- **`note` never renders into any prompt**, actor or narrator. It is user
  prose and often narrator knowledge ("Winifred told her ..."). It is shown
  in the ledger only.
- Ids are `k-` plus 8 hex characters from `uuid4`.

The module follows the ledger conventions: a tolerant `read` (an unparseable
file reads as empty, a malformed entry is skipped), mutators that refuse a
document of the wrong shape rather than publishing an empty file over it (the
`facts._read_ledger` rule, `store/facts.py:87-101`), and whole-file writes via
`store.atomic` under `locks.campaign_lock(cid)`.

### 7.2 Precedence when several entries match

For one actor and one part, collect the matching entries (fingerprints
holding), then choose by:

1. **subject specificity**: a post-listed entry beats a whole-scene entry.
   So the whisper's `unaware` on one post is not overridden by a broad
   "the Watch knows that scene";
2. **audience specificity**: the actor's own ref, then a group the actor is
   affiliated with (7.3), then `"*"`;
3. **newest `created`**;
4. on an exact tie, `unaware` before `suspects` before `knows`, the
   withholding direction, as #116's filter breaks ties.

**`"*"` means every actor except a post's author.** An `"*"` entry never
matches the actor that R2 says wrote the post, so "nobody else heard" does
not silence the whisperer about their own line.

### 7.3 Group audiences

A `groups:<id>` audience is expanded through 07's `affiliated` set: the
group's effective members (07-C1, campaign overlay over world) **and its
leader**, following 07's recommendation (07 section 4.4). A leader nobody
listed as a member is still someone "the Watch knows" plainly includes.

- **As of now.** Expansion reads current membership, so someone who joins
  later is covered. The editor says so, and says that a world-level
  membership edit changes what every campaign of that world reads through the
  entry (the campaign's overlay can diverge where that is wrong).
- **Fails closed.** A group ref that no longer resolves, or that resolves to
  a group whose display name differs from `audience_name`, expands to nobody
  for `knows`/`suspects`.
- Co-membership alone is never knowledge. Only an entry the user wrote makes
  a group an audience.

### 7.4 Writers

- **Hand edits** go through `routes/ledger.py`, the existing home of ledger
  hand edits, under the campaign lock and wrapped in `undo.journalled` as
  `manual` (CLAUDE.md, "Hand edits to the ledger go through
  `routes/ledger.py`"):
  `POST /campaigns/{cid}/ledger/knowledge`,
  `PUT /campaigns/{cid}/ledger/knowledge/{kid}`,
  `DELETE /campaigns/{cid}/ledger/knowledge/{kid}`.
  Bodies are plain `BaseModel` fields, dumped via `routes.common._dump`.
  - The client names the scene by sid; the route resolves it to an identity
    with `ensure_identity` **under the campaign lock** (it may write the
    identity line, as a scene with none needs), so a legacy scene can be
    addressed.
  - `find_by_identity` raising `UnreadableError` (a scan that could not
    finish, for example a synced store mid-write) answers 409 `busy`, never
    404.
  - Post keys are validated against the scene at write time, and the route
    computes the variant and digest itself; a client never supplies a
    fingerprint.
  - A malformed audience, status or subject is 400; an unknown scene is 404.
- **Accept from a capture.** The turn's prompt-log capture view offers, on a
  captured Decision answer or a classified part, "Mark as known / suspected
  / unaware to {name}". It posts the same `POST` with `source: "accepted"`.
  - `knows`/`suspects` is **refused (400 `unkeyed`) for a part with any
    keyless post**: storing it as a whole-scene subject would grant the whole
    scene, summary included, which the user did not judge. `unaware` is
    allowed there and is stored as a whole-scene `unaware`, which only
    withholds more.
  - Accepting a summary answer stores a whole-scene entry.
- **Absorb does not write overrides in this spec.** Absorb already records
  what a character learned as `knows`/`suspects` prose (1.4). Structured
  "learned" rows from absorb are open question 6.
- **No model writes this file.** `test_knowledge_writer_guard.py` (modelled
  on `test_absorb_writer_guard.py`, resolving import bindings) fails if
  anything outside `routes/ledger.py`, `store/knowledge.py` and the delete
  hooks of 7.5 calls `knowledge.add`, `update`, `remove`, `restore` or
  `forget_audience`. It names `store/context/`, `store/absorb/`,
  `store/continuity/` and 12's investigation package explicitly.

The ledger UI follows the list/detail page pattern (CLAUDE.md, Frontend): a
"Knowledge" section with `.editor-list` rows, a read-only detail view (the
audience as a clickable chip, the scene as a chip to the scene, the status as
a plain chip, the stale flag as a field hint, the note rendered as markdown),
and an explicit Edit step. Tests cover row → view, Edit → form, and `+ New` →
form.

### 7.5 Lifecycle of referenced records

- **Scene rename**: nothing to do, since entries hold identities.
- **Scene delete**: an entry whose identity no longer resolves matches
  nothing (4.1 drops the items). The ledger lists it under "points at a
  deleted scene", with Remove. It is not pruned automatically: a
  hand-written statement is not deleted behind the user's back.
- **Actor delete**: `overlay`'s campaign actor delete already drops the
  appearance record so a later create under the same id inherits nothing
  (#225, `store/overlay.py:1618`). `knowledge.forget_audience(cid, ref)` runs
  beside it and removes that actor's entries, journalled with the delete.
  Where an actor disappears by another path (a world-level delete the plan
  must trace), `audience_name` is the backstop: an entry whose audience's
  current name differs from it does not match for `knows`/`suspects`. A
  same-named recreation is undetectable by this check, and the plan must
  find the fan-out rather than rely on it.
- **Group delete or reclassify**: as 7.3, plus `forget_audience` from the
  campaign's group delete.
- **Branch**: `store/branch.branch_scene` copies keyed records through each
  owner's helper (`store/branch.py:165-230`, as
  `regex_rewrites.copy_for_branch` does at `:218`).
  `knowledge.copy_for_branch(cid, src_identity, dst_identity, rid_map,
  copied_keys)` copies post entries whose posts are all among the copied
  posts, re-keying `r-` ids through `rid_map` and keeping variant and digest.
  Whole-scene entries are not copied: the branch's transcript hash differs
  by construction. `branch.discard` drops the branch's entries.
- **Campaign fork**: the file is campaign-local and travels with the fork
  copy, which keeps scene identities (`store/fork.py`).
- **The frozen campaign** has no `knowledge.json`. An absent file is no
  overrides, and `snapshot.json` is unchanged.

## 8. Evals for leakage (11-C4)

### 8.1 Corpus

`evals/cases/epistemic/` holds synthetic campaigns built with placeholder names
only (Seraphine, Mara, Winifred, Realm, Saltmarch), through the real store
APIs as 09's suite builds its recipes. Each case is a store recipe, an
evidence set, a perspective, and two sets of needles: propositions the actor
**may** see, and propositions that **must not** appear.

1. The actor was not recorded present for a revelation (R4): absent.
2. Present, but the secret was whispered privately, with an `unaware`
   override on the whisper (R1 over R3): absent. The whisperer still sees
   their own line.
3. As 2 **without** the override (R3): offline the excerpt is visible, which
   is expected; live, the reply must not use it. Only the live arm can catch
   this; the perception contract is what is being tested.
4. The actor explicitly knows (`knows` override): under "You know this".
5. The actor only suspects: under the suspicion heading only.
6. A `withheld` item: in `history_narrator_only` in the narrator prompt and
   in no actor prompt.
7. A later scene in which the actor is told: deterministic classification
   leaves it `unknown`; with the Decision stage, the gold answer is `known`.
8. A whole-scene attendee, with a summary that states a secret action that
   was narrated but could not be perceived: the summary and the title are
   withheld (S1), and the attendee's excerpts are shown.
9. A partial attendee: only the attended posts appear.
10. A legacy scene with no intervals (R5): withheld from the actor without
    the Decision stage; **in the narrator prompt it renders as plain recall,
    unannotated** (`unattributed`).
11. A PC-only legacy scene in a narrator prompt: the PC is in `Known to`
    through R2'.
12. A group-audience override: a member sees it, the leader sees it, a
    non-member does not, a member added later does; a recreated group of
    another name does not.
13. Fingerprints: a `knows` override on a reply, then a swipe to a variant
    that holds the secret: withheld. A `knows` on a scene, then an edit:
    withheld. An `unaware` survives both.
14. A cut in a source scene between retrieval and classification: no post
    becomes `witnessed` that was not.
15. Decision negatives: the actor met someone who knows, later, but the
    later scene shows they were not told. Gold: not `known`.

### 8.2 Graders and the gate

- **Offline** (runs inside `pytest backend`): compose the actor and narrator
  prompts for each case and assert every forbidden needle is absent from
  **every message** of the actor prompt, and every allowed needle present.
  This is deterministic and needs no model. It does not exercise the Decision
  stage, and the spec says so rather than implying otherwise.
- **Decision gate** (`evals/run.py --gate`, through 02-C5a's
  `decide-epistemic-access` case): seeded from cases 7, 10 and 15, all with
  a structural channel present, because production never sends a no-channel
  item (5.1). **The leakage line is the false-`known` rate** (an answer of
  `known`, `experienced` or `suspected` where the gold is not), reported
  apart from accuracy. Its bar is set in the plan before the gate is run,
  and `epistemic_decide` stays `off` until a recorded run meets it.
  01a reports cost and latency beside it.
- **Live** (`evals/run.py --live`, opt-in): generate the actor's reply and
  grade it for using a forbidden proposition (a needle check first, a
  decide-based judge second). **Leakage is reported separately from
  recall**: a configuration that recalls more but leaks more is a regression
  on the leakage line, whatever its recall line says.

## 9. Contract

**11-C1: Per-actor classes, deterministic first, with a capped Decision pass that is off by default.**

- *Inputs*: a `Perspective` (3.1, or its string form shared with 09-C1 and
  10-C1); 09-C1's evidence items.
- *Outputs*: an `EpistemicView` (4.6). For an actor, each item's posts and
  summary are classed `witnessed | known | suspected | narrator_only |
  unknown` with a basis, and `visible()` omits every item with no visible
  slice. For the narrator, each item is `attributed`, `withheld` or
  `unattributed`, with `known_to` and `suspected_by`.
- *Guarantees*:
  - the deterministic stage calls no model, takes no `campaign_lock`, writes
    nothing, and re-matches every post against one read of its scene;
  - hidden posts and director notes are inadmissible for everyone;
  - an override whose fingerprint holds beats every derived signal;
  - presence never makes a summary or title visible to an actor;
  - `unknown` is never shown to an actor;
  - a model never produces `witnessed` or `narrator_only`;
  - the Decision stage runs only for actor perspectives, only on eligible
    parts with a structural channel, within 8 items per step and 16 per
    round, under one stage ceiling, behind `epistemic_decide`, never on a
    replay, and passes `escalation=` as 02-C5a's policy requires;
  - nothing derived is persisted.
- *Failure*: the table in 4.1. A Decision failure leaves eligible parts
  `unknown`. Neither fails the turn.

**11-C2: Narrator and actor prompt separation, refused on a mismatched perspective.**

- *Inputs*: an `EpistemicView` handed to compose as `history_view`, built by
  `gather` per compose step.
- *Outputs*: for the narrator, `history_recall` (attributed and unattributed
  items) and `history_narrator_only` (withheld items). For an actor,
  `history_known` and `history_suspected`, rendered from `visible()` only.
- *Guarantees*:
  - an actor-scoped prompt contains no slice outside `VISIBLE`, under any
    section, and no header field the actor may not see;
  - the narrator keys are on the actor blanking list;
  - a view whose cid, sid, scene identity or actor differs from the compose
    is refused with `ValueError`;
  - all four sections follow `history_recall`'s layout switch and shed per
    item;
  - basis strings and override notes never reach a message;
  - with `history_view=None`, the prompt is byte-identical to the
    pre-09 prompt.
- *Failure*: a render error in one section omits that section, with the
  per-section policy of `_character_states`, never the turn.

**11-C3: `knowledge.json` overrides through `routes/ledger.py`.**

- `<campaign>/knowledge.json` via `store/knowledge.py`.
- Entries are `{audience, audience_name, status, subject, note, source,
  created}`, addressed by scene identity and fingerprinted content.
- Precedence: subject first, then audience, then recency, then the
  withholding tie-break. `"*"` excludes a post's author.
- `knows`/`suspects` stop matching when their fingerprint fails, and
  `unaware` does not.
- Group audiences expand through `affiliated` (members and leader), failing
  closed.
- The only writers are `routes/ledger.py` and the delete hooks, journalled
  as manual edits, and a guard holds that.

**11-C4: A leakage eval suite.**

- The cases of 8.1.
- The offline needle-absence grader runs in `pytest backend`.
- The Decision gate runs through `evals/run.py --gate`, with channel-present
  negatives and the false-`known` rate as its leakage line.
- Live leakage is reported apart from recall.
- 12-C2c's eval gate reuses the graders.

## 10. Interaction with repo rules

- **Lock domain** (`test_lock_domain_guard.py`): `store/knowledge.py` goes in
  `DOMAIN_MODULES`, and its `cid`-taking mutators take
  `locks.campaign_lock(cid)`. `store/context/epistemic.py` is read-only and is
  declared in **no** list: the guard surveys modules that mutate, and a
  read-only module in `OUTSIDE_DOMAIN` fails
  `test_the_declaration_has_no_phantom_modules` and
  `test_modules_declared_outside_are_really_outside`
  (`backend/tests/test_lock_domain_guard.py:2332-2372`). That is the position
  09 takes for `store/history/`.
- **Atomic writes** (`test_atomic_guard.py`): `knowledge.json` is written
  through `store.atomic` only.
- **Import guard**: module-scope imports only. `epistemic.py` binds
  submodules (`from ..appearances import paths as appearances_paths`,
  `from .. import knowledge, responses`) and never names off a package.
- **Regex prompt guard**: the heuristic rule needs a scene read and a
  template render in one body, which `epistemic.py`'s renderer does not have,
  and the Decision item builder is 02-C5a's `store/epistemic_access.py`,
  outside the scanned directories. So both are **pinned by name**:
  `epistemic`'s slice renderer, and `epistemic_access.build_items`, with the
  guard's `_sources` extended to `store/epistemic_access.py` if 02-C5a has not
  already done so.
- **Routing and operation guards**: the Decision stage uses 02-C5a's task
  with `operation="decide"` through `require_inference`. That route flips to
  `decide` in the change whose call site decides (CLAUDE.md safety rule), so
  02-C5a and the 11 slice that adds the call land together or in that order.
  The call passes `escalation=` exactly when its task's policy escalates,
  which is the routing guard's 01d rule.
- **Revision token**: override writes are ordinary 2xx route writes, stamped
  by the activity middleware. The delete hooks run inside routes that are
  already stamped. Nothing in 11 writes from a detached run.
- **Journal and undo**: every override write is `undo.journalled` as
  `manual`; `store/undo.py` gains a `knowledge` target kind whose restore
  calls `knowledge.restore`.
- **Hide from context**: inadmissible for everyone (4.1). A toggle also
  changes the scene's transcript hash, so a whole-scene `knows` stops
  matching until it is confirmed again.
- **Detached runs**: none added. Retrieval, classification and refinement run
  inside the turn run the request already holds, with no exclusion key and no
  handler.
- **Metering and capture**: `decide` meters per chunk; the capture goes
  through 01b-C1 and is off the decide path.
- **Privacy**: fixtures and eval cases use placeholder names only. The
  capture view and the Knowledge ledger show private prose. They are local
  views like the prompt log, and nothing here adds a new export.
- **Android / pydantic v1**: pure Python, no new dependency; route bodies are
  plain `BaseModel` fields.
- **Docs guard**: CONTRIBUTING.md's guard table names
  `test_knowledge_writer_guard.py`.

## 11. Tests and acceptance

**Deterministic classifier** (`backend/tests/test_epistemic_classify.py`):

- R1–R6 and R2' one case each, using presence fixtures built with
  `appearances.transitions.appear` / `leave` so intervals are real;
- a split excerpt yields two slices, and the actor text holds only the
  covered posts;
- S1: a whole-scene attendee's summary and title are withheld; with a
  whole-scene `knows` whose hash holds they are shown; after an edit they are
  withheld again;
- an item with no visible slice is absent from `visible()`, header included;
- the narrator aggregate: `attributed`, `withheld` and `unattributed`, with a
  legacy scene rendering unannotated;
- re-matching: a post whose key or digest moved is dropped (`moved`); a cut
  between retrieval and classification makes nothing newly `witnessed`;
- a scene with no identity is `unverifiable` for actors;
- each row of 4.1's failure table, including an unreadable `responses.json`.

**Overrides** (`test_knowledge_store.py`, `test_ledger_knowledge_routes.py`):

- precedence: a post `unaware` beats a group whole-scene `knows`; actor beats
  group beats `*`; recency; the withholding tie-break; `*` skips the author;
- fingerprints: swipe, edit, Keep writing and hide each stop a `knows`, and
  none stops an `unaware`;
- a group override expands through `affiliated` (leader included) and fails
  closed for a renamed or reclassified group;
- actor delete prunes that actor's entries; branch copies post entries and
  re-keys `r-` ids, and skips whole-scene ones; discard drops them;
- routes: `ensure_identity` for a legacy scene, 409 `busy` on
  `UnreadableError`, 400 for an invalid post key or a `knows` accept on a
  keyless part, 404 for an unknown scene; writes journal as `manual` and undo
  restores;
- `test_knowledge_writer_guard.py` fails for a call planted in
  `store/absorb/`.

**Decision stage** (`test_epistemic_refine.py`, with `llm_fakes`):

- a refine call reaches a meter (the `escalation=` regression);
- the eligibility gate: no call when every unknown lacks a channel, none for
  the narrator, none for `moved` or `unverifiable` parts;
- both caps and `decision_skipped`, with the round cap surviving a resumed
  round;
- each answer row of 5.3, including `experienced` → `known`;
- a failed call, an overrun of the stage ceiling, and a 409 `incapable` each
  leave the parts withheld and the turn completes;
- a round with a pending incomplete response runs no retrieval, classify or
  refine; a reroll replays the snapshot and makes no call.

**Prompt separation** (`test_epistemic_prompt.py`):

- an actor compose with a withheld item: its needle is absent from every
  message, and so is its title;
- a narrator view handed to an actor compose raises; so does a view for the
  same actor built for another scene;
- `test_epistemic_basis_never_reaches_the_prompt`, extended to override
  notes;
- `history_view=None` is byte-identical to the baseline (a golden in the
  style of `test_lore_golden.py`, recorded before 09 lands);
- turning `history_recall` off in the layout removes all four sections;
- each section sheds per item, and the drop order holds;
- an item appears in `history_recall` or `history_narrator_only`, never both.

**Frontend**: the Knowledge ledger section follows the list/detail tests.
The capture view's accept posts the right subject and shows `unkeyed`.

**Acceptance**:

1. An actor turn is classified by construction: compose refuses a
   mismatched view.
2. `known`, `suspected` and withheld evidence render in distinct sections,
   and withheld never in an actor's.
3. Every case in 8.1 except 3 (live) and 7 and 15 (Decision) is settled with
   no model call.
4. The Decision stage is off by default, gated on its false-`known` rate,
   capped per step and per round, and fail-soft.
5. The offline leakage grader passes in `make check`, and the live suite
   reports leakage apart from recall.

## 12. Non-goals

- Re-deciding lore visibility, or adding group refs to lore `known_by`. A
  group channel for lore is 07-C3d's present-member rule, not 11's.
- Classifying the current scene. `observed_history` already does it, and the
  perception contract covers the rest.
- Fact items and fact-subject overrides (2): there is no producer.
- Tracker awareness as history evidence. A later spec may make "was told this
  value" a `known` basis.
- A durable per-actor knowledge graph, and any automatic write of knowledge
  by absorb, by a model, or by 12.
- Classifying for PCs beyond the narrator's annotation.
- Any Decision call for the narrator.
- Three draft sources, dropped with reasons:
  - **"directly addressed dialogue"**: no structural record says who a post
    addressed. 09 seeds an addressee from names in the text for ranking, and
    a name match is not evidence of hearing. Presence already covers an
    actor who was addressed in the room;
  - **relationship history and continuity links**: they describe a pair or
    two records, not who perceived an event. They seed 09's ranking and
    nothing here;
  - **the draft's `ACTOR_EXPERIENCED` class** is `witnessed`: presence is the
    only structural evidence of experience, and 02-C5a's `experienced`
    answer is read as `known`, never as presence (5.3).

## 13. What to re-check against the parallel specs

The earlier missing edges are now contracts in `ROADMAP-CHECKLIST.md`.
Points for the plan to confirm against the landed specs:

1. **09's `gather` accepts a perspective and runs per compose step** (6.1),
   with `classify` called inside it after each round, before `_prepare`
   composes under the lock (`routes/character_turns.py:476-477`).
2. **09's `query.build` for an actor** reads `observed_history` and
   restricted seeds (6.1). This is 11's slice to add, inside 09's module.
3. **`EvidencePost.key` is populated** from the transcript in 08's prefixed
   form (`r-<response_id>` / `p-<post_id>`, plus `part`; 08's revision). Until it is, post-listed overrides cannot
   match, and the classifier withholds a keyless post whenever its scene has
   any post-listed override.
4. **02-C5a's class set** is read through 5.3; its `decide-epistemic-access`
   gate case is seeded from 8.1's cases 7, 10 and 15.
5. **The world-level actor and group delete fan-out** that 7.5's
   `forget_audience` must join.

## 14. Open questions

1. **Legacy scenes with no presence intervals (R5).** Withhold from actors by
   default (thin legacy recall), or admit `scene_cast` posts as `witnessed`,
   the tracker's convention (rich recall, and a leak whenever someone left
   mid-scene)? *Recommendation:* withhold, with the Decision stage as the
   remedy. Offer a per-campaign "trust old cast lists" switch only if evals
   show legacy recall is unusable.
2. **Do `unknown` parts deserve a hint in an actor turn?** For example, "Mara
   may or may not know about the pier; let her ask." That would leak the
   topic. *Recommendation:* no. The actor prompt names nothing it withholds.
3. **An opt-in Decision audit of `witnessed` posts** for in-room perception
   (whispers, thoughts)? *Recommendation:* not in this spec. The perception
   contract plus `unaware` overrides cover it. Revisit only if live case 3
   leaks.
4. **Should a model's `experienced` ever become `witnessed`?**
   *Recommendation:* no. Read it as `known` (5.3).
5. **Prefilter in 09** (drop items no present actor attended before ranking,
   for actor turns)? *Recommendation:* not for v1. 6.1 already builds the
   actor's query from actor-visible material, which is the larger lever.
   Revisit if `decision_skipped` counts are high.
6. **Should absorb propose structured "learned" overrides** as review rows
   (a `knowledge` StagedEdit, applied at `PUT /chronicle`)?
   *Recommendation:* yes, as a follow-up spec once the accept flow shows
   which overrides people actually write. The writer guard leaves room.
7. **Per-step retrieval cost** (6.1). Accept N retrievals per round, or cap
   history recall to the first K actor steps of a round? *Recommendation:*
   accept it, behind `history_recall_depth` (off by default), and report the
   per-round embed count in 09's history row so the cost is visible.

## 15. Review record

The substitute spec-gate review (`reviews/11.md`: 3 blocking, 9 should-fix, 8
minor) was checked against the code and folded in as follows.

| Finding | Disposition |
|---|---|
| B1 summary and fact `witnessed` by presence | Fixed. S1 (4.3): presence never makes a summary or title visible; only a whole-scene override or a Decision answer can. Fact items dropped (no producer). Case 8 added |
| B2 `narrator_only` meant "not shown" | Fixed. Three-state narrator aggregate (3.4); only override-established items go to `history_narrator_only`; R2' for PC speakers; cases 10 and 11 |
| B3(a) variant reuse under one `response_id` | Fixed. Post subjects carry variant and raw-text digest; scene subjects a transcript hash; `knows`/`suspects` fail closed (7.1). Verified `store/tracker/paths.py:44-47` |
| B3(b) fact id reissue | Moot: fact subjects dropped. Verified `facts._next_id` (`store/facts.py:200-224`) does reissue the highest unreferenced id |
| B3(c) slug reuse | Fixed. `forget_audience` beside the actor delete (`store/overlay.py:1618`), `audience_name` backstop, group name check (7.3, 7.5) |
| S1 alignment, hidden-post re-check, `responses.json` failure | Fixed. One scene read, re-matching by key and digest, `moved` basis, failure table (4.1). Verified `responses._read` raises |
| S2 perspective interface to 09/10 | Fixed. String form (3.1), per-step `gather`, actor query and seeds, classify per round, `history_view` replaces `history=` (6.1) |
| S3 escalation, ceiling, replay | Fixed. `escalation=` passed; one outer `_bounded_call`; round cap; skip on a pending replay (5.2, 5.4) |
| S4 eligibility and gate | Fixed. 3(b) dropped; 3(c) is a roster pass; gate seeded with channel-present negatives, false-`known` rate as the leakage line (5.1, 8.2) |
| S5 precedence and `*` | Fixed. Subject before audience; `*` excludes the author (7.2) |
| S6 accept flow | Fixed. Accept from the capture view; `unkeyed` refusal; live inspector runs `classify` only (6.4, 7.4) |
| S7 guard placements | Fixed. `epistemic.py` in no lock list (verified the phantom tests); renderer and `build_items` pinned in the regex guard (10) |
| S8 identity-less scenes, `UnreadableError`, key validation | Fixed (4.1, 7.4) |
| S9 duplicate rendering, layout, shed | Fixed (6.2, 6.4) |
| M1 ref conversion | Fixed (3.1) |
| M2 basis in the Decision prompt | Fixed: `basis=()` (3.5, 5.2) |
| M3 `is_active` / fact producer | Moot: facts dropped |
| M4 `begin_observing` | Fixed: basis renamed `not_recorded_present` (4.2) |
| M5 mismatch carries only the actor | Fixed: cid, sid, identity and actor (6.4) |
| M6 note in prompts | Fixed: never renders (7.1) |
| M7 dropped draft sources | Fixed: reasons in 12 |
| M8 world-level group edits | Fixed: editor copy (7.3) |
| Coordinator decisions | Presence never vouches for a summary (B1); group expansion counts the leader (07's `affiliated`); the capture outcome is 01b's envelope (5.5) |
