import { act, fireEvent, renderHook, screen, waitFor } from "@testing-library/react";
import { api } from "../api/client";
import type { RunHandle } from "../api/stream";
import { RunRegistryProvider, useRunRegistry } from "../runs/RunRegistryProvider";
import { installCampaignMocks, ONE_SCENE, renderCampaign } from "../testkit/campaignHarness";

vi.mock("../api/client", async () => (await import("../testkit/campaignMocks")).campaignApiMock());
vi.mock("../api/models", () => ({ getModels: vi.fn() }));
vi.mock("../components/CastPanel", async () =>
  (await import("../testkit/campaignMocks")).componentStubs.CastPanel());
vi.mock("../components/NewSceneChooser", async () =>
  (await import("../testkit/campaignMocks")).componentStubs.NewSceneChooser());
vi.mock("../components/CalendarConfig", async () =>
  (await import("../testkit/campaignMocks")).componentStubs.CalendarConfig());
vi.mock("../components/ReplayPanel", async () =>
  (await import("../testkit/campaignMocks")).componentStubs.ReplayPanel());
vi.mock("../components/PostImagePicker", async () =>
  (await import("../testkit/campaignMocks")).componentStubs.PostImagePicker());
vi.mock("../components/ResponseTargetsPicker", async () =>
  (await import("../testkit/campaignMocks")).componentStubs.ResponseTargetsPicker());

const RUN: RunHandle = { id: "reply", attempt_id: "pending", state: "running", next_index: 0 };

beforeEach(() => {
  installCampaignMocks();
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
});

/** Seed a send through the public registry, then recreate the provider as a
 * reload does. The test never depends on its storage encoding. */
function rememberSend() {
  const saved = renderHook(useRunRegistry, { wrapper: RunRegistryProvider });
  act(() => saved.result.current.begin({
    cid: "run", sid: "s1", attempt: "pending", text: "Words worth keeping", runId: null,
  }));
  saved.unmount();
}

test("recovery observes a reply that reserves after the first discovery", async () => {
  rememberSend();
  vi.mocked(api.findRun).mockResolvedValue({ run: null });
  vi.mocked(api.cancelAttempt).mockResolvedValue({ run: RUN });
  vi.mocked(api.attemptState).mockResolvedValue({ attempt: "pending", retained: true, run: RUN });
  vi.mocked(api.attachRun).mockImplementation(async (_cid, _sid, _run, _from, emit) => {
    emit({ delta: "The reply is still arriving." });
    await new Promise(() => {});
  });

  renderCampaign();
  expect(await screen.findByText("The reply is still arriving.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /(send ▸|continue ▶)/i })).toBeDisabled();
  expect(screen.getByRole("textbox")).toHaveValue("");
});

test("empty discovery fences a delayed send before returning its words", async () => {
  rememberSend();
  let fenced = false;
  vi.mocked(api.findRun).mockResolvedValue({ run: null });
  vi.mocked(api.cancelAttempt).mockImplementation(async () => {
    fenced = true;
    return { run: null };
  });
  vi.mocked(api.attemptState).mockImplementation(async () => ({
    attempt: "pending", retained: false, run: fenced ? null : RUN,
  }));

  renderCampaign();
  await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue("Words worth keeping"));
  // The restored words may be sent again only after the original POST can no
  // longer reserve a run. The fake backend rejects that duplicate otherwise.
  vi.mocked(api.chat).mockImplementation(async (_cid, _sid, _text, emit) => {
    if (!fenced) throw new Error("The original send still owns this attempt");
    emit({ delta: "A safely retried reply." });
    await new Promise(() => {});
  });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  expect(await screen.findByText("A safely retried reply.")).toBeInTheDocument();
});

test("a failed attempt fence keeps a reloaded composer locked and words pending", async () => {
  rememberSend();
  vi.mocked(api.findRun).mockResolvedValue({ run: null });
  vi.mocked(api.cancelAttempt).mockRejectedValue(new Error("offline"));
  vi.mocked(api.attemptState).mockResolvedValue({ attempt: "pending", retained: false, run: null });
  renderCampaign();
  await screen.findByRole("textbox");
  await waitFor(() => expect(screen.getByRole("button", { name: /(send ▸|continue ▶)/i })).toBeDisabled());
  expect(screen.getByRole("textbox")).toHaveValue("");

  vi.mocked(api.cancelAttempt).mockResolvedValue({ run: null });
  act(() => { document.dispatchEvent(new Event("visibilitychange")); });
  await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue("Words worth keeping"));
});

test("a terminal error learned from attempt-state is shown after recovery refreshes", async () => {
  rememberSend();
  vi.mocked(api.findRun).mockResolvedValue({ run: null });
  vi.mocked(api.cancelAttempt).mockResolvedValue({ run: null });
  vi.mocked(api.attemptState).mockResolvedValue({ attempt: "pending", retained: false,
    run: { ...RUN, state: "failed", error: { kind: "provider", detail: "The model refused the reply" } } });
  renderCampaign();
  expect(await screen.findByText(/The model refused the reply/)).toBeInTheDocument();
  expect(screen.getByRole("textbox")).toHaveValue("Words worth keeping");
});

test("a delayed recovery pass cannot settle a newer send on the same scene", async () => {
  rememberSend();
  vi.mocked(api.findRun).mockResolvedValue({ run: { ...RUN, state: "landed" } });
  let complete!: (state: Awaited<ReturnType<typeof api.attemptState>>) => void;
  vi.mocked(api.attemptState)
    .mockImplementationOnce(() => new Promise(resolve => { complete = resolve; }))
    .mockResolvedValue({ attempt: "pending", retained: false, run: null });
  renderCampaign();
  await waitFor(() => expect(api.attemptState).toHaveBeenCalled());

  // A second visibility pass resolves the old send while the first read is
  // delayed, and the player starts a fresh turn with the recovered words.
  act(() => { document.dispatchEvent(new Event("visibilitychange")); });
  await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue("Words worth keeping"));
  vi.mocked(api.chat).mockImplementation(async (_cid, _sid, _text, emit) => {
    emit({ delta: "A new reply is arriving." });
    await new Promise(() => {});
  });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  expect(await screen.findByText("A new reply is arriving.")).toBeInTheDocument();

  await act(async () => { complete({ attempt: "pending", retained: true,
    run: { ...RUN, state: "landed" } }); });
  expect(screen.getByText("A new reply is arriving.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /(send ▸|continue ▶)/i })).toBeDisabled();
});

test("Stop reports an unavailable cancellation while recovery is still discovering", async () => {
  rememberSend();
  vi.mocked(api.findRun).mockImplementationOnce(() => new Promise(() => {}));
  vi.mocked(api.cancelAttempt).mockRejectedValue(new Error("offline"));
  renderCampaign();
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  expect(await screen.findByText(/could not reach the server to stop this turn/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /(send ▸|continue ▶)/i })).toBeDisabled();

  vi.mocked(api.findRun).mockResolvedValue({ run: null });
  vi.mocked(api.cancelAttempt).mockResolvedValue({ run: null });
  vi.mocked(api.attemptState).mockResolvedValue({ attempt: "pending", retained: false, run: null });
  act(() => { document.dispatchEvent(new Event("visibilitychange")); });
  await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue("Words worth keeping"));
});

test.each([true, false])("a delayed recovery refresh cannot overwrite a new send (retained=%s)", async retained => {
  rememberSend();
  vi.mocked(api.findRun).mockResolvedValue({ run: { ...RUN, state: "landed" } });
  vi.mocked(api.attemptState).mockResolvedValue({ attempt: "pending", retained, run: null });
  const scene = { meta: { id: "s1", title: "Old" }, messages: [] };
  let complete!: (value: typeof scene) => void;
  vi.mocked(api.getScene).mockResolvedValueOnce(scene)
    .mockImplementationOnce(() => new Promise(resolve => { complete = resolve; }));
  renderCampaign();
  await waitFor(() => expect(complete).toBeTypeOf("function"));
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Mara waits." } });
  vi.mocked(api.chat).mockImplementation(async (_cid, _sid, _text, emit) => {
    emit({ delta: "A new reply is arriving." });
    await new Promise(() => {});
  });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await screen.findByText("A new reply is arriving.");
  await act(async () => { complete(scene); });
  expect(screen.getByText("A new reply is arriving.")).toBeInTheDocument();
  expect(screen.getByRole("textbox")).toHaveValue("");
  expect(screen.getByRole("button", { name: /(send ▸|continue ▶)/i })).toBeDisabled();
});

test.each(["running", "landed"] as const)(
  "a delayed lost-stream %s lookup cannot replace visibility recovery", async state => {
    let complete!: (value: { run: RunHandle }) => void;
    let asked = false;
    vi.mocked(api.findRun).mockImplementation(async (_cid, _sid, attempt) => {
      if (!attempt) return { run: null };
      if (!asked) {
        asked = true;
        return new Promise(resolve => { complete = resolve; });
      }
      return { run: { ...RUN, attempt_id: attempt } };
    });
    vi.mocked(api.chat).mockImplementation(async () => {
      throw Object.assign(new Error("Connection lost"), { beforeResponse: true });
    });
    let readers = 0;
    vi.mocked(api.attachRun).mockImplementation(async (_cid, _sid, _run, _from, emit) => {
      emit({ delta: readers++ ? "A duplicate observer replaced the reply." : "The adopted reply." });
      await new Promise(() => {});
    });
    renderCampaign();
    fireEvent.change(await screen.findByRole("textbox"), { target: { value: "Mara waits." } });
    fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
    await waitFor(() => expect(complete).toBeTypeOf("function"));
    act(() => { document.dispatchEvent(new Event("visibilitychange")); });
    await screen.findByText("The adopted reply.");

    await act(async () => { complete({ run: { ...RUN, state } }); });
    expect(screen.getByText("The adopted reply.")).toBeInTheDocument();
    expect(screen.queryByText("A duplicate observer replaced the reply.")).toBeNull();
    expect(screen.getByRole("button", { name: /(send ▸|continue ▶)/i })).toBeDisabled();
  },
);
