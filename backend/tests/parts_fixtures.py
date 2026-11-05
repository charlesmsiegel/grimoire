"""Holdings and ability parts of accepted effects (021 next slice): synthetic proposals over the 021 cast.

Orin is an uncontrolled character with a thruster pack and 3 fuel; Wren (a PC, ``human:ana``) has 5 coins
and Tam (director) a knife. Every name is synthetic.
"""

from __future__ import annotations

from grimoire.core.ability_use import AbilityAttempt, assess_attempt
from grimoire.core.entity import EntityInstance, effective_entity
from grimoire.core.identity import InstanceRef
from grimoire.effects import ActedLine, EffectHoldings, EffectProposal, Op
from grimoire.holdings import Line, Operation

from ability_fixtures import index_of, iris, person, thruster_pack
from effects_fixtures import C, CHAT, TAM, WREN, moment

ORIN = InstanceRef(C, "character", "orin")
KNIFE = InstanceRef(C, "item", "knife")


def assessment(actor: InstanceRef = ORIN, *, attempt_id: str = "hop-1", definition=None, ability_id="thruster_pack",
               needs=("thrust", "flight"), effect_id="lift", conditions=("fuel",), provenance="scene:s1/t7"):
    """025's assessment of one labelled use by ``actor`` (an instance of ``definition``, by default Orin's pack)."""
    definition = definition or person("orin", "Orin", thruster_pack())
    entity = effective_entity(EntityInstance(actor, definition.ref), index_of(definition))
    return assess_attempt(AbilityAttempt(attempt_id, actor, ability_id, frozenset(needs), provenance, effect_id,
                                         frozenset(conditions)), entity)


def veil(actor: InstanceRef = WREN, attempt_id: str = "c7"):
    """Iris's light-bending veil, used by ``actor`` (a known capability charging ``eye_strain``)."""
    return assessment(actor, attempt_id=attempt_id, definition=iris(), ability_id="light_bending",
                      needs=("light", "concealment"), effect_id="veil", conditions=("ambient_light",))


def hop(*, sequence: int = 7, attempt_id: str = "hop-1", fuel: int = 1, ops=(), abilities=None,
        turn: str | None = None) -> EffectProposal:
    """Orin hops with the thruster pack: it charges cooldown and fuel_burn and consumes the fuel it burns."""
    return EffectProposal(C, CHAT, moment(sequence, turn), "Orin hops onto the roof.", tuple(ops), "director",
                          attempt_id=attempt_id, holdings=EffectHoldings((Line.consume(ORIN, "fuel", fuel),)),
                          abilities=(assessment(attempt_id=attempt_id),) if abilities is None else abilities)


def pay(amount: int = 2, *, backed: str | None = "c7", attempt_id: str = "c7", sequence: int = 7,
        agent: InstanceRef | None = WREN) -> EffectProposal:
    """Wren pays Tam ``amount`` coins (her voluntary act, backed by her contribution)."""
    line = ActedLine(Line.move(WREN, TAM, "coin", amount), agent, backed)
    return EffectProposal(C, CHAT, moment(sequence), "Wren pays Tam.", (), "player", attempt_id=attempt_id,
                          holdings=EffectHoldings((line,), "a fare"))


def trade(*, tam_backed: str | None = None, sequence: int = 7) -> EffectProposal:
    """Wren pays Tam 2 coins while Tam hands Wren the knife: two lines, two agents."""
    lines = (ActedLine(Line.move(WREN, TAM, "coin", 2), WREN, "c7"),
             ActedLine(Line.move(TAM, WREN, "knife", 1), TAM, tam_backed))
    return EffectProposal(C, CHAT, moment(sequence), "Wren buys Tam's knife.", (), "player", attempt_id="c7",
                          holdings=EffectHoldings(lines))


def meet() -> Op:
    return Op("begin", "has_met", TAM, ORIN)


def seed_holdings(store, holdings) -> None:
    """Orin's fuel, Wren's coins and Tam's knife, as one direct 023 operation."""
    holdings.apply(Operation("seed", (Line.add(ORIN, "fuel", 3), Line.add(WREN, "coin", 5),
                                      Line.add(TAM, "knife", 1)))).commit()
