import type { RefObject } from "react";
import {
  type Actor,
  type Message
} from "../../api/client";
import { StreamingMarkdown } from "../../components/play/StreamingMarkdown";
import {
  DIRECTOR_LABEL,
  type TranscriptRunData
} from "../../components/play/TranscriptPost";
import { Portrait } from "../../components/Portrait";
import { Thinking } from "../../components/Thinking";
import { PAGE_SIZE } from "./campaignShared";
import type { SpeakerPart } from "./useRunSession";

export function CampaignTranscript({
  colorQuotes, streamRef, onStreamScroll, firstIndex, loadOlder, loadingOlder, transcript, directorNote, busy, cid, activeId, streaming, streamingId, streamingSpeakers, messages, speakerOf, labels, plateAvatar, matchActor, roundProgress
}: {
  colorQuotes: boolean;
  streamRef: RefObject<HTMLDivElement>;
  onStreamScroll: () => void;
  firstIndex: number;
  loadOlder: () => Promise<void>;
  loadingOlder: boolean;
  transcript: React.ReactNode;
  directorNote: { cid: string; sid: string; text: string; } | null;
  busy: boolean;
  cid: string;
  activeId: string | null;
  streaming: string;
  streamingId: string | null;
  streamingSpeakers: SpeakerPart[];
  messages: Message[];
  speakerOf: (m: Message) => string;
  labels: { user: string; assistant: string; };
  plateAvatar: (run: Pick<TranscriptRunData, "actor" | "speaker" | "pc">, actorRef?: string) => string | null;
  matchActor: (speaker: string, actorRef?: string) => Actor | undefined;
  roundProgress: { index: number; of: number; } | null;
}) {
  return (
    <div className={"stream" + (colorQuotes ? " color-quotes" : "")} ref={streamRef}
      data-testid="stream" onScroll={onStreamScroll}>
      {firstIndex > 0 && (
        <div className="stream-older">
          <button className="subtle" onClick={() => void loadOlder()} disabled={loadingOlder}>
            {loadingOlder ? "Loading…" : (() => {
              const n = Math.min(PAGE_SIZE, firstIndex);
              return `Load ${n} older post${n === 1 ? "" : "s"}`;
            })()}
          </button>
        </div>
      )}
      {/* Memoized per run and per post (`components/play/TranscriptPost`):
                the context and the actions are the same objects across every
                render that does not change the transcript, so a keystroke or a
                streamed delta re-renders none of what is already on screen. */}
      {transcript}
      {directorNote && busy
        && directorNote.cid === cid && directorNote.sid === activeId && (
          /* Plated as a shown stored note is, which is what this one
             becomes once the turn lands. */
          <div className="run director-note">
            <div className="plate"><span className="plate-name">{DIRECTOR_LABEL}</span></div>
            <div className="msg assistant">
              <span className="msg-gutter" />
              <div className="msg-body">{directorNote.text}</div>
            </div>
          </div>
        )}
      {(streaming || (busy && streamingId === activeId && streamingSpeakers.length > 0)) && (
        <div className="run">
          {streamingSpeakers.length === 0 && (messages.length === 0 ||
            speakerOf(messages[messages.length - 1]) !== labels.assistant) && (
              <div className="plate">
                <span className="plate-avatar"><Portrait src="/grimoire-32.png" name={labels.assistant} /></span>
                <span className="plate-name">{labels.assistant}</span>
                <span className="role-chip">narrator</span>
              </div>
            )}
          <div className="msg assistant">
            <span className="msg-gutter" />
            <div className="msg-body">
              {/* Art handles are hidden while the deltas arrive: the
                        server rewrites them into markdown when the reply is
                        persisted, so the reader would otherwise watch a raw
                        `[[art:...]]` sit in the prose and then turn into a
                        picture. Display only -- nothing here changes what is
                        stored. */}
              {/* A block at a time (`StreamingMarkdown`), so the part that
                        is still arriving re-parses its open tail per delta rather
                        than everything above the cursor. */}
              {streamingSpeakers.length ? <>
                {streamingSpeakers[0].offset > 0 && <StreamingMarkdown text={streaming.slice(0, streamingSpeakers[0].offset)} />}
                {streamingSpeakers.map((part, index) => <div className="streaming-response" key={part.id}>
                  <div className="plate">
                    <span className="plate-avatar"><Portrait name={part.speaker}
                      src={plateAvatar({
                        speaker: part.speaker, pc: false,
                        actor: matchActor(part.speaker, part.actor_ref)
                      }, part.actor_ref)} /></span>
                    <strong className="plate-name">{part.speaker}</strong>
                  </div>
                  <Thinking content={part.thinking ?? ""} />
                  {/* A Keep writing part grows from the reply it continues
                            (its seed, display only: the server joins the
                            landed variant itself, and the reload shows that). */}
                  <StreamingMarkdown text={part.seed !== undefined
                    ? part.seed + " " + streaming.slice(part.offset, streamingSpeakers[index + 1]?.offset)
                    : streaming.slice(part.offset, streamingSpeakers[index + 1]?.offset)} />
                  {busy && streamingId === activeId && !part.ended && index === streamingSpeakers.length - 1 && (
                    <div className="response-progress" role="status" aria-label={`${part.speaker} is responding`}>
                      <span className="cursor" aria-hidden="true" /> {roundProgress
                        ? `Round ${roundProgress.index}/${roundProgress.of} · ` : ""}{part.speaker} is responding…
                    </div>
                  )}
                </div>)}
              </> : <><StreamingMarkdown text={streaming} /><span className="cursor" /></>}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
