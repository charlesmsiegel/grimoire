// A coverage report must keep untested source in its denominator. Exercise the
// real provider/config against a tiny store-free project before the full suite;
// a negative control proves removing include cannot silently make this green.
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import config from "../vite.config.ts";

const frontend = fileURLToPath(new URL("../", import.meta.url));
const temporaryBase = realpathSync(tmpdir());
const fixture = mkdtempSync(join(temporaryBase, "grimoire-coverage-"));

function assertUncovered(report) {
  const record = report.split("end_of_record").find((entry) =>
    /^SF:(?:.*\/)?src\/never-imported\.ts$/m.test(entry.replaceAll("\\", "/")));
  assert.ok(record, "never-imported source must remain in the coverage report");
  for (const [total, hits] of [["LF", "LH"], ["FNF", "FNH"], ["BRF", "BRH"]]) {
    assert.ok(Number(record.match(new RegExp(`^${total}:(\\d+)$`, "m"))?.[1]) > 0,
      `never-imported source needs nonzero ${total}`);
    assert.match(record, new RegExp(`^${hits}:0$`, "m"));
  }
}

function measure(label, coverage) {
  const reportsDirectory = join(fixture, label);
  const fixtureConfig = join(fixture, `${label}.config.mjs`);
  writeFileSync(fixtureConfig, `export default ${JSON.stringify({
    root: fixture,
    test: {
      globals: true,
      environment: "node",
      include: ["src/**/*.test.ts"],
      maxWorkers: 1,
      fileParallelism: false,
      coverage: {
        ...coverage,
        enabled: true,
        reporter: ["lcov"],
        reportsDirectory,
        // The fixture deliberately has untested code; production floors stay
        // on the full suite. Here the independent lcov assertions are the gate.
        thresholds: { lines: 0, statements: 0, functions: 0, branches: 0 },
      },
    },
  })};\n`);
  const result = spawnSync(process.execPath, [
    join(frontend, "node_modules/vitest/vitest.mjs"), "run", "--config", fixtureConfig,
  ], { cwd: frontend, encoding: "utf8", timeout: 120_000 });
  assert.ifError(result.error);
  assert.equal(result.status, 0, `${label} fixture failed:\n${result.stdout}\n${result.stderr}`);
  return readFileSync(join(reportsDirectory, "lcov.info"), "utf8").replaceAll("\r", "");
}

try {
  mkdirSync(join(fixture, "src/testkit"), { recursive: true });
  writeFileSync(join(fixture, "src/imported.ts"), "export const answer = () => 42;\n");
  writeFileSync(join(fixture, "src/imported.test.ts"),
    "import { answer } from './imported'; test('answer', () => expect(answer()).toBe(42));\n");
  const untested = "export function choose(value: boolean) {\n  if (value) return 1;\n  return 2;\n}\n";
  writeFileSync(join(fixture, "src/never-imported.ts"), untested);
  writeFileSync(join(fixture, "src/testkit/excluded.ts"), untested);

  const coverage = config.test.coverage;
  const included = measure("included", coverage);
  assertUncovered(included);
  assert.doesNotMatch(included.replaceAll("\\", "/"), /SF:(?:.*\/)?src\/testkit\//);
  assert.match(included, /LH:[1-9]/);

  const { include: _include, ...withoutInclude } = coverage;
  const omitted = measure("without-include", withoutInclude);
  assert.throws(() => assertUncovered(omitted), /never-imported source must remain/);
  console.log("Coverage contract: unimported source counts at zero; exclusions and missing-include control passed.");
} finally {
  // Verify the exact owned directory before a recursive Windows deletion.
  assert.equal(dirname(realpathSync(fixture)), temporaryBase);
  assert.equal(resolve(fixture), realpathSync(fixture));
  rmSync(fixture, { recursive: true });
}
