# Frontend test behavior and coverage

Source: [vite.config.ts](../../../../../frontend/vite.config.ts), [test-setup.ts](../../../../../frontend/src/test-setup.ts), [campaignMocks.tsx](../../../../../frontend/src/testkit/campaignMocks.tsx), [campaignHarness.tsx](../../../../../frontend/src/testkit/campaignHarness.tsx).

  - **In a frontend test, an `await` means the page has SETTLED**, not that the
    query it named passed. `src/test-setup.ts` wraps React Testing Library's
    `asyncWrapper` so every `findBy*`/`waitFor` returns only once the rendered
    DOM has gone quiet. Without it, the statement after a test's first `await`
    runs against a page still building itself — a control still `disabled` (and
    `fireEvent.click` on one dispatches nothing and reports nothing), a gutter
    not yet rendered, a `<select>` not yet filled. That is invisible on an idle
    machine and reds CI on a shared runner, in a different test every run
    (#351). `settle.test.tsx` is the guarantee, deterministically — it fails
    both with the wrapper gone and with it settling for one unchanged tick
    instead of two, which is the rule that carries the invisible stages.
  - `check-web` runs `npm run test:coverage` (`vitest run --coverage`), which
    writes `frontend/coverage/lcov.info` — gitignored, uploaded by CI as the
    `frontend-coverage` artifact, and the file external readers discover by
    name. `npm test` still runs the suite bare when you only want pass/fail.
    Coverage config lives in `frontend/vite.config.ts` under `test.coverage`;
    the **istanbul** provider and explicit `coverage.include` are load-bearing.
    The comments say why — do not switch to the v8 provider, which reports a file
    no test imports as 100% covered rather than 0%.
  - **Shared test scaffolding goes in `frontend/src/testkit/`**, which that
    coverage config excludes. Two suites drive the campaign play view — the
    play loop (`routes/CampaignView.test.tsx`) and the end-of-scene review
    (`components/review/SceneReview.test.tsx`) — against one set of mocks:
    `testkit/campaignMocks.tsx` holds the `vi.mock` factories and
    `testkit/campaignHarness.tsx` the fixtures, per-test defaults and render
    helpers. Both halves are needed because a `vi.mock` factory is hoisted
    above every import and can close over nothing, so the existing testkit uses
    deferred mock-factory loading. Reuse that testkit;
    do not make its factory module import the mocked `api`, which would deadlock.
    Left in `src/routes/` instead, scaffolding lands in the coverage
    denominator the paragraph above exists to keep honest, and a helper only
    one suite uses belongs in that suite, not in the shared half.
