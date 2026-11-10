"""The V2 text API (Sprint 044): JSON ops a phone or desktop client calls to play one library.

An adapter over 107, 035, 052, 038 and 028, never a second store. See docs/TEXT_API.md.
"""

from .api import DEFAULT_SLOT, MAX_ART_BYTES, MAX_POST_CHARS, TextApi, field, parse_ref
from .errors import ApiRefused

__all__ = ["ApiRefused", "DEFAULT_SLOT", "MAX_ART_BYTES", "MAX_POST_CHARS", "TextApi", "field", "parse_ref"]
