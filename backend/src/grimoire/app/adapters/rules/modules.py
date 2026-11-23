"""086's mechanics module authoring on the Rules page (116): ``rules.authoring`` ... ``rules.grade``.

Everything here is ``grimoire.mechanicsstore.MechanicsStore`` over the open library's own root (its root guards, as
the shelf's store), and ``grimoire.mechanics``' pure validation, sheets and grading. Nothing is kept beside them.

**What can be listed.** 086's store lists no drafts and no module ids: it reads one draft by its id, and the
published revisions of one id (``revisions``). So the page opens a module by the id the person gives, and lists that
id's revisions; it never walks the store's folders to make up a list the domain does not answer (docs/RULES_PAGE.md).

**Writes.** A draft is the author's own file: saving keeps any text, unfinished JSON included, and validates
nothing. Each write is reviewed first: ``save_plan`` says which file it would write and what the text would check
as, and writes nothing; ``save`` writes exactly that text against the etag the person reviewed, so a draft changed
in between is refused (``changed``) and the page reads it again. A save retried after a lost answer finds its own
text already there and writes nothing. ``publish_plan`` names the revision digest and the file a draft with no
problem would publish as (a draft with any problem is refused, ``invalid_module``); ``publish`` publishes the draft
read at that etag, refused if it moved on or if it would publish as another revision than the one reviewed.
Publishing the same content again writes nothing (086's own rule).

**Trying a revision.** ``sheet`` fills a sheet of one sheet type under one published revision and ``grade`` grades
one of its checks from dice the person entered (086 never rolls), then confirms the record under the same revision.
Neither is saved: 086 has no sheet store, no campaign selection of a revision and no durable roll journal for its
checks, and every answer says so (``saved: false``).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping

from grimoire.app.adapter import AdapterContext, Handler
from grimoire.mechanics import (
    FORMAT, CheckError, MechanicsModule, ModuleInvalid, Problem, SheetInvalid, confirm, fill_sheet, run_check,
    template,
)
from grimoire.mechanics.templates import TEMPLATES
from grimoire.mechanicsstore import MAX_DRAFT_BYTES, MAX_MODULE_BYTES, MechanicsStore, MechanicsStoreError, decode
from grimoire.store.jsonfmt import render
from grimoire.textapi import ApiRefused

BLANK = "blank"
#: Said with every sheet and every graded check: what neither is.
NOT_SAVED = ("Not saved. A filled sheet and a graded check live only on this screen: 086 keeps no sheets, selects no "
             "revision for a campaign and records no roll of an authored check.")
#: What 086 does not do that a person might look for here, each with its reason (shown as unavailable).
UNAVAILABLE = (
    {"what": "Saving a filled sheet", "reason": "086 has no sheet store: a sheet is checked and its derived values "
                                                "computed, and nothing keeps it."},
    {"what": "Selecting a revision for a campaign or chat",
     "reason": "086 has no campaign selection of a module: no campaign or chat records which revision it plays under."},
    {"what": "Recording a graded check", "reason": "086 has no durable roll journal: its checks grade dice the person "
                                                   "enters. 075's recorded rolls take 062's reference profiles, not an "
                                                   "authored module."},
    {"what": "A list of the library's drafts and modules",
     "reason": "086's store reads one draft by its id and the revisions of one id; it lists no drafts or module ids. "
               "Open a module by its id."},
)
_HINT = re.compile(r"did you mean '([^']+)'\?")


# -- shared ----------------------------------------------------------------------------------------------------
def mechanics_of(ctx: AdapterContext) -> MechanicsStore:
    """086's store over the open library, with the library's own root guards."""
    return MechanicsStore(ctx.store.root, check_read=ctx.store.check_root, check_write=ctx.store.check_destination)


def guarded(handler: Handler) -> Handler:
    """086's refusals answered with their own reasons, never a server error."""
    def run(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
        try:
            return handler(ctx, args)
        except MechanicsStoreError as error:
            raise ApiRefused(error.reason, str(error)) from None
        except ModuleInvalid as error:
            raise ApiRefused("invalid_module", f"the draft has {len(error.problems)} problem(s): {error}") from None
    run.__name__ = handler.__name__
    return run


def _module_id(args: Mapping[str, object]) -> str:
    value = args.get("module")
    MechanicsStore.draft_path(value)  # type: ignore[arg-type]  # 086's own id rule (``invalid_id``)
    return value  # type: ignore[return-value]


def _expected(args: Mapping[str, object]) -> str | None:
    if "expected" not in args:
        raise ApiRefused("review_required", "read the draft before writing it")
    value = args.get("expected")
    if value is not None and (type(value) is not str or not value.startswith("sha256:")):
        raise ApiRefused("invalid_field", "expected must be the etag of the draft that was read, or null")
    return value


def _bytes_of(args: Mapping[str, object]) -> bytes:
    text = args.get("text")
    if type(text) is not str:
        raise ApiRefused("invalid_field", "text must be the draft's text")
    try:
        data = text.encode("utf-8")
    except UnicodeEncodeError:
        raise ApiRefused("invalid_field", "the text holds a character that cannot be stored") from None
    if len(data) > MAX_DRAFT_BYTES:
        raise ApiRefused("too_large", f"a draft is at most {MAX_DRAFT_BYTES} bytes")
    return data


def problem_view(problem: Problem) -> dict:
    hint = _HINT.search(problem.message)
    return {"path": problem.path, "message": problem.message, "hint": hint.group(1) if hint else None}


def _field_view(field) -> dict:  # type: ignore[no-untyped-def]
    default = list(field.default) if isinstance(field.default, tuple) else field.default
    return {"key": field.key, "label": field.label, "type": field.type, "default": default, "min": field.minimum,
            "max": field.maximum, "max_length": field.max_length, "max_items": field.max_items}


def module_view(module: MechanicsModule) -> dict:
    """What a validated module holds, as the page shows it: its sheet types and its checks."""
    sheets = [{"id": sheet.id, "label": sheet.label, "fields": [_field_view(field) for field in sheet.fields],
               "derived": [{"key": key, "formula": expr.text} for key, expr in sheet.derived]}
              for sheet in module.sheets.values()]
    checks = [{"id": check.id, "label": check.label, "sheet": check.sheet, "difficulty": check.difficulty,
               "dice": {"count": check.dice.count.text, "sides": check.dice.sides,
                        "target": None if check.dice.target is None else check.dice.target.text},
               "total": None if check.total is None else check.total.text,
               "outcomes": [{"label": item.label, "when": item.when.text} for item in check.outcomes]}
              for check in module.checks.values()]
    return {"id": module.id, "name": module.name, "version": module.version, "family": module.family,
            "revision": module.revision, "sheets": sheets, "checks": checks}


def _checked(store: MechanicsStore, module_id: str, module: MechanicsModule | None,
             problems: tuple[Problem, ...]) -> dict:
    answer = {"problems": [problem_view(item) for item in problems], "publishable": module is not None,
              "revision": None, "file": None, "published": False, "module": None}
    if module is not None:
        answer.update(revision=module.revision, file=store.revision_path(module_id, module.revision),
                      published=module.revision in store.revisions(module_id), module=module_view(module))
    return answer


def _draft_view(store: MechanicsStore, module_id: str) -> dict | None:
    draft = store.draft(module_id)
    if draft is None:
        return None
    try:
        text: str | None = draft.data.decode("utf-8")
    except UnicodeDecodeError:  # a hand-written file this page cannot show as text; it is never overwritten here
        text = None
    return {"etag": draft.etag, "bytes": len(draft.data), "text": text}


def _module_answer(store: MechanicsStore, module_id: str) -> dict:
    return {"module": module_id, "file": store.draft_path(module_id), "draft": _draft_view(store, module_id),
            "revisions": [{"revision": revision, "file": store.revision_path(module_id, revision)}
                          for revision in store.revisions(module_id)]}


# -- ops -------------------------------------------------------------------------------------------------------
def _authoring(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    templates = [{"template": name, "name": item["name"], "family": item["family"], "notes": item.get("notes")}
                 for name, item in TEMPLATES.items()]
    return {"templates": [*templates, {"template": BLANK, "name": "Blank", "family": "generic",
                                       "notes": "An empty module of the generic family: add sheet types and checks."}],
            "listed": {"drafts": False, "modules": False, "revisions": True},
            "unavailable": [dict(item) for item in UNAVAILABLE], "not_saved": NOT_SAVED,
            "max_draft_bytes": MAX_DRAFT_BYTES, "max_module_bytes": MAX_MODULE_BYTES,
            "drafts": "config/mechanics/drafts/<id>.json", "revisions": "config/mechanics/modules/<id>/<digest>.json"}


def _module(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    module_id = _module_id(args)
    return _module_answer(mechanics_of(ctx), module_id)


def _template(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """A starting text for draft ``module``: a template with its id set to the draft's, or a blank module."""
    module_id = _module_id(args)
    name = args.get("template")
    if type(name) is not str:
        raise ApiRefused("invalid_field", "template must be " + ", ".join([*TEMPLATES, BLANK]))
    if name == BLANK:
        document: dict = {"format": FORMAT, "id": module_id, "name": module_id.replace("_", " ").capitalize(),
                          "version": "1", "family": "generic", "sheets": {}, "checks": {}}
    elif name in TEMPLATES:
        document = template(name)  # type: ignore[arg-type]
        document["id"] = module_id  # a draft's id is the one it is saved under (086)
    else:
        raise ApiRefused("invalid_field", "template must be " + ", ".join([*TEMPLATES, BLANK]))
    return {"module": module_id, "template": name,
            "text": json.dumps(document, indent=2, ensure_ascii=False) + "\n"}


def _check(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """Every problem of the text given (as typed, unsaved: nothing is written), or of the saved draft."""
    module_id = _module_id(args)
    store = mechanics_of(ctx)
    if args.get("text") is not None:
        module, problems = decode(_bytes_of(args), module_id)
        return {"module": module_id, "of": "text", "etag": None, **_checked(store, module_id, module, problems)}
    checked = store.check_draft(module_id)
    return {"module": module_id, "of": "draft", "etag": checked.etag,
            **_checked(store, module_id, checked.module, checked.problems)}


def _reviewed_save(ctx: AdapterContext, args: Mapping[str, object]):  # type: ignore[no-untyped-def]
    """The store, id, bytes, the draft as it is now, and whether those bytes are already the draft."""
    module_id = _module_id(args)
    expected = _expected(args)
    data = _bytes_of(args)
    store = mechanics_of(ctx)
    current = store.draft(module_id)
    already = current is not None and current.data == data
    return store, module_id, data, expected, current, already


def _save_plan(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    store, module_id, data, expected, current, already = _reviewed_save(ctx, args)
    if not already:
        if expected is None and current is not None:
            raise ApiRefused("draft_exists", f"there is already a draft of {module_id}: open it to edit it")
        if (None if current is None else current.etag) != expected:
            raise ApiRefused("changed", "the draft changed since it was read; read it again")
    module, problems = decode(data, module_id)
    return {"plan": {"module": module_id, "file": store.draft_path(module_id), "bytes": len(data),
                     "before": None if current is None else len(current.data), "creates": current is None,
                     "unchanged": already},
            "check": {"of": "text", **_checked(store, module_id, module, problems)}}


def _save(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """Save the reviewed text against the reviewed etag. The same text saved again (a retry) writes nothing."""
    store, module_id, data, expected, current, already = _reviewed_save(ctx, args)
    if not already:
        if expected is None and current is not None:
            raise ApiRefused("draft_exists", f"there is already a draft of {module_id}: open it to edit it")
        store.save_draft(module_id, data, expected)  # a draft that moved on is StaleWrite: ``changed``
    return {"done": {"module": module_id, "already": already}, **_module_answer(store, module_id)}


def _publish_plan(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    module_id = _module_id(args)
    expected = _expected(args)
    store = mechanics_of(ctx)
    checked = store.check_draft(module_id)
    if checked.etag != expected:
        raise ApiRefused("changed", "the draft changed since it was read; read it again")
    if checked.module is None:
        raise ModuleInvalid(checked.problems)
    module = checked.module
    return {"plan": {"module": module_id, "etag": checked.etag, "revision": module.revision,
                     "file": store.revision_path(module_id, module.revision),
                     "already": module.revision in store.revisions(module_id),
                     "name": module.name, "version": module.version, "family": module.family}}


def _publish(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """Publish the draft read at ``expected`` as the reviewed ``revision``; anything else is refused."""
    module_id = _module_id(args)
    expected = _expected(args)
    if expected is None:
        raise ApiRefused("review_required", "review the draft before publishing it")
    revision = args.get("revision")
    if type(revision) is not str:
        raise ApiRefused("review_required", "review what the draft publishes as before publishing it")
    store = mechanics_of(ctx)
    checked = store.check_draft(module_id)
    if checked.etag != expected:
        raise ApiRefused("changed", "the draft changed since it was reviewed; review it again")
    if checked.module is not None and checked.module.revision != revision:
        raise ApiRefused("changed", "the draft would publish as another revision than the one reviewed")
    already = checked.module is not None and revision in store.revisions(module_id)
    module = store.publish(module_id, expected)  # a draft with a problem is ModuleInvalid: nothing is written
    return {"done": {"module": module_id, "revision": module.revision, "already": already,
                     "file": store.revision_path(module_id, module.revision)}, **_module_answer(store, module_id)}


def _loaded(ctx: AdapterContext, args: Mapping[str, object]) -> tuple[MechanicsStore, str, MechanicsModule]:
    module_id = _module_id(args)
    plain([args.get(key) for key in ("revision", "sheet_type", "check")], "the request")
    store = mechanics_of(ctx)
    return store, module_id, store.load(module_id, args.get("revision"))  # type: ignore[arg-type]


def _revision(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """One published revision exactly as published, read-only."""
    store, module_id, module = _loaded(ctx, args)
    return {"module": module_id, "file": store.revision_path(module_id, module.revision),
            "text": render(module.document).decode("utf-8"), "view": module_view(module),
            "not_saved": NOT_SAVED}


def plain(value: object, what: str) -> None:
    """Refuse ``invalid_field`` for any text in ``value`` that cannot be stored or answered (a lone surrogate)."""
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise ApiRefused("invalid_field", f"{what} holds a character that cannot be stored") from None
    elif isinstance(value, Mapping):
        for key, item in value.items():
            plain(key, what)
            plain(item, what)
    elif isinstance(value, (list, tuple)):
        for item in value:
            plain(item, what)


def _values(args: Mapping[str, object]) -> Mapping[str, object]:
    values = args.get("values", {})
    if not isinstance(values, Mapping):
        raise ApiRefused("invalid_field", "values must be an object of field values")
    plain(values, "values")
    return values


def _sheet_answer(module: MechanicsModule, sheet_type: object, values: Mapping[str, object]):  # type: ignore[no-untyped-def]
    try:
        sheet = fill_sheet(module, sheet_type, values)  # type: ignore[arg-type]
    except SheetInvalid as error:
        return None, {"valid": False, "problems": [problem_view(item) for item in error.problems],
                      "values": None, "derived": None}
    shown = {key: list(value) if isinstance(value, tuple) else value for key, value in sheet.values.items()}
    return sheet, {"valid": True, "problems": [], "values": shown, "derived": dict(sheet.derived)}


def _sheet(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """``values`` filled as a sheet of ``sheet_type`` under the revision: its problems, or its derived values."""
    _, module_id, module = _loaded(ctx, args)
    _, answer = _sheet_answer(module, args.get("sheet_type"), _values(args))
    return {"module": module_id, "revision": module.revision, "sheet_type": args.get("sheet_type"), **answer,
            "saved": False, "not_saved": NOT_SAVED}


def _whole(args: Mapping[str, object], key: str, *, optional: bool = False) -> int | None:
    value = args.get(key)
    if value is None and optional:
        return None
    if type(value) is not int:
        raise ApiRefused("invalid_field", f"{key} must be a whole number")
    return value


def _grade(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """Grade ``check`` for the sheet from the dice the person entered, then confirm the record under the same
    revision. 086 rolls nothing; nothing is recorded."""
    _, module_id, module = _loaded(ctx, args)
    sheet, filled = _sheet_answer(module, args.get("sheet_type"), _values(args))
    if sheet is None:
        raise ApiRefused("invalid_sheet", "fill the sheet without problems before grading a check: "
                         + "; ".join(item["path"] + ": " + item["message"] for item in filled["problems"][:3]))
    dice = args.get("dice")
    if type(dice) is not list or not all(type(face) is int for face in dice):
        raise ApiRefused("invalid_field", "dice must be the faces as rolled, a list of whole numbers")
    try:
        record = run_check(module, sheet, args.get("check"), dice=tuple(dice),  # type: ignore[arg-type]
                           modifier=_whole(args, "modifier"),  # type: ignore[arg-type]
                           difficulty=_whole(args, "difficulty", optional=True))
        confirm(module, record)
    except CheckError as error:
        raise ApiRefused("check_refused", str(error)) from None
    return {"module": module_id, "revision": record.revision, "sheet_type": record.sheet_type,
            "derived": filled["derived"],
            "record": {"check": record.check, "dice": list(record.dice), "modifier": record.modifier,
                       "difficulty": record.difficulty, "total": record.total, "successes": record.successes,
                       "outcome": record.outcome},
            "confirmed": True, "saved": False, "not_saved": NOT_SAVED}


OPS: Mapping[str, Callable[[AdapterContext, Mapping[str, object]], dict]] = {
    name: guarded(handler) for name, handler in {
        "authoring": _authoring, "module": _module, "template": _template, "check": _check,
        "save_plan": _save_plan, "save": _save, "publish_plan": _publish_plan, "publish": _publish,
        "revision": _revision, "sheet": _sheet, "grade": _grade}.items()}

__all__ = ["BLANK", "NOT_SAVED", "OPS", "UNAVAILABLE", "mechanics_of", "module_view", "plain", "problem_view"]
