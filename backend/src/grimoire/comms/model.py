"""Values of in-fiction transmission: the scene's present cast, a message and its delivery.

These are a first-slice contract, not a persisted schema. Nothing here is stored: a
``Transmission``'s records are 012 records, which 020 persists unchanged.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal, get_args

from ..core import InstanceRef, Proposition, Report, Subject
from ..core.identity import SLOT_MAX, validate_segment
from ..evidence import BATCH_LIMIT, LABEL_LIMIT, TEXT_LIMIT

Channel = Literal["speech", "whisper", "telepathy"]
CHANNELS: tuple[str, ...] = get_args(Channel)

#: A message id leaves room for the ``.r-<16 hex>`` suffix of its evidence ids, which 020 files
#: as ``<id>.json`` within the store's slot limit.
MESSAGE_ID_MAX = SLOT_MAX - 19
#: One 020 append holds the proposition and every report, so at most this many receivers.
MAX_RECEIVERS = BATCH_LIMIT - 1


class TransmissionError(ValueError):
    """A message that cannot be delivered as given. It carries a reason and never any text."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (self.reason,))


def _fail(reason: str) -> None:
    """Raise outside any handler, so neither cause nor context carries a rejected value."""
    raise TransmissionError(reason)


def _checked(check) -> None:
    """Run field checks; a ValueError or TypeError becomes a text-free ``invalid_record``."""
    try:
        check()
    except TransmissionError:
        raise
    except (ValueError, TypeError):
        pass
    else:
        return
    _fail("invalid_record")


def storable(value: object) -> bool:
    """Text 020 can encode: a str UTF-8 can hold (no lone surrogate)."""
    if type(value) is not str:
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _label(value: object) -> None:
    if not storable(value) or not value.strip() or len(value) > LABEL_LIMIT:
        raise ValueError("expected a nonempty storable label of at most 200 characters")


def _actors(value: object) -> tuple[InstanceRef, ...]:
    """A collection of distinct exact ``InstanceRef``s, as a tuple sorted by reference text."""
    if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Iterable):
        raise TypeError("expected a collection of InstanceRefs")
    refs = tuple(value)
    if any(type(ref) is not InstanceRef for ref in refs):
        raise TypeError("expected InstanceRefs")
    if len(set(refs)) != len(refs):
        raise ValueError("an actor is listed twice")
    return tuple(sorted(refs, key=str))


@dataclass(frozen=True, slots=True)
class Presence:
    """Who is present in one scene, as the director has accepted it.

    ``present`` is the scene's cast; it grants hearing ordinary speech and nothing more.
    ``telepaths`` are the present actors who may *send* telepathically. Being present never
    grants knowledge of anything said before; 033 will own the cast, and 025's abilities the
    telepaths. Both are sorted by reference text.
    """

    campaign_id: str
    scene_id: str
    present: tuple[InstanceRef, ...]
    telepaths: tuple[InstanceRef, ...] = ()

    def __post_init__(self) -> None:
        def check() -> None:
            validate_segment(self.campaign_id, "campaign_id")
            _label(self.scene_id)
            present = _actors(self.present)
            telepaths = _actors(self.telepaths)
            if not present:
                raise ValueError("a scene has at least one present actor")
            object.__setattr__(self, "present", present)
            object.__setattr__(self, "telepaths", telepaths)
        _checked(check)
        if any(ref.campaign_id != self.campaign_id for ref in self.present + self.telepaths):
            _fail("campaign_mismatch")
        if not set(self.telepaths) <= set(self.present):
            _fail("telepath_absent")


@dataclass(frozen=True, slots=True)
class Message:
    """One thing ``sender`` communicates in the fiction, by ``channel``.

    ``addressees`` empty means the whole room, which only ``speech`` can reach: the default
    audience of unmarked dialogue is everyone present. ``about`` lists only what ``text`` overtly
    refers to (012's rule); a reader of player text cannot know it and leaves it empty.
    """

    message_id: str
    sender: InstanceRef
    channel: str
    text: str
    addressees: tuple[InstanceRef, ...] = ()
    about: tuple[Subject, ...] = ()

    def __post_init__(self) -> None:
        def check() -> None:
            validate_segment(self.message_id, "message_id")
            if len(self.message_id) > MESSAGE_ID_MAX:
                raise ValueError("message_id is too long")
            if type(self.sender) is not InstanceRef:
                raise TypeError("sender must be an InstanceRef")
            if not storable(self.text) or not self.text.strip():
                raise ValueError("text must be nonempty storable text")
            object.__setattr__(self, "addressees", _actors(self.addressees))
            if isinstance(self.about, (str, bytes)) or not isinstance(self.about, Iterable):
                raise TypeError("about must be a collection of Subjects")
            about = tuple(self.about)
            if any(type(subject) is not Subject for subject in about):
                raise TypeError("about must hold Subjects")
            object.__setattr__(self, "about", about)
        _checked(check)
        if self.channel not in CHANNELS:
            _fail("unknown_channel")
        if len(self.text) > TEXT_LIMIT:
            _fail("too_long")


@dataclass(frozen=True, slots=True)
class Transmission:
    """What one message became: a new proposition and one report per receiver.

    Director-side: it names every receiver. Hand only ``records()`` to 020; actors learn of it
    only through 012's projections.
    """

    message: Message
    proposition: Proposition
    reports: tuple[Report, ...]

    @property
    def receivers(self) -> tuple[InstanceRef, ...]:
        return tuple(report.holder for report in self.reports)

    def records(self) -> tuple[Proposition | Report, ...]:
        return (self.proposition, *self.reports)
