"""todo: context responsibilities."""

from __future__ import annotations

from ... import store
from ...store import overview
from . import _sources
from ._router import router as router


class _Ctx:
    """One request's inputs, and the derivations more than one chore wants.

    It exists for the second half. `anchors` and `taglines` are the same walk
    asked two different questions, and as two independent builders they ran it
    twice -- the single most expensive thing this module did, paid on every
    `/api/shell` read, which is every navigation. A builder that needs a
    derivation asks the ctx for it and the second asker gets the first one's
    answer.

    Deliberately per-request and thrown away after: nothing on the ctx
    outlives the read it was computed for. "Every chore is a live count" is
    the contract. What does outlive a request is below it, in the overview
    projections, and only under a key covering every file each one reads.
    """

    def __init__(self, cid: str) -> None:
        self.cid = cid
        self._memo: dict[str, object] = {}

    def _once(self, key: str, compute):
        if key not in self._memo:
            self._memo[key] = compute()
        return self._memo[key]

    def _summary_or_error(self) -> dict | Exception:
        """The overview's scene summary for the campaign (spec 04 section
        3.4), or the `CampaignNotFound` / `OSError` it raised, remembered
        either way so the second asker meets the same answer."""
        def read() -> dict | Exception:
            if not self.cid:
                return store.CampaignNotFound(self.cid)
            try:
                return overview.scene_summary(self.cid)
            except (store.CampaignNotFound, OSError) as e:
                return e
        return self._once("scene_summary", read)

    def scene_summary(self) -> dict | None:
        """The summary, or `None` for an unknown campaign or a `scenes/`
        that cannot be listed -- the two `list_scenes` raised, mapped here
        exactly as `_scenes_or_none` mapped them before it."""
        found = self._summary_or_error()
        return None if isinstance(found, Exception) else found

    def scene_summary_or_raise(self) -> dict:
        """The summary, raising what it raised -- for the shell's block,
        which keeps its own mapping of each failure."""
        found = self._summary_or_error()
        if isinstance(found, Exception):
            raise found
        return found

    def has_campaign(self) -> bool:
        """Whether there is a readable campaign to ask campaign chores about.

        The gate `_chores` used to take from `list_scenes` raising, kept
        deliberately: an unknown or unreadable id must answer no campaign
        chores at all, rather than reach seven builders each with its own idea
        of what a bad cid means -- and several of them raise on the way to
        saying nothing, which is a 500 where the shell promises a null.

        Distinct from "has no scenes", a summary whose count is zero. One
        undecodable scene is not a reason to drop the campaign: the summary
        degrades that member (spec 04 section 4.4).
        """
        return self.scene_summary() is not None

    def character_gaps(self) -> tuple[list[dict], list[dict]]:
        return self._once("gaps", lambda: _sources._character_gaps(self.cid))

    def continuity(self) -> dict:
        """The overview's continuity summary for the campaign
        (`store.overview.continuity_summary`), read once for the shell's
        ledger badge and the `owed` and continuity chores.

        Its findings go through `pending.findings`, the GET's own live filter
        (§18.2), so a count cannot disagree with what the Ledger lists. A
        cache or ledger that cannot be read is unknown -- `None` for a count,
        no findings for the chores (spec 04 section 3.5)."""
        def read() -> dict:
            try:
                return overview.continuity_summary(self.cid)
            except store.CampaignNotFound:
                return {"ledger_open": None, "owed": None, "overlaps": 0, "closures": 0,
                        "closures_threads": False}
        return self._once("continuity", read)

    def avatar_gaps(self) -> list[dict]:
        return self._once("avatars", lambda: _sources._items_avatars(self.cid))

    def world_avatar_gaps(self) -> list[dict]:
        return self._once("world_avatars", lambda: _sources._world_avatar_gaps(self.other_worlds()))

    def world_cover_gaps(self) -> list[dict]:
        # Covers belong to the world itself, including a campaign's world.
        # There is no campaign-side substitute to de-duplicate here.
        return self._once("world_covers", lambda: _sources._world_cover_gaps(self.worlds()))

    def world_subject_gaps(self) -> list[dict]:
        return self._once("world_subjects", lambda: _sources._world_subject_gaps(self.worlds()))

    def coverage(self) -> dict:
        """`sheets.coverage` for the campaign, computed once per request.

        `/api/shell` asks it twice -- the rail's "sheeted of total" and the
        sheet chore behind the badge -- and each ask sweeps the cast and reads
        one sheet per member. `get_shell` hands one ctx to both, so the second
        asker gets the first one's answer.

        A tally that RAISES is not memoized (`_once` stores only a return), so
        each asker meets the failure itself and keeps the handling it always
        had: the chore swallows it and the rail's campaign block does not.
        """
        return self._once("coverage", lambda: store.sheets.coverage(self.cid))

    def world_char_gaps(self) -> tuple[list[dict], list[dict]]:
        """(untagged, anchorless) over `other_worlds`, computed once.

        The same sharing `character_gaps` does, for the same reason and against
        the same mistake: `world-taglines` and `world-anchors` are one walk
        asked two questions, and as two independent builders they ran it twice.
        """
        return self._once("world_gaps",
                          lambda: _sources._world_char_gaps(self.other_worlds()))

    def worlds(self) -> list[dict]:
        """Every world, newest first, as `{id, name, created, updated}`.

        `list_world_rows`, not `list_worlds`: every chore here reads a world's
        id and name and nothing else, and the counts `list_worlds` adds are a
        directory listing per record kind per world, on every navigation."""
        def read() -> list[dict]:
            try:
                return store.worlds.read.list_world_rows()
            except OSError:
                return []
        return self._once("worlds", read)

    def other_worlds(self) -> list[dict]:
        """The worlds the open campaign does NOT draw on.

        The exclusion behind `world-taglines` and `world-anchors`: those two
        facts are already reported for the campaign's own world, over its
        effective copy-on-write roster, which is the more accurate of the two
        answers (see `_character_gaps`). Reporting them again from the world
        side would double-count the same character and disagree with itself
        about the campaign-side taglines it cannot see.

        `world-describe` deliberately does NOT use this. Nothing else reports
        an image backlog, so excluding the campaign's world there would hide it
        rather than de-duplicate it.
        """
        def read() -> list[dict]:
            if not self.cid:
                return self.worlds()
            try:
                used = {w for c, _n, w in store.campaigns.read.world_refs()
                        if c == self.cid and w}
            except OSError:
                # "We could not tell" must not become "it uses nothing", which
                # would double-report every world. `world_refs`' own rule.
                return []
            return [w for w in self.worlds() if w["id"] not in used]
        return self._once("other_worlds", read)
