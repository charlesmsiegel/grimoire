# Playing a campaign

[Documentation](../README.md) · [Context and memory](context.md)

## Open the campaign

The campaign hub leads to Scenes, waiting work, campaign settings, the Ledger,
Timeline and Story Graph. Sheets appears when a mechanics module makes it
relevant.

The Scenes list starts with the highest scene number. Use **Sort** to cycle
between scene number, last played and title; use the direction button to reverse
the order. The app remembers both choices. Last played follows scene activity,
including edits. Scenes without a number or activity time stay at the end when
sorting by that field.

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


## Visual novel reader

Open **Scene ⋯ → Visual novel reader** to read the current scene on a stage.
The reader fills the application viewport. Choose **Exit VN** to return to play
and restore Campaign, Library, and the other navigation.
Tap the stage or dialogue, press **Enter**, or use the next arrow to advance through a post. Paragraphs
with one or two sentences appear together; longer paragraphs advance one sentence
at a time. Wheel down advances and wheel up goes back; scrollable prose keeps
its normal scrolling, including at its ends. **Menu** holds Previous/First/Last
and the Post selector. The arrow keys, Home and End provide the same navigation.
Selecting text or following a link does not advance. Focused controls keep their
normal Enter behavior.
**Auto** advances one passage per interval. Set the interval in Menu. Auto stops
at the end, on an error, when you navigate manually, or when an overlay opens.
**H** hides the whole dialogue panel and pauses Auto. Press H again, tap the stage,
or choose **Show dialogue** to restore it without advancing. **Esc** opens/closes
Menu; in another dialog it closes that dialog first. Menu also holds Presentation
settings, Refresh, and shortcut help. The bottom controls have touch-sized targets.
The reader remembers your post and passage in this browser when storage is
available; a removed post recovers to the first surviving post with a notice.

The bottom row offers **Reroll**, **Edit**, and **Insert image** for eligible posts.
Reroll always opens a guidance dialog before generating and can use a different
provider/model. Edit opens the complete stored post, including text transformed
for display. Insert image uses the existing art picker and appends to an editable
draft; choose Save to change the post. Retcon remains a separate, confirmed action.
Broader post actions and history are available through **Exit VN** in normal play.
When your reply box appears, **Enter** sends nonblank text and **Shift+Enter** adds
a line. Empty Enter does nothing; reading controls never submit a reply.

Illustrations appear as temporary backgrounds when you reach them. An image
between paragraphs lasts through the following paragraph; an image inside prose
lasts through the rest of that paragraph. Another image or a change of scene
background replaces it sooner. Image transitions can split a two-sentence
paragraph. Going back restores the illustration for that passage. Illustrations
fit above the dialogue panel without cropping, and character sprites stay hidden
while an illustration is displayed. Image descriptions remain available to screen
readers but are not repeated in the dialogue box.

The speaker and dialogue sit in an inset panel with the reader controls attached.
Character sprites scale to the stage and extend behind the dialogue panel.
PC sprites are hidden by default. Enable **Presentation settings → Show PC sprites**
to show them; this reader preference applies immediately across scenes and is
remembered in this browser. Post-specific Hide choices still apply, and resetting
a post's appearance does not reset this reader preference.

At the last passage, **Your reply** lets you take your turn without leaving VN.
Write your text and choose **Send reply**; Enter inserts a new line. The next
response opens at its first passage after generation finishes. Your unsent draft
survives browsing backward. **Stop reply** asks the server to stop generation.
If a connection fails, **Check reply** resolves the pending turn before another
send; text removed from the transcript is restored to the box. Roll decisions
link back to normal play.

**Refresh** reads newly published posts while keeping your current post.
**Exit VN** returns to that exact scene for the full play and editing controls.
The reader displays the canonical transformed text.

**Presentation settings** changes the current post's outfit, expression, actor
visibility, or background. Automatic uses its recorded public scene information;
None removes background art. Save stores those choices with the library, separate
from narrative facts. Reset to automatic clears this post's overrides. If another
save wins first, a stale-save error preserves your draft; refresh before retrying.
Only actors and location art supported by that post's history are offered.
Missing history leaves a neutral stage; missing portraits fall back to an avatar
and then initials. No authored art is required to read a scene.
