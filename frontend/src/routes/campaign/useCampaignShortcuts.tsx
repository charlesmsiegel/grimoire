import type { Dispatch, SetStateAction } from "react";
import type { SceneAbsorb } from "../../api/client";
import type { PlayError } from "../../components/play/PlayBanners";
import {
  type TranscriptVariantSwipe
} from "../../components/play/TranscriptPost";
import { NO_REROLL_ROUTE, type RerollRoute } from "../../components/RerollRoute";
import { useHotkeys } from "../../shortcuts/useHotkeys";

export function useCampaignShortcuts({
  absorb, activeDone, activeClosed, busy, rolling, renamesInFlight, canGenerate, send, newScene, editing,
  canReroll, setRerollPrompt, variantSwipe, stepVariant, setRerollRoute, error, canRetry, retry, fail,
}: {
  fail: (err: unknown, retryable?: boolean, from?: string) => void;
  absorb: SceneAbsorb | null;
  activeDone: boolean;
  activeClosed: { sid: string; title: string; } | null;
  busy: boolean;
  rolling: boolean;
  renamesInFlight: number;
  canGenerate: boolean;
  send: (speakerRef?: string) => Promise<void>;
  newScene: () => void;
  editing: { index: number; text: string; } | null;
  canReroll: boolean;
  setRerollPrompt: Dispatch<SetStateAction<string | null>>;
  variantSwipe: TranscriptVariantSwipe | null;
  stepVariant: (delta: 1 | -1) => void;
  setRerollRoute: Dispatch<SetStateAction<RerollRoute>>;
  error: PlayError | null;
  canRetry: boolean;
  retry: () => Promise<undefined>;
}) {

  // The scene's own keyboard (#193). Every binding here mirrors a control that
  // is on screen, and carries the same condition that control is disabled by —
  // a shortcut that could do what no button offers is a second implementation
  // of the guards, and the guards are the reason a turn cannot be sent twice.
  // The functions guard themselves as well; this is what keeps the KEY inert,
  // so nothing fires silently and the help sheet can say why.
  //
  // Nothing is bound bare that spends money without a further confirmation:
  // `r` opens the reroll's guidance box exactly as ↻ does, and End scene —
  // which absorbs a scene into the chronicle and cannot be undone by pressing
  // the key again — is deliberately not bound at all.
  useHotkeys([
    {
      keys: "mod+enter", label: "Send · continue the turn", group: "IN THIS SCENE",
      // The one binding that belongs inside prose: the composer is where the
      // reader IS. (The textarea's own Enter still handles the caret being in
      // it, and prevents the default, so this cannot send the same keystroke
      // twice.)
      whileTyping: true,
      // Nothing here about which popover is open, and that is the point. An
      // inline Enter handler that answers the key now PREVENTS it (the reroll
      // box, both dice inputs, both rename boxes), which is the signal the
      // dispatcher has always read, so a chord typed into one of them never
      // reaches this. A list of open widgets here would have to be extended by
      // whoever adds the next inline handler -- and would silently go back to
      // double-firing when they didn't (PR #400 review).
      // `activeDone` is not a disabled control but a REPLACED one: an absorbed
      // scene has its summary written and its changes applied, so the composer
      // goes entirely and a post added now would sit outside the record taken
      // of it. A chord that ignored that would put one there -- the transcript
      // and the chronicle disagreeing, with nothing to say which is right
      // (PR #400 review).
      // A closed branch's composer is replaced the same way, for the same reason.
      enabled: !absorb && !activeDone && !activeClosed && !busy && !rolling && !renamesInFlight
        && canGenerate,
      run: () => { send().catch((err: unknown) => fail(err, false, "scene")); },
    },
    {
      keys: "n", label: "New scene", group: "IN THIS SCENE",
      enabled: !absorb, run: newScene,
    },
    {
      keys: "r", label: "Reroll the last reply", group: "IN THIS SCENE",
      // `editing` as well, the guard `pickAlternate` takes and for its reason:
      // a reroll replaces the post at this index, and an edit form open over
      // it rebinds to the REPLACEMENT, so Save would overwrite the reply that
      // was just generated. The ↻ button is hidden while its own post is being
      // edited, so a key that fired anyway would reach past it (PR #400
      // review).
      enabled: !absorb && !activeClosed && !busy && !rolling && !editing && canReroll,
      run: () => setRerollPrompt(""),
    },
    // The last response's ‹ and ›, carrying their buttons' disabled flags. →
    // at the newest variant opens the reroll box, as `r` does, rather than
    // generating as › does: the rule above, and a bare arrow is easily pressed
    // with a tab button or any other non-typing control focused. Its enabled
    // flag there already requires what that box's submit needs to reach the
    // ledger reroll rather than fall through to Replay.
    {
      keys: "arrowleft", label: "Previous reply variant", group: "IN THIS SCENE",
      enabled: !activeClosed && !!variantSwipe && !variantSwipe.previousDisabled,
      run: () => stepVariant(-1),
    },
    {
      keys: "arrowright", label: "Next reply variant", group: "IN THIS SCENE",
      enabled: !activeClosed && !!variantSwipe && !variantSwipe.nextDisabled,
      run: () => {
        if (!variantSwipe?.generates) return stepVariant(1);
        setRerollPrompt("");
        setRerollRoute(NO_REROLL_ROUTE);
      },
    },
    {
      keys: "t", label: "Retry the turn that failed", group: "IN THIS SCENE",
      // Only while the banner is up. `retry()` with nothing to recover is a
      // fresh generation the reader did not ask for, and a bare letter is far
      // too cheap to press for that.
      enabled: !absorb && !!error?.retryable && !busy && !rolling && !renamesInFlight && canRetry,
      run: () => { retry().catch((err: unknown) => fail(err, false, "scene")); },
    },
  ]);
  return {};
}
