"""Per-campaign inventory configuration, read from the campaign config block."""

from __future__ import annotations

from pydantic import BaseModel, Field

_DEFAULT_FUNGIBLES: frozenset[str] = frozenset(
    {"gold", "silver", "coins", "arrows", "rations", "torches"}
)


class InventoryConfig(BaseModel):
    """Validated per-campaign inventory settings.

    Built from the optional ``inventory:`` block in ``campaign.yaml``. That is
    an external, user-supplied input boundary, so it goes through Pydantic for
    coercion/validation and defaults rather than hand-rolled casts.
    """

    model_config = {"frozen": True}

    enabled: bool = False
    flag_threshold: float = 0.6
    fungible_resources: frozenset[str] = Field(default_factory=lambda: _DEFAULT_FUNGIBLES)

    @classmethod
    def from_campaign_config(cls, campaign_config: dict | None) -> InventoryConfig:
        block = (campaign_config or {}).get("inventory") or {}
        extra = {str(x).strip().lower() for x in block.get("fungible_resources", [])}
        return cls(
            enabled=block.get("enabled", False),
            flag_threshold=block.get("flag_threshold", 0.6),
            fungible_resources=_DEFAULT_FUNGIBLES | extra,
        )
