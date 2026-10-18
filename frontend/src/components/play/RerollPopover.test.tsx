import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { api, type InferenceSettings } from "../../api/client";
import { fixture } from "../../testkit/fixture";
import RerollPopover from "./RerollPopover";

vi.mock("../../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/client")>();
  return { ...actual, api: { ...actual.api, getCampaignInference: vi.fn() } };
});

const resizes = new Set<() => void>();
class LayoutObserver implements ResizeObserver {
  readonly notify = () => this.callback([], this);
  constructor(private readonly callback: ResizeObserverCallback) { resizes.add(this.notify); }
  observe() {}
  unobserve() {}
  disconnect() { resizes.delete(this.notify); }
}

let streamBox: DOMRect;
let anchorBox: DOMRect;
const actions = { setRerollPrompt: vi.fn(), setRerollRoute: vi.fn(), reroll: vi.fn() };

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal("ResizeObserver", LayoutObserver);
  vi.stubGlobal("innerWidth", 1440);
  vi.stubGlobal("innerHeight", 900);
  streamBox = new DOMRect(510, 279, 930, 446);
  anchorBox = new DOMRect(536, 400, 26, 45);
  vi.mocked(api.getCampaignInference).mockResolvedValue(fixture<InferenceSettings>({ routes: [], providers: [], presets: [] }));
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
    if (this.classList.contains("stream")) return streamBox;
    if (this.classList.contains("msg-gutter")) return anchorBox;
    if (this.classList.contains("reroll-close")) return new DOMRect(0, 0, 80, 52);
    if (this.classList.contains("reroll-guide")) return new DOMRect(0, 0, 350, 44);
    if (this.classList.contains("reroll-pop")) {
      const maxHeight = parseFloat(this.style.maxHeight);
      const maxWidth = parseFloat(this.style.maxWidth);
      const left = parseFloat(this.style.left), top = parseFloat(this.style.top);
      return new DOMRect(Number.isFinite(left) ? left : innerWidth <= 720 ? anchorBox.left : anchorBox.right + 10,
        Number.isFinite(top) ? top : anchorBox.bottom - 372,
        Math.min(350, Number.isFinite(maxWidth) ? maxWidth : 350),
        Math.min(372, Number.isFinite(maxHeight) ? maxHeight : 372));
    }
    return new DOMRect();
  });
});

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

function Form() {
  return <div className="stream"><span className="msg-gutter">
    <RerollPopover cid="run"
      pop={{ prompt: "Mara waits", route: { provider: "", model: "", preset: "" } }}
      rolling={false} generationDisabled={false} actions={actions} />
  </span></div>;
}

function popup() {
  const node = screen.getByLabelText("Reroll guidance").closest(".reroll-pop");
  if (!(node instanceof HTMLElement)) throw new Error("Missing reroll form");
  return node;
}

async function showForm() {
  render(<Form />);
  await screen.findByLabelText("Reroll preset");
}

function expectInsideTranscript() {
  const box = popup().getBoundingClientRect();
  expect(box.top).toBeGreaterThanOrEqual(streamBox.top);
  expect(box.bottom).toBeLessThanOrEqual(streamBox.bottom);
  expect(box.left).toBeGreaterThanOrEqual(streamBox.left);
  expect(box.right).toBeLessThanOrEqual(streamBox.right);
}

test("an early-post reroll keeps its model controls below the scene head", async () => {
  await showForm();
  expectInsideTranscript();
  expect(popup().closest(".stream")).toBeNull();
});

test("a late-post reroll stays above the composer", async () => {
  anchorBox = new DOMRect(536, 680, 26, 20);
  await showForm();
  expectInsideTranscript();
});

test("a phone's short transcript viewport caps the picker height", async () => {
  vi.stubGlobal("innerWidth", 390);
  vi.stubGlobal("innerHeight", 844);
  streamBox = new DOMRect(0, 257, 390, 278);
  anchorBox = new DOMRect(16, 370, 26, 45);
  await showForm();
  expectInsideTranscript();
  expect(parseFloat(popup().style.maxHeight)).toBeLessThan(278);
});

test("a resized transcript keeps the same draft inside its new visible bounds", async () => {
  await showForm();
  streamBox = new DOMRect(0, 130, 390, 260);
  anchorBox = new DOMRect(16, 220, 26, 45);
  vi.stubGlobal("innerWidth", 390);
  vi.stubGlobal("innerHeight", 600);
  act(() => { resizes.forEach((notify) => notify()); });
  expectInsideTranscript();
  expect(screen.getByLabelText("Reroll guidance")).toHaveValue("Mara waits");
});

test.each([0, 32])("a %ipx transcript uses the viewport to keep Close, guidance and a touch row readable", async (height) => {
  vi.stubGlobal("innerWidth", 390);
  vi.stubGlobal("innerHeight", 400);
  streamBox = new DOMRect(0, 257, 390, height);
  anchorBox = new DOMRect(16, 260, 26, 45);
  await showForm();
  const box = popup().getBoundingClientRect();
  expect(box.height).toBeGreaterThanOrEqual(52 + 44 + 44);
  expect(box.top).toBeGreaterThanOrEqual(0);
  expect(box.bottom).toBeLessThanOrEqual(400);
  expect(popup()).toHaveClass("reroll-pop-viewport");
  expect(screen.getByLabelText("Reroll guidance")).toHaveValue("Mara waits");
});

test("the keyboard's visual viewport resize keeps the open draft and controls visible", async () => {
  const viewport = Object.assign(new EventTarget(), { offsetTop: 0, offsetLeft: 0, height: 844, width: 390 });
  vi.stubGlobal("visualViewport", viewport);
  vi.stubGlobal("innerWidth", 390);
  vi.stubGlobal("innerHeight", 844);
  streamBox = new DOMRect(0, 257, 390, 278);
  await showForm();
  expectInsideTranscript();
  viewport.height = 200;
  viewport.offsetTop = 100;
  act(() => { viewport.dispatchEvent(new Event("resize")); });
  const box = popup().getBoundingClientRect();
  expect(box.height).toBeGreaterThanOrEqual(52 + 44 + 44);
  expect(box.top).toBeGreaterThanOrEqual(100);
  expect(box.bottom).toBeLessThanOrEqual(300);
  expect(screen.getByLabelText("Reroll guidance")).toHaveValue("Mara waits");
});

test("scrolling follows the post without moving the form outside the transcript", async () => {
  await showForm();
  const before = popup().getBoundingClientRect().top;
  anchorBox = new DOMRect(536, 680, 26, 20);
  const stream = document.querySelector(".stream");
  if (!stream) throw new Error("Missing transcript");
  fireEvent.scroll(stream);
  expect(popup().getBoundingClientRect().top).not.toBe(before);
  expectInsideTranscript();
});

test("touch can close the reroll form without submitting it", async () => {
  await showForm();
  fireEvent.click(screen.getByRole("button", { name: "Close the reroll box" }));
  expect(actions.setRerollPrompt).toHaveBeenCalledWith(null);
  expect(actions.reroll).not.toHaveBeenCalled();
});

test("Enter on Close dismisses the form without submitting a reroll", async () => {
  await showForm();
  screen.getByRole("button", { name: "Close the reroll box" }).focus();
  await userEvent.keyboard("{Enter}");
  expect(actions.setRerollPrompt).toHaveBeenCalledWith(null);
  expect(actions.reroll).not.toHaveBeenCalled();
});

test("Enter in the guidance still submits its reroll", async () => {
  await showForm();
  screen.getByLabelText("Reroll guidance").focus();
  await userEvent.keyboard("{Enter}");
  expect(actions.reroll).toHaveBeenCalledOnce();
  expect(actions.setRerollPrompt).not.toHaveBeenCalled();
});
import { Profiler } from "react";

test.each([446, 32])("identical observed geometry at %ipx transcript height does not commit another layout", async (height) => {
  streamBox = new DOMRect(510, 279, 930, height);
  const committed = vi.fn();
  render(<Profiler id="reroll" onRender={committed}><Form /></Profiler>);
  await screen.findByLabelText("Reroll preset");
  committed.mockClear();
  act(() => { resizes.forEach((notify) => notify()); });
  expect(committed).not.toHaveBeenCalled();
});

test("unmount retires resize and scroll measurements", async () => {
  const view = render(<Form />);
  await screen.findByLabelText("Reroll preset");
  view.unmount();
  expect(resizes.size).toBe(0);
  const measured = vi.spyOn(HTMLElement.prototype, "getBoundingClientRect");
  measured.mockClear();
  fireEvent.resize(window);
  fireEvent.scroll(document);
  expect(measured).not.toHaveBeenCalled();
});
