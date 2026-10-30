# Session zero — first-slice boundary contract

**Status:** first slice of [Session Zero Boundaries](roadmap/sprints/session-zero-boundaries.md) (Wave 2, #016). It is a pure policy in `backend/src/grimoire/core/boundaries.py`. Field names are a first-slice contract, not a frozen file schema, and nothing is persisted. The policy makes **decisions**, not play: no runner, UI, model call or player interaction executes them yet. The [first text runner](roadmap/sprints/minimal-text-scene-runner.md) (#035) is the first execution owner. Don't describe this as playable skip support.

Boundaries control how the fiction is **depicted** and which **proposals** may go ahead. They are separate from [player sovereignty](PLAYER_SOVEREIGNTY.md), which decides who may author a PC's choices and thoughts, and from adjudication, which decides what happened. A boundary or request can never create, edit or delete an accepted event, outcome or resolution.

## Trust boundary

- **Player-input channel only:** `Boundary` (through `BoundaryChange`), `BoundaryRequest` and `CheckInConsent`. Model output must never be turned into any of these. `set_by`, `requested_by` and `given_by` name the authenticated speaker, and none of them may be `DIRECTOR`.
- **Caller duties (035):**
  - `BoundaryContext.audience` must be the authenticated scene audience.
  - The viewer passed to `owner_boundaries` must be the authenticated user.
  - Boundary ids must be opaque.

  Anonymity holds only under these duties. A caller that fabricates an audience, say `{p3}` alone, can isolate one player's boundaries, and a pure function cannot prevent that.
- **Labeller, not the narrator:** applies themes to segments and conditions. Matching is exact, so give the labeller the configured themes from `visible_boundaries`.
- **`direction`** on a request is player-channel guidance, like a `directorial_request`. It authorizes nothing and cannot change a resolution.

## Configuration

A `Boundary` sets one theme's treatment at one scope: `setting`, `world`, `campaign` or `player`. Themes are lowercase slugs (`graphic_violence`). They are user vocabulary, not a fixed catalog; intensity is expressed as separate themes (`violence` vs `graphic_violence`).

| Treatment | Meaning |
|---|---|
| `allow` | May be depicted. Unlike *unset*, this is an explicit statement. |
| `check_in` | Pause and ask before depicting (the "pause/redirect" preference). |
| `fade_to_black` | May happen; depiction cuts away and the outcome is shown non-graphically. |
| `skip` | May happen; its depiction is passed over. |
| `avoid` | Hard limit: never introduce it in new fiction. |

The order above runs from least to most protective. For each theme the **most protective treatment in force wins**, and `ThemeRuling.conflict` reports any disagreement. A theme with no applicable boundary is **`unset`**: neither a presumed allow nor a presumed limit. An empty `SessionZero()` is valid and means nothing is configured.

Applicability:

- A campaign, world or setting boundary applies when its `scope_id` matches the context.
- A player boundary applies while that player is in the `audience`.
- If the configuration holds world or setting boundaries and the context lacks `world_id` or `setting_id`, every function raises `ValueError` rather than silently dropping them.

### Changes and ownership

`SessionZero.apply(BoundaryChange)` returns a new configuration and checks ownership before anything else:

- **`set`:** add a new boundary or replace your own. A replacement may change only treatment, attribution and note; to change scope, scope id or theme, withdraw and set a new one. Changing the treatment resets group agreement to the setter alone.
- **`withdraw`:** remove your own boundary.
- **`agree` / `unagree`:** add or remove *yourself* in a group boundary's `agreed_by`. You can never record agreement for someone else, and the setter cannot unagree.

All refusals raise the same `BoundaryAuthorityError("not your boundary")`, so a refusal does not confirm that a hidden id exists. A fresh `set` of a taken id is still refused, so ids must be opaque. Because the most protective treatment wins, adding a laxer boundary never weakens another player's.

## Privacy

- **Effects are visible to everyone.** `visible_boundaries` and `ThemeRuling.sources` return `VisibleBoundary` values carrying scope, theme and treatment.
- **The owner is hidden by default.** Unless the owner opted into `attributed`, `set_by` and `boundary_id` are `None`. `scope_id` is also hidden for player boundaries, because it is the player.
- **Ordering uses only visible fields.**
- **Private notes and group agreement stay with the owner.** `private_note` and `agreed_by` appear only in `owner_boundaries(config, viewer)`. `private_note` is excluded from `repr`, `SessionZero`'s `repr` shows only a count, and no error message names an owner or a boundary id.
- **Some inference cannot be prevented.** In a two-player table, everyone can still tell whose effect it is.

## Presentation decisions

`decide_presentation(config, context, segments, consents=())` decides how each labelled `NarrationSegment` is shown.

**Segment states:**

- **`accepted`:** depicts accepted canon and carries its `event_id`.
- **`proposed`:** proposes new canonical content and has no `event_id`.
- **Unlabelled** (`themes=None`): the segment is held as `hold_unlabelled` whenever any limit is in force.
- **Labelled with no themes** (`frozenset()`): the segment is presented.

| Strictest treatment | Proposed segment | Accepted segment |
|---|---|---|
| unset / `allow` / cleared check-in | `present` | `present` |
| `check_in` | `check_in` | `check_in` |
| `fade_to_black` | `fade_to_black` | `fade_to_black` |
| `skip` | `withhold_proposal` (`skipped_proposal`) | `skip` |
| `avoid` | `withhold_proposal` (`avoided_theme`) | `skip` (`canon_touches_avoided_theme`) |

- **A skipped or avoided proposal is withheld,** so it can never become canon unseen. The director re-proposes it, for example as a transition summary, and the new proposal goes back through the labeller, `review_narration` and this function.
- **Accepted canon is never changed.** An accepted segment always reports its `preserved_event_id`, and `SegmentDecision` has no field that could edit canon. A boundary added after an event was recorded changes only how that event is shown from then on.
- **A check-in is cleared** for one segment and theme only when **every audience member** has given a matching `CheckInConsent`. The ruling still reads `check_in`, and the decision carries the notice `check_in_cleared`. Collecting consent is 035's job.

## Mid-session requests

`decide_request(request, context, segments)` handles a participant's `pause`, `skip`, `redirect` or `rephrase`.

- **No reason is ever asked for.** The decision never echoes the requester and is the same for any participant in the audience.
- **One participant's request applies to the shared presentation**; there is no vote.
- **A request never touches the configuration**, and it works with none.

| Kind | Targets | Effect |
|---|---|---|
| `pause` | none | `halt=True` |
| `skip` | ≥1 | accepted → `skip_presentation`; proposals → `withdrawn_proposals` |
| `redirect` | ≥1 | proposals → `withdrawn_proposals`, with `direction`; accepted unchanged |
| `rephrase` | ≥1 | all → `rerender`, with `direction` |

- **Ids:** `skip_presentation`, `withdrawn_proposals` and `rerender` hold segment ids; `preserved_events` holds the event ids of accepted targets.
- **Rephrasing is a new proposal for the same canon.** It goes back through `review_narration`, which still enforces recorded resolutions and PC authorship (pinned by `test_rephrase_cannot_launder_outcome_or_pc_speech`).
- **Refusals change nothing.** They are checked in this order: `cross_campaign`, `not_participant`, `unknown_segment`.

## Agreed limits for imposed conditions

`agreed_limits(themes, config, context, *, controller, target_id, consents=())` computes `ImposedCondition.within_agreed_limits` for a rule- or effect-imposed condition such as fear or compulsion. `controller` is the affected actor's controller, from `controller_of`.

- **`DIRECTOR`** gives `npc` (within). **`None`** raises.
- **The controller's own player boundaries always apply**, even when the controller is not in the audience.
- **A theme counts as agreed** when its strictest treatment is `allow`, `fade_to_black` or `skip` (these limit depiction, not occurrence) **and** the controller agreed to it. Agreement means either the controller's own boundary for the theme, or membership in `agreed_by` of an applicable group boundary. A campaign `allow` that another player set does not count.
- **A `check_in` theme** is agreed only through the controller's own consent for this `target_id`.
- **Failures** are reported as the worst one across all themes: `avoided` > `needs_check_in` > `unset` > `not_agreed`.

Nothing caches the result. Callers recompute it on every review, so a tightened boundary applies on the next turn.

## Deferred, with owners

| Deferred work | Owner |
|---|---|
| Persistence of configuration | [Native File Store](roadmap/sprints/native-file-store-and-revisions.md) (#010), as an opaque record that 010 does not interpret |
| Carrying over only genuine V1 preferences; missing ones stay unset | [V1 import](roadmap/sprints/v1-to-v2-import-apply.md) (#026) |
| Rendering visible rulings into prompts, never `SessionZero` directly | #027 |
| Authenticated audience and speakers, consent collection, request execution, extending the [intent router](intent-routing.md) with skip/redirect lanes, a visible correction | #035 |
| Group governance of shared scopes (who may author or withdraw a campaign, world or setting boundary beyond its recorder) | [Collaborative Multiplayer](roadmap/sprints/collaborative-multiplayer.md) |
| Theme synonyms or taxonomies, audio/visual staging, confidential multi-player negotiation | Later milestones |

## Downstream consumers

#010, #026, #027 and #035, plus Player Sovereignty's rule/effect writer through `agreed_limits`.
