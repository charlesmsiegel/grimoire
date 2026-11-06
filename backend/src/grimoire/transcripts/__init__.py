"""Chat transcripts: posts, speakers and variants as stored pages (Sprint 036). Pure.

A transcript is evidence of what was *written*, never of who heard it; see docs/TRANSCRIPTS.md.
The store keeps pages create-only under ``campaigns/<c>/transcripts/<chat>/<seq>.json``.
"""

from .codec import IDENTITY_KEYS, page_from_payload, page_to_payload
from .model import (
    AUDIENCE_UNKNOWN, MAX_LABEL, MAX_POSTS_PER_PAGE, MAX_VARIANTS, ROLES, TRANSCRIPT_SCHEMA, Post, Speaker,
    TranscriptError, TranscriptPage, Variant, opaque, paginate,
)

__all__ = [
    "AUDIENCE_UNKNOWN", "IDENTITY_KEYS", "MAX_LABEL", "MAX_POSTS_PER_PAGE", "MAX_VARIANTS", "ROLES",
    "TRANSCRIPT_SCHEMA", "Post", "Speaker", "TranscriptError", "TranscriptPage", "Variant", "opaque",
    "page_from_payload", "page_to_payload", "paginate",
]
