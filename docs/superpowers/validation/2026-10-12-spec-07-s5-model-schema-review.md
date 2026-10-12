# S5 extraction checkpoint independent review

**Verdict: NON-BLOCKING**, with one P3 parser-tolerance defect. Scope is frozen `9d2ed0ebdefb5144abdaa1503e4b0296d45fa500` versus `558a096f3`: extraction models, portable schema adapter, prompt/generation handoff and raw null/clear behavior. Pending membership lifecycle, staging and apply are explicitly outside this checkpoint.

## P3 — Membership change words are not case-normalized

Where: `backend/src/grimoire/store/absorb/parse.py`, new `membership_changes` comprehension.

Input: `{"membership_changes":[{"group":"groups:salt-circle","actor":"characters:mara","change":" JOIN "}]}`.

Observed: `parse_output(...)["membership_changes"] == []`. Spec 9.3 explicitly calls for stripping/lowercasing the change before comparing `join`/`leave`; expected one row with `change: "join"`. Schema-enforced responses already use lowercase, so this affects tolerant parsing of non-structured/fallback responses rather than valid strict-schema output. Lowercase only the change word before the enum check; preserve actor/group spelling.

Reproduced in reviewer-only `backend/tests/test_schema_independent_review.py` inside `C:/Users/charl/AppData/Local/Temp/spec07-s5-schema-9d2ed0e`, extracted from the frozen commit. The exact test `test_membership_change_word_uses_spec_lowercase_normalization` fails with expected join row versus empty list.

## Passing evidence

Shared repository Python with `PYTHONPATH=<frozen snapshot>/backend/src`:

`python -m pytest backend/tests/test_schema_independent_review.py backend/tests/test_absorb_model_schema.py -q --disable-warnings`

Result: **1 failed, 6 passed, 1 warning in 3.21s**. The one failure is the P3 above. Independent nested-model adversary covers optional referenced model, nullable list items, model/string/null union, nested nullable field and single Literal normalization. It passed. Existing five targeted controls also passed: recursive closed-object schema, complete top-level extraction keys and wire `from`, raw null/empty distinctions, exact `schemas.render` prompt inclusion accepted by `_check_structured`, preservation of current state during knowledge-only edits, and actual final-field actor/group clears.

Code inspection confirms the extraction call passes the same deterministic model-derived schema through `operations.generate(..., schema=...)`; the raw response still enters the tolerant parser without model-default dumping. All preexisting section fields, identity/citation fields, deadline fields and weather fields are represented by typed models. The schema adapter remains outside the stdlib-only portable validator. No defect was established in the current extraction model's union/ref handling.

## Limits

This was a quick scoped checkpoint gate, not the final S5 implementation gate. No lifecycle/apply membership certification, live-provider call, broad test repetition or full branch check. The independent probe used the shared Python environment; the owner's actual Python 3.11/Pydantic1 evidence was not rerun independently. Generic schemas outside the current finite typed-model use case are not exhaustively certified. Mechanical lint/type results are owner-provided; optional detector not run. No production edits, tracked test changes, commits or private-store reads.
