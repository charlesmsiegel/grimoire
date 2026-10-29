# Locations, Adjacency and Travel Paths

**Outcome:** Understand which places connect and how to reach them, even in worlds without coordinates.

## Scope
- Record stable source-aware location relationships, aliases, containment and directed/undirected paths.
- Query neighbors, reachability and routes with conditional traversal constraints.
- Respect campaign-blocked or overridden paths and missing imported locations.

## Milestones
1. Author/query a four-location graph with stable IDs.
2. Block one path in a campaign without changing the world source.
3. Support weighted routes, hazards and travel consumers.

## Proving scenario
A road is blocked in one campaign and an imported town shares a short name; pathfinding never silently uses the other town.

## Independence and interfaces
Graph is usable without maps, travel adjudication or full world dependency management; a source-qualified ID adapter can be introduced incrementally.

## Open questions
- Are edges symmetric by default?
- Where does authoritative graph data live?
- Which costs and constraints are portable?
