"""Original shared resolution vocabulary, sentinels and tuple shapes; each shared mapping remains a single object."""

from __future__ import annotations

from typing import NamedTuple

from .. import capabilities
from ..resolved import Attempt

#: What a connection carries when no preset could be resolved for it at all.
NO_SAMPLING = {"preset_id": "", "preset_name": "", "scope": "none", "params": {}}

#: Where a sampler preset came from, for the scopes that are about the ROUTE
#: rather than the connection: a preset from one of these follows the route onto
#: the fallback. `llm.ROUTE_SCOPES`, restated because this module never imports
#: `llm`; a test holds the two equal.
ROUTE_SCOPES = frozenset({"campaign", "global"})

#: Why a fallback on the primary's own provider is left out of `attempts`
#: (`fallback_problem`): a second try on the connection that just failed is not
#: a fallback (`llm._same_route`). A name of its own, so the one resolution
#: that keeps such a fallback -- a decide primary that cannot generate, whose
#: fallback on another model is a stage apart (`_apart`) -- lifts this reason
#: where it lifts the drop, and leaves the credential ones (`problem`) alone.
#: The same model there is still a second try, and still dropped with it.
SAME_PROVIDER = "it is on the primary's own provider"

#: Why a task's fallback is left out of `attempts` when its code policy sends
#: none (`routing.TaskPolicy.fallback == "none"`, spec 01d §4.2). Outranks the
#: other reasons: on such a task the policy is why the fallback is unsent,
#: whatever else is true of it. Said only for a fallback the cascade chose,
#: so the readout never reports dropping one that did not exist.
NO_FALLBACK_POLICY = "this task's policy sends no fallback"


#: The facts a target is built with, and what each sends unstated (spec 4.2):
#: post images on auto, no prefill, no post-processing -- the defaults a
#: legacy connection carried (`_stated`).
_UNSTATED = {"vision": "", "prefill": False, "post_process": "none"}


#: A capability nothing has said anything about.
_UNKNOWN_CAP = capabilities.Cap(capabilities.UNKNOWN, "unknown")


#: The capabilities an operation needs of itself, as ALTERNATIVES: an attempt
#: meets the operation when it is not known unable to do every one of them.
#: A decision is answered natively (`decide_native`) or by structured
#: generation (`generate`), so a model needs either. Read off
#: `capabilities.NEEDS`, the picker's own table, so the seam and the picker
#: hold one answer to "what does a decision need".
OPERATION_CAPABILITY: dict[str, tuple[str, ...]] = {
    op: capabilities.NEEDS[op] for op in ("generate", "embed", "decide")}


#: The sources whose `no` is a guess rather than knowledge: the name rule
#: reads "embed" in an id, and a chat model can carry that word. Such a `no`
#: hides a model in a picker (`capabilities.group_for`) but is never missing,
#: so the seam never refuses on it.
_GUESSES = frozenset({"name"})


class Silence(NamedTuple):
    """One scope's own choice left out of a resolution: what that scope's row
    would run on if it were cleared (`settings.view`'s `inherits`).

    The keys are dropped from that scope's view AFTER the planner's overlay,
    so a legacy `route_<k>` is silenced as the pin it reads as."""

    #: "global" or "campaign".
    scope: str
    keys: frozenset[str]


class _Tail(NamedTuple):
    """What `_chain` settles about a resolution's attempts."""

    attempts: tuple[Attempt, ...]
    missing: tuple[str, ...]
    fallback_missing: tuple[str, ...]
    #: Whether the fallback rides the primary (`ResolvedInference.rides`).
    rides: bool


#: Why the Embedding role names no space because of its model's facts (01h):
#: the facts file is held by another program, does not parse, or states an
#: `embedding` block the validator refuses. Each turns embedding off for that
#: read rather than reading as "no options" -- which would move the space
#: unasked (`ResolvedInference.embed_options_problem`).
OPTIONS_HELD = "held"
OPTIONS_MANGLED = "mangled"
OPTIONS_INVALID = "invalid"


#: What the Embedding role's one attempt must be able to do.
_EMBED_NEEDS = (frozenset(OPERATION_CAPABILITY["embed"]),)


class EmbedAttempt(NamedTuple):
    """The Embedding role's one attempt on a provider record, and what follows
    from it (`embed_attempt`)."""

    attempt: Attempt
    #: `embed`, when the attempt is known (`no`) not to make embeddings.
    missing: tuple[str, ...]
    #: The space it embeds in (`space_of`), or None when it embeds nothing.
    space_id: str | None
    #: Why its model's facts left it no space (`OPTIONS_HELD`,
    #: `OPTIONS_MANGLED`, `OPTIONS_INVALID`), or "".
    options_problem: str = ""


# ---- the refusal ----
#: A refusal: the HTTP status and the body the seam answers with. The body is
#: a `{detail, kind}` dict, or -- for an image route on a wire protocol that
#: cannot carry an image -- the bare `image_drafts.UNSUPPORTED` sentence that
#: route has always answered with.
Refusal = tuple[int, dict | str]
