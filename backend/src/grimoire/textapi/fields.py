"""How every text API op reads its fields (044), shared with the app's adapter ops (108) and the op modules beside
``api.py``: a field is exactly the kind it should be, and a reference is parsed by its owning backend's parser."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from .errors import ApiRefused


def field(args: Mapping[str, object], key: str, kind: type, *, required: bool = True, default=None):  # type: ignore[no-untyped-def]
    """``args[key]`` as exactly ``kind`` (``bool`` is not an ``int``), else ``invalid_field``. Shared with the app's
    adapter ops (108) so every op checks its fields the same way."""
    value = args.get(key, default)
    if value is None and not required:
        return default
    if type(value) is not kind:
        raise ApiRefused("invalid_field", f"{key} must be a {kind.__name__}")
    return value


def parse_ref(parse: Callable[[str], object], args: Mapping[str, object], key: str, *, required: bool = True):  # type: ignore[no-untyped-def]
    """``args[key]`` parsed by an owning backend's own parser (``SourceRef.parse``, ``ChatRef.parse``...), else
    ``invalid_field``: a reference travels as its canonical string and is never guessed from a name."""
    text = field(args, key, str, required=required)
    if text is None:
        return None
    try:
        return parse(text)
    except (ValueError, TypeError):
        raise ApiRefused("invalid_field", f"{key} is not a valid reference") from None


__all__ = ["field", "parse_ref"]
