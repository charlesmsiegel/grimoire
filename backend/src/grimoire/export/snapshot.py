"""Assemble a read-only campaign view that adapters can iterate over.

The snapshot collapses every piece of state an adapter could possibly need
into a frozen-ish dataclass. Each adapter then formats from it without
touching the live data sources — making exports cheaply re-runnable and
keeping the EPUB / markdown / JSON adapters from re-querying every source.

The builder filters scenes by ``ExportSelection`` (id list, date range,
skip-tags), applies the prose transformation pipeline, and resolves the
illustrations linked to each scene.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from grimoire.export.filters import FilterContext, apply_filters
from grimoire.export.sources import DataSources

if TYPE_CHECKING:
    from grimoire.export.config import ExportFiltersConfig
from grimoire.scenes.types import Post, Scene
from grimoire.types.characters import Character
from grimoire.types.common import CampaignId, CharacterRef, InGameTime
from grimoire.types.composition import Greeting, LibraryEntity, WorldMeta
from grimoire.types.continuity import Commitment, Fact
from grimoire.types.export import ExportOptions, ExportSelection
from grimoire.types.imagegen import ImageMetadata


@dataclass(slots=True)
class FormattedPost:
    order: int
    author_kind: str
    author_label: str
    author_display: str
    body: str

    @property
    def is_player(self) -> bool:
        return self.author_kind == "pc"


@dataclass(slots=True)
class ScenePart:
    scene: Scene
    posts: list[FormattedPost] = field(default_factory=list)
    images: list[ImageMetadata] = field(default_factory=list)
    word_count: int = 0


@dataclass(slots=True)
class CampaignSnapshot:
    campaign_id: CampaignId
    branch_id: str
    selection: ExportSelection
    options: ExportOptions

    scenes: list[ScenePart] = field(default_factory=list)
    characters: list[Character] = field(default_factory=list)
    worlds: list[WorldMeta] = field(default_factory=list)
    locations: list[LibraryEntity] = field(default_factory=list)
    lore: list[LibraryEntity] = field(default_factory=list)
    factions: list[LibraryEntity] = field(default_factory=list)
    items: list[LibraryEntity] = field(default_factory=list)
    greetings: list[Greeting] = field(default_factory=list)
    facts: list[Fact] = field(default_factory=list)
    commitments: list[Commitment] = field(default_factory=list)
    images: list[ImageMetadata] = field(default_factory=list)
    pc_names: dict[CharacterRef, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    data_root: Path | None = None

    @property
    def scene_count(self) -> int:
        return len(self.scenes)

    @property
    def word_count(self) -> int:
        return sum(part.word_count for part in self.scenes)

    @property
    def image_count(self) -> int:
        return len(self.images)


def _count_words(body: str) -> int:
    return len([token for token in body.split() if token.strip()])


def _author_display(post: Post, pc_names: dict[str, str]) -> str:
    if post.author_kind == "pc" and post.author_pc_ref:
        return pc_names.get(post.author_pc_ref, post.author_pc_ref)
    if post.author_kind == "npc" and post.author_npc_ref:
        return post.author_npc_ref
    if post.author_kind == "narrator":
        return "Narrator"
    if post.author_kind == "system":
        return "System"
    return post.author_label


def _in_range(scene: Scene, window: tuple[InGameTime, InGameTime] | None) -> bool:
    if window is None:
        return True
    if scene.in_game_start is None:
        return False
    lo, hi = window
    moment = scene.in_game_start
    if isinstance(lo, InGameTime):
        lo_dt: datetime | None = lo.moment
    else:
        lo_dt = lo
    if isinstance(hi, InGameTime):
        hi_dt: datetime | None = hi.moment
    else:
        hi_dt = hi
    if lo_dt is not None and moment < lo_dt:
        return False
    return not (hi_dt is not None and moment > hi_dt)


def _apply_audience_policy(appendices: set[str], audience: str | None) -> set[str]:
    """Filter appendices to honour the audience mode (spec 13 §15).

    - ``"share"`` excludes the ``continuity`` appendix even when explicitly
      requested, so OOC fact / commitment ledgers don't leak into share-
      style exports.
    - ``"personal"`` and ``None`` leave the selection untouched. The
      current explicit-opt-in default for continuity is preserved.
    """
    if audience == "share" and "continuity" in appendices:
        return {a for a in appendices if a != "continuity"}
    return appendices


def _consolidate_by_pov(
    posts: list[FormattedPost], mode: str
) -> list[FormattedPost]:
    """Merge runs of consecutive posts that share an author.

    Spec 13 §Filter and transformation hooks lists POV consolidation as the
    sixth filter. We treat it as structural rather than text-level: when
    enabled, neighbouring posts with the same author_kind / display name
    are concatenated body-wise (separated by a blank line) and the first
    post's labelling is kept. ``mode == "off"`` is a no-op.
    """
    if mode == "off" or len(posts) < 2:
        return posts

    def key(post: FormattedPost) -> tuple[str, ...]:
        if mode == "by_kind":
            return (post.author_kind,)
        if mode == "by_author":
            return (post.author_kind, post.author_display)
        return ("__unknown__", str(id(post)))

    merged: list[FormattedPost] = []
    for post in posts:
        if merged and key(post) == key(merged[-1]):
            prev = merged[-1]
            prev.body = f"{prev.body}\n\n{post.body}".strip()
            continue
        merged.append(post)
    return merged


def _scene_skipped_by_tags(scene: Scene, skip_tags: list[str]) -> bool:
    if not skip_tags:
        return False
    skip_set = {t for t in skip_tags}
    return any(tag in skip_set for tag in scene.tags)


def _scene_matches_arcs(scene: Scene, arcs: list[str] | None) -> bool:
    """Return True when ``scene`` carries any of the requested arc tags.

    Uses the ``arc:<id>`` tag convention (spec 13 §Per-scene and per-arc):
    selecting arc ``saint-werewolf`` matches scenes tagged
    ``arc:saint-werewolf``. ``None`` / empty list means "no arc filter".
    """

    if not arcs:
        return True
    needles = {f"arc:{arc}" for arc in arcs}
    return any(tag in needles for tag in scene.tags)


async def build_snapshot(
    campaign_id: CampaignId,
    selection: ExportSelection,
    options: ExportOptions,
    sources: DataSources,
    *,
    filter_defaults: ExportFiltersConfig | None = None,
) -> CampaignSnapshot:
    """Collect every piece of state the adapters need.

    Adapter authors should never reach past this object back into the live
    sources — that keeps each format's emit pass deterministic and trivially
    re-runnable from a recorded snapshot.
    """

    # Pull the small things up front so we can label posts and skip filtered
    # scenes consistently.
    pc_names = await sources.pcs.pc_names(campaign_id)
    filter_ctx = _build_filter_context(selection, options, pc_names, filter_defaults)

    all_scenes = await sources.scenes.list_scenes(campaign_id, selection.branch_id)
    selected_ids = set(selection.scene_ids or [])
    snapshot = CampaignSnapshot(
        campaign_id=campaign_id,
        branch_id=selection.branch_id,
        selection=selection,
        options=options,
        pc_names=pc_names,
        data_root=sources.data_root,
    )

    for scene in all_scenes:
        if selected_ids and scene.id not in selected_ids:
            continue
        if not selection.include_drafts and scene.post_count == 0:
            continue
        if not _in_range(scene, selection.date_range):
            continue
        if _scene_skipped_by_tags(scene, filter_ctx.skip_tags):
            continue
        if not _scene_matches_arcs(scene, selection.arcs):
            continue

        posts = await sources.scenes.get_posts(scene.id)
        formatted_posts: list[FormattedPost] = []
        for post in posts:
            body = apply_filters(post.body, filter_ctx)
            if not body:
                continue
            formatted_posts.append(
                FormattedPost(
                    order=post.order_in_scene,
                    author_kind=str(post.author_kind),
                    author_label=post.author_label,
                    author_display=_author_display(post, pc_names),
                    body=body,
                )
            )
        formatted_posts = _consolidate_by_pov(
            formatted_posts, filter_ctx.pov_consolidation_mode
        )

        scene_images: list[ImageMetadata] = []
        if selection.include_images:
            try:
                scene_images = await sources.images.list_images(campaign_id, scene_id=scene.id)
            except NotImplementedError:
                scene_images = []

        part = ScenePart(
            scene=scene,
            posts=formatted_posts,
            images=scene_images,
            word_count=sum(_count_words(fp.body) for fp in formatted_posts),
        )
        snapshot.scenes.append(part)

    appendices = _apply_audience_policy(
        {a for a in selection.include_appendices},
        selection.audience,
    )
    if "cast" in appendices:
        snapshot.characters = list(await sources.characters.list_for_campaign(campaign_id))
        if filter_ctx.anonymize:
            snapshot.characters = [
                _anonymize_character(ch, filter_ctx.anonymize) for ch in snapshot.characters
            ]
    if any(name in appendices for name in ("world", "locations", "lore", "factions", "items")):
        snapshot.worlds = list(await sources.world.get_composition_worlds(campaign_id))
        for st in snapshot.worlds:
            if "locations" in appendices or "world" in appendices:
                snapshot.locations.extend(await sources.world.list_in_world(st.id, "location"))
            if "lore" in appendices or "world" in appendices:
                snapshot.lore.extend(await sources.world.list_in_world(st.id, "lore"))
            if "factions" in appendices or "world" in appendices:
                snapshot.factions.extend(await sources.world.list_in_world(st.id, "faction"))
            if "items" in appendices or "world" in appendices:
                snapshot.items.extend(await sources.world.list_in_world(st.id, "item"))
            if "greetings" in appendices:
                snapshot.greetings.extend(await sources.world.list_greetings(st.id))
    if "continuity" in appendices:
        snapshot.facts = list(await sources.continuity.list_facts(campaign_id))
        snapshot.commitments = list(await sources.continuity.list_commitments(campaign_id))
    if "gallery" in appendices and selection.include_images:
        snapshot.images = list(await sources.images.list_images(campaign_id))

    return snapshot


def _build_filter_context(
    selection: ExportSelection,
    options: ExportOptions,
    pc_names: dict[str, str],
    defaults: ExportFiltersConfig | None = None,
) -> FilterContext:
    extra = dict(options.extra or {})
    filters_meta = dict(selection.filters or {})

    # Per spec 13 §Configuration: ``filters.*_default`` are honoured only when
    # ``selection.filters`` does not override the same key.
    strip_ooc_default = True if defaults is None else defaults.strip_ooc_default
    strip_mech_default = False if defaults is None else defaults.strip_mechanics_default
    strip_narr_default = (
        True if defaults is None else defaults.strip_narrator_scaffolding_default
    )
    anonymize_default = False if defaults is None else defaults.anonymize_default

    ctx = FilterContext(
        strip_ooc=bool(filters_meta.get("strip_ooc", strip_ooc_default)),
        strip_mechanics=bool(filters_meta.get("strip_mechanics", strip_mech_default)),
        strip_narrator_scaffolding=bool(
            filters_meta.get("strip_narrator_scaffolding", strip_narr_default)
        ),
        skip_tags=list(filters_meta.get("skip_tags") or []),
        pov_consolidation_mode=str(filters_meta.get("pov_consolidation", "off")),
    )

    anonymize = dict(filters_meta.get("anonymize") or {})
    # Convenience: ``anonymize_pcs`` swaps every PC display name for the same
    # pseudonym. Existing explicit mappings always win.
    pc_pseudonym = filters_meta.get("anonymize_pcs") or extra.get("anonymize_pcs")
    if pc_pseudonym is None and anonymize_default:
        pc_pseudonym = "Anon"
    if pc_pseudonym:
        for ref, name in pc_names.items():
            anonymize.setdefault(name, str(pc_pseudonym))
            anonymize.setdefault(ref, str(pc_pseudonym))
    ctx.anonymize = anonymize
    return ctx


def _anonymize_character(character: Character, mapping: dict[str, str]) -> Character:
    if character.name not in mapping:
        return character
    replacement = mapping[character.name]
    return character.model_copy(update={"name": replacement})


__all__ = [
    "CampaignSnapshot",
    "FormattedPost",
    "ScenePart",
    "build_snapshot",
]
