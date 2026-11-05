"""Read a decoded card document into typed values (Sprint 037). Pure: the bytes are decoded elsewhere.

``parse_card(document, as_scenario=False)`` accepts a Character Card V3 object, a V3 standalone
lorebook (``lorebook_v3``) or a Grimoire scenario document (``grimoire.scenario-card/1``). It is
lenient where a card is merely messy and strict where identity or meaning would be guessed:

- a known field of the wrong type is left out and reported as a ``Loss`` (it survives in the
  preserved original bytes), never coerced;
- unknown fields are kept verbatim in ``extras``;
- a missing or blank name or title, or a scenario with neither premise nor opening, is
  ``CardError("invalid_card")``;
- every other format, including Character Card V2, is ``CardError("unsupported_format")``: this
  slice never claims compatibility it does not have.

Error messages and losses name field paths only, never card text.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import MappingProxyType

from .model import (
    CCV3, LOREBOOK_V3, MAX_KEPT_DEPTH, MAX_NAME, SCENARIO_DOC, Asset, Authorship, CardError, CastRef,
    CharacterCard, Greeting, LoreEntry, Lorebook, LorebookCard, Loss, ParsedCard, ScenarioCard, key_path,
)

_V3_DATA = frozenset({
    "name", "description", "tags", "creator", "character_version", "mes_example", "extensions",
    "system_prompt", "post_history_instructions", "first_mes", "alternate_greetings", "personality",
    "scenario", "creator_notes", "character_book", "assets", "nickname", "source",
    "group_only_greetings", "creation_date", "modification_date",
})
_BOOK = frozenset({"name", "description", "scan_depth", "token_budget", "recursive_scanning", "extensions",
                   "entries"})
_ENTRY = frozenset({"keys", "content", "extensions", "enabled", "insertion_order", "case_sensitive", "use_regex",
                    "constant", "name", "priority", "id", "comment", "selective", "secondary_keys", "position"})
#: entry settings and the JSON types each may hold (``number`` excludes bool)
_ENTRY_SETTINGS = {"constant": "bool", "selective": "bool", "insertion_order": "number", "priority": "number",
                   "case_sensitive": "bool?", "use_regex": "bool", "position": "text", "id": "id", "name": "text",
                   "comment": "text"}
_BOOK_SETTINGS = {"scan_depth": "number", "token_budget": "number", "recursive_scanning": "bool"}
_SCENARIO = frozenset({"format", "title", "premise", "opening", "openings", "instructions", "setting", "lorebook",
                       "cast", "tags", "creator", "creator_notes", "extensions"})


def _join(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


class _Reader:
    """Collects losses while reading one document."""

    def __init__(self) -> None:
        self.losses: list[Loss] = []

    def lose(self, path: str, reason: str) -> None:
        self.losses.append(Loss(path, reason))

    def text(self, obj: Mapping, key: str, path: str) -> str:
        value = obj.get(key)
        if value is None:
            return ""
        if type(value) is not str:
            self.lose(_join(path, key), "wrong_type")
            return ""
        return value

    def optional_text(self, obj: Mapping, key: str, path: str) -> str | None:
        value = obj.get(key)
        if value is None:
            return None
        if type(value) is not str:
            self.lose(_join(path, key), "wrong_type")
            return None
        return value

    def texts(self, obj: Mapping, key: str, path: str) -> tuple[str, ...]:
        value = obj.get(key)
        if value is None:
            return ()
        if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
            self.lose(_join(path, key), "wrong_type")
            return ()
        kept = []
        for index, item in enumerate(value):
            if type(item) is str:
                kept.append(item)
            else:
                self.lose(f"{_join(path, key)}[{index}]", "wrong_type")
        return tuple(kept)

    def entries(self, obj: Mapping, key: str, path: str) -> list[tuple[int, str]]:
        """``(index, text)`` for each text of a list, indexed by position in the raw list."""
        value = obj.get(key)
        if value is None:
            return []
        if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
            self.lose(_join(path, key), "wrong_type")
            return []
        kept = []
        for index, item in enumerate(value):
            if type(item) is str:
                kept.append((index, item))
            else:
                self.lose(f"{_join(path, key)}[{index}]", "wrong_type")
        return kept

    def number(self, obj: Mapping, key: str, path: str) -> int | float | None:
        value = obj.get(key)
        if value is None:
            return None
        if type(value) is not int and type(value) is not float:
            self.lose(_join(path, key), "wrong_type")
            return None
        return value

    def integer(self, obj: Mapping, key: str, path: str) -> int | None:
        value = obj.get(key)
        if value is None:
            return None
        if type(value) is not int:
            self.lose(_join(path, key), "wrong_type")
            return None
        return value

    def kept(self, value: object, path: str) -> object:
        """``value`` as plain JSON to keep verbatim, or ``None`` (reported) if it nests too deeply."""
        if _depth(value) > MAX_KEPT_DEPTH:
            self.lose(path, "too_deep")
            return None
        return _plain(value, path, self)

    def mapping(self, obj: Mapping, key: str, path: str) -> Mapping[str, object]:
        value = obj.get(key)
        if value is None:
            return MappingProxyType({})
        if not isinstance(value, Mapping):
            self.lose(_join(path, key), "wrong_type")
            return MappingProxyType({})
        # depth is checked per child, so one over-deep value never drops its siblings
        return MappingProxyType(self.unknown(value, frozenset(), _join(path, key)))

    def unknown(self, obj: Mapping, known: frozenset[str], path: str) -> dict[str, object]:
        extras = {}
        positions = {key: index for index, key in enumerate(obj)}
        for key in sorted(obj):
            if key in known:
                continue
            kept = self.kept(obj[key], key_path(path, key, positions[key]))
            if kept is not None or obj[key] is None:
                extras[key] = kept
        return extras


def _depth(value: object) -> int:
    deepest, stack = 0, [(value, 0)]
    while stack:
        item, level = stack.pop()
        if isinstance(item, Mapping):
            deepest = max(deepest, level + 1)
            stack.extend((child, level + 1) for child in item.values())
        elif isinstance(item, (list, tuple)):
            deepest = max(deepest, level + 1)
            stack.extend((child, level + 1) for child in item)
    return deepest


def _plain(value: object, path: str, reader: _Reader) -> object:
    """Plain dict/list JSON (imprecise numbers are reported by the byte-reading importer)."""
    if isinstance(value, Mapping):
        return {key: _plain(child, _join(path, key), reader) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(child, f"{path}[{index}]", reader) for index, child in enumerate(value)]
    return value


def _require_name(value: object, path: str) -> str:
    if type(value) is not str or not value.strip():
        raise CardError(f"{path} must be nonblank text", "invalid_card", path)
    if len(value) > MAX_NAME:
        raise CardError(f"{path} is longer than {MAX_NAME} characters", "invalid_card", path)
    return value


def _object(value: object, path: str) -> Mapping:
    if not isinstance(value, Mapping):
        raise CardError(f"{path} must be an object", "invalid_card", path)
    return value


def _matches(value: object, kind: str) -> bool:
    if kind == "bool":
        return type(value) is bool
    if kind == "bool?":
        return value is None or type(value) is bool
    if kind == "number":
        return type(value) is int or isinstance(value, float)
    if kind == "text":
        return type(value) is str
    if kind == "id":
        return type(value) in (int, str)
    raise AssertionError(kind)


def _settings(obj: Mapping, table: Mapping[str, str], path: str, reader: _Reader) -> dict[str, object]:
    settings: dict[str, object] = {}
    for key, kind in table.items():
        if key not in obj:
            continue
        value = obj[key]
        if value is None and kind != "bool?":
            continue
        if _matches(value, kind):
            settings[key] = _plain(value, _join(path, key), reader)
        else:
            reader.lose(_join(path, key), "wrong_type")
    return settings


def parse_lorebook(book: object, path: str, reader: _Reader) -> Lorebook | None:
    """A V3 ``character_book`` (or ``lorebook_v3`` ``data``); ``None`` (reported) if it is not one."""
    if not isinstance(book, Mapping):
        reader.lose(path, "wrong_type")
        return None
    entries: list[LoreEntry] = []
    raw = book.get("entries")
    if raw is not None and (isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence)):
        reader.lose(f"{path}.entries", "wrong_type")
        raw = ()
    for index, entry in enumerate(raw or ()):
        here = f"{path}.entries[{index}]"
        if not isinstance(entry, Mapping) or type(entry.get("content")) is not str:
            reader.lose(here, "wrong_type")
            continue
        enabled = entry.get("enabled", True)
        if type(enabled) is not bool:
            reader.lose(f"{here}.enabled", "wrong_type")
            enabled = True
        entries.append(LoreEntry(
            keys=reader.texts(entry, "keys", here), content=entry["content"], enabled=enabled,
            secondary_keys=reader.texts(entry, "secondary_keys", here),
            settings=MappingProxyType(_settings(entry, _ENTRY_SETTINGS, here, reader)),
            extensions=reader.mapping(entry, "extensions", here),
            extras=MappingProxyType(reader.unknown(entry, _ENTRY, here)), index=index))
    name = reader.optional_text(book, "name", path)
    if name is not None and not name.strip():
        name = None
    elif name is not None and len(name) > MAX_NAME:
        reader.lose(_join(path, "name"), "too_long")
        name = None
    return Lorebook(
        name=name, description=reader.optional_text(book, "description", path),
        entries=tuple(entries), settings=MappingProxyType(_settings(book, _BOOK_SETTINGS, path, reader)),
        extensions=reader.mapping(book, "extensions", path),
        extras=MappingProxyType(reader.unknown(book, _BOOK, path)))


def _greetings(data: Mapping, path: str, reader: _Reader) -> tuple[Greeting, ...]:
    greetings: list[Greeting] = []
    first = reader.text(data, "first_mes", path)
    if first.strip():
        greetings.append(Greeting("first", first))
    for key, prefix, group in (("alternate_greetings", "alt", False), ("group_only_greetings", "group", True)):
        # ids and loss paths follow the raw list's positions, so fixing one entry never renumbers the rest
        for index, text in reader.entries(data, key, path):
            if text.strip():
                greetings.append(Greeting(f"{prefix}-{index + 1}", text, group))
            else:
                reader.lose(f"{_join(path, key)}[{index}]", "blank")
    return tuple(greetings)


_ASSET = frozenset({"type", "uri", "name", "ext"})


def _assets(data: Mapping, path: str, reader: _Reader) -> tuple[Asset, ...]:
    raw = data.get("assets")
    if raw is None:
        return ()
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        reader.lose(f"{path}.assets", "wrong_type")
        return ()
    assets = []
    for index, item in enumerate(raw):
        here = f"{path}.assets[{index}]"
        if not isinstance(item, Mapping) or type(item.get("type")) is not str or type(item.get("uri")) is not str:
            reader.lose(here, "wrong_type")
            continue
        assets.append(Asset(item["type"], item["uri"], reader.optional_text(item, "name", here),
                            reader.optional_text(item, "ext", here),
                            MappingProxyType(reader.unknown(item, _ASSET, here)), index))
    return tuple(assets)


def _authorship(data: Mapping, path: str, reader: _Reader) -> Authorship:
    return Authorship(
        creator=reader.optional_text(data, "creator", path),
        character_version=reader.optional_text(data, "character_version", path),
        creator_notes=reader.optional_text(data, "creator_notes", path),
        source=reader.texts(data, "source", path),
        creation_date=reader.number(data, "creation_date", path),
        modification_date=reader.number(data, "modification_date", path))


def _extras(top: Mapping, data: Mapping | None, top_known: frozenset[str], data_known: frozenset[str],
            reader: _Reader) -> Mapping[str, object]:
    extras: dict[str, object] = {}
    unknown_top = reader.unknown(top, top_known, "")
    if unknown_top:
        extras["card"] = unknown_top
    if data is not None:
        unknown_data = reader.unknown(data, data_known, "data")
        if unknown_data:
            extras["data"] = unknown_data
    return MappingProxyType(extras)


def _v3(document: Mapping, as_scenario: bool) -> CharacterCard | ScenarioCard:
    version = document.get("spec_version")
    if type(version) is not str or not version.startswith("3."):
        raise CardError("this Character Card V3 spec_version is not supported", "unsupported_format",
                        "chara_card_v3_version")
    data = _object(document.get("data"), "data")
    reader = _Reader()
    name = _require_name(data.get("name"), "data.name")
    book = None
    if data.get("character_book") is not None:
        book = parse_lorebook(data["character_book"], "data.character_book", reader)
    common = dict(
        tags=reader.texts(data, "tags", "data"), authorship=_authorship(data, "data", reader), lorebook=book,
        assets=_assets(data, "data", reader), extensions=reader.mapping(data, "extensions", "data"),
        extras=_extras(document, data, frozenset({"spec", "spec_version", "data"}), _V3_DATA, reader))
    description = reader.text(data, "description", "data")
    personality = reader.text(data, "personality", "data")
    scenario = reader.text(data, "scenario", "data")
    examples = reader.text(data, "mes_example", "data")
    system = reader.text(data, "system_prompt", "data")
    post = reader.text(data, "post_history_instructions", "data")
    nickname = reader.optional_text(data, "nickname", "data")
    greetings = _greetings(data, "data", reader)
    if not as_scenario:
        return CharacterCard(
            format=CCV3, spec_version=version, name=name, description=description, personality=personality,
            scenario=scenario, example_messages=examples, greetings=greetings, system_prompt=system,
            post_history_instructions=post, nickname=nickname, losses=tuple(reader.losses), **common)
    premise, texts = scenario, {}
    paths = {"instructions": "data.system_prompt", "creator_notes": "data.creator_notes", "lorebook": "data.character_book",
             "texts.setting": "data.description", "texts.personality": "data.personality",
             "texts.example-messages": "data.mes_example",
             "texts.post-history-instructions": "data.post_history_instructions"}
    if premise.strip():
        paths["premise"] = "data.scenario"
        if description.strip():
            texts["setting"] = description
    else:
        premise = description
        paths["premise"] = "data.description"
    for key, text in (("personality", personality), ("example-messages", examples),
                      ("post-history-instructions", post)):
        if text.strip():
            texts[key] = text
    if nickname is not None:
        reader.lose("data.nickname", "not_applicable")
    # an opening keeps its greeting's id (``opening-first``, ``opening-alt-2``), stable across versions
    openings = tuple(Greeting(f"opening-{greeting.greeting_id}", greeting.text, greeting.group_only)
                     for greeting in greetings)
    if not premise.strip() and not openings:
        raise CardError("a scenario needs a premise or an opening", "invalid_card", "data.scenario")
    return ScenarioCard(format=CCV3, title=name, premise=premise, openings=openings, instructions=system,
                        texts=MappingProxyType(texts), losses=tuple(reader.losses), paths=MappingProxyType(paths),
                        **common)


def _cast(document: Mapping, reader: _Reader) -> tuple[CastRef, ...]:
    raw = document.get("cast")
    if raw is None:
        return ()
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        reader.lose("cast", "wrong_type")
        return ()
    cast = []
    for index, item in enumerate(raw):
        here = f"cast[{index}]"
        if type(item) is str and item.strip():
            cast.append(CastRef(name=item))
            continue
        if not isinstance(item, Mapping):
            reader.lose(here, "wrong_type")
            continue
        named = {key: item[key] for key in ("ref", "name")
                 if key in item and type(item[key]) is str and item[key].strip()}
        for key in ("ref", "name"):
            if key in item and key not in named:
                reader.lose(f"{here}.{key}", "wrong_type")
        if not named:
            if "ref" not in item and "name" not in item:
                reader.lose(here, "wrong_type")
            continue
        cast.append(CastRef(named.get("ref"), named.get("name"),
                            MappingProxyType(reader.unknown(item, frozenset({"ref", "name"}), here))))
    return tuple(cast)


def _scenario_doc(document: Mapping) -> ScenarioCard:
    reader = _Reader()
    title = _require_name(document.get("title"), "title")
    openings: list[Greeting] = []
    if document.get("opening") is not None and document.get("openings") is not None:
        raise CardError("give either opening or openings, not both", "invalid_card", "openings")
    if document.get("opening") is not None:
        single = reader.text(document, "opening", "")
        if single.strip():
            openings.append(Greeting("opening-1", single))
    for index, text in reader.entries(document, "openings", ""):
        if text.strip():
            openings.append(Greeting(f"opening-{index + 1}", text))
        else:
            reader.lose(f"openings[{index}]", "blank")
    premise = reader.text(document, "premise", "")
    if not premise.strip() and not openings:
        raise CardError("a scenario needs a premise or an opening", "invalid_card", "premise")
    texts = {}
    setting = reader.text(document, "setting", "")
    if setting.strip():
        texts["setting"] = setting
    book = None
    if document.get("lorebook") is not None:
        book = parse_lorebook(document["lorebook"], "lorebook", reader)
    authorship = Authorship(creator=reader.optional_text(document, "creator", ""),
                            creator_notes=reader.optional_text(document, "creator_notes", ""))
    cast = _cast(document, reader)
    return ScenarioCard(
        format=SCENARIO_DOC, title=title, premise=premise, openings=tuple(openings),
        instructions=reader.text(document, "instructions", ""), texts=MappingProxyType(texts), cast=cast,
        tags=reader.texts(document, "tags", ""), authorship=authorship, lorebook=book,
        extensions=reader.mapping(document, "extensions", ""),
        extras=MappingProxyType({"card": unknown} if (unknown := reader.unknown(document, _SCENARIO, "")) else {}),
        losses=tuple(reader.losses))


def _lorebook_doc(document: Mapping) -> LorebookCard:
    reader = _Reader()
    data = _object(document.get("data"), "data")
    book = parse_lorebook(data, "data", reader)
    assert book is not None  # data is a mapping
    return LorebookCard(LOREBOOK_V3, book, _extras(document, None, frozenset({"spec", "data"}), _BOOK, reader),
                        tuple(reader.losses))


def detect(document: Mapping) -> str:
    """The format of a decoded document, or ``CardError("unsupported_format")`` naming what it is."""
    spec = document.get("spec")
    if spec == CCV3:
        return CCV3
    if spec == LOREBOOK_V3:
        return LOREBOOK_V3
    if spec == "chara_card_v2":
        raise CardError("Character Card V2 import is a later milestone", "unsupported_format", "chara_card_v2")
    if spec is None and document.get("format") == SCENARIO_DOC:
        return SCENARIO_DOC
    if spec is None and "name" in document and ("first_mes" in document or "char_persona" in document):
        raise CardError("Character Card V1 import is a later milestone", "unsupported_format", "chara_card_v1")
    if spec is None and "entries" in document:
        raise CardError("SillyTavern world-info import is a later milestone", "unsupported_format",
                        "sillytavern_world_info")
    raise CardError("not a card format this slice imports", "unsupported_format", "unknown")


def parse_card(document: Mapping, *, as_scenario: bool = False) -> ParsedCard:
    """One decoded card document as typed values with its losses; see the module docstring."""
    if not isinstance(document, Mapping):
        raise CardError("a card is a JSON object", "invalid_card", "")
    found = detect(document)
    if found == CCV3:
        return _v3(document, as_scenario)
    if as_scenario:
        raise CardError("only a Character Card V3 can be imported as a scenario", "invalid_card", "spec")
    if found == LOREBOOK_V3:
        return _lorebook_doc(document)
    return _scenario_doc(document)


def parse_lorebook_document(document: Mapping) -> LorebookCard:
    """A separately supplied lorebook: only ``lorebook_v3`` in this slice."""
    if not isinstance(document, Mapping):
        raise CardError("a lorebook is a JSON object", "invalid_card", "")
    found = detect(document)
    if found != LOREBOOK_V3:
        raise CardError("a separate lorebook must be a lorebook_v3 document", "unsupported_format", found)
    return _lorebook_doc(document)
