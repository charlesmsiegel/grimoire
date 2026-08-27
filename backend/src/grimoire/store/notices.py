"""Warn-once pre-notices for imminent scheduled events and observances (#106).

Stored at ``<campaign>/notices.json``::

    {key: {"noticed_at": "<iso stamp>", "scene": "<sid>"}}

The ledger answers one question — *has this reader already been warned about
this?* — and nothing else. What is coming is recomputed every time from the
calendar and from ``events.json``; only the acknowledgement is state, because
only the acknowledgement is something nobody can derive.

**The key names an OCCURRENCE, not an event.** ``holiday:{fixed}:{name}`` and
``event:{fixed}:{id}``, where ``fixed`` is the Rata Die day the thing lands on.
That is what makes next year's Midwinter warn again after this year's was
dismissed: same name, different day, different key. It is also why the day is
the fixed-day integer rather than a native date string — a campaign re-pointed
at another calendar renders the same day differently, and an acknowledgement
must not come undone because the notation changed.

The two kinds share one keyspace deliberately. A reader being warned "the
coronation is in three days" and "Midwinter is in three days" is having the same
experience, and #103's approaching-due-date warnings are meant to land here too.
The prefix is what keeps a holiday called ``eclipse`` from silencing a
scheduled event with that id.

**Nothing here is written on prompt injection.** The "Upcoming:" line the model
receives every turn (``context.world_state._today_data``) is a different channel
from the banner the reader sees, and marking a notice because a turn ran would
consume the warning without anybody having been warned. Marking is the reader's
act — the route behind the banner's dismiss — and until it happens the notice
keeps showing. Which means "warn once" here is once *per acknowledgement*, not
once per render: a warning nobody dismissed has not been delivered, and the
failure mode of showing it again is a second look at a banner, while the failure
mode of the other rule is a holiday the reader never heard about.

Mutators serialize on ``locks.campaign_lock(cid)``: notices.json is rewritten
whole, so two unlocked read-modify-writes lose one of them. Resolving the
campaign's calendar — which runs user-authored plugin code — happens in `pending`,
outside every lock in this module, the same cut `clock` and `events` make.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import atomic, calendars, events, locks, paths
from .campaigns import paths as campaigns_paths

#: How many acknowledgements are kept. The ledger only ever grows — one row per
#: occurrence a reader has dismissed — and `POST .../notices` is a public
#: endpoint, so something has to bound the file.
#:
#: This is a BACKSTOP against a writer that will not stop, not a
#: safe-by-construction property, and the difference matters. Evicting the
#: oldest acknowledgement would be free if `pending` only ever looked forward
#: from the campaign clock, which advances: every evicted day would then be
#: behind every later question. It does not. The scene surface asks from the
#: SCENE's own moment — a flashback is a supported, tested case, and
#: `clock.advance` takes backward corrections too — so a historical scene dated
#: just before an evicted occurrence warns about it again.
#:
#: The cap is therefore set where a real reader cannot reach it: one row per
#: occurrence they personally dismissed, so five thousand is a lifetime of
#: campaigns, while the file stays well under a megabyte for a store read only
#: when a notice surface asks. Reaching it means something is manufacturing
#: dismissals, and re-warning about the oldest of those is the right failure for
#: that case — the alternative is a file with no ceiling at all.
LEDGER_LIMIT = 5000

#: A dismissed key is stored verbatim (it is opaque, and holiday names are not
#: slugs), so this is what stops a crafted POST from writing an unbounded file.
#: Keys over it are REFUSED rather than truncated: a truncated key is a
#: different key, so storing one would report a dismissal that `pending` -- which
#: compares against the full generated key -- then ignores, and the banner the
#: reader just closed would come straight back. `_bounded` below is what keeps
#: every generated key under this, so refusing can only ever reject a crafted
#: one.
KEY_LIMIT = 200

#: How much of an observance's name a key spells out before `_bounded` hashes
#: the rest. A holiday name has no length limit anywhere -- `validate_rule`
#: checks only that one is present, and a custom rule is hand-written -- so
#: without this a legitimate key could exceed `KEY_LIMIT` on its own.
NAME_BUDGET = 120

#: The dismissing scene, recorded for the reader alone. Bounded because it
#: arrives in a public request body and is written into every new row: capping
#: the KEY and the row COUNT bounds nothing if one field beside them is free to
#: be a megabyte. Truncation is right here where it is wrong for a key --
#: nothing compares this value, so a shortened one still says where it happened.
SCENE_LIMIT = 200


def _path(cid: str) -> Path:
    return campaigns_paths.campaign_root(cid) / "notices.json"


def read(cid: str) -> dict:
    """The acknowledgement ledger, or an empty one.

    Never raises over the file, matching `events.read` and `plot.read`: the only
    reader is a banner, and a hand-edited notices.json that no longer parses
    must cost the campaign its dismissals — the banner comes back — rather than
    its scene panel.
    """
    p = _path(cid)
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _write(cid: str, data: dict) -> None:
    atomic.write_text(_path(cid), json.dumps(data, indent=2, sort_keys=True) + "\n")


def _stamp(value) -> str:
    """One row's `noticed_at` as text, or empty. Used only to order eviction."""
    return value if isinstance(value, str) else ""


def _trim(data: dict) -> dict:
    """The newest `LEDGER_LIMIT` acknowledgements. Oldest `noticed_at` goes first.

    Oldest-first because it is the least-bad order, not a correct one: no
    eviction rule can avoid re-warning, since any stored day is reachable by a
    scene dated just before it and nothing here knows which days a campaign's
    scenes actually use. `LEDGER_LIMIT` carries why that is acceptable.

    Ties (and rows with no readable stamp) break on the key, so eviction is
    deterministic: two runs against the same file must not disagree about which
    row survived, or a dismissal would come back on one machine and not another.
    """
    if len(data) <= LEDGER_LIMIT:
        return data
    ordered = sorted(data.items(), key=lambda kv: (_stamp(kv[1].get("noticed_at")
                                                   if isinstance(kv[1], dict) else ""), kv[0]))
    return dict(ordered[len(data) - LEDGER_LIMIT:])


def mark(cid: str, keys: list[str], scene: str = "") -> list[str]:
    """Acknowledge these notice keys. Returns the ones newly written.

    Already-marked keys are skipped rather than re-stamped: the stamp records
    when the reader was first warned, and a second dismissal of a banner that
    two surfaces both showed should not rewrite that. Unknown keys are accepted
    — the ledger is a set of strings and has no idea what an event is, which is
    exactly what lets #103's commitments dismiss into it without changing
    anything here.

    A key past `KEY_LIMIT` is dropped rather than shortened, and the return value
    says so by not naming it: every key this app generates is bounded, so an
    overlong one is crafted, and storing a truncated version of it would be
    recording an acknowledgement of something nobody can be warned about.
    """
    wanted = [k.strip() for k in keys if isinstance(k, str) and k.strip()]
    wanted = [k for k in wanted if len(k) <= KEY_LIMIT]
    if not wanted:
        return []
    stamp = paths.now_iso()
    scene = str(scene or "")[:SCENE_LIMIT]
    done: list[str] = []
    # The return is outside the hold, not inside it: `campaign_lock.__exit__` is
    # typed as able to swallow, so a `return` in the body leaves mypy unable to
    # see that the function always returns -- the five findings `store.events`
    # carries in the mypy baseline are that exact shape, and a sixth here would
    # be a new one, not an inherited one.
    with locks.campaign_lock(cid):
        data = read(cid)
        for key in wanted:
            if key in data:
                continue
            data[key] = {"noticed_at": stamp, "scene": scene}
            done.append(key)
        if done:
            _write(cid, _trim(data))
    return done


def forget(cid: str, keys: list[str]) -> list[str]:
    """Drop these acknowledgements, so the notice can be shown again.

    The undo for a banner dismissed by mistake, and the only way back: `mark`
    refuses to overwrite a stamp, so without this a misclick silences an event
    permanently. Returns the keys that were actually there.
    """
    wanted = [k for k in keys if isinstance(k, str) and k]
    if not wanted:
        return []
    with locks.campaign_lock(cid):
        data = read(cid)
        done = [k for k in wanted if data.pop(k, None) is not None]
        if done:
            _write(cid, data)
    return done


def _bounded(text: str) -> str:
    """`text` for a key: itself when short, else a prefix plus a digest of it.

    Bounded AND collision-free, which a plain truncation is not: two observances
    sharing a 120-character prefix would otherwise share one acknowledgement, so
    dismissing the first would silence the second without the reader ever seeing
    it. The prefix is kept so the key stays legible in a hand-read notices.json,
    which is the whole reason the name is in the key rather than a hash of it.
    """
    if len(text) <= NAME_BUDGET:
        return text
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return f"{text[:NAME_BUDGET]}~{digest}"


def holiday_key(fixed: int, name: str) -> str:
    return f"holiday:{fixed}:{_bounded(name)}"


def event_key(fixed: int, eid: str) -> str:
    return f"event:{fixed}:{_bounded(eid)}"


def pending(cid: str, croot: Path, native: str, window: int | None = None) -> list[dict]:
    """Unacknowledged notices for the days just after `native`, soonest first.

    Each `{key, kind, name, in_days, friendly}`. `window` defaults to this
    root's configured `warn_days`; pass one to ask a narrower or wider question
    without editing the campaign's config.

    Strictly ahead of `native`, never on it. Something happening *today* is not
    a pre-notice — the scene panel already says what today's observances are,
    and the whole point of this feature is lead time. That also makes the window
    the same half-open span `events.upcoming` and `calendars.upcoming_holidays`
    already use, so the three cannot disagree about whether the day being left
    counts.

    Tolerant end to end — a calendar that will not load, a moment this calendar
    cannot parse, an unreadable events.json — because every caller is a panel
    that must degrade to showing nothing rather than failing the page around it.
    """
    if window is None:
        window = calendars.warn_days(croot)
    if window <= 0 or not native:
        return []
    provider = calendars.primary_provider(croot)
    if provider is None:
        return []
    try:
        fixed = calendars.fixed_of(provider, native)
        ahead = calendars.upcoming_holidays(calendars.read_calendar(croot), native, window)
    except (calendars.CalendarError, KeyError, TypeError, ValueError, OverflowError, OSError):
        return []

    def label(day: int) -> str:
        # A day the primary calendar can enumerate but not describe is a broken
        # provider, and the notice is still worth showing without its date line.
        try:
            return provider.describe(day)["friendly"]
        except (calendars.CalendarError, KeyError, TypeError, ValueError, OverflowError, OSError):
            return ""

    rows = [{"key": holiday_key(h["fixed"], h["name"]), "kind": "holiday",
             "name": h["name"], "in_days": h["in_days"], "friendly": label(h["fixed"])}
            for h in ahead]
    rows.extend({"key": event_key(fixed + row["in_days"], row["id"]), "kind": "event",
                 "name": row["name"], "in_days": row["in_days"], "friendly": row["friendly"]}
                for row in events.upcoming(cid, provider, fixed, window))
    seen = read(cid)
    rows = [r for r in rows if r["key"] not in seen]
    # `in_days` first, then the kind, then the name: a day carrying both an
    # authored event and an observance leads with the event, which is the one
    # the reader wrote down and the one a scene is likelier to be planned around.
    rows.sort(key=lambda r: (r["in_days"], r["kind"] != "event", r["name"]))
    return rows
