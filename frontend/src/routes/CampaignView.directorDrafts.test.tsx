import { screen, fireEvent, waitFor } from "@testing-library/react";
import { api, ApiError } from "../api/client";
import type { SceneMeta } from "../api/client";
import { installCampaignMocks, ONE_SCENE, openScene, renderCampaign } from "../testkit/campaignHarness";
import { fixture } from "../testkit/fixture";
import { PROPOSAL_PAYLOAD, TWO_SCENES, transcriptsPerScene } from "../testkit/campaignFixtures";

// The mocks and the per-test defaults live in `src/testkit`, which the review
// suite shares (#378). A `vi.mock` factory is hoisted above every import
// and can close over nothing, so it reaches the harness through a dynamic
// import rather than a module-scope binding.
vi.mock("../components/CastPanel", async () =>
  (await import("../testkit/campaignMocks")).componentStubs.CastPanel());
vi.mock("../components/NewSceneChooser", async () =>
  (await import("../testkit/campaignMocks")).componentStubs.NewSceneChooser());
vi.mock("../components/CalendarConfig", async () =>
  (await import("../testkit/campaignMocks")).componentStubs.CalendarConfig());
vi.mock("../components/ReplayPanel", async () =>
  (await import("../testkit/campaignMocks")).componentStubs.ReplayPanel());
vi.mock("../components/ResponseTargetsPicker", async () =>
  (await import("../testkit/campaignMocks")).componentStubs.ResponseTargetsPicker());
vi.mock("../api/client", async () => (await import("../testkit/campaignMocks")).campaignApiMock());
vi.mock("../components/PostImagePicker", async () =>
  (await import("../testkit/campaignMocks")).componentStubs.PostImagePicker());
vi.mock("../api/models", () => ({ getModels: vi.fn() }));

beforeEach(installCampaignMocks);



const OFFSCREEN_SCENE: SceneMeta[] = [{ id: "s1", title: "Cabal", model: "", created: "", updated: "", date: "", pcless: true }];

test("offscreen scene: director composer, Continue button, badges", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(OFFSCREEN_SCENE);
  renderCampaign();
  await screen.findByPlaceholderText(/direct the scene/i);
  expect(screen.getByRole("button", { name: /continue ▶/i })).toBeInTheDocument();
  // one "Offscreen" chip beside the scene title. The rail that carried the
  // second one is gone.
  expect(screen.getAllByText("Offscreen")).toHaveLength(1);
});

test("offscreen scene: empty Continue sends an empty note", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(OFFSCREEN_SCENE);
  renderCampaign();
  fireEvent.click(await screen.findByRole("button", { name: /continue ▶/i }));
  await waitFor(() => expect(api.chat).toHaveBeenCalledWith("run", "s1", "", expect.any(Function), undefined, expect.any(AbortSignal), expect.any(String), expect.any(Function), true));
});

test("offscreen scene: typed note shows transiently, never lands in messages", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(OFFSCREEN_SCENE);
  let release: () => void = () => {};
  vi.mocked(api.chat).mockReturnValue(new Promise<void>((r) => { release = () => r(); }));
  renderCampaign();
  const box = await screen.findByPlaceholderText(/direct the scene/i);
  fireEvent.change(box, { target: { value: "the guard grows suspicious" } });
  fireEvent.click(screen.getByRole("button", { name: /direct ▸/i }));
  await screen.findByText("the guard grows suspicious");
  release();
  await waitFor(() => expect(screen.queryByText("the guard grows suspicious")).toBeNull());
});

test("normal scene: plain placeholder, Continue on empty input, Send once typed", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  renderCampaign();
  const box = await screen.findByPlaceholderText(/speak your intent/i);
  expect(screen.getByRole("button", { name: /continue ▶/i })).toBeInTheDocument();
  fireEvent.change(box, { target: { value: "I draw my blade." } });
  expect(screen.getByRole("button", { name: /send ▸/i })).toBeInTheDocument();
});

test("normal scene: empty Continue sends an ephemeral round, no user message added", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  renderCampaign();
  fireEvent.click(await screen.findByRole("button", { name: /continue ▶/i }));
  await waitFor(() => expect(api.chat).toHaveBeenCalledWith("run", "s1", "", expect.any(Function), undefined, expect.any(AbortSignal), expect.any(String), expect.any(Function), false));
});

// #83. The director note used to have no input of its own: whatever you typed
// in a pcless scene WAS the note, and an empty send anywhere meant "next NPC
// round" — both true, neither said anywhere on screen. These cover the control
// that says it.

test("normal scene: the composer offers Speak and Direct, and starts in Speak", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  renderCampaign();
  const speak = await screen.findByRole("button", { name: "Speak" });
  const direct = screen.getByRole("button", { name: "Direct" });
  expect(speak).toHaveAttribute("aria-pressed", "true");
  expect(direct).toHaveAttribute("aria-pressed", "false");
  expect(speak).toBeEnabled();
});

test("normal scene: Direct sends a director note, not a player post", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  let release: () => void = () => {};
  vi.mocked(api.chat).mockReturnValue(new Promise<void>((r) => { release = () => r(); }));
  renderCampaign();
  fireEvent.click(await screen.findByRole("button", { name: "Direct" }));
  const box = screen.getByPlaceholderText(/direct the scene/i);
  fireEvent.change(box, { target: { value: "the storm intensifies" } });
  // the send control says what it will do, rather than reading "Send"
  fireEvent.click(screen.getByRole("button", { name: /direct ▸/i }));
  await waitFor(() => expect(api.chat).toHaveBeenCalledWith(
    "run", "s1", "the storm intensifies", expect.any(Function), undefined,
    expect.any(AbortSignal), expect.any(String), expect.any(Function), true));
  // shown transiently as a note, never appended as the player's own words
  await screen.findByText("the storm intensifies");
  release();
  await waitFor(() => expect(screen.queryByText("the storm intensifies")).toBeNull());
});

test("normal scene: Speak is unchanged — a player post, and no director flag", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  let release: () => void = () => {};
  vi.mocked(api.chat).mockReturnValue(new Promise<void>((r) => { release = () => r(); }));
  renderCampaign();
  const box = await screen.findByPlaceholderText(/speak your intent/i);
  fireEvent.change(box, { target: { value: "I draw my blade." } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await waitFor(() => expect(api.chat).toHaveBeenCalledWith(
    "run", "s1", "I draw my blade.", expect.any(Function), undefined,
    expect.any(AbortSignal), expect.any(String), expect.any(Function), false));
  // the player's own words, in the transcript — not a note above it
  expect(await screen.findByText("I draw my blade.")).toBeInTheDocument();
  expect(document.querySelector(".director-note")).toBeNull();
  release();
});

test("Direct mode keeps the empty-send convention", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  renderCampaign();
  fireEvent.click(await screen.findByRole("button", { name: "Direct" }));
  // an empty box is still "next NPC round", in either mode — the button says so
  expect(screen.getByRole("button", { name: /continue ▶/i })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /continue ▶/i }));
  await waitFor(() => expect(api.chat).toHaveBeenCalledWith(
    "run", "s1", "", expect.any(Function), undefined,
    expect.any(AbortSignal), expect.any(String), expect.any(Function), true));
});

test("offscreen scene: the mode is locked to Direct", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(OFFSCREEN_SCENE);
  renderCampaign();
  const direct = await screen.findByRole("button", { name: "Direct" });
  expect(direct).toHaveAttribute("aria-pressed", "true");
  // Speak is shown rather than hidden, and disabled: what it is unavailable
  // for is the fact worth teaching — there is no PC in this scene to speak as.
  const speak = screen.getByRole("button", { name: "Speak" });
  expect(speak).toBeDisabled();
  expect(speak).toHaveAttribute("aria-pressed", "false");
});

test("a note carried out of an offscreen scene is still a note", async () => {
  // The nastier half of the sticky rule. `pcless` FORCES Direct, so a reader
  // who never touched the toggle still has a note in the box — and the box
  // survives the move to a scene whose `pcless` is false. If the mode reverted
  // there, the next Send would post scene direction as the player's own words,
  // which is the one outcome this control exists to prevent.
  vi.mocked(api.listScenes).mockResolvedValue([
    { id: "s1", title: "Cabal", model: "", created: "", updated: "2", date: "", pcless: true },
    { id: "s2", title: "The Saltmarch Gate", model: "", created: "", updated: "1", date: "" },
  ]);
  transcriptsPerScene();
  renderCampaign();
  await screen.findByText("transcript of s1");
  fireEvent.change(screen.getByPlaceholderText(/direct the scene/i),
                   { target: { value: "the storm intensifies" } });

  await openScene(/The Saltmarch Gate/);

  await screen.findByText("transcript of s2");
  // Speak is available again — and not selected
  expect(screen.getByRole("button", { name: "Speak" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "Direct" }))
    .toHaveAttribute("aria-pressed", "true");
  expect(screen.getByPlaceholderText(/direct the scene/i))
    .toHaveValue("the storm intensifies");
});

test("a refused Direct send hands the note back", async () => {
  // Codex P1. The composer is cleared on send, and a director note reaches no
  // transcript by design — so a send the server REFUSES (no connection
  // configured, say) used to destroy what the player wrote with no copy
  // anywhere. A refusal is the unambiguous case: nothing generated, nothing to
  // duplicate, and the words are the only thing at stake.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.chat).mockRejectedValue(
    new ApiError(409, "OpenRouter key not set", "missing_key"));
  renderCampaign();
  fireEvent.click(await screen.findByRole("button", { name: "Direct" }));
  const box = screen.getByPlaceholderText(/direct the scene/i);
  fireEvent.change(box, { target: { value: "the storm intensifies" } });
  fireEvent.click(screen.getByRole("button", { name: /direct ▸/i }));
  await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue("the storm intensifies"));
  // and it comes back as a NOTE: the mode is sticky, so the recovered words
  // are staged as what they were written as
  expect(screen.getByRole("button", { name: "Direct" }))
    .toHaveAttribute("aria-pressed", "true");
});

test("a Direct turn the provider killed hands the note back", async () => {
  // Codex round 2, P1. The refusal fix did not cover this: when the provider
  // dies mid-stream the backend answers with an error frame, and it cannot set
  // `post_returned` because a director turn has no post to return. The error
  // frame therefore recovers nothing, and `errored` is excluded from the
  // transcript-based pass — so the note was still destroyed. An empty
  // transcript is the witness: the generation left no reply behind.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, total: 0, messages: [] }));
  vi.mocked(api.chat).mockImplementation(
    async (_c, _s, _t, onEvent) => {
      onEvent(fixture({ error: { detail: "the provider gave up" } }));
    });
  renderCampaign();
  fireEvent.click(await screen.findByRole("button", { name: "Direct" }));
  fireEvent.change(screen.getByPlaceholderText(/direct the scene/i),
                   { target: { value: "the storm intensifies" } });
  fireEvent.click(screen.getByRole("button", { name: /direct ▸/i }));
  await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue("the storm intensifies"));
});

test("a note recovered into a composer switched to Speak comes back as a note", async () => {
  // Codex round 2, P2. The mode buttons stay live while a turn runs, so the
  // player can be in Speak by the time a Direct turn fails. Handing the words
  // back without the kind stages scene direction as dialogue, and the next
  // Send persists it — the "never posted" contract broken by the recovery
  // meant to protect it.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  let fail: () => void = () => {};
  vi.mocked(api.chat).mockImplementation(
    async (_c, _s, _t, onEvent) =>
      new Promise<void>((resolve) => {
        fail = () => { onEvent(fixture({ error: { detail: "the provider gave up" } })); resolve(); };
      }));
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, total: 0, messages: [] }));
  renderCampaign();
  fireEvent.click(await screen.findByRole("button", { name: "Direct" }));
  fireEvent.change(screen.getByPlaceholderText(/direct the scene/i),
                   { target: { value: "the storm intensifies" } });
  fireEvent.click(screen.getByRole("button", { name: /direct ▸/i }));
  await screen.findByText("the storm intensifies");

  // the player flips to Speak while the turn is still running
  fireEvent.click(screen.getByRole("button", { name: "Speak" }));
  expect(screen.getByRole("button", { name: "Speak" })).toHaveAttribute("aria-pressed", "true");
  fail();

  await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue("the storm intensifies"));
  // back as what it was written as, not as the player's next line
  expect(screen.getByRole("button", { name: "Direct" }))
    .toHaveAttribute("aria-pressed", "true");
});

test("a failed Direct turn offers no Retry, because Retry cannot resend a note", async () => {
  // Codex round 3. The banner's Retry GENERATES, down `/retry`, which
  // regenerates from the stored transcript — and a director note is in no
  // transcript. Offering it produces an undirected continuation in a scene
  // with history, or "nothing to retry" in one without. The note is back in
  // the composer by then, so Send is its retry.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, total: 0, messages: [] }));
  vi.mocked(api.chat).mockImplementation(
    async (_c, _s, _t, onEvent) => {
      onEvent(fixture({ error: { detail: "the provider gave up" } }));
    });
  renderCampaign();
  fireEvent.click(await screen.findByRole("button", { name: "Direct" }));
  fireEvent.change(screen.getByPlaceholderText(/direct the scene/i),
                   { target: { value: "the storm intensifies" } });
  fireEvent.click(screen.getByRole("button", { name: /direct ▸/i }));
  await screen.findByText(/the provider gave up/);
  expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
  // the words are back where Send can resend them
  expect(screen.getByRole("textbox")).toHaveValue("the storm intensifies");
});

test("a Speak send still offers Retry", async () => {
  // The counterpart, so the suppression above is pinned to ephemeral turns
  // rather than to failure in general.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, total: 0, messages: [] }));
  vi.mocked(api.chat).mockImplementation(
    async (_c, _s, _t, onEvent) => {
      onEvent(fixture({ error: { detail: "the provider gave up" } }));
    });
  renderCampaign();
  const box = await screen.findByPlaceholderText(/speak your intent/i);
  fireEvent.change(box, { target: { value: "I draw my blade." } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await screen.findByText(/the provider gave up/);
  expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
});

test("a recovered note waits for a Speak draft rather than merging into it", async () => {
  // Codex round 3, and the author's call on how to resolve it. `giveBackPrompt`
  // prepends, and one composer carries one mode — so merging a recovered note
  // into dialogue typed since gives the combined text one mode that is wrong
  // for half of it. The note waits instead, and says so, until the box frees.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, total: 0, messages: [] }));
  let fail: () => void = () => {};
  vi.mocked(api.chat).mockImplementation(
    async (_c, _s, _t, onEvent) =>
      new Promise<void>((resolve) => {
        fail = () => { onEvent(fixture({ error: { detail: "the provider gave up" } })); resolve(); };
      }));
  renderCampaign();
  fireEvent.click(await screen.findByRole("button", { name: "Direct" }));
  fireEvent.change(screen.getByPlaceholderText(/direct the scene/i),
                   { target: { value: "the storm intensifies" } });
  fireEvent.click(screen.getByRole("button", { name: /direct ▸/i }));
  await screen.findByText("the storm intensifies");

  // the player switches to Speak and starts a line of their own
  fireEvent.click(screen.getByRole("button", { name: "Speak" }));
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "I draw my blade." } });
  fail();

  // the dialogue is untouched and still staged as dialogue...
  await screen.findByText(/note held/i);
  expect(screen.getByRole("textbox")).toHaveValue("I draw my blade.");
  expect(screen.getByRole("button", { name: "Speak" })).toHaveAttribute("aria-pressed", "true");

  // ...and clearing the box hands the note back, as a note
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "" } });
  await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue("the storm intensifies"));
  expect(screen.getByRole("button", { name: "Direct" }))
    .toHaveAttribute("aria-pressed", "true");
  expect(screen.queryByText(/note held/i)).toBeNull();
});

test("a Speak turn does not inherit the previous turn's director note", async () => {
  // Codex round 4. `busy` drops before `runStream` returns, so a second turn
  // can begin while the first still owns the note — and once the clear became
  // ownership-guarded, the stale note rendered beside the new reply as though
  // it had steered it. Every send states what note is in force; for a Speak
  // post that is "none".
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, total: 0, messages: [] }));
  let settle: () => void = () => {};
  vi.mocked(api.chat).mockReturnValue(new Promise<void>((r) => { settle = () => r(); }));
  renderCampaign();
  fireEvent.click(await screen.findByRole("button", { name: "Direct" }));
  fireEvent.change(screen.getByPlaceholderText(/direct the scene/i),
                   { target: { value: "the storm intensifies" } });
  fireEvent.click(screen.getByRole("button", { name: /direct ▸/i }));
  await screen.findByText("the storm intensifies");
  settle();

  // a Speak post follows, and must not be labelled by the previous note
  await waitFor(() => expect(screen.getByRole("button", { name: "Direct" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Speak" }));
  vi.mocked(api.chat).mockReturnValue(new Promise<void>(() => {}));
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "I draw my blade." } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));

  await waitFor(() => expect(screen.queryByText("the storm intensifies")).toBeNull());
});

test("a held Speak draft says so too, not only a held note", async () => {
  // Codex review. The hold rule is symmetric, so the notice has to be: a
  // rolled-back post waits behind a note being drafted exactly as a note waits
  // behind dialogue. Firing only for notes left the player's own words waiting
  // somewhere nothing named — the failure the notice exists to prevent.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, total: 0, messages: [] }));
  let fail: () => void = () => {};
  vi.mocked(api.chat).mockImplementation(
    async (_c, _s, _t, onEvent) =>
      new Promise<void>((resolve) => {
        fail = () => {
          onEvent(fixture({ error: { detail: "the provider gave up", post_returned: true } }));
          resolve();
        };
      }));
  renderCampaign();
  const box = await screen.findByPlaceholderText(/speak your intent/i);
  fireEvent.change(box, { target: { value: "I draw my blade." } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));

  // the player switches to Direct and starts a note while that turn is live
  await waitFor(() => expect(screen.getByRole("button", { name: "Direct" })).toBeInTheDocument());
  fireEvent.click(screen.getByRole("button", { name: "Direct" }));
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "the storm intensifies" } });
  fail();

  await screen.findByText(/post held/i);
  expect(screen.getByRole("textbox")).toHaveValue("the storm intensifies");

  fireEvent.change(screen.getByRole("textbox"), { target: { value: "" } });
  await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue("I draw my blade."));
});

test("recovered dialogue restores Speak, not just notes restoring Direct", async () => {
  // Codex review. The mode restoration was one-sided: dialogue recovered into
  // a composer left in Direct stayed staged as a note, and the next send spent
  // it as an ephemeral instruction instead of posting it. The round-two bug
  // with its mirror still in place.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, total: 0, messages: [] }));
  let fail: () => void = () => {};
  vi.mocked(api.chat).mockImplementation(
    async (_c, _s, _t, onEvent) =>
      new Promise<void>((resolve) => {
        fail = () => {
          onEvent(fixture({ error: { detail: "the provider gave up", post_returned: true } }));
          resolve();
        };
      }));
  renderCampaign();
  const box = await screen.findByPlaceholderText(/speak your intent/i);
  fireEvent.change(box, { target: { value: "I draw my blade." } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));

  // switch to Direct, leaving the box EMPTY so the hold rule does not apply
  await waitFor(() => expect(screen.getByRole("button", { name: "Direct" })).toBeInTheDocument());
  fireEvent.click(screen.getByRole("button", { name: "Direct" }));
  fail();

  await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue("I draw my blade."));
  // the words came back as DIALOGUE, so the box is staged to post them
  expect(screen.getByRole("button", { name: "Speak" })).toHaveAttribute("aria-pressed", "true");
});

test("a Direct turn that generated nothing at all hands the note back", async () => {
  // Codex review. A provider can finish cleanly and write nothing — an empty
  // safety response is a shape the backend supports, and it still ends the
  // stream with `done`. The turn then counts as landed, so no failure branch
  // fires, and the note — already cleared out of the composer — would exist
  // nowhere, with no reply to show for it either.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, total: 0, messages: [] }));
  vi.mocked(api.chat).mockImplementation(
    async (_c, _s, _t, onEvent) => { onEvent({ done: true }); });
  renderCampaign();
  fireEvent.click(await screen.findByRole("button", { name: "Direct" }));
  fireEvent.change(screen.getByPlaceholderText(/direct the scene/i),
                   { target: { value: "the storm intensifies" } });
  fireEvent.click(screen.getByRole("button", { name: /direct ▸/i }));

  await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue("the storm intensifies"));
});

test("a proposal-only Direct completion spends its note and length override", async () => {
  // No transcript growth does not mean an empty generation: the roll proposal
  // is the result the note bought. Dropping the proposal exception would refund
  // both the note and the one-shot length despite the successful turn.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, total: 0, messages: [] }));
  vi.mocked(api.chat).mockImplementation(async (_c, _s, _t, onEvent) => {
    onEvent({ proposal: PROPOSAL_PAYLOAD });
    onEvent({ done: true });
  });
  renderCampaign();
  const picker = await screen.findByLabelText("Next reply words");
  fireEvent.change(picker, { target: { value: "120" } });
  fireEvent.click(screen.getByRole("button", { name: "Direct" }));
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "the storm intensifies" } });
  fireEvent.click(screen.getByRole("button", { name: /direct ▸/i }));

  await screen.findByRole("button", { name: "Roll it" });
  expect(screen.getByRole("textbox")).toHaveValue("");
  expect(picker).toHaveValue(null);
});

test("merely visiting an offscreen scene does not leave the next scene in Direct", async () => {
  // Codex review, against a call made in this change's own first review round.
  // The stamp is justified by words needing to keep their kind across a scene
  // switch; with an empty box there are none, and stamping anyway left an
  // ordinary scene staged to spend the next line typed as a note.
  vi.mocked(api.listScenes).mockResolvedValue([
    { id: "s1", title: "Cabal", model: "", created: "", updated: "2", date: "", pcless: true },
    { id: "s2", title: "The Saltmarch Gate", model: "", created: "", updated: "1", date: "" },
  ]);
  transcriptsPerScene();
  renderCampaign();
  await screen.findByText("transcript of s1");
  expect(screen.getByRole("button", { name: "Direct" })).toHaveAttribute("aria-pressed", "true");

  // leave without typing anything
  await openScene(/The Saltmarch Gate/);

  await screen.findByText("transcript of s2");
  expect(screen.getByRole("button", { name: "Speak" })).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByPlaceholderText(/speak your intent/i)).toBeInTheDocument();
});

test("a Direct turn that produced nothing keeps the one-shot length override", async () => {
  // Codex review, against this change's own round-five fix. That fix hands the
  // note back when a turn finishes cleanly having written nothing — but the
  // turn still reported as landed, so `send` spent the one-shot override on
  // it. The override was promised to the next REPLY, and no reply came; it
  // rides with the note into the retry.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue({
    meta: { id: "s1", title: "Old", response_preset: "cinematic" }, messages: [], total: 0 });
  vi.mocked(api.chat).mockImplementation(
    async (_c, _s, _t, onEvent) => { onEvent({ done: true }); });
  renderCampaign();
  const picker = await screen.findByLabelText("Next reply words");
  await waitFor(() => expect(picker).toHaveValue(null));
  fireEvent.change(picker, { target: { value: "120" } });

  fireEvent.click(screen.getByRole("button", { name: "Direct" }));
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "the storm intensifies" } });
  fireEvent.click(screen.getByRole("button", { name: /direct ▸/i }));

  // the note came back for another attempt...
  await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue("the storm intensifies"));
  // ...and so did the length chosen for the reply that never arrived
  expect(picker).toHaveValue(120);
});

test("a running scene's director note does not show in another scene", async () => {
  // Codex P2. `busy` is global and the note carried no scene, so a Direct turn
  // left running in one scene rendered its note inside whatever transcript the
  // reader moved to — and navigation during a detached run is deliberately
  // allowed. Scoped to the campaign as well as the scene: ids are
  // campaign-local, so `s1` names a different scene in every campaign.
  vi.mocked(api.listScenes).mockResolvedValue(TWO_SCENES);
  transcriptsPerScene();
  // Held open, then RELEASED at the end. A turn left hanging keeps `busy` true
  // past the end of the test and the next one inherits a locked composer.
  let release: () => void = () => {};
  vi.mocked(api.chat).mockReturnValue(new Promise<void>((r) => { release = () => r(); }));
  renderCampaign();
  await screen.findByText("transcript of s1");
  fireEvent.click(screen.getByRole("button", { name: "Direct" }));
  fireEvent.change(screen.getByPlaceholderText(/direct the scene/i),
                   { target: { value: "the storm intensifies" } });
  fireEvent.click(screen.getByRole("button", { name: /direct ▸/i }));
  await screen.findByText("the storm intensifies");

  await openScene(/The Saltmarch Gate/);

  await screen.findByText("transcript of s2");
  expect(screen.queryByText("the storm intensifies")).toBeNull();
  release();
});

test("the mode travels with the text it labels across a scene switch", async () => {
  // The composer is one shared box whose contents deliberately survive a scene
  // change. A mode that reset there would send words written as direction as
  // the player's own post, which is precisely the confusion this control exists
  // to end.
  vi.mocked(api.listScenes).mockResolvedValue(TWO_SCENES);
  transcriptsPerScene();
  renderCampaign();
  await screen.findByText("transcript of s1");
  fireEvent.click(screen.getByRole("button", { name: "Direct" }));
  fireEvent.change(screen.getByPlaceholderText(/direct the scene/i),
                   { target: { value: "the storm intensifies" } });

  await openScene(/The Saltmarch Gate/);

  await screen.findByText("transcript of s2");
  expect(screen.getByRole("button", { name: "Direct" }))
    .toHaveAttribute("aria-pressed", "true");
  expect(screen.getByPlaceholderText(/direct the scene/i))
    .toHaveValue("the storm intensifies");
});
