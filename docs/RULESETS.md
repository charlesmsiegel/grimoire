# Rules-family action contracts

Sprint 062's `grimoire.rulesets` is a headless research prototype. It validates
supplied action/result records for four explicit reference profiles. It does not
roll dice, execute rules hooks, persist results, accept effects, or advance time.
It has no application controls yet; Sprint 115 owns that integration.

## Four distinct profiles

| Binding | Example action | Result record |
|---|---|---|
| `d20`, `5e-2024` | Dexterity/Stealth check, DC15, modifier+3 | Recorded d20=12, total15, success; natural20 is not automatic ability-check success |
| `wod`, `v5` | Wits/Awareness fixed-difficulty test, pool3, one Hunger die, difficulty3 | Normal dice10,6 and Hunger10: five successes, messy critical |
| `pbta`, `dungeon-world-1e` | Proposed Defy Danger trigger: Mara crosses a collapsing bridge, Dexterity+1 | Recorded dice3,4: total8, partial, unresolved bargain/choice |
| `ars`, `5e` | Uninterrupted spring1220 study of an Ignem summa, level5 quality8, accumulated XP13 | Proposed XP2 and the year/season to reserve; no die, no elapsed-time write |

These are narrow profiles, not support for every action or edition in a family.
Ars assumes no virtue/flaw adjustments or interruptions. Existing cumulative Art
XP matters: the level5 cap is15, so the example cannot receive all eight XP.
An at-cap student is rejected. A season represents three months, not an instant
check. PbtA validates a proposed trigger explanation; only trusted adjudication
can establish whether the fiction triggers the move. A partial result must retain
its unresolved cost/choice, and never silently picks it for the player.

Source review: D&D's [2024 basic rules](https://www.dndbeyond.com/sources/dnd/br-2024/playing-the-game)
distinguish ability checks and DCs; the [Dungeon World author source](https://github.com/Sagelt/Dungeon-World/blob/master/text/Playing_the_Game.xml)
describes fiction-triggered moves and result bands. V5's publisher-hosted
[mechanics reference](https://vtm.paradoxwikis.com/Basic_Mechanics) and
[Hunger reference](https://vtm.paradoxwikis.com/Hunger_system) distinguish Hunger
faces and critical/bestial results. The [Atlas Games open-text index](https://atlas-games.com/arsmagica/openars)
links the [Ars fifth-edition advancement text](https://www.redcap.org/page/Ars_Magica_5E_Standard_Edition,_Chapter_Ten:_Long-Term_Events),
which specifies seasonal advancement and source gain limits. These links are
research references; no rulebook prose or art is bundled.

## Minimal shared envelope

`RulesetModule` has a pinned `SourceRef` and explicit `ModuleBinding`s. Select
exactly one module, or select none. A hybrid is one authored composite module
with pinned component provenance and unique family bindings. Components are not
loaded or executed, and no rules are implicitly merged. Unknown editions fail
closed. A same-named module with a different source, revision, or binding is a
different selection; there is no latest-version fallback.

`ActionRequest(request_id, actor, module, binding, action)` uses the existing
campaign `InstanceRef`. `ResolutionProposal(request, result)` embeds the complete
request. Construction validates family agreement and result arithmetic.
`propose(selected, request, result)` additionally checks the exact selection.
`propose(None, None, None)` returns `None`, with no fake roll. A disabled module
with a supplied mechanical request is an error.

```python
from grimoire.core.identity import InstanceRef, SourceRef
from grimoire.rulesets import (
    ActionRequest, D20Check, D20Result, ModuleBinding, RulesetModule, propose,
)

binding = ModuleBinding('d20', '5e-2024')
module = RulesetModule(SourceRef('synthetic', 'ruleset', 'checks', 'v1'), (binding,))
request = ActionRequest('attempt-1', InstanceRef('test', 'actor', 'mara'),
                        module, binding, D20Check('dexterity', 'stealth', 15, 3))
proposal = propose(module, request, D20Result(12, 15, True))
```

The test-owned four-family examples in `backend/tests/test_rulesets.py` are
executable fixtures; runtime/tests never read this document.

## Authority and deferred hooks

All metadata and results remain untrusted proposals, including IDs, a seemingly
legal action, and a mathematically consistent result. This module neither
confirms actor existence/control nor reads knowledge. The caller must supply an
authorized intention and perspective-safe evidence; the acceptance layer must
revalidate control, applicable rules, source/state revisions and player consent.
There is no actor-facing retrieval path. Director information must not be put
into an actor prompt merely because it appears in a proposal.

| Capability | Required owner or future bounded hook |
|---|---|
| Legal actions and prerequisites | Module-specific applicability assessment against an authorized snapshot; string labels alone prove nothing |
| D20 attacks, saves, advantage, resource/action economy | Edition-specific hook; single-die ability check is the only current profile |
| WoD opposed tests | Opponent `InstanceRef`, same-campaign check, provenance of both results, contest-specific tie policy; no generic target-success adapter |
| PbtA move triggers, costs, clocks | Fiction review and move-specific result choices; clocks are proposed effects with their own owner, not a universal dice field |
| Ars lab totals, vis and seasonal activity conflicts | Distinct lab/activity hooks and guarded season reservations; study does not implement laboratory magic |
| Canonical effects, spending, elapsed time | Existing guarded effect owner; proposal text/arithmetic is never a write receipt |
| Durable roll recording/retry | Sprint 075; this package is pure, so repeated identical inputs return equal proposals but IDs are not durable idempotence receipts |
| Combat orchestration | Sprint 090; no initiative, turns, or combat action economy here |

Bounds (30 dice, four family bindings, 2,000-character descriptive text, finite
integer ranges) protect this prototype's input contract and are not universal
game limits. It adds no persisted schema or migration. Removing it changes no
canonical records. Full mechanical resolution, application wiring and authored
executable rules modules remain later work.
