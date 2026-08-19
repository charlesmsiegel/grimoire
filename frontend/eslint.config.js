// The frontend lint gate. `npm run lint` prints the findings; `make check-eslint`
// is the gate, and it runs this same config through `scripts/ratchet.py` so a
// rule whose backlog is real work still lands blocking against its current
// count (see the header of that script).
//
// Type-checked linting is the point of the typescript-eslint half: `strict` is
// already on in tsconfig.json, so the rules that only re-check syntax would
// find nothing. The ones that need the type graph -- no-unsafe-*, no-floating-
// promises, no-misused-promises -- are the ones with something to say about a
// codebase whose API client returns `any`.
import js from "@eslint/js";
import tseslint from "typescript-eslint";
import jsxA11y from "eslint-plugin-jsx-a11y";
import react from "eslint-plugin-react";
import reactHooks from "eslint-plugin-react-hooks";

export default tseslint.config(
  { ignores: ["dist", "coverage", "node_modules"] },
  {
    // `src` only, and deliberately: type-checked rules need a file the
    // tsconfig actually includes, and `tsconfig.json` includes exactly `src`.
    // The config files at the root are left unlinted rather than given a
    // second tsconfig to live in.
    files: ["src/**/*.{ts,tsx}"],
    extends: [js.configs.recommended, ...tseslint.configs.recommendedTypeChecked],
    languageOptions: {
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
    plugins: { "jsx-a11y": jsxA11y, react, "react-hooks": reactHooks },
    settings: { react: { version: "detect" } },
    rules: {
      ...jsxA11y.flatConfigs.recommended.rules,
      ...reactHooks.configs.recommended.rules,
      // The stable-list-key pair. `jsx-key` is at zero and stays there;
      // `no-array-index-key` is the same bug one step subtler -- an index key
      // survives a re-render but not a reorder, so React reuses the wrong
      // component's state.
      "react/jsx-key": [
        "error",
        { checkFragmentShorthand: true, checkKeyMustBeforeSpread: true, warnOnDuplicates: true },
      ],
      "react/no-array-index-key": "error",
      // The two idioms the default settings call unused and this codebase
      // uses on purpose: `const { drop, ...rest } = x` to omit a key, and a
      // leading underscore to say "required by the signature, unread here".
      "@typescript-eslint/no-unused-vars": [
        "error",
        {
          args: "after-used",
          ignoreRestSiblings: true,
          argsIgnorePattern: "^_",
          varsIgnorePattern: "^_",
          caughtErrorsIgnorePattern: "^_",
        },
      ],
    },
  },
);
