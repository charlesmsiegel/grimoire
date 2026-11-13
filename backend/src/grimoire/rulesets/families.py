"""Four deliberately incompatible reference profiles (Sprint 062).

These check recorded proposals, never roll, judge fictional applicability, choose
for a player, or apply an effect. Bounds are prototype input limits, not claims
that all editions or exceptional game effects fit these profiles.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


def integer(value: object, label: str, low: int, high: int) -> None:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{label} must be an integer in {low}..{high}')


def text(value: object, label: str) -> None:
    if type(value) is not str or not value.strip() or len(value) > 2000:
        raise ValueError(f'{label} must be nonempty text of at most 2000 characters')


def choice(value: object, allowed: tuple[str, ...], label: str) -> None:
    if type(value) is not str or value not in allowed:
        raise ValueError(f'unsupported {label}')


def dice(value: object, sides: int, maximum: int) -> None:
    if type(value) is not tuple or len(value) > maximum:
        raise ValueError('dice must be a bounded immutable tuple')
    for face in value:
        integer(face, 'die face', 1, sides)


@dataclass(frozen=True)
class D20Check:
    ability: str
    skill: str
    dc: int
    modifier: int

    def __post_init__(self) -> None:
        choice(self.ability, ('strength', 'dexterity', 'constitution', 'intelligence',
                              'wisdom', 'charisma'), 'ability')
        text(self.skill, 'skill')
        integer(self.dc, 'DC', 1, 100)
        integer(self.modifier, 'modifier', -100, 100)


@dataclass(frozen=True)
class D20Result:
    die: int
    total: int
    success: bool

    def __post_init__(self) -> None:
        integer(self.die, 'd20', 1, 20)
        integer(self.total, 'total', -99, 120)
        if type(self.success) is not bool:
            raise ValueError('success must be boolean')


@dataclass(frozen=True)
class WodTest:
    attribute: str
    skill: str
    pool: int
    hunger: int
    difficulty: int

    def __post_init__(self) -> None:
        text(self.attribute, 'attribute')
        text(self.skill, 'skill')
        integer(self.pool, 'pool', 1, 30)
        integer(self.hunger, 'Hunger dice count', 0, min(5, self.pool))
        integer(self.difficulty, 'difficulty', 1, 60)


WodOutcome = Literal['success', 'critical', 'messy-critical', 'failure', 'bestial-failure']


@dataclass(frozen=True)
class WodResult:
    normal: tuple[int, ...]
    hunger: tuple[int, ...]
    successes: int
    outcome: WodOutcome

    def __post_init__(self) -> None:
        dice(self.normal, 10, 30)
        dice(self.hunger, 10, 5)
        integer(self.successes, 'successes', 0, 60)
        choice(self.outcome, ('success', 'critical', 'messy-critical', 'failure',
                              'bestial-failure'), 'V5 outcome')


@dataclass(frozen=True)
class PbtaMove:
    """A proposed Defy Danger trigger, not a finding that the move applies."""
    trigger: str
    stat: str
    modifier: int

    def __post_init__(self) -> None:
        text(self.trigger, 'proposed fictional trigger')
        choice(self.stat, ('strength', 'dexterity', 'constitution', 'intelligence',
                           'wisdom', 'charisma'), 'stat')
        integer(self.modifier, 'modifier', -100, 100)


@dataclass(frozen=True)
class PbtaResult:
    dice: tuple[int, int]
    total: int
    outcome: Literal['miss', 'partial', 'strong']
    choice: str | None = None

    def __post_init__(self) -> None:
        dice(self.dice, 6, 2)
        if len(self.dice) != 2:
            raise ValueError('Defy Danger needs exactly two recorded d6')
        integer(self.total, 'total', -98, 112)
        choice(self.outcome, ('miss', 'partial', 'strong'), 'move outcome')
        if self.outcome == 'partial':
            text(self.choice, 'unresolved hard bargain, worse outcome or ugly choice')
        elif self.choice is not None:
            raise ValueError('this profile only carries a choice for a partial result')


@dataclass(frozen=True)
class ArsStudy:
    """An uninterrupted season of Art summa study without virtue/flaw modifiers.

    Book access, literacy, season availability and other fictional prerequisites
    await the trusted adjudicator. No reservation or advancement occurs here.
    """
    art: str
    starting_xp: int
    level: int
    quality: int
    year: int
    season: str

    def __post_init__(self) -> None:
        choice(self.art, ('creo', 'intellego', 'muto', 'perdo', 'rego', 'animal',
                          'aquam', 'auram', 'corpus', 'herbam', 'ignem', 'imaginem',
                          'mentem', 'terram', 'vim'), 'Art')
        integer(self.starting_xp, 'starting XP', 0, 1_000_000)
        integer(self.level, 'summa level', 1, 100)
        integer(self.quality, 'summa quality', 1, 100)
        integer(self.year, 'year', 1, 9999)
        choice(self.season, ('spring', 'summer', 'autumn', 'winter'), 'season')
        if self.starting_xp >= self.level * (self.level + 1) // 2:
            raise ValueError('summa cannot advance this Art further')


@dataclass(frozen=True)
class ArsResult:
    xp: int
    year: int
    season: str

    def __post_init__(self) -> None:
        integer(self.xp, 'proposed XP', 1, 100)
        integer(self.year, 'year', 1, 9999)
        choice(self.season, ('spring', 'summer', 'autumn', 'winter'), 'season')


FamilyAction = D20Check | WodTest | PbtaMove | ArsStudy
FamilyResult = D20Result | WodResult | PbtaResult | ArsResult
ACTION_TYPES = {'d20': D20Check, 'wod': WodTest, 'pbta': PbtaMove, 'ars': ArsStudy}
RESULT_TYPES = {D20Check: D20Result, WodTest: WodResult, PbtaMove: PbtaResult, ArsStudy: ArsResult}


def check_result(action: FamilyAction, result: FamilyResult) -> None:
    """Reject impossible records; a valid record remains only a proposal."""
    if type(action) not in RESULT_TYPES or type(result) is not RESULT_TYPES[type(action)]:
        raise ValueError('result family does not match action')
    if isinstance(action, D20Check) and isinstance(result, D20Result):
        total = result.die + action.modifier
        valid = result.total == total and result.success == (total >= action.dc)
    elif isinstance(action, WodTest) and isinstance(result, WodResult):
        faces = result.normal + result.hunger
        successes = sum(face >= 6 for face in faces) + 2 * (faces.count(10) // 2)
        if successes >= action.difficulty:
            outcome = ('messy-critical' if 10 in result.hunger else 'critical') \
                if faces.count(10) >= 2 else 'success'
        else:
            outcome = 'bestial-failure' if 1 in result.hunger else 'failure'
        valid = (len(faces) == action.pool and len(result.hunger) == action.hunger
                 and result.successes == successes and result.outcome == outcome)
    elif isinstance(action, PbtaMove) and isinstance(result, PbtaResult):
        total = sum(result.dice) + action.modifier
        outcome = 'strong' if total >= 10 else 'partial' if total >= 7 else 'miss'
        valid = result.total == total and result.outcome == outcome
    elif isinstance(action, ArsStudy) and isinstance(result, ArsResult):
        award = min(action.quality, action.level * (action.level + 1) // 2 - action.starting_xp)
        valid = (result.xp == award and result.year == action.year and result.season == action.season)
    else:
        raise ValueError('unsupported action')
    if not valid:
        raise ValueError('recorded result contradicts the selected action profile')
