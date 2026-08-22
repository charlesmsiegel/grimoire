"""One model-catalog entry, the same shape whichever provider listed it.

A leaf module, like `llm_errors`: both providers that can list a catalog need
it, and neither may import the other (`test_llm.py` holds the gateway's import
graph acyclic). Nothing here may import from the rest of the package.

The shape is the one the picker already reads -- `frontend/src/api/models.ts`'s
`Model` -- because #149's whole point is that the picker stops caring which
provider answered. Missing metadata stays `None` rather than being defaulted to
zero: a `0` context length and an unpriced model are different facts, and the
combobox already renders nothing for the second.
"""

from __future__ import annotations


def entry(raw: dict) -> dict:
    """One provider's model record, normalized.

    `pricing` is read defensively, and the test is `isinstance` rather than
    truthiness: `"pricing": null` is not hypothetical, and neither is
    `"pricing": "free"` -- a truthy non-mapping, which survives an `or {}` and
    then raises `AttributeError` on `.get`. Both callers normalize *outside*
    their exception funnels, so either one arrives as a 500 for a row the rest
    of the catalog had nothing wrong with.
    """
    pricing = raw.get("pricing")
    if not isinstance(pricing, dict):
        pricing = {}
    return {"id": raw["id"], "name": raw.get("name") or raw["id"],
            "context": raw.get("context_length"),
            "prompt": pricing.get("prompt"), "completion": pricing.get("completion")}


def rows(body: object) -> list | None:
    """The `data` array of a catalog response, or None if this is not one.

    None rather than an exception, and rather than an empty list, because the
    two callers are the two providers and each raises its *own* error class —
    keeping that raise at the call site is what lets this module go on
    importing nothing at all (see the docstring above).

    Empty is not the same answer: a provider that legitimately serves no models
    returns `{"data": []}`, and reporting that as a malformed body would tell
    the reader to check their URL over a perfectly good one. The shapes this
    rejects are the ones that would otherwise crash the normalizer — a
    successful 200 whose body is `{"data": null}`, a bare array, a string, a
    captive portal's JSON — and every one of them reaches the reader as
    `bad_response`, which is what the picker turns into "couldn't load model
    list — type a model id".
    """
    if not isinstance(body, dict):
        return None
    data = body.get("data")
    return data if isinstance(data, list) else None


def entries(raw: list) -> list[dict]:
    """A whole `data` array, normalized and sorted by id.

    Sorted here rather than at each call site so every picker lists every
    provider the same way. It used to be the frontend's job for OpenRouter's
    catalog alone (`models.ts` sorted what it fetched) and nobody's for a
    custom endpoint's, which put two orderings in one combobox depending on
    which connection was open.

    A record with no `id` is dropped rather than raising: it cannot be selected
    (the id is what gets stored on the connection), and one malformed row in a
    catalog of three hundred is not a reason to leave the picker empty.
    """
    return sorted((entry(m) for m in raw if isinstance(m, dict) and m.get("id")),
                  key=lambda m: m["id"])
