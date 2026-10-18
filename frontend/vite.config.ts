/// <reference types="vitest/config" />
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: { "/api": process.env.GRIMOIRE_API ?? "http://127.0.0.1:8173" },
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
    coverage: {
      // Keep Istanbul: the earlier v8 measurement collapsed a source file
      // no test imported to a single synthetic line record --
      // `LF:1 LH:1`, i.e. 100% line coverage for a file whose functions never
      // ran. In that measurement, 47 of 68 files came back that way, and
      // api/client.ts claimed 100% lines while hitting 46 of its 212
      // functions. Istanbul instruments at transform time, so a statement
      // gets a line entry whether or not it executes. The provider package is
      // version-locked to vitest itself -- bump both together.
      provider: "istanbul",
      // `text` so the number lands in the `make check-web` output a human is
      // already reading; `lcov` because that is the artifact every external
      // reader wants (the code-visualization atlas discovers `lcov.info` by
      // name, as do Codecov and the VS Code coverage gutters).
      reporter: ["text", "lcov"],
      reportsDirectory: "coverage",
      // Vitest 4 removed `all`: explicit include now keeps source files no
      // test imports in the denominator at 0%. Without it, deleting a test
      // could raise coverage. verify-coverage-contract.mjs executes this
      // config against an unimported module and checks the missing-include
      // negative control before the full suite.
      include: ["src/**/*.{ts,tsx}"],
      exclude: [
        "src/**/*.test.{ts,tsx}",
        "src/test-setup.ts",
        // ReactDOM bootstrap -- it mounts <App/> into #root and nothing else.
        // A test would assert the framework works, not that we do.
        "src/main.tsx",
        // Type-only module -- it compiles to nothing, so it would sit in the
        // report as an unreachable 0% that no test could ever move.
        "src/theme/types.ts",
        "src/**/*.d.ts",
        // Shared test scaffolding -- mock factories, fixtures and render
        // helpers that several `.test.tsx` files import rather than restate.
        // It is test code that happens not to be named `.test.`, so counting
        // it here would put hundreds of lines of `vi.fn()` in the denominator
        // of a number this config goes out of its way to keep honest.
        "src/testkit/**",
      ],
      // The frontend's coverage floor, the counterpart of the Makefile's
      // COV_FLOOR: each figure is the measured total (91.76% of lines, 89.16%
      // of statements, 84.73% of functions, 84.43% of branches) rounded down
      // to the integer below it. A floor, not a target: it fails `make
      // check-web` on a change that *drops* coverage -- a deleted test file, a
      // module nothing exercises any more -- and says nothing about one that
      // does not raise it. Raise a figure when the number rises; lowering one
      // needs the same justification as any other gate being weakened. Only a
      // whole-suite run can meet it: `vitest run --coverage` over a few files
      // misses it, with `include` above counting every other file at 0%.
      thresholds: { lines: 91, statements: 89, functions: 84, branches: 84 },
    },
  },
});
