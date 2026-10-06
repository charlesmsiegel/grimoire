// The touch gesture: which finger travels count as a swipe, and the ways a
// gesture that looks like one is not.
import { render, fireEvent } from "@testing-library/react";
import { SWIPE_MIN_PX, useSwipe } from "./useSwipe";
import { TestPointerEvent } from "../../testkit/pointer";

const onNext = vi.fn();
const onPrevious = vi.fn();

function Target({ enabled = true }: { enabled?: boolean }) {
  const handlers = useSwipe({ enabled, onNext, onPrevious });
  return (
    <div data-testid="msg" {...handlers}>
      <p data-testid="prose">Words.</p>
      <button data-testid="button">Go</button>
      <details><summary data-testid="summary">More</summary>hidden</details>
      <div data-testid="wide"><span data-testid="cell">cell</span></div>
    </div>
  );
}

type Init = { pointerType?: string; pointerId?: number; x?: number; y?: number };
function send(el: Element, type: string, { pointerType = "touch", pointerId = 7, x = 0, y = 0 }: Init = {}) {
  fireEvent(el, new TestPointerEvent(type, {
    bubbles: true, cancelable: true, pointerType, pointerId, clientX: x, clientY: y,
  }));
}

/** A drag from (100, 100) by (dx, dy), on `el` unless named. */
function drag(dx: number, dy = 0, el?: Element, init: Init = {}) {
  const target = el ?? document.querySelector("[data-testid=prose]")!;
  send(target, "pointerdown", { ...init, x: 100, y: 100 });
  send(target, "pointerup", { ...init, x: 100 + dx, y: 100 + dy });
}

beforeEach(() => {
  vi.clearAllMocks();
});
afterEach(() => {
  vi.restoreAllMocks();
});

const byId = (id: string) => document.querySelector(`[data-testid=${id}]`)!;

it("a left drag past the threshold is next, once", () => {
  render(<Target />);
  drag(-80, 10);
  expect(onNext).toHaveBeenCalledTimes(1);
  expect(onPrevious).not.toHaveBeenCalled();
});

it("a right drag past the threshold is previous, once", () => {
  render(<Target />);
  drag(80, 10);
  expect(onPrevious).toHaveBeenCalledTimes(1);
  expect(onNext).not.toHaveBeenCalled();
});

it("the threshold itself counts", () => {
  render(<Target />);
  drag(-SWIPE_MIN_PX);
  expect(onNext).toHaveBeenCalledTimes(1);
});

it("a short drag does nothing", () => {
  render(<Target />);
  drag(-50);
  expect(onNext).not.toHaveBeenCalled();
});

it("a drag that is mostly vertical does nothing", () => {
  render(<Target />);
  drag(-80, 50);
  expect(onNext).not.toHaveBeenCalled();
});

it("a mouse does nothing", () => {
  render(<Target />);
  drag(-80, 0, undefined, { pointerType: "mouse" });
  expect(onNext).not.toHaveBeenCalled();
  drag(-80);
  expect(onNext).toHaveBeenCalledTimes(1);
});

it("a cancelled pointer does nothing", () => {
  render(<Target />);
  send(byId("prose"), "pointerdown", { x: 100, y: 100 });
  send(byId("prose"), "pointercancel", { x: 100, y: 100 });
  send(byId("prose"), "pointerup", { x: 20, y: 100 });
  expect(onNext).not.toHaveBeenCalled();
  drag(-80);
  expect(onNext).toHaveBeenCalledTimes(1);
});

it("a second pointer discards the gesture", () => {
  render(<Target />);
  send(byId("prose"), "pointerdown", { x: 100, y: 100 });
  send(byId("prose"), "pointerdown", { pointerId: 8, x: 300, y: 100 });
  send(byId("prose"), "pointerup", { x: 20, y: 100 });
  send(byId("prose"), "pointerup", { pointerId: 8, x: 220, y: 100 });
  expect(onNext).not.toHaveBeenCalled();
  expect(onPrevious).not.toHaveBeenCalled();
  drag(-80);
  expect(onNext).toHaveBeenCalledTimes(1);
});

it("an up from another pointer than the one that went down does nothing", () => {
  render(<Target />);
  send(byId("prose"), "pointerdown", { x: 100, y: 100 });
  send(byId("prose"), "pointerup", { pointerId: 9, x: 20, y: 100 });
  expect(onNext).not.toHaveBeenCalled();
});

it.each([
  ["a button", "button"],
  ["a summary", "summary"],
])("a drag that starts on %s does nothing", (_name, id) => {
  render(<Target />);
  drag(-80, 0, byId(id));
  expect(onNext).not.toHaveBeenCalled();
  drag(-80);
  expect(onNext).toHaveBeenCalledTimes(1);
});

it("a drag that starts inside a horizontally scrollable element does nothing", () => {
  render(<Target />);
  const wide = byId("wide");
  Object.defineProperty(wide, "scrollWidth", { configurable: true, value: 600 });
  Object.defineProperty(wide, "clientWidth", { configurable: true, value: 300 });
  drag(-80, 0, byId("cell"));
  expect(onNext).not.toHaveBeenCalled();
  drag(-80);
  expect(onNext).toHaveBeenCalledTimes(1);
});

it("the handler element's own overflow does not disqualify a drag", () => {
  render(<Target />);
  const root = byId("msg");
  Object.defineProperty(root, "scrollWidth", { configurable: true, value: 600 });
  Object.defineProperty(root, "clientWidth", { configurable: true, value: 300 });
  drag(-80);
  expect(onNext).toHaveBeenCalledTimes(1);
});

it("a text selection at the end does nothing", () => {
  render(<Target />);
  const sel = vi.spyOn(window, "getSelection").mockReturnValue({ toString: () => "Words" } as Selection);
  drag(-80);
  expect(onNext).not.toHaveBeenCalled();
  sel.mockReturnValue({ toString: () => "" } as Selection);
  drag(-80);
  expect(onNext).toHaveBeenCalledTimes(1);
});

it("does nothing when disabled, and acts again once enabled", () => {
  const { rerender } = render(<Target enabled={false} />);
  drag(-80);
  expect(onNext).not.toHaveBeenCalled();
  rerender(<Target enabled />);
  drag(-80);
  expect(onNext).toHaveBeenCalledTimes(1);
});

it("reads the latest callbacks without changing the handlers it hands out", () => {
  const seen: unknown[] = [];
  const first = vi.fn();
  const second = vi.fn();
  function Probe({ cb }: { cb: () => void }) {
    const h = useSwipe({ enabled: true, onNext: cb, onPrevious });
    seen.push(h);
    return <div data-testid="p" {...h} />;
  }
  const { rerender } = render(<Probe cb={first} />);
  rerender(<Probe cb={second} />);
  expect(seen[1]).toBe(seen[0]);
  drag(-80, 0, byId("p"));
  expect(first).not.toHaveBeenCalled();
  expect(second).toHaveBeenCalledTimes(1);
});
