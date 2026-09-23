// The intent reading behind a prefetch: what counts as a reader meaning to
// open a world, as opposed to passing over its card on the way elsewhere.
import { createElement } from "react";
import { render, fireEvent } from "@testing-library/react";
import { INTENT_DWELL_MS, intentProps, resetPrefetch } from "./prefetch";

vi.mock("./client", () => ({
  api: {
    getWorld: vi.fn(), listCharacters: vi.fn(), listAppearances: vi.fn(),
    rememberedWorld: vi.fn(), rememberedCharacters: vi.fn(), rememberedAppearances: vi.fn(),
  },
}));
import { api } from "./client";

/** jsdom has no PointerEvent, and testing-library then falls back to a bare
 *  Event -- which drops `pointerType`, `pointerId` and the coordinates, the
 *  very fields a touch is told apart by. The smallest stand-in that keeps
 *  them, so these tests drive the real React handlers rather than calling
 *  the functions directly. */
class TestPointerEvent extends MouseEvent {
  pointerType: string;
  pointerId: number;
  constructor(type: string, init: PointerEventInit = {}) {
    super(type, init);
    this.pointerType = init.pointerType ?? "";
    this.pointerId = init.pointerId ?? 0;
  }
}

const WORLD = { kind: "world" as const, id: "saltmarch" };
const finger = (x = 40, y = 40) => ({ pointerType: "touch", pointerId: 7, clientX: x, clientY: y });

/** A card whose handlers are `intentProps`' own. */
function card(): HTMLElement {
  render(createElement("button", intentProps(WORLD), "Saltmarch"));
  return document.querySelector("button")!;
}

/** Let a started prefetch reach the api: its reads are begun inside a promise. */
async function settled(ms = 0) {
  vi.advanceTimersByTime(ms);
  await Promise.resolve();
  await Promise.resolve();
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal("PointerEvent", TestPointerEvent);
  vi.clearAllMocks();
  resetPrefetch();
  (api.getWorld as any).mockResolvedValue({ meta: { id: "saltmarch", name: "Saltmarch" }, body: "" });
  (api.listCharacters as any).mockResolvedValue([]);
  (api.rememberedWorld as any).mockReturnValue(undefined);
  (api.rememberedCharacters as any).mockReturnValue(undefined);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

// ---- touch: a finger only means it once it rests ----

test("a finger that lands on a card and scrolls away prefetches nothing", async () => {
  // Every card a scroll gesture starts on used to be prefetched on touchstart,
  // loading the server exactly while the reader was still choosing.
  const el = card();
  fireEvent.pointerDown(el, finger(40, 40));
  fireEvent.pointerMove(el, finger(40, 58));
  await settled(INTENT_DWELL_MS * 3);
  fireEvent.pointerUp(el, finger(40, 90));
  await settled(INTENT_DWELL_MS);
  expect(api.getWorld).not.toHaveBeenCalled();
  expect(api.listCharacters).not.toHaveBeenCalled();
});

test("a scroll the browser takes over is not a tap either", async () => {
  // Once the page scrolls, the browser cancels the pointer rather than lifting
  // it, and may do so before the finger has drifted far enough to say so.
  const el = card();
  fireEvent.pointerDown(el, finger());
  fireEvent.pointerCancel(el, finger());
  await settled(INTENT_DWELL_MS * 3);
  expect(api.listCharacters).not.toHaveBeenCalled();
});

test("nothing is asked the instant a finger lands", async () => {
  const el = card();
  fireEvent.pointerEnter(el, finger());   // a touch enters as it lands
  fireEvent.pointerDown(el, finger());
  await settled(INTENT_DWELL_MS - 10);
  expect(api.listCharacters).not.toHaveBeenCalled();
});

test("a finger resting on a card prefetches once, and lifting it adds nothing", async () => {
  const el = card();
  fireEvent.pointerDown(el, finger(40, 40));
  fireEvent.pointerMove(el, finger(43, 42));   // a finger is never perfectly still
  await settled(INTENT_DWELL_MS);
  expect(api.getWorld).toHaveBeenCalledWith("saltmarch");
  expect(api.listCharacters).toHaveBeenCalledWith(WORLD);
  fireEvent.pointerUp(el, finger(43, 42));
  await settled(INTENT_DWELL_MS * 2);
  expect(api.getWorld).toHaveBeenCalledTimes(1);
  expect(api.listCharacters).toHaveBeenCalledTimes(1);
});

test("a quick tap prefetches on the lift, ahead of the click it starts", async () => {
  const el = card();
  fireEvent.pointerDown(el, finger());
  await settled(20);
  fireEvent.pointerUp(el, finger());
  await settled();
  expect(api.listCharacters).toHaveBeenCalledWith(WORLD);
  // ...and the dwell it cut short does not ask a second time
  await settled(INTENT_DWELL_MS * 2);
  expect(api.listCharacters).toHaveBeenCalledTimes(1);
});

test("touchstart alone asks for nothing", async () => {
  const el = card();
  fireEvent.touchStart(el);
  await settled(INTENT_DWELL_MS * 2);
  expect(api.listCharacters).not.toHaveBeenCalled();
});

// ---- mouse and keyboard: as they were ----

test("a mouse press prefetches at once", async () => {
  const el = card();
  fireEvent.pointerDown(el, { pointerType: "mouse", pointerId: 1 });
  await settled();
  expect(api.listCharacters).toHaveBeenCalledWith(WORLD);
});

test("a mouse resting on a card prefetches after the dwell, and a passing one does not", async () => {
  const el = card();
  fireEvent.pointerEnter(el, { pointerType: "mouse", pointerId: 1 });
  fireEvent.pointerLeave(el, { pointerType: "mouse", pointerId: 1 });
  await settled(INTENT_DWELL_MS * 2);
  expect(api.listCharacters).not.toHaveBeenCalled();

  fireEvent.pointerEnter(el, { pointerType: "mouse", pointerId: 1 });
  // a mouse moving over the card it rests on is hovering, not scrolling
  fireEvent.pointerMove(el, { pointerType: "mouse", pointerId: 1, clientX: 90, clientY: 5 });
  await settled(INTENT_DWELL_MS);
  expect(api.listCharacters).toHaveBeenCalledWith(WORLD);
});

test("a pen press is a press, not a touch", async () => {
  const el = card();
  fireEvent.pointerDown(el, { pointerType: "pen", pointerId: 2 });
  await settled();
  expect(api.listCharacters).toHaveBeenCalledWith(WORLD);
});

test("keyboard focus resting on a card prefetches after the dwell", async () => {
  const el = card();
  fireEvent.focus(el);
  await settled(INTENT_DWELL_MS - 10);
  expect(api.listCharacters).not.toHaveBeenCalled();
  await settled(10);
  expect(api.listCharacters).toHaveBeenCalledWith(WORLD);
});
