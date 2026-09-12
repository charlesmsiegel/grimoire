# Greeting phases and bounded recommendations implementation plan

1. Add failing store tests for phase, sequence and optional frontmatter defaults and create/update round trips.
2. Extend the greeting store signatures and metadata parsing with backward-compatible values.
3. Add failing pure-function tests for direct-successor and same-phase optional recommendations, including ordering and exclusions.
4. Add recommendation reason/rank in `playing.available_greetings`, anchored to the `after` scene's greeting, without changing the existing startability contract.
5. Add failing route tests, then pass the fields through request models and world/campaign create/update routes.
6. Add failing frontend tests, then extend API types and GreetingEditor controls. Show recommendation status in the scene-idea greeting cards.
7. Run targeted backend/frontend tests, type checking and `make check`.
8. Review the complete diff against this spec, fix findings, then commit without private-world identifiers.

