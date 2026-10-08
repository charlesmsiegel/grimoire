import { useState } from "react";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { ApiError, api, type ModelTestResult } from "../../api/client";
import { TestCallDialog, forgetModelTests, useModelTests } from "./TestCallDialog";
import { useHotkeys } from "../../shortcuts/useHotkeys";

vi.mock("../../api/client", async () => {
  const actual = await vi.importActual<typeof import("../../api/client")>("../../api/client");
  return { ...actual, api: { previewModelTest: vi.fn(), runModelTest: vi.fn() } };
});

function preview(over: Partial<any> = {}) {
  return {
    provider: "Saltmarch Router", provider_id: "saltmarch", model: "vendor/m",
    sends: [{ capability: "generate",
              description: "One chat message, “Reply with OK.”, with the reply capped at 64 tokens." }],
    estimated_cost_usd: 0.00012,
    ...over,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  // The runs are module scope (one store for every holder), so a run a test
  // left in flight would be rejoined by the next.
  forgetModelTests();
  (api.previewModelTest as any).mockResolvedValue(preview());
});

/** The dialog, held the way the picker holds it: the runs belong to the
 *  holder, which outlives the dialog. */
function Holder({ onClose, onDone }:
  { onClose: () => void; onDone: (r: ModelTestResult) => void }) {
  const tests = useModelTests(onDone);
  return <TestCallDialog provider="saltmarch" model="vendor/m" capabilities={["generate"]}
                         tests={tests} onClose={onClose} />;
}

function open(onClose = vi.fn(), onDone = vi.fn()) {
  render(<Holder onClose={onClose} onDone={onDone} />);
  return { onClose, onDone };
}

test("TestCallDialog sends nothing until Run test", async () => {
  (api.runModelTest as any).mockResolvedValue({
    provider: "saltmarch", model: "vendor/m", rev: "r1",
    results: { generate: { ok: true } }, recorded: true });
  const { onDone } = open();

  const dialog = await screen.findByRole("dialog", { name: "Test a model" });
  // The preview states what is sent, to whom, and what it may cost.
  expect(api.previewModelTest).toHaveBeenCalledWith(
    "saltmarch", { model: "vendor/m", capabilities: ["generate"] });
  expect(dialog).toHaveTextContent("Saltmarch Router");
  expect(dialog).toHaveTextContent("vendor/m");
  expect(dialog).toHaveTextContent("One chat message");
  expect(dialog).toHaveTextContent("$0.0001");
  expect(api.runModelTest).not.toHaveBeenCalled();

  fireEvent.click(within(dialog).getByRole("button", { name: "Run test" }));

  expect(await within(dialog).findByText(/generate: works/i)).toBeInTheDocument();
  expect(api.runModelTest).toHaveBeenCalledTimes(1);
  expect(vi.mocked(api.runModelTest).mock.calls[0].slice(0, 2)).toEqual(
    ["saltmarch", { model: "vendor/m", capabilities: ["generate"], confirm: true }]);
  expect(onDone).toHaveBeenCalledWith(expect.objectContaining({ recorded: true }));
});

test("Run test cannot be pressed twice", async () => {
  // A run that never lands, so the dialog stays in flight.
  (api.runModelTest as any).mockImplementation(() => new Promise(() => {}));
  open();

  const run = await screen.findByRole("button", { name: "Run test" });
  // Both presses inside one act, so React has not re-rendered the button
  // disabled between them -- the gap only the synchronous guard covers.
  act(() => {
    fireEvent.click(run);
    fireEvent.click(run);
  });

  expect(api.runModelTest).toHaveBeenCalledTimes(1);
  expect(await screen.findByRole("button", { name: /testing/i })).toBeDisabled();
});

test("a lost wait asks again under the same attempt", async () => {
  // The network dropped the wait, not the server refusing: the run may still
  // be going, so the next Run must name it rather than start another.
  (api.runModelTest as any)
    .mockRejectedValueOnce(new TypeError("Failed to fetch"))
    .mockImplementationOnce(() => new Promise(() => {}));
  open();

  fireEvent.click(await screen.findByRole("button", { name: "Run test" }));
  expect(await screen.findByText(/could not run/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Run test" }));

  expect(api.runModelTest).toHaveBeenCalledTimes(2);
  const [first, second] = vi.mocked(api.runModelTest).mock.calls;
  expect(first[2]?.attempt).toBeTruthy();
  expect(second[2]?.attempt).toBe(first[2]?.attempt);
});

test("a refused run lets its attempt go", async () => {
  (api.runModelTest as any)
    .mockRejectedValueOnce(new ApiError(502, "the provider is down", "upstream"))
    .mockImplementationOnce(() => new Promise(() => {}));
  open();

  fireEvent.click(await screen.findByRole("button", { name: "Run test" }));
  expect(await screen.findByText(/provider is down/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Run test" }));

  // The server answered, so the next Run is a new test, not a rejoin of the
  // failed one (which would hand back the same failure).
  const [first, second] = vi.mocked(api.runModelTest).mock.calls;
  expect(second[2]?.attempt).not.toBe(first[2]?.attempt);
});

/** A holder with a Test… button, the way a model page or the picker has one. */
function Opener({ onLanded }: { onLanded?: (r: ModelTestResult) => void }) {
  const tests = useModelTests(onLanded);
  const [shown, setShown] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setShown(true)}>Test…</button>
      {shown && <TestCallDialog provider="saltmarch" model="vendor/m"
                                capabilities={["generate"]} tests={tests}
                                onClose={() => setShown(false)} />}
    </>
  );
}

test("a run started before a remount is rejoined, not paid for again", async () => {
  let land: (r: ModelTestResult) => void = () => {};
  (api.runModelTest as any).mockImplementation(
    () => new Promise<ModelTestResult>((resolve) => { land = resolve; }));
  const firstLanded = vi.fn();
  const first = render(<Opener onLanded={firstLanded} />);
  fireEvent.click(screen.getByRole("button", { name: "Test…" }));
  fireEvent.click(await screen.findByRole("button", { name: "Run test" }));
  expect(await screen.findByRole("button", { name: /testing/i })).toBeDisabled();
  // The holder goes (a navigation, another page) while the run is out.
  first.unmount();

  const secondLanded = vi.fn();
  render(<Opener onLanded={secondLanded} />);
  fireEvent.click(screen.getByRole("button", { name: "Test…" }));
  const dialog = await screen.findByRole("dialog", { name: "Test a model" });
  // The new holder's dialog shows the live run; there is nothing to press.
  expect(within(dialog).getByRole("button", { name: /testing/i })).toBeDisabled();
  expect(api.runModelTest).toHaveBeenCalledTimes(1);

  await act(async () => {
    land({ provider: "saltmarch", model: "vendor/m", rev: "r1",
           results: { generate: { ok: true } }, recorded: true });
  });
  expect(await within(dialog).findByText(/generate: works/i)).toBeInTheDocument();
  // Heard by the holder that is here, not the one that started it and left.
  expect(secondLanded).toHaveBeenCalledTimes(1);
  expect(firstLanded).not.toHaveBeenCalled();
  expect(api.runModelTest).toHaveBeenCalledTimes(1);
});

test("the dialog is modal: it takes focus and gives it back", async () => {
  render(<Opener />);
  const opener = screen.getByRole("button", { name: "Test…" });
  opener.focus();
  fireEvent.click(opener);

  const dialog = await screen.findByRole("dialog", { name: "Test a model" });
  expect(dialog).toHaveAttribute("aria-modal", "true");
  expect(dialog).toHaveFocus();

  fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(opener).toHaveFocus();
});

test("focus that leaves the dialog wraps back into it", async () => {
  render(<Opener />);
  const opener = screen.getByRole("button", { name: "Test…" });
  opener.focus();
  fireEvent.click(opener);
  const dialog = await screen.findByRole("dialog", { name: "Test a model" });
  const run = within(dialog).getByRole("button", { name: "Run test" });
  const close = within(dialog).getByRole("button", { name: "Close" });

  // Tab past the last control lands on the page behind: it wraps to the first.
  close.focus();
  opener.focus();
  expect(run).toHaveFocus();
  // ...and Shift+Tab before the first wraps to the last.
  opener.focus();
  expect(close).toHaveFocus();

  // Closed, it holds nothing.
  fireEvent.click(close);
  expect(opener).toHaveFocus();
  const elsewhere = document.createElement("button");
  document.body.appendChild(elsewhere);
  elsewhere.focus();
  expect(elsewhere).toHaveFocus();
  elsewhere.remove();
});

test("an overlay drawn over the dialog keeps its own focus", async () => {
  function Sheet() {
    useHotkeys([], { modal: true });
    return <button type="button">On top</button>;
  }
  render(<Opener />);
  fireEvent.click(screen.getByRole("button", { name: "Test…" }));
  await screen.findByRole("dialog", { name: "Test a model" });
  render(<Sheet />);
  const top = screen.getByRole("button", { name: "On top" });
  top.focus();
  expect(top).toHaveFocus();
});

test("never shows a zero cost for an unknown price", async () => {
  (api.previewModelTest as any).mockResolvedValue(preview({ estimated_cost_usd: null }));
  open();

  const dialog = await screen.findByRole("dialog", { name: "Test a model" });
  expect(await within(dialog).findByText(/cost unknown — one tiny request/))
    .toBeInTheDocument();
  expect(dialog).not.toHaveTextContent("$0");
});

test("a preview the server refuses says why and offers no Run", async () => {
  (api.previewModelTest as any).mockRejectedValue(
    new ApiError(409, "This connection has no API key.", "missing_key"));
  open();

  expect(await screen.findByText(/no API key/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Run test" })).toBeDisabled();
  expect(api.runModelTest).not.toHaveBeenCalled();
});

test("Escape closes the test dialog", async () => {
  const { onClose } = open();
  await screen.findByRole("dialog", { name: "Test a model" });
  fireEvent.keyDown(window, { key: "Escape" });
  expect(onClose).toHaveBeenCalledTimes(1);
});

test("a run forgotten while in flight never answers for the next one", async () => {
  // Two runs for one model, a forget between them: what the first does when
  // it finally settles is not the second's to hear, or to lose its slot to.
  const lands: ((r: ModelTestResult) => void)[] = [];
  const fails: ((e: unknown) => void)[] = [];
  (api.runModelTest as any).mockImplementation(() => new Promise<ModelTestResult>(
    (resolve, reject) => { lands.push(resolve); fails.push(reject); }));
  const heard = vi.fn();
  let tests: ReturnType<typeof useModelTests> | null = null;
  function Hold() { tests = useModelTests(heard); return null; }
  render(<Hold />);
  const held = () => tests!;

  const first = held().start("saltmarch", "vendor/m", ["generate"]);
  forgetModelTests();
  const second = held().start("saltmarch", "vendor/m", ["generate"]);
  expect(second).not.toBe(first);

  await act(async () => {
    lands[0]({ provider: "saltmarch", model: "vendor/m", rev: "r0",
               results: { generate: { ok: false, error: "stale" } }, recorded: true });
  });
  // The stale run neither emptied the slot nor spoke for the live one.
  expect(held().live("saltmarch", "vendor/m")).toBe(second);
  expect(heard).not.toHaveBeenCalled();

  // A stale run's lost wait does not overwrite the live one with its attempt.
  forgetModelTests();
  const third = held().start("saltmarch", "vendor/m", ["generate"]);
  await act(async () => {
    fails[1](new TypeError("Failed to fetch"));
    await second.catch(() => {});
  });
  expect(held().live("saltmarch", "vendor/m")).toBe(third);

  await act(async () => {
    lands[2]({ provider: "saltmarch", model: "vendor/m", rev: "r2",
               results: { generate: { ok: true } }, recorded: true });
  });
  expect(heard).toHaveBeenCalledTimes(1);
  expect(heard).toHaveBeenCalledWith(expect.objectContaining({ rev: "r2" }));
  expect(held().live("saltmarch", "vendor/m")).toBeUndefined();
});

test("Run test hands focus to the dialog, and the outcome is announced", async () => {
  // Run test disables under the reader while the test runs: focus left on it
  // dropped to the body, and the outcome arrived with nothing to say so.
  let land: (r: ModelTestResult) => void = () => {};
  (api.runModelTest as any).mockReturnValue(new Promise((r) => { land = r; }));
  open();
  const dialog = await screen.findByRole("dialog", { name: "Test a model" });
  const run = within(dialog).getByRole("button", { name: "Run test" });
  run.focus();
  fireEvent.click(run);

  expect(await within(dialog).findByRole("button", { name: /testing/i })).toBeDisabled();
  expect(document.activeElement).toBe(dialog);

  const status = within(dialog).getByRole("status");
  expect(status).toHaveAttribute("aria-live", "polite");
  await act(async () => {
    land({ provider: "saltmarch", model: "vendor/m", rev: "r1",
           results: { generate: { ok: false, kind: "provider", error: "402 spend limit" } },
           recorded: false });
  });
  expect(await within(status).findByText(/generate/i)).toBeInTheDocument();
});
