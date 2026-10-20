"""Resolve stable subjects and capture labels before records may disappear."""

from __future__ import annotations

from fastapi import HTTPException

from ...store import scenes
from ...store.campaigns import read as campaigns_read
from ...store.campaigns.paths import CampaignNotFound
from . import _contracts
from ._router import router as router


def _scene_subject(cid: str, sid: str) -> tuple[_contracts.Subject, str | None]:
    """The subject for a scene, plus the scene's current identity.

    The subject IS the identity (see `Subject`): a scene's `sid` moves on
    rename and is handed to the next scene on delete, so it cannot name a run
    that outlives the request that started it. Both are returned because
    callers pass the identity to `_owns` as well, where it stays a belt-and-
    braces check on a run that was indexed before this was true.
    """
    try:
        identity = scenes.scene_identity_strict(cid, sid)
    except CampaignNotFound as exc:
        # `scene_identity` reaches `campaign_root`, which refuses an id the
        # store cannot address. Unhandled that is a 500, and every id-carrying
        # route in this app is swept for exactly that.
        raise _gone("campaign_gone", "no such campaign") from exc
    except OSError as exc:
        # Could not READ the header. Retryable, and it must not fall through as
        # "no identity" -- see `UNRESOLVED` below for what that would allow.
        raise HTTPException(status_code=409, detail={
            "kind": "busy", "detail": f"the scene could not be read: {exc}"}) from exc
    # STRICT, and never `None` past this point. `_owns` reads `identity=None` as
    # "caller did not ask", i.e. a wildcard -- correct for a subject-wide sweep,
    # catastrophic here: a replacement scene that recycled a retained run's
    # `sid` and has no identity of its own would match that run and be allowed
    # to read its frames or cancel it. A scene with no identity has no runs
    # either (`reserve_turn` mints one before it publishes anything), so a
    # sentinel that matches nothing is the honest answer rather than a refusal
    # -- discovery on such a scene still answers "no run" instead of an error.
    resolved = identity or _contracts.UNRESOLVED
    return ("scene", cid, resolved), resolved


def _gone(kind: str, detail: str) -> HTTPException:
    """A 404 whose body is FLAT.

    `main` installs an exception handler that emits a dict `detail` as the
    response body directly, so `kind` lands at the top level -- which is how
    every other structured error in this tree is asserted.
    """
    return HTTPException(status_code=404, detail={"kind": kind, "detail": detail})


# --- runs that belong to something other than a scene -----------------------
#
# The four operations above, mounted once per subject. A `draft` on a campaign,
# a world or the app itself is detached exactly as a turn is, and without these
# it would be detached and unreachable: the scene routes resolve their subject
# from `(cid, sid)`, so a scenario parse or a model refresh has no path to be
# discovered, polled, streamed or cancelled once its response is lost -- which
# is the whole failure this class of run is being migrated to fix.
#
# Deliberately NOT one set of routes over a `?subject=` parameter. The subject
# is what scopes every lookup, and a client that can name it in a query string
# can name somebody else's; keeping it in the path means each family can only
# ever address its own, the same isolation `_scene_subject` gives a scene.


def campaign_subject(cid: str) -> _contracts.Subject:
    """The subject a campaign-wide run belongs to.

    No identity component, unlike a scene's: a campaign id is stable for the
    life of the campaign (nothing renames one in place), so there is no second
    name needed to tell a recycled id from the original.
    """
    return ("campaign", cid)


def world_subject(wid: str) -> _contracts.Subject:
    """The subject a world-wide run belongs to. Stable for the same reason a
    campaign's is."""
    return ("world", wid)


GLOBAL_SUBJECT: _contracts.Subject = ("global",)
"""The subject for work that belongs to the app rather than to any record.

One member today -- refreshing a saved connection's model catalog -- and it is
genuinely global: the catalog is stored beside the connection, which no world
or campaign owns.
"""


def _subject_labels(subject: _contracts.Subject) -> dict:
    """The display text a run carries, derived from its subject.

    Derived rather than passed in, which is the one place this departs from
    `start_or_existing`'s "labels are required, never defaultable" rule. That
    rule exists because a notification with no campaign or scene text is a
    feature silently absent behind a green suite -- and a `draft` is the one
    class that never notifies at all. Thirteen call sites each remembering to
    build the same dict is how twelve of them end up subtly different; a
    subject already says everything there is to say.

    The campaign's title is read HERE, at reservation, for `_campaign_label`'s
    reason: it has to survive the campaign being deleted while the run is still
    discoverable.
    """
    if subject[0] == "campaign":
        return {"campaign": _campaign_label(str(subject[1])), "scene": ""}
    if subject[0] == "world":
        return {"campaign": "", "scene": "", "world": str(subject[1])}
    return {"campaign": "", "scene": ""}


def _campaign_label(cid: str) -> str:
    """The campaign's display name, captured at start.

    Read now rather than at terminal: the notification has to name the scene a
    turn belonged to even when that scene has since been deleted, which is
    exactly the case the error notification exists to report.
    """
    try:
        return campaigns_read.read_campaign(cid)["meta"].get("title", cid)
    except Exception:                                        # noqa: BLE001
        return cid


def _scene_label(cid: str, sid: str) -> str:
    try:
        return scenes.read_scene_meta(cid, sid).get("title", sid)
    except Exception:                                        # noqa: BLE001
        return sid
