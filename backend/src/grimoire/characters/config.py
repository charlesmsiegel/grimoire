"""Configuration for :class:`CharactersService` (spec 08 §Configuration).

Tunable knobs are grouped by concern and live on dedicated dataclasses so
the spec's nested namespace (`drift.check_every_n_appearances`, etc.)
maps cleanly. Hook-shaped dependencies (LLM callables, post fetcher,
state-store-backed protocols) stay as ctor kwargs on the service — only
numeric / boolean / string knobs belong here.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DriftConfig:
    threshold: float = 0.4
    check_every_n_appearances: int = 5
    # Informational — the actual model lives on the injected ``DriftChecker``'s
    # gateway task name. Carried here so config dumps show the intent.
    check_model: str = ""


@dataclass
class TierConfig:
    demote_to_background_after_turns: int = 3
    demote_to_archive_after_turns: int = 10


@dataclass
class VoiceAnchorConfig:
    # When True, ``get_*_card(seed=...)`` rotates ``voice.samples`` before
    # the slice. When False, callers passing a seed get the same output as
    # ``seed=None`` (rotation disabled at the policy level).
    sample_dialogue_rotation: bool = True
    max_samples: int = 5


@dataclass
class CapsulesConfig:
    # When False, ``create_emergent`` skips the auto-capsule LLM call even
    # when ``auto_capsule_llm`` is wired and the payload is sparse.
    auto_generate: bool = True


@dataclass
class PromotionConfig:
    # When True, ``promote_to_library`` requires ``confirm=True``; the
    # propose/commit two-step flow is enforced. Set False for tools that
    # want the single-shot programmatic path.
    require_confirmation: bool = True


@dataclass
class CrossWorldLookupConfig:
    # When False, ``cross_world_lookup`` slug-normalizes the asset id
    # (lower-cased, non-alphanumeric → ``-``) before consulting the
    # Library, so ``Alistair-Hyde-Smythe`` finds ``alistair-hyde-smythe``.
    case_sensitive: bool = False


@dataclass
class MultiPCConfig:
    # Informational — current behaviour already matches both flags.
    auto_advance_with_single_pc: bool = True
    require_advance_with_multiple_pcs: bool = True


@dataclass
class CacheConfig:
    max_size: int = 256


@dataclass
class CharactersConfig:
    drift: DriftConfig = None  # type: ignore[assignment]
    tiers: TierConfig = None  # type: ignore[assignment]
    voice_anchor: VoiceAnchorConfig = None  # type: ignore[assignment]
    capsules: CapsulesConfig = None  # type: ignore[assignment]
    promotion: PromotionConfig = None  # type: ignore[assignment]
    cross_world_lookup: CrossWorldLookupConfig = None  # type: ignore[assignment]
    multi_pc: MultiPCConfig = None  # type: ignore[assignment]
    cache: CacheConfig = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.drift is None:
            self.drift = DriftConfig()
        if self.tiers is None:
            self.tiers = TierConfig()
        if self.voice_anchor is None:
            self.voice_anchor = VoiceAnchorConfig()
        if self.capsules is None:
            self.capsules = CapsulesConfig()
        if self.promotion is None:
            self.promotion = PromotionConfig()
        if self.cross_world_lookup is None:
            self.cross_world_lookup = CrossWorldLookupConfig()
        if self.multi_pc is None:
            self.multi_pc = MultiPCConfig()
        if self.cache is None:
            self.cache = CacheConfig()


__all__ = [
    "CacheConfig",
    "CapsulesConfig",
    "CharactersConfig",
    "CrossWorldLookupConfig",
    "DriftConfig",
    "MultiPCConfig",
    "PromotionConfig",
    "TierConfig",
    "VoiceAnchorConfig",
]
