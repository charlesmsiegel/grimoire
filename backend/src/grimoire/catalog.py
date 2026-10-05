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
    out = {"id": raw["id"], "name": raw.get("name") or raw["id"],
           "context": _context(raw),
           "prompt": pricing.get("prompt"), "completion": pricing.get("completion"),
           "vision": _vision(raw)}
    # Which request parameters the model takes, when the provider says
    # (OpenRouter's `supported_parameters`). Kept only as a list of strings and
    # only when present: an absent list means "unknown", which the sampler split
    # treats as "send everything and say it is unverified" -- a different fact
    # from an empty list, which says the model takes none of them.
    params = raw.get("supported_parameters")
    if isinstance(params, list):
        out["params"] = [p for p in params if isinstance(p, str)]
    return out


def _vision(raw: dict) -> bool | None:
    """Whether the provider says this model reads images (#377).

    OpenRouter publishes `architecture.input_modalities`; an OpenAI-compatible
    endpoint occasionally does through the same field. None when there is no
    such list -- "the provider did not say", which is a different fact from
    "text only", for the same reason an unpriced model is None rather than 0.
    """
    arch = raw.get("architecture")
    modalities = arch.get("input_modalities") if isinstance(arch, dict) else None
    if not isinstance(modalities, list):
        return None
    return "image" in modalities


def _context(raw: dict) -> int | None:
    """The model's context window, from whichever field this server names it.

    `context_length` is OpenRouter's; `max_model_len` is vLLM's, and it is the
    window the server was launched with rather than the one the weights were
    trained to -- which is the one a prompt has to fit. llama.cpp's `meta.n_ctx_train` is deliberately NOT read:
    it is the training length, and a server started with a smaller `-c` would
    have the inspector drawing a bar against a window it does not have. Ollama
    names none here, so its models stay unknown -- `None`, never `0`.

    Only a positive whole number counts (`128000.0` included, since JSON does
    not distinguish it); anything else is a field that does not say.
    """
    for key in ("context_length", "max_model_len"):
        value = raw.get(key)
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
    return None


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
