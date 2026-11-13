# Narration continuity referee: grounded findings about a proposed narration

**Status:** Sprint 047's deterministic referee and independent prose extraction are implemented. `grimoire.referee` remains pure; `grimoire.narration_review` owns the strict extraction reader and separate provider call. `grimoire.play.continuity` joins them to generated proposals, with durable evidence, accounting and explicit acceptance. Tests use synthetic providers and libraries; live-model accuracy and graphical/physical-phone acceptance are not certified.

The narrator never labels its own output. Optional extraction is disabled by default and cannot commit effects, grant a player commitment, or add actor knowledge. The original prose remains unchanged.

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
| `acts` | intention | `tags` (1 to 8 039 approach tags: `trade`, `threat`…) | 039: no limit active for the NPC this turn forbids one of them |

A field a kind does not take must be absent. `mode` is how the labeller read the words: `literal`, `figurative` (a metaphor or turn of phrase) or `unreliable` (deliberately false narration, such as a lying narrator's). `certain` is false when the labeller was unsure. **Neither hides a claim:** it is still checked.

Spends are read in narration order: two draughts uncorked from a stack of one leave the second `insufficient`. A shortfall in the actor's own stack is only `uncertain` when the stacks the actor may carry would cover it: a 023 holder that 034 custody puts in the actor's hands, such as her satchel, or one whose custody is not known to its end. An unsure spend makes a later shortfall `uncertain`, not material.

Speech **content** is not a physical claim: an NPC saying "the potion is gone" may be lying or wrong. The labeller labels what the narration states as so, and an actor's words only as a `knows` of what they reveal.

An `acts` claim gives the kinds of act an NPC is narrated doing, in 039's approach tags. It breaks a limit that is active for the NPC this turn (its `when` holds in the `Choice`) and forbids one of those tags: a briefed refusal, or a rule that was active but not briefed because the NPC could not have broken it by any approach it had. The finding cites limit ids, never a rule's text. A player character has no `Choice`, so its acts are `no_intention`. A tag the agenda's approaches never use is `unknown_tag` when no limit is broken: the labeller's word for the act is not the author's, so nothing says it is allowed.

## Facts

`Facts(campaign_id, as_of, stage=None, holdings=(), possessions=(), knowledge=None, heard=(), resolutions=(), obstacles=(), intentions=())` are the records one review reads, all of one campaign, at campaign sequence `as_of`. Each family is optional, and **a claim whose facts are missing is `unchecked`, never passed** (`no_stage`, `no_holdings`, `no_possession`, `no_knowledge`, `no_resolution`, `no_obstacle`, `no_intention`).

- `stage` must be the 041 stage at `as_of` itself (`stale_stage`). A stage has no lineage: the caller keeps the latest one across turns (041 milestone 2) and moves it on with `Stage.at` only when nothing physical changed.
- `heard` lists `(actor, proposition_id)` pairs said aloud in the actor's hearing this turn that 012 has not admitted as its evidence yet (039's rule: a line heard is not yet knowledge). Answering one is `uncertain`, not a secret.
- `intentions` are the 039 `Choice` each NPC was briefed with this turn, chosen from its knowledge as of `as_of` itself (`stale_intention`: a choice is for one turn). With a `stage`, each must be for the cast on it, everyone the NPC recognises and the rest (`intention_cast_mismatch`), since which limits apply hangs on who is there; with `knowledge`, from the very projection `actor_knowledge` gives that NPC at `as_of` (`intention_mismatch`), as 039's brief requires.
- `holdings` (one per holder), `possessions` (one per item), `obstacles` (one per obstacle id) and `intentions` (one per actor) are unique (`duplicate_id`).

## Findings

`referee(text, *, facts, claims=(), authorship=(), authority=None)` returns a `RefereeReport(text_sha256, findings, clean, unchecked, authorship, mislabels)`: findings sorted by position, the claims that checked clean, and the claims it could not check with why. The same inputs in any order give an equal report.

A `Finding(claim_id, category, kind, classification, reason, span, quote, evidence, assumed)` cites `Evidence(source, ref, detail)` records: `stage` (`placement:<ref>`, `absence:<ref>`, `way:<way_id>` with its state and basis), `holdings` (the stack, and the last operation that spent from it), `possession` (the item's holder and revision, the containers followed, its last operation), `knowledge` (the proposition and its standing, what the actor holds), `resolution` and `obstacle` (status, attempts and the facts that prevent the approach), and `intention` (the choice's moment, and each limit broken, `briefed` or `active`, with the tags it forbids) and `narration` (what the narration's earlier spends of a stack took).

| Classification | When | Material |
|---|---|---|
| `contradiction` | established records say otherwise | yes |
| `uncertain` | the answer rests on an assumed staging fact (`assumed` lists them, as 041 names them), on evidence about the same subject the actor could have reasoned from (`related_evidence`), on a line heard and not yet admitted or on unconfirmed 012 evidence of the proposition (`unconfirmed_evidence`), on conflicting resolutions, on a diverged obstacle, or on a labeller unsure of its reading | no |
| `metaphor`, `unreliable` | the labeller read the words as figurative or deliberately false, and their literal reading would not hold: shown so the table can see it, never material | no |

Reasons are 041's own (`off_stage`, `not_cast`, `elsewhere`, `door_shut`, `held_by_other`, `out_of_earshot`, `restrained`, …), `insufficient`, `not_holder`, `never_told`, `heard_unadmitted`, `unconfirmed_evidence`, `related_evidence`, `contradicts_resolution`, `conflicting_resolutions`, `not_overcome`, `prevented`, `other_approach`, `other_actor`, `diverged` and `breaks_refusal`. A 041 answer that means the label is wrong (`is_self`, `is_actor`, `holds_actor`, a speaker who is not an actor) is `unchecked` as `bad_label:<reason>`; a thing the stage does not track is `untracked`. Other `unchecked` reasons: `unknown_proposition`, `custody_unreadable` (a custody chain that loops, runs past 16 containers or stops at an item with no possession record), `unknown_tag`, `no_winning_frame` (an overcome obstacle whose winning attempt has no frame), and `unreadable` (a reference the stage or 012 refuses).

**A finding is a report**, not a ruling or a repair. The runner holds material, uncertain and unchecked findings for explicit review; protected player authorship and possible mislabels cannot be overridden through continuity acceptance.

**Findings are director-side.** Like a stage's answers and 012's director projection, evidence can name a secret's proposition or where a hidden thing is: never put a finding in an actor's prompt or show it to a player as it is.

## Authorship claims and mislabel review

The referee does not judge authorship itself. `authorship` are 003 `NarrationClaim`s wrapped as `LabelledClaim(claim, span, quote, voiced_by=None)`, reviewed by 003's `review_narration` with `authority` (`Authority(context, controls, contributions, delegations, revocations, conditions)`, required: `needs_authority`) and the facts' resolutions. A `LabelledClaim` whose claim text is not its quote is refused (`invalid_field`): 003 reviews exactly the anchored words. `Authority` keeps its records in a canonical order, so the same records in any order give the same review. Unanchored claims are left out of the review and listed `unchecked`.

`voiced_by` is the actor whose own words or voluntary acts the claim's text carries, in the labeller's reading. It routes the two shapes 003's correction flow cannot reach ([known limits](PLAYER_SOVEREIGNTY.md#correction-flow)) to review as a `Mislabel`:
- `speaks_for_pc`: the claim is about someone else (or no one) but carries a player character's words or acts;
- `speech_in_outcome`: an outcome claim carries a player character's words or acts.

Ambiguous control of the voice routes too. `flag_mislabel(report, corrections, controls)` accepts a flag only from the current human controller of the player character the claim speaks for (`not_controller` otherwise, `unknown_claim` for a claim not routed). **A flag strikes nothing** and widens no correction right over claims about other actors: 003's review stands, and the flag is for the director and a later relabel.

## Errors

`RefereeError(reason)` carries a machine reason and no text, label or reference, and pickles with its reason only: `invalid_field`, `unknown_kind`, `missing_field`, `campaign_mismatch`, `duplicate_id`, `stale_stage`, `stale_intention`, `intention_cast_mismatch`, `intention_mismatch`, `invalid_text` (empty, or over 200 000 characters), `too_many_claims` (over 256), `needs_authority` and `invalid_authority` (003 refused the `Authority`, for instance ambiguous control).

## Independent extraction and production review

Enable `"critique": 1` in the app model configuration to request one independent extraction call per generated take; omit it or set `0` to disable it. The existing per-call attempt cap and provider retry/fallback rules apply. Extraction output honors the configured token limit, capped at 4096. The stage is `critique`, phase `continuity`, on the **same turn meter** as generation. Its attempts, including failures and cancellation, are retained in `Take.calls` and the normal usage API. Missing usage/prices remain unknown. A separate runtime object and untrusted provider identity execute this call, even when its route/model matches the narrator's.

The extraction prompt carries exact line IDs, SHA-256 digests, prose and a bounded director-side vocabulary of qualified references. Its JSON reply supplies atomic continuity and authorship labels with exact half-open Unicode character spans and quotes. A strict reader rejects unoffered references, duplicate IDs, malformed anchors, extra fields and authority data. It does not accept model-written contributions, delegations, conditions or resolutions. Missing lines or authorship coverage remain unchecked; `complete` is the extractor's coverage assertion, **not a guarantee of semantic completeness**.

The runner captures immutable facts before provider execution under the library lock, including current holdings, possessions, knowledge, recorded resolutions, obstacles and actual NPC brief choices where configured. Missing records are unchecked. Obstacle checks require an explicitly supplied runner obstacle service; the campaign hub’s obstacle library is not automatically shared with the text runner. A stage at a different sequence from the epistemic brief is withheld and reported unavailable rather than silently advanced or rewound. This conservative moment policy can require review even when a human knows the latest stage is unchanged. Corrupt/quarantined fact inputs refuse initial capture. If they become unreadable after a billed call, the proposal is held stale and its usage is retained.

A canonical snapshot fingerprint covers facts, control, stage, relevant instances and configured agendas. Landing and keeping recheck it under the writer's commit lock; changed facts require a fresh generated review. The guard follows existing store concurrency guarantees, not an atomic transaction over arbitrary external file edits. Extraction has the generation fence's cancellation token. Superseded work retains billed usage without becoming the current proposal.

Before derived review evidence is written, a separate untrusted library handle must authorize `PROPOSE` for the exact serialized turn payload and address. It cannot `APPLY` effects. The app installs a fixed proposal grant when critique is enabled; revocation is preserved across settings reload and restart. Provider execution approval and library proposal permission are independent checks. A denied proposal drops derived lines while preserving billed attempts.

### Review states and acceptance

`SceneLine.review` is one of:

- `unlabelled`: the optional extraction was not requested, including older records;
- `reviewed`: supplied labels checked without material/uncertain/unchecked findings;
- `needs_review`: a material or uncertain finding, or an unchecked fact;
- `unchecked`: extraction coverage is incomplete;
- `failed`: extraction failed, with a fixed reason and retained accounting;
- `stale`: reviewed inputs changed;
- `blocked`: rejected PC authorship or possible PC-speech mislabel;
- `exception`: the owner explicitly accepted held prose under the exact proposal token.

`blocked` cannot be accepted by acknowledgment. Existing withdrawal/rephrase/reroll operations preserve the original proposal and create fresh text for fresh review. A stale snapshot cannot be accepted with an old token. Accepting an exception changes presentation only: effects, outcomes, holdings and knowledge still require their existing independent writers.

The snapshot fingerprint and review token are different. A review token binds the chat, turn, take, ordered exact lines, dispositions and evidence to that snapshot. `SceneRunner.keep(..., turn=..., acknowledge_review=token)` requires the exact token for held prose and an issued trusted owner handle. `keep_lines(..., acknowledge_reviews={"<source take number>": token})` applies the same checks to every selected source. Neither a Boolean acknowledgment nor a token from another take works. A composition drawn from multiple reviewed takes is a new aggregate: even individually clean sources require owner acknowledgment and are recorded as `exception` with `aggregate_unchecked`. The runner does not falsely label that combination reviewed or invoke a hidden extra model call. Within one generated take, all lines are checked together, so cumulative spends across speakers share the same inventory accounting.

### Contracts for 112 and 113

- **112:** model settings accept optional integer `critique` (`0` or `1`, default `0`). `models.stages` reports the available optional `critique` stage and its cap. Existing usage includes stage/call/attempt counts and cost for extraction; no extra aggregation store is introduced. Settings failure or disabling critique clears the extractor for subsequent takes. The UI owner supplies the control; this slice does not rewrite its page.
- **113 / play:** ordinary text-API lines include `review` and a safe `review_summary` (source digest, coverage and counts). Takes include `review_token`. The `keep` op accepts `acknowledge_review`; `play.keep_lines` accepts `acknowledge_reviews`, always with the exact `turn` the user read.
- **Owner-only evidence:** `continuity_review` takes `chat`, `turn`, and `take` and returns durable source-anchored findings, evidence, coverage, status and tokens. The underlying `SceneRunner.continuity_review` authenticates the library owner. Ordinary actor/player views contain no evidence details, hidden proposition IDs or secret source material. No prompt/history reader consumes review reports.

The persistent line details and take fields are additive: existing unlabelled turns still decode. Older clients cannot supply the new acknowledgment and therefore cannot silently accept a held review. Downgrading a writer that does not understand the new fields is not supported.

## Boundaries and later work

| Not certified or implemented here | Owner |
|---|---|
| Full semantic claim coverage, false-positive/creative-damage quality and live-model latency/cost benchmarks | 047 milestone 3 |
| Automatic targeted rewriting of narration | 047 later; this slice flags and supports explicit review/reroll |
| Persisted multi-turn stage storage and richer time reconciliation | 041 milestone 2 |
| Full identity/capability claim extraction beyond the referee's current kinds | 047 later |
| Full 003 condition/delegation editing and UI | Existing authority owners and 113 |
| Graphical review controls, keyboard/screen-reader/touch acceptance | 112/113 and Wave 7 UI gate |
