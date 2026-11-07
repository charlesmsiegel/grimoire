"""Going on from a forked chat (028's turn merge): the library's owner chooses which branch continues.

Two paired devices that played one chat offline both wrote its next turn; once sync brings both into one
library the chat is forked, and every write is refused ``turn_fork``. Nothing is chosen automatically. The
owner names the first turn of one branch: every other branch is set aside from the fork's number on (kept,
heard by no one, outside the chat's numbering; ``grimoire.scene.branches``), and play goes on from the chosen
one. Choosing decides what every actor at the table heard, so only the library's owner may (fail closed: a
player of a present character may not).
"""

from __future__ import annotations

from grimoire.core.identity import ChatRef
from grimoire.scene import SceneRefused


def choose_branch(runner, chat: ChatRef, by, turn: str):  # type: ignore[no-untyped-def]
    """Set aside every branch of ``chat``'s fork but the one ``turn`` heads; the ``Scene`` afterwards."""
    if type(chat) is not ChatRef:
        raise SceneRefused("invalid_field", "chat must be a ChatRef")
    if not (runner.authority.issued(by) and getattr(by, "trusted", False) is True):  # before anything is read
        raise SceneRefused("not_authenticated", "a choice is made with a trusted handle this library issued")
    scene = runner.open(chat)  # an unknown chat is refused before anything is written
    principal = runner._principal(scene, by)  # the same authentication as every other act at the table
    if principal not in runner.authority.owners:
        raise SceneRefused("not_owner", "only the library's owner chooses which branch of a forked chat goes on")
    if not isinstance(turn, str):
        raise SceneRefused("not_a_branch", "a branch is named by its first turn's id")
    turns = scene.turns  # what the choice is checked against again under the store lock
    other = [code for _, code in turns.problems if code != "turn_fork"]
    if other:
        raise SceneRefused("turns_unreadable", f"this chat's turns are incomplete "
                                               f"({', '.join(sorted(set(other)))}); nothing is written")
    runner.scenes.choose(turns, turn, principal)
    return runner.open(chat)


__all__ = ["choose_branch"]
