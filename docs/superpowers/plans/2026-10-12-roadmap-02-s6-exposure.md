# 02-S6: Exposure of ratified features — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Review gates: substitute review (no Codex CLI in this environment); the Codex gates are owed.**

**Goal:** the three play-decision switches get their Settings section, a
campaign override and the inspector's "Approach" line. Delivers 02-C6 (full).

**Spec:** `docs/superpowers/specs/2026-10-09-roadmap-02-decision-integration-design.md`
§3.6, §3.7, §5.6, §6.5 ("Inspector"), Slices (02-S6).

## The user's ratification, and how it departs from the spec

§4.3 lets a switch be shown only with a ratified live play-gate report, and
keeps every default `off`. The user ratified all three switches directly, with
no live report yet, and chose these defaults:
- `decide_speaker_sampling`: `on`;
- `decide_turn_plan`: `off`;
- `decide_play_tool`: `off`.

This plan carries out that decision. The live report stays owed, and so do
the adherence grader and the ensemble turn graders it needs (02-S5's plan).

What the speaker default changes, and what it does not:
- With the pick answered by a structured model (every generating Decision
  model today), the draw is `basis: answer`. The speaker is the one answered,
  except that the addressed narrowing (§5.2 rule 2) applies, and a round
  stores its `pick`.
- With a native-only Decision model, WHO is sampled from the reported
  distribution.
- No call is added either way: the pick was already asked.

## Design

- **Defaults.** `DEFAULT_DECIDE_SPEAKER_SAMPLING = "on"`.
  - `play_decisions._setting` falls back to the default rather than "off"
    when the global value is absent.
  - A value the switch does not know still reads as off. The module
    docstring and CLAUDE.md say what changed.
- **`GET /play-decisions`** (global): `{"pick_samples": bool | null}`.
  - It is `inference.reports_distribution` of the global `response-selector`
    resolution, resolved softly. `null` means unresolved.
  - The section says, per §5.6: "with this Decision model the pick is always
    the most likely speaker" when the answer is false.
- **`GET`/`PUT /campaigns/{cid}/play-decisions`**, `CampaignPlayDecisions`:
  - fields `speaker_sampling`, `turn_plan` and `play_tool`, each `""` to
    follow the global;
  - the answer gives each own value and the effective value;
  - writes go through `store.campaigns.set_campaign_play_decisions`, in the
    shape of `set_campaign_tracker`: under the campaign lock, a cleared key
    removed, and a no-op write leaves the file alone;
  - a value outside a switch's vocabulary is a 400;
  - a campaign that does not exist is a 404.
- **`GET .../responses/{rid}`** answers `approach`, built by
  `turn_plan.approach(intent)`:
  - "deflect (sampled, 38%)", or "(most likely)" for a plain answer;
  - in plan mode, `· hint · hold steady` after it;
  - "" when nothing was read.
- **Frontend:**
  - ConfigView gains section `{ id: "decisions", group: "What the model
    sees", label: "Play decisions" }`, with draft fields
    `decide_speaker_sampling`, `decide_turn_plan` and `decide_play_tool`;
  - each switch's text names its extra call (none for the pick; one per NPC
    reply on the Turn planning route for intent and plan; a possible second
    turn for the tool) and notes that the actor's card brief goes to the
    Decision-role provider (§3.7);
  - `CampaignPlayDecisions` sits beside the campaign's tracker switch on the
    campaign hub;
  - `ResponseControls` shows the `approach` as a `<span class="chip on">`.

## Tests

- `tests/test_play_decisions.py`:
  - the new defaults;
  - the global endpoint;
  - the campaign route: round trip, inherit, 400 and 404;
  - `approach` for a sampled, an answered, a plan and an empty record.
- Existing suites whose fixtures assume the pick is unsampled are updated
  where the new default changes what they record, never what they assert
  about an off switch. The off path keeps its own tests by switching sampling
  off explicitly.
- Frontend:
  - `ConfigView.test.tsx`: the section renders the three switches and saves
    them;
  - a campaign override test;
  - `ResponseControls` shows the Approach chip.
