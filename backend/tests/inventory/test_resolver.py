import pytest

from grimoire.inventory.config import InventoryConfig
from grimoire.inventory.resolver import ItemResolver

pytestmark = pytest.mark.asyncio


class FakeStore:
    def __init__(self, existing=None):
        self.existing = existing or {}  # slug -> name
        self.created = []  # (entity_id, name)

    async def find_item_by_name(self, campaign_id, name):
        slug = name.strip().lower().replace(" ", "-")
        if slug in self.existing:
            return {"item_ref": slug, "item_name": self.existing[slug]}
        return None

    async def create_emergent_item(self, campaign_id, name, *, source, turn_id=None):
        slug = name.strip().lower().replace(" ", "-")
        self.created.append((slug, name))
        return slug


def _cfg():
    return InventoryConfig(enabled=True)


async def test_fungible_keyword_resolves_to_resource():
    r = ItemResolver(FakeStore(), _cfg())
    ref, name, fungible = await r.resolve("c1", "120 gold", turn_id=None)
    assert ref == "resource:gold"
    assert fungible is True


async def test_existing_item_match():
    store = FakeStore(existing={"silver-ring": "Silver Ring"})
    r = ItemResolver(store, _cfg())
    ref, name, fungible = await r.resolve("c1", "silver ring", turn_id=None)
    assert ref == "silver-ring"
    assert fungible is False
    assert store.created == []


async def test_unknown_item_auto_creates_emergent():
    store = FakeStore()
    r = ItemResolver(store, _cfg())
    ref, name, fungible = await r.resolve("c1", "rusty key", turn_id="t1")
    assert ref == "rusty-key"
    assert store.created == [("rusty-key", "rusty key")]
