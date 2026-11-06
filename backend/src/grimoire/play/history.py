"""A chat's imported history as the runner reads it (035 M2): its 036/026 transcript pages, before turn 1.

The pages say who wrote each post, never who heard it. The rule for who heard it is ``grimoire.scene``'s
(``imported_history``); this module finds the cast it applies to and refuses to apply it to a history that
cannot all be read. See docs/SCENES.md, "Who heard what".
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from grimoire.archaeologystore import read_transcript
from grimoire.core.identity import ChatRef, InstanceRef
from grimoire.scene import ImportedHistory, imported_history
from grimoire.store import LibraryStore


def chat_history(store: LibraryStore, chat: ChatRef, record, snapshot
                 ) -> tuple[ImportedHistory | None, list[tuple[str, str]]]:
    """The chat's imported history (``None`` when it has none, or when it cannot all be read) and problems.

    A damaged page, a gap in the numbering, or a page 1 that says its import wrote more pages than there
    are, is ``imported_history_unreadable``, and then nobody heard any of it: a listener would otherwise hear
    part of the history, unmarked. A problem with the campaign's transcripts folder rather than this chat's
    pages (a link, a look-alike folder) makes this chat's own pages unreadable too, but a chat with no pages
    is only told about it (``transcripts``): it has no history to lose.
    """
    pages, found = read_transcript(store, chat)
    own = [problem for problem in found if _about_chat(problem, chat)]
    others = [problem for problem in found if not _about_chat(problem, chat)]
    if pages and _missing_pages(pages):
        own.append(f"{pages[0].ref}:missing_pages")
    unreadable = own + (others if pages else [])
    if unreadable:
        return None, [("imported_history_unreadable", _problem_text(problem)) for problem in unreadable]
    problems = [("transcripts", _problem_text(problem)) for problem in others]
    if not pages:
        return None, problems
    names = _imported_names(snapshot, record)
    return imported_history(pages, imported_cast(record), names, player=_imported_player(record, names)), problems


def imported_cast(record) -> tuple[InstanceRef, ...]:
    """The actors a chat was imported with: on its roster from its start (sequence 0).

    An importer starts a chat with its whole cast and never rebinds it; an actor attached later was not
    there. 026 may write the pages of a chat it imported before 036 existed on a later run, after the chat
    was played, but that chat's sequence-0 roster is still the V0.2.1 scene's cast."""
    return tuple(entry.actor for entry in record.roster if entry.introduced_seq == 0)


def _imported_names(snapshot, record) -> dict[InstanceRef, str]:
    """Names for the imported cast, present or not; an actor 022 cannot name keeps its post's own label."""
    names = {}
    for actor in imported_cast(record):
        try:
            names[actor] = snapshot.entity(actor).name
        except Exception:  # 022's refusals: the post's label stands
            continue
    return names


def _imported_player(record, names: Mapping[InstanceRef, str]) -> str | None:
    """The name of the persona the chat was imported with: who wrote a player post that names no one."""
    entry = next((entry for entry in record.roster if entry.introduced_seq == 0 and entry.role == "persona"), None)
    return None if entry is None else names.get(entry.actor)


def _about_chat(problem: object, chat: ChatRef) -> bool:
    """Whether a transcript problem can hide this chat's pages: one about its own pages or folder, or about
    the campaign's ``transcripts`` folder itself (a link, a file, an unlistable folder), behind which nobody
    can tell whether the chat has pages. A look-alike sibling (``Transcripts``) hides none of them."""
    text = _problem_text(problem)
    if any(text.startswith(f"campaigns/{chat.campaign_id}{folder}:") for folder in ("", "/transcripts")):
        return True
    return any(f"{prefix}{chat.chat_id}{end}" in text
               for prefix in ("transcripts/", "#transcript/") for end in ("/", ":"))


def _missing_pages(pages: Sequence) -> bool:
    """Whether a page 1 that says how many pages its import wrote (036's SillyTavern import) lost some.

    026's V0.2.1 pages record no count, so a lost last page of theirs goes unnoticed (docs/SCENES.md)."""
    count = pages[0].origin.get("pages")
    return type(count) is int and len(pages) < count


def _problem_text(problem: object) -> str:
    """One readable detail for a transcript problem: a store problem's path and code, or a page's reason."""
    path, code = getattr(problem, "path", None), getattr(problem, "code", None)
    if path is not None and code is not None:
        return f"{path}:{code}"
    if isinstance(problem, tuple) and len(problem) == 2:
        return f"{problem[0]}:{problem[1]}"
    return str(problem)[:200]
