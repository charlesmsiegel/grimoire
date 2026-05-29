from grimoire.inventory.config import InventoryConfig

DEFAULT_FUNGIBLES = {"gold", "silver", "coins", "arrows", "rations", "torches"}


def test_defaults_disabled():
    cfg = InventoryConfig.from_campaign_config(None)
    assert cfg.enabled is False
    assert cfg.flag_threshold == 0.6
    assert cfg.fungible_resources >= DEFAULT_FUNGIBLES


def test_reads_campaign_block():
    cfg = InventoryConfig.from_campaign_config(
        {"inventory": {"enabled": True, "flag_threshold": 0.8, "fungible_resources": ["mana"]}}
    )
    assert cfg.enabled is True
    assert cfg.flag_threshold == 0.8
    assert "mana" in cfg.fungible_resources
    assert "gold" in cfg.fungible_resources  # extends defaults, not replaces


def test_ignores_missing_block():
    cfg = InventoryConfig.from_campaign_config({"model_tiers": {}})
    assert cfg.enabled is False
