"""Read a 008 PC intent as a message, or refuse. Deterministic and English-only.

The reader looks at the routed ``Intent`` only. It never sees the utterance's origin, so a
microphone never makes words a broadcast, and it refuses every non-PC lane, so a UI command
or a director instruction is never dialogue.

The asymmetry is the point: a misread private line that becomes speech leaks to the whole room
and cannot be taken back, while a refusal costs the player a rephrase. So:

- **Speech comes only from 008's speech lane** (``pc_speech``): the PC's spoken words, said to
  the room. Words wholly in double quotes are taken as they are, unless they hold a stage
  direction (a bracket or asterisk). Single quotes are also apostrophes, so single-quoted words
  get every unquoted rule. Unquoted words
  are refused when they show narration (``mixed_narration``): a quote or guillemet mixed with
  other text, a bracket, asterisk, colon, arrow, ``@`` or leading ``/`` ("(low) Emma, run",
  "/w Emma …"), a speech tag ("Run, I tell Emma."), or a known name anywhere but as a vocative
  ("Ines slips Emma a note", "Run, I think at Emma"; "Emma, run" is fine). A privacy marker is
  ``unresolved_private``: broad word families such as whisper, telepathy, any word holding
  "hear" or "voice", ear, low, quiet, breath, psst, between us, "Emma only", our link or DM. The
  marker list is a list: other narration typed in the speech lane is spoken words, as 008 decided.
- **An action is read only as a private message**, in one first-person form::

      I [telepathically|mentally] (whisper to|tell|say to) <name>[,|:]
        [telepathically|mentally][,|:] (that <content> | "<content>" | <content>)

  ``whisper to`` makes a whisper and a manner word telepathy (both: ``conflicting_channel``);
  a bare "whisper" names no listener ("I whisper the guard is asleep"). A
  told or said action with no manner word is ``indirect_speech``: an action is never read as
  speech the room hears, however plain ("I tell Emma that dinner is ready"); the player says
  it in quotes instead. Bare ``<content>`` must start like words, not like manner ("in a low
  voice …", "silently …", "to run"; ``unclear_content``), and unquoted content is one plain
  clause: one sentence, with no comma, semicolon, colon, dash, bracket, asterisk, "then",
  privacy marker, third-person pronoun ("that I love her") or second first-person clause ("…, then shout it to the guard"; ``unclear_content``). Anything
  more goes in quotes.
- The reader takes one addressee, and never guesses where a name ends. Words after the name
  need a ``,``, ``:``, manner word, ``that`` or a quote first; otherwise the name may go on
  ("the first guard captain" when only "the first guard" is known) and the line is
  ``unknown_addressee``. So is a name that another capitalised word continues ("Emma Rose"),
  and "Emma and the guard" is ``ambiguous_addressee``. A message to several actors is built as
  a ``Message`` directly.

An ``Unread`` transmits nothing. A caller must never fall back to speech on an ``Unread``.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass

from ..core import InstanceRef
from ..core.identity import validate_segment
from ..evidence import LABEL_LIMIT, TEXT_LIMIT
from ..intent import Intent
from ..intent.text import split_sentences, straighten, wrapped
from .model import MESSAGE_ID_MAX, Message, _fail

UNREAD_REASONS = frozenset({
    "not_dialogue", "wrong_speaker", "not_directed", "indirect_speech", "unresolved_private",
    "mixed_narration", "unknown_addressee", "ambiguous_addressee", "conflicting_channel",
    "unclear_content", "no_content",
})

_SPACE = re.compile(r"\s+")
#: Words that mark a line as meant for fewer ears than the room's. A list, not understanding.
_PRIVATE = re.compile(
    r"(?<![^\W_])(?:whisper\w*|telepath\w*|psion\w*|psychic\w*|mentally|mind[- ]?(?:speak\w*|speech|link\w*|to[- ]mind|message\w*)|"
    r"eyes?|just (?:us|you and me|between)|only you|you only|for you|sub rosa|entre nous|thought\w*|"
    r"between (?:you and i|me and you)|(?:don't|do not|never|can't|cannot|won't|mustn't|shouldn't) "
    r"(?:\w+ )?(?:tell|mention|say|repeat|share)|just for us|conspirator\w*|surreptitious\w*|faint\w*|"
    r"step\w* away|out of (?:[\w']+ ){0,2}sight|only to (?:him|her|them)|tell no ?one|not a word|confidential\w*|under wraps|"
    r"mum's the word|not so loud|come closer|lean\w* toward\w*|"
    r"notes?|letter\w*|written|writ(?:e|es|ing)|scribbl\w*|signal\w*|gestur\w*|wink\w*|nod(?:s|ded|ding)?|"
    r"saying|meaning|reads?|reading|"
    r"priva\w*|secre\w*|discre\w*|clandestin\w*|stealth\w*|sneak\w*|hidden|intimate\w*|silen\w*|mut(?:e|ed|ing)|mics?|code\w*|encrypt\w*|cipher\w*|privy|personal\w*|exclusive\w*|incognito|undercover|unseen|unnoticed|one[- ]on[- ]one|t[e\u00ea]te|sending|spells?|magic\w*|dream\w*|sign(?:s|ed|ing)?|password\w*|solitude|seclu\w*|isolat\w*|sly|radar|sidelong|sideways|ixnay|(?:just|only|but|except) you|you alone|closed doors|[^\W_]*audibl[^\W_]*|sh+h|covert\w*|furtive\w*|hiss\w*|off the record|to (?:your|my|our)sel(?:f|ves)|"
    r"comms?|radio\w*|earpiece\w*|channel\w*|sign language|lean\w* (?:over|in|close)|close to|"
    r"(?:draw|pull)\w* (?:[\w']+ ){0,2}close|project\w* (?:[\w']+ )?(?:thoughts?|mind)|^(?:message|text)\w*(?= )|"
    r"with (?:[\w']+ )?minds?|silent\w*|"
    r"s*hush\w*|s+h+|ps+t\w*|keep (?:it|this|that|your voice) (?:down|quiet|from|under)|"
    r"(?:no ?one|nobody|not a soul)(?: [\w']+){0,3} know|(?:don't|do not) (?:let|say|repeat|share|react|breathe)|"
    r"keep (?:it|this|that) from|under your hat|on the (?:qt|q\.t\.|dl|down ?low)|lips are sealed|"
    r"behind (?:my|your|his|her|their) hand|(?:my|his|her|their) back to|turn\w* away|act natural|a word\?|"
    r"sotto|sub voce|keep (?:mum|schtum|shtum)|discretion|hid(?:e|ing) (?:this|it|that)|cupp\w*|sidl\w*|"
    r"edg\w* (?:near|close)\w*|(?:can't|cannot|mustn't|must not|shouldn't|won't) (?:[\w']+ )?(?:know|learn|find out)|"
    r"find out|notic\w*|catch (?:this|that|it)|miss (?:this|that|it)|out of (?:[\w']+ ){0,2}range|links?|bond|confid\w*|mumbl\w*|undertone\w*|earshot|sotto[- ]voce|breath\w*|[^\W_]*voice[^\W_]*|tone|low(?:er\w*|ing)?|quiet\w*|soft\w*|"
    r"[^\W_]*hear[^\W_]*|ears?|alone|no ?one else|nobody else|"
    r"voice down|quietly|softly|privately|secretly|discreetly|confidentially|in confidence|aside|"
    r"murmur\w*|mutter\w*|mouth(?:s|ed|ing)?|only (?:\w+ ){1,2}(?:can|could) hear|"
    r"(?:in|into|inside|for|to|through) (?:[\w']+ )?(?:heads?|ears?|minds?|thoughts?)|"
    r"lean(?:s|ing)? (?:in|close)|psst\w*|between (?:us|ourselves|you and me|the two of us)|"
    r"(?:our|the|a|my|your|this|that|their|his|her) (?:\w+ )?(?:link|bond)|"
    r"dm|pm|msg|direct message|private message|private chat)(?![a-z])",
    re.IGNORECASE)
_MANNER = r"(?:telepathically|mentally)(?![a-z])"
#: "whisper to X", never "whisper X": "I whisper the guard is asleep" names no listener
_LEAD = re.compile(rf"i (?P<manner>{_MANNER} )?(?P<verb>whisper to|tell|say to) ", re.IGNORECASE)
_AFTER_NAME = re.compile(rf"\s*[,:]?\s*(?:(?P<manner>{_MANNER})\s*[,:]?\s*)?(?P<that>that(?:\s+|$))?",
                         re.IGNORECASE)
#: a second addressee joined to the first
_JOINED = re.compile(r"(?:and|or|&|plus|then|both|as well as|along with|together with|with)(?![a-z])",
                     re.IGNORECASE)
#: bare content that starts like manner, an instruction or a second clause, not like the words said
_MANNER_START = re.compile(
    r"(?:\w+ly|in|into|with|through|under|out|so|via|using|by|to|from|across|over|inside|while|as|"
    r"without|mind|aloud)(?![a-z'])", re.IGNORECASE)
#: a name ends where a word does: "Emma)", "Emma—", "Emma's" all end "Emma"
_NAME_END = re.compile(r"(?!\w)")
_FIRST_WORD = re.compile(r"\w+")
_WORD_START = re.compile(r"(?<!\w)\w")
#: where a vocative name may stand: at the start or after a clause mark, and before one or the end
#: a trailing tag with any verb: "Run, Emma, I mumble.", "…, I confide."
_TRAILING_TAG = re.compile(r"[,.!?;]\s*(?:i|he|she|they|we|my (?:character|pc))(?: \w+){1,3}[.!?]*$", re.IGNORECASE)
_ONLY = re.compile(r"(?:'s?)? only(?!\w)", re.IGNORECASE)
_VOCATIVE_AFTER = re.compile(r"\s*(?:$|[,.!?])")
#: a single-quoted span closed by punctuation, then more text: 'Run,' I tell Emma
_SINGLE_QUOTED = re.compile(r"(?:^|\s)'[^']*[,.!?;:]'(?=\s|$)")
#: stage directions inside quoted words: "(whispers) Emma, …", "*leans close* …"
_QUOTED_DIRECTION = re.compile(r"\"|(?<![^\W\d_])'|'(?![^\W\d_])|[()\[\]{}*@_~<>\u00ab\u00bb\u2039\u203a\u300c\u300d\u300e\u300f]|>>|->|=>")
#: stage directions in a speech line: (low), [to Emma], *leans over*
_DIRECTION = re.compile(r"[()\[\]{}*@_~<>\u00ab\u00bb\u2039\u203a\u201e\u300c\u300d\u300e\u300f]|^/|:(?!\d)|>>|->|=>")
#: a speech tag in the line: "Run, I tell Emma.", "Run, Emma! I yell."
_SPEECH_VERB = (r"(?:\w+ly )?(?:tell|tells|told|say|says|said|yell|yells|shout|shouts|ask|asks|reply|replies|"
                r"answer|answers|call|calls|cry|cries|add|adds|hiss|hisses|snap|snaps|warn|warns|announce|"
                r"announces|declare|declares|exclaim|exclaims|insist|insists|explain|explains|continue|"
                r"continues|repeat|repeats|urge|urges|plead|pleads|beg|begs|order|orders|sign|signs|hint|hints|"
                r"breathe|breathes|note|notes|state|states|remark|remarks|whisper|whispers)(?![a-z])")
#: what unquoted words of a private message may not hold: more punctuation than one plain clause
_NOT_PLAIN = re.compile(r":(?!\d)|[\",;()\[\]{}*@_~<>|/\\+&=^%#\u2013\u2014\u2026\u00ab\u00bb\u2039\u203a\u201e]|\s-+\s|--|\.{2,}|(?<![a-z])then(?![a-z])",
                        re.IGNORECASE)
#: a second first-person clause after the words: "…, then I shout", "… and I wink", "…; I leave"
_NARRATION_CLAUSE = re.compile(
    r"(?:[;:\u2013\u2014.!?&+]|(?<![a-z])(?:then|and|but|while|before|after|as|so|plus))\s*i(?![\w'])", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Unread:
    """Why an intent is not a message. Nothing was transmitted."""

    reason: str

    def __post_init__(self) -> None:
        if self.reason not in UNREAD_REASONS:
            raise ValueError("unknown unread reason")


def _fold(text: str) -> str:
    return _SPACE.sub(" ", straighten(text)).strip()


def _probe(text: str) -> str:
    """``text`` as the checks should see it: compatibility forms folded (full-width brackets and
    letters to ASCII) and invisible format characters (soft hyphen, zero-width joiners) dropped."""
    visible = "".join(char for char in unicodedata.normalize("NFKC", text)
                      if unicodedata.category(char) != "Cf" and not _ignorable(char))
    return _fold(visible)


#: Unicode's Default_Ignorable_Code_Point ranges: drawn as nothing, so they may split a word
_IGNORABLE = ((0x00AD, 0x00AD), (0x034F, 0x034F), (0x061C, 0x061C), (0x115F, 0x1160), (0x17B4, 0x17B5),
              (0x180B, 0x180F), (0x200B, 0x200F), (0x202A, 0x202E), (0x2060, 0x206F), (0x3164, 0x3164),
              (0xFE00, 0xFE0F), (0xFEFF, 0xFEFF), (0xFFA0, 0xFFA0), (0xFFF0, 0xFFF8), (0x1BCA0, 0x1BCA3),
              (0x1D173, 0x1D17A), (0xE0000, 0xE0FFF))


def _ignorable(char: str) -> bool:
    code = ord(char)
    return any(low <= code <= high for low, high in _IGNORABLE)


_STARS = frozenset("\u2217\u204e\u2051\u2731\u2732\u2733\u274b\ufe61\uff0a")


def _bracketed(text: str) -> bool:
    """A bracket or quote mark of any script (Unicode Ps, Pe, Pi, Pf), an asterisk look-alike, or a
    pictograph such as an emoji (So), which players use as a stage direction ("🤫")."""
    return any(unicodedata.category(char) in ("Ps", "Pe", "Pi", "Pf", "So") or char in _STARS for char in text)


def _names(names: object, campaign_id: str) -> dict[str, set[InstanceRef]]:
    """Folded name -> the actors it may mean."""
    if not isinstance(names, Mapping):
        _fail("invalid_record")
    folded: dict[str, set[InstanceRef]] = {}
    for name, ref in names.items():
        if type(name) is not str or type(ref) is not InstanceRef or not _fold(name) or len(name) > LABEL_LIMIT:
            _fail("invalid_record")
        if ref.campaign_id != campaign_id:
            _fail("campaign_mismatch")
        folded.setdefault(_fold(name), set()).add(ref)
    return folded


def _name_end(text: str, start: int, name: str) -> int | None:
    """Where ``name`` (any case) ends if it stands at ``start`` and ends at a word boundary ("Emma's"
    too). Case folding may change length ("Straße" and "STRASSE"), so it is compared fold by fold."""
    wanted = name.casefold()
    seen = ""
    at = start
    while len(seen) < len(wanted) and at < len(text):
        seen += text[at].casefold()
        at += 1
    return at if seen == wanted and _NAME_END.match(text, at) is not None else None


def _name_at(text: str, start: int, name: str) -> bool:
    return _name_end(text, start, name) is not None


def _caseless(text: str) -> tuple[str, list[int]]:
    """``text`` case-folded one character at a time, and the typed index of every folded position
    (plus one entry for the end), so a match in the fold maps back to the typed text."""
    pieces: list[str] = []
    origin: list[int] = []
    for at, char in enumerate(text):
        piece = char.casefold()
        pieces.append(piece)
        origin += [at] * len(piece)
    origin.append(len(text))
    return "".join(pieces), origin


def _names_a_known_actor(text: str, start: int, names: dict[str, set[InstanceRef]]) -> bool:
    return any(_name_at(text, start, name) for name in names)


def _name_spans(text: str, names: dict[str, set[InstanceRef]]) -> list[tuple[int, int]]:
    """Every place a known name stands in ``text``, the longest at each word start.

    One pass over the word starts, trying each distinct name length once, however many names share
    a first word.
    """
    folded, origin = _caseless(text)
    position = {typed: at for at, typed in reversed(list(enumerate(origin)))}

    def whole(at: int, end: int) -> bool:  # starts and ends between typed characters
        return (at == 0 or origin[at] != origin[at - 1]) and (end == len(folded) or origin[end] != origin[end - 1])

    by_length: dict[int, set[str]] = {}
    odd = []
    for name in names:
        if _FIRST_WORD.match(name) is None:
            odd.append(name.casefold())
        else:
            by_length.setdefault(len(name.casefold()), set()).add(name.casefold())
    lengths = sorted(by_length, reverse=True)
    spans = []
    for word in _WORD_START.finditer(text):
        at = position[word.start()]
        for length in lengths:
            end = at + length
            if end <= len(folded) and whole(at, end) and folded[at:end] in by_length[length] \
                    and _NAME_END.match(text, origin[end]):
                spans.append((word.start(), origin[end]))
                break
    for name in odd:
        at = folded.find(name)
        while at >= 0:
            end = at + len(name)
            if whole(at, end) and _NAME_END.match(text, origin[end]):
                spans.append((origin[at], origin[end]))
            at = folded.find(name, at + 1)
    return spans


def _vocative(text: str, start: int, end: int) -> bool:
    """Whether the name at ``start:end`` stands alone as a vocative: "Emma, run", "Run, Emma!"."""
    before = start - 1
    while before >= 0 and text[before].isspace():
        before -= 1
    return (before < 0 or text[before] in ",.!?;") and _VOCATIVE_AFTER.match(text, end) is not None


def _addressee(text: str, start: int, names: dict[str, set[InstanceRef]]) -> tuple[InstanceRef, int] | Unread:
    """The one actor a name at ``start`` means, and where the name ends."""
    matches = [(name, refs) for name, refs in names.items() if _name_at(text, start, name)]
    meant = {ref for _, refs in matches for ref in refs}
    if not meant:
        return Unread("unknown_addressee")
    if len(meant) > 1:  # "Emma" and "Emma Rose" both fit "Emma Rose …": never guess who hears it
        return Unread("ambiguous_addressee")
    end = max(_name_end(text, start, name) or start for name, _ in matches)
    following = re.match(r" ([A-Z]\w*)", text[end:])
    if following and following.group(1).lower() not in ("that", "telepathically", "mentally", "i"):
        return Unread("unknown_addressee")  # "Emma Rose" when only Emma is known: not Emma
    return meant.pop(), end


def _quoted(text: str) -> str | None:
    """The words of a wholly quoted span, else None."""
    return wrapped(text, '"', '"')


def _private_message(text: str, names: dict[str, set[InstanceRef]]) -> tuple[str, InstanceRef, str] | Unread:
    """(channel, addressee, content) of the first-person private form."""
    lead = _LEAD.match(text)
    if lead is None:
        return Unread("unresolved_private" if _PRIVATE.search(text) else "not_directed")
    found = _addressee(text, lead.end(), names)
    if isinstance(found, Unread):
        return found
    addressee, end = found
    after = _AFTER_NAME.match(text, end)
    assert after is not None  # every part of it is optional
    whisper = lead.group("verb").lower().startswith("whisper")
    telepathy = bool(lead.group("manner") or after.group("manner"))
    if whisper and telepathy:
        return Unread("conflicting_channel")
    if not (whisper or telepathy):
        return Unread("indirect_speech")
    if not after.group("that") and _JOINED.match(text, after.end()):
        return Unread("ambiguous_addressee")  # "Emma and the guard …": one addressee only
    rest = text[after.end():].strip()
    if rest and not text[end:after.end()].strip() and not rest.startswith('"'):
        # no ",", ":", manner word or "that" between the name and more words: the name may go on
        # ("the first guard captain …") or manner may follow ("Emma again …"); never guess where
        return Unread("unclear_content" if not rest[0].isalnum() or _MANNER_START.match(rest)
                      else "unknown_addressee")
    if rest.startswith('"'):
        content = _quoted(rest)
        if content is None:  # a quote with more narration after it
            return Unread("unclear_content")
        content = content.strip()
        if _QUOTED_DIRECTION.search(content) or _bracketed(content):
            return Unread("unclear_content")  # "*winks* the guards …": a direction, not words
    else:
        content = rest
        if content and (not content[0].isalnum()
                        or (not after.group("that") and (_MANNER_START.match(content) or ", that " in content))):
            return Unread("unclear_content")
        if content and not after.group("that") and _names_a_known_actor(content, 0, names):
            return Unread("ambiguous_addressee")  # "Emma, Spy and I must run": a second listener?
        if len(split_sentences(content)) > 1 or _NARRATION_CLAUSE.search(content) or _NOT_PLAIN.search(content) or _bracketed(content):
            return Unread("unclear_content")  # "…, then shout it to the guard", "run *wink*": narration
        if _PRIVATE.search(content) or re.search(r"(?<!\w)(?:without|while)(?!\w)", content, re.IGNORECASE):
            return Unread("unclear_content")  # "… so the guards don't hear", "… without looking": manner
        if re.search(r"(?<!\w)(?:he|him|his|she|her|hers|they|them|their|theirs|himself|herself|themselves)(?!\w)",
                     content, re.IGNORECASE):
            return Unread("unclear_content")  # "that I love her": reported words, not the words said
        if content and _name_spans(content, names):
            # "Emma, the first guard, …", "… and to Emma that we leave": a known name in unquoted words
            # may be a second listener, so the words may be someone else's; quote them to say them
            return Unread("ambiguous_addressee")
        if re.search(r"(?<!\w)that(?!\w)|(?<!\w)(?:and|&|plus|then|but|also|or|as well as|nor|yet|so|while|whilst|whereas|though|although|however|still|besides|meanwhile|otherwise|except|only|instead|whereupon|after|before|later|next) (?:also )?(?:to|tell|tells|say|says)(?!\w)",
                     content, re.IGNORECASE):
            return Unread("unclear_content")  # "we run and to the captain that we stay": a second message
    if not content:
        return Unread("no_content")
    return ("whisper" if whisper else "telepathy"), addressee, content


#: a short aside set off by punctuation that says how or where, not what: "Emma, in silence, …",
#: "Emma, magically, …". Its words vary without end, so its shape is refused, not its vocabulary.
_ASIDE = re.compile(
    r"^(?:(?:in|on|under|by|with|via|behind|off|out|beyond|without|through|thru|between|for|to|from|over|inside|"
    r"within|across|using|among|amid|away|apart|aside)(?![\w'])(?:\W+\w+){0,3}|[^\W\d_]+ly)$", re.IGNORECASE)


def _aside(text: str) -> bool:
    pieces = [piece.strip() for piece in re.split(r"[,;.!?]+", text)]
    return len([piece for piece in pieces if piece]) > 1 and any(_ASIDE.match(piece) for piece in pieces)


def _spoken(text: str, quoted_by_router: bool, names: dict[str, set[InstanceRef]]) -> str | Unread:
    """The words of a ``pc_speech`` line, said to the room."""
    subject = r"(?:i|he|she|they|we|my (?:character|pc)|the (?:character|pc))"
    verb = _SPEECH_VERB.removeprefix("(?:\\w+ly )?")
    tag = re.compile(rf"(?<!\w)(?:{subject} {_SPEECH_VERB}|{verb} {subject}(?!\w))", re.IGNORECASE)
    quoted = _quoted(text) if quoted_by_router else None  # a re-entrant voice candidate keeps them
    if quoted_by_router or quoted is not None:  # 008 read a wholly quoted line: literal words
        words = (quoted if quoted is not None else text).strip()
    else:
        single = wrapped(text, "'", "'")  # single quotes are also apostrophes ("'Cause … listenin'"),
        if single is not None:  # so their words get every unquoted rule below
            text = single.strip()
        words = wrapped(text, '"', '"')
    if words is not None:  # quoted words are spoken aloud, privacy words and all; directions are not
        if _QUOTED_DIRECTION.search(words) or _bracketed(words):
            return Unread("mixed_narration")
        return words.strip() or Unread("no_content")
    spans = _name_spans(text, names)
    spelled = re.sub(r"\s*[&+]\s*", " and ", text)  # "between you & me" is "between you and me"
    if _PRIVATE.search(spelled) or any(_ONLY.match(text, end) for _, end in spans) or _aside(text):
        return Unread("unresolved_private")  # "… under my breath", "Emma only, …"
    if '"' in text or _SINGLE_QUOTED.search(text) or _DIRECTION.search(text) or _bracketed(text):
        return Unread("mixed_narration")  # '"Run," I tell Emma', "(low) Emma, run": words and narration
    if tag.search(text) or _TRAILING_TAG.search(text):
        return Unread("mixed_narration")  # "Run, I tell Emma.": a speech tag is narration
    if not all(_vocative(text, start, end) for start, end in spans):  # "Emma, run", "Run, Emma!" only
        return Unread("mixed_narration")  # "Ines slips Emma a note …", "Run, I think at Emma"
    return text or Unread("no_content")


def read_message(intent: Intent, sender: InstanceRef, names: Mapping[str, InstanceRef], *,
                 message_id: str) -> Message | Unread:
    """Read a routed PC intent as ``sender``'s message, or say why not.

    ``names`` maps the display names a player may use to the actors they mean (any case; one
    name may mean several actors, which is then ambiguous). Presence is not checked here:
    ``transmit`` refuses an absent addressee. Malformed arguments raise ``TransmissionError``
    (``invalid_record``, or ``campaign_mismatch`` for a named actor of another campaign); an intent
    text over 20 000 characters raises ``too_long`` before any parsing.
    """
    if type(intent) is not Intent or type(sender) is not InstanceRef:
        _fail("invalid_record")
    try:
        validate_segment(message_id, "message_id")
        valid_id = len(message_id) <= MESSAGE_ID_MAX
    except ValueError:
        valid_id = False
    if not valid_id:
        _fail("invalid_record")
    folded = _names(names, sender.campaign_id)
    if type(intent.text) is str and len(intent.text) > TEXT_LIMIT:
        _fail("too_long")  # before any parsing, so a long paste costs nothing
    if intent.kind not in ("pc_speech", "pc_action"):
        return Unread("not_dialogue")
    if intent.actor_id != sender.instance_id:
        return Unread("wrong_speaker")
    for name, refs in list(folded.items()):  # "E\u0301mma" is also "Émma": compose and fold names too
        probed = _probe(name)
        if probed and probed != name:
            folded.setdefault(probed, set()).update(refs)
    text = _fold(intent.text)
    read = _read(text, intent, folded)
    probe = _probe(intent.text)
    if probe != text and not isinstance(read, Unread):
        # "（whispers）", "Ｗｈｉｓｐｅｒｉｎｇ", "Whis\u00adpering": the checks must hold on the folded
        # form too, and it must read the same way; the words kept are the ones typed
        shadow = _read(probe, intent, folded)  # only compared: its words may outgrow the limit
        if isinstance(shadow, Unread):
            return shadow
        if shadow[:2] != read[:2]:
            return Unread("unclear_content")
    if isinstance(read, Unread):
        return read
    channel, addressees, words = read
    return Message(message_id, sender, channel, words, addressees)


def _read(text: str, intent: Intent,
          folded: dict[str, set[InstanceRef]]) -> tuple[str, tuple[InstanceRef, ...], str] | Unread:
    """(channel, addressees, words) of one form of the text, or why not."""
    if intent.kind == "pc_speech":
        spoken = _spoken(text, intent.reason == "quoted", folded)
        return spoken if isinstance(spoken, Unread) else ("speech", (), spoken)
    parsed = _private_message(text, folded)
    if isinstance(parsed, Unread):
        return parsed
    channel, addressee, content = parsed
    return channel, (addressee,), content


__all__ = ["UNREAD_REASONS", "Unread", "read_message"]
