"""Sprint 115's source catalogs (056) as the Prep page's ``prep.catalogs``.

Synthetic catalogs are published with 056's own ``publish_catalog`` into the desktop library's card sources; the op
composes each with 056's ``compose_catalogs`` and shows what the campaign's chats bind from them: each binding's
source-qualified ref and pinned revision, which catalogs make that exact revision available, which exclude it, and
the tokens each NPC's context pack takes from it now. The op is the director's and writes nothing. No model is
called.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from prep_app_fixtures import DIRECTOR, cast_of
from scene_fixtures import ANYA, MARA_LORE, MARA_VELL
from store_helpers import OWNER
from textapi_fixtures import Device, desktop_library, keep, say

from grimoire.app.appapi import AppApi
from grimoire.catalogs import Catalog, CatalogError, publish_catalog
from grimoire.catalogs.model import SCHEMA, catalog_envelope
from grimoire.core.identity import InstanceRef, SourceRef
from grimoire.store import RecordEnvelope
from grimoire.testing.fileops import snapshot as files_of
from grimoire.textapi import ApiRefused

MARA_SOURCE, ANYA_SOURCE = MARA_VELL.source, ANYA.source
HARBOR = SourceRef(MARA_SOURCE, "catalog", "harbor", "r1")
ROOKS = SourceRef(ANYA_SOURCE, "catalog", "rooks", "r1")
TORN = SourceRef(ANYA_SOURCE, "catalog", "torn", "r1")
SHAPELESS = SourceRef(MARA_SOURCE, "catalog", "shapeless", "r1")


@pytest.fixture
def desk(tmp_path):
    return desktop_library(tmp_path / "desk")


def refused(app: AppApi, op: str, **args) -> ApiRefused:
    with pytest.raises(ApiRefused) as caught:
        app.call(op, args)
    return caught.value


def pinned(ref):
    return ref if ref.pinned else ref.at("r1")


def publish(device, catalog: Catalog, name=None) -> None:
    store = device.table.store
    publish_catalog(store, device.table.owner, catalog, expected=store.read_source(catalog.ref.source).etag, name=name)


def damage(device) -> None:
    """Two catalogs 056 would never publish: one built on a catalog that is not in the library, one whose record is
    not a catalog's shape. Written as a damaged or hand-edited library would hold them."""
    store = device.table.store
    txn = store.transaction()
    txn.put(catalog_envelope(Catalog(TORN, (pinned(ANYA),), (SourceRef(MARA_SOURCE, "catalog", "gone", "r1"),)), None), None)
    txn.put(RecordEnvelope(SHAPELESS, {"members": "everything"}, name="Shapeless",
                           extra={"schema": SCHEMA}), None)
    for source, heads in ((ANYA_SOURCE, {"catalog/torn": "r1"}), (MARA_SOURCE, {"catalog/shapeless": "r1"})):
        stored = store.read_source(source)
        txn.put(replace(stored.manifest, heads={**stored.manifest.heads, **heads}), stored.etag)
    txn.commit(device.table.owner)


def played(device, chat) -> dict[str, str]:
    device.api.runner.seat(chat, OWNER)
    device("post", chat=str(chat), text="*I press a coin into Mara's hand*")
    device("generate", chat=str(chat))
    keep(device, str(chat))
    return cast_of(device, chat)


def test_the_catalogs_and_bindings_are_the_directors(desk):
    folder, chat = desk
    with Device(folder, [say("Mara pockets it.")]) as device:
        app = AppApi(device.api)
        for audience in ({}, {"audience": "table"}):
            assert refused(app, "prep.catalogs", campaign=chat.campaign_id, **audience).reason == "director_only"
        assert refused(app, "prep.catalogs", campaign="Not A Campaign", **DIRECTOR).reason == "invalid_field"
        assert refused(app, "prep.catalogs", campaign=7, **DIRECTOR).reason == "invalid_field"
        unknown = app.call("prep.catalogs", {"campaign": "no-such-campaign", **DIRECTOR})
        assert unknown["chats"] == [] and unknown["catalogs"] == []


def test_each_binding_shows_its_pinned_revision_its_catalogs_and_its_cost(desk):
    folder, chat = desk
    with Device(folder, [say("Mara pockets it.")]) as device:
        cast = played(device, chat)
        publish(device, Catalog(HARBOR, (pinned(MARA_VELL), pinned(MARA_LORE)), (), (ANYA.unpinned(),)), name="The harbor")
        publish(device, Catalog(ROOKS, (pinned(ANYA),), (HARBOR,)))
        app = AppApi(device.api)
        before = files_of(device.table.store.root)
        answer = app.call("prep.catalogs", {"campaign": chat.campaign_id, **DIRECTOR})
        assert files_of(device.table.store.root) == before, "reading the catalogs writes nothing"

        catalogs = {item["catalog"]: item for item in answer["catalogs"]}
        harbor, rooks = catalogs[str(HARBOR)], catalogs[str(ROOKS)]
        assert harbor["name"] == "The harbor" and harbor["problem"] is None
        assert harbor["excludes"] == [str(ANYA.unpinned())] and harbor["members"] == 2 and harbor["available"] == 2
        assert harbor["kinds"] == {"character": 1, "lore": 1}
        assert [record["ref"] for record in harbor["records"]] == sorted([str(pinned(MARA_VELL)), str(pinned(MARA_LORE))])
        # a catalog built on another offers both, and says what it composed from
        assert rooks["depends_on"] == [str(HARBOR)] and rooks["available"] == 3
        assert {item["catalog"] for item in rooks["basis"]} == {str(ROOKS), str(HARBOR)}

        (shown,) = answer["chats"]
        assert shown["chat"] == str(chat) and shown["problem"] is None
        bindings = {item["role"]: item for item in shown["bindings"]}
        character = bindings["character"]
        assert character["binding"] == str(pinned(MARA_VELL)) and character["pinned"] is True
        assert character["revision"] == "r1" and character["broken"] is None and character["kind"] == "character"
        assert {item["catalog"] for item in character["available_in"]} == {str(HARBOR), str(ROOKS)}
        # availability is not access: Anya is excluded from the harbor's discovery and her binding still holds
        persona = bindings["persona"]
        assert persona["excluded_by"] == [str(HARBOR)] and persona["broken"] is None
        assert [item["catalog"] for item in persona["available_in"]] == [str(ROOKS)]
        assert bindings["lorebook"]["kind"] == "lore"

        # the cost is what Mara's context pack takes from each record now, by the runner's own estimate
        context = device.api.runner.inspect_context(chat, InstanceRef.parse(cast["Mara Vell"]))
        from_card = sum(int(item["tokens"]) for item in context["items"]
                        if item["provenance"].get("source") == str(pinned(MARA_VELL)))
        assert from_card > 0
        assert character["cost"] == [{"actor": cast["Mara Vell"], "name": "Mara Vell", "tokens": from_card}]
        (npc,) = shown["costs"]
        assert npc["actor"] == cast["Mara Vell"] and npc["tokens"] == context["budget"]["tokens"]
        assert npc["measurement"] == "estimated"
        assert "079" in answer["change"] and "103" in answer["change"] and "Library page" in answer["change"]


def test_a_catalog_that_does_not_compose_is_named_with_056s_reason(desk):
    folder, chat = desk
    with Device(folder, [say("Mara pockets it.")]) as device:
        played(device, chat)
        publish(device, Catalog(HARBOR, (pinned(MARA_VELL),)))
        damage(device)
        with pytest.raises(CatalogError):  # 056 itself refuses to publish what the damaged library holds
            publish(device, Catalog(SourceRef(ANYA_SOURCE, "catalog", "again", "r1"), (pinned(ANYA),),
                                    (SourceRef(MARA_SOURCE, "catalog", "gone", "r1"),)))
        answer = AppApi(device.api).call("prep.catalogs", {"campaign": chat.campaign_id, **DIRECTOR})
        catalogs = {item["catalog"]: item for item in answer["catalogs"]}
        assert catalogs[str(TORN)]["problem"] == "missing_catalog" and catalogs[str(TORN)]["available"] == 0
        assert catalogs[str(SHAPELESS)]["problem"] not in (None, "")
        assert catalogs[str(HARBOR)]["problem"] is None
        # the chats are still shown, and offered only by the catalog that composes
        bindings = {item["role"]: item for item in answer["chats"][0]["bindings"]}
        assert [item["catalog"] for item in bindings["character"]["available_in"]] == [str(HARBOR)]
