# Playing a campaign

[Documentation](../README.md) · [Context and memory](context.md)

## Open the campaign

The campaign hub leads to Scenes, waiting work, campaign settings, the Ledger,
Timeline and Story Graph. Sheets appears when a mechanics module makes it
relevant.

Campaign settings can override global model and context choices. Removing an
override returns the field to inheritance. Provider credentials and the library
location remain global settings.

## Create a scene

The chooser separates **PC**, **offscreen** and **import** flows. A PC scene
includes a player persona. An offscreen scene stages activity without one.
Import brings an existing transcript into the campaign.

Choose or write a premise, confirm the cast, location and in-world moment, then
create the scene. Suggestions can use selected story pressure or an anchor from
the Story Graph. Review suggestions as proposals; they do not establish facts
merely by naming them.

## Play turns

Write in the composer and send. Cast and response controls determine which
characters contribute and how a round proceeds. A director instruction steers
the generation rather than becoming ordinary spoken story text. Character
turns keep contributions and their alternative versions separately.

The scene inspector exposes cast, setting, models and context. The app's shortcut
sheet lists bindings available in the current interface; bindings respect typing
fields and open dialogs.

## Revise a response

Expand **Response actions** beneath a reply:

| Action | Use it when |
|---|---|
| Response steer + reroll | The contribution should be written differently |
| Model for this reroll | You want an explicit provider/model for that attempt |
| Keep writing | The trailing contribution needs a continuation |
| Variants | You want to read or select an alternate completed response |
| Replay | You need to rebuild from an earlier point using current context |

A reroll can reuse the historical context retained with that contribution.
Replay explicitly rebuilds it. If an older reply's historical context is
unavailable, the interface says so instead of claiming an exact reroll.

Editing or cutting earlier text can affect later context and scene state.
Review the resulting scene before finishing it. Branching gives you a separate
path to explore a different continuation without discarding the original.

## Navigate away during generation

The server owns an active run. Leaving the page or losing the browser connection
does not itself cancel generation. Reopening the scene can reattach to the run.
Use the app's cancellation control to request cancellation.

A busy scene refuses conflicting edits while a turn or review owns it. Wait or
cancel before renaming, deleting or changing its structure. A server shutdown
differs from a browser disconnect: the run registry does not promise that a
process restart resumes model execution.

## Finish and review

Prepare wrap-up when the scene is ready to become campaign history. Absorb builds
proposed summaries and updates; related phases can prepare dossiers, state
changes and warnings. Read the review, adjust what you need and save it.
Generating a review and accepting it are separate operations.

The saved result feeds the chronicle and campaign records used by later scenes.
A review records the transcript it was prepared from. If play or edits have
moved that transcript, refresh the review before saving. Pending reviews can be
reopened after navigating away.

## Keep continuity useful

The **Ledger** holds structured campaign records and manual corrections.
Continuity review proposes possible overlaps, links or closures for threads and
commitments. Basic matching works without an Embedding model; semantic matching
adds a signal when configured. You decide which review actions to apply.

Use the **Timeline** to read what happened and the **Story Graph** to explore
connections and choose what to pursue next. These views use recorded campaign
material. A generated suggestion does not become authoritative by appearing there.

## Implementation references

- [Campaign hub](../../frontend/src/routes/CampaignHub.tsx) and [new-scene chooser](../../frontend/src/components/NewSceneChooser.tsx)
- [Play view](../../frontend/src/routes/CampaignView.tsx) and [response actions](../../frontend/src/components/ResponseControls.tsx)
- [Scene routes](../../backend/src/grimoire/routes/scenes/), [run registry](../../backend/src/grimoire/routes/runs/) and [wrap-up UI](../../frontend/src/components/review/)
- [Continuity review](../../backend/src/grimoire/store/continuity/review.py) and [knowledge overrides](../../backend/src/grimoire/store/knowledge.py)
