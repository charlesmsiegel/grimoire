"""Roster birthdates against the campaign's calendar: whose anniversary is
coming up, and whose an advance just crossed.

Both questions read the same thing — a birthdate per roster actor, one card
read each — and answer it against the campaign's primary provider, so the
gather lives here once and each caller brings its own predicate. Lifted out of
`suggest._birthdays` when the campaign clock (#100) needed the second
question; `suggest` now asks for `upcoming` and gets exactly what it computed
before.

Sits *below* both callers deliberately. `suggest` and `clock` are siblings and
either importing the other would close a cycle (`clock` reads the chronicle,
which imports `scenes`, which is what `suggest`'s own reach already pulls in),
so the shared half had to come down a level rather than sideways.

Every failure mode here degrades to "no birthdays": a misconfigured calendar,
an actor whose card was deleted from under the appearance record, a birthdate
the provider refuses. None of them is worth failing a digest or a suggestion
prompt over.
"""

from __future__ import annotations

from . import calendars, characters, pcs
from .appearances import paths as appearances_paths
from .campaigns import paths as campaigns_paths


def gather(cid: str, roster: list[dict]) -> list[dict]:
    """`[{name, birth}]` for every roster actor that declares a birthdate.

    One card read per actor, and the name comes out of the same read as the
    birthdate — the version this replaced read each character twice.

    No type-coercion helper here, deliberately, and this is the reason rather
    than an oversight: both fields come out of `parse_frontmatter`, whose values
    are string scalars by construction (`dict[str, str]`) however the file was
    hand-edited. `plot._field` exists because plot.json is *JSON* and a
    hand-written mapping there reaches React intact; a card cannot do that. The
    one place in this feature where the lesson does apply is `clock._row`, over
    the JSON the clock itself writes.
    """
    aroot = appearances_paths.locked_actor_root(cid)   # roster actors are locked, so campaign-side
    out: list[dict] = []
    for a in roster:
        try:
            if a["kind"] == "pcs":
                birth = pcs.read_persona(aroot, a["id"], a["version"]).get("birthdate", "")
                name = pcs.read_pc(aroot, a["id"])["meta"].get("name", a["id"])
            else:
                meta = characters.read_character(aroot, a["id"])["meta"]
                birth, name = meta.get("birthdate", ""), meta.get("name", a["id"])
        except (characters.CharacterNotFound, pcs.PCNotFound, pcs.PCVersionNotFound):
            continue
        if birth:
            out.append({"name": name, "birth": birth})
    return out


def upcoming(cid: str, now: str, roster: list[dict]) -> list[dict]:
    """Birthdays inside `calendars.UPCOMING_WINDOW_DAYS` of `now`, each
    `{name, age, when}` where `when` is "today" or "in N days"."""
    if not now:
        return []
    provider = calendars.primary_provider(campaigns_paths.campaign_root(cid))
    if provider is None:
        return []
    try:
        now_fixed = calendars.fixed_of(provider, now)
    except calendars.CalendarError:
        return []
    out: list[dict] = []
    for row in gather(cid, roster):
        try:
            when = None
            for d in range(0, calendars.UPCOMING_WINDOW_DAYS + 1):
                if calendars.is_anniversary(provider, row["birth"], provider.format(now_fixed + d)):
                    when = "today" if d == 0 else f"in {d} days"
                    break
            if when is None:
                continue
            out.append({"name": row["name"], "age": calendars.age(provider, row["birth"], now),
                        "when": when})
        except calendars.CalendarError:
            continue
    return out


def crossed(provider, lo_fixed: int, hi_fixed: int, rows: list[dict]) -> list[dict]:
    """Birthdays landing in `(lo_fixed, hi_fixed]`, each
    `{name, age, native, friendly}` — the anniversaries an advance passed.

    Half-open at the start: the moment being left is a day already lived
    through, so counting its birthday as "crossed" would announce the same one
    again on every advance out of that day.

    Takes a resolved provider and the gathered rows rather than a `cid`: the
    caller (`clock.digest`) already holds both, and the span is walked once for
    the whole cast — one `describe` per day, not one per day per actor.
    Bounding the span is the caller's job (`clock.SCAN_LIMIT_DAYS`).
    """
    if hi_fixed <= lo_fixed or not rows:
        return []
    # TypeError alongside KeyError in both reads below: `describe` is a provider
    # method and a provider may be a user-authored plugin, so a return value that
    # is not a mapping is a plugin bug to skip past, not a 500 for the campaign.
    born: list[tuple[dict, tuple[int, int]]] = []
    for row in rows:
        try:
            d = provider.describe(calendars.fixed_of(provider, row["birth"]))
            born.append((row, (d["month"], d["day"])))
        except (calendars.CalendarError, KeyError, TypeError):
            continue   # a birthdate this calendar cannot read is simply not tracked
    out: list[dict] = []
    for f in range(lo_fixed + 1, hi_fixed + 1):
        try:
            day = provider.describe(f)
            today = (day["month"], day["day"])
        except (calendars.CalendarError, KeyError, TypeError):
            continue
        for row, md in born:
            # The same (month, day) comparison `provider.is_anniversary` makes,
            # hoisted out of the per-actor loop: a calendar whose leap month
            # shifts a date (Hebrew Adar) answers identically, because both
            # sides of the comparison come from that provider's own `describe`.
            if today != md:
                continue
            # All three reads inside the try, and only on a match: `format` and
            # the `friendly` key are provider output exactly as `describe` is, and
            # labelling every day of the span to name the one or two that match
            # would be four hundred provider calls for two rows.
            try:
                native = provider.format(f)
                found = {"name": row["name"], "native": native,
                         "friendly": day["friendly"],
                         "age": calendars.age(provider, row["birth"], native)}
            except (calendars.CalendarError, KeyError, TypeError):
                continue
            out.append(found)
    return out
