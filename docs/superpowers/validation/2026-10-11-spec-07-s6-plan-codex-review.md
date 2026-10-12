# Actual Codex07-S6 plan gate

Frozen `1246e34b7b2a6eb87546bfab07d6e17d55656331`; installed native thread `01a12e84-3085-7e53-83e7-f51f9aefe708`.
Verdict **needs-attention**. Public immutable spec, plan, approved S2 producer
contract and owning source supplied; implementation/full gate not assessed.

Block the S6 plan gate: ledger-error handling contradicts the spec, and family ordering can omit valid affiliations. No implementation was reviewed.

## Propagate strict ledger failures

Malformed or unreadable deleted.json/detached.json makes S2's campaign_index raise GroupIdentityConflict. This plan wraps that call in _attempt and explicitly tests successful partial graph delivery. Spec 7.3, 7.5 and C2 require these identity/visibility failures to propagate before any graph payload; ordinary group-file failures alone may omit groups. The proposed test enshrines the wrong failure contract and suppresses the required repair refusal.

Correction: Separate GroupIdentityConflict from ordinary group-source errors. Require ledger failures to abort graph delivery with the repair error, including warm-memo cases; retain group-only omission tests for unreadable group files.

## Place groups after every actor-producing family

_record_parts ('plot') and _temporal_parts ('events') create actor nodes after 'chronicle'. The stated insertion interval permits placing groups immediately after chronicle; that interpretation would miss an affiliate represented solely by an involves or birthday_of edge, even though the final payload contains that actor. The planned membership tests cover scene/relationship actors and cannot detect this ordering failure.

Correction: Specify groups immediately after events and before scene_ideas. Add tests for actors introduced solely through thread/commitment involvement and birthday occurrences, asserting their membership and leadership edges.

## SHA-bound corrected-plan approval

Frozen `2355a68dd85ff12baddda2a1b0d49d802fe1acc6`; native persisted thread `01a12e84-3085-7e53-83e7-f51f9aefe708`. Verdict **approve**, no findings.

Approve the 07-S6 plan at 2355a68dd85ff12baddda2a1b0d49d802fe1acc6. The corrected diff confirms strict-error propagation with required cold/warm HTTP409 tests, and ordering after all actor-producing families with plot/birthday-only affiliate tests. No remaining material blocker is supported. Plan-only approval; no implementation or full-gate claim.

The first corrected-text recheck included the stale initial creation diff and
was not recorded as SHA-bound approval. The final packet was rebuilt entirely
from git show at this HEAD and the exact corrected plan diff; the gate above
is the actual approval. No implementation/full-gate claim.
