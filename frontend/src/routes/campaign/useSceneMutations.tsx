import type { Dispatch, SetStateAction } from "react";
import { useCallback, useState } from "react";
import type { NavigateFunction } from "react-router-dom";
import type { SceneMeta } from "../../api/client";
import {
  api
} from "../../api/client";
import { errorText } from "../../api/errors";
import { forkNotes } from "../../components/forkNotes";
import type { PlayError } from "../../components/play/PlayBanners";
import type { RunRegistry } from "../../runs/RunRegistryProvider";
import { sceneNumber as numberOf } from "../sceneNumber";
import { sceneUrl } from "./campaignShared";
import type { Current } from "./campaignTypes";

export function useSceneMutations({
  loadScenes, currentCampaign, cid, setError, setSeedPrompt, goToScene, refreshAndAsk, markRenaming,
  registry, adoptSceneId, selected, selectScene, fail, activeId, setCtxKey, scenes, name, navigate, rolling,
  activeDone, takeRollLatch,
}: {
  loadScenes: () => Promise<void>;
  currentCampaign: () => string;
  cid: string;
  setError: Dispatch<SetStateAction<PlayError | null>>;
  setSeedPrompt: Dispatch<SetStateAction<{ cid: string; sid: string; prompt: string; } | null>>;
  goToScene: (id: string) => void;
  refreshAndAsk: (id: string) => Promise<void>;
  markRenaming: (active: boolean) => void;
  registry: RunRegistry;
  adoptSceneId: (oldId: string, newId: string) => void;
  selected: () => Readonly<{ cid: string; sid: string; }> | null;
  selectScene: (id: string, stillWanted?: Current, renamed?: boolean) => Promise<number>;
  fail: (e: unknown, retryable?: boolean, from?: string) => void;
  activeId: string | null;
  setCtxKey: Dispatch<SetStateAction<number>>;
  scenes: SceneMeta[];
  name: string;
  navigate: NavigateFunction;
  rolling: boolean;
  activeDone: boolean;
  takeRollLatch: (forSid: string) => () => void;
}) {

  const [chooserOpen, setChooserOpen] = useState(false);
  /** The scene being renamed in place, or null. The rail's `EditableRow` used
   *  to own this; the control moved to the scene's own heading with it. */
  const [renamingScene, setRenamingScene] = useState<{ id: string; title: string } | null>(null);

  const newScene = useCallback(() => { setChooserOpen(true); }, []);

  // Dismissing the chooser (Escape/backdrop) after a soft failure -- once
  // SceneConfirmForm's "Continue to scene" is the only OTHER exit -- still
  // leaves a real, created scene behind, and `writing` is already clear by
  // then so the dismissal goes through. NewSceneChooser reports that scene's
  // id as `createdSid`; every other dismissal (Cancel, an idle Escape) reports
  // none, so this stays a no-op for them -- reloading unconditionally would
  // relist on every plain Cancel too, and most of those wrote nothing.
  //
  // Reuses `loadScenes` -- the same relist `sceneCreated` runs after a normal
  // create -- rather than inventing a second refresh path. Unlike
  // `sceneCreated`, this must NOT navigate: the user chose to leave the scene
  // behind, not open it. The error handling mirrors `sceneCreated`'s, for the
  // reason given in its comment below: this too is called from an event
  // handler with the promise dropped, so a failure here would otherwise be an
  // unhandled rejection describing a scene that really was created.
  function closeChooser(createdSid?: string) {
    setChooserOpen(false);
    if (!createdSid) return;
    loadScenes().catch((err: unknown) => {
      if (currentCampaign() === cid) {
        setError({
          text: `The scene was created, but the scene list could not be `
            + `refreshed: ` + errorText(err), retryable: false
        });
      }
    });
  }

  async function sceneCreated(id: string, initialPrompt?: string) {
    setChooserOpen(false);
    if (initialPrompt) setSeedPrompt({ cid, sid: id, prompt: initialPrompt });
    try {
      await loadScenes();
    } catch (err: unknown) {
      // Reports rather than throws, for the reason `renameScene` does: the
      // chooser calls this from an event handler and drops the promise, so
      // anything escaping is an unhandled rejection the player never sees —
      // and the scene WAS created, so a silent failure leaves a real scene
      // that the rail does not list and nothing navigates to. Not retryable:
      // the banner's Retry generates, and there is nothing to generate.
      // Scoped, like the success path right below it. The switch to another
      // campaign already cleared that campaign's errors, so an unscoped late
      // rejection raises a banner over it claiming ITS scene list failed
      // (codex review).
      if (currentCampaign() === cid) {
        setError({
          text: `The scene was created, but the scene list could not be `
            + `refreshed: ` + errorText(err), retryable: false
        });
      }
      return;
    }
    // Asked AFTER the relist, not before it. The rows are guarded (a list for
    // the campaign the reader left is dropped) and the navigation was not, so
    // a switch during that await left the new scene's URL pointing back at the
    // campaign they came from — dragging them into another campaign entirely
    // (codex review). Guarding the data and not the navigation is the same
    // half-fix twice over, so both halves are asked here.
    if (currentCampaign() !== cid) return;
    // The list first, then the URL: the resolver only opens a scene the list
    // knows about, so navigating to a row that has not landed yet would be
    // read as a stale id and bounced straight back to the previous scene.
    goToScene(id);
    // A scene can be BORN past the threshold: starting from a greeting appends
    // that greeting's posts, and a multi-speaker one appends several. None of
    // this component's other triggers fire here — they all hang off a write the
    // reader made in an open scene — so an opener-sized scene would sit with no
    // summary until a later turn or a manual Refresh (#85). A manual (empty)
    // scene answers `refreshed: false` having reached no provider.
    void refreshAndAsk(id);
  }

  // Reports rather than throws. `EditableRow` calls this from an event handler
  // and drops the promise, so anything escaping here is an unhandled rejection
  // the player never sees — and one half of it (a relist that fails after the
  // rename landed) is not even a failed rename.
  async function renameScene(id: string, title: string) {
    markRenaming(true);
    try {
      const { id: newId } = await api.renameScene(cid, id, title);
      // The `sid` carries the slug, so this scene has a new one -- and an
      // unresolved send filed under the old one becomes unreachable, which for
      // a rolled-back post means the player's words are stranded under an id
      // nothing will select again.
      registry.rekey(cid, id, newId);
      adoptSceneId(id, newId);
      try {
        await loadScenes();
      } catch (err: unknown) {
        // The rename LANDED; only the rail is stale. Not retryable — the
        // banner's Retry generates, and there is nothing here to generate.
        setError({
          text: `Renamed, but the scene list could not be refreshed: `
            + errorText(err), retryable: false
        });
      } finally {
        // Re-read the transcript, not only the ids that point at it. A swap in
        // flight against the OLD id finds `selected` already moved on and
        // skips its own refresh, so without this nothing ever replaces the
        // pre-swap posts on screen — with the renamed set's counter on top of
        // them, and edits saving against indices that have shifted.
        //
        // In a `finally`, because the rail relist above is unrelated to it:
        // ordered first so the row exists before the reader is moved onto it,
        // but its failure must not decide whether the transcript gets re-read.
        // The re-read is the half that keeps edits from saving over the wrong
        // post; the rail being stale is cosmetic.
        //
        // Asked HERE, against the new id, rather than remembered from before
        // the rename: the relist above is a second await, and a flag captured
        // ahead of it says what was true then. `selectScene` calls `selectIdentity`,
        // so a reader who moved on during the relist would not merely get a
        // refresh they did not need — they would be dragged back to it.
        //
        // The new id is the right side of the comparison because `adoptSceneId`
        // has already re-pointed the ref: a reader still on this scene is
        // wearing `newId` by now, and one who left is somewhere else. Same test
        // the first-date path makes, for the same reason.
        if ((selected()?.sid ?? null) === newId) await selectScene(newId, undefined, true);
      }
    } catch (err: unknown) {
      fail(err, false);   // the rename itself; generating is not its recovery
    } finally {
      markRenaming(false);
    }
  }

  // the first date set renames the scene file — re-list and adopt the new id.
  // Only ever the active scene: the date controls live in the panels for the
  // scene on screen.
  async function sceneRenamed(id: string) {
    const initiator = activeId ? { cid, sid: activeId } : null;
    const stillHere = () => currentCampaign() === cid && selected()?.cid === cid && selected()?.sid === id;
    if (initiator) adoptSceneId(initiator.sid, id);
    try {
      await loadScenes();
    } catch (err: unknown) {
      if (stillHere()) setError({
        text: `Renamed, but the scene list could not be refreshed: `
          + errorText(err), retryable: false
      });
    } finally {
      // Only pull the renamed scene onto the screen if it is still the one being
      // read. A slow first-date request can land after the reader has moved to
      // another scene, and this callback belongs to the scene that started it —
      // forcing that one back would be wrong on its own, and doing it as a
      // *rename refresh* would carry the turn state of the scene they left onto
      // the one they get.
      // Compared against the NEW id, not the initiator: `adoptSceneId` re-points
      // the ref as part of adopting, so the scene that asked for the stamp is
      // already wearing `id` by now. A reader who moved on during the re-list
      // leaves the ref somewhere else, and this stays put.
      //
      // In a `finally` for the same reason `renameScene` uses one: the relist is
      // the rail's business, the re-read is the transcript's, and a failure in
      // the first must not decide whether the second happens.
      //
      // Awaited and caught, also like `renameScene`. This is a callback the
      // inspector fires and drops, so an unawaited rejection here goes nowhere:
      // no banner, and the pre-rename posts left on screen under the new id,
      // editable against indices that may have shifted.
      if (initiator && stillHere()) {
        try {
          await selectScene(id, undefined, true);
        } catch (err: unknown) {
          if (stillHere()) setError({
            text: `Renamed, but the scene could not be re-read: `
              + errorText(err), retryable: false
          });
        }
      }
    }
  }

  async function commitSceneRename() {
    if (!renamingScene) return;
    const { id, title } = renamingScene;
    setRenamingScene(null);
    await renameScene(id, title);
  }

  // Reports rather than throws, for `renameScene`'s reason: the ✕ drops the
  // promise. A delete the server refuses -- a scene a review or another
  // window's turn holds answers 409 -- otherwise left the row standing with
  // nothing on screen to say it had been asked.
  async function deleteScene(s: SceneMeta) {
    if (!window.confirm(`Delete '${s.title}'?`)) return;
    try {
      await api.deleteScene(cid, s.id);
    } catch (err: unknown) {
      if (currentCampaign() === cid) fail(err, false); // nothing was deleted; generating is not its recovery
      return;
    }
    if (currentCampaign() !== cid) return;
    try {
      await loadScenes();
    } catch (err: unknown) {
      if (currentCampaign() === cid) {
        setError({
          text: `Deleted, but the scene list could not be refreshed: ${errorText(err)}`,
          retryable: false
        });
      }
    }
    if (currentCampaign() !== cid) return;
    // Same reason as `renameScene`: the ledger resolves every thread,
    // commitment and fact against the scene list, so a deletion changes what
    // it returns. Bumped here rather than left to the refresh, because the two
    // deletions that need it least are the ones that reach no refresh at all:
    // deleting a scene the reader is not on moves nothing, and deleting the
    // last one leaves nothing to read.
    setCtxKey((k) => k + 1);
    // Nothing else to do. The deleted row is gone from the list, so if it was
    // the one in the URL the resolver now reads that sid as stale and moves
    // the reader on — or, with nothing left, clears the view and drops the
    // scene from the URL. Deleting a row the reader is NOT on leaves the sid
    // valid, and the resolver leaves them where they are.
  }

  /** Fork the campaign at this scene (#72), leaving this one untouched.
   *
   *  Offered from the scene you are reading rather than from the shelf,
   *  because "fork at an earlier turn" needs a scene and this is the only
   *  place the app already has one in hand. The fork keeps this scene whole
   *  and takes every later one off the copy, putting back what those scenes'
   *  absorbs wrote wherever the change journal still reaches.
   *
   *  The confirmation says what a cut costs, for the reason
   *  `deleteMessagesFrom`'s does: none of it is visible on the branch
   *  afterwards. It is stated only when there ARE later scenes — forking at
   *  the newest one copies the campaign whole and has nothing to warn about.
   *
   *  Navigates to the fork. The player asked to branch, and a branch you are
   *  not looking at is a fork you have to go and find; this campaign is
   *  unchanged and one click away in the shelf.
   *
   *  The note is raised BEFORE that navigation and is meant to survive it —
   *  it describes the fork, so the fork's page is where it belongs. That works
   *  because the `[cid]` effect above clears the campaign-scoped review state
   *  and deliberately not `error`; a change there that started clearing it
   *  would silently drop this report, which is what
   *  `what the fork could not put back is reported, and outlives the
   *  navigation` pins.
   */
  async function forkAtScene(sid: string, fromIndex?: number) {
    // By NUMBER, as the server cuts (gate 9): every later scene goes, and so
    // does every other scene sharing this one's number -- its branches. An id
    // with no number falls back to the order ids sort in.
    const mine = numberOf(sid);
    const later = scenes.filter((x) => {
      if (x.id === sid) return false;
      const theirs = numberOf(x.id);
      return mine === null || theirs === null ? x.id > sid : theirs >= mine;
    }).length;
    const title = scenes.find((x) => x.id === sid)?.title ?? sid;
    const ask = [
      ...(fromIndex === undefined ? [] : [
        "This scene is absorbed, so branching it from this post makes a copy of the " +
        "campaign cut at that post."]),
      `Fork this campaign at '${title}'?`,
      later === 0
        ? "It is the newest scene, so the fork is a copy of this campaign as it stands."
        : `The fork keeps this scene and drops the ${later} scene${later === 1 ? "" : "s"} ` +
        "after it, putting back what their absorbs wrote wherever that is still on " +
        "record. Records something else has written to since, and records a dropped " +
        "scene created, keep what they hold and are reported.",
      "This campaign is not changed.",
    ].join("\n\n");
    if (!window.confirm(ask)) return;
    const forkName = window.prompt("Name the fork?", `${name} (fork)`)?.trim();
    if (!forkName) return;
    let report;
    try {
      report = fromIndex === undefined
        ? await api.forkCampaign(cid, forkName, sid)
        : await api.forkCampaign(cid, forkName, sid, {}, fromIndex);
    } catch (err: unknown) {
      // Not retryable: the banner's Retry generates, and there is nothing here
      // to generate — the same call `deleteMessagesFrom` makes for the same
      // reason.
      fail(err, false);
      return;
    }
    const notes = forkNotes(report);
    if (notes) {
      // A fork that could not put everything back stops here, in the campaign
      // the reader is standing in, and says what did not come across. Leaving
      // for the fork would unmount the only thing reporting it -- and the note
      // is the reason to look at the fork before playing on in it, so carrying
      // the reader past it defeats the point of writing it.
      //
      // The fork exists either way; the rail's Campaigns row and the shelf
      // both have it. This is a pause, not a failure.
      setError({ retryable: false, text: notes });
      return;
    }
    // A clean fork takes the reader with it, to the scene it was cut at.
    void navigate(`/campaigns/${report.id}/scenes`);
  }

  /** "Branch from here" (⑂ in the gutter, play controls III). An unabsorbed
   *  scene branches into a sibling keeping everything through this post, and
   *  the reader is taken there. An absorbed scene's past lives in the
   *  campaign's files, which only a copy of the campaign can hold twice -- so
   *  there it is the fork dialog, cut at this post. */
  async function branchFrom(index: number) {
    if (!activeId || rolling) return;
    if (activeDone) {
      await forkAtScene(activeId, index);
      return;
    }
    // The latch every other write to this transcript takes, held until the
    // reader is on the sibling: it disables ⑂ (and the gutter beside it), so a
    // double click makes one branch rather than two (codex review, PR #458).
    const release = takeRollLatch(activeId);
    try {
      let made;
      try {
        made = await api.branchScene(cid, activeId, index);
      } catch (err) {
        // Not retryable: the banner's Retry generates, and a branch is not a
        // generation -- `forkAtScene`'s reason.
        fail(err, false);
        return;
      }
      await openBranch(made.id);
    } finally {
      release();
    }
  }

  /** Open a sibling a branch has just created -- after the relist that lists
   *  it, as `sceneCreated` does. The resolver only opens a scene the installed
   *  list knows, so a URL naming the sibling ahead of that list is read as a
   *  stale id and replaced with the scene the reader was on, and nothing
   *  navigates back (codex review, PR #458). A failed relist is reported rather
   *  than navigated past, for the same reason: the list it left lacks the row.
   *  Not retryable: the banner's Retry generates, and the branch exists. */
  async function openBranch(id: string) {
    try {
      await loadScenes();
    } catch (err: unknown) {
      if (currentCampaign() === cid) {
        setError({
          text: `The branch was created, but the scene list could not be `
            + `refreshed: ` + errorText(err), retryable: false
        });
      }
      return;
    }
    // The campaign is asked after the relist, as `sceneCreated` asks it.
    if (currentCampaign() !== cid) return;
    void navigate(sceneUrl(cid, id));
  }
  return {
    newScene, branchFrom, sceneRenamed, renamingScene, setRenamingScene, commitSceneRename, forkAtScene,
    deleteScene, openBranch, chooserOpen, closeChooser, sceneCreated,
  };
}
