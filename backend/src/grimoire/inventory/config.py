"""Per-campaign inventory configuration, read from the campaign config block."""

from __future__ import annotations

from dataclasses import dataclass, field

_DEFAULT_FUNGIBLES = frozenset({"gold", "silver", "coins", "arrows", "rations", "torches"})


@dataclass(frozen=True)
class InventoryConfig:
    enabled: bool = False
    flag_threshold: float = 0.6
    fungible_resources: frozenset[str] = field(default_factory=lambda: _DEFAULT_FUNGIBLES)

    @classmethod
    def from_campaign_config(cls, campaign_config: dict | None) -> InventoryConfig:
        block = (campaign_config or {}).get("inventory") or {}
        extra = {str(x).strip().lower() for x in block.get("fungible_resources", [])}
        return cls(
            enabled=bool(block.get("enabled", False)),
            flag_threshold=float(block.get("flag_threshold", 0.6)),
            fungible_resources=_DEFAULT_FUNGIBLES | extra,
        )
