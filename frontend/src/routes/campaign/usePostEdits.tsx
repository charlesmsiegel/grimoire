import type { Dispatch, SetStateAction } from "react";
import { useState } from "react";
import type { Message } from "../../api/client";
import {
  api
} from "../../api/client";
import { errorText } from "../../api/errors";
import type { PlayError } from "../../components/play/PlayBanners";
import { type RegexTestTarget } from "../../components/play/RegexTestDialog";
import { type PickerTarget } from "../../components/PostImagePicker";
import type { Current } from "./campaignTypes";

export function usePostEdits({
  currentCampaign, cid, selected, selectScene, setError, activeId, loadScenes, setCtxKey, askAfterPost,
  rolling, transcriptIsActive, activeDone, takeRollLatch, fail, busy, sceneLocked, firstIndex, messages,
}: {
  currentCampaign: () => string;
  cid: string;
  selected: () => Readonly<{ cid: string; sid: string; }> | null;
  selectScene: (id: string, stillWanted?: Current, renamed?: boolean) => Promise<number>;
  setError: Dispatch<SetStateAction<PlayError | null>>;
  activeId: string | null;
  loadScenes: () => Promise<void>;
  setCtxKey: Dispatch<SetStateAction<number>>;
  askAfterPost: (id: string, upto: number) => void;
  rolling: boolean;
  transcriptIsActive: boolean;
  activeDone: boolean;
  takeRollLatch: (forSid: string) => () => void;
  fail: (e: unknown, retryable?: boolean, from?: string) => void;
  busy: boolean;
  sceneLocked: boolean;
  firstIndex: number;
  messages: Message[];
}) {

  const [editing, setEditing] = useState<{ index: number; text: string } | null>(null);
  /** The post an image is being picked for (#376). Holds the index and who
   *  spoke it, because the picker is scoped by the speaker: an actor post
   *  offers that actor's art, a narrator post the campaign's own library. */
  const [picking, setPicking] = useState<{ index: number; target: PickerTarget } | null>(null);
  // The post the regex test pane is open on (spec 6.2), or null.
  const [regexTest, setRegexTest] = useState<RegexTestTarget | null>(null);
  const [characterPassage, setCharacterPassage] = useState<{ cid: string; sid: string; rid: string; source: string } | null>(null);

  // Re-read a scene after a write made OUTSIDE this component, then ask whether
  // its summary is now due, bounded by the length that re-read verified. The
  // catch is what makes the pair safe to fire without awaiting: a failed read is
  // no boundary, and `askAfterPost` declines a negative one rather than
  // falling back to an unbounded fold.
  async function readAfterWrite(id: string, operation: string, relist = false, opening = false): Promise<number> {
    // A newly created greeting scene is read in the same continuation that
    // navigates to it, before the resolver has selected its id. Other writes
    // refresh only the scene still selected after their await.
    const here = () => currentCampaign() === cid && (opening || selected()?.sid === id);
    if (relist) {
      try { await loadScenes(); }
      catch (err: unknown) {
        if (here()) setError({
          text: `${operation}, but the scene list could not be refreshed: ${errorText(err)}`,
          retryable: false
        });
      }
    }
    try { return await selectScene(id, here); }
    catch (err: unknown) {
      if (here()) setError({
        text: `${operation}, but the scene could not be re-read: ${errorText(err)}`,
        retryable: false
      });
      return -1;
    }
  }

  function refreshSavedScene(id: string, operation: string) {
    const here = () => currentCampaign() === cid && (selected()?.sid ?? null) === id;
    selectScene(id, here).catch((err: unknown) => {
      if (here()) setError({
        text: `${operation}, but the scene could not be re-read: ${errorText(err)}`,
        retryable: false
      });
    });
  }

  function refreshAfterReplay(asked?: boolean) {
    const id = activeId;
    if (!id) return;
    const here = () => currentCampaign() === cid && (selected()?.sid ?? null) === id;
    // Both writes are already committed; each independent read owns its failure.
    loadScenes().catch((err: unknown) => {
      if (here()) setError({
        text: `Replay saved, but the scene list could not be refreshed: ${errorText(err)}`,
        retryable: false
      });
    });
    selectScene(id, here).then((seen) => {
      if (seen < 0 || !here()) return;
      setCtxKey((k) => k + 1);
      // Same reason the cut asks (#85/#84): the stored
      // fold covers a prefix a replay has just cut,
      // regenerated or put back, so its digest no longer
      // describes the transcript — and a walk that has
      // just rewritten several turns is exactly when the
      // scene-break question wants re-asking too.
      //
      // Unless the write was a reroll, which goes through
      // `/regenerate` and so already scheduled both
      // server-side (#397). Asking again would put a
      // second scene-break question at the provider —
      // that route has no in-flight coalescing, so both
      // are billed and the later one is thrown away as
      // superseded.
      if (!asked) askAfterPost(id, seen);
    }).catch((err: unknown) => {
      if (here()) setError({
        text: `Replay saved, but the scene could not be re-read: ${errorText(err)}`,
        retryable: false
      });
    });
  }

  async function refreshAndAsk(id: string) {
    askAfterPost(id, await readAfterWrite(id, "Scene saved", false, true));
  }

  async function saveEdit() {
    // `rolling` for the same reason the swipe buttons read it: an edit saved
    // while a swap is in flight carries the index and text of the message on
    // screen, which the promotion is in the middle of replacing. Whichever
    // write loses the race silently discards the other, and the two refreshes
    // can land out of order on top of that.
    if (!editing || !activeId || rolling) return;
    // The transcript ON SCREEN must be the active scene's own. `editing.index`
    // is an index into what is rendered and `activeId` is where it would be
    // written, and the two describe different scenes for exactly one read
    // whenever a background refresh installs the turn's scene over the one the
    // reader opened: the resolver then corrects `activeId` back to the URL's
    // scene, and until that read lands the old messages are still on screen.
    // Saving in that window overwrites an unrelated message of the scene now
    // active (codex review, P1).
    //
    // `transcriptIsActive` is the named form of exactly this fact.
    if (!transcriptIsActive) return;
    try { await api.editMessage(cid, activeId, editing.index, editing.text); }
    catch (err: unknown) { fail(err, false); return; }
    setEditing(null);
    const seen = await readAfterWrite(activeId, "Post saved");
    // An edit does not APPEND a post, so this never crosses the threshold by
    // count — but it rewrites text the stored summary already covers, which
    // moves `covered_digest` and makes that summary describe words no longer
    // in the transcript. The server reads that as a from-scratch refold being
    // due, so the ask is what turns a summary the panel can only flag as stale
    // back into a correct one. Review caught that leaving it out meant the
    // stale flag sat there until some later *generated* turn asked.
    askAfterPost(activeId, seen);   // #85, #84
  }

  /** Retcon: the same rewrite `saveEdit` makes, plus the reversal of what this
   *  scene's absorb wrote (#78).
   *
   *  Two buttons rather than one, because they are different intentions and
   *  only one of them is destructive. A plain Save fixes the words; a retcon
   *  says the scene did not happen that way, which makes the recorded summary,
   *  the write-backs and the beats descriptions of a transcript that is no
   *  longer there — so they are put back and the scene becomes unfinished,
   *  ready to be absorbed again.
   *
   *  Guarded exactly as `saveEdit` and `deleteMessagesFrom` are, and for their
   *  reasons: `rolling`, because a swap in flight is rewriting the posts this
   *  index addresses, and `transcriptIsActive`, because during a background
   *  refresh the messages on screen can belong to a scene other than `activeId`.
   */
  async function saveRetcon() {
    if (!editing || !activeId || rolling || !transcriptIsActive) return;
    if (!window.confirm(
      "Retcon this post?\n\n" +
      (activeDone
        ? "This scene has been absorbed. Every recorded write-back is put back, its " +
        "chronicle record, beats, change history and citations go, and it becomes " +
        "unfinished so it can be absorbed again — which is how the re-extraction " +
        "gets to disagree with what later scenes recorded.\n\n"
        : "") +
      "The posts after it are left alone. Replay them from the gutter if they no " +
      "longer follow.")) return;
    const release = takeRollLatch(activeId);
    let report;
    try {
      report = await api.retconMessage(cid, activeId, editing.index, editing.text);
    } catch (err: unknown) {
      fail(err, false);            // not retryable: Retry re-runs the CHAT retry
      return;
    } finally {
      release();
    }
    setEditing(null);
    // The rail carries `done`, which an absorbed scene has just lost.
    const seen = await readAfterWrite(activeId, "Post retconned", true);
    setCtxKey((k) => k + 1);       // beats and citations left the ledger
    askAfterPost(activeId, seen);   // #85/#84, exactly as `saveEdit` asks
    // What the transcript afterwards cannot show, in the same two clauses the
    // cut reports and for the same reason — plus the one thing specific to a
    // retcon: which scenes were played after this one, and so can disagree with
    // what a re-absorb now extracts.
    const notes = [];
    if (report.refused.length) {
      notes.push(
        `${report.refused.length} record${report.refused.length === 1 ? "" : "s"} ` +
        "could not be put back: " + report.refused.map((r) => r.label).join(", "));
    }
    if (report.failed.length) {
      notes.push("some continuity records could not be cleaned up (" +
        report.failed.join(", ") + ") — check them by hand");
    }
    if (report.later.length) {
      notes.push(`${report.later.length} later scene${report.later.length === 1 ? "" : "s"} ` +
        "already recorded state from here on — absorb this scene again to see " +
        "which of its findings they contradict");
    }
    if (notes.length) setError({ retryable: false, text: notes.join(". ") });
  }

  /** Cascade post-delete: this post and everything after it (#75).
   *
   *  The confirmation is not a formality. A cascade delete reaches records the
   *  transcript does not show — the chronicle entry, plot and commitment beats,
   *  the change history — and puts back what the scene's absorb wrote. So the
   *  prompt states the post count, names what a finished scene additionally
   *  loses, and names the two things that survive on purpose (the roll log is
   *  append-only; the timeline carries no scene attribution). Guessing at any of
   *  that after the fact is not something the player can do from what is left.
   *
   *  Guarded exactly as `saveEdit` is, and for the same reasons: `rolling`,
   *  because a swap in flight is rewriting the posts this index addresses, and
   *  `transcriptIsActive`, because during a background refresh the messages on
   *  screen can belong to a different scene than `activeId` — which here would
   *  cut an unrelated scene at an index taken from this one.
   */
  async function openCharacterPassage(rid: string) {
    if (!activeId || busy || sceneLocked) return;
    const sid = activeId;
    try {
      const response = await api.getResponse(cid, sid, rid);
      if (currentCampaign() === cid && (selected()?.sid ?? null) === sid)
        setCharacterPassage({ cid, sid, rid, source: response.content });
    } catch (err) { fail(err); }
  }

  /** Hide a post from context, or return it. Guarded as `saveEdit` is, and for
   *  its reasons: a swap in flight is rewriting these posts, and the indices on
   *  screen must be the active scene's own. The same latch the cut takes, since
   *  the toggle rewrites the transcript too. Exclusion moves the digests the
   *  rolling summary and scene-break check are measured against, so the
   *  follow-ups are asked for as after an edit. */
  async function toggleExcluded(index: number, excluded: boolean) {
    if (!activeId || rolling || !transcriptIsActive) return;
    const release = takeRollLatch(activeId);
    try {
      await api.setExcluded(cid, activeId, index, excluded);
    } catch (err: unknown) {
      fail(err, false);
      return;
    } finally {
      release();
    }
    const seen = await readAfterWrite(activeId, "Post visibility saved");
    askAfterPost(activeId, seen);   // #85, #84
  }

  async function deleteMessagesFrom(index: number) {
    if (!activeId || rolling || !transcriptIsActive) return;
    // The window is always the transcript's TAIL, so this is the real total
    // however few posts have been paged in.
    const n = firstIndex + messages.length - index;
    const ask = [
      n === 1 ? "Delete this post?"
        : `Delete this post and the ${n - 1} after it?`,
      activeDone
        ? "This scene has been absorbed. Its chronicle record, plot and commitment " +
        "beats, change history and citations go with it, and it becomes unfinished " +
        "so it can be absorbed again. Every recorded write-back is put back, except " +
        "where the record has changed since — those are reported and left alone."
        : "This cannot be undone.",
      "Dice rolls stay in the roll log and the timeline is not rewritten.",
    ].join("\n\n");
    if (!window.confirm(ask)) return;
    setEditing(null);
    // The same latch reroll, retry and the two roll paths take, and taken for
    // the reason it exists: it holds until an unrelated request has settled, and
    // every one of those rewrites this transcript. Without it a second click
    // lands a second cut against indices the first is in the middle of moving.
    // Confirm is modal so it cannot be double-fired, but the await below is not,
    // and this is the least reversible thing in the app.
    const release = takeRollLatch(activeId);
    let report;
    try {
      report = await api.deleteMessagesFrom(cid, activeId, index);
    } catch (err: unknown) {
      // Not retryable: Retry re-runs the CHAT retry, which would generate a
      // reply into the scene the player was trying to cut back.
      fail(err, false);
      return;
    } finally {
      // Released before the refreshes rather than after: they are reads, and
      // holding a latch that greys out the gutter across them buys nothing.
      release();
    }
    // The rail carries `done`, which an absorbed scene has just lost — without
    // this the composer stays hidden and the row still reads as absorbed.
    const seen = await readAfterWrite(activeId, "Posts deleted", true);
    // The ledger resolves every thread, commitment and fact against the scenes,
    // and this has just removed beats from it — same reason `deleteScene` bumps.
    setCtxKey((k) => k + 1);
    // The stored fold covers a prefix that may no longer exist. The server
    // reports that as stale on its own (`at > total`, or a moved digest); this
    // is what turns the flag back into a correct summary.
    askAfterPost(activeId, seen);   // #85, #84
    // What the shortened transcript cannot show. Two distinct outcomes, and
    // reported as two clauses because conflating them would mislead:
    //
    //  - `refused` — records that could not be put back and still hold what the
    //    deleted scene gave them. Both a record something else wrote to since
    //    (the reversal declines rather than discarding that later change) and a
    //    kind with no reversal at all (a character or lore entry the scene
    //    CREATED, the one a player is most likely to meet) land here.
    //    Deliberately not claiming which: naming the records is what lets the
    //    player go and look, and asserting the wrong reason is worse than none.
    //  - `failed` — cleanup that could not run, typically a store file edited by
    //    hand into something that will not parse. The cut still happened, so
    //    saying nothing would leave stale continuity looking authoritative.
    const notes = [];
    if (report.refused.length) {
      notes.push(
        `${report.refused.length} record${report.refused.length === 1 ? "" : "s"} ` +
        `could not be put back and ${report.refused.length === 1 ? "was" : "were"} ` +
        "left as it stands: " + report.refused.map((r) => r.label).join(", "));
    }
    if (report.failed.length) {
      notes.push("some continuity records could not be cleaned up (" +
        report.failed.join(", ") + ") — check them by hand");
    }
    if (notes.length) setError({ retryable: false, text: notes.join(". ") });
  }

  /** Put one image reference into the post, by opening it for editing with the
   *  markdown already in the buffer.
   *
   *  Which is what keeps this off any new persistence path: the existing Save
   *  — and Retcon, and Cancel — is the save, with exactly the semantics it has
   *  always had. Appended after a blank line rather than spliced at a cursor,
   *  so it can never land mid-sentence, and the textarea it lands in is right
   *  there to move it. */
  function insertImage(index: number, markdown: string) {
    setPicking(null);
    // The same two conditions `saveEdit` refuses on, and for its reasons.
    // `transcriptIsActive`: the picker is a modal that outlives a background
    // refresh, and `index` addresses the transcript ON SCREEN — if the refresh
    // has installed another scene's posts under it, this would open an
    // unrelated message for editing with an image already pasted into it.
    // `rolling`: the button that opened the picker is disabled while a turn is
    // in flight, so the picker must not be a way around that.
    if (!transcriptIsActive || rolling) return;
    const post = messages[index - firstIndex];
    if (!post) return;   // scrolled out of the loaded window; nothing to edit
    const current = (editing?.index === index ? editing.text : post.content).trimEnd();
    setEditing({ index, text: current ? `${current}\n\n${markdown}` : markdown });
  }
  return {
    editing, refreshAndAsk, setEditing, toggleExcluded, saveEdit, saveRetcon, setPicking, deleteMessagesFrom,
    openCharacterPassage, setRegexTest, refreshSavedScene, refreshAfterReplay, characterPassage,
    setCharacterPassage, picking, insertImage, regexTest,
  };
}
