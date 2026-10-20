"""Request models: continuity domain."""


from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

# ---- models ----

class PinRule(BaseModel):
    """One user pin or exclude (#129). `sid` is required for the default
    scene scope; `ttl_posts` counts posts and only a scene rule may carry one
    (see store/pins.py). Literal-typed where the value space is closed, so a
    typo is a 422 rather than a rule that silently matches nothing -- the store
    refuses the same values again, since it is reachable from other callers."""
    ref: str
    mode: Literal["pin", "exclude"]
    scope: Literal["scene", "campaign"] = "scene"
    sid: str = ""
    ttl_posts: int = 0


# ---- hand edits to the continuity ledger (routes/ledger.py) ------------------
#
# Every optional field here is `| None = None` rather than `= ""`, and the
# distinction is load-bearing rather than stylistic: the store mutators these
# reach read a blank as "keep what is stored" (a title, a status) or as "clear
# it" (a commitment's due date), so a model that defaulted to `""` would send
# an instruction the caller never gave. None means the payload said nothing.


class ThreadSave(BaseModel):
    """A plot thread. Blank `title` or an unrecognized `status` keeps the stored
    value, per `plot.set_movement`; `beat` is APPENDED when present, since a
    beat is a thing that happened and there is no editing the list in place."""
    title: str = ""
    status: str = ""
    beat: str = ""
    #: The scene the thread was last moved in. None leaves it where it is —
    #: which is what closing a thread from the ledger should do.
    scene: str | None = None


class CommitmentSave(BaseModel):
    """A commitment. `due` is three-valued all the way down to the store: None
    keeps the stored deadline, `""` clears it, text sets it."""
    title: str = ""
    kind: str = ""
    status: str = ""
    due: str | None = None
    beat: str = ""
    scene: str | None = None


class FactSave(BaseModel):
    """A correction to a stored fact — the user's edit, never the pass's. Each
    field is None to leave it alone, so one can be fixed without restating the
    others. Says nothing about the lifecycle: retiring has its own route."""
    text: str | None = None
    date: str | None = None
    scene: str | None = None


class KnowledgePost(BaseModel):
    """One post a knowledge statement is about, by its key (`r-<response_id>`
    with its part, or `p-<post_id>`). An empty key names a post that has none,
    which only an `unaware` may be about (roadmap 11 §7.4)."""
    key: str = ""
    part: str = ""


class KnowledgeFingerprint(BaseModel):
    """What the client was shown of one post: a precondition, never what is
    stored. A grant is refused 409 `moved` when the post no longer reads so --
    its raw text (`variant`, `digest`) or the prompt-view text it displayed
    (`shown`, the digest of what an actor's prompt would carry)."""
    key: str = ""
    part: str = ""
    variant: str = ""
    digest: str = ""
    shown: str = ""


class KnowledgeSave(BaseModel):
    """A knowledge statement written by hand, or accepted from a capture.
    `scene` is the sid. Exactly one of `posts` and `whole_scene`. A grant
    (`knows`/`suspects`) carries what the user was shown -- `expect` for
    posts, `expect_digest` and `expect_shown` for the whole scene -- and the
    route refuses it when the text moved since; the stored fingerprint is
    computed here. `audience_name` is the name the form displayed for the
    audience: when given, a different current name is 409 `moved`, so a
    record renamed or replaced under the open form is never the one granted."""
    audience: str = ""
    status: str = ""
    scene: str = ""
    posts: list[KnowledgePost] | None = None
    whole_scene: bool = False
    expect: list[KnowledgeFingerprint] | None = None
    expect_digest: str = ""
    expect_shown: str = ""
    audience_name: str = ""
    note: str = ""
    source: str = "manual"


class KnowledgeEdit(BaseModel):
    """A correction to a stored statement; each field None leaves it alone.
    A new subject is `scene` with `posts` or `whole_scene`. `restamp`
    re-fingerprints the stored subject -- the ledger's "confirm against the
    current text" -- and, for a grant, needs `expect`/`expect_digest` as a POST
    does. Without either the stored fingerprint is kept, so a note edit never
    revalidates a stale grant."""
    audience: str | None = None
    status: str | None = None
    note: str | None = None
    scene: str | None = None
    posts: list[KnowledgePost] | None = None
    whole_scene: bool = False
    expect: list[KnowledgeFingerprint] | None = None
    expect_digest: str = ""
    expect_shown: str = ""
    audience_name: str | None = None
    restamp: bool = False


class FactRecord(BaseModel):
    """A fact recorded by hand. `supersedes` retires the named fact and points
    the two at each other in one write, exactly as an extracted one does."""
    text: str = ""
    date: str = ""
    scene: str = ""
    supersedes: str = ""


class FactRetire(BaseModel):
    """The scene that ended a fact. Blank for a retirement that belongs to no
    scene, which a hand edit legitimately does not."""
    scene: str = ""


class RelationshipSave(BaseModel):
    """Where two people stand. `bond` present addresses the undirected record
    and the meters are ignored; absent, this is the directional feeling `a`
    holds toward `b`.

    Every editable field is None-by-default for the reason the rest of this
    block is: `set_feeling` writes whole records, so a payload that only moves
    the note and defaulted the meters to 0 would silently reset a standing of
    4/2/1 to nothing. The route merges what is omitted with what is stored.
    """
    a: str = ""
    b: str = ""
    trust: int | None = None
    affection: int | None = None
    tension: int | None = None
    note: str | None = None
    bond: str | None = None
    scene: str = ""


class ChronicleLineSave(BaseModel):
    """A scene's one-line recap and its in-fiction date. The long `summary` and
    the absorb metadata are not editable here — re-absorbing is how a reading
    of the transcript changes."""
    one_line: str | None = None
    date: str | None = None


class ContinuityAliasCreate(BaseModel):
    """Merge `ref` into `to` (capstone spec §5.1). The flags answer the two
    refusals a reader may override: `replace` an existing merge of `ref`, and
    `accept_status_change` when the two records disagree about being open."""
    ref: str
    to: str
    replace: bool | None = None
    accept_status_change: bool | None = None
    note: str | None = None


class ContinuityLinkCreate(BaseModel):
    """Record ``a <relation> b`` (capstone spec §5.3)."""
    a: str
    b: str
    relation: str
    scene: str | None = None
    note: str | None = None


class ContinuityDismiss(BaseModel):
    """`POST .../continuity/candidates/{id}/dismiss` (spec §21's ``{decision}``).
    `expect_fingerprint` is additive: the current fingerprint a reader saw in a
    409 ``stale_candidate`` and chose to dismiss against (Decision 17)."""
    decision: str | None = None
    expect_fingerprint: str | None = None


