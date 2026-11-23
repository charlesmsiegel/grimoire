"""Sprint 116's adapter op: the review of a voice casting change before 083's ``voicecast.cast`` or ``uncast``
makes it. Called as ``voices.review``; see docs/LISTEN.md.

083's casting writes are guarded by the etag they read, but they have no review of their own: a page that asked
``voicecast.cast`` straight away would learn only afterwards that the voice was refused, or that it replaced
another. ``voices.review`` reads the same casting file and answers what the write would do, and writes nothing.
It refuses as the write would, in the write's order: the target and the voice first (``invalid_*``,
``rights_unrecorded``), then whether the library holds the target (``no_character``, ``no_actor``,
``no_campaign``), then the casting file (``link`` for a link, ``unreadable_record`` for a file that cannot be
decoded), then ``not_cast`` for an uncast where nothing is cast. One difference is kept on purpose: a file the
operating system will not let it read is ``casting_unreadable`` here, where the write says ``casting_unwritable``,
because the review only reads. The page then makes the change with ``voicecast.cast`` or ``voicecast.uncast``
naming the ``etag`` the review read: a casting that changed in between refuses it ``changed``, so a change is only
ever made as it was reviewed.

The review holds no record of its own: it is computed from the casting file each time, through 083's own
``CastingStore`` and ``Casting`` (``with_entry``, ``without``), never a second store.
"""

from __future__ import annotations

from collections.abc import Mapping

from grimoire.app.adapter import Adapter, AdapterContext
from grimoire.app.adapters.voicecast import _engines, _refuse, _require, _store, _target, _text
from grimoire.textapi import ApiRefused
from grimoire.voicecast import CastEntry, CastingError, SpeechVoice
from grimoire.voicecaststore import PATH

KINDS = ("cast", "uncast")


def _voice(entry: CastEntry | None) -> dict | None:
    if entry is None:
        return None
    return {"engine": entry.voice.engine, "voice": entry.voice.voice, "key": entry.voice.key,
            "rights": entry.voice.rights, "note": entry.voice.note}


def _readable(view) -> None:  # type: ignore[no-untyped-def]
    """Refuse a casting file the write would refuse to read, with the write's own reason."""
    problem = view.problem
    if problem is None:
        return
    if problem.reason == "link":  # the store's own CastingError("link"), as the write raises it
        raise _refuse(CastingError("link", problem.detail))
    if problem.reason == "unreadable":  # an OSError: the write would fail too, as casting_unwritable
        raise ApiRefused("casting_unreadable", f"the casting cannot be read ({problem.detail})")
    raise ApiRefused("unreadable_record", f"{problem.path} cannot be read ({problem.reason}: {problem.detail}); "
                     "mend or remove that file first", 409)


def _review(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    kind = args.get("kind")
    if kind not in KINDS:
        raise ApiRefused("invalid_field", f"kind is one of {KINDS}")
    target, ref, campaign = _target(args)
    entry: CastEntry | None = None
    if kind == "cast":  # in voicecast.cast's order: the voice, the target held, then the file
        voice = SpeechVoice(args.get("engine"), args.get("voice"), args.get("rights", "engine"),  # type: ignore[arg-type]
                            _text(args, "note"))
        entry = CastEntry(target, voice, ref, campaign)
        _require(ctx, target, ref, campaign)
    view = _store(ctx).view()
    _readable(view)  # a casting it cannot read is never written over
    held = view.casting.get(target, ref, campaign)
    if entry is not None:
        after = view.casting.with_entry(entry)
        change = "same" if held == entry else "replace" if held is not None else "new"
        engines = {item["engine"]: item["voices"] for item in _engines(ctx)}
        available: bool | None = entry.voice.voice in engines.get(entry.voice.engine, [])
        describes = entry.describe()
    else:
        after = view.casting.without(target, ref, campaign)  # not_cast when nothing is cast there
        if held is None:  # never reached: `without` refuses not_cast first
            raise ApiRefused("not_cast", "nothing is cast there", 404)
        change, available, describes = "remove", None, held.describe()
    writes = [] if change == "same" else [PATH]
    return {"kind": kind, "target": target, "ref": None if ref is None else str(ref), "campaign": campaign,
            "describes": describes, "before": _voice(held), "after": _voice(entry), "change": change,
            "available": available, "etag": view.etag, "writes": writes,
            "removes_file": change != "same" and not after.entries, "entries_after": len(after.entries),
            "written": False}


def _handler(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    if not ctx.store.writable:
        raise ApiRefused("read_only", "this library is open read-only")
    try:
        return _review(ctx, args)
    except CastingError as error:
        raise _refuse(error) from None
    except OSError as error:
        raise ApiRefused("casting_unreadable", f"the casting cannot be read ({error.strerror or type(error).__name__})"
                         ) from None


_handler.__name__ = "voices_review"

ADAPTER = Adapter("voices", owner="116", ops={"review": _handler})

__all__ = ["ADAPTER", "KINDS"]
