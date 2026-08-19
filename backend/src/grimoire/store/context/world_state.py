"""The world's own state, as the prompt sees it: world-info activation plus the
today / weather / character-state / group-state blocks.

`activate` is the swap point the module docstring names -- everything else here
gathers the data one section renders from.
"""

from __future__ import annotations

import re
from typing import Callable

from .. import (calendars, characters, entities, events, groupstate, overlay, pcs,
                playstate, turnstate, weather)
from ..appearances import versions as appearances_versions
from ..scenes import read as scenes_read
# Aliased to match `assemble.py` and `macros.py`, and because `_character_states`
# below takes a parameter named `cast`.
from . import cast as cast_data, semantic


def keyword_hit(keys, text: str) -> bool:
    """Whole-word, case-insensitive: does any key appear in `text`?

    Factored out of `activate` so archive retrieval (`archive._archive_entries`)
    selects by exactly these semantics rather than a lookalike that drifts from
    them -- "pact" must keep not matching the key "pac" on both sides of the
    seam.
    """
    return any(re.search(rf"\b{re.escape(k)}\b", text, re.IGNORECASE) for k in keys)


def _ref(e: dict) -> str:
    """An entry's pin ref (`"<kind>:<id>"`), as `store/pins.py` spells it.

    `.get` on both halves: `activate` is a pure function over whatever dicts it
    is handed, and a caller that supplies neither (several tests, and any future
    strategy assembling entries of its own) gets a ref that matches no rule
    rather than a KeyError.
    """
    return f"{e.get('kind')}:{e.get('id')}"


def activate(entries: list[dict], recent_text: str, present: frozenset = frozenset(),
             recall: Callable[[list[dict], str], list[dict]] | None = None,
             pinned_refs: frozenset = frozenset(),
             excluded_refs: frozenset = frozenset()) -> list[dict]:
    """Select world-info entries. Owned entries (owners non-empty) are silent unless one
    owner ref is in `present`; then keyless = always-on, keyed = any key whole-word (ci) in
    recent_text. Unowned entries behave as before.

    `secrecy: gm-only` (#49) is dropped here, before any other rule and before
    `recall` ever sees the entry: this function is THE gate every world-info
    entry passes through, so dropping it here is what makes "never enters the
    prompt" true of the keyword path, the always-on path and the similarity
    path at once, rather than of whichever one someone remembered. `secret`
    entries are selected exactly like public ones — the difference is entirely
    in how they render (see `secrecy_split`).

    `pinned_refs` / `excluded_refs` are the reader's own overrides (#129,
    resolved by `pins.active`), checked next — ahead of the owner gate, the
    keyword rule and `recall` alike. That ordering is the feature: those are
    guesses about what the scene needs, and a pin is the reader saying they
    already know.

    **A pin does not beat `gm-only`,** and the difference from the gates it does
    beat is the whole reason the order above is what it is. The owner gate and
    the keyword rule are CONDITIONAL — "not unless her owner is here", "not
    unless someone says the word" — and a pin is the reader answering the
    condition. `gm-only` is not a condition; it is the entry saying it is not
    for the model at all, which is a property of the record rather than of this
    turn. A pin that could override it would make pinning a way to leak the GM's
    own notes into the prompt, which is precisely what #49 exists to prevent.
    Pinning one is therefore inert, exactly like pinning a record the campaign
    has since deleted.

    A pin DOES beat the owner gate, which is the one override that costs
    something: an entry owned by a character who is not in the scene stays out
    so absent people's lore cannot leak into it. A pin names that entry
    explicitly, by a reader looking at their own campaign, so it goes in — but
    nothing else opens that gate, and an owned entry the reader has not named is
    as silent as it ever was.

    An excluded entry is dropped outright rather than merely failing the keyword
    rule: `recall` only ever sees what the keyword rule REJECTED, so leaving it
    in `missed` would let the second stage put back exactly what the reader
    asked to remove.

    `recall` is the second-stage retrieval strategy — `semantic.recall` in
    production, wired in by `_world_info`; anything with its signature in a
    test. It is handed the entries the keyword rule *rejected*, and only after
    the owner gate has already admitted them: an entry whose owner is absent
    never reaches it, so no similarity score can leak owned lore. Its hits are
    appended, so this function's result is a superset of what the keyword rule
    alone returns, in the same order. Defaulting it to None keeps the plain
    three-argument call exactly as pure and as offline as it has always been.
    """
    out: list[dict] = []
    missed: list[dict] = []
    for e in entries:
        if entities.normalize_secrecy(e.get("secrecy")) == entities.GM_ONLY:
            continue  # GM-only -> never enters the prompt, by any path, pin included
        ref = _ref(e)
        if ref in excluded_refs:
            continue  # the reader said no: not here, not through recall either
        if ref in pinned_refs:
            out.append(e)
            continue  # the reader said yes: no key and no owner has to agree
        owners = e.get("owners") or []
        if owners and not any(o in present for o in owners):
            continue  # owned but no owner in scene -> never leak
        keys = e.get("keys") or []
        if not keys or keyword_hit(keys, recent_text):
            out.append(e)
        else:
            missed.append(e)
    if recall is not None and missed:
        out.extend(recall(missed, recent_text))
    return out


def secrecy_split(entries: list[dict]) -> tuple[list[str], list[str]]:
    """`(public bodies, secret bodies)` for a list of activated entries.

    The two lists render as two blocks of one section rather than two sections:
    a secret is exactly as relevant to the turn as the public entry beside it,
    so it must live or die with that entry when the packer trims — a separate
    section could drop the secrets alone and leave the model narrating the
    scene they were the twist in.
    """
    public: list[str] = []
    secret: list[str] = []
    for e in entries:
        target = (secret if entities.normalize_secrecy(e.get("secrecy")) == entities.SECRET
                  else public)
        target.append(e["body"])
    return public, secret


def _world_info(cid: str, recent_text: str, exclude: frozenset = frozenset(),
                present: frozenset = frozenset(), pinned_refs: frozenset = frozenset(),
                excluded_refs: frozenset = frozenset()) -> tuple[list[dict], list[dict]]:
    """Activated lore/location/item/group/creature entries as
    {"body", "kind", "id"} dicts — _assemble renders the bodies and uses the
    refs (e.g. activated groups pull their campaign state into context).

    Returns ``(keyword, recalled)``: what the keyword rule selected, and what
    semantic recall added on top. They render as separate sections in separate
    packer tiers — see the comment at the return statement.

    Two different exclusions meet here, deliberately spelled apart. `exclude` is
    the CURRENT LOCATION, held back because the Current setting section already
    renders it; `excluded_refs` is the reader's own rule (#129), which applies to
    every kind. `pinned_refs` is its opposite, and both are enforced in
    `activate` — the skips below are only there to save reading a file whose
    body is about to be thrown away."""
    entries = []
    for kind in ("lore", "locations", "items", "groups", "creatures"):
        for meta in overlay.list_entities(cid, kind):
            ref = f"{kind}:{meta['id']}"
            if ref in excluded_refs:
                continue
            if kind == "locations" and meta["id"] in exclude:
                # Held back even when pinned: `exclude` is the CURRENT location,
                # which the Current setting section is already rendering, so
                # honouring the pin here would print its body twice. What the
                # pin buys it is protection from the packer, in `_assemble`.
                continue
            e = overlay.read_entity(cid, kind, meta["id"])
            keys = [k.strip() for k in e["meta"].get("keys", "").split(",") if k.strip()]
            owners = [o.strip() for o in e["meta"].get("owners", "").split(",") if o.strip()]
            if kind == "locations" and not keys and ref not in pinned_refs:
                # A keyless location surfaces only as the current setting, never
                # always-on -- unless the reader pinned it, which is a request
                # for this location in the prompt whatever the scene is doing.
                continue
            entries.append({"body": e["body"].strip(), "keys": keys, "owners": owners,
                            "secrecy": entities.normalize_secrecy(e["meta"].get("secrecy")),
                            "kind": kind, "id": meta["id"],
                            "name": e["meta"].get("name", meta["id"])})
    # The only production caller that supplies a second stage, so the strategy
    # is chosen in one visible place and `activate` stays a pure function of
    # its arguments. The attribute is resolved off the module on every call,
    # which is what keeps `semantic.recall` patchable from a test.
    #
    # The split is reported rather than merged because the two halves are
    # packed differently: keyword hits are `spotlight`, recalled ones are
    # `archive` (see assemble.SECTIONS). Merging them let a recall grow the
    # World info section until the packer dropped the whole thing, keyword
    # hits included -- so enabling recall could REMOVE lore, which is exactly
    # what this layer promises never to do.
    recalled: list[dict] = []

    def recall(candidates: list[dict], text: str) -> list[dict]:
        hits = semantic.recall(candidates, text)
        recalled.extend(hits)
        return hits

    activated = activate(entries, recent_text, present, recall=recall,
                         pinned_refs=pinned_refs, excluded_refs=excluded_refs)
    by_recall = {id(e) for e in recalled}
    return [e for e in activated if id(e) not in by_recall], recalled


def _today_data(cid: str, sid: str, croot) -> dict | None:
    history = scenes_read.get_time_history(cid, sid)
    if not history:
        return None
    cfg = calendars.read_calendar(croot)
    try:
        facts = calendars.today_facts(cfg, history[-1])
    except calendars.CalendarError:
        return None  # garbled date — omit, don't crash
    # The campaign's own scheduled events (#101) beside the calendar's holidays.
    # Two sources, one section: a holiday recurs and belongs to the world's
    # calendar, an event happens once and belongs to this campaign, but to the
    # model reading this block they are both "what today is". `events.day_facts`
    # is where the campaign half lives — `today_facts` takes a calendar config
    # and has no business reading a campaign — and `events.sooner` is the merge
    # rule the two callers of it share, so the prompt and the suggestion
    # snapshot cannot disagree about what is next.
    scheduled = events.day_facts(cid, croot, history[-1])
    return {"friendly": facts["friendly"], "weekday": facts["weekday"],
            "secondary_friendly": facts["secondary_friendly"],
            "holidays_today": facts["holidays_today"],
            "events_today": scheduled["events_today"],
            "upcoming": events.sooner(facts["upcoming"], scheduled["upcoming"]),
            "cast": cast_data.cast_datetime_facts(cid, sid, history[-1])}


def _weather_data(cid: str, sid: str) -> dict | None:
    """The sky at the scene's current location and moment, or None.

    Tolerant by construction — `current_weather` returns None rather than
    raising for a missing location, a missing moment, or a stored moment the
    campaign's calendar can no longer parse.
    """
    locations = scenes_read.get_location_history(cid, sid)
    moments = scenes_read.get_time_history(cid, sid)
    got = weather.current_weather(cid, locations[-1] if locations else None,
                                  moments[-1] if moments else None)
    if not got:
        return None
    out = {k: got[k] for k in ("condition", "temperature", "wind")}
    # Authored notes ride along: the model gets "the Wintertide storm" rather
    # than only `storm`, which is why a note is stored at all.
    out["notes"] = got.get("notes") or []
    return out


#: Titles that PRECEDE a personal name. The token after one of these is the
#: name, so `_short_alias` steps over it rather than giving up.
_HONORIFIC = frozenset({
    "mr", "mrs", "ms", "dr", "sir", "lady", "lord", "saint", "st",
    "old", "young", "king", "queen", "prince", "princess", "captain",
})
#: Articles. The token after one of these is a common noun, not a name -- "The
#: Woman" is an epithet -- so these end the search instead of being stepped over.
_ARTICLE = frozenset({"the", "a", "an"})
#: Nobiliary and patronymic particles, which are conventionally lower-cased
#: INSIDE a personal name -- `Winifred van Saltmarch`, `Mara de la Vance`. They
#: are the one place the capitalization rule below has to yield: a name is not
#: an epithet because it carries one. Allowed only in non-head position, so
#: nothing here can become an alias itself.
_PARTICLE = frozenset({
    "van", "von", "der", "den", "de", "del", "della", "di", "da", "do", "dos",
    "du", "la", "le", "ter", "ten", "bin", "ibn", "al", "af", "av",
})
#: Generational and honorary suffixes, which FOLLOW the family name. Stepped
#: back over when choosing the surname form -- `Mara Vance Jr.` is a person
#: called Vance, and taking the last token made `Jr` the alias while the name
#: prose actually uses matched nothing.
_SUFFIX = frozenset({"jr", "sr", "ii", "iii", "iv", "phd", "md", "esq"})
#: First tokens that are ordinary words rather than a personal name. A name
#: whose head is one of these yields no alias directly -- see `_short_alias`.
_NOT_A_GIVEN_NAME = _HONORIFIC | _ARTICLE

#: An ELIDED particle, which attaches to the name instead of standing beside it
#: -- `d'Ormesson`, `dell'Acqua`, `O'Brien`. `_PARTICLE` cannot cover these:
#: they are one token, and the set is matched whole, so `d'Ormesson` was a
#: lower-case token belonging to no set and rejected the WHOLE name -- neither
#: `Jean` nor `Ormesson` was derived and the suspicion went to the prompt. The
#: apostrophe is the signal and needs no lexicon: a short prefix, an apostrophe
#: (straight or typographic), then the name. The captured group is that name,
#: which prose uses on its own as readily as with the particle attached.
_ELIDED = re.compile(r"\w{1,4}['’](\w.*)$")


#: Punctuation a name token carries at its EDGES and that no form should keep:
#: the abbreviating dot (`Dr.`, `J.`), the list comma, and the marks a nickname
#: is set off by -- `Mara "Red" Vance`, `Mara (Red) Vance`. The quotes were the
#: leak: the alias came back as `"Red"`, and `_mentions` then wanted the quotes
#: in the suspicion too, so the ordinary `Red is hiding the ledger` matched
#: nothing. Interior punctuation is untouched, which is what keeps `d'Ormesson`
#: whole while stripping the quotes around `"Red"`.
_EDGE_PUNCT = ".,\"'()[]“”‘’«»"


def _word(token: str) -> str:
    """One token, lower-cased and stripped of the punctuation names carry --
    so `Dr.` is read as `dr`, `J.` as `j` and `"Red"` as `red`."""
    return token.strip(_EDGE_PUNCT).lower()


def _name_tokens(name: str) -> list[str]:
    """`name` split into tokens when it looks like a personal name, else [].

    One place decides that, because `_short_alias` and `_surname_alias` have to
    agree about it: a string either is a personal name, in which case both ends
    of it are worth matching, or it is an epithet, in which case neither is.
    """
    parts = name.split()
    if parts and _word(parts[0]) in _HONORIFIC:
        parts = parts[1:]                # "Dr. Mara Vance" -> "Mara Vance"
    elif len(parts) < 2:
        return []                        # one bare token needs no alias
    if not parts:
        return []                        # a bare honorific names nobody
    # NOT `isupper()`: a script without letter case -- Arabic, Hebrew, the CJK
    # scripts -- answers False to it for every token, so requiring upper case
    # rejected every multi-token name written in one and derived no alias at
    # all. That is the `\b` failure again: not a missed edge, an entire class of
    # campaign for which this filter did nothing. What actually marks an epithet
    # is a token that is explicitly LOWER case, and an uncased token is neither.
    if not all(not p[:1].islower() or _word(p) in _PARTICLE or _ELIDED.match(p)
               for p in parts[1:]):
        return []                        # "The Woman on the Pier" -- an epithet
    if parts[0][:1].islower():
        return []                        # a particle is never the head
    if any(_word(p) in _ARTICLE for p in parts):
        return []                        # "Woman Of The Pier" -- the same, title-cased
    return parts


def _short_alias(name: str) -> str:
    """The given name inside `name`, or "" when it has none to offer.

    `_mentions` needs this because prose says "Winifred is lying", not
    "Winifred Vance is lying" -- a whole-name match alone misses every ordinary
    reference.

    But the short form is only derived from something that LOOKS like a personal
    name: every token capitalized, no article among them, the head at least two
    characters and not an article or honorific. Taking the first token
    unconditionally was a real bug, not a theoretical one -- a card named "The
    Woman on the Pier" contributed the alias "The", and every suspicion
    containing the word "the" was then read as naming her and withheld. That is
    not the conservative direction; it silently empties the block.

    What rejects an epithet is the ARTICLE, not the token count. This capped
    names at three tokens for a while, which is a proxy that fails on the thing
    it is supposed to allow: four capitalized tokens -- a given name, a middle
    name and two surnames -- is an ordinary personal name, and capping it meant
    "Winifred is hiding the ledger" was checked only against the whole
    four-token string and reached the prompt. A title-cased epithet is caught by
    the article wherever it sits -- `Woman Of The Pier`, `Keeper Of The Flame`
    -- and a lower-cased one by the capitalization rule, so the count was never
    doing the work.

    The capitalization rule yields in exactly one place: a `_PARTICLE` inside
    the name. `Winifred van Saltmarch` and `Mara de la Vance` are conventional
    personal names whose middle tokens are lower-case by convention, and
    requiring every token to be capitalized rejected them wholesale -- so the
    card matched only in full while prose said "Winifred". The head is still
    required to be capitalized and is still checked against
    `_NOT_A_GIVEN_NAME`, so a particle can never become the alias itself, and
    the article check is unchanged: `de la` passes, `The`/`A`/`An` anywhere
    still does not.

    An honorific is STEPPED OVER rather than treated as a dead end: "Dr Mara
    Vance" yields `Mara`, because what follows a title is a name. An article is
    not, because what follows one is a common noun -- deriving `Woman` from "The
    Woman on the Pier" is the same over-match by a different route. That
    asymmetry is the whole reason the two sets are separate now.

    After the title is stepped over a SINGLE remaining token is enough ("Lady
    Winifred" -> `Winifred`, "Dr Vance" -> `Vance`), where a bare one is not:
    "Mara" alone needs no alias, since `_mentions` already matches it in full.

    Both lookups compare the token with its trailing punctuation removed, so
    the conventional "Dr. Mara Vance" is read the same as "Dr Mara Vance".
    Without that the abbreviation matched no set at all and `Dr.` came back as
    the alias itself -- missing the name it precedes AND matching every line
    that happens to abbreviate a doctor.
    """
    parts = _name_tokens(name)
    if not parts:
        return ""
    return _usable(parts[0])


def _surname_alias(name: str) -> str:
    """The family name inside `name`, or "" -- the other half of `_short_alias`.

    Prose refers to a character by surname as readily as by given name ("Vance
    is hiding the ledger"), and a form set holding only the full name and the
    given name matched neither, so the suspicion reached the prompt. It carries
    the same collision handling for free: `_character_states` subtracts the
    entry OWNER's forms, so two actors sharing a surname drop it from each
    other's filter exactly as two sharing a given name do.

    The family name is the last token that IS one: a generational suffix is
    stepped back over (`Mara Vance Jr.` -> `Vance`), because taking the last
    token blindly made `Jr` the alias and left `Vance` matching nothing — the
    surname form pointing at the one token in the name that is not a name. A
    trailing particle is stepped over for the same reason (`Mara de` is a
    malformed name, not a person called `de`), and a name that is nothing but
    suffixes after its head yields none. The one-character rule is the initial
    rule again.
    """
    parts = _name_tokens(name)
    if len(parts) < 2:
        return ""                        # nothing follows the given name
    i = len(parts) - 1
    while i > 0 and (_word(parts[i]) in _SUFFIX or _word(parts[i]) in _PARTICLE):
        i -= 1
    return "" if i == 0 else _usable(parts[i])


def _usable(token: str) -> str:
    """One token as a matchable form, or "" if it is not one on its own.

    Two characters, not three: `Jo Li` and `Dr. Li Chen` are ordinary names, and
    rejecting them meant the card matched only in full while prose said "Li
    knows the truth". Three was a proxy for "not a short ordinary word", and
    `_mentions` now does that job properly by matching one-word forms
    case-sensitively. One character stays out -- an initial would match every
    capital J standing alone.

    The length is measured on the token with its punctuation removed, which is
    the whole point: `J.` is two characters and one letter, so checking the raw
    token let the conventional `J. Smith` through as the alias `J.` -- the
    initial guard, defeated by the punctuation that marks it as an initial.

    The RETURNED form is stripped the same way, and by the same set. A nickname
    is written set off by quotes or brackets -- `Mara "Red" Vance` -- and
    `_interior_aliases` lands on exactly that token, so keeping the marks made the
    form `"Red"` and `_mentions` then required the suspicion to quote her too.
    A name is what is inside the marks; the marks are how the writer said it is
    a nickname.
    """
    word = _word(token)
    return "" if len(word) < 2 or word in _NOT_A_GIVEN_NAME else token.strip(_EDGE_PUNCT)


def _interior_aliases(name: str) -> set[str]:
    """EVERY name-shaped token between the head and the surname.

    `_short_alias` reads the head as the given name, which is wrong whenever the
    head is a title `_HONORIFIC` does not list -- `Professor Mara Vance` yields
    `Professor` and `Vance`, and the ordinary "Mara is hiding the ledger"
    matches neither and reaches the prompt. Every finite lexicon in this file
    has been found incomplete by the next review round, and the set of titles a
    person may carry (`Professor`, `Reverend`, `Sergeant`, a rank or role this
    world invented) is not one anybody can enumerate -- so this does not try.

    The structural fact is that an interior token is a name either way: the
    GIVEN name if what precedes it was a title, a MIDDLE name if it wasn't.
    Which cannot be decided without the lexicon and does not need to be, because
    both are worth matching.

    ALL of them, not the first. Taking one and stepping over only the tokens
    `_usable` rejects meant a second UNRECOGNIZED title stopped the walk --
    `Professor Reverend Mara Vance` yielded `Reverend` and lost `Mara`, which is
    the same lexicon dependency one token further along. Titles stack, and how
    many is not knowable either; the walk stops depending on the answer.

    The cost is that middle names and unrecognized titles become matchable,
    which is an over-hide only if some other line uses one of them for somebody
    else. That is a far smaller price than the leak: a title in front of a name
    is ordinary, and the name behind it is the one prose actually uses.
    """
    parts = _name_tokens(name)
    if len(parts) < 3:
        return set()                     # the second token is the surname
    # Particles and suffixes are stepped over here as everywhere else: `Mara de
    # Vance` is not a person called `de`, and `_usable` alone would let it
    # through -- two characters, in none of the rejected sets.
    return {f for f in (_usable(p) for p in parts[1:-1]
                        if _word(p) not in _PARTICLE and _word(p) not in _SUFFIX) if f}


def _elided_stem(alias: str) -> str:
    """The name inside an elided particle -- `d'Ormesson` -> `Ormesson`, or "".

    A form of its own, because prose drops the particle as readily as it keeps
    it, and the two spellings share no substring the matcher would find: with
    only `d'Ormesson` in the set, "Ormesson is hiding the ledger" reaches the
    prompt. `O'Brien` -> `Brien` falls out of the same rule; matching a stem
    prose rarely uses on its own costs an over-hide only if another line means
    somebody else by it.
    """
    m = _ELIDED.match(alias)
    return _usable(m.group(1)) if m else ""


def _forms(names: set[str]) -> set[str]:
    """Every string an actor can be recognized by: each name plus the given name,
    the surname and every name-shaped token between them -- each of those also
    without an elided particle. Resolved for the OWNER as well as for the others (see
    `_character_states`), which is what keeps two actors sharing any of those
    names from hiding each other's interiority."""
    out: set[str] = set()
    for n in names:
        aliases = {_short_alias(n), _surname_alias(n)} | _interior_aliases(n)
        out |= {n.strip()} | aliases | {_elided_stem(a) for a in aliases}
    return out - {""}


def _mentions(text: str, form: str) -> bool:
    """Whether `text` contains this one form of an actor's name. The caller
    supplies the forms (`_forms`), so that "which names count as this actor's"
    is decided once, where the owner's names are also in scope.

    A ONE-WORD form rejects a LOWER-CASE use of itself; a multi-word one is
    matched case-insensitively. Plenty of names are also ordinary words — Will,
    May, Hope, Grace, Art — and a case-insensitive whole-word match reads "Mara
    will steal the crates" as naming Will and withholds the paragraph. With that
    actor on stage, most of every other NPC's state disappears; this is the
    `The`-matches-everything bug again, reached without an epithet. A name is a
    proper noun and is capitalized wherever it is used as one, so case is the
    signal already in the text, and no whitelist of ambiguous names has to be
    right. Multi-word forms keep the looser match: "the woman on the pier"
    collides with nothing.

    What the rule rejects is only the all-lower-case use, NOT every case that
    differs from the stored one. Requiring the stored spelling exactly missed
    `WINIFRED is hiding the ledger` — an ordinary shape in a heading or in
    imported prose, and one where the writer is plainly naming her. The
    distinction the ordinary-word guard actually needs is `will` from `Will`,
    and upper case is on the far side of it. An uncased form is unaffected:
    `str.islower()` is False for a script with no case, so 李明 still matches.

    The word boundary is applied only at an end that is ASCII alphanumeric.
    `\b` is defined between a word character and a non-word character, and in
    a script written without spaces — 李明 in 李明藏着账本, ジョー in
    ジョーが隠している — both neighbours are word characters, so the boundary
    never matches and the name is never found. That is not a rare shape; it is
    every campaign not written in a spaced script, for which this filter did
    nothing whatsoever. Such a form is matched as a plain substring instead.

    The residual imprecision here is NOT one-directional, and that is worth
    stating plainly because the rest of this filter's is. An epithet-shaped name
    ("The Woman on the Pier") is matched only in full, so a suspicion that calls
    her "The Woman" is not recognized and reaches the prompt; a suspicion that
    writes a one-word name in lowercase is missed too; and a boundary-less form
    can match inside a longer word. A one-word name that is also an ordinary
    word over-matches when a sentence starts with it or a heading shouts it, and
    that is the same trade taken from the other end. The first two leak and the
    last two over-hide, and all of them are accepted rather than papered over: a
    leak costs one line, the over-match cost the feature, and a filter that
    cannot see a script at all costs everything for the people writing in it.
    """
    if not form:
        return False
    edge = r"\b"
    lead = edge if form[0].isascii() and form[0].isalnum() else ""
    tail = edge if form[-1].isascii() and form[-1].isalnum() else ""
    pattern = rf"{lead}{re.escape(form)}{tail}"
    if " " in form:
        return bool(re.search(pattern, text, re.IGNORECASE))
    # Search case-insensitively and then judge the text that was FOUND, rather
    # than pinning the pattern to the stored spelling: what disqualifies a hit
    # is that the writer used the word in lower case, not that they capitalized
    # it differently from the card.
    return any(not m.group(0).islower()
               for m in re.finditer(pattern, text, re.IGNORECASE))


#: The player macro, matched exactly as `macros._substitute` matches it —
#: `re.escape` of the literal token under IGNORECASE, so `{{User}}` counts and
#: `{{ user }}` does not (that one never expands either, so it is not a name).
_USER_MACRO = re.compile(r"\{\{user\}\}", re.IGNORECASE)


#: Markdown's tab stop. Indentation is a COLUMN count, not a character count,
#: and every nesting comparison below is one of those columns.
_TAB = 4

_LIST_MARKER = re.compile(r"[-*•+]\s|\d+[.)]\s")

#: Blockquote markers, taken off the front before a line is classified. A quote
#: is a wrapper, not a syntax of its own: `> - is hiding the ledger` is a list
#: item, and every rule below -- list marker, ATX heading, colon heading, setext
#: underline -- asks about the markdown INSIDE it. Reading the raw line saw `>`
#: where the bullet is, called it an ordinary paragraph, and popped the heading
#: that named her, publishing the subjectless detail underneath. One space per
#: marker is consumed, the markdown convention, so indentation deeper than that
#: survives and a quoted list still nests.
_QUOTE = re.compile(r"(>[ \t]?)+")

#: Markdown emphasis, stripped from the END of a line before asking whether it
#: is a heading. These blocks are authored prose and `**Winifred:**` is how a
#: subject line is conventionally written; a bare `endswith(":")` saw the
#: closing `**`, called it an ordinary line, and left the bullets under it
#: ungoverned -- publishing the detail whose subject had just been withheld.
_EMPHASIS_TAIL = "*_`~ \t"


#: An ATX markdown heading -- `## Winifred`. A heading is the other way a
#: subject line is written, and it carries no colon, so `_heads_a_list` has to
#: know both. Its level is what nests it, and unlike a colon heading it is NOT
#: ended by a blank line: `## Winifred` / blank / `- is hiding the ledger` is
#: ordinary markdown, and a heading that stopped governing at the blank would
#: leave that bullet with no subject.
_ATX = re.compile(r"(#{1,6})\s+\S")

#: A SETEXT underline -- the other standard markdown heading, where the title is
#: an ordinary line and the line BELOW it makes it a heading::
#:
#:     Winifred
#:     --------
#:     - is hiding the ledger
#:
#: It is the one heading form that is not recognizable from its own line, which
#: is why it needs its own pass: the title reads as a paragraph, so the bullets
#: under it were ungoverned and the detail survived while its subject was
#: withheld. `=` is level 1 and `-` is level 2, as in markdown.
_SETEXT = re.compile(r"(=+|-+)$")


def _heads_a_list(stripped: str) -> bool:
    """Whether this line introduces the list items below it."""
    return bool(_ATX.match(stripped)) or stripped.rstrip(_EMPHASIS_TAIL).endswith(":")


def _in_a_list(open_heads: list, indent: int) -> bool:
    """Whether a heading at `indent` is written INSIDE an open list item.

    A bullet or colon heading SHALLOWER than it is what "written under it"
    means. Asked at push time because the enclosing governors are in scope
    exactly then.
    """
    return any(h[2] != "atx" and h[1] < indent for h in open_heads)


def _nested(open_heads: list, indent: int) -> int:
    """The indent an ATX heading is nested at, or -1 when it is not in a list.

    `_outdented` reads it back. -1 because markdown allows a top-level heading
    up to three leading spaces, so the column alone cannot say.
    """
    return indent if _in_a_list(open_heads, indent) else -1


def _governor_rank(open_heads: list, indent: int) -> int:
    """The indent a COLON heading nests at: its own, or 0 at the top level.

    Markdown lets a top-level line carry one to three cosmetic leading spaces,
    and `  Winifred:` over a column-zero `- is hiding the ledger` is that line.
    Ranking the heading by its raw indent made the bullet look like a return to
    an outer level, popping the heading that names her and leaving the
    subjectless bullet ungoverned. A heading not inside a list is at the top
    level whatever its column, so it ranks there and nothing shallower exists to
    pop it. Inside a list the indent is real nesting and is kept -- that is the
    round-twenty case, an indented `Nested:` that must not govern the outer
    bullet after it.
    """
    return indent if _in_a_list(open_heads, indent) else 0


def _outdented(top: tuple, indent: int) -> bool:
    """Whether an open ATX heading is closed by a line at `indent`.

    A section normally ends only at the next heading of its level or shallower,
    which is why a blank line does not close one. But a heading written INSIDE a
    list item belongs to that item, and the item ends where the indentation
    does: without this, `- Winifred` / `  ### Plans` left `Plans` governing
    every later line in the block, so unrelated statements outside the list were
    withheld along with it.

    What makes a heading nested is the open LIST it sits inside, not its column.
    Markdown allows a top-level heading up to three leading spaces, so `  ###
    Winifred` at the start of a block is an ordinary section -- and closing it
    because a later column-zero bullet is "less indented" withheld the line that
    named her while publishing the subjectless bullet under it. The decision is
    made once, where the heading is pushed and the enclosing governors are in
    scope; -1 means "not inside a list" and never closes this way.
    """
    return top[2] == "atx" and top[4] >= 0 and top[4] > indent


def _entries(suspects: str) -> list[list[str]]:
    """`suspects` grouped into entries — the unit the filter withholds.

    **A paragraph is one entry.** Only a blank line or a list marker starts a
    new one; consecutive unbulleted lines belong together no matter how they
    begin.

    This was three times a whitelist of "words that continue a sentence"
    instead, and it leaked three times: first by dropping only the named line
    and keeping its pronoun-led continuation, then by keeping the lines BEFORE
    the name, then — with both of those closed — by treating a capitalized
    continuation as a fresh statement:

        Winifred is lying.
        At midnight, she plans to steal the crates.

    "At" is not a pronoun, so the second line read as new and survived. The
    lesson is not that the whitelist needed another word in it. Whether a
    sentence continues the one above is anaphora, and no list of head words
    decides it; within a paragraph any sentence can be about the previous
    sentence's subject. So the whitelist is gone rather than extended.

    The cost is real and is the one worth accepting: a paragraph of unbulleted
    suspicions is withheld together when any of them names a present actor, so
    granularity below the paragraph is no longer available. A blank line or a
    bullet buys it back, and both are cheap to write. The alternative — a
    heuristic that decides where a thought ends — has now been wrong three
    times in the direction that publishes a secret.

    A blank line becomes its own (empty) entry rather than being swallowed: it
    names nobody, so it survives, and paragraph spacing is preserved instead of
    collapsing when a neighbour is withheld.

    **A bullet under a heading is GOVERNED by that heading**, which is what the
    second element of each pair records: the index of the entry that heads this
    one, or -1. A heading is a non-bullet line ending in `:`, and the shape it
    creates is the natural way to write these::

        Winifred:
        - is hiding the ledger

    The bullet is its own entry and names nobody, so on its own verdict it
    survives -- publishing the private half of a suspicion whose subject was
    withheld one line above, which is the same leak `_entries` was written to
    close, arriving through the list marker instead of the line break.

    Governed rather than merged, deliberately. Merging the block into one entry
    would also bind `Notes:` to every bullet under it, so one named suspicion
    would cost the whole list -- and a heading that names nobody is exactly
    where entry granularity is worth keeping. `_visible_suspects` drops a
    subordinate entry when its heading is dropped and judges it on its own
    otherwise.

    Headings NEST, so the open ones are a stack rather than one innermost
    heading: a list can descend into a sub-heading and come back out, and the
    bullet that comes back out is still governed by the heading it never left.
    """
    out: list[list[str]] = []
    heads: list[int] = []
    fresh = True                            # the next line opens a new entry
    pending_blank = False                   # a blank whose pop is not yet decided
    # Open headings, outermost first: (entry, rank, kind, depth, indent).
    # `rank` is the indent for a colon heading or a heading bullet, and the
    # heading LEVEL for an ATX one -- the two never nest against each other by
    # the same measure. `depth` is the blockquote nesting the heading was opened
    # at, so leaving the quote closes it, and `indent` is where the line sat, so
    # leaving the LIST ITEM a heading was written inside closes it too.
    open_heads: list[tuple[int, int, str, int, int]] = []
    depth = 0                               # blockquote nesting of the last line
    for line in suspects.splitlines():
        # A blockquote is a wrapper, not a syntax of its own, so every rule
        # below asks about the markdown INSIDE it: `stripped` is the line's own
        # content and `indent` is measured from where that content starts, so a
        # quoted list nests exactly as an unquoted one does. A line that is
        # nothing but quote markers IS the blank line of its quote, and is
        # treated as one.
        #
        # Measured on a TAB-EXPANDED copy, because markdown nests by column and
        # a tab is one character but four columns. `  - Winifred` followed by
        # `\t- is hiding the ledger` is a child bullet, and counting characters
        # made it 2 against 1 -- the child looked shallower, popped the parent
        # that names her, and the subjectless line under it went to the prompt.
        # The ORIGINAL line is what gets stored, so nothing here rewrites the
        # author's whitespace; only the arithmetic sees the expansion.
        exp = line.expandtabs(_TAB)
        outer = len(exp) - len(exp.lstrip())
        stripped = exp.strip()
        quote = _QUOTE.match(stripped)
        if quote:
            inner = stripped[quote.end():]
            # The indent is measured from column zero, THROUGH the markers: a
            # quote nested inside a list item is written `- Winifred:` /
            # `  > - is hiding the ledger`, and measuring only inside the quote
            # put that child at indent 0, where the same-indent pop took its
            # parent out -- the named bullet withheld and the subjectless one
            # under it published. The whitespace before the marker is what
            # places the quote under its parent, so it counts.
            indent = outer + len(inner) - len(inner.lstrip())
            stripped = inner.strip()
        else:
            indent = outer
        if not stripped:
            out.append([line])              # blank: its own entry, and ends the paragraph
            heads.append(-1)
            fresh = True
            # The pop is DEFERRED to the next non-blank line, because whether
            # this blank ends anything depends on what follows it. A list item
            # continues across a blank when the paragraph below is indented
            # inside it --
            #
            #     - Winifred is lying.
            #
            #       She hid the ledger at the pier.
            #
            # -- which is ordinary markdown, and popping here made that second
            # paragraph independent: the named bullet withheld, the continuation
            # that has no subject of its own published.
            pending_blank = True
            continue
        atx = _ATX.match(stripped)
        item = bool(_LIST_MARKER.match(stripped))
        # A CHANGE of blockquote nesting is a container boundary, and the one a
        # blank line does not mark: `> Winifred is lying.` / `Mara watches the
        # pier.` leaves the quote with no blank between, and reading the second
        # line as a continuation of the first withheld an unrelated outer
        # statement along with the entry that names her. Leaving a quote also
        # closes the headings opened inside it, which otherwise governed -- and
        # so withheld -- the text that came after the quote ended. Entering one
        # pops nothing: a quoted list under an unquoted subject line is governed
        # by it, which is the shape the round before this one fixed.
        #
        # And entering one does not BREAK THE PARAGRAPH either, which is the
        # other half of the same asymmetry. `> Winifred is lying.` / `>> At
        # midnight, she steals the ledger.` is one thought written at two quote
        # depths; treating the deeper line as a fresh entry gave the continuation
        # its own verdict, and since it names nobody it survived -- the entry
        # naming her withheld, the subjectless private half published. That is
        # the multiline leak again, reached through the quote marker instead of
        # the line break. Only a DECREASE ends anything: it closes a container,
        # and what follows is outside it.
        was, depth = depth, quote.group(0).count(">") if quote else 0
        if depth < was:
            while open_heads and open_heads[-1][3] > depth:
                open_heads.pop()
            fresh = True
        if pending_blank:
            # Now the line after the blank is known, which is what decides
            # whether the blank ended anything. A blank ends the colon headings
            # and the bullets it closes -- but NOT:
            #
            #   * a markdown section heading, conventionally SEPARATED from its
            #     own list by exactly this blank line;
            #   * a bullet this line is indented inside, which is an ordinary
            #     multi-paragraph list item;
            #   * a colon heading whose LIST is what follows. `Winifred:` /
            #     blank / `- is hiding the ledger` is as ordinary as writing it
            #     tight, and popping here withheld the heading that names her
            #     while publishing the subjectless bullet under it -- the same
            #     leak the governor rule exists to close, reached through the
            #     blank line instead of the syntax. Only a LIST keeps it open:
            #     an ordinary paragraph after the blank is a new statement, and
            #     that is the case the pop was written for.
            while open_heads and (
                    _outdented(open_heads[-1], indent)
                    or (open_heads[-1][2] == "item" and indent <= open_heads[-1][1])
                    or (open_heads[-1][2] == "colon"
                        and not (item and indent >= open_heads[-1][1]))):
                open_heads.pop()
            pending_blank = False
        # A setext underline retroactively makes the line ABOVE it a heading, so
        # it is handled where that line's entry is still reachable rather than
        # in `_heads_a_list`, which only ever sees one line at a time. A `-----`
        # is not a list item -- `_LIST_MARKER` wants whitespace after the dash --
        # so it cannot have been consumed above.
        # The non-blank check keeps a horizontal RULE from being read as one: a
        # `---` after a blank line underlines nothing, and treating the blank as
        # a heading would govern the list below it by an entry that names nobody.
        if not item and out and out[-1][-1].strip() and _SETEXT.fullmatch(stripped):
            level = 1 if stripped[0] == "=" else 2
            # Same rule as the ATX branch below: a setext heading indented
            # inside a list item does not end that item.
            while open_heads and (
                    _outdented(open_heads[-1], indent)
                    or (open_heads[-1][2] == "atx" and open_heads[-1][1] >= level)
                    or (open_heads[-1][2] != "atx" and open_heads[-1][1] >= indent)):
                open_heads.pop()
            # The title's OWN governor is re-decided here, because it was
            # assigned one line too early: when it was read it looked like an
            # ordinary paragraph and inherited the section still open above it.
            # A heading is not governed by the section it closes -- without this
            # the second `Winifred` / `-------` block in a file would be
            # withheld along with the first.
            heads[len(out) - 1] = open_heads[-1][0] if open_heads else -1
            open_heads.append((len(out) - 1, level, "atx", depth, _nested(open_heads, indent)))
        if atx:
            # A section ends at the next heading of its own level or shallower,
            # and takes every colon heading and bullet inside it along -- but
            # only the ones it is not itself INSIDE. A heading indented within a
            # list item (`- Winifred` / `  ### Plans`) is part of that item, and
            # popping the item because it is not an ATX heading severed the
            # named parent: the bullets under `Plans` were then governed by a
            # heading that names nobody, and the private detail published. A
            # bullet or colon heading shallower than this line survives, and the
            # stack is outermost-first, so everything below it does too.
            level = len(atx.group(1))
            while open_heads and (
                    _outdented(open_heads[-1], indent)
                    or (open_heads[-1][2] == "atx" and open_heads[-1][1] >= level)
                    or (open_heads[-1][2] != "atx" and open_heads[-1][1] >= indent)):
                open_heads.pop()
        elif item:
            # Returning to an outer level POPS the headings it has left, and
            # leaves the one that governs BOTH levels in place. A single
            # innermost heading could not express that: it reset to "governed by
            # nobody" instead of to the outer heading, so in
            #
            #     Winifred:
            #     - Plans:
            #       - steal the ledger
            #     - knows the truth
            #
            # the last bullet came back ungoverned and published a suspicion
            # about a present actor.
            #
            # The two kinds pop at different depths, and the asymmetry is the
            # convention rather than an accident: a heading BULLET is a sibling
            # of the bullets at its own indent, so it goes at `>=`, while a
            # plain colon heading sits at the SAME indent as the bullets it
            # governs, so it may only be popped by something shallower (`>`).
            # Popping neither by indent left an indented `Nested:` governing the
            # outer-level bullet that came after it -- withholding an unrelated
            # suspicion rather than leaking one, but wrong in the same way.
            while open_heads and (
                    _outdented(open_heads[-1], indent)
                    or (open_heads[-1][2] == "item" and open_heads[-1][1] >= indent)
                    or (open_heads[-1][2] == "colon" and open_heads[-1][1] > indent)):
                open_heads.pop()
        governor = open_heads[-1][0] if open_heads else -1
        if item or atx or fresh:
            out.append([line])
            heads.append(governor)
            fresh = False
        else:
            out[-1].append(line)
        if item or _heads_a_list(stripped):
            # EVERY list item is pushed, not only a colon-terminated one: a
            # nested list is written `- Winifred` / `  - is hiding the ledger`
            # just as readily as with a colon, and requiring one left the
            # subjectless child ungoverned. A bullet governs what is indented
            # under it because that is what indenting under it means; the colon
            # only ever mattered for a line that is NOT a list item. Siblings
            # still do not govern each other -- the pop above runs first and
            # takes any item at this indent or deeper with it.
            #
            # For a non-item, heading-ness belongs to the line just read,
            # whichever entry it landed in: a `:` line arriving as a
            # continuation still heads the bullets below it.
            if atx:
                rank, kind = len(atx.group(1)), "atx"
            elif item:
                rank, kind = indent, "item"
            else:
                rank, kind = _governor_rank(open_heads, indent), "colon"
            open_heads.append((len(out) - 1, rank, kind, depth,
                               _nested(open_heads, indent) if atx else indent))
    return list(zip(out, heads))


def _names_present_actor(text: str, others: set[str]) -> bool:
    """Whether `text` refers to a present actor other than the one it belongs to.

    Two ways it can: by one of their names (`_mentions`), or through the
    `{{user}}` macro. The macro is the one an alias sweep structurally cannot
    see — at storage time the entry holds a token, not a name, so matching
    aliases against it finds nothing and the entry survives the filter. Then
    `_system_text` runs the assembled block through `macros.expand_macros`,
    which replaces `{{user}}` with the present player's name, and the private
    suspicion arrives at the model reading `Seraphine is hiding the ledger`
    after all. The filter has to know about the macro because the filter runs
    first.

    No expansion is done here, and none is needed: `_visible_suspects` only
    runs in a scene with a player in it, so the macro names a present actor by
    construction — and never *this* NPC, who is not the player. Resolving it to
    check would only be able to change the answer to the same one.
    """
    return bool(_USER_MACRO.search(text)) or any(_mentions(text, f) for f in others)


def _joined(lines: list[str]) -> str:
    """An entry's lines as one string a multi-word form can be found in.

    Not `" ".join(lines)`: the lines still carry their markdown, and a wrapped
    name is written under it. `- The Woman on the` / `  Pier is hiding the
    ledger` joins raw to `- The Woman on the   Pier ...` -- three spaces where
    the form has one -- and the escaped pattern matched nothing, so the fix that
    added this check did not reach the shape it is most likely to meet. A
    continuation carrying a blockquote marker puts a `>` in the middle of the
    name, which is worse.

    So each line is reduced to its own content first -- quote markers, then a
    leading list marker, then the surrounding whitespace -- and the runs between
    are collapsed to the single space the forms are written with. Only the
    COPY used for matching; the stored lines are untouched.
    """
    out = []
    for line in lines:
        text = line.expandtabs(_TAB).strip()
        quote = _QUOTE.match(text)
        if quote:
            text = text[quote.end():].strip()
        marker = _LIST_MARKER.match(text)
        if marker:
            text = text[marker.end():].strip()
        out.append(text)
    return re.sub(r"\s+", " ", " ".join(out)).strip()


def _visible_suspects(suspects: str, others: set[str]) -> str:
    """`suspects` with every ENTRY about ANOTHER present actor removed (#116).

    An NPC's suspicions are the one tier of stored knowledge that is wrong by
    construction: `knows` is what the character holds as fact and the narration
    may lean on, while `suspects` is a private, possibly-false belief. Feeding
    every present NPC's suspicions about each other into the same prompt on
    every turn is how a scene gets narration that quietly knows what no one on
    stage has said — the model has no way to tell "Mara believes this" from
    "this is true", because nothing in the block marks the difference.

    So a suspicion about someone *in the room* is withheld, and a suspicion
    about the absent world is kept: the first is the leak, the second is what
    makes the character play consistently offstage-aware. Its own subject is
    never "another actor" — an NPC suspecting something about herself is her
    own interiority and stays.

    Entry-granular rather than all-or-nothing, so one named suspicion does not
    cost the block — but an entry is withheld ENTIRE if any of its lines names a
    present actor, wherever in the entry that name falls (`_entries`). Half a
    suspicion is still the private half, whichever half it is.

    The residual imprecision is deliberate and one-directional: an entry about
    an absent character that happens to name a present one is dropped, because
    withholding a true line costs a detail while publishing a private one costs
    the scene.

    `others` is every FORM the other present actors are recognized by — their
    names and the given name inside each (`_forms`), minus the owner's own
    forms. See `_actor_aliases` for why one name each is not enough.

    An entry under a heading goes when the heading goes. Written in the order
    the entries come in, so it is transitive without a second pass: an entry's
    head always precedes it, and that head's verdict already includes its own.

    Not applied in `pcless` scenes — see `_character_states`.
    """
    entries = _entries(suspects)
    dropped: list[bool] = []
    for lines, head in entries:
        # The JOINED entry as well as each line. `_entries` already groups a
        # paragraph correctly, but a multi-word form is only ever found within
        # one line -- so a name soft-wrapped across the break (`The Woman on
        # the` / `Pier is hiding the ledger`) appeared in neither line and the
        # whole private suspicion survived. That is exactly the actor whose name
        # yields no short alias, so the full form is the only thing that can
        # match her at all.
        dropped.append((head >= 0 and dropped[head])
                       or any(_names_present_actor(line, others) for line in lines)
                       or (len(lines) > 1
                           and _names_present_actor(_joined(lines), others)))
    kept: list[str] = []
    for (lines, _), gone in zip(entries, dropped):
        if not gone:
            kept.extend(lines)
    return "\n".join(kept).strip()


def _npc_name(aroot, char_id: str) -> str:
    try:
        return characters.read_character(aroot, char_id)["meta"].get("name", char_id)
    except characters.CharacterNotFound:
        return char_id


def _actor_aliases(aroot, cid: str, actor: dict) -> set[str]:
    """Every name a present actor is referred to by — the character's own meta
    name AND the name on the card/persona version locked into this scene.

    Both, because the two can differ and each is the one some part of the app
    uses. The meta name labels the `# Character state` block; the locked card's
    `data.name` is what the character-description section, the transcript and
    the cast UI show, and therefore what another NPC's stored `suspects` is
    likely to call them. Matching on either one alone leaves a hole in the
    opposite direction: with only the meta name, a suspicion written as "The
    Woman on the Pier is hiding the ledger" sails past a filter looking for
    "Seraphine"; with only the card name, an NPC's own interiority reads as
    being about somebody else and is withheld for nothing.

    Failure is per actor and silent: an actor whose card cannot be read
    contributes the names that could be resolved. Fewer aliases can only hide
    less, never leak more — the same one-directional bias the rest of this
    filter is built on — and the alternative is one unreadable card emptying the
    whole block.
    """
    kind, aid = actor["kind"], actor["id"]
    # A LIST, not a set, and the filtering happens once at the end. A card is
    # hand-editable and importable, so `data.name` can arrive as a list or an
    # object -- and `set.add` of an unhashable value raises TypeError, which
    # escaped this function into `_character_states`' outer catch and emptied
    # the state block for EVERY actor in the scene. This function's failure
    # policy is per actor (below); collecting first and validating after is what
    # makes that true for a malformed name as well as for an unreadable card.
    names: list = []
    vid = appearances_versions.locked_version(cid, kind, aid)
    if kind == "characters":
        names.append(_npc_name(aroot, aid))
        if vid:
            try:
                names.append(characters.read_card(aroot, aid, vid)["data"].get("name", ""))
            except (characters.CharacterNotFound, characters.VersionNotFound):
                pass
    elif kind == "pcs":
        # BOTH names here too, for the reason the character branch above takes
        # both: a PC's container name and its locked persona name can differ,
        # and taking only the persona left a suspicion written against the
        # canonical PC name unmatched. The asymmetry was an oversight, not a
        # judgement about PCs.
        try:
            names.append(pcs.read_pc(aroot, aid)["meta"].get("name", ""))
        except pcs.PCNotFound:
            pass
        if vid:
            try:
                names.append(pcs.read_persona(aroot, aid, vid).get("name", ""))
            except (pcs.PCNotFound, pcs.PCVersionNotFound):
                pass
    return {n.strip() for n in names if isinstance(n, str) and n.strip()}


def _character_states(aroot, cid: str, cast, pcless: bool) -> list[dict]:
    """`aroot` is an `appearances.locked_actor_root` — `cast` comes from the
    appearance record, so both the campaign-local state.md and the actor's
    campaign-side copy are found under it.

    `pcless` drives the POV filter (#116). A pcless scene is the director's own
    view — there is no player whose knowledge the narration has to respect, and
    the whole point of an offscreen turn is to move NPCs by what they privately
    believe — so it gets full disclosure. A scene with a player in it does not:
    each present NPC's `suspects` is filtered to what is not about another
    present actor. `current_state` and `knows` are never filtered; they are the
    tier the narration is allowed to treat as true.

    Every present actor's names are resolved HERE, from the cast, rather than
    handed in by `assemble`. Its `npc_names` / `player_names` are one name each
    and the wrong one for this: they are the locked card's `data.name`, while
    the block is labelled with the character's meta name. Passing either list
    made the filter and the label disagree — see `_actor_aliases`, which
    resolves both names for each actor so neither hole is open.

    This is the coarse half of #116. Filtering by mentioned name is a heuristic
    standing in for an audience marked on the entry itself, and true
    player-facing POV needs a scene to record whose eyes it is behind (#86) and
    knowledge to be stored per-entry rather than as prose (#122). Both are
    prerequisites this deliberately does not invent a field for.
    """
    try:
        aliases = [(a, _actor_aliases(aroot, cid, a)) for a in cast]
        out = []
        for actor, own in aliases:
            if actor["role"] != "npc" or actor["kind"] != "characters":
                continue
            char_id = actor["id"]
            st = playstate.read_state(aroot, char_id)
            if not st:
                continue
            name = _npc_name(aroot, char_id)
            if not pcless:
                # Every FORM of every OTHER present actor, minus this one's
                # own — a character suspecting something about herself is her
                # own interiority, under whichever name she is written down by.
                #
                # Forms, not names, because the collision is between the
                # DERIVED ones: an owner called `Mara Chen` beside an actor
                # called `Mara Vance` shares the short alias `Mara`, and
                # subtracting stored names alone left it in `others` — so her
                # own "Mara Chen fears she made a mistake" matched the other
                # actor and was withheld, which is precisely the exception this
                # subtraction exists to make. `Mara Vance` in full still
                # withholds, because only the ambiguous form is dropped.
                #
                # Case-FOLDED, because that is the comparison `_mentions` makes:
                # a multi-word form matches under IGNORECASE and a one-word form
                # matches any spelling that is not all lower case, so `MARA` and
                # `Mara` are one form to the matcher and have to be one form
                # here too. Subtracting exact strings left the other actor's
                # `Mara` in `others` for an owner written `MARA CHEN`, and the
                # protection silently did not apply to the pair it exists for.
                own_forms = {f.casefold() for f in _forms(own)}
                others = {f for f in set().union(*(_forms(names)
                                                   for other, names in aliases
                                                   if other is not actor), set())
                          if f.casefold() not in own_forms}
                st = {**st, "suspects": _visible_suspects(st["suspects"], others)}
            if st["current_state"] or st["knows"] or st["suspects"]:
                out.append({"name": name, **st})
        return out
    except Exception:  # noqa: BLE001 — garbled state: omit, don't crash the context build
        return []


def _transient_states(cast, live: dict) -> list[dict]:
    """The transient per-turn ledger, decayed to what is still live (#120).

    Takes the already-read map rather than reading it, so `_assemble` can pair
    it with the transcript under one campaign-lock hold — `_persist_reply`
    writes the reply and its entry under that same lock, and a reader that
    fetched them separately could see the new narration beside the previous
    turn's mood.

    Labelled with the CAST name — the locked card's `data.name`, which is what
    the character-description section shows, what the transcript's
    `**Speaker:**` markers carry, and therefore what the model was asked to key
    its tracker block by. The adjacent `# Character state` block labels with the
    character's *meta* name instead; where the two differ this section agrees
    with the half the model writes, because writing it back under a name it
    never uses is how a value stops resolving.

    Same failure policy as `_character_states`: a garbled ledger omits the
    block rather than crashing a context build on the way to a paid generation.
    `turnstate.read` already swallows an unparseable file; this covers the rest.
    """
    try:
        out = []
        for a in cast:
            if a.get("role") != "npc" or a.get("kind") != "characters":
                continue
            fields = live.get(f"characters:{a['id']}") or {}
            rows = [{"label": f, "value": fields[f]} for f in turnstate.FIELDS if fields.get(f)]
            if rows:
                out.append({"name": a.get("name") or a["id"], "fields": rows})
        return out
    except Exception:  # noqa: BLE001 — garbled state: omit, don't crash the context build
        return []


def _group_states(cid: str, croot, activated: list[dict],
                  secrecy: str = entities.PUBLIC) -> list[dict]:
    """State for each activated group at `secrecy` that has a state.md — same
    failure policy as _character_states: a garbled file omits the block, never
    crashes.

    Filtered by level rather than returning everything, because a group's state
    is the half of it worth hiding: `groupstate.FIELDS` ends in `secrets`. A
    secret group whose state rendered in the plain block would have its
    description labelled and its secrets published — the feature inverted. The
    caller asks twice and the section renders the two answers as two blocks
    (see `sections/group_state.j2`); `gm-only` groups are not in `activated` at
    all, so their state is already gone with them.
    """
    try:
        out = []
        for e in activated:
            if e["kind"] != "groups" or entities.normalize_secrecy(e.get("secrecy")) != secrecy:
                continue
            st = groupstate.read_state(croot, e["id"])
            if st and any(st[k] for k in groupstate.FIELDS):
                out.append({"name": e["name"], **st})
        return out
    except Exception:  # noqa: BLE001 — garbled state: omit, don't crash the context build
        return []
