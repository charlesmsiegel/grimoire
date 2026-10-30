# Player sovereignty — first-slice authority contract

**Status:** first slice of [Player Sovereignty](roadmap/sprints/player-sovereignty.md), implemented as a pure policy in `backend/src/grimoire/core/authority.py`. Field names are a first-slice contract, not a frozen file schema; nothing here is persisted yet.

The player owns their PC's intentions, voluntary speech and actions, interior experience, beliefs and commitments. The director narrates a responsive, challenging world but does not author those things unless the player explicitly delegated them. Sovereignty never guarantees success: failures, harm, refusals, sensations and disclosed rule-imposed reactions remain the director's to narrate.

## What is and is not enforced

The policy judges **labelled narration claims**: atomic `NarrationClaim` values that each carry a domain and subject. It does not read prose. The checks are only as good as the labeller that produces the claims, which must not be the narrator. Right now no prose narrator is constrained. That needs a generation/runtime/referee sprint to extract and label claims from model output.

Trust boundary:

- **Player-input channel only:** `PlayerContribution`, `Delegation`, `Revocation` and `Correction`. Model output must never be converted into these. Every review re-checks them against current `ControlAssignment`s, so a value naming the wrong submitter or grantor grants nothing.
- **Rule, effect and adjudication writers only:** `ImposedCondition` and `Resolution`, never narration.
- **Reserved id:** `DIRECTOR` (`"director"`). It controls NPCs and can never submit a contribution or grant a delegation. Unknown or ambiguous control fails closed.

The scenarios are synthetic fixtures; V2 has no freeform scene runner yet. Player trust, useful challenge and non-patronizing refusals are encoded as deterministic policy tests, not evaluated with people or models. None of this is app-integrated.

## Authorship domains

| Group | Domains | The director may author it about a player-controlled actor when |
|---|---|---|
| protected | `intent`, `voluntary_action`, `voluntary_speech`, `interiority`, `belief`, `commitment` | the claim reproduces the controller's contribution for this turn **verbatim**, or a valid delegation covers it |
| conditional | `involuntary_reaction` | it cites an active condition on that actor, from a rule or committed effect, that is disclosed and within agreed limits |
| adjudicated | `outcome` | it agrees with any recorded resolution and names an action the controller actually submitted |
| open | `sensation`, `external_effect`, `world_fact` | always |

Boundaries:

- **`sensation`:** what the senses register (cold rain, pain from a wound).
- **`involuntary_reaction`:** an involuntary response (supernatural terror, a flinch, compelled movement).
- **`interiority`:** characterizing feelings and thoughts.
- **`belief`:** conclusions, trust and deductions.
- **`external_effect`:** what is done to the PC (wounded, knocked down).

A condition never authorizes a protected domain. Fear can drive the PC back a step, but it cannot make them *say* "I yield". For a director-controlled actor every domain is the director's. Outcomes must still agree with a recorded resolution.

Contribution kinds map to the domains they can back:

| Kind | Domains |
|---|---|
| `speech` | speech, and commitments made aloud |
| `action` | intent, voluntary action |
| `interiority` | interiority, belief |
| `commitment` | commitment |
| `directorial_request` | nothing; it is an out-of-character request to the director, not PC authorship |

## Forbidden PC-authorship errors

Each error below is rejected and pinned by tests in `backend/tests/test_authority.py` and `test_sovereignty_scenarios.py`:

1. Invented PC dialogue, a paraphrased quote, or an escalated action presented as the player's own (`pc_authorship`, `not_verbatim`).
2. A PC feeling, belief, trust decision, deduction, intention or promise the player did not author (`pc_authorship`).
3. A contribution used to back a domain it does not cover. Examples: a wary feeling relabelled as trust, an action used as a belief, a directorial request used as PC interiority. Also a contribution from another turn, scene, actor or submitter (`invalid_expression`).
4. A failed attempt narrated as success, or the reverse; conflicting rulings (`contradicts_resolution`, `conflicting_resolutions`, `unknown_attempt`).
5. Fear or compulsion without an active condition, or with one that is undisclosed or outside agreed limits (`missing_condition`, `undisclosed_condition`, `outside_agreed_limits`).
6. Authority creep: a delegation used for a different PC, scene, turn, domain or contribution, after a control change or revocation, or one granted by a non-controller.

## Delegation modes

The default is strict: no delegation. Every delegation is explicit and scoped (`contribution`, `turn`, `scene` or `campaign`), and a revocation by the current controller ends it.

| Mode | Grants |
|---|---|
| `auto_complete(contribution)` | The director may phrase one submitted action (`voluntary_action`). Keeping that phrasing to the one action is the labeller's job. |
| `incidental_dialogue` | voluntary action and speech |
| `characterization` | voluntary action, speech and interiority |
| `custom` | any explicit protected set, including intent, belief or commitment; these three are never in a preset |

Presets are frozen into explicit domains at grant time. Redefining a preset later never widens an existing grant.

`narration_policy` states, per actor and moment, which domains the director may narrate and which are reserved. A future prompt renderer consumes it. It shares one code path with `review_narration`, and tests check that the two agree.

## Correction flow

`apply_corrections` handles a player's objections to accepted claims:

- **Protected claims about the player's own PC are struck** (`player_correction`). This applies even when the claim was delegated or expressed the player's own contribution.
- **Other claims about the player's PC are only flagged** and stay accepted (`review.disputed`). This covers sensations, external effects, unresolved outcomes and subjectless world facts; any player with an actor in the campaign may flag the last. A flag is a possible-mislabel report for a later referee, never a veto of harm or failure.
- **Resolved outcomes and disclosed condition reactions are refused** (`not_player_authority`). Content limits belong to Session Zero.
- **Corrections by anyone other than the subject's controller are refused** (`not_controller`).

A correction does not revoke a delegation. The player revokes it separately so the overreach does not recur.

## Deferred

- Prose claim extraction and labelling.
- Prompt rendering.
- Session Zero's computation of agreed limits. Until then the rule/effect writer sets `within_agreed_limits`, which defaults to false.
- Persistence of delegations and corrections.
- Policy inheritance across worlds and retconned scenes.
- AI-controlled companions as a third authority class.
