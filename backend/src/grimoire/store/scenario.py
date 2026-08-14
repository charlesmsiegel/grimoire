"""Populate a world from a *scenario* card (#217).

Some SillyTavern-style cards are settings rather than people: one card whose
description/lorebook describe a whole place and cast, and whose greetings are
scene openers for it. `characters.import_card` lands such a card as ONE
character, which is the wrong shape — the cast, the places and the openers all
end up inside a single record's text.

This module turns one of those cards into the records a world is made of, in
two halves that meet at a review gate:

- `build_prompt` / `parse_output` — the extraction. One deterministic-primed
  LLM call proposes the cast (and re-files the card's world-info under better
  categories); the call itself lives in the route layer, as it does for
  `absorb`, `taglines` and every other prompt in the store.
- `proposal` — what the reviewer sees. The LLM's cast, merged with the parts
  that need no model at all: the card's own world-info entries (via
  `lorebook.from_character_book`) and its greetings (`first_mes` plus every
  `alternate_greeting`), each opener's cast resolved by `greetings.present_in`
  against the proposed names.
- `apply` — the write, of a proposal the user has already edited. Nothing here
  writes until `apply` runs; `proposal` and everything above it are pure.

Two deliberate choices worth stating:

**Names, not ids, inside a proposal.** A greeting's `character`/`present` name
cast members that do not exist yet, so the review payload cannot carry ids.
`apply` resolves names to ids once the characters are written, which is also
what lets a reviewer retype a name and have the opener follow it.

**Greeting bodies ride through the proposal verbatim.** They are what the user
edits, and `apply` localizes their art (`localize.localize_greeting`) after the
greeting exists — there is no greeting to store an image against before that.
The cost is that a card whose openers embed `data:` images sends those bytes to
the browser and back; the extraction prompt strips them (`strip_images`), so
only the review payload pays it. A card with URL-referenced art — the common
shape, since embedding a dozen images makes a card unusable elsewhere — pays
nothing.
"""

from __future__ import annotations

import re
from pathlib import Path

from .. import prompts
from . import characters, entities, greetings, localize, lorebook
from .absorb import parse as absorb_parse

#: The card fields the extraction reads, in the order the prompt lists them.
#: `first_mes`/`alternate_greetings` are deliberately absent: they reach the
#: prompt as openers, under their own heading, so the model can tell an opener
#: from the setting description.
PROMPT_FIELDS: tuple[tuple[str, str], ...] = (
    ("Description", "description"),
    ("Personality", "personality"),
    ("Scenario", "scenario"),
    ("Creator notes", "creator_notes"),
    ("Example dialogue", "mes_example"),
)

#: What a proposed character carries. Everything else a card can hold is left
#: for the user to fill in afterwards — a synthesized `mes_example` for a cast
#: member the scenario never quotes is invention with nothing behind it.
CHARACTER_FIELDS: tuple[str, ...] = ("name", "description", "personality")

#: How much of one world-info entry, and one opener, the prompt may spend.
#:
#: The cards this module exists for are the big ones — a whole setting, dozens
#: of entries, a dozen long openers — and their bodies are what makes them big.
#: Neither is needed whole: an entry is re-filed by NAME (its text is already
#: exact and is never retyped), and an opener is read for who appears in it,
#: which the opening lines say. The card's own description/scenario are NOT
#: clipped: those are the setting, and clipping them would gut the extraction.
#:
#: This bounds the prompt; it does not bound the import. `proposal` and `apply`
#: carry every body whole — see `_clip`.
ENTRY_PROMPT_CHARS = 600
GREETING_PROMPT_CHARS = 900


def _card_data(card: dict) -> dict:
    data = card.get("data")
    return data if isinstance(data, dict) else {}


def _text(v) -> str:
    return v.strip() if isinstance(v, str) else ""


def strip_images(text: str) -> str:
    """`text` with every image reference removed, for the extraction prompt.

    A scenario card's openers carry the art, and an embedded `data:` image is
    megabytes of base64 that says nothing about the cast. `localize.find_refs`
    already knows every shape a reference takes here (markdown, `<img>`, bare
    URL, data URI), so the spans it reports are the spans to drop — which keeps
    this in step with what `apply` will later localize, rather than being a
    second, weaker idea of what an image looks like.
    """
    if not text:
        return text
    for ref in sorted(localize.find_refs(text), key=lambda r: r.span[0], reverse=True):
        start, end = ref.span
        text = text[:start] + text[end:]
    return text


def _clip(text: str, limit: int) -> str:
    """`text` cut to `limit` characters, ending in an ellipsis when it was cut.

    The marker is the point: a model handed a body that stops mid-sentence with
    nothing to say so reads it as the whole entry, and may "complete" what it
    thinks is a truncated fact. Clipping only ever happens on the way INTO the
    prompt, so no import ever loses a character of the card.
    """
    return text if len(text) <= limit else text[:limit].rstrip() + " …"


def lorebook_entries(card: dict) -> list[dict]:
    """The card's own world-info, normalized into commit-ready entries.

    Straight through `lorebook.from_character_book`, so a scenario card's book
    imports exactly as it would through the standalone lorebook importer —
    including its skip rules for disabled and empty entries.
    """
    return lorebook.from_character_book(_card_data(card).get("character_book"))


def card_greetings(card: dict) -> list[dict]:
    """`[{"name","body"}]` for `first_mes` and every `alternate_greeting`.

    Named the way `greetings.import_from_character` names them, off the card's
    own name — for a scenario card that is the scenario's title, which makes
    "Saltmarch (alt 3)" a placeholder the reviewer renames rather than a claim
    about whose opener it is.
    """
    data = _card_data(card)
    title = _text(data.get("name")) or "Scenario"
    out: list[dict] = []
    first = data.get("first_mes")
    if isinstance(first, str) and first.strip():
        out.append({"name": title, "body": first})
    alts = data.get("alternate_greetings")
    for i, alt in enumerate(alts if isinstance(alts, list) else [], start=1):
        if isinstance(alt, str) and alt.strip():
            out.append({"name": f"{title} (alt {i})", "body": alt})
    return out


def prompt_entries(card: dict) -> list[dict]:
    """`lorebook_entries` with each body clipped — what the prompt is shown."""
    return [{**e, "body": _clip(e["body"], ENTRY_PROMPT_CHARS)} for e in lorebook_entries(card)]


def prompt_greetings(card: dict) -> list[dict]:
    """`card_greetings` with the art taken out and each body clipped.

    Both cuts are the prompt's alone. `_primary` reads the WHOLE opener when it
    decides whose it is, so nothing here narrows the cast an opener resolves to
    — the model is only being asked which people exist, not which are in this
    one.
    """
    return [{"name": g["name"], "body": _clip(strip_images(g["body"]), GREETING_PROMPT_CHARS)}
            for g in card_greetings(card)]


def build_prompt(card: dict) -> list[dict]:
    return [{"role": "system", "content": prompts.render("scenario/system.j2")},
            {"role": "user", "content": prompts.render(
                "scenario/user.j2", card=_card_data(card), fields=PROMPT_FIELDS,
                entries=prompt_entries(card), greetings=prompt_greetings(card))}]


def _keys(v) -> list[str]:
    """A `keys` value as a list, from either shape a model actually sends.

    A list of strings is the asked-for form; a comma-joined string is what
    arrives about as often, and it is the form the entities themselves store,
    so refusing it would throw away a usable answer.
    """
    if isinstance(v, str):
        return [k.strip() for k in v.split(",") if k.strip()]
    if isinstance(v, list):
        return [str(k).strip() for k in v if str(k).strip()]
    return []


def _category(v) -> str:
    c = v.strip().lower() if isinstance(v, str) else ""
    return c if c in entities.ENTITY_KINDS else "lore"


def parse_output(text: str) -> dict:
    """The model's reply as `{"characters": [...], "entries": [...]}`.

    Rebuilt key by key rather than passed through — the same contract
    `absorb.parse_output` keeps, and for the same reason: what this returns is
    what the reviewer sees and `apply` writes, so a model that invents a section
    or sends a scalar where a list belongs must not reach either. A row with no
    usable name is dropped; everything else is clamped to the fields above.
    """
    obj = absorb_parse.extract_object(text) or {}

    def rows(key: str) -> list:
        section = obj.get(key)
        return section if isinstance(section, list) else []

    chars: list[dict] = []
    for row in rows("characters"):
        if not isinstance(row, dict) or not _text(row.get("name")):
            continue
        chars.append({k: _text(row.get(k)) for k in CHARACTER_FIELDS})
    ents: list[dict] = []
    for row in rows("entries"):
        if not isinstance(row, dict) or not _text(row.get("name")):
            continue
        ents.append({"name": _text(row.get("name")), "keys": _keys(row.get("keys")),
                     "body": _text(row.get("body")), "category": _category(row.get("category"))})
    return {"characters": chars, "entries": ents}


def _norm(name: str) -> str:
    return " ".join(name.split()).casefold()


def merge_entries(book: list[dict], proposed: list[dict]) -> list[dict]:
    """The card's world-info, re-filed and extended by the model's entries.

    A proposed entry whose name matches one of the card's own takes over that
    entry's `category` and **nothing else** — re-filing "The Drowned Chapel"
    from lore to locations is the single most useful thing the extraction does
    with a lorebook, and it costs no body. A proposed entry with a body and no
    match is appended as a new entry; one with neither is dropped, since there
    is nothing to write.

    A match taking only the category is a rule, not a shortcut. The card's text
    is exact and the model's is a paraphrase of what it was shown — and what it
    was shown is CLIPPED (`ENTRY_PROMPT_CHARS`), so a body it offers for a
    listed entry is at best a retype and at worst a completion of a sentence
    the prompt cut off. Either way it can only lose detail the card already
    had, so it is never taken. Keys are the card's for the same reason.
    """
    out = [dict(e) for e in book]
    index = {_norm(e.get("name", "")): i for i, e in enumerate(out)}
    for e in proposed:
        i = index.get(_norm(e["name"]))
        if i is None:
            if e["body"]:
                out.append(dict(e))
                index[_norm(e["name"])] = len(out) - 1
            continue
        out[i]["category"] = e["category"]
    return out


def _primary(body: str, names: list[str]) -> str:
    """The proposed cast member whose name appears earliest in `body`, or "".

    "Earliest" rather than "first in the list": an opener that opens on Mara and
    mentions Winifred in its last line is Mara's, whatever order the extraction
    happened to propose the two in.
    """
    best, at = "", None
    for name in names:
        m = re.search(rf"\b{re.escape(name)}\b", body, re.IGNORECASE)
        if m is not None and (at is None or m.start() < at):
            best, at = name, m.start()
    return best


def resolve_cast(body: str, names: list[str]) -> tuple[str, list[str]]:
    """`(character, present)` for one opener, as cast NAMES.

    `greetings.present_in` does the work, called with a name-keyed roster
    (`{name: name}`) so it answers in the same currency the proposal speaks —
    the ids do not exist yet. Its ordering contract (source first, then by first
    appearance) is the contract here too.

    An opener naming nobody gets `("", [])` rather than `("", [...])`:
    `present_in` puts the source at the head of its answer, and a blank source
    would put a blank id in the greeting's `present` list.
    """
    primary = _primary(body, names)
    if not primary:
        return "", []
    return primary, greetings.present_in(body, primary, {n: n for n in names})


def proposal(card: dict, extracted: dict, existing: list[str] | tuple[str, ...] = ()) -> dict:
    """The review payload: what an import would create, before it creates it.

    Pure. `extracted` is `parse_output`'s result — pass `{"characters": [],
    "entries": []}` to see what the card alone yields, which is what a world
    with no LLM connection can still import.

    `existing` is the world's current character names, and each proposed row
    comes back with `exists`: whether `apply` will REUSE a character rather than
    create one. Advisory only — `apply` re-resolves against the world as it
    stands, since anything can happen between a review and an import — but
    without it, a world that already has a Mara absorbs this card's openers into
    her with nothing said, and renaming the row (the reviewer's way out) is a
    choice they never got offered. Matched with `_norm`, the same rule `apply`
    uses, rather than a second spelling of "the same name".

    Cast names are normalized here rather than trusted, even though
    `parse_output` already trims them: the two halves of a proposal reference
    each other BY NAME, so a cast row reading " Mara " beside an opener reading
    "Mara" is a review screen whose picker has no option for its own value, and
    an `apply` that resolves neither. One spelling, decided in one place.

    For the same reason a name may appear only once. A model asked for one
    entry per cast member does sometimes list one twice, and a proposal wired on
    names cannot represent two people who share one: `apply` would resolve both
    rows to a single character and report the second as a pre-existing one it
    reused. The first row wins, which keeps the fuller description — models put
    their best answer first and hedge on the repeat.
    """
    known = {_norm(n) for n in existing}
    chars, names = [], []
    for row in extracted.get("characters", []):
        name = _text(row.get("name"))
        if not name or _norm(name) in {_norm(n) for n in names}:
            continue
        chars.append({**dict(row), "name": name, "exists": _norm(name) in known})
        names.append(name)
    entries = merge_entries(lorebook_entries(card), extracted.get("entries", []))
    openers = []
    for g in card_greetings(card):
        character, present = resolve_cast(g["body"], names)
        openers.append({"name": g["name"], "body": g["body"],
                        "character": character, "present": present})
    return {"characters": chars, "entries": entries, "greetings": openers}


def _character_card(name: str, row: dict) -> dict:
    card = characters.blank_card(name)
    card["data"]["description"] = row.get("description", "")
    card["data"]["personality"] = row.get("personality", "")
    return card


def apply(root: Path, wid: str, prop: dict, *, art: bool = True, fetch=None) -> dict:
    """Write a reviewed proposal into the world at `root`.

    Order matters: characters first, because the openers reference them by name
    and a greeting's `character`/`present` are resolved against the roster as it
    stands *after* the cast lands. Entries go through `lorebook.commit`, which
    already drops exact duplicates, so re-importing the same card does not pile
    up slug-suffixed copies of its world-info.

    A proposed character whose name already exists in the world is **reused**,
    not duplicated — the same call the reviewer would make by hand, and what
    makes a second pass over a card that was half-imported land on the records
    the first pass created rather than beside them.

    `art` localizes each new greeting's images into that greeting's own asset
    store. It is best-effort per reference (`localize.localize_greeting`), and
    the totals are summed across the openers so the caller can say what landed.

    Raises `lorebook.LorebookError` for an entry filed under a category that is
    not an entity kind — checked up front, before the cast is written, so a
    hand-edited category cannot leave half an import behind.
    """
    entries = [dict(e) for e in prop.get("entries", [])]
    for e in entries:
        if e.get("category", "lore") not in entities.ENTITY_KINDS:
            raise lorebook.LorebookError(f"unknown category: {e.get('category')}")

    roster: dict[str, tuple[str, str]] = {}
    for meta in characters.list_characters(root):
        roster.setdefault(_norm(meta["name"]), (meta["id"], meta.get("default_version", "default")))

    made_chars = []
    for row in prop.get("characters", []):
        name = _text(row.get("name"))
        if not name:
            continue
        found = roster.get(_norm(name))
        if found is None:
            char_id, vid = characters.create_character(root, name, "default",
                                                       _character_card(name, row))
            roster[_norm(name)] = (char_id, vid)
            made_chars.append({"name": name, "id": char_id, "version": vid, "created": True})
        else:
            made_chars.append({"name": name, "id": found[0], "version": found[1], "created": False})

    created_entries = lorebook.commit(root, entries)

    made_greetings = []
    art_total = {"total": 0, "localized": 0, "skipped": 0, "failed": 0, "capped": False}
    for row in prop.get("greetings", []):
        body = row.get("body", "")
        if not isinstance(body, str) or not body.strip():
            continue
        # `or None`: a blank name is "this opener leads on nobody", which must
        # not be looked up — a world holding a character whose name is itself
        # blank would otherwise answer for every cast-less opener there is.
        primary = roster.get(_norm(_text(row.get("character")))) if row.get("character") else None
        # A name the world has no character for is dropped rather than written:
        # `present` holds ids, and a name that resolved to nothing would put a
        # dangling one in the greeting's frontmatter.
        present: list[str] = []
        for name in row.get("present") or []:
            found = roster.get(_norm(_text(name)))
            if found is not None and found[0] not in present:
                present.append(found[0])
        if primary is not None and primary[0] not in present:
            present.insert(0, primary[0])
        name = _text(row.get("name")) or "Opener"
        gid = greetings.create_greeting(
            root, name, primary[0] if primary else "", primary[1] if primary else "",
            body, present=present)
        made_greetings.append({"name": name, "id": gid})
        if art:
            got = localize.localize_greeting(root, gid, wid, fetch=fetch)
            for key in ("total", "localized", "skipped", "failed"):
                art_total[key] += got[key]
            art_total["capped"] = art_total["capped"] or got["capped"]

    return {"characters": made_chars, "entries": created_entries,
            "greetings": made_greetings, "art": art_total}
