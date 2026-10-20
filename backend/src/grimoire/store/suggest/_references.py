"""suggest: references responsibilities."""

from __future__ import annotations

from .. import overlay
from ..appearances import cast as appearances_cast
from . import _dates


def valid_ids(cid: str):
    """The ids a proposed scene may reference: characters, player tokens,
    locations.

    Public because a *saved* scene idea (#88) has to be held to the same rule
    as a freshly generated one -- checked on write and again on every read,
    since a campaign moves under a durable idea -- and doing that through this
    module is what keeps one definition of "a token this campaign actually
    has". Reads the campaign's entities and roster, so callers resolve it once
    for a whole list rather than per row (see `ref_validator`).
    """
    # Ids off directory listings: validity is membership, so neither the full
    # character rows nor every location's parsed body is needed to decide it.
    v = overlay.view(cid)
    char_ids = set(overlay.character_ids(cid, v=v))
    player_tokens = {f"{a['kind']}:{a['id']}" for a in appearances_cast.roster(cid) if a["role"] == "player"}
    loc_ids = set(overlay.cast_ids(cid, "locations", v=v))
    return char_ids, player_tokens, loc_ids


def ref_validator(cid: str):
    """A reference checker bound to one campaign:
    ``(cast, location, date, offscreen) -> {"cast", "location", "date"}``, with
    unknown cast tokens and an unknown location dropped and the date canonical
    or blank.

    Returned as a closure because the id sets it checks against cost a read of
    the campaign's entities and roster (`valid_ids`) and a calendar-provider
    resolution (`date_normalizer`): a caller with a list of records resolves
    them once and reuses the checker, rather than paying per row.
    """
    char_ids, player_tokens, loc_ids = valid_ids(cid)
    norm = _dates.date_normalizer(cid)

    def check(cast, location, date="", offscreen=False) -> dict:
        loc = str(location).strip()
        return {"cast": [t for t in (str(x).strip() for x in cast)
                         if token_ok(t, char_ids, player_tokens, offscreen)],
                "location": loc if loc in loc_ids else "",
                "date": norm(str(date).strip())}
    return check


def valid_refs(cid: str, cast: list[str], location: str, date: str = "",
               offscreen: bool = False) -> dict:
    """One record's references, checked. The single-record form of what
    `parse_output` does across a whole reply, for the caller that has a record
    rather than a model's text -- `routes.scenes.post_scene_idea`, saving a
    scene idea (#88)."""
    return ref_validator(cid)(cast, location, date, offscreen)


def token_ok(tok: str, char_ids: set[str], player_tokens: set[str], offscreen: bool) -> bool:
    """A cast token this campaign actually has. Public for `valid_ids`' reason.

    The offscreen clause is FIRST and guarded, both deliberately. A PC seated as
    a `characters` actor (CastPanel's role selector allows exactly that) would
    otherwise pass on the `char_ids` check below, and an offscreen scene is
    defined by the player's absence. Guarded, because dropping the `offscreen`
    condition would reject players from ordinary PC scenes."""
    kind, _, aid = tok.partition(":")
    if offscreen and tok in player_tokens:
        return False
    if kind == "characters" and aid in char_ids:
        return True
    return not offscreen and tok in player_tokens
