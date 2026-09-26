"""Birthdate precision against the campaign's calendar.

A provider-native full date or `--month-day` identifies an anniversary;
only a full date identifies an age. `--month` and `year-month` can suggest
a scene in that month without inventing a day. A year alone is metadata,
with no anniversary signal. These forms extend the existing container and
persona field, so switching card versions does not change a character's birth.

Suggestions and conversation retrieval scan campaign-visible characters,
including ones created only there. Clock digests read the locked appearance
roster: a character who never appeared has no crossed scene time.

The gather lives here once and each caller supplies its own question:
`upcoming` for scene ideas, `crossed` for clock advances, and `relevant`
for birthday questions in conversation.

Sits *below* both callers deliberately. `suggest` and `clock` are siblings and
either importing the other would close a cycle (`clock` reads the chronicle,
which imports `scenes`, which is what `suggest`'s own reach already pulls in),
so the shared half had to come down a level rather than sideways.

An unreadable date is skipped for anniversaries. Conversation retrieval can
still show the saved string when the calendar provider is unavailable.
"""

from __future__ import annotations

import re

from . import calendars, characters, overlay, pcs
from .appearances import cast as appearances_cast
from .appearances import paths as appearances_paths
from .campaigns import paths as campaigns_paths


def gather(cid: str, roster: list[dict], *, visible_characters: bool = False,
           include_undated: bool = False) -> list[dict]:
    """`[{name, birth}]` for dated roster actors, or all when requested.

    The character path reads only container metadata, so the visible-roster
    scan does not parse every version card and image sidecar.

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
    seen: set[str] = set()
    for a in roster:
        try:
            if a["kind"] == "pcs":
                birth = pcs.read_persona(aroot, a["id"], a["version"]).get("birthdate", "")
                name = pcs.read_pc(aroot, a["id"])["meta"].get("name", a["id"])
            else:
                name, birth = characters.birthdate_meta(aroot, a["id"])
        except (characters.CharacterNotFound, pcs.PCNotFound, pcs.PCVersionNotFound):
            continue
        if birth or include_undated:
            out.append({"name": name, "birth": birth})
        if a["kind"] == "characters":
            seen.add(a["id"])
    if visible_characters:
        out.extend(_visible_birthdates(cid, seen, include_undated=include_undated))
    return out


def _visible_birthdates(cid: str, seen: set[str], *, include_undated: bool) -> list[dict]:
    # Suggestions include characters who have never appeared, including
    # campaign-created NPCs. The appearance roster cannot name either.
    out = []
    for a in overlay.character_roster(cid):
        if a["id"] in seen:
            continue
        try:
            name, birth = characters.birthdate_meta(overlay.char_root(cid, a["id"]), a["id"])
        except characters.CharacterNotFound:
            continue
        if birth or include_undated:
            out.append({"name": name, "birth": birth})
    return out


def _parts(birth: str) -> tuple[int | None, str, int | None] | None:
    """Incomplete year/month/day parts, or None for a full date.

    The leading `--` is disjoint from a provider's year-first native form and
    preserves month keys such as `Mirtul` without imposing Gregorian notation.
    """
    if birth.startswith("--"):
        raw = birth[2:]
        month, sep, day = raw.rpartition("-")
        if sep and day.isdigit():
            n = int(day)
            if month and 1 <= n <= 31:
                return None, month, n
            raise calendars.CalendarError(f"bad birthdate: {birth!r}")
        if raw:
            return None, raw, None
        raise calendars.CalendarError(f"bad birthdate: {birth!r}")
    if re.fullmatch(r"-?\d+", birth):
        return int(birth), "", None
    if re.fullmatch(r"-?\d+-.+-\d{1,2}", birth):
        return None  # complete provider-native date; let the provider validate it
    match = re.fullmatch(r"(-?\d+)-(.+)", birth)
    if match:
        return int(match[1]), match[2], None
    return None


def _birth_fixed(provider, birth: str, asof_fixed: int) -> int | None:
    parts = _parts(birth)
    if parts is None:
        return calendars.fixed_of(provider, birth)
    _, month, day = parts
    if day is None:
        return None
    year = provider.describe(asof_fixed)["year"]
    # Leap days and leap months need a year in which they exist. Nineteen years
    # covers the Hebrew leap cycle; the Gregorian leap day is found sooner.
    for candidate in range(year, year - 20, -1):
        try:
            return provider.parse(f"{candidate}-{month}-{day:02d}")
        except calendars.CalendarError:
            continue
    raise calendars.CalendarError(f"bad birthdate: {birth!r}")


def facts(provider, birth: str, asof: str) -> tuple[int | None, bool]:
    """Known age and whether this is an exact birthday; missing year has no age."""
    asof_fixed = calendars.fixed_of(provider, asof)
    born = _birth_fixed(provider, birth, asof_fixed)
    if born is None:
        return None, False
    return (None if _parts(birth) is not None else provider.age(born, asof_fixed),
            provider.is_anniversary(born, asof_fixed))


def relevant(cid: str, recent_text: str) -> list[dict]:
    """Birthdate metadata retrieved only when the conversation asks for it.

    A named actor narrows the block to that actor, including an explicit
    unknown when the date is unset. A generic question gets dated actors only.
    Campaign-only characters are part of that union.
    """
    if not re.search(r"\b(?:birthday|birthdays|birthdate|birthdates|born)\b|\bhow old\b",
                     recent_text, re.IGNORECASE):
        return []
    rows = gather(cid, appearances_cast.roster(cid), visible_characters=True,
                  include_undated=True)
    named = [r for r in rows if _named(r["name"], recent_text)]
    rows = named or [r for r in rows if r["birth"]]
    provider = calendars.primary_provider(campaigns_paths.campaign_root(cid))
    if provider is None:
        return [{"name": row["name"], "birthdate": row["birth"] or "not recorded"} for row in rows]
    return [{"name": row["name"],
             "birthdate": (_friendly_birthdate(provider, row["birth"])
                           if row["birth"] else "not recorded")}
            for row in rows]


def _named(name: str, text: str) -> bool:
    if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text, re.IGNORECASE):
        return True
    # A given name is an ordinary way to ask about a character with a family
    # name. Avoid deriving an alias from epithet heads such as "The".
    first = name.split()[0] if name.split() else ""
    return (len(name.split()) > 1 and len(first) >= 3 and first.istitle()
            and first.casefold() not in {"the", "a", "an"}
            and bool(re.search(rf"(?<!\w){re.escape(first)}(?!\w)", text, re.IGNORECASE)))


def _friendly_birthdate(provider, birth: str) -> str:
    try:
        parts = _parts(birth)
        if parts is None:
            return calendars.friendly(provider, birth)
        year_value, month, day = parts
        if not month:
            return str(year_value)
        # A yearless key may name a leap month, so use the first of a bounded
        # run of years in which this provider offers it.
        label = month
        for year in range(provider.RULE_REFERENCE_YEAR, provider.RULE_REFERENCE_YEAR + 20):
            found = next((m for m in provider.months(year)
                          if str(m["key"]).casefold() == month.casefold()), None)
            if found:
                label = found["name"]
                break
        shown = f"{label} {day}" if day is not None else label
        return f"{shown}, {year_value}" if year_value is not None else shown
    except (calendars.CalendarError, ValueError, OverflowError, OSError):
        return birth


def _when(provider, birth: str, now_fixed: int) -> tuple[str | None, int | None]:
    parts = _parts(birth)
    born = _birth_fixed(provider, birth, now_fixed)
    if parts is not None and not parts[1]:
        return None, None  # a year alone has no anniversary month
    for d in range(calendars.UPCOMING_WINDOW_DAYS + 1):
        day_fixed = now_fixed + d
        if born is not None and provider.is_anniversary(born, day_fixed):
            return ("today" if d == 0 else f"in {d} days",
                    None if parts else provider.age(born, day_fixed))
        if born is None and parts is not None:
            day = provider.describe(day_fixed)
            month = provider.months(day["year"])[day["month"] - 1]
            if str(month["key"]).casefold() == parts[1].casefold():
                return ("this month" if d == 0 else f"in {day['month_name']}"), None
    return None, None


def upcoming(cid: str, now: str, roster: list[dict], *, visible_characters: bool = False) -> list[dict]:
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
    for row in gather(cid, roster, visible_characters=visible_characters):
        try:
            when, age = _when(provider, row["birth"], now_fixed)
            if when is None:
                continue
            out.append({"name": row["name"], "age": age, "when": when})
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
    the whole cast. Each actor/day match goes through the provider's own
    anniversary rule so leap-month shifts are respected.
    Bounding the span is the caller's job (`clock.SCAN_LIMIT_DAYS`).
    """
    if hi_fixed <= lo_fixed or not rows:
        return []
    # `CalendarError` only for birthdate reads below. That is the failure of the *data*
    # -- a birthdate string this calendar cannot parse -- and skipping the actor
    # is the right answer to it. A `describe` that returns something other than a
    # mapping with `friendly` in it is a broken provider, not a bad row, and it
    # fails here for the same reason it already fails in `calendars.today_facts`:
    # see `clock._holidays`, which draws the same line and says why.
    born: list[tuple[dict, int]] = []
    for row in rows:
        try:
            born_fixed = _birth_fixed(provider, row["birth"], lo_fixed)
            if born_fixed is None:
                continue
        except calendars.CalendarError:
            continue   # a birthdate this calendar cannot read is simply not tracked
        born.append((row, born_fixed))
    if not born:
        return []      # nothing to match: skip the whole per-day walk
    out: list[dict] = []
    for f in range(lo_fixed + 1, hi_fixed + 1):
        day = provider.describe(f)
        for row, birth_fixed in born:
            # Let the provider move leap-month or day-30 anniversaries. A raw
            # month/day comparison misses those in a common or short year.
            if not provider.is_anniversary(birth_fixed, f):
                continue
            # Labelled only on a match: formatting every day of the span to name
            # the one or two that match would be four hundred provider calls for
            # two rows. `age` re-parses the birthdate, which `born` already proved
            # parseable, so the guard is belt-and-braces rather than load-bearing.
            try:
                native = provider.format(f)
                found = {"name": row["name"], "native": native,
                         "friendly": day["friendly"],
                         "age": facts(provider, row["birth"], native)[0]}
            except calendars.CalendarError:
                continue
            out.append(found)
    return out
