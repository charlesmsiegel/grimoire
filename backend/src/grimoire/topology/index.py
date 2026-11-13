"""Disposable director topology index. Connectivity never grants knowledge or moves anyone."""
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from grimoire.core.identity import SourceRef
from grimoire.graph.index import require_director
from .model import Topology, TopologyError, pinned, text, tokens


@dataclass(frozen=True)
class Route:
    places: tuple[SourceRef, ...]
    paths: tuple[str, ...]


@dataclass(frozen=True)
class Reach:
    reached: Mapping[SourceRef, Route]
    truncated: bool


class TopologyIndex:
    def __init__(self, topology: Topology, names: Mapping[SourceRef, str]):
        if type(topology) is not Topology:
            raise TopologyError('invalid_topology')
        self.topology = topology
        self._places = {p.ref: p for p in topology.places}
        self._names = {ref: name for ref, name in names.items() if ref in self._places}
        for name in self._names.values():
            if type(name) is not str:
                raise TopologyError('invalid_name')
        self.missing = tuple(sorted(set(self._places) - set(self._names), key=str))

    def _available(self, ref):
        pinned(ref)
        if ref not in self._names:
            raise TopologyError('missing_place')

    def matches(self, label: str, *, requested_by: object) -> tuple[SourceRef, ...]:
        require_director(requested_by)
        text(label)
        key = label.casefold()
        return tuple(sorted((ref for ref, place in self._places.items()
                             if key in {s.casefold() for s in (*place.aliases, self._names.get(ref, ''))}), key=str))

    def children(self, ref: SourceRef, *, requested_by: object) -> tuple[SourceRef, ...]:
        require_director(requested_by)
        self._available(ref)
        return tuple(sorted((p.ref for p in self._places.values()
                             if p.parent == ref and p.ref in self._names), key=str))

    def ancestors(self, ref: SourceRef, *, requested_by: object) -> tuple[SourceRef, ...]:
        require_director(requested_by)
        self._available(ref)
        result = []
        parent = self._places[ref].parent
        while parent is not None:
            self._available(parent)
            result.append(parent)
            parent = self._places[parent].parent
        return tuple(result)

    def neighbors(self, ref: SourceRef, *, requested_by: object,
                  conditions: tuple[str, ...] = ()) -> tuple[tuple[str, SourceRef], ...]:
        require_director(requested_by)
        self._available(ref)
        tokens(conditions)
        result = []
        for path in sorted(self.topology.paths, key=lambda p: p.path_id):
            if path.blocked or not set(path.requires) <= set(conditions):
                continue
            target = path.target if path.source == ref else (
                path.source if path.bidirectional and path.target == ref else None)
            if target in self._names:
                result.append((path.path_id, target))
        return tuple(result)

    def reach(self, start: SourceRef, *, requested_by: object, conditions: tuple[str, ...] = (),
              max_depth: int = 32, max_steps: int = 4096) -> Reach:
        require_director(requested_by)
        self._available(start)
        tokens(conditions)
        if type(max_depth) is not int or not 0 <= max_depth <= 256 \
                or type(max_steps) is not int or not 1 <= max_steps <= 4096:
            raise TopologyError('invalid_limit')
        reached = {start: Route((start,), ())}
        queue = deque([start])
        steps, truncated = 0, False
        while queue:
            here = queue.popleft()
            route = reached[here]
            for path_id, target in self.neighbors(here, requested_by=requested_by, conditions=conditions):
                if steps == max_steps:
                    return Reach(MappingProxyType(reached), True)
                steps += 1
                if target in reached:
                    continue
                if len(route.paths) == max_depth:
                    truncated = True
                    continue
                reached[target] = Route((*route.places, target), (*route.paths, path_id))
                queue.append(target)
        return Reach(MappingProxyType(reached), truncated)

    def route(self, start: SourceRef, destination: SourceRef, *, requested_by: object,
              conditions: tuple[str, ...] = (), max_depth: int = 32, max_steps: int = 4096) -> Route | None:
        require_director(requested_by)
        self._available(destination)
        result = self.reach(start, requested_by=requested_by, conditions=conditions,
                            max_depth=max_depth, max_steps=max_steps)
        if destination in result.reached:
            return result.reached[destination]
        if result.truncated:
            raise TopologyError('search_limit')
        return None
