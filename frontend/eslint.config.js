import js from "@eslint/js";
import globals from "globals";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import tseslint from "typescript-eslint";
import localRules from "./eslint-rules/index.js";

export default tseslint.config(
  { ignores: ["dist", "node_modules"] },
  {
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.browser,
    },
    plugins: {
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
      local: localRules,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "react-refresh/only-export-components": ["warn", { allowConstantExport: true }],
      "local/no-bespoke-delete": "error",
      // Native popups bypass the app's themed, accessible dialogs. Use
      // components/ConfirmDestructiveDialog or components/PromptDialog.
      "no-restricted-globals": [
        "error",
        { name: "confirm", message: "Use ConfirmDestructiveDialog instead." },
        { name: "prompt", message: "Use PromptDialog instead." },
        { name: "alert", message: "Render the message inline (role=alert) or in a Dialog." },
      ],
      "no-restricted-properties": [
        "error",
        {
          object: "window",
          property: "confirm",
          message: "Use ConfirmDestructiveDialog instead.",
        },
        { object: "window", property: "prompt", message: "Use PromptDialog instead." },
        {
          object: "window",
          property: "alert",
          message: "Render the message inline (role=alert) or in a Dialog.",
        },
      ],
    },
  },
);
