"""Bounded deterministic reachability over declared dependence, never prose inference."""
from collections import deque
from collections.abc import Mapping
from itertools import islice

from .model import MAX_EDGES, MAX_NODES, Dependency, Impact, ImpactReport, Pin


def preview_impact(nodes, dependencies, roots, *, max_nodes=MAX_NODES, max_edges=MAX_EDGES) -> ImpactReport:
    """Return an incomplete empty result if graph premises are invalid; inputs remain untouched.

    Nodes are Pin values or a path→revision mapping. Every edge/root must match a current
    node revision. One deterministic shortest explanation per target is returned; a wholly
    known path is preferred over a potential path, even if the latter is shorter.
    """
    if type(max_nodes) is not int or not 1 <= max_nodes <= MAX_NODES or type(max_edges) is not int or not 1 <= max_edges <= MAX_EDGES:
        raise ValueError('invalid continuity bounds')
    if isinstance(nodes, Mapping):
        nodes = (Pin(path, revision) for path, revision in nodes.items())
    nodes, edges, roots = (tuple(islice(nodes, max_nodes + 1)), tuple(islice(dependencies, max_edges + 1)),
                           tuple(islice(roots, max_nodes + 1)))
    problems = []
    if len(nodes) > max_nodes or len(edges) > max_edges or len(roots) > max_nodes:
        problems.append('bounds exceeded')
    if any(type(n) is not Pin for n in nodes + roots) or any(type(e) is not Dependency for e in edges):
        return ImpactReport((), (), ('invalid continuity input',))
    ordered_roots = tuple(sorted(set(roots)))
    if len(set(nodes)) != len(nodes) or len({n.path for n in nodes}) != len(nodes): problems.append('duplicate nodes')
    if len(set(edges)) != len(edges): problems.append('duplicate edges')
    if len(set(roots)) != len(roots): problems.append('duplicate roots')
    if not roots: problems.append('missing roots')
    all_pins = nodes + roots + tuple(p for e in edges for p in (e.source, e.dependent))
    if len({n.campaign_id for n in all_pins}) > 1: problems.append('cross-campaign reference')
    current = {n.path: n for n in nodes}
    for pin in roots + tuple(p for e in edges for p in (e.source, e.dependent)):
        if current.get(pin.path) != pin:
            problems.append(('stale revision: ' if pin.path in current else 'missing node: ') + pin.path)
    if problems: return ImpactReport(ordered_roots, (), tuple(sorted(set(problems))))
    adjacency = {n: [] for n in nodes}
    degree = dict.fromkeys(nodes, 0)
    for edge in sorted(edges):
        adjacency[edge.source].append(edge)
        degree[edge.dependent] += 1
    pending = deque(sorted(n for n in nodes if degree[n] == 0))
    visited = 0
    while pending:
        node = pending.popleft()
        visited += 1
        for edge in adjacency[node]:
            degree[edge.dependent] -= 1
            if degree[edge.dependent] == 0: pending.append(edge.dependent)
    if visited != len(nodes): return ImpactReport(ordered_roots, (), ('dependency cycle',))
    impacts = {}
    # Separate known-only reachability ensures chronology cannot downgrade an exact proof.
    for allow_potential in (False, True):
        queue = deque((root, (root,), 'known') for root in ordered_roots)
        seen = set(ordered_roots)
        while queue:
            node, chain, certainty = queue.popleft()
            for edge in adjacency[node]:
                if edge.certainty == 'potential' and not allow_potential: continue
                target = edge.dependent
                if target in seen: continue
                seen.add(target)
                status = 'potential' if 'potential' in (certainty, edge.certainty) else 'known'
                extended = chain + (target,)
                impacts.setdefault(target, Impact(target, edge.reason, extended, status))
                queue.append((target, extended, status))
    return ImpactReport(ordered_roots, tuple(impacts[n] for n in sorted(impacts)))
