"""common: lookups responsibilities."""

from __future__ import annotations

from fastapi import HTTPException

from ... import store


def _display_name_or_400(name: str) -> str:
    """A trimmed, single-line display name, or a 400.

    `dump_frontmatter` writes one `key: value` line per field and the parser
    reads them back a line at a time, so a value carrying a newline stores a
    mangled name AND leaves a stray line in the record that a later parse
    could read as a field. Tabs and other control characters round-trip no
    better. Printability is the test rather than ASCII: accents, CJK and
    symbols are ordinary names, and a library is not English.
    """
    cleaned = name.strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="name is required")
    if not cleaned.isprintable():
        raise HTTPException(status_code=400, detail="name must be a single line")
    return cleaned


# ---- opt-in paging on the list routes that grow with play (#216) ----
def _page_window(limit: int | None, offset: int | None) -> tuple[int | None, int]:
    """An opt-in `limit`/`offset` pair, range-checked and normalized, or a 400.

    Both are optional and both default to "the whole listing", so a route that
    adopts this returns exactly what it returned before to every caller that
    sends neither -- which is what lets these land without touching `client.ts`.

    What this shares between the routes is the RANGE CHECK and its wording, not
    the meaning of `offset`: it sees two integers and nothing about the listing
    they will be applied to. Two of the three routes read `offset` as "skip that
    many from the front of what I print"; `GET /campaigns/{cid}/chronicle`,
    whose window was already anchored at the newest end, reads it as "skip that
    many of the newest". A helper cannot close that gap -- only the route
    docstrings can, and each says which it is.

    Hand-checked rather than `Query(ge=1)`, which would be shorter and would put
    the bound in the OpenAPI schema: FastAPI answers a violated `ge` with a 422
    and its own error body, and `GET /campaigns/{cid}/scenes/{sid}` -- the
    windowed route these follow -- already answers 400 with this wording. Either
    way one pair of inputs answers inconsistently, so the choice is which pair:
    `ge` would have split `limit=0` from the windowed route it was copied from,
    where this splits `limit=0` from `limit=abc`. A client sends the first by
    arithmetic and the second only by writing a bug.

    Checked BEFORE the route looks for its campaign, matching that same route.
    FastAPI validates the query TYPES ahead of the handler, so `?limit=abc` is a
    422 whatever the campaign is; a hand-written range check that deferred to
    the 404 would make `limit=abc` and `limit=0` answer differently for the same
    request.
    """
    if limit is not None and limit < 1:
        raise HTTPException(status_code=400, detail="limit must be at least 1")
    if offset is not None and offset < 0:
        raise HTTPException(status_code=400, detail="offset must not be negative")
    return limit, offset or 0


def _page_of(rows: list, limit: int | None, offset: int) -> list:
    """`rows` narrowed to the window `_page_window` returned. `limit` None: no cap.

    Takes the built list, because every one of these listings is sorted before
    it is paged and a sort has to see every row. What a page bounds is the
    response -- and, where per-row work outlives the sort (`GET /changes`
    renders a diff per field), whatever the route does after this call.
    """
    return rows[offset:] if limit is None else rows[offset:offset + limit]


def _world_root_or_404(wid: str):
    if not store.worlds.world_exists(wid):
        raise HTTPException(status_code=404, detail="world not found")
    return store.worlds.world_root(wid)


#: What a PC revision-history route can miss (#67), in the order the route
#: resolves them: the PC, then its version, then the revision.
PC_HISTORY_MISSES = (store.pcs.PCNotFound, store.pcs.PCVersionNotFound,
                     store.pcs.PCRevisionNotFoundError)


def pc_history_404(exc: Exception) -> HTTPException:
    """The 404 for one of `PC_HISTORY_MISSES`, naming which thing was missing."""
    detail = {store.pcs.PCNotFound: "pc not found",
              store.pcs.PCVersionNotFound: "version not found",
              store.pcs.PCRevisionNotFoundError: "revision not found"}[type(exc)]
    return HTTPException(status_code=404, detail=detail)


def _world_char_version_or_404(wid: str, cid: str, vid: str):
    """The world root, once `cid`/`vid` are known to name a real character version.

    Every *write* on the character image surface goes through this (#360).
    `assets.put_image` creates the directory it writes into, so an unchecked id
    turned a typo into `characters/<typo>/assets/<vid>/avatar.png`: bytes no
    listing shows (`list_characters` needs `character.md`, `read_character`
    only reports images for versions it can resolve) and no delete route can
    name, reported to the caller as a successful upload.

    The reads are deliberately left ungated, which is the one place this
    departs from `worlds._world_pc_version_or_404`: they create nothing, they
    already answer "no image" for an id that names nothing, and
    `GET .../images/avatar` is hit once per portrait per rendered grid.

    Honest about its reach: this refuses an id that names nothing *now*. A
    version deleted between the check and the write still strands the upload,
    because no lock spans the two -- it is a guard against a typo, not against
    a race.
    """
    root = _world_root_or_404(wid)
    try:
        store.characters.require_version(root, cid, vid)   # two stats, no read
    except store.characters.CharacterNotFound as exc:
        raise HTTPException(status_code=404, detail="character not found") from exc
    except store.characters.VersionNotFound as exc:
        raise HTTPException(status_code=404, detail="version not found") from exc
    return root


def _card_data(card: dict) -> dict:
    """`store.cards.card_data`, under the name the prompt-building routes use.

    Reading `card["data"]` blind is a KeyError on a card that has none -- `{}`
    is what version PUT stores for one -- and a non-object `data` raises one
    attribute access into the prompt template. Either way a 500 before the model
    is ever called, where the templates already render "(none)" for a missing
    field, which is a far better answer for a draft the user edits anyway.
    """
    return store.cards.card_data(card)


def _campaign_root_or_404(cid: str):
    try:
        store.campaigns.ensure_campaign_slim(cid)  # lazy slim of pre-overlay campaigns
    except store.campaigns.CampaignNotFound as exc:
        raise HTTPException(status_code=404, detail="campaign not found") from exc
    return store.campaigns.campaign_root(cid)


def _content_fields(kind: str, content: dict) -> dict:
    """The declared fields a module content entry carries, checked.

    The check is the point, and it was missing: both instantiate routes handed
    these straight to `create_entity`, while the ordinary create/update routes
    ran `invalid_values` on the identical dict. A pack naming a bare, malformed
    or wrong-kind `holder` therefore instantiated a record that `EntityEditor`
    could never save again -- every later save resends the field and the save
    boundary rejects it. That is the same trap a reclassify used to set, one
    door over.

    A 400 rather than a silent drop: the value came from a file somebody wrote,
    and naming the field is what lets them fix it. Module *pack* validation
    (`store/modules/validate.py`) does not look at these fields at all, so this
    is the only boundary that can say anything, and it checks everything
    `invalid_values` covers -- the location weather fields had the identical
    hole beside the new ref ones.
    """
    fields = {k: content[k] for k in store.entity_schema.field_keys(kind) if k in content}
    bad = store.entity_schema.invalid_values(kind, fields)
    if bad:
        raise HTTPException(
            status_code=400,
            detail=f"module content has invalid values for {kind}: {', '.join(bad)}")
    return fields


def _sheet_failure_status(exc: Exception) -> int:
    """The status a rejected sheet write answers with: 409 for a CAS conflict,
    400 for everything else the writer refuses.

    Every sheet route in `mechanics.py` and `routes/worlds/_mechanics.py` splits the two this
    way. The instantiate routes catch the base class in one clause (their
    rollback runs for both), and used to answer 400 for the subclass too --
    an id freshly minted by `create_entity` cannot carry a sheet yet, so the
    conflict is unreachable there today, but the mapping is one rule, not
    one per route.
    """
    return 409 if isinstance(exc, store.sheets.SheetConflict) else 400


def _require_scene(cid: str, sid: str) -> dict:
    try:
        return store.scenes.read_scene(cid, sid)
    except (store.scenes.SceneNotFound, store.campaigns.CampaignNotFound) as exc:
        # a scene path is built from campaign_root, so an unusable campaign id
        # surfaces here as CampaignNotFound -- still a 404, not a 500
        raise HTTPException(status_code=404, detail="scene not found") from exc
