import { describe, expect, it } from "vitest";
import { Linter } from "eslint";
import tsParser from "@typescript-eslint/parser";

import localPlugin from "../index.js";

function lint(code: string, filename: string) {
  const linter = new Linter({ configType: "flat" });
  return linter.verify(
    code,
    {
      files: ["**/*.{ts,tsx}"],
      languageOptions: {
        parser: tsParser,
        parserOptions: { ecmaFeatures: { jsx: true }, ecmaVersion: 2022, sourceType: "module" },
      },
      plugins: { local: localPlugin },
      rules: { "local/no-bespoke-delete": "error" },
    },
    { filename },
  );
}

describe("no-bespoke-delete", () => {
  it("flags a bespoke delete button by className", () => {
    const msgs = lint(
      `const x = <button className="campaign-card-delete">Delete</button>;`,
      "Foo.tsx",
    );
    expect(msgs).toHaveLength(1);
    expect(msgs[0]?.ruleId).toBe("local/no-bespoke-delete");
  });

  it("flags a button labelled Delete via aria-label", () => {
    const msgs = lint(`const x = <button aria-label="Delete world" />;`, "Foo.tsx");
    expect(msgs).toHaveLength(1);
  });

  it("flags a button whose only child is the trash emoji", () => {
    const msgs = lint(`const x = <button title="Remove">🗑</button>;`, "Foo.tsx");
    expect(msgs).toHaveLength(1);
  });

  it("does not flag inside CardIconBar.tsx", () => {
    const msgs = lint(
      `const x = <button className="card-icon-button danger">🗑</button>;`,
      "components/CardIconBar.tsx",
    );
    expect(msgs).toHaveLength(0);
  });

  it("does not flag confirm buttons in *Dialog* files", () => {
    const msgs = lint(
      `const x = <button aria-label="Confirm delete">Delete</button>;`,
      "ConfirmDestructiveDialog.tsx",
    );
    expect(msgs).toHaveLength(0);
  });

  it("does not flag non-delete buttons", () => {
    const msgs = lint(`const x = <button aria-label="Edit world">Edit</button>;`, "Foo.tsx");
    expect(msgs).toHaveLength(0);
  });
});
