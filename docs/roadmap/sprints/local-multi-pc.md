# One Player, Multiple PCs per Turn

**Outcome:** Compose actions for multiple PCs and advance the scene once with correct speaker and knowledge attribution.

## Scope
- Provide separate editable PC drafts with reorder/correction before one combined submission.
- Distinguish controller, PC, present cast, NPC and actual speaker in the transcript.
- Avoid intermediate GM responses or duplicate posts during partial failure.

## Milestones
1. Compose and submit a two-PC combined turn.
2. Persist drafts and recover an interrupted submission.
3. Support troupe-style play and multiple simultaneous protagonists.

## Proving scenario
Two PCs speak, the narrator responds once, and each post retains the correct author; no NPC is accidentally promoted into a PC.

## Independence and interfaces
A local UI + turn envelope change; does not depend on network collaboration or voice diarization.

## Inherited from Wave 1

The [Wave 1 audit](../README.md#wave-1-status) left these with this sprint. *Obligations* are contracts it must not violate; *first-proof additions* enlarge its first slice and are listed in the roadmap row.

**Obligations**
- Map routed intents to contributions with the actual speaker as submitter ([intent routing](../../intent-routing.md#handing-a-pc-intent-to-the-authority-policy)), and reconcile authority `controller_id`s with `grimoire.security` principals (003, 007, 008).
- Record control history, or end delegations explicitly on a control change. Today a delegation revives when its grantor regains control ([player sovereignty](../../PLAYER_SOVEREIGNTY.md)).

## Open questions
- Should drafts survive restart?
- How is ordering determined?
- When is one versus multiple narrator responses appropriate?

## Follow-up TODOs after the first useful slice

[PR #711](https://github.com/charlesmsiegel/grimoire/pull/711) landed milestone 1:
independent local controllers, editable/reorderable drafts and one attributed combined
turn. The overlapping implementation in [PR #699](https://github.com/charlesmsiegel/grimoire/pull/699)
is superseded. These unchecked follow-ups do not reopen the completed 058 first-useful
roadmap row or change completion totals. They record work, not implemented behavior.

### Verified defect

- [ ] Include secondary `also` posts in `continuitywire.projection.scene_revision`.
  At `21cddd665`, changing only a secondary post's text leaves the semantic hash
  unchanged because `TURN_FIELDS` omits `also`; `continuitystore.snapshot` uses that
  hash for turn source pins. Add regressions proving secondary text, author, audience
  and order changes invalidate pins, while inference-call bookkeeping remains excluded.

### Consent and recovery enhancements

- [ ] Add explicit per-controller approval of the final draft revision and order,
  including consent to share unpublished text. Invalidate approval on edits, reorder,
  participant changes and control-history changes, including control away and back.
  The landed shared-screen API currently submits drafts without this approval step;
  this is a same-device consent boundary, not multiplayer authentication.
- [ ] Persist device-private drafts with guarded writes and restart recovery
  (milestone 2). Define who may read each unpublished draft and explicitly consent
  to sharing; preserve replication exclusions and require fresh approval after restart.
  Landed drafts are in memory and visible in the owner's shared-screen view.
- [ ] Add durable submission receipts for lost acknowledgements and restart-safe
  retries (milestone 2). A matching retry must recover the original accepted turn
  without duplicate posts, generation or effects; a conflicting retry must refuse.
  Current retry support is limited to the matching open turn before generation.

Implement follow-ups against the landed `grimoire.scene-turn/2` / `also` contract,
`post_together`, and existing local-player API and renderers. Do not replay #699's
incompatible `/1` / `extra_posts` schema: the landed decoder does not recognize its
secondary contributions as posts. Review any migration and consent adapter before
changing persisted state or the shared-device trust model. Drafting UI and physical
device acceptance remain separate work; no enhancement above is claimed complete.
