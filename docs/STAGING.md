# Scene staging: who and what is where, and who can reach, see and hear what

**Status:** first slice of [Scene Physical Continuity](roadmap/sprints/scene-physical-continuity.md) (Sprint 041, Wave 5): milestone 1 and the proving scenario. It is a pure, headless library, `grimoire.staging`:
- `model.py`: areas, ways, placements, absences and the validated `Stage`;
- `changes.py`: physical changes checked against the staging, and corrections (`apply`);
- `senses.py`: the questions (`where`, `reach`, `sees`, `hears`, `audience`, `witnesses`), a 032 presence and delivery within earshot, 012 perceptions for witnesses, and checks against 033's cast and 034's holders.

Nothing here touches files or calls a model, and **nothing is stored in this slice**. There is no runner and no app; nothing turns narration or a player's words into a change (the caller builds the `Change`). The scenarios are synthetic.

## What it does

**Proving scenario** (`backend/tests/test_staging_scenarios.py`). Mara and Toma stand in a study. The door to the hall is locked, and Mara holds its key. A letter lies on the desk.
- Mara unlocks and opens the door, Toma goes into the hall, and Mara shuts and locks it behind him.
- The next post cannot have Toma take the letter: `take(toma, letter)` is refused (`elsewhere`), and `reach` says the same. Toma cannot see the desk (`door_shut`).
- Mara takes the letter. Only Mara gets a perception of it.
- A whisper from Mara to Toma through the shut door is refused by 032 (`addressee_absent`). Telepathy, another channel, reaches him.
- With the door open again, Mara's call to the room is *overheard* in the hall, never "told": it enters no one's `sent`.
- Toma leaves the house through the front door. Off stage, he reaches and sees nothing (`not_on_stage`, `viewer_off_stage`), and a lamp falling in the hall gives him no evidence.
- 020 stores the events, perceptions and reports. After a reopen, each actor holds exactly what it could perceive.

## What a stage is

The stage is **scene-local staging and director truth**:
- It is **not world canon.** Durable consequences, such as a key changing hands or someone leaving a building, become [021](EFFECTS.md) operations in a later slice.
- It is **not actor knowledge.** An actor knows where things are only from its own 012 evidence, such as the perceptions `perceptions` builds. Holding a `Stage`, or any answer from it, is the director's, like 017's `GraphIndex`: never put it into an actor's prompt (027, 031).
- **It is a projection of records other sprints own.** 033's staged cast says who is present in the scene; the stage says which of them has physically left. For a 034 unique item, the possession record's holder is the authority, and the stage's custody is a scene projection of it (see [checks](#checks-against-033-and-034)).

### Values

| Value | Meaning |
|---|---|
| `Area(area_id, label=None)` | One room-scale local area: a study, a hall. No coordinates, distances or positions within it. `label` is for people. |
| `Way(way_id, sides, state, key=None, basis, since)` | A door or passage between two areas, or from an area to off stage (`sides == (area, None)`). `state` is `open`, `closed` or `locked`. `key` is the item that locks and unlocks it; a way with no key has no lock (only a correction can change a locked one: a door barred from the other side). |
| `Placement(who, area=None, holder=None, basis, since)` | Exactly one of `area` (rests or stands in it) and `holder` (held or carried by that individual). A thing held by an actor is in hand; a thing held by a thing is inside or on it (**contained**). |
| `Absence(actor, via=None, since)` | A cast actor who is off stage: who left through `via` (a way that leads off stage), or who has not entered (`None`). |
| `Stage(campaign_id, scene_id, as_of, areas, ways, actors, placements, absences)` | The staging at campaign sequence `as_of`. `scene_id` is a label of at most 200 characters, as 032's. |

**Basis.** Every placement and way carries `basis`, `established` (stated in accepted play, or corrected) or `assumed` (plausible staging the director filled in that nothing has stated), and `since`, the sequence it has held from. Every answer lists the assumed facts it rests on, so a caller can ask rather than invent.

**Invariants**, checked on every construction, `dataclasses.replace` included:
- `actors` are the individuals who act (the runner builds them from 033's cast). Every other placed individual is a thing.
- Every actor is placed or absent, never both (`placed_twice`) and never neither (`actor_unplaced`). Only an actor can be absent (`not_cast`).
- A holder is itself placed, or is an absent actor (`orphan_holder`): **what someone carries leaves with them** and stays in their custody. No individual holds itself at any depth (`custody_cycle`).
- An actor goes off stage only by leaving, never as someone's load: a placed actor's custody chain ends in an area (`carried_off_stage`). So `stage.on_stage()` and every question agree on who is present.
- Areas and ways named exist (`unknown_area`, `unknown_way`); an absence's `via` leads off stage (`leads_to_area`); no fact is later than `as_of` (`future_fact`); ids are unique (`duplicate_id`); everything is in the stage's campaign (`campaign_mismatch`).
- Bounds: 1–64 areas, 128 ways, 256 actors (033's cast limit), 1024 placements (`too_large`).
- Collections are sorted, so equal facts give equal stages. A stage pickles, copies and converts with `dataclasses.asdict` by its facts alone.

`stage.at(sequence)` is the same facts at a later sequence: the caller's word that nothing physical changed in between. `stage.on_stage()` lists the actors physically present.

## Changes (`apply(stage, change, *, sequence, provenance)`)

`apply` returns the new stage (its `as_of` is `sequence`) or raises `StagingRefused`, with nothing changed. `sequence` may not be before `as_of` (`out_of_order`). `provenance` is attribution text, validated and not recorded on the stage in this slice.

**Physical changes** are made by an actor standing in an area. One who is not an actor (`not_actor`), off stage (`not_on_stage`) or carried by someone (`restrained`) cannot act. Every fact a physical change sets is `established`: accepted play establishes its own result, even where it rested on an assumption.

| Change | Needs | Result | Refusals |
|---|---|---|---|
| `move(who, way)` | the way touches who's area, its other side is an area, it is open | who in the other area | `way_elsewhere`, `leads_off_stage`, `way_closed`, `way_locked`, `unknown_way` |
| `leave(who, way)` | the way touches who's area, leads off stage, is open; who carries no actor | who absent via the way, with everything they carry | `leads_to_area`, `holds_actor`, … |
| `enter(who, way)` | who is absent; the way leads off stage and is open | who in the way's area, with what they carry | `already_on_stage`, `leads_to_area`, … |
| `take(who, thing)` | the reach rule below | held by who | the reach reasons, `already_held` |
| `drop(who, thing)` | who holds it directly; it is not an actor | it rests in who's area | `not_holding`, `is_actor` |
| `give(who, thing, to)` | who holds it directly; it is not an actor and holds none; `to` is an actor standing in the same area | held by `to` | `not_holding`, `is_actor`, `holds_actor`, `is_self`, `custody_cycle`, `not_actor`, `recipient_off_stage`, `recipient_restrained`, `recipient_elsewhere` |
| `open_way`, `close_way(who, way)` | who's area is a side; closed→open, open→closed | the state | `way_elsewhere`, `already_open`, `way_locked`, `already_closed` |
| `lock_way`, `unlock_way(who, way)` | who at a side; the way has a key; the key is in who's own custody at any depth (a key in their satchel works; one in the pocket of someone they carry does not: `stage.own_custody`); closed→locked, locked→closed | the state | `no_lock`, `still_open`, `already_locked`, `not_locked`, `no_key` |

An **absent actor stands outside**: it may open, close, lock or unlock a way that leads off stage, so it can come back in through a door shut behind it. Any other way is `not_on_stage`.

**People are not handed around.** An actor carried by another (set there by a correction) is set down or moved only by a correction: `drop` and `give` refuse `is_actor`, and nothing that holds an actor at any depth can be taken, given or carried off stage (`holds_actor`).

**Corrections and staging assumptions** have no physical check (a director or player correction) and still keep every invariant:

| Correction | Effect |
|---|---|
| `place(who, area=… or holder=…, basis="established")` | Puts `who` there. An absent actor placed this way is on stage again. `basis="assumed"` records plausible, unstated staging; a later `place` with the default basis establishes it. |
| `remove(who)` | An actor becomes absent with no way, keeping what it carries (`already_absent`; `holds_actor` while it carries an actor). A thing stops being tracked with everything inside or on it (`untracked`), unless an actor is inside it (`holds_actor`). |
| `set_way(way, state, basis="established")` | Sets the state, whoever holds the key, and a keyless way may be set `locked`. |

Recasting (a new actor, area or way) is a new `Stage`, checked again against 033's cast.

## Questions (`senses.py`)

Each returns `Answer(ok, reason, assumed)` unless noted. `assumed` lists the facts the answer rests on that are only assumed, as `placement:<ref>` and `way:<way_id>`, for a no as much as a yes (a letter assumed to be in the hall is `elsewhere` on a guess).

| Question | Yes | No |
|---|---|---|
| `where(stage, x)` → `Whereabouts(status, area, holder, absent, assumed)` | `in_area`, `held` (with the area its custody ends in) | `off_stage` (an absent actor, or carried by one: `absent` names who), `untracked` |
| `reach(stage, who, thing)`: the rule `take` applies | `within_reach`, `in_hand` (already in who's custody) | `not_actor`, `not_on_stage`, `restrained`, `is_self`, `is_actor` (people are not taken), `untracked`, `holds_actor` (someone is inside it), `thing_off_stage`, `held_by_other` (in another actor's hand or pack, at any depth), `elsewhere` |
| `sees(stage, viewer, target)` | `same_area`, `through_open_way`, `is_self` | `not_actor`, `viewer_off_stage`, `viewer_contained` (an actor inside a thing sees nothing), `untracked`, `target_off_stage`, `contained` (inside or on a thing), `door_shut`, `elsewhere` |
| `hears(stage, listener, speaker, channel)` | `same_area`, `through_open_way` (speech), `no_barrier` (telepathy), `is_self` | `unknown_channel`, `not_actor`, `speaker_off_stage`, `listener_off_stage`, `door_shut`, `out_of_earshot` (a whisper through an open way), `elsewhere` |

Room scale, no distances:
- **Reach.** An actor reaches a non-actor in its own area that no other actor holds at any depth (a coin inside a box on the table is reachable), and anything in its own custody.
- **Sight.** An actor on stage sees its own area and any area joined to it by an open way. A shut way blocks sight. Carried actors see from where they are carried; anything inside a thing is out of sight both ways.
- **Hearing** uses 032's channels. `speech` carries into an area joined by an open way and no further; a shut way stops it. `whisper` reaches only the same area. `telepathy` ignores ways and walls: any actor on stage. Who may *send* telepathically stays 032's input (later 025's).

`audience(stage, speaker, channel)` lists the other actors who receive the speaker (refused `not_actor`, `not_on_stage`, `unknown_channel`).

### Delivering a message within earshot (032)

- `presence(stage, message, *, telepaths=(), allow_assumed=False)` builds a 032 `Presence` of the sender and everyone who physically receives the sender **on the message's own channel**. It takes the message, never a bare channel, so a presence built for speech cannot carry a whisper through an open door. Use it only for that message and that stage: it carries no sequence, so one kept after the stage changes is stale. 032's `transmit` over it reports room speech heard through an open way as `told`; only `deliver` makes it `overheard`, so prefer `deliver`.
- `deliver(stage, message, *, sequence, provenance, turn_id=None, telepaths=(), allow_assumed=False)` is `presence` then 032's `transmit`, at the stage's own sequence (`stale_stage` otherwise). It adds one rule: **speech to the room is addressed only to the speaker's own area.** A listener who hears it through an open way gets an `overheard` report (unaddressed, no `sent_at`), so it never enters the speaker's `sent`; the speaker did not knowingly tell them. A named addressee in the next area is still told. 032's refusals raise `TransmissionError` as before.
- So a whisper to someone behind a shut door is `addressee_absent`, and nothing is written.

### Perceptions for witnesses (012, 020)

- `witnesses(stage, area)`: the actors who can see what happens openly in `area`: on stage, not inside a thing, and in the area or in one joined to it by an open way.
- `perceptions(stage, event, *, area, percept, provenance, channel="witnessed", allow_assumed=False)` gives one confirmed 012 `Perception` per witness, sorted by holder, at the event's moment.
  - The stage must be the scene at the event's own sequence (`stale_stage`; `stage.at` moves a stage forward when nothing changed), and in the event's scene when the event names one (`scene_mismatch`).
  - `percept` is sensory register only (012's rule), nonempty, at most 20 000 characters.
  - Ids are `perception_id(event_id, holder, *, channel, area, percept)`: `<event_id>.w-<16 hex>`, the first 16 hex digits of SHA-256 over the holder's reference text, the channel, the area and the percept. Neutral, deterministic (a repeated call is 012's and 020's `duplicate_id`) and silent about other witnesses; a second channel or percept for the same event (seen, then heard) is a second perception. An event id is at most `EVENT_ID_MAX` (104) characters (`too_long`), so the id plus 020's `.json` fits a store slot.
- Witnesses are judged on the stage after any change the event itself made: an actor behind a door the event shut, or one the event took off stage, gets nothing of it. Perceiving the change itself is later work.
- 020 stores the result unchanged.

**Evidence is never invented.** `presence`, `deliver` and `perceptions` decide who receives evidence, which 020 cannot take back. When an answer they use rests on an assumed fact (an assumed position, or an assumed open door), they refuse with `rests_on_assumption` unless the caller passes `allow_assumed=True`: ask, or establish the position, first. That includes an actor left out on a guess (assumed inside a wardrobe, or behind a door assumed shut). For a whisper or telepathy only the addressees' staging counts, since no one else receives it; for speech every listener's does.

### Checks against 033 and 034

- `check_cast(stage, cast)` refuses a stage whose actors are not exactly 033's staged `SceneCast` of the same campaign and scene (`cast_mismatch`). The cast's sequence is not compared: a cast is staged once and the stage moves on. **Physical presence** is the cast less the stage's absences (`stage.on_stage()`). 033's `presence` and `may_speak` still answer cast membership. A runner (035) takes 032 presences from the stage, and treats an absent cast member as not speaking in the room.
- `custody_divergence(stage, holders, *, here=())` takes each 034 unique item's canonical holder and returns the items whose staging disagrees. `here` names the canonical holders that stand for this scene's ground (the place it is set in): an item resting in an area agrees with those. Any other holder the stage does not track (a chest in another town, someone not in the scene) disagrees with an item on this stage. A runner checks this when it builds a stage and before it turns a stage custody change into a 034 move. The stage knows nothing of 034's equipped slots: 034's own move refuses `still_equipped`.

## Errors

`StagingRefused(reason)` carries a machine reason and no text, label or reference. It is raised outside any handler, so its cause and context are empty, and it pickles with its reason only. Malformed values are `invalid_field`.

## Boundaries and later work

| Not in this slice | Owner |
|---|---|
| A persisted stage, maintained across narrated turns, with corrections recorded (milestone 2) | 041, next slice, with 035's runner |
| Turning narration or an intent into a `Change`; refereeing narration against the stage | 035, 047 |
| Durable consequences as 021 operations (`located_in`, `carries`), and seeding a stage from 017 relations and 034 holders. 021 has no 034 possession part yet | 041 next slice, 021, 034 |
| A player-scoped view of the staging, and players' own corrections through 003's channels | 041 later, 003 |
| Positions and spots within an area, distance bands, posture, cover, barriers within an area, hiding and stealth | 041 later |
| Putting things into containers, lockable containers, keys that are not items | 041 later |
| Audibility levels (a shout through a door), languages, range | 041 later, 032 milestone 2 |
| Perceiving a change itself: those a shutting door hides seeing it shut, an actor's own exit, carrying an actor | 041 later |
| Spatial and sensory affordance queries for maps, audio and combat (milestone 3) | 041 milestone 3 |
| Geography between places | [Location Topology](roadmap/sprints/location-topology.md) |
