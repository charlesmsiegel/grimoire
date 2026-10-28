import { useCallback, useMemo } from "react";
import type { RosterEntry } from "../../api/client";
import {
  api,
  type Actor,
  type Message
} from "../../api/client";
import { THUMB } from "../../api/thumbs";
import { quotedIn } from "../../components/play/citation";
import {
  DIRECTOR_LABEL, DIRECTOR_SPEAKER,
  isNarrator,
  TRANSITION_SPEAKER,
  type TranscriptRunData
} from "../../components/play/TranscriptPost";
import { type PickerTarget } from "../../components/PostImagePicker";
import type { SceneReview } from "../../components/review/useSceneReview";
import type { SpeakerPart } from "./useRunSession";

export function useTranscriptSpeakers({
  busy, streamingId, activeId, streamingSpeakers, cast, citedQuote, review, messages, firstIndex, roster,
  labels, showNotes, cid,
}: {
  busy: boolean;
  streamingId: string | null;
  activeId: string | null;
  streamingSpeakers: SpeakerPart[];
  cast: Actor[];
  citedQuote: string;
  review: SceneReview;
  messages: Message[];
  firstIndex: number;
  roster: RosterEntry[];
  labels: { user: string; assistant: string; };
  showNotes: boolean;
  cid: string;
}) {
  const isCited = (m: Message) => quotedIn(m, citedNeedle);

  /** The response a Keep writing stream is continuing, while it streams here. */
  const hiddenResponse = busy && streamingId === activeId
    ? streamingSpeakers.find((part) => part.seed !== undefined)?.id ?? null : null;

  // Unstamped user lines fall back to the seated player's name on their plate.
  //
  // With several players present this is the FIRST in cast order, which is a
  // deterministic pick and not a derivation: an unstamped post records which
  // side of the table it came from and nothing finer, so it can name the wrong
  // PC. It used to give up and print the reserved label in that case, which is
  // strictly less informative and no more honest. The order is the server's
  // `(kind, id)` sort, so this agrees with `appearances.player_label` -- the
  // same name the book and every transcript-quoting prompt now use, and one
  // that does not move when a second player joins mid-scene.
  const playerName = useMemo(
    () => cast.find((a) => a.role === "player")?.name ?? null, [cast]);

  // A speaker label names a cast member if it matches exactly (case-insensitive)
  // or is a word-boundary prefix of exactly one name — "Winifred" is Winifred
  // Vance; an ambiguous or mid-word label matches no one. Mirrors the
  // backend's scenes.match_name so role attribution and plates agree.
  const matchActor = useCallback((speaker: string, actorRef?: string): Actor | undefined => {
    if (actorRef) {
      return cast.find((actor) => `${actor.kind}:${actor.id}` === actorRef);
    }
    const low = speaker.trim().toLowerCase();
    if (!low) return undefined;
    const exact = cast.filter((a) => a.name.toLowerCase() === low);
    if (exact.length) return exact.length === 1 ? exact[0] : undefined;
    const prefixed = cast.filter((a) => {
      const n = a.name.toLowerCase();
      return n.startsWith(low) && !/[\p{L}\p{N}]/u.test(n[low.length] ?? "");
    });
    return prefixed.length === 1 ? prefixed[0] : undefined;
  }, [cast]);

  // One highlight, two sources: the dossier's provenance popover and the
  // review's "find in transcript". They can never both be open — the review
  // replaces the play view outright — so one piece of state serves both.
  const citedNeedle = (citedQuote || review.reviewQuote).trim().toLowerCase();

  /** How many notes this scene is holding, for the toggle's label.
   *
   *  Counted over the loaded window rather than the whole scene, like
   *  everything else on this screen: the number says how many are on the page
   *  the toggle governs. */
  const noteCount = messages.filter((m) => m.speaker === DIRECTOR_SPEAKER).length;

  /** Which posts are the last part of their response in the window — the one
   *  that carries the response's controls and its saved thinking.
   *
   *  One pass from the end. It used to be asked per post, by slicing the rest
   *  of the window and scanning it, which made every render of a transcript
   *  full of responses quadratic in its length. */
  const lastOfResponse = useMemo(() => {
    const out = new Set<number>();
    const seen = new Set<string>();
    for (let i = messages.length - 1; i >= 0; i--) {
      const rid = messages[i].response_id;
      if (!rid || seen.has(rid)) continue;
      seen.add(rid);
      out.add(firstIndex + i);
    }
    return out;
  }, [messages, firstIndex]);

  /** Each actor's roster row -- the version the campaign has locked, and its
   *  avatar's token -- keyed `kind/id`: a lookup per plate rather than a scan
   *  of the roster per plate. */
  const lockedEntry = useMemo(
    () => new Map(roster.map((r) => [`${r.kind}/${r.id}`, r])), [roster]);

  // The transition tag is internal drift metadata, never a speaker: a
  // transition renders as the unlabelled narration it was before the tag
  // existed, so tagged and pre-existing untagged transitions look the same.
  //
  // A callback, like `matchActor` below, because `runs` is memoized on both:
  // as plain functions they were new on every render and the memo would have
  // recomputed every time.
  const speakerOf = useCallback((m: Message) =>
    (m.speaker === TRANSITION_SPEAKER ? undefined
      // A note is assistant-role by the transcript format's construction (the
      // role is derived from the label, and `You` is the only user label), so
      // without this it would be plated as the narrator — the one thing it is
      // definitely not.
      : m.speaker === DIRECTOR_SPEAKER ? DIRECTOR_LABEL
        : m.speaker)
    ?? (m.role === "user" ? playerName ?? labels.user : labels.assistant),
    [playerName, labels]);

  // Consecutive messages by the same speaker form one run under a single plate.
  // Memoized on what a run is made of, so the run objects -- and so the
  // memoized rows that receive them -- survive every render that is not a
  // change to the transcript: a keystroke, a streamed delta.
  const runs = useMemo(() => {
    const out: TranscriptRunData[] = [];
    messages.forEach((m, i) => {
      const index = firstIndex + i; // absolute: what edit/reroll address it by
      // Hidden, not removed. The index above is computed from the position in
      // the loaded window, so skipping here leaves every other post addressed
      // exactly as before — filtering `messages` first would renumber them and
      // send an edit to the wrong post.
      if (m.speaker === DIRECTOR_SPEAKER && !showNotes) return;
      // The reply Keep writing is continuing: the live bubble below draws it
      // whole (its seed, then the stream), so drawing it here too would show
      // it twice. Skipped while the groups are built, so a run it emptied
      // draws no plate.
      if (hiddenResponse !== null && m.response_id === hiddenResponse) return;
      const speaker = speakerOf(m);
      const last = out[out.length - 1];
      if (last && last.speaker === speaker &&
        last.posts[0].m.actor_ref === m.actor_ref) {
        last.posts.push({ m, index });
        return;
      }
      const actor = matchActor(speaker, m.actor_ref);
      out.push({
        speaker, pc: actor ? actor.role === "player" : m.role === "user",
        actor, posts: [{ m, index }]
      });
    });
    return out;
  }, [messages, firstIndex, showNotes, speakerOf, matchActor, hiddenResponse]);

  const plateAvatar = useCallback((run: Pick<TranscriptRunData, "actor" | "speaker" | "pc">,
    actorRef?: string): string | null => {
    if (isNarrator(run, actorRef, labels.assistant)) return "/grimoire-32.png";
    if (!run.actor) return null;
    // Either actor kind: a speaker plate used to fall back to initials for
    // every PC, because PCs had no images to point at (#219).
    const { kind, id } = run.actor;
    const locked = lockedEntry.get(`${kind}/${id}`);
    // A 26px plate: the smallest bucket, never the original. The token makes
    // it immutable, so a reopened scene asks nothing for any of its plates.
    // Null is the server's positive report that no avatar exists. An omitted
    // token is the older API contract, which still needs its fallback request.
    return locked?.version && locked.avatar_v !== null
      ? api.actorImageUrl({ kind: "campaign", id: cid }, kind, id, locked.version, "avatar",
        { w: THUMB.row, v: locked.avatar_v })
      : null;
  }, [lockedEntry, cid, labels.assistant]);

  /** Whose images the picker offers for a post by `speaker` (#376).
   *
   *  The speaker decides: an actor post offers that actor's art at the version
   *  the roster has locked — the version that spoke, and the same one
   *  `plateAvatar` draws the portrait from — and a narrator post offers the
   *  campaign's own library, because "Grimoire" is nobody and has no record to
   *  hang art off. An actor the roster cannot place gets the library too: that
   *  is the one scope that is always there, and an empty picker would be worse
   *  than a general one. */
  function pickerTarget(actor: Actor | undefined, speaker: string): PickerTarget {
    const ver = actor && lockedEntry.get(`${actor.kind}/${actor.id}`)?.version;
    return actor && ver
      ? { kind: actor.kind, id: actor.id, version: ver, name: speaker }
      : { kind: "campaign", name: speaker };
  }
  return { citedNeedle, lastOfResponse, pickerTarget, runs, plateAvatar, noteCount, speakerOf, matchActor, isCited };
}
