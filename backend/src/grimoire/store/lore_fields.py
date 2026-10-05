"""Activation controls on a world-info entry: the field catalog.

A leaf module -- it reads no store and writes none. Three things live here and
nothing else, so the engine, the editor save and the adopt path cannot drift on
what a field is:

- the catalog (`FIELD_KEYS`, the enums, `BOUNDS`);
- `parse`, the **lenient** reader activation uses: frontmatter is hand-editable,
  so a value that does not parse is the field's default, never an exception,
  and one bad field does not spoil its neighbours;
- `invalid`, the **strict** check a save uses: it names the keys whose value is
  bad, so the editor can refuse with a 400 rather than clamp. A blank value is
  valid -- it is how a save clears a field.

Frontmatter is flat single-line strings, so lists are comma-joined and numbers
are decimal strings.
"""
from collections.abc import Mapping
from dataclasses import dataclass

from . import entity_schema

FIELD_KEYS: tuple[str, ...] = (
    "secondary_keys", "key_logic", "scan_depth", "sticky", "cooldown",
    "priority", "keep", "recursion", "known_by",
)
KEY_LOGIC = ("and_any", "and_all", "not_any", "not_all")
RECURSION = ("both", "pulled_only", "pulls_only", "none")
BOUNDS: dict[str, tuple[int, int]] = {
    "scan_depth": (0, 100),
    "sticky": (0, 50),
    "cooldown": (0, 50),
    "priority": (0, 1000),
}
DEFAULT_PRIORITY = 100
# `known_by` names actors only; the five generic kinds can be reclassified and
# an actor cannot, so a ref to one of those would be a ref that can move.
KNOWN_BY_KINDS = ("characters", "pcs")


@dataclass(frozen=True)
class Controls:
    secondary_keys: tuple[str, ...] = ()
    key_logic: str = "and_any"
    scan_depth: int | None = None
    sticky: int = 0
    cooldown: int = 0
    priority: int = DEFAULT_PRIORITY
    keep: bool = False
    recursion: str = "both"
    known_by: tuple[str, ...] = ()

    def pulled_ok(self) -> bool:
        """Can another entry's body activate this one?"""
        return self.recursion in ("both", "pulled_only")

    def pulls_ok(self) -> bool:
        """Can this entry's own body activate others?"""
        return self.recursion in ("both", "pulls_only")

    def timed(self) -> bool:
        return self.sticky > 0 or self.cooldown > 0


def split_list(value: object) -> tuple[str, ...]:
    """A comma-joined scalar as its items: stripped, empties dropped, order
    kept, duplicates removed. Anything that is not a string is no items."""
    if not isinstance(value, str):
        return ()
    seen: dict[str, None] = {}
    for raw in value.split(entity_schema.REF_DELIMITER):
        part = raw.strip()
        if part:
            seen.setdefault(part, None)
    return tuple(seen)


def _int(value: object) -> int | None:
    """An integer, or None. A bool is not one, and neither is "2.5"."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if not isinstance(value, str):
        return None
    try:
        return int(value.strip())
    except ValueError:
        return None


def _bounded(key: str, value: object) -> int | None:
    n = _int(value)
    if n is None:
        return None
    lo, hi = BOUNDS[key]
    return n if lo <= n <= hi else None


def _is_actor_ref(ref: str) -> bool:
    kind, sep, eid = ref.partition(":")
    return bool(sep) and kind in KNOWN_BY_KINDS and entity_schema.referenceable(eid)


def single_line(value: str) -> bool:
    """Can this string be a frontmatter scalar? The writer puts a value on one
    line and the reader splits with `str.splitlines`, so anything `splitlines`
    treats as a boundary (not just `\\n` and `\\r`) truncates the record on its
    way back -- the rule `entity_schema.referenceable` holds refs to, asked the
    same way. The empty string is no lines and is fine."""
    return value.splitlines() in ([], [value])


def _blank(value: object) -> bool:
    return isinstance(value, str) and not value.strip()


def parse(meta: Mapping[str, object]) -> Controls:
    """The controls a record's frontmatter carries. Never raises: each field
    that is absent or does not parse is its default, independently."""
    key_logic = meta.get("key_logic")
    key_logic = key_logic.strip() if isinstance(key_logic, str) else None
    recursion = meta.get("recursion")
    recursion = recursion.strip() if isinstance(recursion, str) else None
    keep = meta.get("keep")
    defaults = Controls()
    sticky = _bounded("sticky", meta.get("sticky"))
    cooldown = _bounded("cooldown", meta.get("cooldown"))
    priority = _bounded("priority", meta.get("priority"))
    return Controls(
        secondary_keys=split_list(meta.get("secondary_keys")),
        key_logic=key_logic if key_logic in KEY_LOGIC else defaults.key_logic,
        scan_depth=_bounded("scan_depth", meta.get("scan_depth")),
        sticky=defaults.sticky if sticky is None else sticky,
        cooldown=defaults.cooldown if cooldown is None else cooldown,
        priority=defaults.priority if priority is None else priority,
        keep=isinstance(keep, str) and keep.strip().lower() == "true",
        recursion=recursion if recursion in RECURSION else defaults.recursion,
        known_by=tuple(r for r in split_list(meta.get("known_by"))
                       if _is_actor_ref(r)),
    )


def _field_ok(key: str, value: object) -> bool:
    if key in BOUNDS:
        return _bounded(key, value) is not None
    if not isinstance(value, str):
        return False
    text = value.strip()
    if key == "key_logic":
        return text in KEY_LOGIC
    if key == "recursion":
        return text in RECURSION
    if key == "keep":
        return text.lower() == "true"
    if key == "known_by":
        return all(_is_actor_ref(r) for r in split_list(text))
    return True  # secondary_keys: any comma list


def _refused(key: str, value: object) -> bool:
    # A line boundary is refused whatever the field and whatever else the value
    # says: even a blank "\n" or a "2\n" would be written as-is and corrupt
    # the record, though `parse` would have read it as empty or as 2.
    if isinstance(value, str) and not single_line(value):
        return True
    return not _blank(value) and not _field_ok(key, value)


def invalid(fields: Mapping[str, object]) -> list[str]:
    """Sorted keys, among the catalog's, whose value a save must refuse. A
    blank string is valid (it clears the field); a value that spans lines is
    not, since the frontmatter writer cannot carry one. Keys outside the
    catalog are not this module's to judge."""
    return sorted(k for k in FIELD_KEYS if k in fields and _refused(k, fields[k]))
