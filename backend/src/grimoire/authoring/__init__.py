"""World authoring proposals (087): bounded suggestions reviewed and accepted in part, never canon by themselves.

Pure values and the provider-backed proposer. Reviewing a proposal against a world and writing the
accepted subset is ``grimoire.authoringstore``'s (an adapter). See docs/AUTHORING.md.
"""

from .generate import AuthoringGenerator, authoring_messages, parse_proposal
from .model import (
    ID_MAX, KINDS, MAX_CANDIDATES, MAX_LINKS, RELATIONS, ROLES, AuthoringContext, AuthoringError, AuthoringRequest,
    Candidate, Decision, Duplicate, Link, LinkProblem, Proposal, Selection, WorldRecord, name_key, select,
)

__all__ = [
    "ID_MAX", "KINDS", "MAX_CANDIDATES", "MAX_LINKS", "RELATIONS", "ROLES", "AuthoringContext", "AuthoringError",
    "AuthoringGenerator", "AuthoringRequest", "Candidate", "Decision", "Duplicate", "Link", "LinkProblem", "Proposal",
    "Selection", "WorldRecord", "authoring_messages", "name_key", "parse_proposal", "select",
]
