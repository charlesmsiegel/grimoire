"""The shared rewrite of a proposed-new row onto an existing record."""

from __future__ import annotations

from .. import commitments, fieldtext


def onto_existing(kind: str, row: dict, rid: str) -> dict:
    """The §10.2 rewrite of `row` onto the existing record `rid`.

    The id becomes the canonical one and the title blank (keep stored). A plot
    status is the model's ``closed`` or ``advanced``, else ``advanced`` -- never
    ``open``, which the extraction's own default is and which would regress an
    advanced thread. A commitment keeps its stored kind and keeps a status only
    when it resolves the record. A stated ``due`` is kept and an absent one
    stays absent; a blank one is dropped, because ``""`` means "lift the
    deadline" only on a row that named the record -- on a row that would have
    opened one there was no deadline to lift, so carrying it across would
    clear the stored record's. Citation keys are untouched."""
    new = {**row, "id": rid, "title": ""}
    if "due" in row and not fieldtext.text(row.get("due")):
        del new["due"]
    status = row.get("status")
    if kind == "thread":
        new["status"] = status if status in ("closed", "advanced") else "advanced"
    else:
        new["kind"] = ""
        new["status"] = status if status in commitments.RESOLVED else ""
    return new
