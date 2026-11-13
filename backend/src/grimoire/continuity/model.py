"""Pure, revision-pinned director review values; no canonical state or knowledge."""
from __future__ import annotations

import re
from dataclasses import dataclass

MAX_NODES = 2048
MAX_EDGES = 8192
REASONS = frozenset({'scene', 'dialogue', 'evidence', 'summary', 'asset', 'causal_action', 'relationship', 'possession',
                     'accepted_event', 'chronological', 'recorded_source'})
CERTAINTIES = frozenset({'known', 'potential'})


@dataclass(frozen=True, order=True)
class Pin:
    path: str
    revision: str

    def __post_init__(self) -> None:
        parts = self.path.split('/') if type(self.path) is str else []
        flat = {'turns', 'accepted', 'relations', 'events', 'wrapups', 'chats', 'continuity',
                'bookmarks', 'images', 'scope-art'}
        nested = {'instances', 'holdings', 'evidence', 'transcripts'}
        shape = (len(parts) == 4 and parts[2] in flat) or (len(parts) == 5 and parts[2] in nested)
        shape = shape or (len(parts) == 6 and parts[2] == 'placements')
        if not shape or parts[0] != 'campaigns' or not parts[-1].endswith('.json'):
            raise ValueError('a continuity pin names a supported canonical campaign JSON record')
        segments = parts[1:-1] + [parts[-1][:-5]]
        reserved = {'con', 'prn', 'aux', 'nul', *(f'com{i}' for i in range(1, 10)),
                    *(f'lpt{i}' for i in range(1, 10))}
        if any(re.fullmatch(r'[a-z0-9][a-z0-9._-]*', p) is None or len(p) > 128
               or p.endswith('.') or p.split('.')[0] in reserved for p in segments) or len(parts[-1]) > 128:
            raise ValueError('a continuity pin has portable canonical path segments')
        if type(self.revision) is not str or re.fullmatch(r'sha256:[0-9a-f]{64}', self.revision) is None:
            raise ValueError('a continuity pin has a sha256 revision')

    @property
    def campaign_id(self) -> str:
        return self.path.split('/')[1]


@dataclass(frozen=True, order=True)
class Dependency:
    source: Pin
    dependent: Pin
    reason: str
    certainty: str

    def __post_init__(self) -> None:
        if type(self.source) is not Pin or type(self.dependent) is not Pin:
            raise ValueError('dependency endpoints are pins')
        if type(self.reason) is not str or type(self.certainty) is not str or self.reason not in REASONS or self.certainty not in CERTAINTIES:
            raise ValueError('unsupported dependency reason or certainty')
        if self.reason == 'chronological' and self.certainty != 'potential':
            raise ValueError('chronology establishes only potential dependence')


@dataclass(frozen=True)
class Impact:
    target: Pin
    reason: str
    chain: tuple[Pin, ...]
    certainty: str


@dataclass(frozen=True)
class ImpactReport:
    roots: tuple[Pin, ...]
    impacts: tuple[Impact, ...]
    problems: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        return not self.problems


@dataclass(frozen=True)
class RetconRequest:
    action: str
    roots: tuple[Pin, ...]

    def __post_init__(self) -> None:
        if type(self.action) is not str or self.action not in {'edit', 'cut', 'replace', 'replay', 'fork'}:
            raise ValueError('unsupported retcon action')
        if not isinstance(self.roots, (tuple, list)) or len(self.roots) > MAX_NODES:
            raise ValueError('retcon roots are a bounded list')
        roots = tuple(self.roots)
        if not roots or len(roots) > MAX_NODES or any(type(p) is not Pin for p in roots):
            raise ValueError('a retcon identifies bounded original pins')
        if len({p.path for p in roots}) != len(roots) or len({p.campaign_id for p in roots}) != 1:
            raise ValueError('retcon roots are unique and in one campaign')
        object.__setattr__(self, 'roots', tuple(sorted(roots)))


@dataclass(frozen=True)
class Rehearsal:
    request: RetconRequest
    alternatives: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.alternatives, (tuple, list)) or len(self.alternatives) > 8:
            raise ValueError('authored alternatives are a bounded list')
        alternatives = tuple(self.alternatives)
        if type(self.request) is not RetconRequest or not 1 <= len(alternatives) <= 8 or any(
                type(a) is not str or not a.strip() or len(a) > 16384 for a in alternatives):
            raise ValueError('a rehearsal contains 1..8 bounded authored alternatives')
        object.__setattr__(self, 'alternatives', alternatives)

    @property
    def status(self) -> str:
        return 'speculative'

    @property
    def model_cost(self) -> int:
        return 0
