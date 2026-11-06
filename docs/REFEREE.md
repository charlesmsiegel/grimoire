# Narration continuity referee: grounded findings about a proposed narration

**Status:** first slice of [Narration Continuity Referee](roadmap/sprints/narration-continuity-referee.md) (Sprint 047, Wave 6): milestone 1 and the proving scenario. It is a pure, headless library, `grimoire.referee`:
- `model.py`: labelled claims, the facts a review reads, findings and the report;
- `check.py`: `referee`, the deterministic checks;
- `mislabel.py`: routing and flagging the authorship mislabels 003's correction flow cannot reach.

Nothing here touches files, calls a model or changes the narration, and **nothing labels prose yet**. The claims are typed values the caller builds, as 029's frames and 040's attempts are: the labeller that extracts them from generated text, its model call and its cost, and wiring the referee into 035's runner, are [milestone 2](#boundaries-and-later-work). The scenarios are synthetic.

## What it does

**Proving scenario** (`backend/tests/test_referee_scenarios.py`). In an inn, Oda (an NPC) and Mara (a player character) stand in the taproom; the cellar door is locked and Mara holds the key. Only Toma was told where Mara hid the strongbox key. Oda drank her last healing draught at the last turn. A model narrates Oda reaching through the cellar door to draw from the cask, mentioning where the strongbox key is hidden, and uncorking her last healing draught; it adds that Oda's laugh "rolls through the taproom like a cart over cobbles".
- The referee reports exactly three material contradictions, each with the records it rests on: `elsewhere` (the cask is in the cellar, the cellar door is locked), `never_told` (Oda holds nothing of that proposition) and `insufficient` (Oda has no draughts; operation `drink-1` used the last).
- The metaphor is not objected to: labelled `figurative`, its literal reading is reported as a non-material `metaphor` finding, and "Oda is in the taproom" checks clean.
- A separately labelled claim that Mara agrees to pay double goes through 003's `review_narration` and is rejected (`pc_authorship`). A claim that carries Mara's words under Oda's name ("Oda tells you Mara will gladly pay") is routed to mislabel review, and Mara's player can flag it, which 003's own correction flow refuses.
- The narration is unchanged; the report names it by its SHA-256.

## Claims

A `ContinuityClaim(campaign_id, claim_id, kind, actor, span, quote, …, mode="literal", certain=True)` is one atomic statement of the narration, as a labeller that is not the narrator read it. `span` is the half-open character range it reads and `quote` those characters: a claim whose quote does not match the narration is `unchecked` (`bad_anchor`), never checked against facts it may not be about. `actor` is who it is about.

| Kind | Category | Fields | Checked against |
|---|---|---|---|
| `present` | presence | | 041: the actor is in the stage's cast and on stage |
| `reaches` | staging | `target` (a thing) | 041 `reach`: the rule `take` applies |
| `sees` | staging | `target` | 041 `sees` |
| `hears` | staging | `target` (the speaker), `channel` (032) | 041 `hears` |
| `spends` | possession | `item` (a 023 key), `count` | 023: the actor's own stack holds `count` on top of what the narration's earlier literal `spends` of it took |
| `wields` | possession | `target` (a 034 item) | 034: the item's holder is the actor, directly or through items it holds (a dagger in her satchel) |
| `knows` | knowledge | `proposition_id` | 012: the actor holds the proposition, by 027's `known_ids` rule |
| `resolves` | outcome | `attempt_id`, `result` | 029/003: the recorded resolution |
| `overcomes` | outcome | `obstacle_id`, optional `approach_id` | 040: the derived `ObstacleState`, the winning attempt's actor and approach, and its escalations |

A field a kind does not take must be absent. `mode` is how the labeller read the words: `literal`, `figurative` (a metaphor or turn of phrase) or `unreliable` (deliberately false narration, such as a lying narrator's). `certain` is false when the labeller was unsure. **Neither hides a claim:** it is still checked.

Spends are read in narration order: two draughts uncorked from a stack of one leave the second `insufficient`. A shortfall in the actor's own stack is only `uncertain` when a stack the actor carries (a 023 holder that 034 custody puts in the actor's hands, such as her satchel) holds the item.

Speech **content** is not a physical claim: an NPC saying "the potion is gone" may be lying or wrong. The labeller labels what the narration states as so, and an actor's words only as a `knows` of what they reveal.

## Facts

`Facts(campaign_id, as_of, stage=None, holdings=(), possessions=(), knowledge=None, heard=(), resolutions=(), obstacles=())` are the records one review reads, all of one campaign, at campaign sequence `as_of`. Each family is optional, and **a claim whose facts are missing is `unchecked`, never passed** (`no_stage`, `no_holdings`, `no_possession`, `no_knowledge`, `no_resolution`, `no_obstacle`).

- `stage` must be the 041 stage at `as_of` itself (`stale_stage`). A stage has no lineage: the caller keeps the latest one across turns (041 milestone 2) and moves it on with `Stage.at` only when nothing physical changed.
- `heard` lists `(actor, proposition_id)` pairs said aloud in the actor's hearing this turn that 012 has not admitted as its evidence yet (039's rule: a line heard is not yet knowledge). Answering one is `uncertain`, not a secret.
- `holdings` (one per holder), `possessions` (one per item) and `obstacles` (one per obstacle id) are unique (`duplicate_id`).

## Findings

`referee(text, *, facts, claims=(), authorship=(), authority=None)` returns a `RefereeReport(text_sha256, findings, clean, unchecked, authorship, mislabels)`: findings sorted by position, the claims that checked clean, and the claims it could not check with why. The same inputs in any order give an equal report.

A `Finding(claim_id, category, kind, classification, reason, span, quote, evidence, assumed)` cites `Evidence(source, ref, detail)` records: `stage` (`placement:<ref>`, `absence:<ref>`, `way:<way_id>` with its state and basis), `holdings` (the stack, and the last operation that spent from it), `possession` (the item's holder and revision, the containers followed, its last operation), `knowledge` (the proposition and its standing, what the actor holds), `resolution` and `obstacle` (status, attempts and the facts that prevent the approach).

| Classification | When | Material |
|---|---|---|
| `contradiction` | established records say otherwise | yes |
| `uncertain` | the answer rests on an assumed staging fact (`assumed` lists them, as 041 names them), on evidence about the same subject the actor could have reasoned from (`related_evidence`), on a line heard and not yet admitted or on unconfirmed 012 evidence of the proposition (`unconfirmed_evidence`), on conflicting resolutions, on a diverged obstacle, or on a labeller unsure of its reading | no |
| `metaphor`, `unreliable` | the labeller read the words as figurative or deliberately false, and their literal reading would not hold: shown so the table can see it, never material | no |

Reasons are 041's own (`off_stage`, `not_cast`, `elsewhere`, `door_shut`, `held_by_other`, `out_of_earshot`, `restrained`, …), `insufficient`, `not_holder`, `never_told`, `heard_unadmitted`, `unconfirmed_evidence`, `related_evidence`, `contradicts_resolution`, `conflicting_resolutions`, `not_overcome`, `prevented`, `other_approach`, `other_actor` and `diverged`. A 041 answer that means the label is wrong (`is_self`, `is_actor`, `holds_actor`, a speaker who is not an actor) is `unchecked` as `bad_label:<reason>`; a thing the stage does not track is `untracked`. Other `unchecked` reasons: `unknown_proposition`, `custody_unreadable` (a custody chain that loops or runs past 16 containers), `no_winning_frame` (an overcome obstacle whose winning attempt has no frame), and `unreadable` (a reference the stage or 012 refuses).

**A finding is a report**, not a ruling, a repair or a block. What follows from one (repair, player review, an accepted exception, blocking) is milestone 2's.

**Findings are director-side.** Like a stage's answers and 012's director projection, evidence can name a secret's proposition or where a hidden thing is: never put a finding in an actor's prompt or show it to a player as it is.

## Authorship claims and mislabel review

The referee does not judge authorship itself. `authorship` are 003 `NarrationClaim`s wrapped as `LabelledClaim(claim, span, quote, voiced_by=None)`, reviewed by 003's `review_narration` with `authority` (`Authority(context, controls, contributions, delegations, revocations, conditions)`, required: `needs_authority`) and the facts' resolutions. Unanchored ones are left out of the review and listed `unchecked`.

`voiced_by` is the actor whose own words or voluntary acts the claim's text carries, in the labeller's reading. It routes the two shapes 003's correction flow cannot reach ([known limits](PLAYER_SOVEREIGNTY.md#correction-flow)) to review as a `Mislabel`:
- `speaks_for_pc`: the claim is about someone else (or no one) but carries a player character's words or acts;
- `speech_in_outcome`: an outcome claim carries a player character's words or acts.

Ambiguous control of the voice routes too. `flag_mislabel(report, corrections, controls)` accepts a flag only from the current human controller of the player character the claim speaks for (`not_controller` otherwise, `unknown_claim` for a claim not routed). **A flag strikes nothing** and widens no correction right over claims about other actors: 003's review stands, and the flag is for the director and a later relabel.

## Errors

`RefereeError(reason)` carries a machine reason and no text, label or reference, and pickles with its reason only: `invalid_field`, `unknown_kind`, `missing_field`, `campaign_mismatch`, `duplicate_id`, `stale_stage`, `invalid_text` (empty, or over 200 000 characters), `too_many_claims` (over 256) `needs_authority` and `invalid_authority` (003 refused the `Authority`, for instance ambiguous control).

## Boundaries and later work

| Not in this slice | Owner |
|---|---|
| The labeller: extracting atomic claims from generated prose with a model call that is not the narrator's, opt-in and metered like any optional stage, and a strict reader of its output | 047 milestone 2 |
| Running the referee on a take in 035's runner before it can be kept, and what a material finding does there (block, repair, player review, accepted exception); filling `SceneLine.review` | 047 milestone 2, with 035 |
| A stage kept across narrated turns, to pass as `Facts.stage` | 041 milestone 2, with 035 |
| A player-facing view of findings that reveals no hidden fact | 047 milestone 2 |
| Identity (a name used for the wrong individual) and capability (025 abilities) claims | 047 later |
| Long-form continuity, stylized prose and deliberately deceptive narrators at realistic latency and cost; measuring false positives on real model output | 047 milestone 3 |
| Whether the labeller covered every claim: the referee checks what it is given | 047 milestone 2 (coverage evaluation) |
