"""characters: card fields responsibilities."""

from __future__ import annotations

import json

from .. import cards, lorebook


def _dumps(card: dict) -> str:
    return json.dumps(card, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def blank_card(name: str) -> dict:
    return {
        "spec": "chara_card_v3",
        "spec_version": "3.0",
        "data": {
            "name": name,
            "description": "",
            "personality": "",
            "scenario": "",
            "first_mes": "",
            "mes_example": "",
            "alternate_greetings": [],
            "tags": [],
            "extensions": {},
        },
    }


def _extensions(card: dict) -> dict:
    """A card's `extensions` object, or `{}` when it does not have a usable one.

    `cards.card_data`'s rule one level down: the store is user-editable JSON and
    promises no schema, so `extensions` is whatever a text editor left there. A
    string is truthy and has no `.get`, so `(data.get("extensions") or {})`
    hands back something that raises one attribute access later -- which took
    out the whole roster read, not just the odd card.
    """
    ext = cards.card_data(card).get("extensions")
    return ext if isinstance(ext, dict) else {}


def _version_label(card: dict, vid: str) -> str:
    """Display label for a version, worst case the id.

    Three sources, in order: an explicit `grimoire_label` (kept in extensions so
    it never leaks into `{{char}}`), the card spec's own `character_version`,
    then the version id.

    The card's NAME is deliberately not in that chain any more, though it used
    to be the fallback. It is the same string for every version of a character
    by construction -- so a character with three versions listed one name three
    times, and the only thing that told them apart, the slug, was the one thing
    never shown. An id at least differs per version.
    """
    label = _extensions(card).get("grimoire_label")
    if isinstance(label, str) and label.strip():
        return label.strip()
    spec_version = cards.card_data(card).get("character_version")
    if isinstance(spec_version, str) and spec_version.strip():
        return spec_version.strip()
    return vid


def _writable_extensions(card: dict) -> dict:
    """`_extensions`, but attached to the card so writes to it persist.

    A card whose `data` is unusable gets a throwaway: nothing can be stored on
    it, and the caller's write is dropped rather than raising.
    """
    data = cards.card_data(card)
    ext = data.get("extensions")
    if isinstance(ext, dict):
        return ext
    ext = {}
    data["extensions"] = ext          # a no-op on `card_data`'s throwaway `{}`
    return ext


#: What `create_character` calls a first version nobody named. Treated as "no
#: name given" rather than as a name, so importing a card that carries its own
#: `character_version` is labelled by it instead of by this placeholder.
DEFAULT_VERSION_NAME = "default"


def _apply_label(card: dict, version_name: str, vid: str) -> None:
    """Record what this version is called, unless the card already says it.

    Cleared first, then written only if it adds something: a version created
    FROM another's card inherits that card's label, so a clone of `young` named
    `older` has to lose the inherited one whether or not it gains a new one.

    What counts as adding something is asked of `_version_label` rather than
    guessed at, so the two cannot drift: `"after the flood"` keeps its spacing
    and case where the id would flatten it, and a card whose own
    `character_version` reads `v2` is labelled `second` if that is what the
    importer called it. What is NOT stored is a name the fallback chain already
    produces, nor `DEFAULT_VERSION_NAME`: that one is not a name a caller chose
    but the one `create_character` uses for a first version nobody named, so
    storing it would both be noise in every card file and mask the
    `character_version` an imported card brought with it.
    """
    extensions = _writable_extensions(card)
    extensions.pop("grimoire_label", None)
    label = (version_name or "").strip()
    if label and label != DEFAULT_VERSION_NAME and label != _version_label(card, vid):
        extensions["grimoire_label"] = label


def _version_chub_source(card: dict) -> str:
    return _extensions(card).get("chub_source", "")


def _importable_lore(card: dict) -> int:
    """How many embedded-lorebook entries the import would actually commit.

    Counted through the same rule the import route normalizes by, not off the
    raw `entries` list: normalization drops disabled and blank-content entries,
    so a UI counting the raw list offers to import entries that never arrive --
    and cards from ST routinely carry disabled ones (#16)."""
    return lorebook.importable_count(cards.card_data(card).get("character_book"))


def _carry_label(stored: dict, incoming: dict) -> None:
    """Move a version's stored label onto a card that is about to replace it."""
    label = _extensions(stored).get("grimoire_label")
    if not (isinstance(label, str) and label.strip()):
        return
    _writable_extensions(incoming)["grimoire_label"] = label
