"""suggest: contracts responsibilities."""

from __future__ import annotations

from dataclasses import dataclass

#: The floor of the near-future window, in days: `near` asks for a date in
#: `[now, now + max(warn_days, NEAR_MIN_DAYS)]` (spec §15), so a campaign that
#: warns late, or not at all, still has a week to place a near scene in.
NEAR_MIN_DAYS = 7

#: The kinds whose timeline items `events.sooner` can pick: the Upcoming line
#: merges the calendar's holidays and the campaign's scheduled events only.
_SOONER_KINDS = frozenset({"event", "holiday"})

#: At most this many must-include refs (spec §16.3): every suggestion has to
#: serve each one, and a batch of three or four cannot serve many at once.
MUST_CAP = 3

TIME_MODES = ("auto", "near", "move", "anchor")
ANCHOR_RELATIONS = ("before", "on", "after", "by")

#: The kinds a must ref may name; temporal constraints go through the anchor.
MUST_KINDS = ("thread", "commitment")

#: The driver kinds a time anchor may name, in `DRIVER_KINDS` order.
TEMPORAL_KINDS = ("event", "birthday", "holiday")


@dataclass(frozen=True)
class Controls:
    """The reader's Story Pressure controls for one batch (spec §16.3).

    The ref tuples keep request order. `relation` is the request's, possibly
    "" (the model's or `on` then applies). Validation and precedence are
    `resolve_controls`'; this is only the value it produces.
    """
    focus: tuple[str, ...] = ()
    avoid: tuple[str, ...] = ()
    must: tuple[str, ...] = ()
    time_mode: str = "auto"
    anchor: str = ""
    relation: str = ""

    @property
    def active(self) -> bool:
        """Any structured control set: the controls addendum renders, and the
        picker shows every generated suggestion (§16.4)."""
        return bool(self.focus or self.avoid or self.must) or self.time_mode != "auto"

    @property
    def pinned(self) -> tuple[str, ...]:
        """The refs the prompt always shows: must, then focus, then the anchor."""
        out: list[str] = []
        for ref in (*self.must, *self.focus, *((self.anchor,) if self.anchor else ())):
            if ref not in out:
                out.append(ref)
        return tuple(out)


NO_CONTROLS = Controls()


class ControlsError(ValueError):
    """A request's controls, refused before any run is reserved (Decision 7).

    `status` 400 with `kind` "bad_controls" and a `reason` (`must_avoid`,
    `must_cap`, `must_kind`, `anchor_missing`, `anchor_without_mode`,
    `anchor_kind`, `anchor_relation`) for a request that can never be valid;
    409 "stale_drivers" with the `refs` the request-time capture lacks, for one
    that was valid against an older read of the campaign.
    """

    def __init__(self, status: int, kind: str, detail: str, *, reason: str = "",
                 refs: list[str] | None = None):
        super().__init__(detail)
        self.status = status
        self.kind = kind
        self.detail = detail
        self.reason = reason
        self.refs = refs
