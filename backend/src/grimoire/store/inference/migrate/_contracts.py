"""Public migration status shape and marker/stop reasons, with fresh retirement defaults."""

from __future__ import annotations

import dataclasses

from .. import legacy_plan

#: Every state `status` answers.
STATES: tuple[str, ...] = ("done", "pending", "running", "failed", "newer")

#: The global keys the migration writes, every one on every switch
#: (`legacy_plan.OWNED_GLOBAL_KEYS`, where the mapping lives).
OWNED_GLOBAL_KEYS: tuple[str, ...] = legacy_plan.OWNED_GLOBAL_KEYS

#: The reason a run that found its root moved under it reports.
MOVED = "the storage location changed during the upgrade; it resumes on the next start there"
#: The reason a run stopped by its caller (app shutdown) reports.
STOPPED = "the app shut down during the upgrade; it resumes on the next start"


def _no_retirement() -> dict:
    """`Status.retirement` when nothing about retirement was asked: a fresh
    dict per Status, so no caller can edit another's."""
    return {"left": [], "failed": ""}


@dataclasses.dataclass(frozen=True)
class Status:
    state: str
    reason: str = ""
    skipped: tuple[str, ...] = ()
    #: Retirement's own account (slice I): `left`, what it has still to do
    #: (`retire.left()`), and `failed`, why the last pass on this root stopped
    #: short ("" when it did not). Never moves `state`: a store whose
    #: migration is done reads `done` whatever retirement has left.
    retirement: dict = dataclasses.field(default_factory=_no_retirement)

    active: bool = False
    progress: dict | None = None

    def as_dict(self) -> dict:
        """The JSON shape (`not_migrated`'s `status`, Settings)."""
        body = {"state": self.state, "reason": self.reason, "skipped": list(self.skipped),
                "retirement": {"left": list(self.retirement.get("left") or ()),
                               "failed": str(self.retirement.get("failed") or "")},
                "active": self.active}
        if self.active and self.progress is not None:
            body["progress"] = dict(self.progress)
        return body
