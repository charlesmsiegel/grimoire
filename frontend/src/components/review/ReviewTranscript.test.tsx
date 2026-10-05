import { render, screen } from "@testing-library/react";
import ReviewTranscript from "./ReviewTranscript";
import type { Message } from "../../api/client";

test("a post a display rule changed is shown as the rule shows it", () => {
  // The review's pane is the same scene the reader played, so it reads
  // `shown` exactly as the transcript does (spec §5.3) — a stripped
  // `<think>` block does not come back the moment the scene is ended.
  const messages: Message[] = [
    { role: "assistant", speaker: "Mara",
      content: "<think>plan the reply</think>She nodded.", shown: "She nodded." },
    { role: "user", content: "hi" },
  ];
  render(<ReviewTranscript messages={messages} speakerOf={(m) => m.speaker ?? "You"}
                           isCited={() => false} />);
  expect(screen.getByText("She nodded.")).toBeInTheDocument();
  expect(screen.queryByText(/plan the reply/)).toBeNull();
  expect(screen.getByText("hi")).toBeInTheDocument();
});

test("the cited check is asked of the whole post, so it can see both texts", () => {
  const messages: Message[] = [
    { role: "assistant", speaker: "Mara", content: "raw", shown: "cleaned" },
    { role: "assistant", speaker: "Mara", content: "other" },
  ];
  render(<ReviewTranscript messages={messages} speakerOf={(m) => m.speaker ?? "You"}
                           isCited={(m) => m.shown === "cleaned"} />);
  const cited = document.querySelectorAll(".review-post.cited");
  expect(cited).toHaveLength(1);
  expect(cited[0].textContent).toMatch(/cleaned/);
});
