/** jsdom has no PointerEvent, and testing-library then falls back to a bare
 *  Event -- which drops `pointerType`, `pointerId` and the coordinates, the
 *  very fields a touch is told apart by. The smallest stand-in that keeps
 *  them, so a test drives the real React handlers rather than calling the
 *  functions directly. Either stub it over the global
 *  (`vi.stubGlobal("PointerEvent", TestPointerEvent)`, so `fireEvent.pointerDown`
 *  builds it) or dispatch one yourself with `fireEvent(el, new TestPointerEvent(...))`. */
export class TestPointerEvent extends MouseEvent {
  pointerType: string;
  pointerId: number;
  constructor(type: string, init: PointerEventInit = {}) {
    super(type, init);
    this.pointerType = init.pointerType ?? "";
    this.pointerId = init.pointerId ?? 0;
  }
}
