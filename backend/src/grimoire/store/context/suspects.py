"""The POV filter (#116): which of an NPC's private suspicions name another
actor on stage, and so stay out of a prompt with a player in it.

`world_state._character_states` asks `visible` for each present
NPC's `suspects`, handing it every form (`forms`) the other present actors
are recognised by. Everything here is about reading prose that was written
for a person: how a name is shortened and spelled (`_short_alias` and its
neighbours, `_mentions`), and how a block of suspicions is grouped into the
entries that are withheld whole (`_entries`). It reads no store and holds no
state; its inputs are strings.
"""

from __future__ import annotations

import re
from typing import NamedTuple

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
_ELIDED = re.compile(r"\w{1,4}['\u2019](\w.*)$")


#: Punctuation a name token carries at its EDGES and that no form should keep:
#: the abbreviating dot (`Dr.`, `J.`), the list comma, and the marks a nickname
#: is set off by -- `Mara "Red" Vance`, `Mara (Red) Vance`. The quotes were the
#: leak: the alias came back as `"Red"`, and `_mentions` then wanted the quotes
#: in the suspicion too, so the ordinary `Red is hiding the ledger` matched
#: nothing. Interior punctuation is untouched, which is what keeps `d'Ormesson`
#: whole while stripping the quotes around `"Red"`.
_EDGE_PUNCT = ".,\"'()[]“”\u2018\u2019«»"


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
    the same collision handling for free: `world_state._character_states` subtracts the
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


def forms(names: set[str]) -> set[str]:
    """Every string an actor can be recognized by: each name plus the given name,
    the surname and every name-shaped token between them -- each of those also
    without an elided particle. Resolved for the OWNER as well as for the others (see
    `world_state._character_states`), which is what keeps two actors sharing any of those
    names from hiding each other's interiority."""
    out: set[str] = set()
    for n in names:
        aliases = {_short_alias(n), _surname_alias(n)} | _interior_aliases(n)
        out |= {n.strip()} | aliases | {_elided_stem(a) for a in aliases}
    return out - {""}


def _mentions(text: str, form: str) -> bool:
    """Whether `text` contains this one form of an actor's name. The caller
    supplies the forms (`forms`), so that "which names count as this actor's"
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


class _Head(NamedTuple):
    """A heading `_entries` still holds open over the lines below it."""
    #: The entry it heads, and so governs the entries below it.
    entry: int
    #: The indent for a colon heading or a heading bullet, and the heading
    #: LEVEL for an ATX one -- the two never nest against each other by the
    #: same measure.
    rank: int
    #: `"atx"` (a markdown section heading, setext included), `"item"` (a
    #: list item) or `"colon"` (a line ending in `:`).
    kind: str
    #: The blockquote nesting it was opened at, so leaving the quote closes it.
    depth: int
    #: Where the line sat, so leaving the LIST ITEM a heading was written
    #: inside closes it too. An ATX heading's is `_nested`'s answer.
    indent: int


def _in_a_list(open_heads: list[_Head], indent: int) -> bool:
    """Whether a heading at `indent` is written INSIDE an open list item.

    A bullet or colon heading SHALLOWER than it is what "written under it"
    means. Asked at push time because the enclosing governors are in scope
    exactly then.
    """
    return any(h.kind != "atx" and h.rank < indent for h in open_heads)


def _nested(open_heads: list[_Head], indent: int) -> int:
    """The indent an ATX heading is nested at, or -1 when it is not in a list.

    `_outdented` reads it back. -1 because markdown allows a top-level heading
    up to three leading spaces, so the column alone cannot say.
    """
    return indent if _in_a_list(open_heads, indent) else -1


def _governor_rank(open_heads: list[_Head], indent: int) -> int:
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


def _outdented(top: _Head, indent: int) -> bool:
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
    return top.kind == "atx" and top.indent >= 0 and top.indent > indent


def _content(line: str) -> tuple[str, int, int]:
    """`(content, indent, quote depth)` of one line, as `_entries` classifies it.

    A blockquote is a wrapper, not a syntax of its own, so every rule in
    `_entries` asks about the markdown INSIDE it: the content is the line's own
    text and the indent is measured from where that text starts, so a quoted
    list nests exactly as an unquoted one does. A line that is nothing but
    quote markers IS the blank line of its quote, and comes back with no
    content.

    Measured on a TAB-EXPANDED copy, because markdown nests by column and a tab
    is one character but four columns. `  - Winifred` followed by
    `\\t- is hiding the ledger` is a child bullet, and counting characters made
    it 2 against 1 -- the child looked shallower, popped the parent that names
    her, and the subjectless line under it went to the prompt. The ORIGINAL
    line is what `_entries` stores, so nothing here rewrites the author's
    whitespace; only the arithmetic sees the expansion.
    """
    exp = line.expandtabs(_TAB)
    outer = len(exp) - len(exp.lstrip())
    stripped = exp.strip()
    quote = _QUOTE.match(stripped)
    if not quote:
        return stripped, outer, 0
    inner = stripped[quote.end():]
    # The indent is measured from column zero, THROUGH the markers: a quote
    # nested inside a list item is written `- Winifred:` / `  > - is hiding the
    # ledger`, and measuring only inside the quote put that child at indent 0,
    # where the same-indent pop took its parent out -- the named bullet
    # withheld and the subjectless one under it published. The whitespace
    # before the marker is what places the quote under its parent, so it
    # counts.
    return inner.strip(), outer + len(inner) - len(inner.lstrip()), quote.group(0).count(">")


def _close(open_heads: list[_Head], ends, *args) -> None:
    """Pop the innermost open headings for as long as `ends(heading, *args)`
    says the line being read closes them. The stack is outermost-first, so
    the first heading that survives keeps everything outside it open too."""
    while open_heads and ends(open_heads[-1], *args):
        open_heads.pop()


def _ends_at_quote(head: _Head, depth: int) -> bool:
    """Whether leaving a blockquote for nesting `depth` closes `head`: it was
    opened inside the quote being left. See `_entries`."""
    return head.depth > depth


def _ends_at_blank(head: _Head, indent: int, item: bool) -> bool:
    """Whether a blank line closes `head`, judged by the line after it at
    `indent` (`item`: that line is a list item). See `_entries`."""
    return (_outdented(head, indent)
            or (head.kind == "item" and indent <= head.rank)
            or (head.kind == "colon" and not (item and indent >= head.rank)))


def _ends_at_heading(head: _Head, indent: int, level: int) -> bool:
    """Whether a section heading of `level` at `indent` closes `head`: a
    section of its own level or deeper, and any colon heading or bullet not
    shallower than it. See `_entries`."""
    return (_outdented(head, indent)
            or (head.kind == "atx" and head.rank >= level)
            or (head.kind != "atx" and head.rank >= indent))


def _ends_at_item(head: _Head, indent: int) -> bool:
    """Whether a list item at `indent` closes `head`: a heading bullet at its
    indent or deeper, and a colon heading deeper than it. See `_entries`."""
    return (_outdented(head, indent)
            or (head.kind == "item" and head.rank >= indent)
            or (head.kind == "colon" and head.rank > indent))


def _governor(open_heads: list[_Head]) -> int:
    """The entry the innermost open heading heads, or -1 for none."""
    return open_heads[-1].entry if open_heads else -1


def _underline_level(out: list[list[str]], stripped: str, item: bool) -> int:
    """The heading level this line's SETEXT underline gives the line above
    it, or 0 when it is not one.

    It is the one heading form that is not recognizable from its own line: a
    setext underline retroactively makes the line ABOVE it a heading, so it is
    asked where that line's entry is still reachable rather than in
    `_heads_a_list`, which only ever sees one line at a time. A `-----` is not
    a list item -- `_LIST_MARKER` wants whitespace after the dash -- so no line
    read as an item is ever one.

    The non-blank check keeps a horizontal RULE from being read as one: a
    `---` after a blank line underlines nothing, and treating the blank as a
    heading would govern the list below it by an entry that names nobody.
    """
    if item or not out or not out[-1][-1].strip() or not _SETEXT.fullmatch(stripped):
        return 0
    return 1 if stripped[0] == "=" else 2


def _opened(open_heads: list[_Head], entry: int, stripped: str, indent: int,
            depth: int) -> _Head:
    """The heading this line opens over the entries below it, as `entry`.

    For a non-item, heading-ness belongs to the line just read, whichever
    entry it landed in: a `:` line arriving as a continuation still heads the
    bullets below it.
    """
    atx = _ATX.match(stripped)
    if atx:
        return _Head(entry, len(atx.group(1)), "atx", depth, _nested(open_heads, indent))
    if _LIST_MARKER.match(stripped):
        return _Head(entry, indent, "item", depth, indent)
    return _Head(entry, _governor_rank(open_heads, indent), "colon", depth, indent)


def _entries(suspects: str) -> list[tuple[list[str], int]]:
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
    where entry granularity is worth keeping. `visible` drops a
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
    # Open headings, outermost first (`_Head`).
    open_heads: list[_Head] = []
    depth = 0                               # blockquote nesting of the last line
    for line in suspects.splitlines():
        stripped, indent, quoted = _content(line)
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
        was, depth = depth, quoted
        if depth < was:
            _close(open_heads, _ends_at_quote, depth)
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
            _close(open_heads, _ends_at_blank, indent, item)
            pending_blank = False
        level = _underline_level(out, stripped, item)
        if level:
            # Same rule as the ATX branch below: a setext heading indented
            # inside a list item does not end that item.
            _close(open_heads, _ends_at_heading, indent, level)
            # The title's OWN governor is re-decided here, because it was
            # assigned one line too early: when it was read it looked like an
            # ordinary paragraph and inherited the section still open above it.
            # A heading is not governed by the section it closes -- without this
            # the second `Winifred` / `-------` block in a file would be
            # withheld along with the first.
            heads[len(out) - 1] = _governor(open_heads)
            open_heads.append(_Head(len(out) - 1, level, "atx", depth, _nested(open_heads, indent)))
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
            _close(open_heads, _ends_at_heading, indent, len(atx.group(1)))
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
            _close(open_heads, _ends_at_item, indent)
        if item or atx or fresh:
            out.append([line])
            heads.append(_governor(open_heads))
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
            open_heads.append(_opened(open_heads, len(out) - 1, stripped, indent, depth))
    return list(zip(out, heads, strict=True))


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

    No expansion is done here, and none is needed: `visible` only
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


def visible(suspects: str, others: set[str]) -> str:
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
    names and the given name inside each (`forms`), minus the owner's own
    forms. See `world_state._actor_aliases` for why one name each is not enough.

    An entry under a heading goes when the heading goes. Written in the order
    the entries come in, so it is transitive without a second pass: an entry's
    head always precedes it, and that head's verdict already includes its own.

    Not applied in `pcless` scenes — see `world_state._character_states`.
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
    for (lines, _), gone in zip(entries, dropped, strict=True):
        if not gone:
            kept.extend(lines)
    return "\n".join(kept).strip()
