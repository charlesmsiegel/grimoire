"""Sprint 110's adapter ops: card import, V1 migration and guarded review. Called as ``imports.<op>``.

Only Sprint 110 edits this module (or a ``imports`` subpackage that replaces it, keeping ``ADAPTER``). Each op is
``handler(ctx, args) -> dict`` over an existing domain service; see ``grimoire.app.adapter`` and docs/APP_SHELL.md.

The ops are ``grimoire.reviewapi``'s (docs/REVIEW_UI.md), one per review op: ``imports.card_preview`` is the review
API's ``card.preview``. One ``ReviewApi`` is kept per open library, built over the app's own trusted owner handle,
so a proposal previewed in one request can be approved in the next; it holds proposals in memory only.
"""

from __future__ import annotations

import json
import weakref
from collections.abc import Mapping

from grimoire.app.adapter import Adapter, AdapterContext
from grimoire.reviewapi import ReviewApi, ReviewRefused
from grimoire.textapi import ApiRefused

#: each adapter op and the review op it runs
OPS = {
    "capabilities": "review.capabilities",
    "card_inspect": "card.inspect", "card_preview": "card.preview",
    "history_scan": "history.scan", "history_preview": "history.preview",
    "v1_scan": "v1.scan", "v1_preview": "v1.preview", "v1_receipts": "v1.receipts",
    "changeset_review": "changeset.review",
    "rebase_candidates": "rebase.candidates", "rebase_review": "rebase.review", "rebase_preview": "rebase.preview",
    "proposal_get": "proposal.get", "proposal_apply": "proposal.apply", "proposal_reject": "proposal.reject",
}
#: how much of a refusal's details is folded into its message (the shell's client reads reason and message)
MAX_DETAIL = 600

_reviews: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def review_api(ctx: AdapterContext) -> ReviewApi:
    """The one review API of this open library (built on first use, over the app's trusted owner handle)."""
    found = _reviews.get(ctx.api)
    if found is None or found.store is not ctx.store:
        found = ReviewApi(ctx.store, ctx.authority, owner=ctx.owner, handle=ctx.handle)
        _reviews[ctx.api] = found
    return found


def _summary(details: object) -> str:
    if details in (None, {}, []):
        return ""
    text = json.dumps(details, ensure_ascii=False, separators=(", ", ": "), default=str)
    return " (" + (text if len(text) <= MAX_DETAIL else text[:MAX_DETAIL] + "…") + ")"


def _op(name: str):  # type: ignore[no-untyped-def]
    def handler(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
        try:
            answer = review_api(ctx).call(name, args)
        except ReviewRefused as error:
            raise ApiRefused(error.reason, str(error) + _summary(error.details), error.status) from None
        if name == "review.capabilities":
            answer = {**answer, "ops": [f"imports.{op}" for op in OPS]}
        return answer

    handler.__name__ = f"imports_{name.replace('.', '_')}"
    return handler


ADAPTER = Adapter("imports", owner="110", ops={op: _op(name) for op, name in OPS.items()})

__all__ = ["ADAPTER", "OPS", "review_api"]
