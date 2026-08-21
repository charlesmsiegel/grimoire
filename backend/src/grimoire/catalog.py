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

    `pricing` is read defensively -- `or {}` rather than `.get("pricing", {})`
    -- because an endpoint that sends `"pricing": null` is not hypothetical
    and would otherwise raise inside a listing that has nothing else wrong
    with it.
    """
    pricing = raw.get("pricing") or {}
    return {"id": raw["id"], "name": raw.get("name") or raw["id"],
            "context": raw.get("context_length"),
            "prompt": pricing.get("prompt"), "completion": pricing.get("completion")}


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
