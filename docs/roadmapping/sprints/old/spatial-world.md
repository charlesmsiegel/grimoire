# Locations, Spatial World and Maps

**Priority:** after source-qualified World Resolution for broad reuse; a narrow local adjacency viewer can be built earlier if it migrates safely.

## Goal

Locations have meaningful relationships so Grimoire can answer "what is nearby?", plan travel, choose relevant context and display a navigable map without requiring every world to have a geographic coordinate system.

## Work to plan

- **Location Management:** preserve location definitions, aliases, parent/child structure, scene presence and source-qualified cross-record links.
- **Spatial graph:** support stable directed/undirected edges with optional travel attributes, neighbor queries, bounded-depth reachability and path-between/shortest-hop routes.
- **Effective-world reads:** combine local, imported and campaign-overridden links appropriately. Never connect two places merely because their short IDs or names collide; report missing/deleted dependencies honestly.
- **Map view:** show nodes, labels, connections, selected location and optional images/portraits. Permit stable manual coordinate placement and reasonable auto-layout when coordinates are absent.
- **Authoring UX:** clicking a node opens the correct record; editing a connection writes to the correct owning world or campaign scope, never an imported catalog by accident.
- **Optional world artwork:** support illustrated fantasy/region maps and image pins if justified, without making map images mandatory for locations or traversal.
- **Travel consumer:** allow the world simulation and encounter planner to use paths, adjacency and travel costs while preserving player choice and optional mechanics.

## Proving scenario

A region has four linked settlements, one route blocked in the campaign, and an imported named location with a colliding short ID. Nearby-place retrieval and map navigation use the campaign's effective links and show the blocked connection without silently substituting the imported name. Travel between two reachable places produces a plausible path; a world with no coordinates still works.

## Dependencies and boundaries

[World Resolution](world-resolution.md) provides qualified location identity and provenance. [Challenges](challenges.md) and [World Simulation](world-simulation.md) consume travel/neighbor information. This sprint owns topology and presentation, not all travel adjudication or procedural world generation.

## Open questions

- Are connections always symmetric by default? Which directionality, weights and traversal constraints are first-class?
- What coordinate system, image anchoring and editing workflow is appropriate for hand-drawn or invented maps?
- Should graph edges live in location frontmatter, separate editable graph records, or a derived view over existing references?
- How are world-imported links overridden or tombstoned locally without editing an external module?
- What path/distance semantics are useful across theater-of-the-mind settings and rules-driven wilderness travel?
