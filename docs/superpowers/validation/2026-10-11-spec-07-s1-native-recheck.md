# Spec 07 S1 native identity boundary: independent brutal recheck

Date: 2026-10-11

Frozen commit: `5abe9c8de826df8e217ec40cc4b3f6afc98f2f89`.
Previous checkpoint: `09d1460bb346aa87cc461b695169f1fe01d6ed50`.

**Verdict: non-blocking within this scope.** The supported detached take-world native-alias finding is resolved. The parallel pinned boundary and centralized owned-only Members Undo lookup also survived the checks below. No remaining grounded finding in this narrow recheck. This is not final whole-S1 certification.

Applied the brutal-review skill and existing approved spec/plan requirements. Reviewed committed sources extracted to Temp, independent of concurrent UI work. No product/test edits, commits, private-store access or live application runs.

## Reviewed repair

`overlay.record_is_marked` checks an exact marker first, then proves native identity using `samefile` on the relevant record paths. It validates kinds/ids before alias probing, handles genuine missing files as no proof, and turns other identity-access errors into `GroupIdentityConflict`. It neither reads actor bodies nor depends on a membership index. Both detached and pinned exclusions in `sync._advance` use this predicate under their existing campaign hold. `independent_owned_record` reuses it for World-move exclusion.

`overlay.require_owned_entity` calls `entities.require_entity` with only the campaign root. Members Undo still validates scope, kind, lifetime metadata and resolved-path containment, and still refuses when the owned file is absent. Moving this lookup into overlay does not introduce an inherited fallback or expand guard exemptions.

## Independent reproductions

**Original blocking input, rerun unchanged through the typed route handler:** create a World Salt Circle; materialize its campaign copy by saving Members `characters:mara` with an Undo offer; delete the World original; recreate the World slug with Members `pcs:winifred`; submit uppercase `SALT-CIRCLE` to `routes.campaigns.post_accept` after proving the uppercase and lowercase owned paths are the same native file.

```text
detached {'groups/salt-circle'}
alias_samefile True
route_response {'ok': True}
owned_still_exists True
effective_members characters:mara
retired None
```

The original owned file and its offer now survive, rather than exposing the replacement and retiring the offer.

**Parallel pin boundary:** create owned and World Salt Circle records with different Members, an old common-version manifest entry, a Members Undo offer and an exact lowercase pin. Submit the uppercase alias through both typed accept and reject route handlers. A before/after byte snapshot of every campaign file was identical. This covers the publication/removal and base-advancement directions.

**Owned-only preservation:** with a matching World group and no owned group, `overlay.require_owned_entity` raises `EntityNotFound`. The route regression for a Members target with a missing owned copy also passed; World content cannot satisfy the sealed target.

**Identity policy and access failure:** a Temp-only `samefile -> False` simulation preserves the distinction between differently spelled identities while exact markers still match. Injected `PermissionError` during alias proof raises `GroupIdentityConflict` instead of allowing the operation. This is explicitly a case-sensitive identity-policy simulation, not a physical case-sensitive-volume run.

## Verification

Configured frozen lint: `python scripts/ratchet.py ruff` passed, **1,146 findings, all at baseline**.

Frozen tests, isolated synthetic home, pytest cache disabled:

```text
backend/tests/test_group_membership_lifecycle.py
backend/tests/test_group_membership_routes.py
backend/tests/test_sync_store.py
backend/tests/test_sync_pins.py
backend/tests/test_reclassify_store.py
backend/tests/test_overlay_guard.py
185 passed, 1 existing FastAPI/Starlette warning (53.71s)
```

The two native detached/pinned alias tests ran rather than skipping on this host. Independent disposable-store scripts additionally verified the original public-handler failure, pinned accept/reject whole-tree preservation, World-only owned-lookup refusal, distinct-identity simulation and access-failure refusal. The overlay guard passed without exemption expansion.

## Not reviewed

Scope is the supported sync alias finding, its parallel pin boundary, and preservation of the centralized owned-only Undo lookup. No certification of live or later commits, ongoing Task 5 UI, final whole-S1 integration, S2-S8, browser behavior, the complete Task 4 suite, full `make check`, performance or broad crash recovery. Earlier journal/World failure findings were covered by their separate frozen rechecks and are not re-audited wholesale here. No physical case-sensitive volume was used. The optional sibling `python-code-doctor/scripts/analyze_diff.py` pass was not run; configured lint ran and passed.
