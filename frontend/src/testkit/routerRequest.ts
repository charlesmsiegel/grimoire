const NativeRequest = globalThis.Request;

/** A data-router request in jsdom: Node's Request rejects jsdom's signal at
 *  construction even though the router can use it. Keep the native request's
 *  URL/body handling and attach the original signal afterward, so navigation
 *  cancellation still reaches the router's loaders. Shared by route suites
 *  that exercise the app's real data host rather than a MemoryRouter alone. */
export class RouterRequest extends NativeRequest {
  constructor(input: RequestInfo | URL, init?: RequestInit) {
    const { signal, ...rest } = init ?? {};
    super(input, rest);
    if (signal) Object.defineProperty(this, "signal", { value: signal });
  }
}
