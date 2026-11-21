"""A synthetic campaign stocked for Sprint 115's Prep page (``prep.*``): no real library, no paid calls.

``stocked`` plays one kept turn of the desktop library's Mara Vell card chat (Anya Rook seated as the owner's
persona) and gives the campaign what 064's evidence is assembled from: two director propositions (one Anya was told,
so only the other is a secret, and a third the director refuted), a started clock with one deadline, and a harbor
location. ``settings`` is a model settings file (112) with prices and an agenda for Mara; the transport is always
``grimoire.testing.providers.ScriptedTransport``.
"""

from __future__ import annotations

import json
from pathlib import Path

from store_helpers import OWNER
from textapi_fixtures import keep

from grimoire.app.adapters import providers
from grimoire.app.appapi import AppApi
from grimoire.core import Event, Moment, Proposition, Report, Ruling
from grimoire.core.entity import EntityInstance
from grimoire.core.entity_codec import ENTITY_SCHEMA, instance_to_payload
from grimoire.core.identity import InstanceRef
from grimoire.evidencestore import EvidenceLibrary
from grimoire.store import RecordEnvelope
from grimoire.testing.providers import Reply, ScriptedTransport

DIRECTOR = {"audience": "director"}
SECRET = "The harbor master sold the lighthouse oil to smugglers."
TOLD = "Mara owes the harbor guild a debt."
REFUTED = "Anya is the guild's spy."
AIM = "Keep the guild from learning about the coin"
BRIEF = "A tense night on the quay after the coin changes hands"
USAGE = {"prompt_tokens": 410, "completion_tokens": 120}


def cast_of(device, chat) -> dict[str, str]:
    return {member["name"]: member["actor"] for member in
            AppApi(device.api).call("campaign.hub", {"chat": str(chat), **DIRECTOR})["cast"]}


def stocked(device, chat) -> dict[str, str]:
    """Play one kept turn and give the campaign its evidence; the cast's actor refs by name, and the harbor's."""
    device.api.runner.seat(chat, OWNER)
    device("post", chat=str(chat), text="*I press a coin into Mara's hand*")
    device("generate", chat=str(chat))
    keep(device, str(chat))
    cast = cast_of(device, chat)
    c = chat.campaign_id
    anya = InstanceRef.parse(cast["Anya Rook"])
    harbor = InstanceRef(c, "location", "harbor")
    EvidenceLibrary(device.table.store, device.table.owner, owner=OWNER).append(c, [
        Event(c, "ev-oil", "the oil changes hands", Moment(1, "quay", "t1"), "director"),
        Proposition(c, "oil-sold", SECRET, Moment(1, "quay", "t1"), "director"),
        Proposition(c, "mara-debt", TOLD, Moment(2, "quay", "t1"), "director"),
        Report(c, "told-debt", anya, "mara-debt", "speech", Moment(2, "quay", "t1"), "director", confirmed=True),
        Proposition(c, "anya-spy", REFUTED, Moment(3, "quay", "t1"), "director"),
        Ruling(c, "not-a-spy", "anya-spy", "refuted", Moment(3, "quay", "t1"), "director"),
    ]).commit()
    txn = device.table.store.transaction()
    txn.put(RecordEnvelope(harbor, instance_to_payload(EntityInstance(harbor, local_name="The Quay")), owner=OWNER,
                           extra={"schema": ENTITY_SCHEMA}), None)
    txn.commit(device.table.owner)
    app = AppApi(device.api)
    clock = app.call("chronicle.clock", {"campaign": c, **DIRECTOR})
    etag = app.call("chronicle.clock_commit", {
        "campaign": c, **DIRECTOR, "operation": "initialize", "operation_id": "start", "expected": clock["etag"],
        "provider": "gregorian", "date": "1024-3-1", "minute": 1260})["clock"]["etag"]
    app.call("chronicle.clock_commit", {
        "campaign": c, **DIRECTOR, "operation": "schedule", "operation_id": "tide", "expected": etag,
        "deadline": "low-tide", "label": "The smugglers' boat leaves at low tide", "date": "1024-03-02"})
    return {**cast, "harbor": str(harbor)}


def settings(path: Path, mara: str, **extra) -> Path:
    raw = {"provider_id": "prep", "base_url": "https://prep.example/v1", "model": "prep-model",
           "max_output_tokens": 800, "price_prompt": "3", "price_reply": "15", "preparation": 1,
           "agendas": [{"actor": mara, "goals": [{"id": "hide-coin", "aim": AIM, "priority": 2,
                                                  "approaches": [{"id": "deflect", "action": "change the subject"}]}]}],
           **extra}
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


def configured(device, path: Path, replies) -> ScriptedTransport:
    """The model settings ``path`` over a scripted transport answering ``replies`` in turn."""
    transport = ScriptedTransport(list(replies))
    providers.configure(device.api, path, environ={}, transport=transport)
    return transport


def note(text: str, *evidence: str) -> dict:
    return {"text": text, "evidence": list(evidence)}


def draft(mara: str, harbor: str, ids: dict[str, str]) -> dict:
    """A reply that grounds every item in the snapshot's evidence ids (``ids``: kind -> one evidence id)."""
    return {
        "premise": note("Mara waits on the quay as the smugglers' boat readies", ids["choice"], ids["goal"]),
        "cast": [mara], "location": harbor,
        "questions": [note("Who paid the harbor master, and does Mara know?", ids["secret"])],
        "clues": [note("Oil-stained rope coils by the lighthouse steps", ids["secret"])],
        "tensions": [note("Low tide comes before dawn", ids["calendar"])],
        "stakes": [note("If the guild hears of the coin, Mara loses her berth", ids["goal"])],
        "directions": [note("Let Anya follow the oil", ids["secret"]),
                       note("Let Anya walk away from the quay", ids["choice"])],
    }


def reply(body: dict, **extra) -> Reply:
    return Reply(deltas=(json.dumps(body),), usage=dict(USAGE), **extra)


def ids_of(evidence: list[dict]) -> dict[str, str]:
    found: dict[str, str] = {}
    for item in evidence:
        found.setdefault(item["kind"], item["evidence"])
    return found


__all__ = ["AIM", "BRIEF", "DIRECTOR", "REFUTED", "SECRET", "TOLD", "USAGE", "cast_of", "configured", "draft",
           "ids_of", "note", "reply", "settings", "stocked"]
