"""Coherent read-only input to 047 and its freshness fence.

A mismatched stage is deliberately unavailable, not silently resequenced. Fingerprints cover the
actual latest stage anyway, so changing it invalidates a review even when it was not checkable.
"""
from __future__ import annotations

import hashlib
import json

from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum

from grimoire.core.authority import NarrationContext, PlayerContribution
from grimoire.holdingstore import HoldingsLibrary
from grimoire.possessionstore import PossessionLibrary
from grimoire.referee import Authority, Facts
from grimoire.play.scene import _no_clock, _refuse


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def canonical(value):
    """Canonical immutable domain values, excluding only derived/private caches."""
    if value is None or type(value) in (str, int, float, bool):
        return value
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {field.name: canonical(getattr(value, field.name)) for field in fields(value)
                if not field.name.startswith("_")}
    if isinstance(value, Mapping):
        return {str(key): canonical(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (set, frozenset)):
        return sorted((canonical(item) for item in value), key=lambda item: digest(item))
    if isinstance(value, (list, tuple)):
        return [canonical(item) for item in value]
    raise ValueError("unfingerprintable_fact")


@dataclass(frozen=True)
class Snapshot:
    facts: Facts
    fingerprint: str
    stage_unavailable: bool
    targets: tuple
    names: tuple


def snapshot(runner, context, *, choices=()) -> Snapshot:
    """Caller holds the store lock; capture includes only trusted read services, never generated labels."""
    scene = context.scene
    campaign = scene.chat.campaign_id
    ledger, moment = runner._knowledge(scene.chat)
    holdings = HoldingsLibrary(runner.store, runner.handle, clock=_no_clock, owner=runner.owner).campaign_holdings(campaign)
    items = PossessionLibrary(runner.store, runner.handle, clock=_no_clock, owner=runner.owner).campaign_items(campaign)
    accepted = runner.store.campaign_accepted(campaign)
    if ledger.quarantined or ledger.problems or holdings.problems or items.problems or accepted.problems:
        raise _refuse("continuity_damaged", "continuity facts cannot be read in full")
    resolutions = () if runner.adjudication is None else runner.adjudication.resolutions(campaign)
    obstacles = () if runner.obstacles is None else runner.obstacles.states(campaign)
    stage = scene.stage
    stale_stage = stage is not None and stage.as_of != moment
    facts = Facts(campaign, moment, stage=None if stale_stage else stage,
                  holdings=tuple(view.holdings for view in holdings.views.values()),
                  possessions=tuple(view.possession for view in items.views.values()),
                  knowledge=ledger.knowledge, resolutions=resolutions, obstacles=obstacles,
                  intentions=tuple(choices))
    targets = tuple(sorted((ref for ref in context.view.instances if ref.campaign_id == campaign), key=str))
    # Canonical values rather than repr: identities, caches and insertion order cannot move the fence.
    # Choices are checked from the exact brief but fingerprinted via their input records. This lets keep
    # recompute freshness without recreating a model-selected choice or storing private briefs.
    facts_without_choices = Facts(campaign, moment, stage=None if stale_stage else stage,
                                 holdings=facts.holdings, possessions=facts.possessions, knowledge=facts.knowledge,
                                 resolutions=resolutions, obstacles=obstacles)
    fingerprint = digest(canonical({"facts": facts_without_choices, "stage": stage, "controls": scene.controls,
                                    "cast": scene.cast, "names": scene.names, "accepted": accepted,
                                    "instances": {str(ref): context.view.instances[ref] for ref in targets},
                                    "agendas": runner._agendas,
                                    "adjudication": runner.adjudication is not None,
                                    "obstacles_available": runner.obstacles is not None}))
    return Snapshot(facts, fingerprint, stale_stage, targets, tuple(scene.names.items()))


def authority_for(record, scene) -> Authority:
    contributions = ()
    if record.post is not None:
        post = record.post
        contributions = (PlayerContribution(record.chat.campaign_id, record.turn_id, record.chat.chat_id,
                                             record.turn_id, post.actor.instance_id, post.submitted_by,
                                             post.kind, post.text),)
    return Authority(NarrationContext(record.chat.campaign_id, record.chat.chat_id, record.turn_id),
                     scene.controls, contributions=contributions)
