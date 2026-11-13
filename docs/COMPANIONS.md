# Capture and trainer association

Sprint 050's first useful slice is a headless service, `grimoire.companions`. It accepts an explicitly adjudicated capture of an **existing qualified creature instance**, associates it with a trainer, optionally spends one device, and places it in the trainer's active party or reserve in one accepted-effect transaction. `grimoire.capture` remains unrelated provider-usage capture.

## Identity and authority

`CaptureRequest` names the encounter's `InstanceRef`, never a display name or species slug. The instance and its source, name, individuality, memories and control assignments are untouched. To encounter a second creature of the same species, first instantiate a distinct individual through the entity/encounter owner. Capture reuses each encountered ID; it never allocates or copies actors.

The directed `trained_by` relation runs **creature → trainer**. It is separate from ownership, `in_party`/`in_reserve`, scene presence and who may speak or decide for the creature. Capture does not make a creature an inventory item or transfer control.

Construct `CaptureLibrary(store, handle, *, issued, clock, new_id, owner=None)` with the same trusted outcome-handle configuration as `TurnEffectsLibrary`. Only a trusted application/director decision supplies the outcome. Narrator prose is never interpreted as acceptance. Current `controls` are required on every acceptance. A player-controlled trainer must contribute and endorse its action; a player-controlled creature must separately contribute and endorse its voluntary association. Backing IDs are supplied in the request and endorsements bind the exact resulting `preview.proposal.fingerprint`. This mechanics-free slice supports voluntary accepted association; coercive ruleset capture is not implicitly authorized.

## Preview and commit

```python
from grimoire.companions import CaptureRequest

request = CaptureRequest(
    attempt_id="capture-1", chat=chat_ref, at=moment,
    trainer=trainer_ref, creature=encountered_creature_ref,
    outcome="success", device="ordinary-ball", capacity=6,
)
review = captures.preview(request)
# Gather any required player contributions and endorsements of review.proposal.
change = captures.accept(review, controls=current_controls,
                         contributions=contributions, endorsements=endorsements)
files_to_review = change.preview()
receipt = change.commit()
```

The request requires a chat and a `Moment` with scene/turn labels. `capacity` is an explicit nonnegative integer; no universal six-member rule is hardcoded. The optional `trainer_backing` and `creature_backing` identify the corresponding player action contributions. Request identifiers and the device slug use the existing safe-segment grammar.

| Outcome | State changes |
|---|---|
| `success` | One `trained_by` and one membership relation, plus one optional device spent |
| `failed` with device | One device spent and an accepted failure recorded; no companion or membership |
| `failed` without device | No writes; a no-effect result |
| `declined` | No attempt committed and no writes, even if a device was selected |

Declined and device-free failed results are explicit **non-events**: they do not reserve a durable attempt ID. Durable receipts and recovery cover state-changing attempts. Use a fresh attempt ID for a later decision.

Placement defaults to `auto`: active (`party`) while the trainer has fewer direct active members than capacity, otherwise `reserve`. Capacity zero means reserve. Explicit `party` when full refuses without spending; explicit `reserve` stays reserve even with room. Reserve is unbounded in this slice. Already trained or already party/reserve-associated creatures are refused; capture is not a transfer operation. Membership ambiguity, cycles, unreadable/unsupported relations and backdated changes fail closed. Active placement under a reserved trainer is refused.

Both endpoints' bytes and the complete relation snapshot are pinned from preview to acceptance and through commit. A concurrent party fill, assignment, replacement or hand edit requires a fresh preview. Holdings and accepted-event checks use the existing guarded writers. Insufficient devices never produce an unpaid companion.

`change.context(requested_by=DIRECTOR)` returns only the capture outcome, exact trainer/creature refs and placement. It is director context, **not an observation or actor memory**. An active member is available to stage, never automatically on stage. Stage a fresh cast with the public [party APIs](PARTY.md); a reserved creature cannot be staged. No previous transcript or private evidence is shared by capture.

## Retry and recovery

An exact retry returns a no-write change only after existing accepted-effect receipts verify. The recorded placement is reused even if the party has since filled. Reusing an attempt with changed capture inputs refuses with `attempt_conflict`; a revoked attempt refuses with `attempt_revoked`. A new attempt needs a new ID.

Capture metadata (`capture_v1`) lives in the accepted event's preserved extension fields, never a second ledger. The event ID is `c50-` plus the SHA-256 of canonical JSON request/placement, followed by the ordinary optional device suffix. Every partial relation/holdings provenance token therefore binds fields omitted from generic effect fingerprints, including device-free attempt IDs, capacity and failed-capture targets. Metadata reads validate both this digest and the reconstructed effect fingerprint. Old graph builds preserve `trained_by` as unsupported rather than guess its meaning.

The native journal recovers interrupted transactions. If an external partial transfer or lost staging leaves only some files, normal retries refuse damaged receipts. Retain `review.to_payload()` as proposal data **before committing**, so orphan adoption can use `CapturePreview.from_payload(saved_json)` after a restart. It is recovery input, not canonical truth. Restored previews lack review pins and cannot authorize a fresh acceptance; obtain a new preview for that.

- Event present: `captures.complete(ref, expected=event_etag)` returns a previewable recovery change.
- Event absent, relation or holdings writes present: `captures.adopt(saved_preview, controls=…, contributions=…, endorsements=…)` returns a previewable recovery change. It verifies the orphan's digest-bound provenance and restores capture metadata in the same transaction.
- Abort: use `captures.effects.revoke(...)` with the existing latest-first and player undo-endorsement requirements. For an event-absent orphan, adopt first. Partial abort uses the existing `partial=True` rules; it never overwrites human repairs.

Capture-aware completion/adoption uses [030 recovery](RECOVERY.md), then checks exclusive trainer association, membership, capacity and ordering after excluding only its own validated receipts. These checks remain pinned through commit. A conflicting intervening edit refuses recovery rather than overfilling a party or replacing another trainer. Calling generic `EffectRecovery` directly bypasses these additional capture rules; capture callers must use the adapter.

`CaptureRefused.reason` identifies request, metadata, attempt, identity, relation, membership, ordering and capacity refusals. Existing `InvalidEffect`, holdings errors and `StaleWrite` retain their contracts; no exception should be retried blindly as a new attempt.

## Integration limits

- Sprint 033 owns creature chat rosters. This service uses its existing public party contracts and does not modify chatstore. Capturing is not inserting a speaker into a running chat.
- Sprint 115 owns the later sequential application controls and context wiring. This headless proof does not claim playable UI integration.
- Ruleset-specific probability/resolution, command delegation, finite reserve capacity, trainer transfer and new creature encounter allocation remain separate adapters/milestones.
- Canonical files and existing sync families remain the owners. Concurrent offline captures are not automatically reconciled into a valid capacity; semantic conflict review belongs to sync integration.
- There are no provider calls, V1 reads/writes, migrations, paid measurements or physical-device claims. Tests use synthetic instances and real native transactions.
