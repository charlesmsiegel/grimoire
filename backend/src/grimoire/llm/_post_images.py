"""Post images (#377) for the facade: how many an attempt may carry, each one
loaded, and a prompt's content parts lowered to what that attempt is sent.

Both lookups are the store's, so both arrive as callables the facade was
constructed with; this module, like the rest of the package, imports no store.
"""

from __future__ import annotations

from .. import content_parts
from . import _routes
from ._routes import _Route as _Route


class _PostImages:
    """`images(attempt)` is how many a prompt for that attempt may carry right
    now (0 = none) and `load_image(campaign, part)` turns one reference into a
    data URI. Either may be None: no images, and nothing loaded."""

    def __init__(self, images=None, load_image=None):
        self._images = images
        self._load_image = load_image

    def lower(self, messages: list[dict], route: _Route,
              campaign: str) -> tuple[list[dict], int]:
        """`(messages lowered for route, how many images they carry)`."""
        keep = 0 if route.target.degrade else self._budget(route)
        if keep <= 0:
            return content_parts.as_text(messages), 0
        return content_parts.as_images(messages, keep, lambda part: self._load(campaign, part))

    def _budget(self, route: _Route) -> int:
        """How many images `route` may be sent now. Never more than 0 for a kind
        whose client cannot carry a part, and 0 when the resolver is missing or
        raises: a broken lookup must not fail a turn the text would serve. The
        resolver is handed the attempt's target, less the degrade marker."""
        if self._images is None or route.target.kind in _routes.TEXT_ONLY_KINDS:
            return 0
        try:
            return max(0, int(self._images(route.sent())))
        except Exception as exc:  # noqa: BLE001 - see the docstring
            _routes.log.warning("could not resolve whether %r reads images: %s", _routes._label(route.target), exc)
            return 0

    def _load(self, campaign: str, part: dict) -> str | None:
        if self._load_image is None:
            return None
        try:
            return self._load_image(campaign, part)
        except Exception as exc:  # noqa: BLE001 - one picture is never worth a turn
            _routes.log.warning("could not load a post image to send: %s", exc)
            return None
