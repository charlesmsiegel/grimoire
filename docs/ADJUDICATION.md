# Fiction-first adjudication

**Status:** first slice of [Fiction First Adjudication](roadmap/sprints/fiction-first-adjudication.md) (Sprint 029, Wave 4), milestones 1 and 2. It is a headless library with synthetic tests:
- `grimoire.adjudication` (pure): action frames, the deterministic assessment, decisions, outcomes and the ledger codecs;
- `grimoire.adjudicationstore` (adapter): `AdjudicationLibrary`, which records frames, ability rulings and decisions in an authority ledger and hands decided ability uses to [accepted effects (021)](EFFECTS.md).

There is no runner, prompt, model call or dice. Nothing labels prose yet: the labeller that produces frames is a caller obligation (035). All scenarios are synthetic.

**First useful proof.** "Resolve a risky mechanics-free action without narrator fiat": Mara, a player character, vaults a collapsing bridge. Her training favours the vault, the gap and the falling span hinder it, and a fall or a lost satchel are at stake. The outcome may be a success, a failure or a success at a cost, but only one that a recorded cause supports, and every narration retry must agree with it.

## The guarantee is order

Narrator fiat is an outcome with no cause on record, or a difficulty invented in the same breath as the ruling it justifies. Adjudication refuses both by fixing the order of events:

1. A **frame** records the action, the established facts that bear on it (**factors**) and what is at stake (**stakes**), before anything is decided. A factor must have been established strictly before the action's moment.
2. For an action that relies on a power, any [025 ability ruling](ABILITIES.md) is recorded against the frame's exact attempt.
3. The **decision** records the result. It may cite only recorded factors, and bring only declared stakes.

A frame may be revised, and a ruling superseded, only until the attempt is decided. A decision is final in this slice.

## Frames

`ActionFrame(attempt_id, actor, chat, moment, intent, factors, stakes, provenance, ability=None)`:

| Field | Meaning |
|---|---|
| `attempt_id` | The action's id in its campaign. For a player character it is the id of the player's own `action` contribution, as 003's `Resolution.attempt_id` and 025's attempt are. |
| `actor`, `chat` | An `InstanceRef` in the chat's campaign. The actor must be on the chat's roster. |
| `moment` | A 012 `Moment` with a scene and turn. |
| `intent` | Prose for people, never read. The player's own words stay in their contribution. |
| `factors` | Up to 32 `Factor(factor_id, bearing, text, established, source)`, sorted by id. `bearing` is `favours`, `hinders` or `prevents`. `established` is the campaign sequence at which the fact entered the fiction and must be lower than `moment.sequence` (`unestablished_factor`). `source` names the canonical record it comes from (a 017 relation, a 020 event, a 021 accepted event). |
| `stakes` | Up to 16 `Stake(stake_id, text)`: consequences a failure or a costly success may bring. |
| `provenance` | Which labeller produced the frame: attribution, never authority. |
| `ability` | The 025 `AbilityAttempt` the action relies on, if any: same `attempt_id` and actor. |

## Assessment

`assess_action(frame, ability)` classifies a frame deterministically. `ability` is the 025 `AbilityAssessment` of the frame's attempt, or the `AbilityFinding` a decision recorded from one. The first match wins:

| Classification | When | Allowed results |
|---|---|---|
| `clarify` | The ability use cannot be judged as labelled: a ruling is pending (`ability_ruling_needed`), or 025 says `relabel` or `needs_upgrade`. | none |
| `impossible` | Something prevents it: a labelled factor, or a power that does not permit this use (refused, beyond its limits, unavailable). | failure |
| `straightforward` | Nothing hinders it (`unopposed`), or nothing is at stake (`nothing_at_stake`). | success |
| `uncertain` | It is hindered and something is at stake. | failure, partial and success if something favours it; otherwise failure only |

A power becomes **derived factors**. A permitted use (025's `permitted`, never `approval == "not_needed"`) adds `#ability`, which favours. An uncertain one (an attempted feat, or an approved extrapolation, new ability or combination) also adds `#ability-uncertain`, which hinders. A use that is not permitted adds `#ability` as `prevents`. `#` cannot start a labelled factor id.

## Decisions

`Decision(result, basis, cites=(), consequences=())`. `result` is `success`, `partial` or `failure`. `check_decision(assessment, decision)` refuses one the assessment does not support:

| Basis | Use | Refusals |
|---|---|---|
| `rule` | A straightforward or impossible action: its one allowed result. | `rule_mismatch` |
| `adjudicated` | An uncertain action: the director's choice among the allowed results. Success and partial must cite a favouring factor; failure and partial must cite a hindering or preventing one. | `not_uncertain`, `result_not_allowed`, `uncited_result` |
| `consent` | The actor's controller chooses a setback the rules would not force: failure, or a result worse than the best the frame allows. Consent never turns a risk into a success. | `result_not_allowed` |

For every basis: no clarifying action can be decided (`needs_clarification`); cites must be recorded factors (`unknown_factor`); consequences must be declared stakes (`undeclared_consequence`); a success brings none (`success_with_consequence`); a partial success names its cost (`partial_without_cost`).

An `Outcome` is a recorded decision. `Outcome.resolution()` is the 003 `Resolution(campaign, attempt_id, result)`, so [`review_narration`](PLAYER_SOVEREIGNTY.md) rejects narration that contradicts it (`contradicts_resolution`). A swipe, a retry or an alternate response can reframe the drama, never the result.

## The store service

`AdjudicationLibrary(store, authority, handle)` works over the library's `DurableAuthority`. `handle` is the **adjudication key**: a trusted handle that authority issued. Only that channel records frames and rulings, never narration (a 025 obligation). The library only checks handles; it never mints one.

| Call | Does |
|---|---|
| `frame(frame, contribution=None)` | Records a new attempt, or a revision of an undecided one (`FrameEntry.revision` counts from 1). |
| `rule(ruling, supersedes=())` | Records a 025 `AbilityRuling` on a recorded frame's exact ability attempt, after 025's `check_ruling`. `supersedes` names live rulings on the same attempt that this one replaces. |
| `decide(campaign, attempt_id, decision, consent=None)` | Assesses the latest frame revision now and records the decision. Deciding the same again returns the recorded outcome; a different one is `already_decided`. |
| `assess`, `outcome`, `resolutions`, `state` | Read. |
| `ability_use(campaign, attempt_id)` | The 025 assessment of a decided, permitted ability use, for 021 to charge. |
| `check_effect(proposal)` | Refuses a 021 `EffectProposal` whose ability assessments are not exactly `ability_use` of their attempts (`assessment_mismatch`). |

**Player characters.** Control is read from the [sovereignty ledger](PLAYER_SOVEREIGNTY.md#persistence-010b).
- The frame of a player-controlled actor is backed by the controller's own `action` contribution for that scene and turn, whose id is the `attempt_id`. Without one it is `pc_authorship`; with another player's, another turn's or another kind's, it is `invalid_expression`.
- An actor no human controls takes no contribution (`unexpected_backing`).
- A `consent` decision for a player character needs the controller's own trusted human handle (`not_controller` otherwise). For an actor no player controls, the director consents alone (`consented_by` is `director`).

**Abilities.** The library computes the 025 assessment itself, from the chat's snapshot and the campaign's live (not superseded) rulings. A caller cannot supply it. The decision records what it relied on as an `AbilityFinding`: classification, approval, ruling id, uncertainty and charges.
- `ability_use` re-assesses now and refuses when the result differs from the record (`assessment_changed`, for example after an authored change to the ability). It also refuses an undecided attempt (`undecided`), one with no ability (`no_ability`) and a use the power does not permit (`ability_not_permitted`).
- 021 then charges that assessment once.
- A ruling never pins the entity's `resolved_source`. The decision's finding is the pin: a later change shows up as `assessment_changed` at hand-off.

**Other refusals.** `missing_chat`, `not_in_chat`, `unresolved_actor`, `attempt_reused` (an attempt id is another actor's), `ruling_mismatch` (no frame, or other labels), `duplicate_ruling`, `unknown_ruling`, `unknown_attempt`, `stale_frame`, `consent_mismatch`, and 025's own reasons from `check_ruling` (`limit_not_approvable`, `wrong_classification` and so on). A refusal writes nothing.

## Persistence

Each campaign has one authority ledger, `authority/adjudication/<campaign_id>` ([STORAGE](STORAGE.md#authority-ledgers-010b)), with three entry kinds: `frame`, `ruling` and `decision`. Each entry is `{"schema": 1, "kind", "recorded_at", "recorded_by", "value"}`. The value is the exact JSON of `grimoire.adjudication`'s codecs: sets are sorted lists and every key is present.

- **Replay** re-checks every entry with the rules used at record time: frame revisions and attempt ownership, rulings on recorded frames, and each decision against its frame revision, its recorded finding and the live rulings it names. Anything else is `LedgerInvalid`, and the library fails closed.
- **Not re-checked on replay:** who controlled the actor when a frame was recorded (replay does not join the sovereignty ledger's control history), and the entity a finding was computed from. Both are checked at record time; the finding is re-verified at hand-off.
- **State is shared.** The replayed state lives on the `DurableAuthority`, so every library over one authority shares it. A batch written by anything else poisons the authority until it is reopened and the ledger replayed.
- **Reserved ids.** `adjudication_actor_ids` scans the batches raw and is part of [`campaign_taken_ids`](CHATS.md#taken-instance-ids), so a hand-deleted actor's id never goes to a new individual who would inherit its outcomes. `campaign_authority_presence` counts this ledger too, so a new campaign never takes the id of one whose adjudication ledger survives.
- **Not synced.** Like the other authority ledgers, it is not part of 019's sync ([028](roadmap/sprints/app-managed-device-sync.md)).

## Known limits

- **Factor sources are attributed, not verified.** Nothing checks that a factor's `source` exists or says what the factor claims, or that `established` matches it. Checking against 017 relations, 020 events and 021 accepted events is a later slice. Until then the guarantee rests on the labeller and on the order of records.
- **The labeller is trusted.** As in 003 and 025, the classification is only as good as the labels. Under-labelling a hindrance makes an action straightforward.
- **Decisions are final.** Revising a decided outcome needs coordination with 021 revocation and is deferred.
- **No graded consequences or contests** (milestone 3). Each consequence is one declared stake, and there is no opposed action between two actors.
- **Tail forgery.** As for every authority ledger, a valid batch appended at the tail with local file access is not detected by the chain. Replay still refuses one that breaks the rules above.
- **Prompts and disclosure** (how much adjudication a player sees) belong to 035.
