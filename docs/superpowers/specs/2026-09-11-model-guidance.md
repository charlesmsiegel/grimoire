# Automatic model guidance for scene dialogue

The user approved automatic editable templates selected by the actual requested
model ID. Shared continuity and voice rules remain model independent. Optional
scene profiles refine expression without overriding facts, knowledge, player
control, or output format. Unknown and empty IDs add nothing.

Discover direct-child templates in `templates/scene/model_guidance/` by safe,
exact stem; do not build paths from incoming model strings. Ship `glm-5.3.j2`
and an explicit `z-ai/glm-5.3` alias. Future exact model templates require no
new routing configuration. Respect `GRIMOIRE_TEMPLATES` and the section layout.

Resolve the requested model before packing each attempted scene generation,
including chat, director, opener, retry, regenerate, replay and mechanics.
Fallback may have a different profile and packing result. Freeze the gathered
context, expanded macros, templates, extras and budget for the generation;
changing model must not reroll or reread the campaign. Plain-list utility and
JSON prompts remain unaffected. Keep the LLM facade independent of the store.

The inspector includes an optional model-guidance row in the existing shape.
Frozen capture records primary composition after claim and distinct fallback
composition when attempted; capture errors cannot abort generation. Live
inspection resolves the standing scene route without requiring credentials.
Provider postprocessing remains downstream; accounting describes composed
messages, as it already does. Keep sampling and reasoning parameters unchanged.

Python >=3.11; no dependencies; portable template roots; public repository
contains only existing placeholder campaign/character data. Offline tests
establish wiring and invariants, not improved GLM dialogue quality.

Implementation review ruling: prepare the finite empty/profile compositions
before dispatch, then select and capture on demand. This freezes Jinja include
dependencies as well as direct templates without replacing the loader.
The tradeoff is one packing pass per distinct installed profile plus the empty
profile, even when fallback is unused; capture-disabled unbounded composition
still avoids tokenization. Only one nonempty profile ships initially.
