"""suggest: controls responsibilities."""

from __future__ import annotations

from ..continuity import effective
from . import _contracts, _dates


def _bad(reason: str, detail: str) -> _contracts.ControlsError:
    return _contracts.ControlsError(400, "bad_controls", detail, reason=reason)


def _distinct(refs) -> list[str]:
    """Stripped, blanks dropped, de-duplicated in order."""
    out: list[str] = []
    for raw in refs:
        ref = raw.strip() if isinstance(raw, str) else ""
        if ref and ref not in out:
            out.append(ref)
    return out


def _live_canon_or_identity(cid: str) -> dict[str, str]:
    try:
        return effective.live_canon(cid)
    except Exception:  # noqa: BLE001 -- an unreadable continuity doc canonicalizes nothing (Decision 4); a ref it would have moved is judged as written
        return {}


def _check_must(must: tuple[str, ...], avoid: tuple[str, ...]) -> None:
    if set(must) & set(avoid):
        raise _bad("must_avoid", "a driver cannot be both must-include and avoided")
    if len(must) > _contracts.MUST_CAP:
        raise _bad("must_cap", f"at most {_contracts.MUST_CAP} must-include drivers")
    if any(ref.split(":", 1)[0] not in _contracts.MUST_KINDS for ref in must):
        raise _bad("must_kind", "only threads and commitments can be must-include; "
                                "use the time anchor for a date")


def _check_anchor(snapshot: dict, time_mode: str, anchor: str, relation: str) -> bool:
    """Decisions 5 and 6: the anchor's structure, judged before staleness, and
    whether the anchor is month-only. A month-only anchor's precision comes
    from its option when the capture has one, else from the ref's own shape."""
    if time_mode == "anchor" and not anchor:
        raise _bad("anchor_missing", "an anchored time mode needs an anchor")
    if not anchor:
        return False
    if time_mode != "anchor":
        raise _bad("anchor_without_mode", "an anchor needs the anchored time mode")
    prefix, sep, rest = anchor.partition(":")
    if not sep or not rest or prefix not in _contracts.TEMPORAL_KINDS:
        raise _bad("anchor_kind", "an anchor is an event, a holiday or a birthday")
    option = next((a for a in snapshot.get("anchors", []) if a["ref"] == anchor), None)
    month = (option.get("precision") == "month" if option is not None
             else _dates._month_of(anchor) is not None)
    if month and relation not in ("", "on"):
        raise _bad("anchor_relation", "a birthday known only by its month takes only \"on\"")
    return month


def _stale(snapshot: dict, refs: tuple[str, ...], anchor: str) -> list[str]:
    """The refs the request-time capture does not list, in request order. An
    anchor has to be an anchor option: a passed or undated event is a driver,
    never an anchor (Decision 5)."""
    index = {d["ref"] for d in snapshot.get("driver_index", [])}
    out = [ref for ref in refs if ref not in index]
    if anchor and anchor not in {a["ref"] for a in snapshot.get("anchors", [])}:
        out.append(anchor)
    return _distinct(out)


def resolve_controls(cid: str, snapshot: dict, *, focus_refs=(), avoid_refs=(), must_refs=(),
                     time_mode: str = "auto", time_anchor_ref: str = "",
                     time_anchor_relation: str = "") -> _contracts.Controls:
    """A request's Story Pressure controls validated against `snapshot`, the
    capture the prompt is rendered from (spec §16.3, Decision 7).

    In order: refs are stripped, blanks dropped and de-duplicated; every ref
    canonicalizes through `effective.live_canon` (Decision 4: an alias whose
    target was deleted leaves its source a driver of its own; unreadable means
    the identity); the 400s (`ControlsError`, `bad_controls`) for must/avoid
    and for the anchor; then one 409 `stale_drivers` naming every ref the
    capture does not hold. Precedence runs last: the anchor leaves `avoid`
    (Decision 26), then `focus` loses what `must` or `avoid` holds (Decision 8).
    A relation without an anchor means nothing and is dropped; a month-only
    anchor's blank relation resolves to `on` here, so the prompt never offers
    the model a relation the parser would coerce away (Decision 6)."""
    canon = _live_canon_or_identity(cid)

    def canonical(refs) -> tuple[str, ...]:
        return tuple(_distinct(canon.get(ref, ref) for ref in _distinct(refs)))

    focus, avoid, must = canonical(focus_refs), canonical(avoid_refs), canonical(must_refs)
    anchor = next(iter(canonical([time_anchor_ref])), "")
    _check_must(must, avoid)
    month = _check_anchor(snapshot, time_mode, anchor, time_anchor_relation)
    stale = _stale(snapshot, (*focus, *avoid, *must), anchor)
    if stale:
        raise _contracts.ControlsError(409, "stale_drivers",
                            "some selected drivers are no longer current", refs=stale)
    avoid = tuple(ref for ref in avoid if ref != anchor)
    focus = tuple(ref for ref in focus if ref not in must and ref not in avoid)
    return _contracts.Controls(focus=focus, avoid=avoid, must=must, time_mode=time_mode, anchor=anchor,
                    relation=("on" if month else time_anchor_relation) if anchor else "")
