// Hoisted mock factories cannot import the api they replace. Keep this
// factory independent; the greeting suites load it through a deferred import.
export function greetingApiMock() {
  return {
  // The editor branches on `instanceof ApiError` to tell a stale-record 409
  // from any other failure; declared in here because `vi.mock` is hoisted
  // above every top-level statement in the file.
  ApiError: class extends Error {
    constructor(public status: number, public detail: string, public kind?: string,
                public body?: Record<string, unknown>) {
      super(detail);
    }
  },
  api: {
    // The campaign-scope sidebar's LibraryPanel (#52). "already library
    // content, unedited" renders no button, so this suite is unchanged;
    // LibraryPanel.test.tsx owns the panel's own behaviour.
    libraryStatus: vi.fn().mockResolvedValue(
      { in_library: true, diverged: false, can_promote: false, can_push: false }),
    promoteToLibrary: vi.fn(), pushToLibrary: vi.fn(),
    libraryDependents: vi.fn().mockResolvedValue([]), demoteFromLibrary: vi.fn(),
    listGreetings: vi.fn(), listCharacters: vi.fn(), listTags: vi.fn(), readGreeting: vi.fn(),
    createGreeting: vi.fn(), updateGreeting: vi.fn(), deleteGreeting: vi.fn(),
    setEdges: vi.fn(), importGreetings: vi.fn(), listEntities: vi.fn(),
    getGreetingSubjects: vi.fn(), setImageSubjects: vi.fn(), listUntaggedImages: vi.fn(), markGreeting: vi.fn(),
  },
  };
}
