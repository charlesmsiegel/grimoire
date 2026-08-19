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
      // istanbul, not v8, and the difference is not a preference. v8 reports
      // only what the runtime actually loaded, so with `all` below every file
      // no test imports collapses to a single synthetic line record --
      // `LF:1 LH:1`, i.e. 100% line coverage for a file whose functions never
      // ran. Measured on this suite: 47 of 68 files came back that way, and
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
      include: ["src/**/*.{ts,tsx}"],
      // `all` is what makes the number honest: without it a source file that
      // no test imports is simply absent from the report, so deleting a test
      // file would *raise* coverage. With it, untested modules are counted at
      // 0% and show up as the gap they are.
      all: true,
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
    },
  },
});
