"""Synthetic scene for the 032 tests: Ines (a telepathic PC), Emma, two guards and an off-stage spy.

No user content. The secret line contains ``SECRET-TOKEN`` so substring checks prove absence.
"""

from __future__ import annotations

from grimoire.comms import Presence
from grimoire.core import InstanceRef

C = "c1"
SECRET = "SECRET-TOKEN the guards are listening"


def actor(name: str, kind: str = "character", campaign: str = C) -> InstanceRef:
    return InstanceRef(campaign, kind, name)


INES, EMMA, GUARD_1, GUARD_2, SPY = (actor(n) for n in ("ines", "emma", "guard-1", "guard-2", "spy"))
ACTORS = (INES, EMMA, GUARD_1, GUARD_2, SPY)
BYSTANDERS = (GUARD_1, GUARD_2)
NAMES = {"Ines": INES, "Emma": EMMA, "the first guard": GUARD_1, "the second guard": GUARD_2, "Spy": SPY}


def hall(*, telepaths=(INES,), present=(INES, EMMA, GUARD_1, GUARD_2)) -> Presence:
    return Presence(C, "great-hall", present, telepaths=telepaths)
